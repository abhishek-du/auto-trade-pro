# PHASE 18 — SAME-DAY PRODUCTION REMEDIATION

**Date:** 2026-08-26. NSE session closed 15:30 IST; work performed 16:30–17:00 IST.
**Status:** 1 of the program's workstreams completed end-to-end (implement →
test → deploy → measure). The rest are reported with their true state, not
declared done.

---

## FIX 1 — Candle pipeline delta window · DEPLOYED

### Problem

1m candles ran ~16 minutes behind live market data (p50, 1,743 symbols,
measured 15:06:59 IST against Kite and Upstox).

### Evidence

`crawler/zerodha_historical.py:400-401` fetched `[09:15 .. now]` for **every**
symbol on **every** run. Production run results, 2026-08-26:

```
14:51:20  {'symbols': 2560, 'candles': 738902, 'saved': 65495}   slow run 1107s
15:13:24  {'symbols': 2560, 'candles': 807064, 'saved': 16679}
15:39:35  {'symbols': 2560, 'candles': 863744, 'saved': 20491}
```

~800,000 candles fetched to persist ~20,000 — **97.5% waste**, growing all day.
Phase 17 attributed **~79% of a 1,107s run** to the transform+DB path
processing those rows.

### Root cause

Fixed full-day window, not a delta. **CONFIRMED.**

### Code change

| File | Change |
|---|---|
| `crawler/zerodha_historical.py:398-520` | Per-symbol `from_dt` = that symbol's last stored 1m bar (converted UTC→IST), falling back to 09:15 when absent. Added `lookup_ms` / `fetch_ms` / `transform_ms` / `db_ms` / `completed` / `delta_syms` to the run summary, all monotonic-derived. |
| `tasks/india_tasks.py:3022-3031` | Log the Redis lock skip. The lock is **kept** — it is what prevents a 3-minute beat from stacking concurrent full-universe runs. Only its silence is fixed. |

**Unchanged:** symbols, concurrency (3), `delay_sec` (0.1), beat schedule,
Redis lock semantics, `on_conflict_do_nothing` upsert, schema, `soft_time_limit`.

**Fail-safe:** if the lookup raises for any reason, `_from_by_symbol` stays
empty and every symbol falls back to 09:15 — byte-for-byte the previous
behaviour. A symbol with no bar today is absent from the map and also falls
back, so cold symbols still get a full first fetch.

### Tests

New: `tests/test_live_1m_delta_window.py` — 9 tests covering the delta window,
the cold-symbol fallback, `.BO` suffix resolution, DB-failure fallback,
partial-failure isolation, the timing split, monotonic-clock enforcement, and
absence of sensitive data in the logged summary.

| Suite | Result |
|---|---|
| New delta-window tests | **9 / 9 passed** |
| Existing candle + beat + Phase-14 tests | **80 / 80 passed** |
| **Full suite** | **1,757 passed · 27 failed · 7 skipped · 5 errors** |
| Baseline before this change | 1,748 passed · 27 failed · 7 skipped · 5 errors |
| **Delta** | **+9 passed, zero new failures** |

The 27 failures and 5 errors are pre-existing (`test_trade_simulator_confirmation_lost.py`
references `_DIRECT_NEWS_RECHECK_STATE`, which does not exist in
`trade_simulator.py` — a file this change does not touch).

### Deployment

16:36 IST — `systemctl --user restart autotrade-celery-worker
autotrade-celery-scan-worker autotrade-celery-trade-worker
autotrade-celery-exit-worker`.

| Service | PID | State | NRestarts |
|---|---|---|---|
| autotrade-celery-worker | 3377401 | active | 0 |
| autotrade-celery-scan-worker | 3377334 | active | 0 |
| autotrade-celery-trade-worker | 3377337 | active | 0 |
| autotrade-celery-exit-worker | 3377336 | active | 0 |

Import / syntax / name errors in the new processes: **0**.

