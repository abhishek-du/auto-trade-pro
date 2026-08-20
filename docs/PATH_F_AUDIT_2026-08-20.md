# Path F — Tactical Pipeline: Verification Audit

**Date:** 2026-08-20 · **Market:** OPEN (verified live during NSE hours)
**Commit range:** `5451353` … `9041b40` · **Branch:** `fix/audit-2026-08-19-critical`
**Method:** static review + live execution against the running system + DB queries.

---

## 1. Executive Summary

**Status: ✅ PASS for shadow mode · ❌ FAIL for execution**
**Recommendation: `FIX_BEFORE_GO_LIVE`** — continue shadow collection; do **not** wire execution yet.

Path F is running live and behaving correctly *as built*. Every safety invariant
holds and the pipeline is producing real signals on live data.

**But the audit checklist assumes a system that was deliberately not built.** Phase 1
was approved as **shadow-only**: it generates, scores and sizes signals, writes them to
`tactical_signals`, and stops. There is no `StrategyFamily.TACTICAL`, no
`execute_trade_intent` call, no position, no Telegram alert. Checklist items 3.11,
3.6 (execution half), and all of §4's position/alert queries test for the absence of
that decision, not for defects.

| Severity | Count | |
|---|---|---|
| 🔴 Critical (blocks execution) | **1** | 2% risk bucket resets every cycle |
| 🟠 High | **1** | F1 trades on 20–40-minute-old indicators |
| 🟡 Medium | **3** | in-memory cooldown; duplicate guard untested in prod; F1 duty cycle |
| ⚪ Scope gap (approved) | **6** | F2, F3, journal, ML model, LLM veto, router wiring |
| ✅ Fixed during audit | **1** | ML ranker log format |

**Nothing found endangers the live book** — the shadow invariant held throughout.

---

## 2. Component-by-Component Review

### 2.1 `tactical_data_fetcher.py` — ⚠️ PASS with concern

| Check | Verdict | Evidence |
|---|---|---|
| Live price via fixed `get_price`/snapshot | ✅ | Uses `get_market_snapshot` (age-checked since D3), returns `None` not `0` on failure |
| Candles → DataFrame, oldest-first | ✅ | Reverses `get_latest_candles` (which returns newest-first) — inverting this silently flips every trend |
| Forming-bar handling | ✅ | Deliberately **not** truncated here; `compute_indicators(exclude_forming_bar=True)` does it once. Truncating in both places would drop two bars |
| Error handling | ✅ | Every fetch wrapped; failures logged and degrade to `None` |
| `get_sector_data()` / `get_macro_data()` | ❌ **absent** | F3 deferred to Phase 2. `get_market_context()` covers VIX + NIFTY |
| Batched price fetch | ✅ | Added after measuring 34.5s for 10 symbols per-symbol → **5.8s for 50** batched |

**🟠 HIGH — staleness threshold vs real ingestion lag.** `MAX_BAR_AGE_MIN["1m"] = 30`,
calibrated on a measured 15–20 min lag. Observed today at **37.4 min**, so the guard
rejected F1 entirely for stretches:

```
1m: newest=08:02:00  age=37.4min  limit=30min  -> REJECTED
5m: newest=08:10:00  age=29.4min  limit=45min  -> OK
```

This is the guard working as designed (fail-closed, added after finding F4 signalling
on 21-hour-old bars). The real problem is upstream: `kite_live_candles` fetches
thousands of symbols per run, so the newest 1m bar structurally trails.

### 2.2 `tactical_rules.py` — ✅ PASS (F1/F4 scope)

| Strategy | Built | Verified |
|---|---|---|
| ORB | ✅ | 09:15–09:30 window; `>1.002` / `<0.998`; volume ≥1.5× |
| VWAP | ✅ | 2 consecutive **closed** minutes; reuses `IndicatorSignals.vwap` |
| Gap-and-Go | ✅ | >1% gap + continuation + RSI>60 |
| Pivot bounce/breakout | ✅ | P/R1/S1/R2 from previous **closed** daily bar |
| Scalp | ✅ | Reuses existing `patterns` (`Bullish Engulfing`) — no second candlestick impl |
| Mean reversion (F4) | ✅ | RSI + Bollinger; targets middle band |
| 52-week high, ROC, RSI/MACD trend, volume breakout, golden cross | ❌ | **F2 — deferred**, `15m` timeframe holds 125 rows for 1 symbol, last written 2026-06-25 |
| Sector rotation | ❌ | **F3 — deferred** |

**Lookahead: clean.** 2 × `exclude_forming_bar=True`, 12 × `closed(df)` guards, zero raw
`df.iloc[-1]` outside them. `_vol_surge` deliberately excludes its own window from the
trailing mean — the flaw audit D5 found in `_momentum_breakout_score`.

**Signal integrity — live proof:** of **408** signals, **0** have stop/target on the wrong
side and **0** have null levels. The `is_sane()` guard is doing real work: it caught a bug
during build where fading a runaway price put the short's stop *below* entry.

