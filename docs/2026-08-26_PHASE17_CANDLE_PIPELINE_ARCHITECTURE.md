# PHASE 17 — CANDLE PIPELINE ARCHITECTURE DESIGN

**Mode:** READ-ONLY. No code, config, DB row, Redis key, task state or service
was modified. Broker calls were market-data reads only; no order was placed.
**Date:** 2026-08-26, 15:45–16:15 IST (NSE session closed 15:30).
**Builds on:** Phase 16, which confirmed the 16-minute lag and its scheduling
mechanism. This phase does not repeat that work.

---

## 1. Executive verdict

**The bottleneck is not the broker, not the rate limiter, and not the schedule.
It is that every run re-fetches and re-inserts the entire trading day for every
symbol.**

`crawler/zerodha_historical.py:400-401` sets `from_dt = 09:15` and
`to_dt = now` on **every** run. Production run results show the consequence
directly:

```
2026-08-26 14:51:20  {'symbols': 2560, 'candles': 738902, 'saved': 65495}   slow run (1107s)
2026-08-26 15:01:11  {'symbols': 2560, 'candles': 783161, 'saved': 30591}
2026-08-26 15:13:24  {'symbols': 2560, 'candles': 807064, 'saved': 16679}
2026-08-26 15:27:11  {'symbols': 2560, 'candles': 835783, 'saved': 20045}
2026-08-26 15:39:35  {'symbols': 2560, 'candles': 863744, 'saved': 20491}
```

**~800,000 candles fetched to persist ~20,000 — a 97.5% waste ratio**, repeated
every run, growing all day.

Measured, not assumed:

| Quantity | Measured value | Method |
|---|---|---|
| Kite fetch, full-day range | **0.170s** mean / 0.127s p50 | 10 top-rank symbols, live API |
| Kite fetch, 5-minute delta | **0.044s** mean and p50 | same 10 symbols, same call |
| Bars returned, full day | 3,645 for 10 symbols (365/sym) | live API |
| Bars returned, 5-min delta | 15 for 10 symbols (1.5/sym) | live API |
| Rows written per run, current | ~960,000 (320 chunks of 3,000) | arithmetic on the above |
| Rows written per run, delta | ~12,800 (4 chunks) | arithmetic on the above |

**A 5-minute delta window reduces fetch time 3.9× and row volume 75×.** On the
measured constants, the *entire* 2,560-symbol universe would complete in
**~2.1 minutes** — which meets a sub-3-minute freshness target with no tiering
at all.

**Recommended architecture: C (hybrid) — but its first and largest component is
the delta window, which is independent of tiering and should be validated
first.** → §13.

---

## 2. Current architecture

```
tasks/celery_app.py:517-520     beat "kite-live-1m-candles"
                                crontab(minute="*/3", hour="3-10", dow="1-5")
        │                       ~125 dispatches/session, 37 completions observed
        ▼
tasks/india_tasks.py:2969       kite_live_candles_task()
        │                       soft_time_limit=1200 / time_limit=1260  (:2966-2967)
        ├─ :3011                Redis SET "kite_live_candles:running" nx=True ex=1320
        ├─ :3022-3023           if lock not acquired -> return, NO LOG
        ├─ :3033                get_hub_universe(session)          2,560 symbols
        └─ :3036                sync_live_1m_candles(session, symbols)
                                        │
crawler/zerodha_historical.py:374       │
        ├─ :378                 concurrency = 3   (asyncio.Semaphore)
        ├─ :379                 delay_sec  = 0.1  per symbol
        ├─ :400-401             from_dt = 09:15,  to_dt = now      ← FULL DAY, EVERY RUN
        ├─ :409-413             _fetch(sym): one API call per symbol + sleep(0.1)
        ├─ :415                 asyncio.gather(*[_fetch(s) for s in symbols])
        ├─ :417-422             zip(symbols, results)  — order preserved
        ├─ :426                 save_candles_to_db(all_candles, session)
        └─ :428                 session.commit()
                                        │
crawler/price_feed.py:399-408           ├─ _CHUNK = 3_000
                                        ├─ pg_insert(...).on_conflict_do_nothing(
                                        │     constraint="uq_candle_bar")
                                        └─ commit per chunk
        ▼
candles  (40.9M rows, uq_candle_bar UNIQUE(symbol, timeframe, timestamp))
```