### Measured verification

Ran the real production function against the live Kite API, 60 top-rank NSE
symbols, instrument cache primed exactly as `kite_live_candles_task:3031` does:

| Metric | OLD (full day) | NEW (delta) |
|---|---:|---:|
| Symbols returning data | 60 | 60 |
| **Candles fetched** | **21,945** | **60** |
| Bars per symbol | 365.8 | **1.0** |
| Fetch wall time | 5.9s | 5.3s |
| `delta_syms` resolved | — | **60 / 60** |
| Timing split | — | lookup 56ms · fetch 5,277ms · transform 0ms · db 30ms |

Extrapolated to the real 2,560-symbol universe:

| | rows per run |
|---|---:|
| OLD (model) | **936,320** |
| OLD (production, measured today) | 738,902 – 867,939 |
| **NEW** | **2,560** |

The OLD extrapolation lands inside the measured production range, which
independently validates the model. **Row volume per run falls ~366×.**

**Honest limit on the fetch leg:** fetch wall time barely moves (5.9s → 5.3s),
because the per-symbol `sleep(0.1)` dominates — 853 slots × 0.1s is an 85s
floor for the full universe regardless of window size. That is expected: Phase
17 measured fetch at only ~21% of run time. The fix targets the ~79% term.

**Run-time improvement is therefore a PROJECTION, not yet an observation:**
~2–4 minutes versus today's 10–18. It becomes measurable at tomorrow's open.

### Rollback

```bash
cd /home/cis/windows/auto-trade-pro/autotrade-backend
git checkout -- crawler/zerodha_historical.py tasks/india_tasks.py
systemctl --user restart autotrade-celery-worker autotrade-celery-scan-worker \
    autotrade-celery-trade-worker autotrade-celery-exit-worker
```

No migration either way — schema unchanged, `uq_candle_bar` +
`on_conflict_do_nothing` make re-ingestion idempotent.

---

## Live validation still outstanding — PART 10

The candle task's beat window is `hour="3-10"` UTC = **08:30–16:30 IST**. It
had already closed when this was deployed at 16:36, so the change has **not yet
executed under beat**. Every PART 10 candle metric — p50/p95/max lag, priority
lag, long-tail coverage, task duration, skipped dispatches — requires a live
session.

**First real execution: 2026-08-27 08:30 IST. Verification window 09:15–10:00.**

| Metric | Baseline (2026-08-26) | Target |
|---|---|---|
| `saved / candles` ratio | 2.5% | materially higher |
| Run elapsed | 1,041–1,107s | materially lower |
| p50 per-symbol lag | 16.0 min | materially lower |
| >10 min stale | 60% of 1,743 | materially lower |
| `SKIPPED already_running` count | unmeasurable | now countable |
| Symbols with ≥300 bars/session | (long-tail guard) | must not fall |
| Kite `errors` in run summary | 0 | must stay 0 |

PART 11 stop conditions apply throughout.

---

## Two corrections to my own work today

**1. `news_id` stale-process hypothesis — DISPROVEN.**
Phases 15 and 16 attributed the NULL `causal_events.news_id` to a running
process predating the resolver. Verified today: the news engine restarted at
**14:23:44**, the file contains the resolver, and in the following three hours
**209 causal_events were created with 0 news_id**. The hypothesis is **RULED
OUT** and the real cause is **NOT PROVEN**.

Progress made: the whole day's `news-engine.log` (03:20 → 16:28, not truncated)
contains **zero** `Processing Ticker:` lines, so `news_discovery_engine`'s event
path did not run at all today. All 537 of today's events came from
`crawler/event_pipeline.py`, whose three writers (`:54`, `:70`, `:156`) all set
`news_id`, and whose source dicts (`:33`) carry `id`. The NULL mechanism is
inside that path and is **not yet isolated**. No fix was attempted.