### 2.3 `tactical_ml_ranker.py` — ✅ PASS (as a documented placeholder)

`model_available()=False`, `predict_proba()=0.5`. No `models/tactical_xgb.json` and
`xgboost` is not installed — **approved**: there is no labelled training set, and a model
trained on fabricated labels is worse than none (`tasks/ml_optimizer.py` is the cautionary
example already in this repo).

Correct design detail: with no model, the `min_prob=0.55` filter is **not** applied — else
neutral 0.5 would silently drop every signal and look like "found nothing".

**✅ Fixed during audit:** the log printed literal `%s`/`%.2f` (loguru uses `{}`). Committed `9041b40`.

### 2.4 `tactical_llm_veto.py` — ✅ PASS (stub, honestly labelled)

Returns `vetoed=False, checked=False` with reason *"llm veto not implemented (Phase 3) —
signal not screened for news risk"*. The `checked` flag matters: a row can never imply a
veto check that never ran. No headline fetch, no LLM call — Phase 3.

### 2.5 `tactical_risk.py` — 🔴 **CRITICAL**

| Check | Verdict | Evidence |
|---|---|---|
| Per-trade ≤ 0.5% | ✅ | max observed `risk_amount` = **2,499.99** vs 2,500.00 cap |
| VIX > 25 halves size | ✅ | code + unit tests |
| Kelly/ML multiplier | ✅ | and correctly **skips** the neutral sentinel — else the stub shrank every position 30% |
| 3-loss cooldown | ⚠️ | implemented, but in-memory |
| **Total ≤ 2% across time** | ❌ **NO** | see below |

**🔴 The 2% bucket resets every cycle.** `tasks/tactical_tasks.py:75` constructs
`TacticalExecutor()` per run → `TacticalRiskManager()` → `open_risk = 0.0`.

Live measurement:

```
would-trade signals today : 322
cumulative risk           : Rs 793,907
2% bucket                 : Rs  10,000   -> exceeded 79x
blocked within-cycle      : 72
```

The cap is enforced **within** a scan (72 signals correctly rejected with
*"would exceed tactical bucket: 2496 > 354 remaining of 10000"*) but resets every minute.
**Harmless today because nothing executes. Blocking for Phase 2** — the headline safety
property of the whole pipeline is not actually enforced over time.

The 60-minute cooldown has the same defect and additionally clears on worker restart.

### 2.6 `tactical_duplicate_guard.py` — ✅ PASS (verified by direct test)

Spec asked to filter `positions.strategy_family` — **that column does not exist**. Guard
joins `open_positions.trade_id → paper_trades.strategy_name/source` instead.

```
open map: {'ZAGGLE': 'DIRECT_NEWS', 'JUNIPER': 'DIRECT_NEWS'}
JUNIPER.NS     duplicate=True   news-family position already open (DIRECT_NEWS)
ZAGGLE.BO      duplicate=True   news-family position already open (DIRECT_NEWS)
RELIANCE.NS    duplicate=False
```

Correctly identifies news-family ownership. **Fails closed**: raises on lookup failure
rather than returning `{}`, so "DB down" can't read as "nothing open" — the executor then
skips the cycle.

**🟡 0 production blocks so far** — both held symbols are outside F1's top-50 universe, so
the guard is verified by direct test but not yet exercised by the live pipeline.

### 2.7 `tactical_executor.py` — ✅ PASS (shadow scope)

Full chain verified live: universe → batched prices → rules → Layer 1 → Layer 2 → veto →
duplicate → risk → persist. Ends at `session.add`. Per-symbol failures are caught and
skipped; `SoftTimeLimitExceeded` rolls back and abandons cleanly.

Deliberately **absent**: `TradeIntent`, `execute_trade_intent`, `strategy_family='TACTICAL'`,
Telegram. Enforced structurally by `test_tactical_shadow_mode.py`, which AST-scans the
package for execution symbols.

### 2.8 `tactical_journal.py` — ❌ **NOT BUILT** (was marked optional)

### 2.9 `tasks/tactical_tasks.py` — ✅ PASS

Redis `SET NX EX`, TTL 65s > 60s hard limit, released in `finally`. Soft/hard 50/60s.
Uses a per-call client (Celery prefork gives each task its own loop) — not
`utils.cache.get_redis`, deliberately.

### 2.10 Beat schedule — ✅ PASS

```
tactical-intraday-1min  expires=55s   registered=True
tactical-meanrev-5min   expires=280s  registered=True
```

`hour="3-10"` **UTC** = 09:15–15:30 IST. The spec's `hour='9-15'` would have fired
14:30–21:00 IST — entirely outside market hours. Explicit `expires` added after a live
backlog replayed ~4 stale F1 cycles in one minute.

### 2.11 `TacticalSignal` model — ✅ PASS

Table exists with 17 columns and 3 indexes; 408 rows. Created via
`Base.metadata.create_all` — **Alembic has never run in this repo** (no `alembic.ini`, no
`alembic_version` table), so migration `0006` is committed for completeness only.