---

## 3. Exact bottleneck

### Runtime decomposition, against a measured 1,107s run

| Component | Derivation | Estimated share |
|---|---|---|
| API fetch | 2,560 symbols ÷ concurrency 3 = 853 slots × (0.170s fetch + 0.1s sleep) = **230s** | **~21%** |
| — of which pure `sleep(0.1)` | 853 × 0.1 = 85s | ~8% |
| Transform + DB write | 1,107 − 230 = **~877s** for ~800,000 rows across ~267 chunks | **~79%** |
| Implied cost per row | 877s ÷ 800,000 ≈ **1.1 ms** | — |

**The dominant contributor is the write path processing ~800,000 rows per run
to persist ~20,000 of them.** → **STRONGLY SUPPORTED** (the split is derived
from a measured total and a measured fetch rate; the transform/DB legs were not
separately instrumented — see §7).

### Causes explicitly ruled out in this phase

| Candidate | Status | Evidence |
|---|---|---|
| Kite API latency | **RULED OUT** | 0.170s mean full-day, 0.044s delta — n=10 live calls |
| Kite rate limiter | **RULED OUT** | `crawler/zerodha_kite_lib.py:460-487` — `get_historical_data` calls `_call()` directly; **no `_rl_acquire_sync`** in its body. The `Bucket.QUOTE` limiter (`zerodha_kite_limiter.py:78-92`, `KITE_QUOTE_RPS=1`) does not govern this path. |
| Retry storm | **RULED OUT** | `errors: 0` on every observed run; retry loop at `zerodha_historical.py:63-77` never engaged |
| Beat schedule | **RULED OUT as the binding constraint** | 3-min schedule is irrelevant while a run takes 10–18 min |

---

## 4. Fetch-order analysis

**CONFIRMED: the fetch order is `hub_universe.rank`, ascending, and that order
survives all the way into the write.**

Chain of custody:

1. `engine/hub_universe.py:208-210` —
   `select(HubUniverse.symbol).order_by(HubUniverse.rank)`.
   Rank is turnover-derived (`:206` "top-N by turnover").
2. `tasks/india_tasks.py:3033-3036` — the list is passed straight through,
   suffix-stripped only.
3. `crawler/zerodha_historical.py:415` — `asyncio.gather` creates coroutines in
   list order; the `Semaphore(3)` at `:406` is FIFO in CPython, so acquisition
   follows list order.
4. `:417-422` — `zip(symbols, results)` rebuilds `all_candles` in list order.
5. `crawler/price_feed.py:401-408` — chunks are cut from that list in order and
   committed per chunk.

Every stage preserves order. Nothing sorts, shuffles or re-partitions.

---

## 5. Freshness vs rank — measured

Median write lag (`created_at − bar timestamp`) by rank quartile, universe
symbols only:

| Session | Q1 (best rank) | Q2 | Q3 | Q4 (worst rank) | Pearson r |
|---|---:|---:|---:|---:|---:|
| 2026-08-24 | **6.0m** | 6.8m | 8.6m | **11.0m** | +0.240 |
| 2026-08-25 | **4.0m** | 4.7m | 5.8m | **7.9m** | +0.211 |
| 2026-08-26 | **24.0m** | 27.9m | 30.8m | **34.7m** | +0.843 |

n = 1,734–1,736 universe symbols per session.

**Monotonic across all four quartiles in all three sessions.** → **CONFIRMED.**

Two consequences for the design:

- A priority tier is **directly implementable on the existing `rank` column** —
  no new data, no new classification.
