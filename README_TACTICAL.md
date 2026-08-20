# Path F — Tactical Pipeline

An independent technical-signal pipeline (intraday momentum + mean reversion)
running alongside the news paths. **It runs in shadow mode: it generates, scores
and sizes signals, and writes them to `tactical_signals`. It does not trade.**

---

## Why shadow mode

Path F originates signals from technical conditions — ORB, VWAP, pivots,
Bollinger/RSI extremes — with no news event behind them.
`docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md` forbids exactly that:

- §1 line 49 — *"No component downstream of 'Technical Validation' may originate
  a trade."*
- §6 line 281 — *"Technical indicators → FILTER ONLY, universally — never a
  trigger, for any strategy."*
- §10 line 347 — forbids *"constructing a TradeIntent from a technical/score
  condition alone."*
- §2 line 162 pre-empts renaming around it: *"`strategy_family` is a label a
  caller sets; it is not proof."*

So Path F is not wired to execution, and adding a `StrategyFamily.TACTICAL`
would not be enough on its own — verified in `engine/decision_router.py`, adding
the enum member alone makes a family executable, because every family block is
an `==` test against one member and there is no allowlist.

§6 line 285 also rejects flag-guarded execution as *"disabled by configuration,
which is reversible by anyone who flips the flag without knowing this contract
exists."* Accordingly, **the tactical package contains no execution import at
all** — not `execute_trade_intent`, not `open_paper_trade`, not
`place_real_order`, not even behind a flag.
`tests/test_tactical_shadow_mode.py` enforces this by AST-scanning the package.

Shadow mode is how Path F earns evidence without weakening a guardrail: it
records exactly the trades it would have taken, at exactly the size it would
have taken them.

---

## What runs

| Sub-pipeline | Cadence | Timeframe | Universe | Strategies |
|---|---|---|---|---|
| **F1** Intraday momentum | every 1 min | `1m` | top 50 by `hub_universe` rank | ORB, VWAP, Gap-and-Go, Pivot bounce/breakout, Scalp (engulfing) |
| **F4** Mean reversion | every 5 min | `5m` | top 150 with 5m data | Overbought fade, Oversold rebound |

Both run 09:15–15:20 IST on weekdays (beat entries use `hour="3-10"` UTC —
Celery runs on UTC, so an IST-looking hour range would fire in the evening).

**F2 (swing) and F3 (sector rotation) are not built yet.** F2 was specified on
15-minute candles, but that timeframe holds 125 rows for a single symbol, last
written 2026-06-25 — it is unusable. F2's rules are all daily by nature
(52-week high, ROC-10, RSI/MACD over 5 days, 20-day high, golden cross), so
Phase 2 will run it on daily candles at the 15-minute cadence.

## The three layers

1. **Rule scoring** (`tactical_scoring.py`) — composite 0-100 from the rule's own
   confidence, a volume z-score capped at ±2, sector mood read from
   `MasterIntelligenceScore`, and an RSI/MACD alignment bonus. Drops below 50,
   keeps the top 15.
2. **ML ranker** (`tactical_ml_ranker.py`) — **a documented placeholder.** No
   model exists and `xgboost` is not installed; `predict_proba` returns a
   neutral 0.5 and ranking falls back to Layer-1 order. Training needs a
   labelled set built by replaying signals (the point-in-time hook already
   exists: `get_latest_candles(..., before=…)`). A model trained on fabricated
   labels would be worse than none — see `tasks/ml_optimizer.py` for what that
   looks like.
3. **LLM veto** (`tactical_llm_veto.py`) — stub, always PASS, explicitly marked
   `checked=False` so a row never implies a veto check that did not happen.

## Risk

Path F caps itself, because it cannot inherit anyone else's cap:
`engine/risk_manager.validate_signal` is family-blind, and its per-strategy
capital cap keys on the free-text strategy name — so a new name receives a full
fresh allocation rather than sharing the news families' budget.

