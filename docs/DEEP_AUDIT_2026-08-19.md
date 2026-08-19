# AutoTrade Pro — Deep Architecture & Code Audit

**Date:** 2026-08-19
**Commit at audit time:** `91457d7` (*feat: remove F&O functionality entirely — equity-only system*)
**Scope:** full depth on `autotrade-backend/`; a real but lighter pass over `autotrade-frontend/`.
**Working tree:** diverges from HEAD. `engine/decision_router.py` and `engine/hub_universe.py` carry uncommitted changes made during this audit by someone else — see D1 and D13. Findings state HEAD and working-tree values separately wherever they differ.
**Method:** read-only. No code was changed and nothing was committed. Findings were produced by three parallel research passes over the actual code, with every severe claim re-verified first-hand by the author before inclusion. Every defect carries a `file:line` citation and a confidence tag.

**Companion:** [`../CLAUDE.md`](../CLAUDE.md) is the short operational version of this document, intended to be loaded into every working session. This file is the reference you come to when you need the detail.

### Confidence tags

| Tag | Meaning |
|---|---|
| **CONFIRMED** | The code path was read end to end. The defect follows from the code as written. |
| **PLAUSIBLE** | Strong evidence from reading, but proving it requires running the system, observing production state, or data the audit could not access. |

### Supersession notice

`autotrade-pro-codebase-map.md` (repo root, 83KB, dated **2026-07-17**) is a good prior audit and much of its structural analysis still holds. It is **partially stale** and this document supersedes it where they disagree:

| Prior map says | Current truth |
|---|---|
| Documents `engine/fno/*` at length (contracts, adjustments, options pricing, vol strategies) | **Deleted** in `91457d7`. The directory holds only `__pycache__`. Three beat entries still reference the deleted tasks — see D9. |
| Capital ₹20,00,000 | **₹5,00,000** since `712106a` |
| Single-EMA / earlier regime handling | Tier-2 macro-risk overlay added in `ded31b4` |
| Two long-running processes (uvicorn + celery) | **Four** — the news engine and beat are separate units (§2.1) |
| `decision_router.route_decision()` "VERIFIED UNCALLED outside its own docstring/tests" | Now genuinely reachable in production via `news_discovery_engine.py:377` |
| `~130` root-level scratch scripts | **157**, of which 137 are git-tracked |

---

## 1. Executive summary

AutoTrade Pro is a news-driven Indian cash-equity trading system running in paper mode. An LLM agent on AWS Bedrock evaluates news-derived events through a tool-calling loop, and a central execution gate authorises or rejects the resulting trade intent against a canonical event and a 12-check risk gate.

**What is genuinely good.** The safety architecture is real, not declarative. The app refuses to boot on unsafe risk config (`main.py:36-60`). The central gate fails *closed* and re-verifies the canonical event against the database rather than trusting the caller. Paper execution models real NSE transaction costs and applies adverse-only slippage. Position closes take a row lock. Every long Celery task has a Redis overlap guard, and every beat entry gets an automatic `expires`. Every rejection is persisted as an audited `AgentDecision` with a `skip_reason`. The LLM layer has a grounding/hallucination check that structurally removes tools rather than merely discouraging their use. Much of the code carries unusually good incident-archaeology comments explaining *why* a guard exists.

**What is broken.** Four defects dominate:

1. Technical trade origination is **live in the working tree** via an uncommitted flag flip, so the repo's own binding news-only contract is not currently enforced (D1).
2. The primary live-order path **raises `TypeError` on every call** and reports it as a generic error. Latent only because paper mode is on (D2).
3. `PRICE_CACHE` staleness checks are **structurally inert** — a days-old price reports `age_seconds: 0.0` (D3).
4. API authentication covers **6 handlers**; the kill switch does not cross the process boundary (D4).

**What is fragile.** In-process caches are written from a WebSocket thread and read from the asyncio loop with no locking. Duplicate-position guards are TOCTOU across three independent origination paths. There is no Zerodha REST rate limiter and no order idempotency key. Indicators are computed on a possibly-forming bar with no caller contract.

**On the quant question specifically:** the lookahead exposure is real but narrower than it first appears. The *execution* path is lookahead-aware in several places by deliberate design. The *indicator* layer is not, and no caller truncates the forming bar.

---

## 2. Architecture

### 2.1 Processes

Four long-running services plus a timer, all under systemd **user** units (`deploy/systemd/`, canonical) — no root required.

| Unit | Entry point | Concurrency | Auto-reload | Notes |
|---|---|---|---|---|
| `autotrade-uvicorn` | `main.py` | single | **No** | `MemoryAccounting=yes`, `MemoryHigh=2G`, `MemoryMax=3G`, `TimeoutStopSec=30`, `--timeout-graceful-shutdown 12` |
| `autotrade-celery-worker` | `tasks.celery_app` | `--concurrency=2` | yes (`watchmedo`) | single default queue |
| `autotrade-celery-beat` | `tasks.celery_app` | — | yes (`watchmedo`) | ~60 beat entries |
| `autotrade-news-engine` | `news_discovery_engine.py` | single | yes (`watchmedo`) | **the live trade engine** |
| `autotrade-zerodha-refresh.timer` | `scripts/refresh_zerodha_token.py` | — | — | `OnCalendar=*-*-* 02:30:00 UTC`, `Persistent=true`, `RandomizedDelaySec=60` |

The memory caps and `TimeoutStopSec` were added after the kernel OOM-killed uvicorn twice (6.0 GB on 14 Aug, 5.6 GB on 17 Aug) and after repeated `stop-sigterm` timeouts forced SIGKILL. **The underlying memory leak is still unresolved** — see §9 U1.

`watchmedo` uses `--pattern="*.py"`. `.env` is not watched, and both `Settings` and the boto3 Bedrock client are cached per process. This is precisely why the 2026-08-08→17 Bedrock outage persisted for roughly five days *after* a valid key was already sitting in `.env`.

`autotrade-backend/deploy/systemd/` is a **stale 4-file duplicate** (last touched 19 Jun vs 17 Aug for the root copy) missing the uvicorn and news-engine units. Use the repo-root copy.

### 2.2 The uvicorn process

`main.py` (342 lines) mounts 26 routers under `/api/v1/*` plus `/ws` (`main.py:288-313`), and runs four asyncio background tasks plus a thread inside the lifespan:

| Component | Line | Cadence | Purpose |
|---|---|---|---|
| `_init_db_with_retries()` | 64-100 | once | Schema DDL **off** the startup path: 5 attempts, 20s timeout each, backoff `5*(n+1)` |
| `_live_price_loop()` | 139-178 | 15s | Hydrate `PRICE_CACHE` from Redis, fan out to WS clients |
| `_breadth_loop()` | 184-198 | 120s | NSE advances/declines |
| `_warmup_info_cache()` | 202-211 | once, +10s | PE / market cap / beta |
| KiteTicker thread | 218-235 | continuous | **explicit daemon `threading.Thread`** |

Three of these carry incident archaeology worth preserving:

- **DDL backgrounding** (`:64-100`): `init_db()` needs an `ACCESS EXCLUSIVE` lock. `crawler/news_crawler.py` holds a read transaction open across slow LLM sentiment work — three connections were observed idle-in-transaction on `news_items` for 1–4 minutes with an `ALTER TABLE` blocked behind them for 76s. Awaiting it inline made the process unkillable, because uvicorn cannot service SIGTERM during lifespan startup.
- **Daemon thread** (`:222-232`): `asyncio.to_thread` uses the default `ThreadPoolExecutor`, whose workers are non-daemon and are *joined* at interpreter exit. The ticker's WebSocket loop never returns, so that join blocked process exit indefinitely.
- **Scoped shutdown** (`:239-254`): the previous version cancelled `asyncio.all_tasks()` indiscriminately, killing uvicorn's own server tasks mid-shutdown. Every stop then hit `TimeoutStopSec` and was SIGKILLed (observed 04, 06 and 17 Aug), which meant this block never ran at all.

### 2.3 State stores

| Store | Location | Scope | Risk |
|---|---|---|---|
| `LIVE_TICKS: dict[int, dict]` | `crawler/zerodha_ticker.py:24` | in-process, per-token | unbounded, unlocked |
| `PRICE_CACHE: dict[str, dict]` | `crawler/live_prices.py:24` | in-process | **141 read sites across 29 modules**; unlocked, written from the ticker thread |
| `INFO_CACHE` | `crawler/live_prices.py:25` | in-process, 24h TTL | — |
| `SECTOR_CACHE` | `crawler/sector_data.py` | in-process | — |
| `_snapshot_cache` | `crawler/market_snapshot.py:45` | in-process, 5s TTL | unbounded, never evicted |
| Redis `live_prices:snapshot` | `crawler/live_prices.py:558`, 900s TTL | cross-process | transport only |
| Postgres `candles` | `crawler/price_feed.py:370` | durable | `ON CONFLICT DO NOTHING` on `uq_candle_bar` |

**Cross-process design.** `tasks/price_cache.py` (Celery, 30s) fetches and calls `publish_prices_to_redis()`. Every other process calls `hydrate_prices_from_redis()` (`live_prices.py:577`; uvicorn every 15s at `main.py:167`). Hydration deliberately will not overwrite entries whose `data_source == "kite_ws"` (`live_prices.py:608`), so sub-second ticker data survives.

This design is correct in principle. Its defect is that the freshness metadata it depends on is not written — see D3.

### 2.4 External dependencies

Market data: Zerodha Kite (WebSocket + REST), Upstox (WS + REST + fundamentals), yfinance, Alpha Vantage, `nselib`, `bsedata`, `mftool`.
News: NewsAPI, Finnhub, NewsData, RSS/feedparser, RBI, PIB, SEBI, bulk/block deals, media crawler, Tavily.
LLM: **AWS Bedrock Converse API via boto3**, single provider, no fallback chain.
Out: Telegram (Bot API), Google Sheets (gspread).

---

## 3. End-to-end trade lifecycle

### 3.1 Market data ingestion

Two tick implementations exist; **only one is wired up**.