- The mechanism that would make a tier work is **already proven to operate**;
  it is simply not being used deliberately.

08-26's much larger absolute lag and much stronger correlation (r = 0.843)
coincides with that day's DB connection-exhaustion incident, which serialised
the writes harder. That day is not representative of steady state.

---

## 6. Liquidity vs freshness

**NOT SEPARATELY MEASURED.** `hub_universe.rank` is itself turnover-ordered
(`engine/hub_universe.py:206`), so rank and liquidity are collinear by
construction and the §5 result cannot distinguish them. Any statement that
"liquid symbols are fresher *because* they are liquid" rather than *because
they are early in the list* is **NOT PROVEN**.

---

## 7. Instrumentation gaps

| Stage | Available? | Where it would come from |
|---|---|---|
| `T_publish` | **NOT AVAILABLE** | beat does not log dispatches |
| `T_start` | **NOT AVAILABLE** | only completion is logged (`india_tasks.py:3042`) |
| `T_fetch_start` / `T_fetch_end` | **NOT AVAILABLE** | no timing around `:415` |
| `T_transform` | **NOT AVAILABLE** | no timing around `:417-422` |
| `T_DB_insert` / `T_DB_commit` | **PARTIAL** | `candles.created_at` gives the outcome, not the duration |
| `T_task_complete` | **AVAILABLE** | log line at `:3042`; elapsed at `zerodha_historical.py:432` |
| Dropped dispatches | **NOT AVAILABLE** | `india_tasks.py:3023` returns without logging |

**Minimum instrumentation to close the gap** (design only, not proposed for
implementation here):

1. One `logger.info` on the lock-skip path at `india_tasks.py:3023` — converts
   an invisible ~70% dispatch loss into a countable metric. Lowest-risk change
   available anywhere in this pipeline.
2. Three `monotonic()` deltas inside `sync_live_1m_candles` — around the
   `gather` at `:415`, around the transform at `:417-422`, and around
   `save_candles_to_db` at `:426` — logged in the existing summary dict at
   `:433-438`. This splits the 79% "transform + DB" term, which is currently
   only an arithmetic residual.

---

## 8. Architecture A — priority tiers

Tier assignment uses data that already exists.

| Tier | Membership | Source of truth | Symbols |
|---|---|---|---|
| 0 | open positions | `open_positions.symbol` | 9–11 observed |
| 1 | active news candidates | `causal_events` in the last 60 min | 5–30 observed |
| 2 | high-rank universe | `hub_universe.rank ≤ N` | tunable |
| 3 | long tail | remainder of `hub_universe` | 2,560 − N |

### Capacity model (measured constants)

Per-symbol cost at the current `concurrency=3`, `delay_sec=0.1`, **with a
5-minute delta window**:

```
slot cost   = fetch 0.044s + sleep 0.100s = 0.144s
per symbol  = 0.144s / 3 concurrent       = 0.048s
```

| Freshness target | Symbols that fit |
|---|---:|
| < 2 min (120s) | **2,500** |
| < 3 min (180s) | 3,750 |
| < 5 min (300s) | 6,250 |

**With the delta window, the whole 2,560-symbol universe already fits inside a
~2.1-minute cycle.** Tiering is therefore not required to reach sub-3-minute
freshness — it is required only to *guarantee* Tier 0/1 freshness when the
long tail degrades or the universe grows again.

### Proposed cadences

| Tier | Cadence | Max symbols | Target p95 freshness | On failure |
|---|---|---:|---|---|
| 0 | 60s | 50 | < 90s | alert; never skip |
| 1 | 60s | 200 | < 90s | alert; never skip |
| 2 | 180s | 500 | < 4 min | log and continue |
| 3 | 600s | remainder | < 12 min | log and continue |

Tier 0+1 = 250 symbols × 0.048s = **12s per cycle** — comfortably inside 60s
even if per-symbol cost triples under live load.

---

## 9. Architecture B — rotating chunks

