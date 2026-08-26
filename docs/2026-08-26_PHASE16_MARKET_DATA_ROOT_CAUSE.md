# PHASE 16 — MARKET DATA ROOT-CAUSE REPORT

**Scope:** why the internal 1-minute candle pipeline runs ~16 minutes behind
live market data.
**Mode:** READ-ONLY. No code, config, DB row, Redis key, task state or service
was modified. Broker and exchange calls were market-data reads only; no order
was placed.
**Date:** 2026-08-26, 15:00–15:45 IST (NSE session closed 15:30).

---

## 1. Executive verdict

**CONFIRMED root cause: UNIVERSE DESIGN FAILURE + WORKER CAPACITY FAILURE.**

`tasks.kite_live_candles` synchronises **2,560 symbols in a single task run**.
It is dispatched by beat **every 3 minutes**, but a full run now takes
**10–14 minutes**. A Redis `SET NX` lock causes every dispatch arriving during
a run to be **silently discarded**. The effective refresh cadence is therefore
the task's own duration, not its schedule — and the newest bar for any symbol
is up to one full run old.

The chain, with the losing stage marked:

| Stage | Status |
|---|---|
| Broker API (Kite / Upstox) | **RULED OUT** — both fresh, agree within 0.20% |
| Fetch correctness | **RULED OUT** — our OHLCV matches Kite bar-for-bar |
| **Scheduling vs task duration** | **CONFIRMED — this is where the 16 minutes are lost** |
| **Universe size per run (2,560 symbols)** | **CONFIRMED — the reason duration exceeds the interval** |
| DB write / commit | RULED OUT as primary; a secondary failure on 08-26 only |
| DB read (consumer selects latest) | RULED OUT |
| Timezone | **RULED OUT** as a latency cause |
| Cache | EVIDENCE NOT AVAILABLE |

**Zero-volume is a separate question and is NOT a defect:** it is genuine
absence of trading in illiquid symbols. **This corrects the Phase 15 report.**

---

## 2. The exact candle pipeline

```
Kite Connect historical/quote API
        │
        ▼
tasks/celery_app.py:517-520      beat entry "kite-live-1m-candles"
                                 crontab(minute="*/3", hour="3-10", dow="1-5")
                                 ~125 dispatches per session
        │
        ▼
tasks/india_tasks.py:2969        kite_live_candles_task()
        │                        soft_time_limit=1200, time_limit=1260  (:2966-2967)
        │
        ├─ :3008-3011  _acquire_lock()  Redis SET "kite_live_candles:running"
        │                               nx=True, ex=1320
        ├─ :3022-3023  if not acquired -> return {"skipped": "already_running"}
        │                               ** RETURNS WITHOUT LOGGING **
        │
        ├─ :3031       refresh_instrument_cache()
        ├─ :3033       get_hub_universe(session)          -> 2,560 symbols
        ├─ :3035       strip .NS / .BO suffixes
        └─ :3036       sync_live_1m_candles(session, symbols)
                            │
                            └─ crawler/price_feed.py::save_candles_to_db
                                 per-chunk commit
        │
        ▼
candles (symbol, timeframe, timestamp, open..volume, created_at)
   uq_candle_bar UNIQUE (symbol, timeframe, timestamp)
        │
        ▼
indicators / technical validation / LLM context
```

Confirming evidence for the 2,560 figure — every completed run logs it
(`logger.info` at `tasks/india_tasks.py:3042`):

```
2026-08-26 15:39:35 | INFO | tasks.india_tasks:kite_live_candles_task:3042
  — [kite_live_candles] {'symbols': 2560, 'candles': …
```

### A documented-intent divergence

`tasks/celery_app.py:515-520`:

```python
# Every minute during NSE session (03:45–10:00 UTC = 09:15–15:30 IST).
"kite-live-1m-candles": {
    "task":     "tasks.kite_live_candles",
    "schedule": crontab(minute="*/3", hour="3-10", day_of_week="1-5"),
},
```