**Active** — the official `KiteTicker`:
```
crawler/zerodha_kite_lib.py:641  start_ticker()  → KiteTicker(...).connect(threaded=True)
crawler/zerodha_ticker.py:163    on_connect()    → two-tier subscribe
crawler/zerodha_ticker.py:118    on_ticks()      → LIVE_TICKS[token]; PRICE_CACHE[sym]
```
Subscription is tiered (`zerodha_ticker.py:184-254`): open positions + watchlist + indices in `MODE_QUOTE` (cap 3000), the remainder in `MODE_LTP` (cap 3000). New positions hot-subscribe via `subscribe_open_position()` (`:75`), called from `engine/agent/execution.py:167`.

**Dead** — `crawler/zerodha_websocket.py`, a hand-rolled binary packet decoder writing to its own `LIVE_PRICES` dict. `start_kite_websocket()` has zero callers and nothing in the trade path reads its output. It also gives up permanently after 5 retries (`:140`, `:189`) with no self-heal. *(CONFIRMED, dead code)*

**Fallback chains.** There are five, and they disagree with each other:

| # | Purpose | Chain | Location |
|---|---|---|---|
| A | Spot, in-process | Zerodha `LIVE_TICKS` (≤30s) → Upstox WS (≤30s) → `PRICE_CACHE` (≤30s) → None | `live_prices.py:31` |
| B | Spot, batch refresh | Kite quote (chunks of 500) → Upstox batch → yfinance `fast_info` | `live_prices.py:254` |
| C | Spot, authoritative | Kite WS tick → Kite REST full quote → yfinance — **no age check on any tier** | `market_snapshot.py:87` |
| D | Candles | Kite historical → yfinance → Alpha Vantage *(`price_feed.py:306`)* vs Kite → Upstox → yfinance *(`india_price_feed.py:159`)* | two chains, same job |
| E | Exit-loop price | Kite LTP batch → yfinance backstop *(`india_tasks.py:1362`)* / Kite LTP → Kite 1-min ≤120s → DB *(`trade_simulator.py:838`)* | — |

Chain C is the one that prices every live news trade, and it is the weakest of the three spot chains. See D3.

**News.** `crawler/news_crawler.py:1204 run_news_crawl()` fans 11 sources through one `asyncio.gather` (`:1245`). `crawler/news_router.py:103 route_headline()` is a deliberately non-LLM regex Tier-0 classifier (`COMPANY|MACRO|FILING|NOISE`) — chosen because ~823 headlines/day against a shared RPM ceiling makes per-headline LLM routing infeasible. `crawler/event_pipeline.py:11 process_latest_events()` clusters unmapped `NewsItem`s into `CausalEvent` rows via `engine.event_classifier.classify_event`.

### 3.2 The decision path

`news_discovery_engine.py` (1499 lines, repo root) is the production trade engine, running as its own systemd service.

```
run_news_discovery_loop()                                       :1284
  └─ process_ticker(ticker, side, headline, summary)            :1118
       ├─ _build_evidence()                                     :678
       │     └─ event_id is None → return False, NO LLM call    :1125
       ├─ maybe_direct_trade()   engine/direct_news_strategy.py  → DIRECT_NEWS
       ├─ llm_tooluse_candidate()                                :1143
       ├─ validate_evidence_consistency()                        :1158
       ├─ _execute_news_trade()                                  :1173 → :349
       └─ get_second_order_trades()                              :1184
```

The `event_id is None` early return at `:1125` is a good piece of design: it enforces `NO EVENT → NO TRADE` *before* spending an LLM call, rather than deliberating and then rejecting.

**`_execute_news_trade()` (`:349`)** builds the `TradeIntent`:

1. **`get_market_snapshot(ticker)` (`:392`)** — the same `MarketSnapshot` service the LLM's `price_action`/`market_depth` tools read from, so decision and execution observe one tick instead of racing two price paths. Good design, undermined by C's missing age check.
2. **30-minute late-entry gate (`:409-428`)** — rejects entry after a >2% 30-minute move in the trade's own direction. Uses `_candles[-3]` explicitly because "the last bar is the one currently forming" (`:417`). Added after NESTLEIND was bought at the top of a spike that ran 10:45–11:15 while the news arrived at 11:19. **Fail-open.**
3. **Multi-session late-entry gate (`:448-468`)** — added 2026-08-18 on `1d` bars, deliberately separate because (a) the intraday check silently skips when no 15m bars exist for the day, and (b) 30 minutes cannot see a multi-session move. BSE had already fallen 7.1% over four sessions before being shorted. **Fail-open.**
4. `_compute_news_trade_levels()` → SL / T1 / T2 / ATR.
5. `execute_trade_intent()` — **fail-closed**.

Both late-entry gates being fail-open is deliberate and documented: a data outage must not silently halt all news trading, because `authorize_trade_intent` is the gate that fails closed. That is a defensible split of responsibilities.

### 3.3 The LLM tool-use loop

`engine/agent/decision_engine.py:1268 llm_tooluse_candidate()` — a ReAct loop using **prompted JSON, not native function-calling**.

- 10 tools registered at `_LLM_TOOLS` (`:743`): `fundamentals`, `company_intelligence`, `news`, `options`, `price_action`, `market_depth`, `intraday_candles`, `sector`, `macro`, `predict_candle`, `expert_research`.
- `_max_rounds = 20` (`:1425`); `_force_decide` at round ≥12 (`:1436`).
- Up to 3 retries per round on empty/unparseable response, `sleep(2*(attempt+1))` (`:1449-1467`).
- Malformed-action normalisation (`:1489-1498`) repairs the model putting a tool name in the `"action"` field.
- Tool-result memoisation (`:1513`) — repeat calls replay cached output.
- Core-tool enforcement by **identity, not count** (`:1573`).
- **Grounding check `_check_grounding()` (`:1142`)** — deterministic provenance (`:846`), entity overlap (`:886`), numeric consistency (`:996`). One self-correction retry; a second failure hard-rejects — *unless* a canonical event exists, in which case it soft-fails by stripping claims and applying a confidence haircut (`:1606-1628`).
- **Canonical-event binding**: when `candidate.evidence` is set, `news` and `expert_research` are **structurally removed** from the tool menu (`:1292`), not merely discouraged. This is the right way to prevent the model re-deriving its own evidence.

### 3.4 Risk gate

`engine/risk_manager.py:158 validate_signal(signal, wallet_balance, open_positions, session) -> (bool, str)`:

| # | Line | Check |
|---|---|---|
| 0 | 185 | Derivative symbol (FUT/CE/PE) blocked from the equity pipeline |
| 1a | 214 | `max_open_positions` absolute ceiling (runaway-loop guard) |
| 1a-ii | 226 | `max_concurrent_positions` diversification cap |
| 1b | 248 | Portfolio risk budget: `open_risk + this_risk > max_portfolio_risk × equity` |
| 1c | 249 | Cash buffer: `deployed + notional > (1 − min_cash_buffer) × equity` |
| — | 247-281 | On 1b/1c failure: **one** thesis-based reallocation attempt, then recompute and re-check |
| 1d | 314/322 | Per-sector name count, then per-sector capital. **Fails open** on unresolved sector |
| 1e | 349 | Per-strategy capital cap — added after `PRE_EVENT_EXPECTATION_GAP` reached 91% of trades at PF 1.069 |
| 2 | 373 | Daily-loss breaker, **mark-to-market** (`closed_today + unrealised`) |
| 3 | 392 | `PAPER_CONFIDENCE_THRESHOLD` floor |
| 3b | 411 | S&R proximity — SL within `SR_MAX_DIST_PCT` of nearest level; bypassed when `sr_support == 0` |
| 4 | 438 | R:R vs `min_risk_reward`, measured to **`target_2`**, not T1 |
| 5 | 454 | Hard notional cap `AGENT_MAX_POSITION_WEIGHT` (5%) + 1% tolerance |
| 6 | 467 | Duplicate open position, `.NS`/`.BO`-normalised |

**Sizing** — `calculate_position_size()` (`:503`): conviction-scaled risk interpolating `RISK_PER_TRADE_MIN (1.5%) → MAX (3.0%)` across `[PAPER_CONFIDENCE_THRESHOLD, CONVICTION_HIGH=70]`; shorts halved and their weight cap halved to 2.5%; whole shares only.

**Stops** — `compute_trade_levels()` (`:48`), three tiers (dynamic → ATR 2×/4× → static 5/10/15%). Every tier must clear `MIN_STOP_DISTANCE_PCT = 0.015` (`:45`) and T1 must be ≥ the SL distance (`:101`).

**Kelly** — `_kelly_percent()` (`:575`) is computed and **reported only** (`get_daily_stats:651`). It is never fed back into sizing. *(CONFIRMED — a known gap, also noted in the Varsity implementation log.)*

**Gaps:** no correlation/beta check, no gross-vs-net exposure check, no per-symbol notional check independent of the 5% weight cap.

### 3.5 Execution

**Paper** (the only path that fires today):
```
news_discovery_engine.py:535  execute_trade_intent()
engine/decision_router.py:744 authorize_trade_intent()
engine/decision_router.py:334 route_decision()
engine/decision_router.py:337 open_paper_trade()
paper_trading/trade_simulator.py:229
```

The agent path uses its own executor — `engine/agent/execution.py:28 _paper_execute()` — with an idempotency guard (`:41-49`), `int(qty)` with a `qty < 1` reject (`:52-55`), `VirtualWallet.deduct_margin` (`:75`), then `PaperTrade` + `OpenPosition` + `AgentDecision` + `AgentTrade` in **one commit** (`:155`).

**Live** — `engine/zerodha_executor.py:300 place_real_order()` enforces 10 rules (`:337-433`): paper-mode off · valid token + `ZERODHA_ENABLED` · confidence ≥ `max(60, LIVE_CONFIDENCE_THRESHOLD)` · ≤5% of live Kite balance · `is_nse_market_open()` · daily-loss check · a 3s `asyncio.sleep` abort window (`:78`) · MARKET forced to LIMIT with a ±0.5% buffer (`:87`) · `OpenPosition + ZerodhaPosition < 5` · tag `ATP_{signal_id}`.

