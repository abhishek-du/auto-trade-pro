# AutoTrade Pro — Stale-Data Architecture Audit

**Date:** 2026-07-09
**Auditor scope:** Deeply traced the decision→execution spine (scheduler → hub cycle → trade loop → decision engine → execution → price feed) and sampled the periphery. Every claim below cites a `file:line` that was read directly. Modules **not** read are called out explicitly rather than asserted.

---

## 1. Executive Summary

The reported concern ("system computes a decision after close and blindly fires it at 9:15") is directionally right but the mechanism is different. The system does **not** naively execute yesterday's decision. It has real, recently-added freshness defenses (a 5% divergence guard, fail-closed live-price snap, candle-age gates, a staleness watchdog).

**The actual problem is architectural fragmentation:** there are **three** code paths that can open trades, and they do **not** share the same freshness rules.

| Path | Scheduled? | Candle-age guard | Live-price snap + divergence guard | Signal-age (score) guard |
|---|---|---|---|---|
| `_process_symbol` (agent_loop.py) | **NO** (dead / manual only) | ✅ ≤72h (1d) | ✅ fail-closed, 5% reject | ✅ 2h cutoff |
| Hub inline executor (`run_master_intelligence_cycle`) | ✅ every 15 min | ❌ only `len<20` | ❌ **none — fills at candle close** | N/A (scores in-cycle) |
| `_india_trade_loop` | ✅ every 60 s | live-price path (≤90 min) | live entry ✅, but **no divergence guard** | ❌ **no `scored_at` cutoff** |

The one path that fully implements the anti-stale logic from the 2026-07-08 fix (`agent_loop._process_symbol`) is **not in the Celery beat schedule** — it is effectively dead code reachable only by the manual trigger. The two paths that actually run in production each have a *different hole*.

**Production readiness for real money: 48/100.** Safe-ish in paper mode; not ready for live capital until the three paths are unified behind one freshness gate.

---

## 2 & 3. Architecture & Current Pipeline (as actually wired)

```
Celery Beat (celery_app.py:61 beat_schedule)
│
├─ tasks.run_master_intelligence_cycle  ── crontab hour="3-10" UTC, every 15m   [PATH A]
│     (celery_app.py:380)  → india_tasks.py:2748
│     build_master_context → score_universe(1d) → persist_scores  [ALWAYS]
│     if _is_market_hours(): inline executor  [EXECUTES, india_tasks.py:2865-2990]
│
├─ tasks.india_trade_loop  ── every 60s, window 09:15–16:00 IST                  [PATH B]
│     (celery_app.py:222) → india_tasks.py:512 → _india_trade_loop
│     read latest MasterIntelligenceScore per symbol (NO age gate) → open_paper_trade
│
├─ tasks.fast_sl_check  ── every 5s (exits only, Kite LTP + yfinance backstop)   [GOOD]
│     (celery_app.py:199) → india_tasks.py:1301
│
├─ tasks.intraday_entry ── 09:30 IST MIS entries
│
└─ tasks.run_agent_cycle  ── ❌ NOT SCHEDULED  → agent_loop._process_symbol      [PATH C, DEAD]
      (the only fully hardened path — 5% divergence, fail-closed snap, 2h score cutoff)

Data feeds:
  PRICE_CACHE (live_prices.py:24)  ← yfinance poll 15s/60s + Kite ticker
  LIVE_TICKS  (zerodha_ticker.py)  ← KiteTicker websocket (FastAPI proc only; NOT in Celery workers)
  MasterIntelligenceScore table    ← written pre-open, intraday, AND post-close
```

Confirmed: `grep "run_agent_cycle" celery_app.py` → **NOT in beat schedule**. The scheduled scorer/executors are Path A and Path B.

---

## 4. Problems Found (the stale-data reality)

### The score-timing window (the core concern, precisely located)

`master-intelligence-every-15min` = `crontab(hour="3-10", minute="14,29,44,59")` UTC (`celery_app.py:382`). NSE 09:15–15:30 IST = 03:45–10:00 UTC. That schedule therefore fires:

- **03:14 UTC = 08:44 IST and 03:29 = 08:59 IST → BEFORE the 9:15 open.** `persist_scores` is **not** market-hours gated (`india_tasks.py:2840-2841`), so scores computed on **yesterday's daily close + overnight-stale news/macro, before any live tape exists,** get written to the table.
- **10:14–10:59 UTC = 15:44–16:29 IST → AFTER the 15:30 close.**

Execution inside Path A *is* gated (`if _is_market_hours()`, `india_tasks.py:2865`), so Path A won't fire pre-open/post-close. **But Path B (`_india_trade_loop`) has no score-age filter at all** (see CRIT-2), so in the **09:15–09:28 window** — before the first in-session hub cycle at 09:29 IST — it reads the **08:44/08:59 pre-open scores** (15–44 min old, "fresh" by any 2h rule) and can open trades whose *direction and conviction were decided before the market opened.* The live-price snap fixes the *fill price*, not the *decision*.

This is the real version of the "trades from yesterday's market" concern: not stale prices (mostly guarded) but **a stale signal generated on pre-market/overnight information driving today's entry.**

---

## 5. Critical Bugs

**CRIT-1 — Three execution paths, inconsistent freshness; the hardened one is unscheduled.**
`agent_loop._process_symbol` (the 2026-07-08 fix with fail-closed snap + 5% divergence + 72h candle gate, `agent_loop.py:442-597`) is reachable only via `tasks.run_agent_cycle`, which is **not scheduled**. Production runs Paths A and B, each missing part of that protection. *Consequence:* the fix believed to protect live trading does not run in the live loop.

**CRIT-2 — `_india_trade_loop` has no signal-age cutoff.**
`india_tasks.py:613-649`: the candidate query joins on `max(scored_at)` per symbol and filters only `is_blocked==False` + actionable signal. There is **no `scored_at >= cutoff`**. The code comment (`:609-612`) even acknowledges the "days old" risk but only fixed *per-symbol latest*, not *absolute freshness*. *Failure scenario:* the hub cycle is skipped by its own overlap guard (`india_tasks.py:2805`) or errors for several ticks → the "latest" score silently ages → this 60s loop keeps trading a score from hours/days ago, entry snapped to a live price that no longer matches the thesis. Compare `fetch_hub_candidate` which *does* enforce 2h (`decision_engine.py:704`).

**CRIT-3 — Hub inline executor fills at the candle close with no live snap / no divergence guard.**
Path A inline executor (`india_tasks.py:2908-2990`) loads `get_latest_candles(AGENT_TIMEFRAME="1d")`, builds a candidate via `selector.propose` (entry = candle close), runs `de.fuse` → `executor.execute(decision)`. `_paper_execute` uses `decision.entry` **verbatim** (`execution.py:56, 93`). There is **no equivalent of the `agent_loop.py:527-597` live-snap/divergence block** here, and **no candle-age guard** (only `len(candles)<20`, `:2909`). *Failure scenario:* on a daily-timeframe run at 09:29 IST after a gap-up open, this path books the entry at *yesterday's 1d close* while the tape is materially higher/lower — the exact phantom-fill class the earlier TBZ/JINDRILL incidents describe, now on the *scheduled* path.

---

## 6. Medium Issues

**MED-1 — Stale ticks reported as `age_seconds: 0.0`.** `get_live_tick` returns `LIVE_TICKS.get(token)` with **no timestamp validation** (`zerodha_ticker.py:44`); `get_price` stamps the Kite path `"age_seconds": 0.0` unconditionally (`live_prices.py:52-58`). At the first read after open, or during a feed stall, the last stored tick can be yesterday's 15:30 print but reads as real-time. In `_process_symbol` a stale tick ≈ candle close ⇒ ~0 divergence ⇒ passes the 5% guard. *No caller rejects on `age_seconds`.*

**MED-2 — `_india_trade_loop` opens NEW entries up to 16:00 IST (30 min after close).** `_is_india_trading_window` returns True 09:15–16:00 (`india_tasks.py:29-39`) and the loop opens trades in that window. Between 15:30–16:00 the market is closed; "live" price = the close, so new entries fill at a static post-close price with no further tape.