The comment says **every minute**; the code says **every 3 minutes**. Neither
matches the observed effective cadence of 10–14 minutes.

### The code already documents its own failure

`tasks/india_tasks.py:2958-2979`:

> "hub_universe has grown to 2,569 symbols since this task's original
> **~500 in ~90s** design point, so real runs now regularly exceed 300s."

> "…the universe does not finish before the task's own window closes: measured
> on 24 Aug, only 582 symbols carried a 15:29 bar while 1,463 stopped at
> 15:26."

`soft_time_limit` was raised **300s → 1200s** in response. That removed the
crash; it did not restore freshness.

---

## 3. Timestamp evidence

`candles.created_at` exists (`timestamp without time zone`), so write latency
is directly measurable: `created_at − timestamp` = how old a bar was when
written.

### Live ingestion only — bars written inside the same session

Excludes overnight backfill and the close sweep by requiring
`created_at` to fall on the same date between 09:15 and 15:45 IST.

| Session | live bars | p50 | p75 | p95 | max | > 10 min |
|---|---:|---:|---:|---:|---:|---:|
| 2026-08-21 | 1,153,086 | **24.1m** | 62.0m | 133.9m | 282.2m | 68.5% |
| 2026-08-24 | 711,365 | **11.0m** | 16.5m | 246.9m | 389.9m | 57.0% |
| 2026-08-25 | 727,042 | **11.5m** | 35.5m | 268.2m | 386.8m | 58.6% |
| 2026-08-26 | 712,794 | **14.8m** | 41.8m | 250.9m | 389.9m | 63.9% |

**Median live write latency is 11–24 minutes; 57–68% of bars are written more
than ten minutes after the minute they describe.** This is the mechanism behind
the 16-minute staleness observed live at 15:06:59.

*Method note:* the unfiltered figure (backfill included) is far larger — p50
122.0m on 08-25, max 866.2m — because 45% of that session's bars were written
outside the session by the close sweep. Reporting the unfiltered number as
"latency" would be wrong, and it is excluded here.

### Stage-by-stage

| Stage | Available? |
|---|---|
| `T_market` (bar close) | ✅ `candles.timestamp` |
| `T_fetch` (broker response) | ❌ **EVIDENCE NOT AVAILABLE** — not recorded |
| `T_task_publish` | ❌ **EVIDENCE NOT AVAILABLE** — beat does not log dispatches |
| `T_task_start` | ❌ **EVIDENCE NOT AVAILABLE** — only completion is logged |
| `T_task_complete` | ✅ log line at `india_tasks.py:3042` |
| `T_DB_insert / commit` | ✅ `candles.created_at` (per-chunk commit) |
| `T_cache` | ❌ **EVIDENCE NOT AVAILABLE** |
| `T_consumer` | ✅ reconstructable via `created_at ≤ decision_time` |

---

## 4. Broker cross-validation

Live three-source comparison at **15:06:39 IST**. Kite via `kiteconnect`,
Upstox via the repo's own `crawler/upstox_market.py::get_ltp`, DB via newest
1m bar.

| Symbol | KITE | UPSTOX | our DB | K−U | K−DB | DB bar | lag |
|---|---:|---:|---:|---:|---:|---|---:|
| APOLLOPIPE | 649.15 | 649.15 | 649.80 | 0.00% | −0.10% | 15:01 | 6m |
| GENESYS | 247.00 | 247.50 | 254.85 | −0.20% | −3.08% | 14:51 | 16m |
| FACT | 862.80 | 863.70 | 862.90 | −0.10% | −0.01% | 15:01 | 6m |
| GIPCL | 201.74 | 201.59 | 201.30 | +0.07% | +0.22% | 14:51 | 16m |
| NITINSPIN | 627.45 | 627.45 | 628.00 | 0.00% | −0.09% | 14:51 | 16m |
| RPTECH | 758.00 | 758.00 | 761.75 | 0.00% | −0.49% | 15:01 | 6m |