Orders are always `LIMIT`, `variety="regular"`, `validity="DAY"`, `product` = CNC for BUY / MIS for SELL.

`engine/agent/execution.py:183-200` blocks a CNC SELL unless the stock is present in Kite CNC holdings in the required quantity, citing the SEBI/NSE prohibition on delivery short selling. **This is the one genuine SEBI-compliance control in the codebase and it is correct.**

---

## 4. Defect register

Ordered by severity. Every entry is reproducible from the cited lines.

### D1 — Technical trade origination is LIVE in the working tree, contradicting the news-only contract — CONFIRMED

**The working tree diverges from the committed state on this flag.** The change is uncommitted and was made on 2026-08-19 at 16:44 IST, during this audit, by someone other than the audit.

| Flag | HEAD (`91457d7`) | Working tree (what is running) |
|---|---|---|
| `_TECHNICAL_TRADE_ORIGINATION_BLOCKED` | `True` — `decision_router.py:564` | **`False`** — `decision_router.py:566` |
| `_NEWS_ONLY_BLOCKS_HUB_ENTRIES` (`india_trade_loop`) | `False` — `india_tasks.py:572` | `False` (unchanged) |
| `_NEWS_ONLY_BLOCKS_HUB_ENTRIES` (`_intraday_entry_task`) | `True` — `india_tasks.py:1737` | `True` (unchanged) |
| `_NEWS_ONLY_BLOCKS_HUB_ENTRIES` (`agent_loop`) | `True` — `agent_loop.py:312` | `True` (unchanged) |

**At HEAD**, the two flags cancel each other out. `india_tasks.py:572` sets the local block to `False` — re-enabled by explicit user instruction on 2026-07-24, per the comment at `:559-571`, because the news path had gone roughly two days without a trade during the Bedrock reliability failure. But the intent it builds at `:1262` carries `strategy_family=StrategyFamily.TECHNICAL`, and the central gate rejects every TECHNICAL intent with `BLOCKED_TECHNICAL_ORIGIN`. Net effect: every 60 seconds `india_trade_loop` reads the shortlist, computes indicators, runs the LLM reasoning gate, runs pre-trade research (12s timeout) and runs the full 12-check risk validation — then discards every candidate. Pure waste, no trades.

**In the working tree**, the central block is off. Both gates on `india_trade_loop` are now open, so it can originate TECHNICAL trades. Because `watchmedo` hot-reloads `*.py` (§2.1), the running Celery worker is executing the working-tree version, not HEAD.

Two consequences follow:

1. **The binding architecture contract is not in force.** `docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md` §5-6 states that only news/event-derived intents may originate a trade. With this flag off, that invariant is unenforced on the main equity loop. The contract document has not been updated to match.
2. **Performance attribution is compromised.** `StrategyFamily` exists precisely so that "a news-driven trade and a technical-scan trade should never be silently pooled together when measuring whether news actually has edge" (`decision_router.py:88-97`). With both families originating, any P&L analysis over this period pools them.

It also **widens D2's blast radius**: TECHNICAL intents now reach `route_decision`, whose LIVE branch is broken.

**This audit does not flip the flag.** The change may well be intentional and mid-flight. It needs an owner decision: either commit it deliberately, with the contract doc updated to match and the two remaining `True` blocks reconciled, or revert it. What it should not do is sit uncommitted while the running system quietly trades under rules the documentation says are in force.

*(The `product`-threading change in the same uncommitted diff — adding `product` to `route_decision` and passing it through to `place_real_order` and `open_paper_trade` — is unrelated and looks correct; `place_real_order` does accept `product`.)*

### D2 — Every live order routed through `decision_router` raises `TypeError` — CONFIRMED

`engine/decision_router.py:308-315`:
```python
result = await place_real_order(
    symbol=signal.symbol,
    transaction_type=signal.action,
    quantity=qty,
    session=session,
    signal_id=str(getattr(signal, "id", "")),   # not a parameter
    confidence=conf,                            # not a parameter
)
```
`engine/zerodha_executor.py:300-314` declares keyword-only `signal`, `order_type`, `product`, `exchange`, `variety`, `price`, `trigger_price`, `tag` — neither `signal_id` nor `confidence`.

The `TypeError` is caught by the broad `except Exception` at `:330` and returned as `RoutingOutcome.ERROR` with a generic message. There is no crash and no distinctive log line, so the failure is indistinguishable from a broker outage.

Only `engine/agent/execution.py:204` calls `place_real_order` correctly — and that path is gated off by D1.

**Latent only because `PAPER_MODE=true`.** Note that the uncommitted change described in D1 widens this: TECHNICAL intents now reach `route_decision` too. Re-verified against the current working tree — the uncommitted diff adds a valid `product=` argument but leaves `signal_id=` and `confidence=` in place, so the `TypeError` stands. This must be fixed before live trading is considered, and it is a strong argument for a smoke test that exercises the live branch against a mock.

### D3 — `PRICE_CACHE` staleness checks are structurally inert — CONFIRMED

`crawler/live_prices.py:84-90`:
```python
cached = PRICE_CACHE.get(symbol)
if cached and cached.get("price"):
    age = _time.time() - cached.get("_ts", _time.time())
    if age <= 30.0:
        return {..., "source": "yfinance_cache", "age_seconds": round(age, 2)}
```
`_ts` is written into `PRICE_CACHE` in exactly **one** place — `crawler/live_snapshot.py:162`. It is not written by `zerodha_ticker.on_ticks` (`:148-160`), by `fetch_prices_batch` (`:311`, `:387`, `:469`), or by `hydrate_prices_from_redis` (`:621`).

For every entry originating from those three paths, `cached.get("_ts", now)` returns `now`, `age` evaluates to exactly `0.0`, and the guard **always passes**. `get_price()` will return a price from days ago while reporting `age_seconds: 0.0`.

Three compounding sites:

- **`crawler/market_snapshot.py:104-124`** — `_from_websocket_tick()` returns any `LIVE_TICKS` entry with a non-zero `last_price`, **discarding** the `_age_seconds` that `zerodha_ticker.get_live_tick` (`:47`) already computed. If the ticker dropped, or an illiquid mid-cap simply stopped printing, `_fetch_fresh` never falls through to REST. This is the entry-price source for every live news trade (`news_discovery_engine.py:392`), and it is strictly weaker than the `live_prices.get_price()` check it was introduced to replace.
- **`engine/zerodha_executor.py:357-360`** — reads a raw `PRICE_CACHE` entry and only falls through to `kite.get_ltp` when the value is `<= 0`, never when it is merely old. The ±0.5% LIMIT buffer is computed from that price.
- **`engine/agent/agent_loop.py:553-570`** — the 5% divergence guard compares `live_px` against `candidate.entry`; if `live_px` itself came from the inert check, it compares stale to stale.

This is the same failure class as the 2026-07-08 stale-fill incident (TBZ filled at ₹198.71 against a live ₹208.60), relocated.

**Fix shape:** write `_ts` at every `PRICE_CACHE` write site, and change the default to `0` so a missing timestamp reads as infinitely stale (fail-closed) rather than perfectly fresh.

### D4 — Authentication covers 6 handlers; the kill switch does not cross processes — CONFIRMED

`require_auth` (`api/auth.py:63-72`) is applied to exactly five handlers: `agent.trigger_cycle` (`api/agent.py:121`), `agent.halt` (`:677`), `agent.resume` (`:687`), `settings.set_trade_mode` (`api/settings.py:164`), `zerodha.place_order` (`api/zerodha.py:415`). No router declares `dependencies=[...]`, so there is no blanket protection.

Unauthenticated **and** state-mutating, ranked by blast radius:

| Route | Effect | Guard |
|---|---|---|
| `POST /api/v1/agent/kill-switch` | flattens the entire book | `X-Kill-Confirm: FLATTEN` header only |
| `PATCH /api/v1/settings/` | changes `paper_mode`, `max_risk_per_trade`, `max_open_positions`, `trading_halted` | **none** |
| `DELETE /api/v1/settings/{key}` | resets any runtime setting | **none** |
| `PUT /api/v1/agent/config` | mutates agent config | `X-Agent-Config-Update: yes` header only |
| `DELETE|PUT /api/v1/zerodha/orders/{id}` | cancels/modifies real broker orders | **none** |
| 7 × `/api/v1/zerodha/gtt*` | creates/deletes real standing orders | **none** |
| `POST /api/v1/zerodha/ticker/start\|stop` | starts/stops the live feed | **none** |
| `POST /api/v1/zerodha/positions/convert` | converts product type | **none** |
| MF orders + SIP create/update/delete | real MF transactions | **none** |
| `POST /api/v1/portfolio/reset` | wipes the wallet | `?confirm=true` only |
| all 7 WebSocket endpoints | streams portfolio/trade data | **none** |

`PATCH /api/v1/settings/` is the sharpest edge: it can silently flip paper mode to live with **none** of the safeguards its sibling `POST /settings/mode` enforces (JWT + `confirm == "I_UNDERSTAND_REAL_MONEY"` + token presence check).

**Separately**, `api/agent.py:424` implements the kill switch as:
```python
settings.AGENT_ENABLED = False
```
uvicorn and Celery are separate OS processes. This mutation is invisible to the worker, so **the kill switch stops new entries in the API process only** while every Celery-scheduled path continues. `halt`/`resume` do this correctly through `RuntimeConfig` (DB-backed). The kill switch should too.

### D5 — Indicator lookahead and bar-misalignment — CONFIRMED (LB-1..LB-3), PLAUSIBLE (LB-4)

TA-Lib is genuinely in use (`TA-Lib==0.6.8`, `requirements.txt:174`; `engine/indicators.py:15`, `engine/candlestick.py:31`, `engine/candlestick_patterns.py:5`), with hand-rolled pandas/numpy fallbacks for every indicator.