### 2.12 Settings — ✅ PASS

```
TACTICAL_PIPELINE_ENABLED = True     TACTICAL_MAX_TOTAL_RISK     = 0.02
TACTICAL_EXECUTION_MODE   = shadow   TACTICAL_MAX_PER_TRADE_RISK = 0.005
TACTICAL_CAPITAL          = 500000.0 PAPER_MODE                  = True
```

### 2.13 `decision_router.py` — ⚪ DELIBERATELY UNCHANGED

`StrategyFamily.TACTICAL` absent by design. Verified: adding the enum member **alone**
would make it execute — every family block is an `==` test against one member, there is no
allowlist, and `_verify_canonical_event` no-ops for non-`EVENT_DRIVEN` families.

Contract §6 line 285 rejects flag-guarded execution as *"disabled by configuration, which
is reversible by anyone who flips the flag without knowing this contract exists."*

---

## 3. Live Test Results

```
F1: scanned=50  raw=30  kept=15  persisted=5  skipped=1
F4: scanned=65  raw= 4  kept= 4  persisted=4  skipped=0
```

Signal mix (408 total): VWAP 162 · PIVOT_BREAKOUT 89 · GAP_AND_GO 56 ·
OVERBOUGHT_FADE 38 · OVERSOLD_REBOUND 36 · PIVOT_BOUNCE 9 · SCALP 4

| Query | Result |
|---|---|
| `executed = true` | **0** ✅ |
| bad stop/target levels | **0 / 408** ✅ |
| null levels | **0** ✅ |
| max `risk_amount` | 2,499.99 ≤ 2,500 ✅ |
| `NotRegistered` errors | **0** ✅ (D9 fix holding) |
| tactical `TypeError`/`KeyError` | **0** ✅ |
| tactical test suite | **69 passed** ✅ |

Errors found: 5 `SoftTimeLimitExceeded`, all 10:20–10:27, caused by my own manual timing
scans contending with scheduled ones for Kite rate-limiter slots. **None since.**

`open_positions` and `paper_trades` unchanged throughout.

---

## 4. Risk & Performance Assessment

| Control | Status |
|---|---|
| Per-trade 0.5% | ✅ enforced, never breached |
| **Total 2%** | 🔴 **per-cycle only — resets each run** |
| VIX scaling | ✅ |
| 3-loss cooldown | 🟡 in-memory; resets on restart |
| Duplicate positions | ✅ verified; fails closed |
| Paper trades recorded/costed | ✅ n/a — nothing executes |

**Performance:** F1 5.8s / 50 symbols, F4 13.9s / 147 — both inside the 50s soft limit.
Worker is `--concurrency=2` shared with the 5s `fast_sl_check`; scans are time-boxed.

---

## 5. Recommendations

### Before ANY execution (blocking)
1. **🔴 Persist the risk bucket to Redis**, keyed by trading day. Today's cumulative
   would-trade risk was 79× the cap. This is the single blocking issue.
2. **🟠 Move the cooldown to Redis** — same reset defect, plus worker-restart amnesia.
3. **🟠 Decide what F1 is for.** Its indicators are 20–40 minutes old *when the guard
   passes at all*. ORB/VWAP/scalping on 30-minute-old bars is not intraday trading. Either
   fix ingestion latency (a dedicated fast lane for the F1 universe), or move F1 to the 5m
   timeframe and stop calling it 1-minute momentum.
4. **Wire execution only together with the contract amendment** (§6 authority matrix, §10
   forbidden patterns), never behind a flag alone.

### Before trusting shadow results
5. Duplicate guard is unexercised in production — widen the universe or wait for overlap.
6. Layers 2 and 3 are pass-throughs. Current ranking is Layer-1 score only, and **no signal
   has been screened for news risk**. Do not read shadow P&L as strategy-quality evidence.

### Optional
7. Build `tactical_journal.py`; build F2 (on daily) and F3.
8. Consider raising `MAX_BAR_AGE_MIN["1m"]` to ~45 only if you accept (3) — otherwise the
   guard is correctly telling you the data isn't fresh enough.

---

## 6. Logs Snapshot

```
[tactical:F1] scanned=50 raw=25 kept=15 persisted=5 skipped=1
[tactical:F4] scanned=65 raw=4 kept=4 persisted=4 skipped=0
[tactical:F1] scanned 0 of 50 symbols — no usable candles (feed stale or missing)
[tactical_ml] no model at models/tactical_xgb.json — Layer 2 is a pass-through (neutral 0.50)
```

Sample persisted row:
```
COFORGE.NS  GAP_AND_GO  F1 BUY  entry=1873.00 sl=1795.00 tgt=1910.46
            score=85.28  qty=32  risk=2496.00  executed=false
reason: shadow mode — Path F does not execute (see NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md)
```

Bucket cap firing correctly:
```
blocked: would exceed tactical bucket: 2496 > 354 remaining of 10000
```