**MED-3 — No market-open cache invalidation / context rebuild.** `grep` found no 9:15 cache-clear or forced rebuild anywhere in `tasks/`. The system relies entirely on TTLs and per-cycle rebuilds, so there is no explicit "invalidate yesterday, wait for N live candles, detect the gap" step at open — which is exactly the most vulnerable moment.

**MED-4 — Docstring/code mismatch in exit score fetch.** `_fetch_hub_scores_for_exits` docstring says "older than 2 hours are excluded" but the code uses `timedelta(hours=24)` (`execution.py:224-225` vs `234`). Not dangerous for exits (stale-but-real is acceptable to *close*), but it signals the freshness policy is not centralized or reviewed.

---

## 7. Low Issues

- `morning_regime` is cached by calendar date in-process (`morning_regime.py:80-82`); a mid-day restart recomputes — acceptable, but it is one LLM call/day computed once (potentially pre-open) and reused all day.
- `_is_trading_day()` in agent_loop uses `datetime.now().weekday()` (server-local, `agent_loop.py:142`) while `_is_market_hours()` was correctly moved to IST (B15). Mixed clock bases; low risk but inconsistent.
- Hardcoded `age_seconds` and mixed `.NS`/bare-symbol keying throughout create latent lookup-miss risk (handled defensively, but fragile).

---

## 8. Stale-Data Analysis (by source)

| Source | Refresh | Freshness enforced at decision? | Verdict |
|---|---|---|---|
| Live price (entry) | Kite tick / 15s poll / yfinance | Path B ≤90min ✅; Path A inline ❌; tick age faked (MED-1) | Partial |
| Daily candles (indicators/ATR/RSI/MACD) | EOD + backfill | Path B/C guard age; Path A only `len<20` | **Inherently yesterday's data on 1d TF** |
| Master score (signal) | 15 min + pre-open + post-close | Path C 2h ✅; Path B **none** ❌; Path A in-cycle | **Hole in Path B** |
| Shortlist universe | scanner cadence | selects *which* symbols only; each re-scored | Low risk |
| News/sentiment | 5 min cache; keyword scan on DB headlines | used, but no explicit age gate on the news subscore | Medium |
| Macro / VIX / breadth | live_snapshot per cycle | present in ctx; pre-open runs have no intraday value | Medium |
| Broker holdings | `sync_kite_holdings` every 15 min | reconciliation lag up to 15 min | Medium (live mode) |

**Indicators are computed on daily candles** (`AGENT_TIMEFRAME="1d"`, `config.py:385`). This is legitimate for swing trading, but it means live RSI/MACD/VWAP/intraday-trend/bid-ask/spread are **not** in the decision — the signal is a daily-bar signal with a live *price* overlay, nothing more.

---

## 9. Cache Analysis

- `PRICE_CACHE` (`live_prices.py:24`): 15s/60s refresh, returns cached value **regardless of age** (`:63-71`); age is reported but never enforced by callers.
- `LIVE_TICKS`: no per-tick timestamp check (`zerodha_ticker.py:44`).
- `INFO_CACHE_TTL = 86_400` (24h) for fundamentals — fine.
- Celery workers **do not** receive websocket ticks; both scheduled paths hot-patch via `fetch_live_snapshot()` (`india_tasks.py:2816`, `:542`) — good, but that snapshot's own freshness pre-open is unverified.

---

## 10. Scheduler Analysis

- Auto-expiry on all beat tasks prevents the 63k-task backlog seen before (`celery_app.py:437-447`) — good.
- Overlap guard on the hub cycle (`india_tasks.py:2805`) — good, **but** every skip silently ages the scores that Path B trades with no age gate (compounds CRIT-2).
- `hour="3-10"` UTC deliberately spans pre-open and post-close (documented intent: score outside hours). The bug is that scoring-outside-hours + Path B's missing age gate = trading on outside-hours signals.

---

## 11. AI Analysis