**LB-1 — indicators are computed on a possibly-forming bar. CONFIRMED.**
`compute_indicators()` reads `close[-1]` at `:942` (BB position), `:955` (EMA trend), and via `calculate_supertrend`'s `close[-1] > value` at `:320`. Whether that is the in-progress bar depends entirely on the caller — and **no caller anywhere truncates it**. `engine/agent/agent_loop.py:411` fetches `get_latest_candles(symbol, AGENT_TIMEFRAME='1d', 300)`; `AGENT_TIMEFRAME` is `"1d"` (`utils/config.py:512`). During the session, today's `1d` row exists in `candles` from the crawl and is incomplete. The frame is passed whole (`:433-442`) with no `.iloc[:-1]`. Same pattern at `tasks/market_scanner.py:139`, `api/zerodha.py:694`/`:843`, `engine/intelligence_hub.py:840`, `tasks/india_tasks.py:877`.

The indicator value traded on is therefore not the value that bar will finally print. This is lookahead-adjacent rather than strict lookahead — no future data is read — but it produces exactly the same backtest-vs-live divergence, because a backtest over closed bars sees a different number than production does.

**LB-2 — `_safe_last()` silently mixes bars. CONFIRMED.**
`engine/indicators.py:26-33` scans backwards for the first non-NaN value. So `bb_pos = _bb_position(close[-1], bbu, bbm, bbl)` (`:942`) can compare **today's close against a Bollinger band computed several bars earlier**. The same applies to `calculate_supertrend:316` and `calculate_adx:565-567`, where ADX and ±DI can come from *different* bars. This is not lookahead; it silently fabricates crossovers.

**LB-3 — volume surge uses the forming bar and a self-inclusive denominator. CONFIRMED.**
`engine/indicators.py:837-838`:
```python
avg_vol   = float(np.mean(volume[-20:])) if len(volume) >= 20 else float(np.mean(volume))
vol_surge = float(volume[-1]) / avg_vol if avg_vol > 0 else 1.0
```
`volume[-1]` on an incomplete bar is under-counted, so `vol_surge` is systematically depressed intraday — and it drives a ±10 point `vol_confirm` (`:1050-1055`) plus up to +15 bonus (`:846`). `avg_vol` also includes `volume[-1]` in its own window.

**LB-4 — Fibonacci bonus compares index labels, not positions. PLAUSIBLE.**
`engine/indicators.py:770`: `if trough_idx >= peak_idx: return 0.0`, where both come from `idxmax()`/`idxmin()` and are therefore *index labels*. In `agent_loop.py` the index is a `DatetimeIndex` (`:442`) so the comparison works; `tasks/india_tasks.py` and `market_scanner.py` build frames with a default `RangeIndex`, which also works. It breaks silently — awarding +10 on a downtrend — only if a caller ever passes a descending-sorted or reset index. No such caller exists today.

**What is correct, and deserves saying:**
- Ichimoku shifts properly (`:460-461`), and `chikou_close = close_s.iloc[-27]` (`:476`) is the standard backward comparison. **No forward leak.**
- Pivot points use `iloc[-2]` (`:1079-1087`) — correctly the previous bar. (They are the previous bar *of whatever timeframe was passed*, though, and are consumed downstream as if daily.)
- VWAP bails out above a 30-minute median bar spacing (`:359-367`) so it cannot pollute daily scoring.
- `news_discovery_engine.py:417` deliberately uses the third-from-last 15m bar because "the last bar is the one currently forming".
- `tasks/replay_audit.py` exists **specifically** to detect look-ahead: it freezes `datetime` via a `MockDatetime` patch, re-scores with only candles ≤ that timestamp, and diffs against the stored score. The tooling to catch LB-1 already exists; it is a standalone script, not a scheduled check.

**Root cause behind all of it:** `compute_indicators()` accepts any DataFrame and asserts nothing about timeframe or bar completeness. `engine/risk_manager.py:45` documents `MIN_STOP_DISTANCE_PCT` existing because ATR computed off 1-minute candles produced a 0.21% stop — a band-aid over the same missing caller contract.

### D6 — No Zerodha rate limiting and no order idempotency — CONFIRMED

| Surface | Protection |
|---|---|
| Kite LTP | chunked at 200/call (`zerodha_market.py:551`); **no rate limiter, no retry**; 403 → 60s cooldown (`:40`) |
| Kite quote (batch) | chunked at 500 (`live_prices.py:277`); no throttle, no retry |
| Kite historical | 4 attempts, `sleep(1.0*(n+1))` on 429 (`india_price_feed.py:213-228`); separate token-error retry (`zerodha_kite_lib.py:469-478`) |
| **Kite `place_order`** | **no retry at all** — one shot, exception propagates (`crawler/zerodha_client.py:244`) |
| `KiteClient._get/_post` | `timeout=15.0`, **no retry** on 5xx/timeout (`:51-85`) |
| Alpha Vantage | proper: `asyncio.Lock` + 15s min gap + 3 exponential attempts (`price_feed.py:231-301`) |
| Bedrock | Redis cross-process RPM limiter + circuit breaker (`utils/llm.py`) |

Kite Connect permits roughly 1 req/s for quotes and 10 req/s for orders. Against that, `fast_sl_check` runs every **5s**, `price_cache` every **30s**, `live_snapshot` every **60s**, `refresh_live_prices` every **15s**, and `compute_live_pnl` fires on every portfolio read endpoint — all hitting `/quote` or `/quote/ltp`, unthrottled, **from multiple processes simultaneously**. Nothing coordinates them.

The inconsistency is instructive: Alpha Vantage, a free data source, has a correct limiter. The broker API that places real money orders has none.

**Idempotency** exists in paper (`execution.py:41`, `risk_manager.py:467`, `trade_simulator` `with_for_update`) and is **absent in live**: `place_real_order` has no client order ID, no pre-check against `kite.get_orders()`, and no dedupe on its own `ATP_{signal_id}` tag. A Celery retry or a duplicate beat tick would double-place. With `task_acks_late=True` (`celery_app.py:56`), a worker killed mid-task **will** redeliver.

### D7 — Cross-thread and cross-process races — CONFIRMED

**R1 — `PRICE_CACHE` / `LIVE_TICKS` are unlocked shared dicts across a thread boundary.** `on_ticks` runs on the KiteTicker WebSocket thread (`main.py:229-232`, `zerodha_kite_lib.py:659 connect(threaded=True)`) and writes `PRICE_CACHE` at `zerodha_ticker.py:160`. The asyncio loop reads and *iterates* the same dicts. `live_prices.py:536` defensively copies (`for s in list(PRICE_CACHE)`), but `:645` and `:751` iterate live — `RuntimeError: dictionary changed size during iteration` is reachable. The sharper issue is the read-modify-write at `:135-160` (`existing = PRICE_CACHE.get(sym, {})` … `existing.update(...)`) racing `hydrate_prices_from_redis`'s `PRICE_CACHE[sym] = entry` (`:621`) — torn or lost updates.

**R2 — `_active_ws` / `CONNECTED` globals race** (`zerodha_ticker.py:30,85`): `_resubscribe_open_positions` reads `_active_ws` from the asyncio thread while `on_close` (`:265`) sets it to `None` from the WS thread. `_OPEN_POSITION_SYMBOLS` is mutated from both (`.add()` at `:78` vs rebind at `:70`).

**R3 — duplicate-position guards are TOCTOU.** `engine/agent/execution.py:41-49` selects then later adds, with no lock and no DB unique constraint at that layer. Three independent origination paths run concurrently (`india_trade_loop`, the news engine, the pre-event scan), so two can both pass. Same at `risk_manager.py:467` and `position_tracker.py:290`.

**R4 — `_hydrate_portfolio_from_db` is a process-global one-shot** (`agent_loop.py:44`) over a module-global `_portfolio` (`:42`). Under Celery prefork each worker holds its own copy, so positions opened by worker A stay invisible to worker B's `ALREADY_IN_POSITION` check (`:389`) for the rest of that process's life.

**R5 — wallet balance drifts from the DB inside the entry loop.** `virtual_wallet._fetch` uses `SELECT … FOR UPDATE` (`:44`), which covers the read-modify-write *within* a transaction. But `tasks/india_tasks.py:1276` commits after each trade, releasing the lock between candidates, while `balance` is separately tracked in a Python local at `:1275`.

**R6 — LLM circuit-breaker globals are unsynchronised.** `_mantle_blocked_until`, `_mantle_consecutive_failures`, `_mantle_block_logged` (`utils/llm.py:90-98`) are mutated from concurrent coroutines with no lock; the read-modify-write at `:423` races, so backoff can be shorter than intended under concurrency.

**R7 — `_get_av_lock()` lazy init is itself racy** (`price_feed.py:84`): check-then-set with no guard, so two coroutines can create two different `asyncio.Lock`s and defeat the Alpha Vantage limiter they exist to enforce.

**R8 — fire-and-forget tasks are never awaited or tracked.** `agent_loop.py:899` and `execution.py:565` use `asyncio.ensure_future(...)` and discard the handle — exceptions vanish and the task can outlive the session it closes over. `zerodha_ticker.py:313` does this **from the WS thread**, which is not thread-safe; it needs `run_coroutine_threadsafe`.

### D8 — LLM client issues — CONFIRMED

`utils/llm.py` uses the AWS Bedrock **Converse** API via boto3, single provider, no fallback chain.