| Setting | Default | Meaning |
|---|---|---|
| `TACTICAL_CAPITAL` | 500,000 | notional capital base |
| `TACTICAL_MAX_TOTAL_RISK` | 0.02 | 2% — the whole tactical bucket |
| `TACTICAL_MAX_PER_TRADE_RISK` | 0.005 | 0.5% per trade → 4 concurrent trades fills the bucket |
| `TACTICAL_VIX_THRESHOLD` / `_SIZE_SCALE` | 25.0 / 0.5 | halve size above the threshold |

Plus a 3-consecutive-stop cooldown (60 min) and a duplicate-position guard that
blocks any symbol already open in any strategy.

> The cooldown is in-memory, so a worker restart clears it. Acceptable while
> nothing executes; **it must move to Redis before Phase 2.**

## Monitoring

```sql
-- today's signals
SELECT sub_pipeline, strategy, signal_type, symbol, composite_score, quantity, risk_amount
  FROM tactical_signals WHERE timestamp::date = current_date ORDER BY id DESC;

-- the invariant: this must always return 0
SELECT count(*) FROM tactical_signals WHERE executed = true;

-- why signals were blocked
SELECT split_part(reason, 'blocked: ', 2) AS blocked, count(*)
  FROM tactical_signals WHERE reason LIKE '%blocked%' GROUP BY 1 ORDER BY 2 DESC;
```

Logs: `logs/tactical_pipeline.log` (own sink, 14-day retention) and the shared
application log.

## Disabling it

```bash
# In .env — then restart the worker and beat (watchmedo watches *.py only)
TACTICAL_PIPELINE_ENABLED=false
```
Or remove the `tactical-intraday-1min` / `tactical-meanrev-5min` entries from
`tasks/celery_app.py`. Either way nothing else is affected — Path F shares no
state with the news paths and writes to no other table.

## Measured performance

On live market data, 2026-08-20:

| Scan | Universe | Elapsed | Signals |
|---|---|---|---|
| F1 | 50 | **5.8s** | 11 raw → 5 persisted |
| F4 | 147 | **13.9s** | 1 raw → 1 persisted |

Both well inside the 50s soft limit. The first implementation fetched prices
per symbol and took **34.5s for 10 symbols** (~3.4s each, because every miss
walks WS tick → Kite REST → yfinance); batching through
`zerodha_market.get_live_prices` — which chunks at 200/request and uses the
shared rate limiter — is what made the 1-minute cadence viable.

The worker is `--concurrency=2` on a single queue shared with the 5-second
`fast_sl_check` exit loop, so scans are time-boxed and abandon cleanly on
`SoftTimeLimitExceeded` rather than being killed mid-transaction.

## Phase 2 / Phase 3

**Phase 2** — F2 (daily) and F3 (sector rotation); `StrategyFamily.TACTICAL`
with a blocked-by-default gate branch mirroring PRE_EVENT/DIRECT_NEWS;
execution wiring. This lands **together with a written amendment to contract
§6 and §10**, or not at all. Move the cooldown to Redis first.

**Phase 3** — real XGBoost ranker (needs the labelling job) and the LLM news
veto.

## Files

```
engine/tactical_data_fetcher.py     candles → oldest-first DataFrame, batched prices, universe
engine/tactical_rules.py            pure strategy functions → Signal
engine/tactical_scoring.py          Layer 1 composite score
engine/tactical_ml_ranker.py        Layer 2 (placeholder)
engine/tactical_llm_veto.py         Layer 3 (stub)
engine/tactical_risk.py             TacticalRiskManager — 2% bucket, sizing, cooldown
engine/tactical_duplicate_guard.py  open-position check
engine/tactical_executor.py         orchestration; ends at session.add
tasks/tactical_tasks.py             Celery entry points + Redis overlap guards
db/migrations/versions/0006_tactical_signals.py
```
