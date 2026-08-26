# Path F — Audit Blocker Fixes

Addresses the blockers in [`docs/PATH_F_AUDIT_2026-08-20.md`](docs/PATH_F_AUDIT_2026-08-20.md).
Verified live during NSE hours. Full suite **1444 passed / 27 failed / 5 errors — zero net-new failures**.

| Task | Status |
|---|---|
| 1 · Persist risk bucket to Redis 🔴 | ✅ done, verified live |
| 2 · Persist cooldown to Redis 🟡 | ✅ done (same file/bug class) |
| 3 · Fast candle lane for F1 🟠 | ✅ done, verified live |
| 4 · Flag-guarded execution wiring | ⛔ **skipped by decision** — see below |

---

## Task 1 & 2 — Redis-backed risk bucket and cooldown

**Problem.** `TacticalExecutor` is constructed fresh by every Celery run, so
`open_risk` / `cooldown_until` / `consecutive_losses` were instance attributes that
reset each minute. The 2% daily cap was only ever enforced *within* one scan. The
audit measured 322 would-trade signals totalling **₹793,907** against a ₹10,000
bucket — **79× over**.

**Fix.** Per-trading-day Redis keys:

```
tactical:risk:{YYYY-MM-DD}              INCRBYFLOAT, TTL 2 days
tactical:cooldown:{YYYY-MM-DD}          SETEX, self-expiring after 3600s
tactical:cooldown:{YYYY-MM-DD}:streak   consecutive-loss counter
```

- **Fails CLOSED**, unlike the Kite rate limiter. That one fails open because a
  throttling outage must never wedge the trading loop; a risk cap that stops
  applying when Redis blips is not a cap. Cost here is a skipped signal.
- **`INCRBYFLOAT` is atomic**, so two workers committing at once cannot lose an
  increment — which a read-modify-write on an instance attribute very much could.
- The **loss streak** moved to Redis too, not just the cooldown timestamp: otherwise
  a redeploy mid-streak silently resets the count and the 3rd loss never pauses.
- `reset_daily_risk()` added as a manual escape hatch.

**Live proof.**
```
8 simulated Celery cycles -> 4 approved (₹10,000), cycles 5-8 BLOCKED
  "would exceed tactical bucket: 2500 > 0 remaining of 10000"
Live F1 scan with bucket at ₹9,523.90 -> persisted=5, skipped=5, bucket unchanged
```

**Spec deviations** (the named APIs don't exist):
`utils/redis_client.py` → **`utils/cache.py::get_redis()`**, which is *async* and
re-creates per event loop (required under Celery prefork), so `size()`/`commit()`
are now async. `can_take_trade()`/`update_consecutive_losses()` → the real methods
are `size()`/`record_stop_loss()`.

---

## Task 3 — Fast 1-minute candle lane

**Problem.** `kite_live_candles` bulk-fetches thousands of symbols, so the newest
DB 1m bar trails 15–40 min (measured at **37.4 min**). F1's own freshness guard was
rejecting whole stretches of the session, and when it passed, ORB/VWAP/pivots were
computing on half-hour-old bars.

**Fix.** `crawler/live_candle_builder.py` aggregates the existing WebSocket tick
stream into 1-minute bars and publishes them to Redis:

```
fast_candle:{symbol}:1m   capped list, newest-first, 120 bars, 1h TTL
```

- Runs in the **uvicorn** process — `LIVE_TICKS` is a module dict owned by the
  ticker thread there. The tactical pipeline runs in the **Celery worker**, a
  different OS process, so Redis is the transport (same pattern as the price
  snapshot).
- Costs **no Kite REST quota** (doesn't compete with the D6 limiter) and adds **no
  Celery task** to the 2-slot queue.
- `get_candles_df()` merges fast bars onto the DB frame **before** judging
  staleness. Order matters: checking first would reject the frame and the fast bars
  would never get to rescue it — exactly the state F1 was in.
- Dedup on timestamp with the **DB winning**, since it is the authoritative
  aggregate.

**Live proof (BHARTIARTL.NS).**
```
DB     200 bars  newest 09:01:00   age 9.6 min
FAST     1 bar   newest 09:09:00
MERGED 201 bars  newest 09:09:00   age 1.6 min
```
39 symbols publishing. Sample bar:
`{"timestamp":"2026-08-20T09:09:00","open":1946.5,"high":1946.5,"low":1946.4,"close":1946.4,"volume":2737.0,"samples":5}`

### ⚠️ Honest limits of these bars

1. **Sampled, not tick-exact.** ~12 observations/minute at the 5s cadence, so
   high/low are slightly **understated** — an extreme between two samples is
   missed. Fine for VWAP/RSI/breakout levels; not exact wick data. Each bar carries
   a `samples` count so a reader can judge its quality.
2. **Fills forward only.** State starts empty on process start; the Redis history
   covers only minutes observed since. It closes the DB gap after ~30 min of uptime
   and self-heals after a restart.
3. **Volume is a delta** of the cumulative `volume_traded`. The first bucket after
   startup reports 0 rather than a bogus day-to-date total.

New settings: `TACTICAL_FAST_CANDLE_ENABLED=True`,
`TACTICAL_FAST_CANDLE_INTERVAL_SEC=5`, `TACTICAL_FAST_CANDLE_MAX_AGE_MIN=2`.
The 30-min DB threshold is retained as the fallback.

---

## Task 4 — skipped, by decision

Task 4 asked for an execution call guarded by `TACTICAL_EXECUTION_ENABLED=False`.
That is specifically what contract §6 line 285 rejects:

> *"a feature-flagged-off strategy that still contains a live call to one of these
> functions is not 'safely disabled' — it is disabled by configuration, which is
> reversible by anyone who flips the flag without knowing this contract exists."*

It was also self-contradictory: it specified building a `TradeIntent` with
`strategy_family="TACTICAL"` while leaving `StrategyFamily.TACTICAL` commented out
of the router — but `TradeIntent` requires that enum member to exist.

**Decision: keep shadow structural.** `tactical_executor.py` still contains no
execution import, and `tests/test_tactical_shadow_mode.py` keeps AST-scanning the
package to enforce it. When you go live, the enum + wiring + the §6/§10 contract
amendment land in **one reviewed commit**.

---

## Verification

```bash
redis-cli --scan --pattern "tactical:*"      # risk + cooldown keys
redis-cli --scan --pattern "fast_candle:*"   # 39 symbols
redis-cli get "tactical:risk:$(date +%F)"    # committed risk today
cd autotrade-backend && .venv/bin/python -m pytest tests/test_tactical_*.py tests/test_live_candle_builder.py -q
```

**Tests added:** 19 risk (incl. a replay of the audit's 322-attempt scenario asserting
cumulative risk ≤ ₹10,000) + 20 candle builder.

## Still open from the audit

- Duplicate guard is verified by direct test but **unexercised in production** — both
  held symbols sit outside F1's top-50 universe.
- **Layers 2 and 3 are pass-throughs.** Ranking is Layer-1 score only and no signal
  has been screened for news risk. Do not read shadow P&L as evidence of edge yet.
- F2, F3 and `tactical_journal.py` remain unbuilt (Phase 2).