- **Model-ID disagreement, three ways.** The file header (`:1-16`) documents `amazon.nova-pro-v1:0`; `utils/config.py:181` defaults to `nvidia.nemotron-super-3-120b`; `.env` sets `nvidia.nemotron-super-3-120b`. The `.env` value wins, which means every Nova-specific piece of reasoning in the file (no reasoning channel, no shared token budget, 10,000-token ceiling) documents a model that is not in use.
- **The `timeout=` kwarg is accepted and ignored.** Timeouts are fixed at client construction (`read_timeout=90, connect_timeout=10, retries={"max_attempts": 0}`, `:139`) and the client is `lru_cache`d. `agent_loop.py:1022` passing `timeout=50.0` silently gets 90s. This is documented at `:126-128`, but it is a trap.
- **The rate limiter is set 3.6× above the quota it protects.** `MANTLE_MAX_RPM` defaults to **90** (`utils/config.py:209`), while the module comment at `:209-221` states the real Bedrock account quota is **25 RPM**. The limiter also **fails open** after a 90s wait (`:260`) — it stops throttling exactly when throttling matters most.
- **No schema validation on LLM output.** `extract_json_from_response` (`:73`) uses `JSONDecoder().raw_decode` from the first `{`/`[` and tolerates fences and prose; the ReAct loop then does ad-hoc key checks (`decision_engine.py:1579`). A typed model would catch malformed verdicts earlier.
- **Not deterministic.** `temperature=0.3` default, `0.2` in the ReAct loop (`decision_engine.py:1456`), no `top_p`, no seed. For a system whose replay tooling assumes reproducibility, this is worth stating explicitly: **decision replay cannot be exact.**
- **Auth mutates process env inside a cached factory** — `os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.MANTLE_API_KEY` (`:135`) inside the `lru_cache`d `_nova_client()`.
- **Streaming thread leak.** `call_llm_chat_stream` (`:433`) spawns a daemon thread per call (`:494`) and breaks out of the queue loop on error/done. A consumer that abandons the generator mid-stream leaves the thread draining `converse_stream` into a queue nobody reads.
- **`AGENT_LLM_TOOLUSE_ENABLED` is a no-op.** `apply_reasoning_gate` (`decision_engine.py:1794`) dispatches `if AGENT_LLM_DEVILS_ADVOCATE_ENABLED: … elif AGENT_LLM_TOOLUSE_ENABLED: …` (`:1808-1810`). The former is absent from `.env` and defaults `True` (`config.py:403`), so the `elif` never fires despite `.env` setting it true. The tool-use loop runs only where it is called directly, from `news_discovery_engine.py:860` and `:1143`. *(PLAUSIBLE — depends on live `.env`, which was read but is not under version control.)*

### D9 — Three beat entries enqueue deleted tasks — CONFIRMED

`tasks/celery_app.py:127`, `:136`, `:149` schedule `tasks.india_options_analysis`, `tasks.india_equity_options_enrich` and `tasks.fno_expiry_sweep`. A repo-wide grep for those task names returns **zero definitions** — they were removed with F&O in `91457d7`, but the schedule was not updated. Beat keeps enqueueing them (every 900s, twice daily, and daily respectively) and the worker raises `NotRegistered` each time.

### D10 — Schema is managed two ways — CONFIRMED

Alembic (`db/migrations/versions/0001-0005`) **and** a ~60-statement inline DDL block in `db/database.py::init_db()` (`:93-156`) that partially duplicates it — the 0003 attribution columns and indexes and the `candles` bigint change appear in both. Additionally `db/init/01_supabase_dump.sql` declares **79 tables** against **52 ORM models** in `db/models.py`; e.g. `option_contract_snapshots` (the target of migration 0005) has no ORM class.

Anyone adding a column must check both paths, and it is not obvious which is authoritative.

### D11 — Smaller confirmed defects

| # | Finding | Location |
|---|---|---|
| a | `_is_mis_squareoff_window()` and `_is_trading_day()` use server-local `datetime.now()`, not IST — while the sibling `_is_market_hours()` was explicitly fixed to use `ZoneInfo` | `agent_loop.py:144`, `:135` vs `:129` |
| b | `_MAX_SHORTLIST_ALERTS_PER_CYCLE` / `_shortlist_alerts_this_cycle` are declared and reset but **never incremented or compared** — the per-cycle alert cap does not exist | `agent_loop.py:38-39`, `:179` |
| c | `_send_shortlist_alert()` has no callers | `agent_loop.py:949` |
| d | `pnl_calculator.realised_for_close()` returns **gross** P&L, never subtracting `estimate_trade_cost` — so `portfolio_summary` overstates performance relative to `close_paper_trade`'s cost-aware path | `paper_trading/pnl_calculator.py:49` |
| e | `position_tracker.check_sl_tp()` is dead code, and lacks the `with_for_update` lock its live equivalent has | `paper_trading/position_tracker.py:176` |
| f | `DuplicateEventEngine` sets `cluster_size: 1` and never increments it | `engine/news_discovery_engine.py` |
| g | `execute_iceberg_order()` has zero callers, hardcodes `EXCHANGE_NFO`/`PRODUCT_NRML` (orphaned by the F&O removal), has no paper-mode guard, leaves a partial fill unwound on leg failure, and calls a sync `place_order` inside `async def` | `integrations/zerodha_iceberg.py:25,34-36` |
| h | Duplicate non-atomic `.env` read-modify-write implementations; two concurrent token refreshes can truncate the file, and `current_access_token()` reads it on an mtime check | `crawler/zerodha_client.py:368`, `zerodha_kite_lib.py:73`, `:43` |
| i | The Upstox candle fallback is silently skipped whenever a loop is running (`try: get_running_loop() except RuntimeError:`), leaving `raw = []` with no log | `crawler/india_price_feed.py:293-303` |
| j | `store_order_postback` calls `asyncio.get_event_loop()` from the WS thread, so order postbacks are likely never persisted (swallowed at `:318`) | `crawler/zerodha_ticker.py:296` |
| k | `_ikey_to_symbol()` is an O(n) linear scan per tick — O(n²) per batch on the WS callback thread | `crawler/upstox_websocket.py:19` |
| l | `temporal_audit.py` builds SQL by f-string interpolation instead of bound parameters | `tasks/temporal_audit.py:42,46,61,79` |
| m | `ml_optimizer.py` optimises against a **mock target return** despite its "Self-Learning Strategy Weight Optimizer" banner — scaffolding, not a live optimizer | `tasks/ml_optimizer.py:41` |
| n | Three different `PAPER_CONFIDENCE_THRESHOLD` fallbacks for one gate (60.0 / 40.0 / 30.0) | `decision_router.py:241`, `risk_manager.py:391`, `:516` |
| o | `boto3>=1.43` and `openai>=1.40` are **unpinned** in an otherwise fully `==`-pinned file — and boto3 is the primary LLM path | `requirements.txt:208,212` |
| p | Service logs (`/tmp/{uvicorn,celery_worker,celery_beat,news-engine}.log`) do not rotate; `celery_worker.log` has reached 2.6 GB | `deploy/systemd/*` |
| q | `autotrade-frontend/.env` holds a plaintext email and password for the puppeteer capture script | `autotrade-frontend/.env` |
| r | `main.py:25` still prints `Virtual Balance: $1000` at startup; actual capital is ₹5,00,000 | `main.py:25` |

### D12 — No pytest configuration — CONFIRMED

There is no `pytest.ini`, `pyproject.toml`, `setup.cfg`, or `tox.ini` anywhere in the repo. Two consequences:

1. **No `testpaths`.** 54 scratch `test_*.py` files sit at the backend root, most calling `asyncio.run(...)` at module scope — so bare `pytest` from the backend root **executes live network crawls during collection** (27 real HTTPS requests to `pib.gov.in` observed in one run). Measured: **1308 collected, 8 errors, 3m26s** for bare `pytest` versus **1277 collected, 2 errors, ~12s** for `pytest tests/`. Only 21 of the 54 even contain a `def test_`.
2. **`asyncio_mode` is unset**, so pytest-asyncio runs in strict mode and every async test needs an explicit `@pytest.mark.asyncio` (869 occurrences across 43 files).

The two real collection errors are stale imports against refactored modules: `tests/test_fundamental_analyzer.py:18` (`fetch_fundamentals_yfinance`) and `tests/test_paper_trading.py:10` (`SignalGenerator`).

### D13 — Uncommitted hub-universe widening — CONFIRMED, flagged not judged

`engine/hub_universe.py:25-26` carries an uncommitted change made the same day as D1:

```diff
-    top_n: int = 3000,
-    min_turnover_cr: float = 1.0,
+    top_n: int = 20000,
+    min_turnover_cr: float = 0.0,
```

This widens the scored universe from the top 3000 NSE equities by 30-day turnover to effectively all of them, and removes the liquidity floor entirely. `min_turnover_cr = 0.0` admits illiquid counters that the floor existed to exclude — the prior architecture notes explicitly recommend *tightening* this (excluding SME/BE/BZ and using adaptive liquidity filters), not removing it.

The audit does not judge whether this is right — it may be a deliberate experiment. But it is worth an explicit decision, because `rebuild_hub_universe` runs daily at 03:30 and a 20,000-symbol universe changes the cost and latency of every downstream scoring cycle, and admitting zero-turnover symbols raises the risk of untradeable fills.

---

## 5. Merits

These are as real as the defects and should not be regressed.