`build_master_context` provides macro_bias, india_vix, nse_market_mood, per-symbol news scores, earnings tones, sector moods, options subscore (confirmed via the cycle log at `india_tasks.py:2831-2834`). So the Hub *does* assemble a broad 7-factor context — it is **not** "just candles." What it does **not** demonstrably include in the traded decision (no evidence found in the path traced; `engine/agent/macro.py` was **not** read in full, so treat these as *unverified-absent*, worth confirming): Gift Nifty, Dow/Nasdaq/Europe futures, Asian markets, crude, gold, USDINR, bond yields, pre-market volume, live bid/ask/spread, VWAP, gap-at-open detection, block/bulk deals, insider/promoter activity, social/Twitter sentiment.

---

## 12. Risk Analysis

Strong: idempotency guard on entries (`execution.py:41-49`), drawdown breaker + sticky halt (`india_tasks.py:574`), shock-cooldown gate (`:584`), 5-state regime gate (`agent_loop.py:233`), sector caps, MIS square-off. Gap: risk checks operate on the candidate's levels, which in Path A inline can be built off a stale close.

---

## 13. Missing Live Context (checklist verdict)

**Present:** Live price (entry), India VIX, macro bias, market mood/breadth, news sentiment, sector rotation, options/PCR/OI/IV (F&O-gated), earnings tone, fundamentals.

**Absent/unverified in the traded decision:** live bid/ask/spread, VWAP, live intraday RSI/MACD, today's open & gap classification, Gift Nifty, US/Asia/Europe futures, crude/gold/USDINR/bond yields, pre-market volume, FII/DII intraday, max-pain, block/bulk/insider/promoter, social sentiment.

*Consequence of the important ones:* without **gap-at-open detection** and **live intraday confirmation**, a daily-bar BUY generated pre-open executes into an adverse gap so long as the gap is <5% (Path C) or unguarded entirely (Path A inline).

---

## 14. Recommended Architecture

**Collapse three paths into one gated executor.** Every entry, regardless of trigger, must pass a single `FreshnessGate` before `executor.execute`:

```python
# engine/agent/freshness.py  (new, single source of truth)
@dataclass
class FreshnessVerdict: ok: bool; reason: str

async def validate_trade_freshness(symbol, decision, session, *, now=None) -> FreshnessVerdict:
    now = now or datetime.utcnow()
    # 1. Signal age — score must be from THIS session, not pre-open/overnight
    if decision.master_score is not None:
        if scored_at is None or (now - scored_at) > timedelta(minutes=MAX_SIGNAL_AGE_MIN):  # e.g. 45
            return FreshnessVerdict(False, "signal_stale")
        if scored_at.time() < MARKET_OPEN_IST:            # reject pre-open-computed scores
            return FreshnessVerdict(False, "signal_precomputed_preopen")
    # 2. Live price — must be confirmed AND genuinely fresh (age, not faked)
    px, age = await confirmed_live_price(symbol)          # rejects age_seconds > 30
    if px is None: return FreshnessVerdict(False, "no_confirmed_live_price")
    # 3. Divergence guard (unify the 5% rule here)
    if abs(px - decision.entry)/decision.entry > MAX_DIVERGENCE: return FreshnessVerdict(False, "candle_stale_divergence")
    # 4. Enough live bars since open (skip first-N-minutes volatility)
    if bars_since_open(symbol) < MIN_LIVE_BARS: return FreshnessVerdict(False, "awaiting_live_bars")
    # 5. Gap classification — abnormal gap → require re-score, don't fire yesterday's thesis
    if abs(gap_pct(symbol)) > MAX_GAP_AUTO: return FreshnessVerdict(False, "abnormal_gap_needs_rescore")
    return FreshnessVerdict(True, "ok")
```

Then **route Paths A, B, C through it** — delete the divergent inline logic. Principles:

1. **One executor, one gate.** No trade without `validate_trade_freshness().ok`.
2. **Every dataset exposes a timestamp; the gate enforces max-age** (score, price, candle, snapshot).
3. **Scores carry a `computed_in_session` flag;** pre-open/post-close scores are usable for *display* but **never for entry** — entries require a score stamped after 09:15 IST + N bars.
4. **Fix `get_price` to return true tick age** and have the gate reject `age_seconds > 30` (kill MED-1).
5. **Market-open protocol at 9:15:** invalidate prior-day live caches, wait for ≥N live bars, compute gap vs prior close, force a fresh in-session score before any entry.
6. **No new entries after 15:20 IST** (only exits/square-off).