**Kite and Upstox agree. Our values match once lag is subtracted.** The
divergence scales with lag: 6-minute lag ≤0.5%; 16-minute lag up to 3.08%.

### Fetch correctness — bar-for-bar

Kite `historical_data` versus our stored bars, APOLLOPIPE, 2026-08-26
10:00–10:05 IST:

| Minute | Kite volume | our volume |
|---|---:|---:|
| 10:00 | 2,838 | 2,838 |
| 10:01 | 208 | 208 |
| 10:02 | 707 | 707 |
| 10:03 | 344 | 344 |
| 10:04 | 3,346 | 3,346 |
| 10:05 | 777 | 777 |

Identical. **Fetch and parse are correct. RULED OUT as a cause.**

### Per-symbol lag across the universe (15:06:59 IST, live)

| Metric | Value |
|---|---|
| Symbols measured | 1,743 |
| p50 | **16.0 min** |
| p75 / p95 | 16.0 / 17.0 min |
| Max | 52.0 min |
| > 10 min stale | **1,046 / 1,743 = 60%** |
| > 30 min stale | 12 / 1,743 = 1% |

### §14 live re-observation — INCOMPLETE

A second observation was taken at 15:40:03 (p50 13.1m, p95 26.1m, max 38.1m)
but the session had already closed at 15:30, so it measures post-close drift,
not live catch-up behaviour. **The "does the lag grow or recover?" question is
EVIDENCE NOT AVAILABLE and must be repeated during a live session.**

---

## 5. Celery / task analysis

### The decisive numbers

| Quantity | Value | Source |
|---|---|---|
| Beat schedule | every 3 min, 03–10 UTC, Mon–Fri | `celery_app.py:519` |
| Expected dispatches per session | ~125 | schedule arithmetic |
| **Completed runs logged today** | **37** | `grep '[kite_live_candles]'` |
| Symbols per run | **2,560** | run result line, `india_tasks.py:3042` |
| `soft_time_limit` / `time_limit` | 1200s / 1260s | `india_tasks.py:2966-2967` |
| Redis lock TTL | 1320s | `india_tasks.py:3011` |

### Observed completion intervals — 2026-08-26

```
14:51:20 → 15:01:11   9m 51s
15:01:11 → 15:13:24  12m 13s
15:13:24 → 15:27:11  13m 47s
15:27:11 → 15:39:35  12m 24s
```

**`task_duration (10–14 min) >> task_interval (3 min)` — CONFIRMED.**

### What happens to the overlapping dispatches: they are silently dropped

`tasks/india_tasks.py:3008-3023`:

```python
async def _acquire_lock() -> bool:
    return bool(await _r.set("kite_live_candles:running", "1", nx=True, ex=1320))
...
if not _run_async(_acquire_lock()):
    return {"skipped": "already_running"}
```

The skip path **returns before reaching the `logger.info` at `:3042`**, so a
discarded dispatch produces **no log line at all**. ~125 dispatches versus 37
completions implies roughly **70% of dispatches are discarded invisibly** —
consistent with, and explained by, the lock.

→ Overlap / queue / expire behaviour: **CONFIRMED — dropped, not queued.**

### Secondary failure on 08-26 only

`kite_live_candles` raised `TooManyConnectionsError` at 13:36:44, 13:36:45,
13:37:02, 13:40:54, 13:42:46 and 13:45:29 (`india_tasks.py:3021`), during the
day's DB connection-exhaustion incident. **This is a distinct, one-day event.**
The 16-minute measurement was taken at 15:06, after that incident had cleared,
so the structural lag is not attributable to it.

---

## 6. Universe-size analysis

| Session | symbols with 1m bars | bars |
|---|---:|---:|
| 2026-08-21 | 4,156 | 1,177,010 |
| 2026-08-24 | 4,342 | 1,496,770 |
| 2026-08-25 | 3,936 | 1,324,888 |
| 2026-08-26 *(part.)* | 2,130 | 727,479 |