| # | Merit | Evidence |
|---|---|---|
| M1 | **Boot-time refusal on unsafe risk config** — `SystemExit` if `MAX_RISK_PER_TRADE > 5%`, `MAX_PORTFOLIO_RISK > 50%`, or the conviction band exceeds 5%. Enforced in code, not policy. | `main.py:36-60` |
| M2 | **The central gate fails closed and does not trust its caller** — `_verify_canonical_event` re-checks the event against the DB rather than believing the caller-supplied evidence snapshot. | `decision_router.py:395`, `:492` |
| M3 | **`NO EVENT → NO TRADE` is enforced before spending an LLM call**, not after. | `news_discovery_engine.py:1125` |
| M4 | **Decision and execution observe one tick** via a shared `MarketSnapshot`, instead of racing two independent price paths. | `news_discovery_engine.py:392`, `market_snapshot.py` |
| M5 | **Realistic cost model** — brokerage `min(₹20, 0.03%)`, STT, exchange, SEBI, stamp, 18% GST; plus adverse-only 2–8bps slippage. More rigorous than a naive fill simulator. | `trade_simulator.py:133`, `:172` |
| M6 | **Position close takes a row lock** — `SELECT … WITH_FOR_UPDATE` filtered on `status == OPEN`, raising if already closed. | `trade_simulator.py:549-553` |
| M7 | **Every long Celery task has a Redis `SET NX EX` overlap guard** with a TTL exceeding its hard time limit, released in `finally`. | `india_tasks.py:111`, `news_scan.py:56`, `price_cache.py:67` |
| M8 | **Automatic `expires` on every beat entry**, applied programmatically so future entries are covered too — added after a 63k-task Redis backlog. | `celery_app.py:517-527` |
| M9 | **LLM grounding/hallucination check** with deterministic provenance, entity overlap and numeric consistency; tools are **structurally removed** from the menu when a canonical event is bound, not merely discouraged. | `decision_engine.py:1142`, `:1292` |
| M10 | **Every rejection is audited** — `_log_skipped_decision` writes an `AgentDecision` with a `skip_reason` at every rejection point. | `agent_loop.py:1138` |
| M11 | **Correct SEBI short-sell control** — a CNC SELL is blocked unless the stock is in Kite CNC holdings in sufficient quantity. | `execution.py:183-200` |
| M12 | **Anti-chase gates learned from real losses**, with the 30-minute and multi-session checks deliberately separate because the intraday one silently no-ops when 15m bars are missing. | `news_discovery_engine.py:409`, `:448` |
| M13 | **Fail-open/fail-closed split is deliberate and correct** — timing filters fail open so a data outage cannot halt all trading; the risk gate fails closed. | `news_discovery_engine.py:405-408` |
| M14 | **Celery session isolation done right** — a fresh `NullPool` engine per task, because prefork runs each task in its own `asyncio.run()` loop and pooled asyncpg connections bound to a dead loop cause `MissingGreenlet`. | `tasks/_db.py:21-50` |
| M15 | **Redis-backed alert dedup that works across workers**, replacing four per-process module globals that could not dedup in a multi-worker deployment. | `integrations/alerts/dedup.py:25-41` |
| M16 | **Alerts can never break a calling task** — `publish()` is wrapped in a blanket try/except that only logs. | `integrations/alerts/router.py:145` |
| M17 | **Telegram is hard-disabled under pytest**, so a test cannot post to the real chat. | `integrations/telegram_service.py:36-39` |
| M18 | **Look-ahead detection tooling already exists** — `replay_audit.py` freezes `datetime`, re-scores with only candles ≤ that timestamp, and diffs against the stored score. | `tasks/replay_audit.py` |
| M19 | **Deterministic fallbacks for LLM-dependent narrative** — `trade_explainer` falls back to templates whenever the LLM is unavailable. | `integrations/trade_explainer.py:39` |
| M20 | **Exceptional incident archaeology in comments.** Many guards carry the date, symbol and loss that motivated them. This is genuinely rare and materially speeds up future work. | `main.py:64-100`, `india_tasks.py:559-571`, `news_scan.py:28-45` |

---

## 6. Remediation backlog

Effort: **S** ≤1h · **M** ≤1 day · **L** multi-day.

### P0 — before the next live-mode consideration

| # | Action | Defect | Effort |
|---|---|---|---|
| P0-1 | Fix the `place_real_order` call signature in `decision_router.py:308-315`; add a test that exercises the LIVE branch against a mock so it cannot regress silently | D2 | **S** |
| P0-2 | Write `_ts` at every `PRICE_CACHE` write site and change the default to `0` so a missing timestamp reads as infinitely stale | D3 | **S** |
| P0-3 | Add an age check to `market_snapshot._from_websocket_tick` — it already has `_age_seconds`, it just discards it | D3 | **S** |
| P0-4 | Resolve D1: decide whether the uncommitted `_TECHNICAL_TRADE_ORIGINATION_BLOCKED = False` is intentional. If yes, commit it and update the contract doc; if no, revert. Do not leave it uncommitted while the worker hot-reloads it. **Owner decision, not the audit's.** | D1 | **S** |
| P0-6 | Decide on the uncommitted hub-universe widening (`top_n` 3000→20000, `min_turnover_cr` 1.0→0.0) before the 03:30 rebuild runs again | D13 | **S** |
| P0-5 | Make the kill switch DB-backed via `RuntimeConfig`, matching `halt`/`resume` | D4 | **S** |

### P1 — this week

| # | Action | Defect | Effort |
|---|---|---|---|
| P1-1 | Put `require_auth` on every mutating route; at minimum `PATCH /settings/`, the GTT routes, order cancel/modify, and ticker start/stop | D4 | **M** |
| P1-2 | Add a `pytest.ini` with `testpaths = tests` and an explicit `asyncio_mode` | D12 | **S** |
| P1-3 | Delete the three orphaned beat entries | D9 | **S** |
| P1-4 | Add a shared Kite REST rate limiter (Redis token bucket, as the LLM limiter already does) | D6 | **M** |
| P1-5 | Add a client order ID / idempotency key to `place_real_order` and pre-check `get_orders()` before placing | D6 | **M** |
| P1-6 | Fix the two stale test imports | D12 | **S** |
| P1-7 | Pin `boto3` and `openai` | D11-o | **S** |
| P1-8 | Add log rotation for the four `/tmp/*.log` files | D11-p | **S** |
| P1-9 | Remove the plaintext credentials from `autotrade-frontend/.env` | D11-q | **S** |

### P2 — structural

| # | Action | Defect | Effort |
|---|---|---|---|
| P2-1 | Give `compute_indicators()` an explicit caller contract: accept a `timeframe` and a `bar_closed` flag, or truncate the forming bar internally. Then run `replay_audit.py` as a scheduled check rather than a manual script | D5 | **L** |
| P2-2 | Replace `_safe_last()` with a bar-aligned accessor that returns NaN rather than silently reaching backwards | D5 (LB-2) | **M** |
| P2-3 | Put `PRICE_CACHE`/`LIVE_TICKS` behind a lock or a thread-safe wrapper; stop iterating them live | D7-R1 | **M** |
| P2-4 | Add a DB unique constraint on open positions per symbol to close the TOCTOU windows structurally | D7-R3 | **M** |
| P2-5 | Choose one schema path — Alembic or `init_db()` — and delete the other | D10 | **M** |
| P2-6 | Introduce Celery queues so the 5s exit loop does not contend with 2-hour backfills on 2 worker slots | §7.1 | **M** |
| P2-7 | Align `MANTLE_MAX_RPM` with the real 25 RPM quota, and decide whether the limiter should fail closed | D8 | **S** |
| P2-8 | Add schema validation (typed models) to LLM output instead of ad-hoc key checks | D8 | **M** |
| P2-9 | Reconcile the three model-ID sources and rewrite the `utils/llm.py` header, which documents a model no longer in use | D8 | **S** |
| P2-10 | Investigate the unresolved uvicorn memory leak (§9 U1) | U1 | **L** |

### Cross-reference — strategy-level backlog

`docs/2026-08-17_FORENSIC_POST_MORTEM.md` already carries a data-backed P0/P1/P2 backlog for **strategy** problems (no market-expectation anchor; `nc.confidence` being a sector label rather than a conviction score; post-event holding beyond 2 days; concentration with no hedge). That analysis is not repeated here and remains valid. **The two backlogs are complementary — this one is about code correctness, that one is about edge.** P0-1 and P0-2 there (force exit ≤2 days after the event; replace the `POST_EVENT_REVERSAL` stop-halving with an immediate exit) address roughly ₹44,000 of identified damage at low effort and should not wait on anything here.

---

## 7. Reference tables

### 7.1 Celery

`tasks/celery_app.py` — app `autotrade_pro`, broker and backend both `settings.REDIS_URL`.

| Setting | Value |
|---|---|
| `task_soft_time_limit` / `task_time_limit` | 300 / 600 (global default) |
| `beat_max_loop_interval` | 5 |
| `worker_prefetch_multiplier` | 1 |
| `task_acks_late` | True |
| `worker_cancel_long_running_tasks_on_connection_loss` | True |
| Serialization / timezone | json / UTC |
| Worker concurrency | `--concurrency=2`, set on the command line only |

**There are no queues and no routing.** No `task_routes`, no `task_queues`, no `task_default_queue`, and no per-call `queue=` anywhere. Every task lands on the single default queue, so `fast_sl_check` (5s), `refresh_live_prices` (15s) and the ~2-hour `refresh_full_nse_candles` all contend for the same 2 slots. The `expires` loop bounds the backlog but cannot prioritise.

TLS (`broker_use_ssl` with `CERT_NONE`) applies only when `REDIS_URL` starts `rediss://`. The live `.env` uses plain `redis://localhost:6379/0`, so that branch is currently inert — worth knowing before anyone "fixes" it.

**Interval-scheduled tasks**

| Beat key | Task | Cadence |
|---|---|---|
| `fast-sl-check-every-5s` | `tasks.fast_sl_check` | 5s |
| `refresh-live-prices-15s` | `tasks.refresh_live_prices` | 15s |
| `refresh-price-cache-every-30s` | `tasks.price_cache.refresh_price_cache` | 30s |
| `scan-prices-every-30s` | `tasks.market_scan.scan_watchlist` | 30s |
| `market-shock-guard-every-30s` | `tasks.market_shock_guard` | 30s |
| `market-news-alert-every-1min` | `tasks.market_news_alert` | 60s |
| `india-trade-loop-every-60s` | `tasks.india_trade_loop` | 60s |
| `refresh-sector-data-60s` | `tasks.refresh_sector_data` | 60s |
| `refresh-market-breadth-2min` | `tasks.refresh_market_breadth` | 120s |
| `crawl-news-every-5min` | `tasks.news_scan.scan_news` | 300s |
| `india-price-scan-every-5min` | `tasks.india_price_scan` | 300s |
| `narrative-intelligence-every-5min` | `tasks.refresh_narrative_intelligence` | 300s |
| `trade-journal-sync-5min` | `tasks.india_tasks.sync_trade_journal` | 300s |
| `candle-staleness-watchdog-5min` | `tasks.candle_staleness_watchdog` | 300s |
| `breakout-discovery-every-5min` | `tasks.breakout_discovery` | 300s |
| `sync-sse-announcements-10min` | `tasks.india_tasks.sync_sse_announcements` | 600s |
| `market-scanner-every-15min` | `tasks.market_scanner.run_market_scanner` | 900s |
| `pre-event-gap-scan-every-15min` | `tasks.india_pre_event_gap_scan` | 900s |
| `kite-portfolio-sync-15min` | `tasks.india_tasks.sync_kite_holdings` | 900s |
| `long-tail-intraday-every-30min` | `tasks.sync_long_tail_intraday` | 1800s |
| `refresh-ipo-data-30min` | `tasks.india_tasks.refresh_ipo_data` | 1800s |
| `momentum-discovery-every-30min` | `tasks.momentum_discovery` | 1800s |
| ~~`india-options-every-15min`~~ | ~~`tasks.india_options_analysis`~~ | **orphaned — D9** |