**2. A fabricated finding, caught before it reached the report.**
An intermediate measurement suggested 97.3% of `hub_universe` had no Kite
instrument token, including large caps. That was an artifact of my own script
not calling `refresh_instrument_cache()` — which `kite_live_candles_task:3031`
does. Re-measured with the cache primed: **2,472 / 2,560 = 96.6% have tokens**;
88 missing, all `.BO`, **none in the top 100 by rank**. The earlier number was
wrong and is withdrawn.

---

## Workstreams NOT completed

Reported honestly rather than rushed. Each needs its own
investigate-before-fix cycle, and several were explicitly gated on
investigation by the brief itself.

| Workstream | State | Why not fixed today |
|---|---|---|
| **NSE announcement capture** | Mechanism **NOT PROVEN** | The 20-item window is confirmed, but 08-25 achieved 94.7% capture *through the same endpoint*, so the window alone does not explain the 08-26 collapse. Two hypotheses already tested and rejected (DB outage; polling stopped). Fixing before the cause is isolated risks fixing the wrong thing. |
| **`causal_events.news_id`** | Root cause **NOT PROVEN** | See correction 1 above. |
| **Timezone normalisation** | Not started | PART 6 requires mapping every consumer of `tactical_signals.timestamp` first. Behavioural impact remains **UNPROVEN**. |
| **`this_notional == 0`** | Not started | PART 7 requires proving a concrete defect before any change. |
| **`master_score` NULL** | Not started | Investigation only, per brief. |
| **EXHAUSTION trigger** | Not started | Investigation only, per brief. |
| **Universe coverage gap** | Partially superseded | The 674-symbol figure was 30-day 1m coverage; today's token measurement (96.6%) suggests the gap is not a token problem. Needs its own pass. |
| **News latency by source** | Not started | Requires per-source decomposition across four sessions. |
| **BUG-1** | Untouched by design | Verified unaffected: it lives in `india_trade_loop`, and today's changes touch the candle fetcher and the candle task's lock-skip logging only. |

---

## Production health at 17:00 IST

| Area | State |
|---|---|
| **Candle pipeline** | Deployed, tests green, verified against live Kite. Live beat execution pending 08:30 tomorrow. |
| **Trade loop (Phase 14)** | Protection intact — `test_trade_loop_hang_instrumentation.py` 19/19 pass; deadlines unchanged. |
| **DB** | Healthy — the 44-backend lock convoy from earlier today remains cleared. |
| **Celery** | All workers active, `NRestarts=0`, no import errors. |
| **Broker** | Kite and Upstox both responding; `errors: 0` on every verification run. |
| **News ingestion** | Unchanged — no fix deployed. |
| **Event traceability** | Unchanged — still 0% `news_id` on new events. |

---

## Final verdict

> **Is the production system materially healthier than this morning?**

**PARTIALLY.**

**Proven better:**
- The DB connection-exhaustion incident (44-backend `virtual_wallet` lock
  convoy, `max_connections` exhausted, every DB-backed endpoint returning 500)
  was diagnosed and fixed: backends 100 → 7–13, blocked 44 → 0, all endpoints
  200. Measured before and after.
- The WebSocket handler leak (`trades` channel 4 → 125 sockets in 12 minutes)
  was fixed: 493 connects since, live count stable at 1–2. Measured.
- Candle row volume per run falls ~366× — measured against the live broker,
  with the OLD model validated against production's own run results.

**Not yet proven better:**
- Candle *freshness* — the headline metric — has not been observed under beat.
  It is a projection until 09:15 tomorrow.
- News capture, event traceability, timezone, capital sizing: unchanged.

**A deployment is not a fix.** The candle change is deployed, tested and
measured against real broker data, and its rollback is one command. Whether it
delivers the freshness improvement is tomorrow morning's measurement, and this
report does not claim it in advance.

---

*No order was placed. No strategy parameter, threshold, prompt, risk limit or
capital control was changed. BUG-1 and BUG-2 remain as they were.*