Deterministic partition of the rank-ordered list into K chunks, one chunk per
dispatch, cycling.

Full-universe refresh interval = K × dispatch interval.

| Chunk size | K (chunks) | Cycle time at 60s dispatch | Cycle at 180s dispatch |
|---:|---:|---|---|
| 2,560 | 1 | 60s* | 180s* |
| 1,280 | 2 | 2 min | 6 min |
| 640 | 4 | 4 min | 12 min |
| 256 | 10 | 10 min | 30 min |

*only if a single chunk completes inside the dispatch interval — which, with
the delta window, it does (2.1 min for the full universe).

**Assessment: strictly worse than A for the stated objective.** Rotation gives
every symbol the *same* freshness guarantee, which means Tier-0 symbols wait
behind the long tail exactly as they do today — just in smaller increments. It
fails the §10 required property.

Its one advantage is bounded per-run cost, which matters if the universe grows
much further.

---

## 10. Architecture C — hybrid

Combines the delta window, the tiers of A, and rotation for the tail.

```
delta window (all tiers)  fetch only [last_bar_ts .. now], not [09:15 .. now]
        │
        ├─ Tier 0+1  every 60s   open positions + fresh causal_events   ~250 syms
        ├─ Tier 2    every 180s  hub_universe rank <= 500               500 syms
        └─ Tier 3    every 600s  remainder, rotating in 2 chunks        ~1,800 syms
```

Tier 3 uses B's rotation because it is the only tier where bounded per-run cost
matters and where freshness is not latency-sensitive.

---

## 11. Comparative scorecard

Expected freshness. Derived from the measured 0.048s/symbol; **all figures are
projections, not observations.**

| | Current | A (tiers) | B (rotation) | C (hybrid) |
|---|---|---|---|---|
| **Tier 0 p50** | 16 min | < 60s | ~2 min | **< 60s** |
| **Tier 0 p95** | 17 min | < 90s | ~4 min | **< 90s** |
| **Tier 0 worst** | 52 min | ~2 min | ~6 min | **~2 min** |
| Candidate p50 | 16 min | < 60s | ~2 min | **< 60s** |
| Long-tail p50 | 16 min | ~10 min | ~2 min | ~10 min |
| Long-tail worst | 52 min | ~20 min | **~6 min** | ~20 min |
| Rows written/run | ~960,000 | ~12,800 | ~12,800 | **~1,250 (T0+1)** |
| Meets §10 property | **NO** | YES | **NO** | **YES** |
| New DB objects | — | none | none | none |
| Implementation surface | — | medium | small | medium |

**B is the only design that improves the long-tail worst case, and the only
one besides Current that fails §10.**

---

## 12. Failure-mode analysis

| Risk | Current | A / C | Note |
|---|---|---|---|
| **Kite historical rate limit** | not hit (`errors: 0`) | **RAISED** | Kite documents ~3 req/s for historical. Current effective rate ≈ 3 ÷ 0.270s ≈ **11 req/s** and produces no errors, so the real ceiling is higher than documented — but **the true limit is NOT PROVEN**. Any increase in `concurrency` (`zerodha_historical.py:378`) must be validated against it separately. The delta window does **not** raise request *count*; it lowers request *cost*. |
| DB load | ~960k rows/run | ~12.8k/run | Strictly reduced |
| Celery load | 1 long task | 3–4 short tasks | More scheduling, far less work each |
| **Redis lock** | drops ~70% of dispatches silently | **must become per-tier** | See below |
| Duplicate writes | impossible | impossible | `uq_candle_bar` + `on_conflict_do_nothing` |
| Missing symbols | 45% stop before 15:20 | Tier 3 may still lag | Accepted by design |
| Worker failure | whole universe stalls | one tier stalls | Blast radius reduced |
| Partial chunk failure | per-chunk commit already isolates | unchanged | `price_feed.py:406-408` |
| Retry storm | not observed | unchanged | retry loop at `zerodha_historical.py:63-77` |
| **Long-tail starvation** | already happening | **worsens under A** | C mitigates via rotation; must be monitored, not assumed away |