**Cron-scheduled tasks** (all UTC; IST = UTC+5:30)

| Beat key | Task | Cron |
|---|---|---|
| `zerodha-token-expiry-check` | `tasks.india_tasks.check_zerodha_token` | 00:35 |
| `kite-check-token-daily` | `tasks.kite_check_token` | 00:35 |
| `full-nse-candles-weekly` | `tasks.refresh_full_nse_candles` | Sun 01:00 |
| `seed-calendar-daily` | `tasks.seed_calendar_events` | 01:30 |
| `refresh-stock-info-daily` | `tasks.refresh_stock_info_cache` | 02:30 |
| `kite-token-refresh-daily` | `tasks.zerodha_token_refresh` | 02:30 |
| `upstox-token-refresh-daily` | `tasks.refresh_upstox_token` | 02:45 |
| `full-bse-candles-daily` | `tasks.refresh_full_bse_candles` | 03:00 |
| `sync-nse-eq-instruments-daily` | `tasks.sync_nse_eq_instruments` | 03:00 |
| `zerodha-nfo-instrument-refresh-daily` | `tasks.india_tasks.refresh_zerodha_instruments` | 03:05 |
| `backfill-hub-1d-candles-daily` | `tasks.backfill_hub_1d_candles` | 03:10 |
| `rebuild-hub-universe-daily` | `tasks.rebuild_hub_universe` | 03:30 |
| `corporate-action-check-daily` | `tasks.india_tasks.corporate_action_check` | 03:35 |
| `refresh-isin-map-daily` | `tasks.refresh_isin_map` | 03:40 |
| `kite-start-ticker-on-open` | `tasks.kite_start_ticker` | 03:45 (09:15 IST) |
| `intraday-morning-entry` | `tasks.intraday_entry` | 04:00 Mon–Fri — **hard-blocked at runtime** |
| `weekend-reflection-loop` | `tasks.india_weekend_reflection` | Sat 05:30 |
| `agent-eod-reconcile` | `tasks.agent_eod_reconcile` | 09:55 Mon–Fri (15:25 IST) |
| `intraday-eod-squareoff` | `tasks.intraday_squareoff` | 09:40 Mon–Fri (15:10 IST) |
| `kite-sync-candles-daily` | `tasks.kite_sync_candles` | 10:00 |
| `capital-snapshot-daily` | `tasks.india_tasks.save_capital_snapshot` | 10:45 (16:15 IST) |
| `refresh-priority-1d-candles-evening` | `tasks.refresh_priority_1d_candles` | 12:00 Mon–Fri (17:30 IST) |
| `india-fii-dii-daily` | `tasks.india_fii_dii_fetch` | 13:00 (18:30 IST) |
| `india-mf-nav-daily` | `tasks.india_mutual_fund_nav` | 14:30 (20:00 IST) |
| `fetch-earnings-daily` | `tasks.fetch_earnings_transcripts` | 14:30 (20:00 IST) |
| `kite-sync-holdings-daily` | `tasks.kite_sync_holdings` | 15:35 |
| `weekly-report` | `tasks.india_tasks.weekly_report` | Sun 17:00 |
| `india-fundamentals-weekly` | `tasks.india_fundamental_update` | Sun 18:30 |
| `sector-cache-rebuild-weekly` | `tasks.rebuild_sector_cache` | Sun 19:00 |
| `ml-model-training-weekly` | `tasks.india_tasks.train_ml_models_task` | Sat 20:30 |
| `purge-old-news-weekly` | `tasks.purge_old_news` | Sun 21:00 |
| `master-intelligence-every-15min` | `tasks.run_master_intelligence_cycle` | h3–10, m14/29/44/59, Mon–Fri |
| ~~`india-equity-options-enrich`~~ | ~~`tasks.india_equity_options_enrich`~~ | **orphaned — D9** |
| ~~`fno-expiry-sweep-daily`~~ | ~~`tasks.fno_expiry_sweep`~~ | **orphaned — D9** |

**Overlap guards**

| Redis key | TTL | Task (soft/hard limit) |
|---|---|---|
| `india_price_scan:running` | 2520s | `tasks.india_price_scan` (2400/2460) |
| `kite_live_candles:running` | 1320s | `tasks.kite_live_candles` (1200/1260) |
| `news_scan:running` | 1020s | `tasks.news_scan.scan_news` (900/960) |
| `price_cache_refresh:running` | 180s | `tasks.price_cache.refresh_price_cache` (120/150) |

`run_master_intelligence_cycle` instead uses a DB guard (`india_tasks.py:2865-2887`): it looks for a `HubCycleLog` row with `status == "running"` and `cycle_start >= now - 1200s`.

### 7.2 API surface

26 routers (`main.py:288-313`). Full per-route detail is long; what matters operationally is the **auth posture**.

**Authenticated (5 handlers via `require_auth`)**

| Route | Handler | Extra guard |
|---|---|---|
| `POST /api/v1/agent/cycle/trigger` | `trigger_cycle` | — |
| `POST /api/v1/agent/halt` | `halt_trading` | — |
| `POST /api/v1/agent/resume` | `resume_trading` | — |
| `POST /api/v1/settings/mode` | `set_trade_mode` | `confirm == "I_UNDERSTAND_REAL_MONEY"` + token check, else 409 |
| `POST /api/v1/zerodha/orders` | `place_order` | `X-Confirm-Real-Order: yes` + `PAPER_MODE=false` + `ZERODHA_ENABLED=true` |

**Header-guarded but unauthenticated**

| Route | Guard | Effect |
|---|---|---|
| `POST /api/v1/agent/kill-switch` | `X-Kill-Confirm: FLATTEN` | flattens the entire book |
| `PUT /api/v1/agent/config` | `X-Agent-Config-Update: yes` | mutates agent config |

**Unauthenticated and mutating** — see the D4 table.

**Router prefixes**

`/api/v1/` + `portfolio` · `doctor` · `earnings` · `agent` · `intelligence` · `portfolios` · `mf-tracker` · `sip` · `tax` · `allocation` · `ipo` · `chat` · `trades` · `signals` · `news` · `analytics` *(shared by `analytics.py` and `attribution.py`)* · `simulation` · `settings` · `india` · `kite` · `zerodha` · `auth` · `buyback` · `upstox`; plus `/ws`.

**WebSocket endpoints** (`api/websocket.py`, all unauthenticated): `/ws/portfolio` (:63), `/ws/trades` (:93), `/ws/prices` (:136), `/ws/logs` (:164), `/ws/candles/{symbol}` (:299), `/ws/live-prices` (:408), `/ws/positions-pnl` (:423).

Largest route modules: `api/india.py` (2485 lines), `api/zerodha.py` (1789), `api/agent.py` (802), `api/intelligence.py` (699), `api/attribution.py` (645).

### 7.3 Database

`db/database.py` — `create_async_engine` over `postgresql+asyncpg://`, **`NullPool`** with `connect_args={"statement_cache_size": 0}` (`:22-23`). The comment (`:11-18`) explains this was for Supabase's transaction-mode PgBouncer, which reassigns backends per transaction. **The system now runs against local Docker Postgres**, so `NullPool` — opening a fresh connection per session — is likely no longer the right choice. *(PLAUSIBLE: worth benchmarking; not a correctness bug.)*

`get_db()` (`:40-61`) commits on success and rolls back on exception, with the rollback itself guarded so a dead connection cannot raise a masking `PendingRollbackError`.

**52 ORM models.** The trading-critical core:

| Model | Table | Notes |
|---|---|---|
| `VirtualWallet` | `virtual_wallet` | balance, equity, realised/unrealised, peak_balance, max_drawdown |
| `PaperTrade` | `paper_trades` | the canonical trade record; attribution block (strategy_name, regime_at_entry/exit, exit_reason, r_multiple, MFE/MAE) |
| `OpenPosition` | `open_positions` | FK → `PaperTrade` |
| `Candle` | `candles` | **id BigInteger** (migration 0004 — int4 exhausted 2026-06-19 because `ON CONFLICT DO NOTHING` burns sequence values); `UniqueConstraint(symbol, timeframe, timestamp)` |
| `CausalEvent` | `causal_events` | the canonical event the whole architecture hangs on; FK → `news_items` |
| `AgentDecision` | `agent_decisions` | includes `skip_reason` — every rejection is recorded |
| `AgentTrade` | `agent_trades` | agent-side trade record |
| `MasterIntelligenceScore` | `master_intelligence_scores` | 8 sub-scores + master_score + rank |
| `RuntimeSettings` | `runtime_settings` | key/value JSON — the DB override layer |
| `TradeExcursionSample` | `trade_excursion_samples` | MFE/MAE time series |
| `ReasoningVerdict` | `reasoning_verdicts` | LLM verdict vs arithmetic confidence |
| `TradeLesson` | `trade_lessons` | fed back into LLM prompts |
| `PortfolioThesis` | `portfolio_theses` | `halt_new`, `size_multiplier`, `max_new_entries` |
| `ReentryWatch` | `reentry_watches` | FK → event |
| `MasterEvent` | `master_events` | **declared but unused** — the prior map's finding still holds |

Remaining 37 cover portfolio tracking, SIP/MF, tax, IPO, buyback, Kite/Zerodha sync, hub universe/history, and performance snapshots.