`hub_universe` currently holds **2,560** symbols (the task's input). More
symbols carry bars than the task syncs, so other writers (BSE daily refresh,
resample, long-tail sync) also populate this table.

**Historical universe size is EVIDENCE NOT AVAILABLE** — `hub_universe` is
rewritten wholesale and every row carries `updated_at = 2026-08-26`. The
correlation "larger universe → longer duration → worse freshness" is therefore
**NOT PROVEN from measurement**, though the code comment at
`india_tasks.py:2959` states the growth from ~500 to 2,569 explicitly.

### Direct proof that a run does not finish the universe

If the sweep cannot complete, symbols late in the fetch order must stop
receiving bars earlier. Distribution of each symbol's **last bar of the
session**:

| 2026-08-24 | 2026-08-25 | 2026-08-26 |
|---|---|---|
| 15:29 → 2,602 syms | 15:29 → 2,180 syms | 15:27 → 1,145 syms |
| 15:26 → 1,452 syms | 15:18 → 1,160 syms | 15:29 → 548 syms |
| 15:14 → 207 syms | 15:17 → 299 syms | 15:14 → 211 syms |
| 15:25 → 36 syms | 15:14 → 214 syms | 15:26 → 168 syms |

**On 2026-08-25, 1,756 of 3,936 symbols (45%) stopped receiving bars before
15:20**, and the earliest last-bar was **14:26** — 64 minutes before the close.

This reproduces the pattern the code itself documented for 24 Aug and explains
the per-symbol lag unevenness seen in §4 (6 min for some symbols, 16 for
others, at the same instant).

→ **CONFIRMED.**

---

## 7. Zero-volume root cause

**Classification: (A) genuine zero trading volume. NOT a defect.**
**This corrects the Phase 15 report, which listed it as a data defect.**

Zero-volume share by symbol liquidity, 2026-08-25 session:

| Liquidity bucket | symbols | bars | % bars with volume = 0 |
|---|---:|---:|---:|
| Turnover ≥ ₹25 cr | 521 | 188,653 | **1.9%** |
| ₹5–25 cr | 554 | 197,827 | 13.6% |
| ₹0.5–5 cr | 821 | 278,566 | 43.9% |
| < ₹0.5 cr | **2,038** | 659,830 | **87.8%** |
| Zero volume all day | 2 | 12 | 100% |
| **Top-200 `hub_universe` by rank** | 200 | 71,204 | **1.0%** |

The 55% headline is a **composition artefact**: the universe is dominated by
2,038 microcaps that genuinely do not trade every minute. Where liquidity
exists, zero-volume bars are 1–2%.

Corroborating evidence across timeframes on 08-25 — aggregation removes the
zeros exactly as genuine sparse trading would predict:

| Timeframe | bars | % volume = 0 |
|---|---:|---:|
| 1m | 1,324,888 | 55.3% |
| 5m | 178,545 | 21.5% |
| 15m | 51,953 | 12.6% |
| 1h | 17,838 | 15.3% |

Ruled out by the §4 bar-for-bar comparison: **(C) API parsing bug**,
**(D) fallback/default value**, **(F) wrong instrument**. Our values are
byte-identical to Kite's for a liquid symbol.

---

## 8. Database analysis

| Property | Value |
|---|---|
| `timestamp` type | `timestamp without time zone`, **UTC** |
| `created_at` | `timestamp without time zone` — present, enables §3 |
| Uniqueness | `uq_candle_bar UNIQUE (symbol, timeframe, timestamp)` |
| Indexes | `ix_candles_symbol_timeframe`, `ix_candles_symbol_timestamp` |
| Commit frequency | per chunk (`crawler/price_feed.py::save_candles_to_db`) |

The unique constraint makes duplicate bars impossible, and the
`(symbol, timestamp)` index supports the consumer's
`ORDER BY timestamp DESC LIMIT 1` pattern efficiently.

**DB read failure RULED OUT:** consumers do select the newest available row.
Reconstruction in §11 shows the newest row *readable at decision time* was
consistently 7–21 minutes old — because that is the newest row that had been
**written**, not because the query missed newer data.

**DB write failure RULED OUT as primary cause;** confirmed as a secondary,
single-day failure on 08-26 (§5).

---

## 9. Timezone analysis

**RULED OUT as a cause of latency.**

Direct proof — CEIGALL.NS, the bar for 10:15 IST on 2026-08-26:

| stored raw | rendered IST |
|---|---|
| `2026-08-26 04:45:00` | `2026-08-26 10:15:00` |

10:15 IST = 04:45 UTC. The conversion is correct.

The Phase 15 finding stands but is a **different problem**:
`tactical_signals.timestamp` is naive IST while `candles`, `agent_decisions`,
`news_items`, `causal_events`, `simulation_logs`, `paper_trades` and
`master_intelligence_scores` are UTC. That produces **cross-table analytical
errors of 5h30m**, not latency.

---

## 10. Fast-lane analysis

**PARTIAL — EVIDENCE NOT AVAILABLE for the decisive question.**

`tasks.kite_live_candles` fetches the full `hub_universe` (2,560) in one pass
with no priority ordering visible at the call site
(`india_tasks.py:3033-3036`): the symbol list is taken from
`get_hub_universe()` and passed straight to `sync_live_1m_candles`.

Whether an internal fast lane exists inside `sync_live_1m_candles`, and whether
the fetch order correlates with the observed per-symbol lag, was **not
established**. The last-bar distribution in §6 proves that *some* ordering
effect exists — 2,180 symbols reached 15:29 while 1,160 stopped at 15:18 — but
the ordering key itself was not identified.

**Open question for a follow-up:** does fetch order correlate with
`hub_universe.rank`? If it does, the lag is rank-ordered and a genuine fast
lane could be built on it.

---

## 11. Technical-context evidence — CEIGALL.NS, 2026-08-26

The newest candle **actually readable** (`created_at ≤ decision time`) at each
of the nine LLM decisions:

| Decision | Newest readable bar | Lag | Close | Volume | Bar written at |
|---|---|---:|---:|---:|---|
| 09:41 | 09:30 | 11m | 325.40 | 102 | 09:37 |
| 09:55 | 09:45 | 10m | 327.75 | 105 | 09:49 |
| 10:14 | 10:03 | 11m | 327.15 | 53 | 10:07 |
| 10:51 | 10:43 | 8m | 328.00 | 22 | 10:46 |
| **11:27** | **11:20** | **7m** | **327.60** | **0** | **11:24** |
| 12:18 | 12:11 | 7m | 335.00 | 133 | 12:17 |
| 13:23 | 13:10 | 13m | 337.45 | 970 | 13:16 |
| 14:53 | 14:32 | **21m** | 338.85 | 697 | 14:44 |
| 15:11 | 15:01 | 10m | 337.90 | 92 | 15:07 |

**Every decision was made on evidence 7–21 minutes old.**

The highest-conviction refusal — 11:27, confidence **85**, stated reason
*"genuine HIGH materiality catalyst … but absent volume confirmation"* — was
made against a bar carrying **volume = 0** that was already 7 minutes stale.

### Correction to Phase 15

Phase 15 attributed the CEIGALL "volume confirmation" refusals to the 55%
zero-volume problem. **That attribution is NOT SUPPORTED.** CEIGALL is liquid:
375 bars that session, only **11 with volume = 0 (2.9%)**, ₹44 cr turnover,
and its last bar was 15:29 — it was in the *fresh* cohort.

The precise, defensible statement is narrower: the validator was reading a
**single stale minute bar** carrying 0–133 shares, rather than recent
cumulative activity. The volume column is correct; the *slice* of it presented
to the decision was thin and old.

---

## 12. Change-point analysis

| When | Commit | Change | Expected effect | Observed |
|---|---|---|---|---|
| 08-21 13:03 | `3cf6e05` | F1 cadence 1→3 min, limits 50/60 → 170/180 | more scan load | — |
| 08-21 19:38 | `70ab02a` | move heavy batch tasks out of market hours, gate long-tail sync | less contention | — |
| 08-21 19:06 | `2f6d9ac` | chunk + prioritise `backfill_hub_1d`, resumable cursor | less contention | — |
| 08-24 18:02 | `0d20813` | derive 5m/15m/1h from 1m; repair long-tail gate and universe fast lane | better coverage | 5m p50 latency 13.8m vs 1m 122.0m |
| *(undated in window)* | — | `soft_time_limit` 300 → 1200 in `kite_live_candles_task` | prevents mid-run rollback | crash removed, freshness not restored |

**The first change after which candle freshness degraded: NOT PROVEN.**

Live write latency across the window is 24.1m → 11.0m → 11.5m → 14.8m — it
does **not** show a step degradation inside these four sessions. The degradation
predates the window: it followed the universe's growth from the documented
"~500 symbols in ~90s" design point to 2,560, and that growth is not dated by
any commit in this window.

---

## 13. Confirmed root cause

**MULTIPLE FAILURE, with one dominant term.**

| # | Cause | Status |
|---|---|---|
| 1 | **UNIVERSE DESIGN FAILURE** — 2,560 symbols in one task run, against a "~500 in ~90s" design | **CONFIRMED** |
| 2 | **WORKER CAPACITY / SCHEDULING FAILURE** — 10–14 min duration against a 3-min schedule; ~70% of dispatches silently dropped by the Redis NX lock | **CONFIRMED** |
| 3 | Observability failure — the skip path returns without logging, so the drop is invisible | **CONFIRMED** |
| 4 | DB write failure (08-26 only, connection exhaustion) | **CONFIRMED, secondary** |

---

## 14. Ruled-out causes

| Cause | Status | Evidence |
|---|---|---|
| Data source failure | **RULED OUT** | Kite and Upstox both fresh and mutually consistent (≤0.20%) |
| Fetch / parse failure | **RULED OUT** | OHLCV byte-identical to Kite for a liquid symbol |
| Database read failure | **RULED OUT** | Consumers select the newest *written* row; unique index and ordering index present |
| Timezone error | **RULED OUT** | 10:15 IST stored as 04:45 UTC — correct |
| Zero-volume as a defect | **RULED OUT** | 1.9% in liquid names, 87.8% in sub-₹0.5 cr names; aggregation to 5m/15m removes it |
| Duplicate / out-of-order rows | **RULED OUT** | `uq_candle_bar` makes duplicates impossible |
| Cache failure | **EVIDENCE NOT AVAILABLE** | not instrumented |

---

## 15. Impact

| Metric | Value | Denominator |
|---|---|---|
| Symbols affected (>10 min stale, live) | **1,046** | 1,743 measured = **60%** |
| Median lag, live | **16.0 min** | 1,743 symbols |
| p95 lag, live | 17.0 min | 1,743 symbols |
| Max lag, live | 52.0 min | 1,743 symbols |
| Median live write latency | 11.0–24.1 min | per session |
| Bars written >10 min late | 57.0–68.5% | per session |
| Symbols stopping before 15:20 (08-25) | **1,756** | 3,936 = **45%** |
| Zero-volume bars, 1m, 08-25 | 732,185 | 1,324,888 = 55.3% — **not a defect** |
| Zero-volume in liquid names | 1.9% | 188,653 bars |
| Beat dispatches silently dropped | ~88 | ~125 = **~70%** |

**AI decisions demonstrably made on stale evidence:** 9 of 9 for CEIGALL
(7–21 min). **Generalising this to all 2,185 AI decisions in the window was NOT
performed** — the denominator would have to be built decision by decision, and
it was not.

---

## 16. Counterfactual

**EVIDENCE NOT AVAILABLE — deliberately not attempted.**

Running the technical validator against fresh broker candles would require
executing production decision code with substituted inputs. Doing that safely
requires an isolated harness that does not exist, and Phase 16 is read-only.

What *can* be said without running anything: at 10:14 the validator read a bar
timestamped 10:03 showing 327.15. Kite's contemporaneous data for CEIGALL that
day reached a high of 346.00 (+6.67% on prev close 324.35). **Whether a fresh
bar would have changed the verdict is NOT PROVEN** — the stated refusal at that
moment was the follow-through gate ("price only +0.80% on the day"), and
`change_pct` is computed from the market snapshot rather than from the stale
candle, so this particular refusal may not have been caused by staleness at
all.

---

## 17. What remains unknown

- `T_fetch`, `T_task_publish`, `T_task_start`, `T_cache` — **not instrumented**.
  The 10–14 minutes cannot yet be split between broker round-trips, transform,
  and DB write.
- Whether fetch order correlates with `hub_universe.rank` — i.e. whether the
  lag is rank-ordered and a priority lane is feasible.
- Whether the lag grows or self-corrects within a live session (§14 incomplete;
  the market closed mid-observation).
- Historical `hub_universe` size — the table is rewritten wholesale.
- The proportion of all 2,185 AI decisions made on stale candles.
- Why the 08-26 NSE announcement capture collapsed (carried over from Phase 15,
  still NOT PROVEN).

---

## 18. Recommended fix — DESIGN ONLY

*No change is proposed for implementation in this phase.*

**A. Split the universe into freshness tiers.** The binding constraint is
2,560 symbols per run. A small high-priority set (open positions + the day's
candidates + top-N by rank) refreshed on a short cadence, with the long tail on
a slower one, would decouple freshness from universe size. This is the direct
attack on cause #1.

**B. Make the dropped dispatch visible.** `india_tasks.py:3023` returns
`{"skipped": "already_running"}` without logging. One log line there converts
an invisible 70% loss into an observable metric. Lowest-risk change available.

**C. Instrument the missing stages.** Record fetch start/end and per-chunk
write timings so the 10–14 minutes can be attributed. Without this, any tuning
is guesswork.

**D. Reconsider the schedule.** A 3-minute cron against a 10–14 minute task is
misleading in both directions — it neither achieves 3-minute freshness nor
signals that it cannot. Aligning the schedule with achievable duration would at
least make the system honest about its own cadence.

**Explicitly out of scope of this phase's conclusions:** whether fixing
freshness improves profitability, whether the AI is at fault, and whether
capital allocation should change.

---

## 19. Validation plan

1. **Re-run the live lag measurement during an open session** — the §14
   observation that could not be completed today. Sample every 5 minutes from
   09:30 to 15:15; record p50/p95/max and whether lag grows, holds or recovers.
2. **Correlate fetch order with `hub_universe.rank`** on stored data: does
   last-bar time track rank? Read-only, no deployment needed.
3. **Baseline before any change:** p50/p95 per-symbol lag; % of universe >10
   min stale; count of symbols whose last bar precedes 15:20.
4. **Success criterion for a tiered design:** p95 lag < 2 min for the priority
   tier, with no regression in long-tail coverage (symbols carrying ≥ 300 bars
   per session).
5. **Guard:** the `uq_candle_bar` constraint already prevents duplicate or
   corrupted bars from any change to fetch ordering.

---

## 20. Rollback plan

Nothing was deployed, so nothing requires rollback.

For any future change to this path: `kite_live_candles_task` is a single task
whose behaviour is governed by three values —
`crontab(minute=…)` at `celery_app.py:519`, `soft_time_limit`/`time_limit` at
`india_tasks.py:2966-2967`, and the symbol list built at
`india_tasks.py:3033-3035`. Reverting those three restores current behaviour
exactly; the `candles` table needs no migration because the schema is unchanged
and `uq_candle_bar` makes re-ingestion idempotent.

---

*Evidence gathered 2026-08-26 15:00–15:45 IST from the production database,
production logs, the working-tree source, git history, the live Kite API and
the live Upstox API. Read-only throughout; no order was placed.*