### Why the Redis lock exists, and whether to keep it

`tasks/india_tasks.py:3011` — `SET nx=True ex=1320`.

Its purpose is **CONFIRMED sound**: without it, a 3-minute schedule against a
10–18 minute task would stack concurrent runs, each fetching the same
~800,000 rows, multiplying DB load and Kite requests until the worker pool is
exhausted. It is the only thing preventing that today.

**Recommendation: keep it, one lock key per tier, and log the skip.** Removing
it would be actively dangerous. The defect is not the lock — it is that the
skip is silent (`:3023`) and that the lock covers the whole universe rather
than a tier.

---

## 13. Recommended architecture

**Architecture C, staged — with the delta window as Stage 1 and validated
before any tiering is built.**

The reason for staging is that the evidence points overwhelmingly at one term:
the delta window addresses **~79% of run time** and **98.7% of row volume**, on
its own, without changing the scheduling model, the queue model, the lock, or
the universe. Tiering addresses the residual and the guarantee.

| | |
|---|---|
| **Problem** | 1m candles run 16 min behind live market data (p50, 1,743 symbols, 15:06:59 IST). |
| **Evidence** | Run results showing 800k fetched / 20k saved; measured fetch 0.170s full-day vs 0.044s delta; rank→lag gradient monotonic across three sessions. |
| **Current architecture** | One task, 2,560 symbols, full-day range every run, semaphore 3, single Redis lock, 3-min schedule that cannot be met. |
| **Failure mechanism** | Run duration (10–18 min) exceeds schedule (3 min); the NX lock silently drops ~70% of dispatches; effective cadence becomes run duration. |
| **Proposed architecture** | Delta-window fetch for all tiers; Tier 0+1 (~250 symbols) every 60s; Tier 2 (rank ≤ 500) every 180s; Tier 3 rotating in 2 chunks every 600s. |
| **Expected freshness** | Tier 0/1 p95 < 90s; Tier 2 < 4 min; Tier 3 < 12 min. **Projections.** |
| **Expected load** | Request count unchanged; row volume −98.7%; per-run DB chunks 320 → 4. |
| **Failure behaviour** | Per-tier lock; a stalled tier degrades only itself; long tail may starve and must be monitored. |

**This removes a confirmed freshness bottleneck and increases the probability
that latency-sensitive decisions receive current market data. It makes no
profitability claim; that must be validated separately after deployment.**

---

## 14. Exact proposed data flow

```
                       ┌─ open_positions.symbol ──────────┐
                       ├─ causal_events (last 60 min) ────┤ Tier 0+1   ~250   60s
hub_universe (rank) ───┼─ rank <= 500 ────────────────────┤ Tier 2      500  180s
                       └─ remainder, 2 rotating chunks ───┘ Tier 3   ~1,800  600s
                                    │
                                    ▼
              per symbol: last_bar_ts = MAX(candles.timestamp)
                          from_dt = last_bar_ts             ← the change
                          to_dt   = now
                                    │
                                    ▼
              sync_live_1m_candles(symbols, from_dt=per-symbol)
                  semaphore 3, delay 0.1s   (both UNCHANGED)
                                    │
                                    ▼
              save_candles_to_db  →  on_conflict_do_nothing(uq_candle_bar)
                                    │
                                    ▼
                                 candles
```

The per-symbol `last_bar_ts` lookup is a single grouped query against the
existing `ix_candles_symbol_timestamp` index — no new index, no new table, no
migration.

**Fallback for a symbol with no 1m data today** (§7 of the brief): if
`last_bar_ts` is NULL, fall back to the current behaviour — `from_dt = 09:15`
— for that symbol only. First fetch is full-day; every subsequent one is a
delta.

---

## 15. Proposed Celery / Redis model