`utils/runtime_config.py` is the DB override layer over `.env`, with a **whitelist of 30 keys** (`:33-86`); `RuntimeConfig.set()` raises on an unknown key or a type mismatch. `to_dict()` (`:281-309`) powers `GET /api/v1/settings` and omits `shock_cooldown_until` and `last_news_alert_at`.

### 7.4 Configuration

`utils/config.py` (806 lines), `Settings(BaseSettings)`, singleton at `:806`.

**Capital and risk**

| Setting | Value | Line |
|---|---|---|
| `AGENT_EQUITY` / `PAPER_TRADING_BALANCE` | 500,000.0 | 290 / 540 |
| `MAX_RISK_PER_TRADE` | 0.02 | 559 |
| `MAX_OPEN_POSITIONS` | 125 (runaway-loop ceiling) | 563 |
| `MAX_DAILY_LOSS` | 0.05 | 571 |
| `MAX_PORTFOLIO_RISK` | 0.15 | 579 |
| `MIN_CASH_BUFFER` | 0.10 | 580 |
| `RISK_PER_TRADE_MIN` / `MAX` | 0.015 / 0.030 | 581-582 |
| `CONVICTION_HIGH` | 70.0 | 583 |
| `MAX_NEW_ENTRIES_PER_CYCLE` | 8 | 584 |
| `MAX_CONCURRENT_POSITIONS` | 10 | 608 |
| `MAX_POSITIONS_PER_SECTOR` | 2 | 609 |
| `MAX_SECTOR_CAPITAL_PCT` | 0.20 | 610 |
| `MAX_STRATEGY_CAPITAL_PCT` | 0.35 | 611 |
| `AGENT_MAX_POSITION_WEIGHT` | 0.05 | — |
| `AGENT_DAILY/WEEKLY/MONTHLY_DD_STOP` | 0.03 / 0.05 / 0.10 | — |

Note the tension between `MAX_OPEN_POSITIONS = 125` and `MAX_CONCURRENT_POSITIONS = 10`. The former is a deliberate runaway guard, not a target — the forensic post-mortem observed 101 concurrent positions and recommends capping at ~40.

**Mode flags**

| Flag | Default |
|---|---|
| `PAPER_MODE` | **True** |
| `AGENT_PAPER_MODE` | **True** |
| `ZERODHA_PAPER_MODE` | **True** |
| `ZERODHA_ENABLED` | **False** |
| `AGENT_ENABLED` | True |
| `AGENT_DRY_RUN` | False |

**Strategy flags:** `PRE_EVENT_GAP_ENABLED` True (:345), `DIRECT_NEWS_ENABLED` True (:363), `NEWS_REGIME_OVERRIDE_ENABLED` True (:326), `EQUITY_SHORT_ENABLED` True (:296), `INTRADAY_ENABLED` **False** (:527), `SCANNER_ENABLED` **False** (:573), `ENABLE_ML_PREDICTIONS` False (:126), `ENABLE_SHOCK_GUARD` False (:480), `ENABLE_PRE_EVENT_MARKET_EXPECTATION_GATE` True (:629), `ENABLE_MACRO_REGIME_OVERLAY` True (:635).

**Confidence:** `PAPER_CONFIDENCE_THRESHOLD` 20.0 (:278, `.env` sets 50.0), `LIVE_CONFIDENCE_THRESHOLD` 70.0 (:279), `AGENT_CONFIDENCE_THRESHOLD` 30.

**VIX:** `VIX_HIGH_THRESHOLD` 22.0, `VIX_SIZE_SCALE_MIN` 0.50, `VIX_EXTREME_THRESHOLD` 30.0 (:449-451), `SHORT_MAX_VIX` 28.0 (:301).

### 7.5 Frontend

React 19.2 + Vite 8 + Tailwind 4 + React Router 7, plain JS (`.jsx`, no TypeScript). 41 pages, ~120 components across 18 feature folders, 25 hooks, 3 contexts.

- **`src/api/client.js`** — `baseURL = import.meta.env.VITE_API_BASE || ''`, i.e. **same-origin by default**, so the Vite proxy handles dev and the prod build hits whatever origin served it. JWT from `localStorage['atp_admin_token']`; request interceptor attaches it, response interceptor unwraps `res.data`. Axios timeout **10s**. `apiFetch()` (`:43-78`) adds an `AbortController` 10s timeout — added because bare `fetch()` has no default timeout and left `Settings.jsx` stuck on "Loading settings…" forever against a hung backend. ~90 named endpoint wrappers.
- **`vite.config.js`** — dev proxy `/api → backendUrl`, `/ws → backendWs` with `ws: true`. HMR host is conditional on `VITE_HMR_HOST`; hardcoding the ELB value previously broke every localhost page load.
- **`src/hooks/useWebSocket.js`** — builds the URL from `window.location.host`, auto-reconnects after 3s, and detaches all handlers before close on unmount so teardown cannot resurrect the socket.
- `puppeteer` is a **runtime** dependency (not a devDependency), used by `capture.cjs`/`test_ui.js`.

### 7.6 Integrations

One funnel: call sites build an `AlertEvent` and call `alerts.publish()`; nothing calls `telegram_service` directly.

| Module | Role |
|---|---|
| `telegram_service.py` (156) | pure transport over httpx; returns `message_id` so the router can thread replies; hard test guard at `:36-39` |
| `alerts/events.py` (131) | schema — `AlertCategory`, `AlertAction`, `Severity`, payload dataclasses |
| `alerts/router.py` (191) | `publish()` — severity filter → dedup → reply-lookup → render → send → store message_id → optional chart/PDF. Blanket try/except |
| `alerts/dedup.py` (128) | Redis `SET NX EX`; fails open |
| `alerts/templates.py` (164) | **all Telegram copy is LLM-generated** via `call_llm_chat` |
| `alerts/charts.py` (152) | matplotlib `Agg` backend (no display in a worker) |
| `alerts/reports.py` (127) | ReportLab weekly PDF — chosen over weasyprint for being pure-Python; never raises on empty sections |
| `sheet_logger.py` (1597) | 36-column trade journal → Excel or Google Sheets, with live `GOOGLEFINANCE()` formulas; `sync_journal()` is idempotent |
| `trade_explainer.py` (320) | entry/hold/post-mortem narratives with deterministic template fallback |
| `zerodha_iceberg.py` (38) | **dead and unsafe — see D11-g** |

---

## 8. The root-level script sprawl

`autotrade-backend/` holds **157 `.py` files at its root**, of which **137 are git-tracked**. Exactly two are production:

- `main.py` — the FastAPI application
- `news_discovery_engine.py` — the live news trade engine

The remaining 155 break down as: 54 `test_*` (ad-hoc scripts, **not** the pytest suite — that is `tests/`), 29 `check_*`, 19 `query_*`, 7 `analyze_*`, 6 `update_*`, 5 `dump_*`, 4 `get_*`, plus assorted `fix_*`, `scan_*`, `diagnose_*`, `alter_*`, `migrate_*`.

None are reachable from any scheduled task or API route. They are excluded from the function inventory above by design — they are not production code. They cause two concrete problems: they make the pytest default unusable (D12), and they make the repo root actively misleading to navigate, since the single most important file in the system sits among them.

**Suggested disposition** (not performed by this audit): move the useful ones under `scripts/` (which already exists and holds the maintained tooling), and delete the rest. That is a judgement call for the owner, since some may be referenced in personal shell history or notes.

---

## 9. Not verified

Honest limits of this audit. Each of these would need a running system, production data, or access this pass did not have.

| # | Open question |
|---|---|
| U1 | **The uvicorn memory leak is unresolved.** OOM-killed 14 Aug (6.0 GB) and 17 Aug (5.6 GB). `MemoryMax=3G` bounds the blast radius but does not fix the cause. Unbounded in-process dicts (`LIVE_TICKS`, `_snapshot_cache` at `market_snapshot.py:45`, `INFO_CACHE`) are plausible contributors but were not profiled. `py-spy` is already in `requirements.txt`. |
| U2 | Whether D3 has actually caused a bad fill since 2026-07-08. Requires correlating `paper_trades.entry_price` against tick history. |
| U3 | Whether `hub_daily_history` reproduces matching scores on a real replay. `replay_audit.py` exists but was not run, and D8's non-determinism means exact replay is impossible for any LLM-influenced field. |
| U4 | The live values of any `RuntimeConfig`-overridden setting. Only `.env` and `config.py` defaults were read; a DB row could be overriding any of the 30 whitelisted keys right now. |
| U5 | Whether the D8 `AGENT_LLM_TOOLUSE_ENABLED` no-op holds in production — it depends on the live `.env`, which is not version-controlled. |
| U6 | Whether the FK relationship between `causal_events.news_id` and the weekly news purge has caused failures. Depends on live DDL not inspected here. |
| U7 | Actual `NotRegistered` error volume from D9 — the schedule was read, the worker logs were not. |
| U8 | Whether `NullPool` is now hurting throughput against local Postgres. Needs benchmarking. |
| U9 | Frontend runtime behaviour. The code was read; the app was not driven, and mobile viewports were not verified (a known tool limitation on this machine). |
| U10 | Per-column read/write audit of the ~37 non-trading-critical ORM models, and of the 27 tables that exist in `01_supabase_dump.sql` but have no ORM class. |

---

## 10. Method

Read-only throughout. Three parallel research passes covered (a) `main.py`, `api/`, `db/`, `utils/`; (b) the trade lifecycle — `crawler/` → `engine/indicators.py` → `engine/agent/*` → `utils/llm.py` → `engine/risk_manager.py` → `engine/zerodha_executor.py` → `paper_trading/`; (c) `tasks/`, `tests/`, deployment, frontend, `integrations/`.

Every finding promoted to D1–D4 was independently re-read by the author at the cited lines before inclusion. Test counts are measured, not remembered — `pytest tests/ --collect-only -q` was executed and reported **1277 collected, 2 errors**, superseding the recorded 2026-07-21 baseline of 399 pass / 54 fail. Function bodies were read rather than inferred from names; where a claim could not be verified without running the system, it is in §9 rather than stated as fact.

No code was changed, nothing was staged, and nothing was committed.
