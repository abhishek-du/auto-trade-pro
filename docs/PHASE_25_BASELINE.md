# PHASE 25 — BASELINE (recorded before any change)

Taken 2026-08-26, ~19:10 IST, before the Phase-25 work began.

## Git

| | |
|---|---|
| Branch | `fix/audit-2026-08-19-critical` |
| HEAD | `50d6834` docs(research): phase 24 |
| origin/main | `5f32f08` Merge: phase 24 |
| Staged | 0 |
| Uncommitted work of mine | **none** — everything from Phases 23–24 was committed |

## Tests

| | |
|---|---|
| passed | **1,791** |
| failed | 27 |
| skipped | 7 |
| errors | 5 |

The 27 failures and 5 errors are pre-existing and unrelated
(`test_trade_simulator_confirmation_lost.py` references
`_DIRECT_NEWS_RECHECK_STATE`, which does not exist; plus alert-render,
pre-event-gap and upstox-ISIN suites). They have been constant since the
Phase-19 baseline.

## Wallet

| | ₹ |
|---|---:|
| balance | 477,054.99 |
| equity | 501,863.67 |
| realised | 805.77 |
| unrealised | 1,057.89 |
| total_trades | 72 |

## Classification of the Phase 23–24 changes already in the repo

| Change | Class | Deployed at baseline? |
|---|---|---|
| `estimate_trade_cost(..., product=)` — simulator | **accounting, behaviour-affecting** | **NO** |
| `estimate_trade_cost(..., product=)` — backtester duplicate | accounting | NO |
| Scan funnel telemetry (`tactical_executor.py`) | measurement-only | yes (18:32) |
| Universe snapshot (`hub_universe.py`) | measurement-only | yes |
| Candle delta window (`zerodha_historical.py`) | engineering fix | yes (16:36) |
| Pre-market age cutoff (`news_discovery_engine.py`) | **strategy-adjacent** (narrows what is replayed) | yes (17:05) |
| Phase 24 research | none — documentation | n/a |

**Important state at baseline:** `trade_simulator.py` mtime 19:03:27 was *after*
every worker start (17:16 / 18:32), so **no running process carried the cost
correction.** It was committed but not deployed — a clean starting point for a
deliberate `V1_COST_CORRECTED` deployment.

## Services at baseline

All 7 active, `NRestarts=0`: uvicorn (3312032), celery-worker (3439376),
celery-beat (3300942), news-engine (3391682), trade-worker (3397687),
exit-worker (3397690), scan-worker (3439377).

## Historical data

No `paper_trades`, `candles` or other historical row was rewritten at any point
in Phase 25.