| Element | Proposal | Justification |
|---|---|---|
| Tasks | **Three** — `candles_tier01`, `candles_tier2`, `candles_tier3` | Tiers have different cadences and different failure tolerances; one task cannot express that |
| Queue | **Dedicated `candles_queue`** | Same argument that produced `trade_queue` (`f6a80aa`) and `exit_queue` (`fc71927`): a long task in a shared pool starves short ones. Tier 0+1 must not queue behind Tier 3. |
| Worker | Separate pool, concurrency 1 | Tier tasks are I/O-bound; the semaphore inside the task already provides parallelism |
| Priority queue | **No** | Three fixed cadences on one queue with a dedicated worker is simpler and sufficient. Celery priorities on Redis are advisory and would add a failure mode for no measured gain. |
| Redis lock | **One key per tier**, `ex` = 3 × that tier's cadence | Preserves the stacking protection; confines it |
| Skip logging | **Required** | Without it the design is unobservable, exactly as today |
| Event trigger | **No new task** | See §16 |

---

## 16. News-event interaction

**If a major news event arrives for a symbol outside the fast tier:**

- **Mechanism:** Tier 1 membership is a *query*, not a static list —
  `causal_events` rows created in the last 60 minutes. The 60-second Tier-0/1
  task re-evaluates that query on every run, so a newly-classified symbol joins
  the fast tier **at the next 60-second boundary**. No trigger, no signal, no
  new task, no cache invalidation.
- **Worst-case entry latency:** 60s + one tier run (~12s) ≈ **72s**.
- **If the symbol has no 1m data:** the NULL-`last_bar_ts` fallback in §14
  fetches the full day on first contact — measured 0.170s for one symbol. A
  single cold symbol costs one extra slot and does not affect the tier's
  cadence.

**Not redesigned here, and deliberately so:** how candidates are created, how
the AI evaluates them, and how capital is allocated are downstream questions
that Phase 17 does not touch.

---

## 17. §10 required property — explicit test

> A latency-sensitive symbol must not have to wait for all 2,560 symbols before
> receiving its next refresh.

| Design | Result | Why |
|---|---|---|
| **Current** | **FAILS** | One task, one lock, whole universe. A Tier-0 symbol's next bar arrives only when the run containing it completes — 10–18 min. Proven by the rank→lag gradient (§5) and the 45% of symbols whose last bar precedes 15:20. |
| **A** | **PASSES** | Tier 0+1 is an independent task with its own lock and cadence. |
| **B** | **FAILS** | Rotation is uniform; a Tier-0 symbol waits K−1 chunks for its turn. |
| **C** | **PASSES** | Inherits A's tier isolation. |

---

## 18. Session simulation

Applying the measured per-symbol cost to the last four sessions. **These are
projections from measured constants, not replays** — the pipeline was not
re-executed.

| Session | Universe | Current: symbols stale >10 min | C: Tier 0+1 symbols | C: Tier 0+1 projected p95 |
|---|---:|---:|---:|---|
| 2026-08-21 | 2,560 | 68.5% of bars written >10 min late | 9 positions + candidates | < 90s |
| 2026-08-24 | 2,560 | 57.0% | 11 positions + candidates | < 90s |
| 2026-08-25 | 2,560 | 58.6% | 9 positions + candidates | < 90s |
| 2026-08-26 | 2,560 | 63.9% | 9 positions + candidates | < 90s |

**Open-position symbols per session were 9–11** (`open_positions`, observed).
Tier 0 is therefore two orders of magnitude smaller than the universe — the
symbols whose freshness matters most are the cheapest to keep fresh.

**Stale exposure under the current design, measured:** on 2026-08-25, **1,756
of 3,936 symbols (45%) received no bar after 15:20**, the earliest last-bar
being 14:26 — 64 minutes before the close.

**Not simulated:** trading outcomes, P&L, or whether any decision would have
changed. Out of scope by §13 of the brief.

---

## 19. Migration plan