---

## 15. Priority Fixes

| # | Fix | File:line | Effort |
|---|---|---|---|
| P0 | Add `scored_at >= cutoff` (+ post-open requirement) to `_india_trade_loop` candidate query | `india_tasks.py:613-649` | S |
| P0 | Add live-snap + divergence guard (reuse agent_loop block) to hub inline executor, or route it through the shared gate | `india_tasks.py:2908-2990` | M |
| P0 | Decide: schedule the hardened `run_agent_cycle` and retire the other two, **or** port its gate into a shared module both call | `celery_app.py` / new `freshness.py` | M |
| P1 | Make `get_price`/`get_live_tick` return real tick age; reject stale ticks | `live_prices.py:52-71`, `zerodha_ticker.py:44` | S |
| P1 | Gap-at-open detection + "wait N live bars" gate | new | M |
| P1 | Block new entries 15:20–16:00 IST in `_india_trade_loop` | `india_tasks.py:537` | S |
| P2 | Fix exit-score docstring/code (2h vs 24h) and centralize the number | `execution.py:224-234` | S |

---

## 16–19. Code Locations / Files / Functions / Suggested Changes

- **Score generation timing:** `tasks/celery_app.py:380-383` (`crontab(hour="3-10")`) → widen-gate so scores stamped outside 09:15–15:30 IST set `computed_in_session=False`.
- **Path B signal query (no age gate):** `tasks/india_tasks.py:613-649` — add `.where(MasterIntelligenceScore.scored_at >= now - timedelta(minutes=45))`.
- **Path B entry pricing (good, keep):** `tasks/india_tasks.py:706-749`.
- **Path A inline executor (no snap):** `tasks/india_tasks.py:2908-2990` — insert the divergence/snap block from `engine/agent/agent_loop.py:527-597`.
- **Hardened but unscheduled path:** `engine/agent/agent_loop.py:396-597`; not in `celery_app.py` beat_schedule.
- **Hub candidate 2h cutoff (the correct pattern to replicate everywhere):** `engine/agent/decision_engine.py:704`.
- **Tick freshness:** `crawler/zerodha_ticker.py:38-44`, `crawler/live_prices.py:52-71`.
- **Timeframe:** `utils/config.py:385` (`AGENT_TIMEFRAME="1d"`).

---

## 20. Production Readiness Score: **48 / 100**

**Why not lower:** genuine, working defenses exist — fail-closed live snap + 5% divergence (on one path), ≤90 min entry-candle gate on the live loop, ≤4d exit-candle gate (`execution.py:285-301`), 5s fast-SL with dual price source, idempotency guard, drawdown/halt/shock breakers, IST-anchored hours, auto-expiring queue, a candle-staleness watchdog (`celery_app.py:370`).

**Why not higher:** the anti-stale logic is **not uniformly applied**, the **best-guarded path isn't scheduled**, the 60s live loop **trades signals of unbounded age**, the 15-min path **can fill at a stale daily close**, live-tick age is **faked to 0**, and there is **no explicit market-open rebuild/gap gate**. For paper mode these are acceptable-with-monitoring. **For real money they are disqualifying until the three paths are unified behind one freshness gate.** Ship P0+P1 and this moves to ~75.

---

## Audit Limits (honesty note)

- `engine/agent/macro.py`, `engine/intelligence_hub.py` internals, and the F&O paths were **not** fully read. The "missing global context" list (§13) is therefore *unverified-absent*, not confirmed-absent — confirm before quoting it.
- Everything in §4–§7 is traced to lines read directly.

## Recommended Next Step

Before changing code, add instrumentation: for every trade over one trading day, log the **score age**, **price age**, and **which path opened it**. This *measures* how often the stale window actually fires, so fixes are prioritized against real frequency rather than theory. Then ship P0 + P1.