Staged, each stage independently reversible and independently measurable.

**Stage 1 — observability only.** Add the lock-skip log line and the three
timing deltas from §7. No behavioural change. Establishes the baseline the
later stages are measured against, and splits the 79% residual.

**Stage 2 — delta window.** Change `from_dt` in
`crawler/zerodha_historical.py:400` from a fixed 09:15 to per-symbol
`last_bar_ts`, with the NULL fallback. Universe, schedule, lock, concurrency
and queue all unchanged. This is the single highest-leverage change and is
testable on its own.

**Stage 3 — tiering.** Split into three tasks on a dedicated queue with
per-tier locks, only if Stage 2's measured freshness does not meet the target.

Stage 3 may prove unnecessary. On the measured constants Stage 2 alone brings
the full universe inside ~2.1 minutes.

---

## 20. Validation plan

| Stage | Criterion | Measurement |
|---|---|---|
| Baseline | recorded before any change | p50/p95/max per-symbol lag; % universe >10 min stale; symbols with last bar < 15:20; `candles` vs `saved` per run |
| 1 | dispatch loss becomes countable | skip-log count vs completion count per session |
| 2 | `saved / candles` ratio rises from 2.5% toward ~100% | run result line |
| 2 | run elapsed falls from 1,041–1,107s | `zerodha_historical.py:432` elapsed |
| 2 | p50 per-symbol lag < 3 min | live three-source comparison, hourly, full session |
| 2 | no new `errors` in the run summary | run result line — guards the rate-limit risk in §12 |
| 3 | Tier 0/1 p95 < 90s | per-tier lag measured separately |
| all | long tail not starved | count of symbols with ≥ 300 bars per session must not fall |

The three-source Kite/Upstox/DB comparison used in Phase 16 is the correct
external check and should be re-run **during an open session** — Phase 16's own
second observation was invalidated by the 15:30 close.

---

## 21. Rollback plan

Nothing has been deployed. For any future change to this path, the entire
behaviour is governed by five values:

| File | Line | Value |
|---|---|---|
| `tasks/celery_app.py` | 519 | `crontab(minute="*/3", ...)` |
| `tasks/india_tasks.py` | 2966-2967 | `soft_time_limit=1200`, `time_limit=1260` |
| `tasks/india_tasks.py` | 3011 | Redis lock key and `ex=1320` |
| `crawler/zerodha_historical.py` | 378-379 | `concurrency=3`, `delay_sec=0.1` |
| `crawler/zerodha_historical.py` | 400-401 | `from_dt` / `to_dt` |

Reverting these restores current behaviour exactly. **No migration is
required in either direction:** the schema is unchanged, and `uq_candle_bar`
with `on_conflict_do_nothing` makes re-ingestion idempotent, so a rollback
cannot corrupt or duplicate existing bars.

---

## 22. Remaining unknowns

- **The 79% "transform + DB" term is an arithmetic residual**, not a
  measurement. Stage 1 instrumentation resolves it. Until then the split
  between transform and write is **NOT PROVEN**.
- **Kite's true historical-endpoint rate limit is NOT PROVEN.** The observed
  ~11 req/s produces no errors, which exceeds the documented ~3 req/s. Any
  change to `concurrency` must be validated separately.
- **Per-symbol fetch cost under live market load** was measured post-close on
  10 top-rank symbols. Long-tail and in-session costs may differ →
  **STRONGLY SUPPORTED**, not CONFIRMED.
- **Rank vs liquidity** cannot be separated (§6) — they are collinear by
  construction.
- **Whether lag grows or self-corrects within a session** — Phase 16's §14
  observation was cut short by the close and must be repeated.
- **Whether any trading decision would change** with fresh candles — explicitly
  out of scope, and not claimed.

---

*Evidence gathered 2026-08-26 15:45–16:15 IST from the production database,
production logs, the working-tree source, and the live Kite API. Read-only
throughout; no order was placed and nothing was deployed.*
