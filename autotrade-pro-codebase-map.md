# AutoTrade Pro — Full Codebase Map & Trading Pipeline Audit

**Date:** 2026-07-17
**Method:** Read-only. No code was changed. Findings were gathered by seven parallel research passes over the actual code (function bodies read, not inferred from names/docstrings), each covering a bounded subsystem, plus direct verification greps by the report author for the most consequential claims. File:line citations are given wherever a specific claim is made; where something could not be verified without running the system, that is stated explicitly rather than guessed.

**Coverage scope (see §12 for exact boundaries):** Full depth on the automated trading pipeline — Celery beat entry points, `engine/intelligence_hub.py`, `engine/agent/*` (primary flow functions), `engine/fno/*`, `paper_trading/*`, `crawler/*`, the news/narrative subsystem, all `api/*.py` routes, `db/models.py` + `db/database.py`, `utils/config.py` + `utils/runtime_config.py`, and a lighter pass over the frontend. Not covered at full function-by-function depth: `engine/agent/strategies/*` internals, `engine/agent/decision_engine.py`'s LLM tool-use functions, `engine/agent/execution._live_execute`/`engine/zerodha_executor.py`'s full order-modification surface, and exhaustive column audits of all 45 DB tables (the trading-critical dozen were audited; the remainder were not). The ~130 root-level `check_*.py`/`test_*.py`/`dump_*.py`/`query_*.py`/`diagnose_*.py` scripts were confirmed unreachable from any scheduled or API entry point and are excluded from function-level inventory per the task's own scoping rule (not production code).

---

## 1. Repository Map

```
autotrade-backend/
├── main.py                  FastAPI app: router registration, startup safety checks,
│                             3 non-Celery asyncio background loops (live prices, breadth, cache warm)
├── tasks/                    Celery app + all scheduled entry points
│   ├── celery_app.py         Broker config + full beat schedule (the master entry-point list)
│   ├── india_tasks.py        3973 lines — houses the real trading loops (hub cycle wrapper,
│   │                         india_trade_loop, fast_sl_check, intraday, shock_guard, etc.)
│   ├── market_scan.py        Legacy/parallel 30s watchlist candle crawler (UserWatchlist only)
│   ├── market_scanner.py     15-min NSE-universe scorer → market_shortlist table
│   ├── news_scan.py          1-min news crawl task wrapper
│   ├── narrative_scan.py     5-min narrative-intelligence refresh wrapper
│   ├── unstructured_alpha_scan.py  Hourly Apple-supply-chain-only news scanner
│   ├── pre_diagnose.py       Cache-warming task for top-10 shortlist deep-analysis
│   ├── ml_optimizer.py       Standalone CLI script, NOT wired to Celery — trains on a
│   │                         **fabricated (mocked) target variable**; dead scaffold
│   ├── replay_audit.py, temporal_audit.py, test_dry_run.py, test_full_flow.py  Manual/CLI-only
│   └── _db.py                Celery per-task DB session/engine factory (NullPool, no shared state)
├── engine/                   Scoring, strategy, and decision logic
│   ├── intelligence_hub.py   THE central scoring engine — builds MasterContext, scores the
│   │                         universe, persists MasterIntelligenceScore rows
│   ├── decision_router.py    Documented as "every decision funnels through route_decision()" —
│   │                         VERIFIED UNCALLED outside its own docstring/tests (see §8)
│   ├── risk_manager.py       Top-level risk manager — used by news_discovery_engine.py and
│   │                         engine/agent/backtester.py, NOT by the live hub/trade-loop flow
│   ├── sector_graph.py       Separate "sector" concept (2nd-order sector graph), only caller
│   │                         is the dead engine/news_discovery_engine.py — distinct from the
│   │                         SECTOR_CACHE/build_sector_context() actually used by the Hub
│   ├── signal_generator.py, india_signal_generator.py, allocation_engine.py
│   │                         Manually/API-invoked signal paths, NOT called by the automated
│   │                         Hub cycle or india_trade_loop
│   ├── zerodha_executor.py   Real-broker order placement (paper-mode-gated), 10-rule safety gate
│   ├── zerodha_portfolio.py  Read-only Demat holdings sync — never places orders
│   ├── agent/                 Legacy full-cycle orchestrator (agent_loop.py) plus still-live
│   │                         library modules reused directly by india_tasks.py:
│   │   ├── agent_loop.py     run_agent_cycle() — docstring claims 15-min Celery-beat scheduling;
│   │   │                     VERIFIED not in beat_schedule; reachable only via manual API/tests
│   │   ├── decision_engine.py  fuse() — sizing + multiplicative confidence formula; used by
│   │   │                     BOTH the hub cycle (fresh instances) and agent_loop (singletons)
│   │   ├── risk_manager.py   RiskManagerAgent — the risk gate ACTUALLY used by the live pipeline
│   │   │                     (distinct file from engine/risk_manager.py above — name collision)
│   │   ├── execution.py      AgentExecutionManager — paper/live dispatch, DB writes
│   │   ├── dynamic_management.py, chart_brief.py, shock_guard.py  Live library code, called
│   │   │                     directly by india_tasks.py functions
│   │   ├── selector.py, analyzer.py, fundamentals.py, macro.py, portfolio_context.py,
│   │   │   momentum_filter.py, market_regime.py, morning_regime.py, event_arbitrage.py,
│   │   │   unstructured_alpha.py, reflection.py, performance_engine.py, backtester.py
│   │   │                     Mostly reachable only through agent_loop.run_agent_cycle()
│   │   │                     (manual-trigger path) except where cited otherwise above
│   │   └── strategies/       Per-strategy candidate generators (not read function-by-function)
│   ├── fno/                   Futures & options: Greeks, contracts, expiry sweep, margin model,
│   │                         hedging, vol strategies — ALL gated behind ENABLE_FNO=False (default)
│   ├── narrative_engine.py, news_discovery_engine.py, news_impact.py, event_classifier.py,
│   │   tavily_enricher.py, stock_enricher.py, stock_context_builder.py, calendar_engine.py,
│   │   llm_explainer.py, deep_analysis.py, fundamental_analyzer.py
│   │                         News/event/narrative intelligence — feeds sector-boost + sentiment
│   │                         into the Hub; engine/news_discovery_engine.py is UNCALLED (dead)
│   ├── momentum_screener.py, breakout_screener.py, screener_deep.py, pre_trade_research.py
│   │                         Universe-widening feeders (write to hub_universe/user_watchlist),
│   │                         NOT scoring paths — no overlap with intelligence_hub.py's scoring
│   ├── portfolio_analytics.py, portfolio_doctor.py, portfolio_service.py, mf_signal_engine.py,
│   │   sip_engine.py, tax_engine.py, ipo_analyzer.py, mutual_fund_analyzer.py,
│   │   earnings_summarizer.py, candlestick.py, candlestick_patterns.py, indicators.py,
│   │   ml_predictor.py, backtester.py, hub_universe.py, nse_crawler.py, india_specific.py
│   │                         Supporting/display analytics for the non-trading dashboards
│   │                         (tax, SIP, MF, IPO, portfolio-doctor) — not part of the trade
│   │                         decision pipeline; module-purpose only, not function-audited
├── crawler/                   Every external-data ingestion path (28 files) — see §5
├── paper_trading/             Virtual execution: trade_simulator.py (canonical), virtual_wallet.py,
│                             pnl_calculator.py, simulation_logger.py, position_tracker.py
│                             (position_tracker.py is legacy/mostly-dead — see §8)
├── services/kite_service.py   Read-only Kite holdings sync + XIRR calc, no order placement
├── db/                        models.py (45 tables), database.py (session/engine mgmt),
│                             migrations/ (vestigial — never actually run, see §6)
├── api/                       26 FastAPI routers — see §2/§9 for the full route inventory
└── utils/                     config.py (Settings, ~140 fields), runtime_config.py (DB-backed
                              override layer, precedence: DB > env), cache.py, logger.py,
                              nse_market_status.py, sector_cache.py, nav_cache.py

autotrade-frontend/src/
├── api/client.js              Axios wrapper, JWT from localStorage, no hardcoded secrets found
├── pages/ (37 files)          Dashboards over the above APIs; no page wires a button to place/
│                             close/convert a trade or to kill-switch/halt/resume (verified by
│                             grep — those endpoints are reachable only via direct HTTP)
└── components/                Chart, watchlist, portfolio, tax, SIP, IPO, MF widgets — display only
```

---

## 2. Entry Points Inventory

Source: `tasks/celery_app.py` beat schedule (all cadences below are exact, from the file) cross-referenced with the seven research passes' call-graph tracing, plus a full read of `main.py` and every `api/*.py` router.

### Celery beat-scheduled tasks (automated, no human input required)

| Entry point | Trigger | Schedule | file:line | What it ultimately does |
|---|---|---|---|---|
| `tasks.run_master_intelligence_cycle` | cron | `hour="3-10", minute="14,29,44,59"` Mon-Fri (15-min bar close, 45s countdown) | tasks/india_tasks.py:2850 | **The core Hub cycle** — builds unified market context, scores the full NSE universe, opens/closes paper positions. See §3 Flow A. |
| `tasks.india_trade_loop` | interval | 60s, NSE hours | tasks/india_tasks.py:1323 (`_india_trade_loop` at :512) | Independent 60s paper-trade entry loop reading `MasterIntelligenceScore` directly. See §3 Flow B. |
| `tasks.fast_sl_check` | interval | 5s | tasks/india_tasks.py:1524 (`_fast_sl_check` at :1341) | Exit-only SL/TP sweep on open positions via live LTP. |
| `tasks.market_shock_guard` | interval | 30s | tasks/india_tasks.py:1566 | Delegates to `run_shock_guard`; **no-op by default** (`ENABLE_SHOCK_GUARD=False`). |
| `tasks.intraday_entry` | cron | 09:30 IST daily, Mon-Fri | tasks/india_tasks.py:2236 (`_intraday_entry_task` at :1695) | MIS intraday book entry; **no-op by default** (`INTRADAY_ENABLED=False`). Reimplements `india_trade_loop`'s logic independently rather than sharing it. |
| `tasks.intraday_squareoff` | cron | 15:10 IST daily, Mon-Fri | tasks/india_tasks.py:2243 (`_intraday_squareoff_task` at :2153) | Force-closes all MIS positions; runs unconditionally (not gated by `INTRADAY_ENABLED`) since SELL/short trades from the main loop are also MIS-tagged. |
| `tasks.breakout_discovery` | interval | 5min, NSE hours | tasks/india_tasks.py:228 | Screens all NSE symbols for breakouts, injects into `hub_universe`/`user_watchlist`. Feeder only, not a scoring path. |
| `tasks.momentum_discovery` | interval | 30min | tasks/india_tasks.py:272 | Screens for 30-day sustained momentum, injects into universe. Feeder only. |
| `tasks.market_scan.scan_watchlist` | interval | 30s | tasks/market_scan.py:29 | Fetches 1h candles for `UserWatchlist` symbols only (legacy/narrow path, separate from the India-wide crawl). |
| `tasks.market_scanner.run_market_scanner` | interval | 15min | tasks/market_scanner.py:291 (`_run_market_scanner` at :35) | Scores full NSE universe (Hub-score-aware) → overwrites `market_shortlist` table. **Its own docstring claims `india_trade_loop` reads this output; verified false** — `india_trade_loop` reads `MasterIntelligenceScore` directly. |
| `tasks.news_scan.scan_news` | interval | 60s | tasks/news_scan.py:29 | Crawls 7+ news sources, sentiment-scores, writes `news_items`, triggers event classification. See §3 Flow D. |
| `tasks.purge_old_news` | cron | Sunday 21:00 UTC | tasks/india_tasks.py:3565 | Raw-SQL delete of `news_items` older than 60 days. Potential FK-violation risk against `causal_events.news_id` (unconfirmed against live migration DDL). |
| `tasks.refresh_narrative_intelligence` | interval | 5min, NSE hours | tasks/narrative_scan.py:20 | RSS+Telegram scrape → keyword/LLM sector-boost scores → in-memory cache only (no DB persistence). |
| `tasks.unstructured_alpha_scan` | cron | hourly, minute=15 | tasks/unstructured_alpha_scan.py:33 | Apple-supply-chain-only news scanner; **computes a signal and discards it** (logged only, never persisted or acted on). |
| `tasks.india_price_scan` | interval | 5min, NSE hours | crawler/india_price_feed.py (`run_india_price_crawl`) | OHLCV candle ingestion for the India universe + indices + VIX. |
| `tasks.india_fii_dii_fetch` | cron | 13:00 UTC daily | crawler/fii_dii_crawler.py | FII/DII net-flow ingestion. |
| `tasks.india_weekend_reflection` | cron | Saturday 05:30 UTC | engine/agent/reflection.py (not deep-traced) | Weekend LLM self-reflection loop. |
| `tasks.india_options_analysis` | interval | 15min, NSE hours | crawler/options_chain.py | NIFTY/BANKNIFTY options chain ingestion. |
| `tasks.india_equity_options_enrich` | cron | 05:30 & 09:30 UTC, Mon-Fri | crawler/equity_options.py | Per-stock options enrichment (PCR/IV); gated `ENABLE_HUB_OPTIONS`. |
| `tasks.india_mutual_fund_nav` | cron | 14:30 UTC daily | crawler/india_price_feed.py (`fetch_all_mutual_fund_navs`) | AMFI NAV bulk fetch. |
| `tasks.fno_expiry_sweep` | cron | 10:15 UTC, Mon-Fri | engine/fno/expiry.py (`settle_expired_positions`) | Cash-settles expired F&O positions; no-op unless `ENABLE_FNO=True`. |
| `tasks.india_fundamental_update` | cron | Sunday 18:30 UTC | engine/fundamental_analyzer.py (not deep-traced) | Weekly PE/ROE/promoter-holding refresh. |
| `tasks.rebuild_sector_cache` | cron | Sunday 19:00 UTC | crawler/sector_data.py | Rebuilds sector mapping cache. |
| `tasks.refresh_full_nse_candles` | cron | Sunday 01:00 UTC | crawler/zerodha_historical.py (`sync_full_nse_universe`) | Full-universe weekly candle refresh via Kite. |
| `tasks.sync_nse_eq_instruments` | cron | 03:00 UTC daily | crawler/zerodha_market.py (`sync_nse_eq_instruments`) | Syncs ~9,600 NSE EQ instruments from Kite. |
| `tasks.rebuild_hub_universe` | cron | 03:30 UTC daily | engine/hub_universe.py | Rebuilds `hub_universe` by 30-day avg turnover. |
| `tasks.backfill_hub_1d_candles` | cron | 03:10 UTC daily | (india_tasks.py) | Backfills prior-day 1d close for Hub symbols. |
| `tasks.refresh_priority_1d_candles` | cron | 12:00 UTC, Mon-Fri | (india_tasks.py) | Same-day 1d close refresh for held/scored stocks (2026-07-08 stale-close fix). |
| `tasks.refresh_live_prices` | interval | 15s | crawler/live_prices.py | Refreshes `PRICE_CACHE`. **Duplicated by `main.py`'s own `_live_price_loop()` — see §10.** |
| `tasks.refresh_stock_info_cache` | cron | 02:30 UTC daily | (fundamentals) | PE/market-cap/beta cache refresh. |
| `tasks.refresh_sector_data` | interval | 60s | crawler/sector_data.py | Sector performance from `PRICE_CACHE`. |
| `tasks.refresh_market_breadth` | interval | 120s | crawler/market_breadth.py | Advances/declines/gainers/losers. **Duplicated by `main.py`'s `_breadth_loop()` — acknowledged-intentional per its own comment, see §10.** |
| `tasks.seed_calendar_events` | cron | 01:30 UTC daily | engine/calendar_engine.py | Expiries/RBI/IPO/earnings calendar seed. |
| `tasks.india_tasks.refresh_ipo_data` | interval | 30min | crawler/ipo_crawler.py | IPO data refresh, in-memory only (no DB persistence). |
| `tasks.india_tasks.sync_sse_announcements` | interval | 10min | crawler/news_crawler.py (`fetch_sse_announcements`) | NSE Social Stock Exchange announcements. |
| `tasks.india_tasks.save_capital_snapshot` | cron | 10:45 UTC daily | engine/portfolio_analytics.py | Sharpe/Treynor/Jensen capital snapshot. |
| `tasks.india_tasks.weekly_portfolio_rebalance` | cron | Sunday 17:00 UTC | (portfolio) | Rebalance check + Telegram alert. |
| `tasks.india_tasks.weekly_ai_portfolio_report` | cron | Sunday 17:30 UTC | (portfolio/LLM) | AI-generated weekly report via Telegram. |
| `tasks.kite_sync_holdings` / `kite_sync_candles` / `kite_live_candles` / `kite_check_token` / `zerodha_token_refresh` / `kite_start_ticker` | cron/interval | various (daily / every 3min in-session) | crawler/zerodha_* | Broker connectivity/token lifecycle — gates whether real execution is even possible. |
| `tasks.candle_staleness_watchdog` | interval | 5min | (india_tasks.py) | Telegram warning if the live feed goes stale during NSE hours. |
| `tasks.fetch_earnings_transcripts` | cron | 14:30 UTC daily | crawler/earnings_crawler.py + engine/pdf_parser | Earnings transcript ingestion + LLM parsing. |
| `tasks.agent_eod_reconcile` | cron | 09:55 UTC, Mon-Fri | tasks/india_tasks.py (`agent_eod_reconcile_task`) | End-of-day reconciliation via `agent_loop`'s `_get_portfolio`/`_executor`. |
| `tasks.india_tasks.corporate_action_check` | cron | 03:35 UTC daily | crawler/corporate_actions.py | Detects splits/bonus issues, adjusts open positions. |
| `tasks.india_tasks.refresh_zerodha_instruments` | cron | 03:05 UTC daily | crawler/zerodha_instruments.py | NFO contract refresh (index F&O only). |
| `tasks.india_tasks.check_zerodha_token` / `zerodha-token-expiry-check` | cron | 00:35 UTC daily | crawler/zerodha_auth-adjacent | Token-expiry check. |
| `tasks.pre_diagnose.run_pre_diagnose` | fire-and-forget `.delay()` | triggered by market_scanner, not beat-scheduled | tasks/pre_diagnose.py:26 | Cache-warms deep-analysis for top-10 shortlist symbols. |

### API entry points (manual/webhook, human- or script-triggered)

Full route inventory is in §9 (Manual-Override Risk + full table). Summary: **26 routers**, the large majority read-only. The trading-relevant *mutating* routes are enumerated with their auth status in §9 — headline finding: only 5 of the dozens of mutating routes (`agent/cycle/trigger`, `agent/halt`, `agent/resume`, `settings/mode`, `zerodha/orders` POST) are behind authentication; the rest (kill-switch, position close, portfolio reset/reconcile, settings PATCH, watchlist injection, order modify/convert, GTT management) have none.

### Non-Celery entry points

- `main.py:_live_price_loop()` (108-131) — asyncio background task inside the FastAPI process, polls prices every 15s (open)/60s (closed). Runs independently of the Celery `refresh_live_prices` task.
- `main.py:_breadth_loop()` (137-151) — same pattern for market breadth, explicitly commented as an intentional per-process duplicate.
- `main.py:_warmup_info_cache()` (155-164) — one-shot fundamentals warm on boot.
- Kite WebSocket ticker — started both on FastAPI boot (`main.py:171-177`, if `ZERODHA_ENABLED` + token) and via the `kite-start-ticker-on-open` Celery cron — belt-and-suspenders by design.

---

## 3. End-to-End Trading Flow (per entry point)

### Flow A — The Master Intelligence Hub cycle (the primary, scheduled decision path)

Entry: `run_master_intelligence_cycle()`, `tasks/india_tasks.py:2850`, every 15 min in NSE hours.

1. **Gate**: `settings.AGENT_ENABLED` + `_is_trading_day()` (india_tasks.py:2871-2874). Not satisfied → SKIP.
2. **Portfolio load**: `_get_portfolio()` from `engine/agent/agent_loop.py` — shared in-memory `_portfolio` singleton, DB-hydrated on first use.
3. **Overlap guard**: queries `HubCycleLog` for a `status="running"` row within the last 1200s (india_tasks.py:2880-2903); if found → SKIP this tick (guards against the multi-cycle pile-up incident documented in project history).
4. **Live-price hot-patch**: `fetch_live_snapshot()` (crawler/live_snapshot.py) patches `PRICE_CACHE`/`SECTOR_CACHE` for open-position symbols — necessary because the Celery worker process doesn't receive WebSocket ticks directly.
5. `HubCycleLog(status="running")` row written and committed.
6. **Universe build**: `get_hub_universe(session)` — reads `HubUniverse`, rebuilt daily by 30-day avg turnover.
7. **`build_master_context()`** (engine/intelligence_hub.py:725-789) — sequentially builds macro (VIX/FII-DII), news, earnings, options (gated `ENABLE_HUB_OPTIONS`), portfolio, event, mutual-fund-flow, and sector context. A synchronous Tavily call is explicitly commented out here with a stated "OFFLINE SCORING ENGINE POLICY... deterministic replay" rationale (intelligence_hub.py:743-746) — **but see step 10, which violates that policy within the same cycle.** Results published to module globals `LAST_MACRO_CONTEXT`/`LAST_NEWS_CONTEXT`/`LAST_EARNINGS_CONTEXT`/`LAST_BUILT_AT` (:786-790).
8. **`score_universe()`** (intelligence_hub.py:1414) — per symbol, fetches candles with a timeframe fallback chain, calls **`_score_symbol_sync()`** (:932-1398, **466 lines, flagged >80**) to compute the composite `master_score` from technical/news/sector/macro/earnings/fundamental/options sub-scores, plus an intraday overlay adjustment.
9. **`persist_scores()`** (intelligence_hub.py:1586-1602) — writes one `MasterIntelligenceScore` row per symbol per cycle: all sub-scores, `master_score`, `rank`, `signal`, `regime`, full `reasoning` JSON, `is_blocked`/`blocked_reason`. This is the closest thing in the system to an immutable per-decision feature snapshot (see §11).
10. **Research gate** (best-effort, non-fatal): `run_research_gate_for_history()` runs **live Tavily/web research** for the top 15 BUY signals, then `persist_daily_history()` writes `hub_daily_history` — described in-code as "the historical replay source for backtest." Wrapped in try/except; failure here does not abort the cycle.
11. Top 5 buy/sell lists built (excluding blocked symbols).
12. **If market hours** — fresh `AgentExecutionManager`, `StrategySelectorAgent`, `DecisionEngine`, `RiskManagerAgent` instances (NOT the module-level singletons `agent_loop.py` uses — see §10):
    a. `check_and_close_positions()` — exit-check pass on open positions.
    b. Sector-bearish forced exit: if a position's sector mood is `STRONGLY_BEARISH`, force-close at current price.
    c. Per candidate (max 10 tried, capped by `AGENT_MAX_NEW_ENTRIES_DAY`): skip if blocked/not-BUY-or-SELL/SELL-without-shorting-enabled/SELL-during-STRONG_BULL; re-fetch candles (<20 bars → skip); compute `TechnicalAnalyzer` features, bridge in the Hub's `master_score`; **BUY** → `StrategySelectorAgent.propose()`; **SELL** → `HubShortStrategy.evaluate()`; no candidate → skip.
    d. **Live-price divergence guard**: if live LTP diverges ≥5% from the candidate's entry (candle-derived) → skip. This is the fix for the 2026-07-08 TBZ stale-close incident.
    e. **`DecisionEngine.fuse()`** — position sizing via `capital_utilization_size()` (uses live India VIX); `qty<=0` → reject; opposing-view bear-case appended (non-blocking); hard conflict check against hub context; **multiplicative confidence formula** `final_confidence = signal_strength × regime_factor × news_factor × earnings_factor × fii_factor × 100` (reading the step-7 globals); reject if below `AGENT_CONFIDENCE_THRESHOLD`.
    f. **`apply_reasoning_gate()`** — opt-in LLM veto, fail-open; `None` → shadow-skip, continue to next candidate.
    g. **`RiskManagerAgent.can_take_trade()`** — full risk chain: daily/weekly/monthly drawdown breakers, consecutive-loss lockout, per-trade/portfolio risk caps, cash-buffer floor, dedup, correlation-cluster guard (>0.70), sector-exposure cap (20% default). Reject → risk_veto.
    h. **`executor.execute()`** → `_paper_execute()` (default) or `_live_execute()`: idempotency check against `OpenPosition`, `VirtualWallet.deduct_margin()`, writes `PaperTrade`, `OpenPosition`, `AgentDecision` (with `master_score`+`confidence_factors` persisted), `AgentTrade`, commit, ticker-subscribe.
13. MF scoring (best-effort, non-fatal side branch).
14. `HubCycleLog` finalized: `cycle_end`, `symbols_scored`, `top_buys/sells`, `decisions_made`, `skipped_count`, `status="complete"`.
15. Redis pub/sub broadcast to `hub_events` (bare `except: pass` on failure — intentional non-fatal).
16. On any uncaught exception: `cycle_log.status="error"`, truncated error message committed (nested try/except swallows a second failure silently).

**Terminus:** SKIP at any gate above, or a paper order logged (`PaperTrade`+`OpenPosition`+`AgentDecision`+`AgentTrade`), or (rarely, if paper-mode flags are off) a real order via `_live_execute` → `engine/zerodha_executor.py`.

**Architectural note verified by this report's author** (`grep -rn "route_decision" autotrade-backend`): `engine/decision_router.py`'s docstring states *"Every trading decision in the system funnels through `route_decision()`"* — but `route_decision()` is defined at decision_router.py:119 and has **zero callers anywhere in the codebase** outside its own docstring. Only a different function from the same file, `resolve_mode`, is imported once (by `api/settings.py:145`). The module is not on the live decision path at all — Flow A above never touches it.

### Flow B — `india_trade_loop` (independent, scheduled every 60s)

Entry: `_india_trade_loop()`, tasks/india_tasks.py:512.

1. Gate: `_is_india_trading_window()`.
2. `fetch_live_snapshot()` hot-patches `PRICE_CACHE`/`SECTOR_CACHE`.
3. `update_positions_with_current_prices()` (paper_trading/trade_simulator.py) — closes SL/TP hits, Telegram exit alerts.
4. `llm_dynamic_sl_tp()` (engine/agent/dynamic_management.py) — LLM SL/TP adjustment; **wrapped in a bare try/except that only logs — failure here is silently swallowed and the loop proceeds** (india_tasks.py:571-575).
5. Halt gate: `VirtualWallet.check_drawdown_breakers()`, shock cooldown, 15:20 IST cutoff — any trips → exits-only.
6. **Candidate sourcing**: queries `MasterIntelligenceScore` directly (latest per symbol, ≤45 min old, not blocked, signal in BUY/STRONG_BUY/SELL/STRONG_SELL). The in-code comment claims this reads "market_shortlist — the SINGLE source of truth" — **verified false**; it queries `MasterIntelligenceScore`, never `MarketShortlist`.
7. Filter: confidence ≥ `PAPER_CONFIDENCE_THRESHOLD` (20.0 default); SELL requires ≥50 (hardcoded).
8. Entry price: `PRICE_CACHE` → Kite REST LTP → freshest candle if ≤90 min old, else skip (phantom-fill guard).
9. Portfolio caps: `max_single_stock_weight`/`max_sector_weight` via `engine/portfolio_analytics.py`.
10. Phase-9 technical gates (regime/RS/EMA-slope/pullback pattern), `validate_signal()` (engine/risk_manager.py — the *top-level*, non-agent risk manager), every accept/reject logged via `SimLogger`.
11. **LLM reasoning gate** — **fail-closed** here (unlike Flow A's fail-open shadow-skip): any exception rejects the candidate outright, with an explicit comment citing an 8-trade unreviewed-entry incident from a swallowed `AttributeError` on 2026-07-14.
12. Sizing (`calculate_position_size()`) → `open_paper_trade()` — writes `PaperTrade`/`OpenPosition`. Product = `MIS` for SELL, `CNC` for BUY.
13. Telegram notification.
14. If `ENABLE_OPTIONS`: `evaluate_index_options()` runs after equity pass, against the now-reduced wallet balance.

**Terminus:** SKIP at any gate, or paper trade logged. **This task never calls `engine/zerodha_executor.py`** — it is paper-only regardless of global paper-mode flags.

### Flow C — Universe-widening feeders (breakout/momentum discovery)

`breakout_discovery`/`momentum_discovery` scan the full NSE universe for price/volume breakouts or 30-day momentum, and write qualifying symbols into `hub_universe`/`user_watchlist`. They do not score or trade — they only affect what Flow A and Flow B see in their next cycle. Confirmed not redundant with Hub scoring (different purpose: candidate discovery vs. scoring).

### Flow D — News → sentiment → narrative (feeds Flow A/B's context, does not itself trade)

1. `run_news_crawl()` (crawler/news_crawler.py:1069, 241 lines) — 7-source fan-out, URL-dedup, FinBERT/keyword sentiment scoring, writes `news_items`.
2. `process_latest_events()` (crawler/event_pipeline.py:9) — LLM-classifies the 10 latest unlinked news items into `causal_events` (has a real FK to `news_items`, but **is never read by any scoring code** — see §11).
3. `get_market_sentiment()` (crawler/news_crawler.py:1314) — the actual read path Flow A/B's news context consumes: a flat, unweighted mean of the last 10 scored headlines per symbol. **No time-decay.**
4. `refresh_narrative_cache()` (engine/narrative_engine.py:301) — RSS+Telegram scrape → keyword scoring → LLM sector-boost decode → a hardcoded **"Fake News Trap" override that discards the computed 0-25 boost and replaces it with a flat 40** once ≥2 keyword-source corroboration is hit (narrative_engine.py:341-342). Result lives only in an in-memory module dict, no DB persistence.

**Terminus of Flow D:** sentiment/narrative scores land in DB (`news_items`) or in-memory cache, consumed by Flow A's `build_master_context()`/`build_news_context()` — Flow D itself never places a trade.

### Flow E — Manual/API-triggered decision path (not on any schedule)

`POST /api/v1/agent/cycle/trigger` (api/agent.py:118, auth-gated) → `run_agent_cycle()` (engine/agent/agent_loop.py:171) — the **legacy full-cycle orchestrator**, whose own docstring claims 15-min Celery-beat scheduling but is confirmed absent from `beat_schedule`. Uses `agent_loop.py`'s module-level singleton instances (`_selector`, `_decision`, `_executor`, etc. — distinct from Flow A's fresh-instance-per-cycle pattern) and runs its own independent candidate loop, ending in the same `paper_trading`/`zerodha_executor` writes as Flow A. **A third, independently-instantiated decision-making code path exists alongside Flows A and B**, reachable only by manual trigger or in tests.

### Flow F — F&O sub-flow (all gated OFF by default)

Entered from `engine/agent/agent_loop.py:335-359` (agent_loop path only) and `tasks/india_tasks.py:1272-1280` (`india_equity_options_enrich`): `evaluate_index_options()`/`evaluate_index_futures()`/`evaluate_portfolio_hedge()` (engine/fno/selection.py, futures.py) → sizing/margin-fit → `open_spread_paper_trade()`/`open_future_paper_trade()` → `VirtualWallet.deduct_margin()`. `fno_expiry_sweep` cash-settles at expiry. All master flags (`ENABLE_FNO`, `ENABLE_OPTIONS`, `ENABLE_FUTURES`, `FNO_HEDGE_ENABLED`) default `False`.

---

## 4. Function Inventory by Module

*(Consolidated from all research passes; full per-function detail for the highest-traffic files below. Every function listed was read in its body, not inferred from its name, except where explicitly marked "signature-level only.")*

### `tasks/india_tasks.py` (3973 lines — the busiest file in the repo)

| Function | Line | Behavior | Side effects | Callers | Flags |
|---|---|---|---|---|---|
| `run_master_intelligence_cycle` | 2850 | Full Hub cycle orchestrator | DB writes across 7 tables, Redis publish | Celery beat only | ~350 lines; multiple bare/broad `except: pass` (~3138, ~3191, ~3202) |
| `_india_trade_loop` | 512 | Full 60s paper-entry cycle | DB writes, Telegram, Kite/yfinance calls | `india_trade_loop()` | ≈780 lines, **>80**; inline comments only, no docstring |
| `_fast_sl_check` | 1341 | Exit-only SL/TP sweep, cross-process-safe (re-fetches live price rather than trusting `PRICE_CACHE`) | Closes trades, Telegram | `fast_sl_check()` | ≈180 lines, **>80** |
| `_intraday_entry_task` | 1695 | MIS entry, independently reimplements `_india_trade_loop`'s logic | DB writes, Telegram, Tavily/LLM | `intraday_entry()` | ≈370 lines, **>80**, has docstring |
| `_intraday_squareoff_task` | 2153 | EOD MIS close, per-position SAVEPOINT isolation (fix for a 2026-07-03 cascading-deadlock incident) | DB writes, Telegram | `intraday_squareoff()` | ≈80 lines, has docstring |
| `_run_breakout_discovery` / `_run_momentum_discovery` | 218 / 262 | Wrap `engine.breakout_screener`/`engine.momentum_screener` | DB writes | Celery beat | have docstrings |
| `_purge_old_news` | 3549 | Raw-SQL delete of old `news_items` | DB delete | `purge_old_news_task` | No FK-cascade visible for `causal_events.news_id` |
| `_is_india_trading_window` | 29 | IST weekday + hours check | none | multiple tasks | no docstring |

### `engine/intelligence_hub.py`

| Function | Line | Behavior | Side effects | Callers | Flags |
|---|---|---|---|---|---|
| `build_master_context` | 725 | Aggregates macro/news/earnings/options/portfolio/event/MF/sector context | Mutates `LAST_MACRO_CONTEXT` etc. globals | `run_master_intelligence_cycle` | typed, ~65 lines |
| `build_sector_context` | 306 | Reads `SECTOR_CACHE` → mood/bias per sector | none (read-only) | `build_master_context` | sync, no DB |
| `_score_symbol_sync` | 932-1398 | Computes composite `master_score` | none (pure compute) | `score_universe` | **466 lines — flagged >80** |
| `score_universe` | 1414 | Per-symbol candle fetch (TF fallback) + scoring dispatch | DB reads only | `run_master_intelligence_cycle` | typed |
| `persist_scores` | 1586 | Writes `MasterIntelligenceScore` rows | DB write+commit | `run_master_intelligence_cycle` | — |
| `run_research_gate_for_history` | 1605 | Live web research for top-15 BUYs | **External API call inside the nominally-"offline" cycle** | `run_master_intelligence_cycle` | contradicts the offline-scoring policy stated at :743-746 |
| `persist_daily_history` | 1646 | Writes `hub_daily_history` (backtest/replay source) | DB write | `run_master_intelligence_cycle` | — |

### `engine/agent/` (primary flow functions)

| Function | Line | Behavior | Side effects | Callers | Flags |
|---|---|---|---|---|---|
| `DecisionEngine.fuse` | decision_engine.py:724 | Sizing + multiplicative confidence + decision object | Reads `hub.LAST_*` globals | Flow A, Flow E | ~160 lines, typed |
| `apply_reasoning_gate` | decision_engine.py:581 | Opt-in LLM veto, fail-open (Flow A) / fail-closed (Flow B) depending on caller | LLM API call | Flow A, Flow B, Flow E | not fully read — LLM tool-use functions (:62-581) not traced |
| `RiskManagerAgent.can_take_trade` | risk_manager.py:104 | Full risk-gate chain | Reads `PRICE_CACHE` global | Flow A, Flow E | typed, long but decomposed |
| `capital_utilization_size` | risk_manager.py:37 | Conviction-weighted position sizing | none | `fuse`, `can_take_trade` | — |
| `AgentExecutionManager.execute`/`_paper_execute` | execution.py:19/27 | Dispatch + paper order write path | Multi-table DB write, wallet deduction, ticker subscribe | Flow A, Flow E | `_paper_execute` ~145 lines |
| `StrategySelectorAgent.propose` | selector.py:32 | Runs strategy set, returns best candidate | delegates to `strategies/*` (not read in depth) | Flow A, Flow E | — |
| `run_agent_cycle` | agent_loop.py:171 | Legacy full per-bar cycle, own candidate loop | Same DB tables as Flow A | api/agent.py:131 (manual, admin-only), tests only | Docstring claims Celery-beat scheduling — **false** |

### `paper_trading/trade_simulator.py` (canonical execution-write path)

| Function | Line | Behavior | Side effects | Callers | Flags |
|---|---|---|---|---|---|
| `estimate_trade_cost` | 44 | Real NSE delivery-cost model (brokerage/STT/exchange/SEBI/stamp/GST) | none (pure) | `close_paper_trade` | — |
| `open_paper_trade` | 140 | Opens virtual position with slippage + hard guards | writes `PaperTrade`, `OpenPosition`; calls wallet | Flow A, Flow B, Flow E, F&O selection, `news_discovery_engine.py` | — |
| `close_paper_trade` | 365 | Closes position, books P&L incl. real costs, MFE/MAE tracking | writes `PaperTrade`, deletes `OpenPosition`, wallet, AgentTrade sync (bare-except-swallowed on failure), reflection call (also swallowed) | `update_positions_with_current_prices`, shock_guard, Flow E, `scripts/reconcile_agent_ledger.py` | — |
| `update_positions_with_current_prices` | 599 | Mark-to-market + SL/TP/trailing/sector-exit/stale-exit loop | mutates `OpenPosition`, `PaperTrade`, wallet | `tasks/india_tasks.py` only | **171 lines — flagged >80** |
| `compute_live_pnl` | 537 | On-demand live P&L for read endpoints | none (read-only) | read-side API endpoints | — |

### `paper_trading/position_tracker.py` — largely dead

| Function | Line | Status |
|---|---|---|
| `PositionTracker.open_position` | 26 | **UNCALLED** — fully superseded by `trade_simulator.open_paper_trade` |
| `PositionTracker.close_position` | 111 | Only caller: `api/trades.py:154` manual close route — a **second, cost-unaware** close implementation (no brokerage/STT/GST accounted, unlike the canonical path) |
| `PositionTracker.check_sl_tp` | 176 | **UNCALLED anywhere** — dead |

### `engine/zerodha_executor.py`

| Function | Line | Behavior | Callers | Flags |
|---|---|---|---|---|
| `place_real_order` | 300 | 10-rule-gated real order (paper flags, confidence floor, 5% cash cap, market-hours, daily-loss breaker, max-5-positions, 3s abort window, LIMIT-only, tagging) | `decision_router.py:180` (itself unreached — see above; effectively means this call site is also dormant unless something else invokes `route_decision`), `agent/execution.py:203` | **134 lines — flagged >80** |
| `execute_real_buy` / `execute_real_sell` | 96 / 175 | Real order via live-margin sizing | **UNCALLED** anywhere | dead code |
| `place_gtt_with_oco` | 466 | Real OCO GTT bracket order | `api/zerodha.py:1374` only | — |

### `engine/fno/adjustments.py`

| Function | Line | Status |
|---|---|---|
| `trail_stop_loss_atr` | 7 | **UNCALLED anywhere** — and would **crash if called**: it filters `OpenPosition.status == "OPEN"`, but the `OpenPosition` table has **no `status` column** (row existence itself denotes "open"; rows are deleted on close, never status-flagged) |

### `tasks/ml_optimizer.py` — dead scaffold with a fabricated training signal

| Function | Line | Behavior | Status |
|---|---|---|---|
| `fetch_historical_dataset` | 8 | Pulls real `MasterIntelligenceScore` features, but the forward-return target `Y` is **`mock_return = (features[0]*0.4 + features[1]*0.4 + np.random.normal(0,10))/100.0`** (line 41-42) — an explicit in-code comment calls this a "MOCK TARGET" | UNCALLED in production |
| `optimize_strategy_weights` | 63-83 | SLSQP optimizer against the mocked target — would trivially just re-discover the mock formula's own 0.4/0.4 weighting | UNCALLED |
| `run_optimization_pipeline` | 88 | CLI entrypoint; its own closing print says *"Next Step: Automatically updating intelligence_hub.py weights..."* — the write-back was never built | **UNREACHABLE** from any scheduled/API path; manual `python tasks/ml_optimizer.py` only |

### `crawler/news_crawler.py` (1382 lines, fully read)

Key functions: `run_news_crawl` (1069, 241 lines, **>80**), `SentimentAnalyser.analyse`/`analyse_batch` (792/846), `get_market_sentiment` (1314, the actual read-path), `extract_tickers_from_headline` (686), `_build_india_name_map` (520, 67 lines, 6h TTL cache). `fetch_nse_corporate_announcements` (338) and `fetch_sse_announcements` (399) are defined but **not wired into `run_news_crawl`'s gather list** — likely called elsewhere (`sync_sse_announcements` task, unconfirmed) but not from the main crawl orchestrator.

### `crawler/india_price_feed.py` (721 lines, fully read)

`is_nse_market_open()` (85) — the canonical market-hours check, reused across most of the codebase (see §8 for the two inconsistent duplicates). `run_india_price_crawl()` (541, 180 lines, **>80**) has a **fully silent** `except Exception: pass` at lines 669-670 for the 5-minute-candle fetch loop — no log line at all, unlike its 1-hour sibling three lines later which does log. This is a genuine silent-failure defect: a systemic 5m-candle outage would leave zero diagnostic trace.

### `crawler/*` remaining files — signature-level inventory

| File | Lines | Key functions | DB table(s) | Notes |
|---|---|---|---|---|
| bhavcopy_fno.py | 340 | `fetch_fno_bhavcopy`:289, `_download_zip`:261 (retry=3) | none directly | |
| corporate_actions.py | 307 | `adjust_open_positions`:100, `check_and_handle_corporate_actions`:183 | `PaperTrade`, `OpenPosition`, `Candle` | |
| equity_options.py | 263 | `enrich_equity_options`:180, `_build_chain_via_kite`:89 | `KiteInstrument`, `OptionsChainSnapshot` | gated `ENABLE_HUB_OPTIONS` |
| event_pipeline.py | 63 | `process_latest_events`:9, `run_pipeline`:58 | `NewsItem`, `CausalEvent` | `run_pipeline` standalone entry not confirmed scheduled |
| exchange_crawler.py | 87 | `fetch_bulk_deals`:14, `fetch_block_deals`:52 | none directly | feeds news_crawler gather |
| ipo_crawler.py | 436 | `refresh_ipo_cache`:167, `enrich_ipo_data`:293 | **none — in-memory `IPO_CACHE` only, does not survive worker restart** | |
| live_snapshot.py | 206 | `fetch_live_snapshot`:92 | — | |
| macro_crawler.py | 162 | RBI/PIB/SEBI fetchers:18,76,112 | none | feeds news_crawler gather |
| media_crawler.py | 60 | `fetch_financial_media`:11 | none | |
| pdf_parser.py | 104 | `download_and_parse_pdf`:11, `analyze_announcement_llm`:56 (LLM), `process_nse_announcement`:91 | — | LLM in ingestion path, malformed-JSON handling not verified |
| sector_data.py | 296 | `compute_sector_from_cache`:144 (cache-only, "never fails"), `refresh_sector_data`:262 | **none — in-memory `SECTOR_CACHE` only** | |
| sentiment.py | 77 | `SentimentAnalyser` class:37 | n/a | **second, apparently-unimported sentiment implementation — see §8** |
| upstox_auth.py | 46 | `generate_and_save_upstox_token`:11 | writes `.env`, not DB | |
| upstox_data.py | 443 | fundamentals wrappers:208-256 | none | bare `except: pass` at line 109 |
| zerodha_client.py | 382 | `KiteClient` class:29, `get_kite_client`:324 | none | bare `except: pass` at line 339 |
| zerodha_historical.py | 323 | `sync_full_nse_universe`:169, `sync_live_1m_candles`:266 | `Candle` | |
| zerodha_instruments.py | 180 | `refresh_instrument_cache`:147 | `KiteInstrument` (inferred) | |
| zerodha_ticker.py | 367 | `on_ticks`:118, `start_kite_ticker`:312 | `SimulationLog`; module-level `LIVE_TICKS` mutated from WS callback thread | |
| zerodha_websocket.py | 189 | `start_kite_websocket`:121 | none | possible duplicate of zerodha_ticker.py's WS client |
| zerodha_market.py | 697 | `sync_nse_eq_instruments`:271, `get_live_prices`:443, `sync_kite_candles_to_db`:636 | `KiteInstrument` | |
| zerodha_kite_lib.py | 694 | ~65 thin `pykiteconnect` wrappers | none directly | `place_order`:176, `place_gtt_single/oco`:300/323 — the actual broker SDK adapter |

### News/narrative subsystem — key functions

| Function | Line | Behavior | Callers | Flags |
|---|---|---|---|---|
| `process_latest_events` | crawler/event_pipeline.py:9 | LLM-classifies latest 10 unlinked news items → `CausalEvent` | `run_news_crawl` | per-item try/except, retried naturally next cycle (no placeholder row on failure) |
| `classify_event` | engine/event_classifier.py:19 | One LLM call → `EventClassification` | `process_latest_events`; dead `engine/news_discovery_engine.py` | `try/except Exception → log + return None` |
| `refresh_narrative_cache` | engine/narrative_engine.py:301 | RSS+Telegram→keyword→LLM→cache, TTL-gated, "Fake News Trap" override | `narrative_scan._refresh`; `api/news.py:143` (force=True, duplicate trigger path) | mutates module global, no DB |
| `_fetch_telegram_headlines` | engine/narrative_engine.py:153 | Scrapes public `t.me/s/<channel>` HTML | `refresh_narrative_cache` | — |
| `analyze_supply_chain_shock` | engine/agent/unstructured_alpha.py:12 | LLM: headline → affected Indian suppliers | `unstructured_alpha_scan` | result only logged, never persisted (dead end) |
| `detect_sentiment_divergence` | engine/agent/unstructured_alpha.py:51 | LLM: sentiment-vs-price divergence | **UNCALLED** in production | — |
| `fetch_telegram_messages` | extract_telegram.py:6 | Sync scrape of one hardcoded channel, hardcoded date filter | **UNCALLED anywhere** | standalone script, superseded by `narrative_engine._fetch_telegram_headlines` |
| `purge_old_news_task`/`_purge_old_news` | tasks/india_tasks.py:3565/3549 | Raw-SQL delete | Celery beat | see FK-risk note above |

### API layer — see §9 for the complete route table (all 26 routers). Notable individual functions:

- `main.py:lifespan()` (21-192) — boot-time safety-bounds check: refuses to start if `MAX_RISK_PER_TRADE > 0.05`, `MAX_PORTFOLIO_RISK > 0.50`, or `RISK_PER_TRADE_MIN/MAX > 0.05` (34-60) — a genuine hard-coded safety rail enforced in code, not just documented.
- `main.py:init_db()` retry loop (63-74) — retries 5×, but **continues booting even if all attempts fail** ("will retry on first request").

---

## 5. Data Sources & External Dependencies

| Source | Key file:line | Sync/Async | Failure behavior (cited) | Rate limits observed | Live scoring path? |
|---|---|---|---|---|---|
| NewsAPI `/v2/everything` | news_crawler.py:134 | async | `except Exception` → `[]` (176-178); also `[]` if key unset | none | No |
| Finnhub `/news` | news_crawler.py:181 | async | `[]` on error (216-218) | none | No |
| NewsData.io | news_crawler.py:224 | async | `[]` on error (257-259) | none enforced | No |
| Free RSS feeds | news_crawler.py:262 | sync feedparser via executor | per-feed `[]` on error, others unaffected | none | No |
| yfinance news | news_crawler.py:1030 | sync in thread executor | per-symbol debug-log + skip | `_YF_MAX_SYMBOLS=60`, `_YF_MAX_PER_SYMBOL=8` | No |
| RBI/PIB/SEBI, NSE bulk/block deals, financial media | macro_crawler.py, exchange_crawler.py, media_crawler.py | async | caught at the `asyncio.gather(..., return_exceptions=True)` level in `run_news_crawl` | unknown per-source | No |
| NSE corporate announcements / SSE | news_crawler.py:338/399 | async, cookie warm-up | non-200/exception → `[]`, logged | `sleep(1.5)` | No |
| NSE holiday master | utils/nse_market_status.py:11/53 | both | falls back to stale cache, then local file | 24h cache | **Yes** — gates `is_nse_market_open()` |
| yfinance OHLCV | india_price_feed.py:156, price_feed.py:93 | sync via executor + 20s `wait_for` | `[]`/log on error; timeout appended to errors list | `Semaphore(15)`, 20s/symbol timeout | **Yes** — direct Hub input |
| Alpha Vantage | price_feed.py:191 | async | exponential backoff via module-level `asyncio.Lock` | real backoff logic (rare in this codebase) | Fallback only |
| Zerodha Kite (REST+WS) | zerodha_kite_lib.py, zerodha_market.py, zerodha_websocket.py | mixed | dedicated 403 handler (`zerodha_market.py:36`); WS backoff max 5 retries, `2**retry` capped 60s, then **gives up entirely**, no auto-restart observed | WS backoff; 3 historical-fetch sleeps | **Yes** — primary price/order path |
| Upstox (REST) | upstox_auth.py, upstox_data.py | mixed | bare `except: pass` at upstox_data.py:109 | TTL cache | Secondary/fallback (not fully confirmed) |
| NSE options chain | options_chain.py:168/214 | mixed, sync fetch inside async wrapper | not fully verified — two `time.sleep(1.0)` calls that **may block the event loop if not run in an executor** (unconfirmed without full read) | 2×sleep(1.0) | Yes — feeds options scoring |
| NSE FII/DII flow | fii_dii_crawler.py:260 | async | inferred fallback to last DB row | `sleep(2)` retries | No (daily cron) |
| NSE bhavcopy F&O | bhavcopy_fno.py:261 | async | `retries=3`, `1.5×attempt` backoff, `None` on exhaustion | 3 retries | No (EOD) |
| AMFI NAV / BSE / NSDL FPI | india_price_feed.py:324/435/475 | sync | zeroed dict on failure, never raises | none | No |
| IPO data | ipo_crawler.py:49/127 | async | **stale-over-empty policy** — keeps old cache on 0 results | sleeps 1.0-1.5s | No |
| Earnings transcripts (PDF) | earnings_crawler.py, pdf_parser.py | async | not fully read | `sleep(1.5)` | No |
| Market breadth / sector rotation | market_breadth.py, sector_data.py | **cache-only, not external on the hot path** | "always succeeds" per docstring | 1s sleeps (raw fetchers only) | Cache-only, in-process |

**Silent-failure defect confirmed**: `india_price_feed.py:669-670` — 5-minute candle fetch loop, fully silent `except Exception: pass`, zero logging, unlike its 1h sibling three lines later.

---

## 6. Database Schema Reality Check

**Schema bootstrap is not Alembic-driven.** `db/migrations/versions/` contains 5 versioned files, but a repo-wide grep for `alembic upgrade`/`command.upgrade` returns zero matches — nothing invokes them at runtime. The actual live schema is created by `db/database.py:init_db()` (`main.py` lifespan) via `Base.metadata.create_all()` (database.py:87) plus ~30 hardcoded idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements (database.py:93-149, each retried 3× on deadlock). **`models.py` + this ALTER list is the authoritative live schema; `migrations/versions/` is vestigial.**

Session management: both the FastAPI (`get_db()`, database.py:40-61) and Celery (`tasks/_db.py:33-50`) paths use `NullPool` + `statement_cache_size=0`, required for Supabase's PgBouncer transaction-mode pooler (a prior `MissingGreenlet` incident is cited in comments). Celery creates a **fresh engine per task invocation**, disposed in `finally` — no shared/global session risk across workers by construction.

### Confirmed dead/broken columns and tables

- **`master_events` (`MasterEvent`, models.py:1112-1137) — the entire table is dead.** Its own docstring calls it *"Persistent Event Store: a unified, deduplicated market event"* — exactly the "event store" design intent asked about in §11 — but a repo-wide grep finds **zero production code that ever inserts, queries, or updates a `MasterEvent` row**. The only other reference is a one-off `migrate_events.py` script that just prints a creation message and manually ALTERs a related column. Consequently `agent_trades.event_id` (models.py:1150, FK to `master_events.event_id`) is **always NULL** — nothing ever sets it.
- **`paper_trades.holding_bars`** (models.py:121) — written nowhere, read nowhere. Always `NULL`.
- **`paper_trades.mfe_pct` / `mae_pct`** (models.py:125/128) — write-only: computed in `trade_simulator.py:451-452`, but `api/attribution.py` (the one consumer of the sibling excursion fields) never reads these two specifically. Computed, stored, never surfaced.
- `paper_trades.mfe_abs`, `mfe_r`, `mae_abs`, `mae_r`, `max_open_profit`, `r_multiple`, `holding_hours`, `entry_reason`, `regime_at_exit`, `confidence_bucket`, `instrument_segment`, `regime_at_entry`, `strategy_name` — **confirmed properly wired** (written at open/close, read by `api/attribution.py` and Telegram journal summary).
- `agent_decisions` — confirmed fully wired end-to-end (`master_score`, `confidence_factors`, `macro_bias`, `fund_score`, `skip_reason`, `order_id` all written at 3 construction sites and read by `GET /agent/decisions`).
- `portfolio_policy`, `virtual_wallet` — confirmed fully wired.
- `news_items.category`/`.company` — confirmed written (NSE-announcement rows) and displayed.
- `hub_universe`, `hub_cycle_logs`, `master_intelligence_scores`, `hub_daily_history`, `fundamental_data` — confirmed live end-to-end via the Flow A trace in §3, not independently column-audited beyond that.

**Not exhaustively column-audited**: the remaining ~30 of 45 tables (time-boxed out of this pass). Treat as unverified, not "clean."

### Schema/Pydantic mismatch

`api/schemas.py`'s `PaperTradeOut` (schemas.py:75-94) — the response model backing the primary `/api/v1/trades` routes — exposes only ~14 original core fields. It **omits every F&O field** (`instrument_type`, `strike_price`, `option_type`, `expiry_date`, `lot_size`, `margin_blocked`, etc.) and **every Phase-1 attribution/excursion field** added 2026-06-17 (`product`, `strategy_name`, `regime_at_entry/exit`, `r_multiple`, `mfe_*`, `mae_*`, etc.) that exist on the ORM model. Callers of `GET /api/v1/trades` silently lose that data; `api/attribution.py` works around this by building response dicts manually rather than reusing the schema.

---

## 7. Configuration & Magic Numbers

**Precedence model (verified in code):** `utils/config.py`'s `Settings` (pydantic-settings, `.env`-backed) is the deploy-time default layer. `utils/runtime_config.py`'s `RuntimeConfig.load(session)` reads DB rows (JSON-encoded, keyed by a fixed `_KNOWN_KEYS` allowlist, runtime_config.py:33-82) and each typed property does `self._data.get(key, settings.SOME_FIELD)` — **DB row wins if present, otherwise falls back to the `.env` default.** `api/settings.py` writes exclusively through `RuntimeConfig.set`/`set_many`, consistent with this model. **This means core risk/mode parameters (`paper_mode`, `trading_halted`, risk caps, confidence thresholds, watchlists) can change live via HTTP without a redeploy** — confirmed architecturally real, and (per §9) largely unauthenticated.

One material inconsistency: `ENABLE_FNO`, `ENABLE_OPTIONS`, `ENABLE_FUTURES`, `ENABLE_SHOCK_GUARD`, `INTRADAY_ENABLED`, `ENABLE_HUB_OPTIONS`, `FNO_HEDGE_ENABLED` exist **only** in `config.py` (static `.env`-only) and are **not** in `RuntimeConfig._KNOWN_KEYS` — they cannot be toggled live; only a narrower risk/sizing/mode subset is DB-overridable.

### Current default state of every feature flag (from `utils/config.py`)

| Flag | Default | Line |
|---|---|---|
| `AGENT_ENABLED` | **True** | 197 |
| `AGENT_PAPER_MODE` | **True** | 198 |
| `PAPER_MODE` | **True** | 427 |
| `ZERODHA_ENABLED` | **False** | 184 |
| `ZERODHA_PAPER_MODE` | **True** | 185 |
| `EQUITY_SHORT_ENABLED` | **True** | 205 |
| `ENABLE_FNO` / `ENABLE_OPTIONS` / `ENABLE_FUTURES` | **False / False / False** | 215-217 |
| `FNO_HEDGE_ENABLED` / `FNO_VOL_ENABLED` | **False / False** | 241 / 244 |
| `ENABLE_HUB_OPTIONS` | **False** | 253 |
| `AGENT_LLM_REASONING_ENABLED` | **True** | 283 |
| `AGENT_LLM_DEBATE_ENABLED` | **True** | 288 |
| `AGENT_LLM_TOOLUSE_ENABLED` | **True** | 293 |
| `AGENT_LLM_SHADOW_MODE` | False | 298 |
| `AGENT_LLM_REFLECTION_ENABLED` | **True** | 303 |
| `AGENT_PORTFOLIO_BRAIN_ENABLED` | **True** | 310 |
| `ENABLE_SHOCK_GUARD` | **False** | 365 |
| `INTRADAY_ENABLED` | **False** | 412 |
| `SCANNER_ENABLED` | **False** | 428 |

**Doc/code contradiction found**: `AGENT_LLM_REASONING_ENABLED`, `AGENT_LLM_DEBATE_ENABLED`, `AGENT_LLM_TOOLUSE_ENABLED`, `AGENT_LLM_REFLECTION_ENABLED` are all `True` by default, despite inline comments describing them as *"Default OFF — opt-in until A/B validated"* (config.py:280-282, 287, 299, 302-303). The comments and the shipped defaults disagree.

### Key trading-relevant settings

| Setting | Default | Gates | Line |
|---|---|---|---|
| `PAPER_CONFIDENCE_THRESHOLD` | 20.0 | Flow B entry floor | 192 |
| `LIVE_CONFIDENCE_THRESHOLD` | 70.0 | Real-order floor | 193 |
| `AGENT_EQUITY` | ₹2,500,000 | Sizing base | 199 |
| `AGENT_MAX_RISK_PER_TRADE` | 0.01 | Risk manager | 266 |
| `AGENT_MAX_OPEN_RISK` | 0.15 | Portfolio open-risk cap | 270 |
| `AGENT_MAX_POSITIONS` | 15 | Hard concurrent cap | 271 |
| `AGENT_DAILY_DD_STOP`/`WEEKLY`/`MONTHLY` | 0.03/0.05/0.10 | Drawdown breakers | 272-274 |
| `AGENT_CASH_BUFFER_MIN` | 0.20 | Cash floor | 275 |
| `AGENT_CONSEC_LOSS_LOCKOUT` | 2 | Loss lockout | 277 |
| `AGENT_MAX_SECTOR_EXPOSURE` | 0.20 | Sector cap | 320 |
| `AGENT_MAX_POSITION_WEIGHT` | 0.05 | Single-stock cap | 330 |
| `HUB_UNIVERSE_SIZE` | 2000 | Symbols scored/cycle | 351 |
| `AGENT_TIMEFRAME` | "1d" | Scoring candle basis — comment cites a prior incident where 5m candles became an "unvalidated scalping basis," now pinned to 1d | 397 |
| `INTRADAY_MAX_TRADES_PER_DAY` | 3 | MIS daily cap | 413 |
| `MAX_PORTFOLIO_RISK` | 0.15 | Sum of open-position risk | 434 |
| `MAX_OPEN_POSITIONS` | 20 | Comment: *"SAFETY CEILING (bug guard), not the primary limiter"* (`AGENT_MAX_POSITIONS`=15 is the real cap) | 425 |
| `FNO_SPAN_PCT_INDEX`/`FNO_EXPOSURE_PCT`/`FNO_MARGIN_BUFFER` | 0.12/0.03/0.20 | F&O paper-margin model, explicitly documented as NOT exchange-exact SPAN | 236-238 |

### Notable hardcoded-not-configurable values (spot-checked across forks)

SELL/short min confidence `50` (india_tasks.py:724), entry-price freshness cutoff `90 min` (india_tasks.py:759), live-price divergence guard `5%` (india_tasks.py:3084), max candidates/cycle `10` (india_tasks.py:2999), correlation-cluster threshold `0.70` (risk_manager.py:163), confidence-factor clamps `0.5–1.5`/`0.6–1.4` (decision_engine.py:800/806/812), 5% max-real-order-value cap **duplicated 3×** independently (zerodha_executor.py:70,125,382), 3-second human-abort window (zerodha_executor.py:84), 45-day/-2% stale-loser exit (trade_simulator.py:884/891), "Fake News Trap" flat-40 override (narrative_engine.py:342).

All `Settings` fields are pydantic-settings-backed, meaning every one is technically env-overridable — the hardcoded values flagged above are hardcoded because they're inline literals in engine/task code, never promoted into a `Settings` field, not because `config.py` lacks a mechanism for them.

---

## 8. Duplicate / Redundant Logic

1. **`decision_router.py` is entirely bypassed** — its docstring claims to be the mandatory routing chokepoint for every trade; verified zero callers of `route_decision()` anywhere. All three real trade-opening pipelines (Flow A, B, E) implement their own paper/live dispatch inline via `AgentExecutionManager`/`zerodha_executor` instead.
2. **Three independent code paths can open a position**: Flow A (Hub cycle, fresh instances), Flow B (`india_trade_loop`, independent candidate sourcing), Flow E (`agent_loop.run_agent_cycle`, manual-trigger-only, module-level singletons). They share some library functions (`DecisionEngine.fuse`, `RiskManagerAgent.can_take_trade`) but not others (`decision_router` unused by all three; `agent_loop`'s singletons vs. Flow A's fresh instances).
3. **Market-shortlist confusion**: `market_scanner.py`'s own docstring says `india_trade_loop` reads its `market_shortlist` output; `india_trade_loop`'s actual query targets `MasterIntelligenceScore` directly — confirmed by grep (`MarketShortlist` returns zero matches in `india_tasks.py`). `MarketShortlist` is written by `market_scanner.py` but read only by UI/display code, `india_price_feed.py`, and `india_signal_generator.py` — not by the scheduled trade-execution loop.
4. **"Is market open" checked at least 3 different ways**: canonical `crawler/india_price_feed.py:85 is_nse_market_open()` (weekday + holiday-set + configurable hours, reused across most callers); an inline duplicate in `crawler/live_prices.py:229` with no visible holiday-calendar check; another inline duplicate in `tasks/india_tasks.py:2751` that checks weekday but **not** holidays. A trading holiday would be correctly excluded by the canonical check but not by the two inline duplicates — a real correctness risk for anything using the latter two.
5. **Two named "risk manager" classes**: `engine/risk_manager.py` (top-level, used by `news_discovery_engine.py`, `Flow B`'s `validate_signal()`, and `engine/agent/backtester.py`) vs. `engine/agent/risk_manager.py` (`RiskManagerAgent`, the one actually used by Flow A/E's risk-veto gate). Same conceptual role, two unrelated implementations, same class-purpose naming.
6. **Two "sector" concepts with the same vocabulary**: `engine/sector_graph.py` (2nd-order sector graph, only caller is dead `news_discovery_engine.py`) vs. `SECTOR_CACHE`/`build_sector_context()` (crawler/sector_data.py + intelligence_hub.py, actually used by Flow A/B).
7. **Sentiment analysis implemented twice**: `crawler/news_crawler.py:781 SentimentAnalyser` (FinBERT + keyword fallback, actively used) vs. `crawler/sentiment.py:37 SentimentAnalyser` (separate class, same name, no confirmed importer found in `tasks/`/`engine/` — likely dead, not fully confirmed).
8. **`news_discovery_engine.py` exists twice**: root-level (381 lines, imported only by a standalone test script `test_kg.py`) and `engine/news_discovery_engine.py` (96 lines, **zero importers anywhere** — confirmed dead). Neither is wired into the beat schedule or any API router.
9. **RSI/EMA computed independently at least 3 times**: `engine/breakout_screener.py:83/97`, `engine/momentum_screener.py:82/96`, and `tasks/market_scanner.py:265/280` — each with its own near-identical Wilder-RSI/EMA implementation, despite a shared `engine/indicators.py` module existing and being used elsewhere (`market_scanner.py:62` imports `compute_indicators` from it for a different purpose in the same file).
10. **Two close-trade implementations with different cost accounting**: `trade_simulator.close_paper_trade` (transaction-cost-aware, MFE/MAE tracking, AgentTrade sync, reflection hook — the canonical automated path) vs. `PositionTracker.close_position` (none of the above), reachable only via the manual `POST /api/v1/trades/{id}/close` UI action — a manually-closed trade's stored P&L excludes brokerage/STT/GST that an automatically-closed trade would include.
11. **Two real-order safety rule sets**: `zerodha_executor.place_real_order` (10 rules) vs. `api/zerodha.py:POST /orders` (3 checks: paper-mode flag, header, admin auth — no confidence/size/hours/loss-limit gates) — same underlying action, materially different guardrails.
12. **Two independent live-price refresh loops**: Celery's `refresh_live_prices` task (15s) and `main.py:_live_price_loop()` (15s in-process asyncio loop) — both populate/refresh price caches independently. Similarly, `main.py:_breadth_loop()` duplicates the Celery `refresh_market_breadth` task, but this one is **explicitly acknowledged** in a code comment as intentional (each OS process needs its own in-memory copy).
13. **Two independently-named halt mechanisms**: `POST /api/v1/agent/halt`/`resume` (flips `RuntimeConfig.trading_halted`) vs. `POST /api/v1/simulation/pause`/`resume` (semantics not fully traced, likely a separate simulation-only flag) — not reconciled, unclear which one operators are expected to use.
14. **NSE cookie-warm-up boilerplate** repeated near-verbatim across `news_crawler.py:358-364`, `news_crawler.py:412-418`, and (per in-code comment) `earnings_crawler.py`/`fii_dii_crawler.py` — 4+ copies of the same two-step session pattern, never factored into a shared helper.
15. **5% max-real-order-value cap** hardcoded independently 3× in `zerodha_executor.py` (lines 70, 125, 382) rather than a shared constant.
16. **F&O margin-authorization gate inconsistency**: `fno/margin.can_block_margin` (cross-book capital check) gates the futures-open path but is **not** called by the options/spread-open path in `fno/selection.py`, which checks only wallet cash + a flat 5% notional cap.

---

## 9. API Route Inventory & Manual-Override Risk

Full route table is large (26 routers, ~85 routes in `api/india.py` alone); the trading-relevant summary:

### Auth-gated mutating routes (the only 5 in the entire API surface)

`POST /agent/cycle/trigger`, `POST /agent/halt`, `POST /agent/resume`, `POST /settings/mode`, `POST /zerodha/orders` — all behind `require_auth` (single hardcoded admin, bcrypt+JWT, `api/auth.py`).

### Unauthenticated routes that can directly affect trades or risk parameters (ranked by blast radius)

1. **`PATCH /api/v1/settings/`** (settings.py:92) — no auth at all; can silently flip `paper_mode` to `False` (going live) and rewrite every risk cap. **This bypasses the `require_auth` + `"I_UNDERSTAND_REAL_MONEY"` confirmation that the dedicated `POST /settings/mode` route enforces for the identical `paper_mode` key** — two write paths to the same field, only one protected.
2. **`POST /api/v1/agent/kill-switch`** (agent.py:399) — flattens the entire book; guarded only by a static header string (`X-Kill-Confirm: FLATTEN`), no JWT.
3. **`POST /api/v1/agent/positions/{symbol}/close`** (agent.py:480) — closes any position; zero protection.
4. **`POST /api/v1/trades/{trade_id}/close`** (trades.py:136) — closes a trade at a **caller-supplied exit price**, no market-price validation, no auth (also uses the legacy cost-unaware close path, see §8.10).
5. **`PUT /api/v1/agent/config`** (agent.py:577) — toggles `AGENT_ENABLED`/`AGENT_PAPER_MODE`/confidence threshold/max risk per trade; guarded only by a static header string.
6. **`PUT /api/v1/portfolio/capital-model/policy`**, **`POST /reset`**, **`POST /reconcile`** (portfolio.py:304/338/358) — rewrite risk policy / wipe the wallet / force-close all agent trades; `reset`/`reconcile` gated only by a boolean query param.
7. **`PUT /api/v1/zerodha/orders/{id}`**, **`POST /positions/convert`**, GTT place/modify/delete (zerodha.py:1233,1260,1321-1409) — modify/convert a **real** Kite order/position; unauthenticated (only the initial `POST /orders` is protected).
8. **`POST /api/v1/india/user-watchlist/{symbol}`** / DELETE (india.py:2543/2560) — adds/removes symbols from the live scan universe; no auth.
9. **`POST /india/signals/trigger`**, **`POST /intelligence/trigger`**, **`POST /intelligence/rescore/{symbol}`**, **`POST /signals/trigger`** — force off-cycle scoring writes the next Hub/trade cycle may act on; no auth.

The auth pattern reads as reactive (protecting the most obviously dangerous actions) rather than systematic — most mutating endpoints in `india.py`, `portfolio.py`, `intelligence.py`, `trades.py`, and most of `zerodha.py` have none. **The frontend does not wire any button to the highest-risk routes** (kill-switch, position close, agent halt/resume, trade close — confirmed by grepping every `.jsx` file for the corresponding client.js call names, zero matches), so today's exposure is via direct HTTP only, not the SPA — this reduces accidental-click risk but does not close the API-level gap for anyone with host/network access.

Everything else — `api/allocation.py`, `analytics.py`, `attribution.py`, `buyback.py`, `earnings.py`, `ipo_tracker.py`, `mf_tracker.py`, `portfolio_tracker.py`, `sip_tracker.py`, `stock_chat.py`, `tax_calculator.py`, `portfolio_doctor.py`, `upstox.py`, `news.py`, `kite.py` (legacy holdings tracker) — is either fully read-only or scoped to its own bookkeeping tables (tax/SIP/MF/IPO/buyback/portfolio-doctor data), not the trading engine.

### Frontend notes

`client.js` — axios wrapper, JWT from `localStorage`, no hardcoded secrets found; `placeZerodhaOrder` auto-attaches the `X-Confirm-Real-Order: yes` header. **`News.jsx`'s `EventIntelligencePanel`** (lines 61-274) falls back to **three hardcoded fabricated "dummy" events** (RBI rate cut, Israel tensions, US CPI print, lines 80-120) whenever the real causal-events API returns empty, rendered with identical UI chrome to real data (same "Live AI" badge, no "sample data" disclaimer) — a user cannot visually distinguish fabricated example events from genuine AI-derived intelligence when the table is empty (which, per §11, it always will be for anything scoring-relevant, since `causal_events` is never read by scoring code anyway).

---

## 10. Concurrency & State

| Shared state | Location | Written by | Read by | Thread-safety |
|---|---|---|---|---|
| `LAST_MACRO_CONTEXT`/`LAST_NEWS_CONTEXT`/`LAST_EARNINGS_CONTEXT`/`LAST_BUILT_AT` | intelligence_hub.py:786-790 | `build_master_context` (once/cycle) | `DecisionEngine.fuse` mid-cycle | No lock; safe only because the overlap guard (§3 Flow A step 3) is relied upon to prevent concurrent cycles — not an explicit concurrency primitive |
| `PRICE_CACHE` | crawler/live_prices.py | Multiple Celery tasks (`fast_sl_check` every 5s, `refresh_live_prices` every 15s, Flow A/B) + `main.py`'s own `_live_price_loop` in a **separate OS process** | Same set | No lock found; `fast_sl_check` explicitly distrusts it cross-process (comment: "Celery worker processes have a stale copy") and re-fetches from Kite/yfinance directly instead — an intentional, documented workaround for the cross-process gap, but not a fix for intra-process races |
| `SECTOR_CACHE` | crawler/sector_data.py | `refresh_sector_data` task + `live_snapshot.py` hot-patch | `build_sector_context` | Same pattern as `PRICE_CACHE` |
| `BREADTH_CACHE` | crawler/market_breadth.py | Celery `refresh_market_breadth` (one instance) + `main.py:_breadth_loop()` (a **second, independent instance in the API process**) | regime engine, `/breadth` API | **Two independent instances that can disagree** — acknowledged as intentional in a code comment ("Uvicorn needs its own background loop so the API endpoint stays current in this process too"), but this means the API-served breadth value and the Celery-worker-internal value are not guaranteed to match at any given instant |
| `agent_loop.py` module singletons (`_analyzer`, `_selector`, `_fund_agent`, `_macro`, `_decision`, `_executor`, `_portfolio`, `_portfolio_hydrated`, `_shortlist_alerted`) | agent_loop.py:29-43 | Flow E only | Flow E only | Process-wide, shared across every Celery task invocation in the same worker process — **but Flow A deliberately does NOT use these**, building fresh instances per cycle instead. Two different concurrency postures for conceptually the same object types across Flow A vs. Flow E |
| `_fast_sl_heartbeat_ts`, `_exit_alerted_trade_ids` | tasks/india_tasks.py | `_fast_sl_check` | same | No lock; safe in practice only because Celery's `worker_prefetch_multiplier=1` and single-process-per-task execution limit concurrent access — would race under `--concurrency>1` for this queue (not verified against actual deployment concurrency) |
| `NARRATIVE_BOOST_CACHE` | engine/narrative_engine.py:44 | `refresh_narrative_cache` (Celery task, 5-min TTL) + `api/news.py:143` (lazy force-refresh on stale read) | Flow A's `build_master_context`, `api/news.py` | Two independent triggers of the same refresh function; no lock on the dict mutation itself |
| `LIVE_TICKS` | crawler/zerodha_ticker.py | WebSocket callback thread (`on_ticks`) | various price-read call sites | Mutated from a WS callback thread, read from async/Celery contexts — no lock observed; not independently verified for a race condition, flagged as a candidate for deeper review |
| `NEWS_ALERT`/`_SOURCE_ZERO_STREAK`-style per-source counters | crawler/news_crawler.py | `run_news_crawl` | itself (monitoring) | Single-process-per-cycle, low risk |

---

## 11. Gaps vs. Prior Design Discussions

| Design intent | Status | Evidence |
|---|---|---|
| **Persistent event store with event_id linking trades back to source news events** | **NOT IMPLEMENTED** (schema exists, wiring does not) | `MasterEvent` table (db/models.py:1112-1137) is explicitly docstringed as the persistent event store, but has **zero production writers/readers** anywhere in the repo (confirmed by grep). `agent_trades.event_id` FK is consequently always NULL. The table that *is* actively written, `CausalEvent` (crawler/event_pipeline.py), links news→event only — never event→trade — and is itself never read by any scoring code (only a display-only GET endpoint and a one-off script read it). |
| **Duplicate/cluster detection for news (same story, multiple outlets)** | **NOT FOUND** | Only exact-URL dedup within a crawl batch (news_crawler.py:1183-1193) and exact-string dedup of RSS titles (narrative_engine.py:139). No fuzzy-match/embedding-based clustering anywhere. The narrative engine's "≥2 keyword-source hits" gate is a weak corroboration proxy, not per-story dedup. |
| **Fully offline scoring path (no synchronous external calls during live scoring/replay)** | **PARTIALLY IMPLEMENTED, and self-contradicting** | `build_master_context` explicitly disables a synchronous Tavily call with an in-code "OFFLINE SCORING ENGINE POLICY" comment for deterministic replay (intelligence_hub.py:743-746) — but the **same cycle**, seconds later, `run_research_gate_for_history()` makes live Tavily/web calls for the top-15 BUY signals as part of `persist_daily_history()`, which the code itself calls "the historical replay source for backtest." The core scoring function is offline; the history/replay-persistence step is not — meaning the declared offline-replay guarantee does not actually hold end-to-end. |
| **Immutable decision snapshots (feature vector + weights + versions per trade)** | **PARTIALLY IMPLEMENTED** | `AgentDecision.master_score`/`confidence_factors` (JSON) and `MasterIntelligenceScore.reasoning` (JSON) are genuinely persisted per decision/symbol. No stored strategy-code version or weights-config version identifier was found in either table — only the resulting factor *values*, not a schema/weights version hash. |
| **Deterministic replay capability, and does it currently produce matching output** | **NOT VERIFIABLE by static reading; likely broken given the offline-policy contradiction above** | `hub_daily_history` is written as the stated replay source, but since the same cycle makes a live external call, and since candle timeframe fallback chains / live-price divergence overrides are time-of-run-dependent, a byte-for-byte deterministic re-run was not demonstrated or attempted in this pass — would require actually running a replay to confirm either way. |
| **Feature availability logging (available/fallback/reason per feature per decision)** | **PARTIALLY IMPLEMENTED** | Per-cycle aggregate funnel counters (`no_data`, `no_candidate`, `fuse_drop`, `shadow_skip`, `risk_veto`) are logged once per cycle (india_tasks.py:3144-3146); per-symbol reject reasons are logged via `logger.debug/info` and `candidate.reasons` is persisted to `AgentDecision.reasons`. No systematic "feature X: available/fallback/reason" table exists. |
| **Look-ahead bias safeguards (feature_timestamp ≤ decision_timestamp)** | **PARTIALLY IMPLEMENTED, inconsistently** | `get_market_sentiment()`'s `bar_date` parameter is a genuine, working guard (crawler/news_crawler.py:1333-1335,1345,1372) — but it's opt-in, used only in backtest mode. In the live scoring path, no explicit assertion compares candle/feature timestamps to `cycle_start`; the live-price divergence guard (Flow A step 12d) actually does the *opposite* by design — pulling a *later* live price to override a stale candle close for entry accuracy, which is correct for live trading but means the recorded entry is not strictly derived from an immutable historical bar. |
| **Git commit / config hash stored per decision** | **NOT FOUND** | No `git_sha`, `config_hash`, or similar column exists on `AgentDecision`, `PaperTrade`, or `MasterIntelligenceScore`. |
| **Universe selection filters: listing age, free float, spread, F&O flag, corporate-action status** | **NOT DEEP-VERIFIED in this pass** — `hub_universe` rebuild is confirmed to filter by 30-day avg turnover (`HUB_UNIVERSE_MIN_TURNOVER_CR`); listing-age/free-float/spread/corporate-action filters were not confirmed present or absent in `engine/hub_universe.py` — flag for follow-up. |
| **Strategy-specific weighting (per-strategy weight sets vs. one global weight set)** | **PARTIALLY IMPLEMENTED** | `DecisionEngine.fuse`'s confidence formula is one global multiplicative formula applied regardless of `candidate.strategy`. Whether individual `engine/agent/strategies/*` files carry their own internal parameter sets was not confirmed — those files were not read function-by-function in any research pass. |
| **News decay function (linear/exponential, category-specific half-lives)** | **NOT IMPLEMENTED in the path that feeds live scoring** | `get_market_sentiment()` (the actual read path) computes a **flat, unweighted arithmetic mean** of the last 10 scored headlines — a 9-minute-old and a 10-day-old headline within that window count equally. A half-life concept exists only as an **unused** field: `EventClassification.expected_half_life_hours` is captured from the LLM and stored on `CausalEvent.duration` as a **string** (event_pipeline.py:48, column type `String(50)` — not even numeric) — and since `CausalEvent` is never read by scoring code, this value is written and never consumed. The half-life/decay concept was designed into the schema but never wired into actual decay math. |
| **Risk/portfolio engine: position limits, sector concentration limits, daily loss limits** | **IMPLEMENTED** | Confirmed in `RiskManagerAgent.can_take_trade` (engine/agent/risk_manager.py:104-190+): daily/weekly/monthly drawdown circuit breakers, consecutive-loss lockout, max-daily-entries cap (live mode only — explicitly skipped in paper mode), per-trade risk-distance sizing, per-trade risk-% cap, portfolio open-risk cap, cash-buffer floor, dedup, correlation-cluster guard (>0.70), 20%-default sector-exposure cap. This is the one item on this list that is solidly built. |

---

## 12. Honest Summary

**What's solid:**
- The core risk-gating logic (`RiskManagerAgent.can_take_trade`) is real and comprehensive: drawdown breakers, correlation clustering, sector exposure caps, cash buffers, and consecutive-loss lockouts are all implemented and wired into the live pipeline, not just declared in config.
- Boot-time safety rails in `main.py:lifespan()` genuinely refuse to start the app if risk-per-trade/portfolio-risk config exceeds hard ceilings — this is enforced in code, not just documented as a policy.
- The paper-execution write path (`trade_simulator.py`) models real Indian transaction costs (brokerage/STT/exchange/SEBI/stamp/GST) and tracks MFE/MAE excursion data, which is more rigorous than a naive fill simulator.
- F&O is genuinely gated off by default across every relevant flag, consistent with project history — this was verified, not assumed.
- The system is confirmed **paper-trading only in its default configuration**; the two reachable real-order code paths require explicit `.env` flag changes plus (in one path) admin auth and a confirmation header.
- Failure handling in the crawler layer is consistently fail-soft (catch, log or silently degrade, return empty/zeroed) rather than crash-prone — a deliberate, broadly-applied pattern.

**What's incomplete:**
- The "persistent event store linking trades to news events" — a stated design goal — has a table for it (`MasterEvent`) that is completely unused; the event pipeline that *does* run (`CausalEvent`) only links news to a classified event, never reaches trades, and is never consumed by scoring.
- News/narrative decay is not implemented in the path that actually feeds live scoring (`get_market_sentiment` uses a flat 10-item mean); a half-life field exists in the schema but is dead.
- The stated "offline, deterministic scoring" policy is violated within the same cycle it's declared in, by a live research call inside the "replay source" persistence step.
- API authentication is applied to 5 routes out of dozens that can mutate trading state, risk parameters, or open/close positions — including a path (`PATCH /settings/`) that can silently flip paper-mode to live trading with none of the safeguards its sibling route (`POST /settings/mode`) enforces.
- Roughly a third of the DB schema (30 of 45 tables) was not column-audited in this pass; two confirmed-dead columns and one confirmed-dead table were found in the portion that was audited, so it's reasonable to expect more in the unaudited portion.

**What's fragile:**
- Three independent, only-partially-overlapping code paths (Flow A, Flow B, Flow E) can each open a position, with different candidate-sourcing logic, different singleton-vs-fresh-instance concurrency postures, and at least one confirmed stale in-code comment (`market_scanner.py` claiming a linkage to `india_trade_loop` that doesn't exist).
- Market-hours checks are duplicated three ways with inconsistent holiday-awareness — two of the three duplicates would treat a trading holiday as a live session.
- Cross-process shared state (`PRICE_CACHE`, `SECTOR_CACHE`, `BREADTH_CACHE`) has no locking and is known-inconsistent across the FastAPI process vs. Celery worker process boundary; one workaround for this (`fast_sl_check` re-fetching live prices instead of trusting the cache) is deliberate and documented, but it's a workaround for a real gap, not a fix.
- A silent (unlogged) exception swallow in the 5-minute candle fetch loop (`india_price_feed.py:669-670`) means a systemic ingestion outage for this timeframe would currently produce zero diagnostic trace.

**What I'm not fully certain about** (would require running the system, not just reading it):
- Whether `hub_daily_history` actually reproduces matching scores on a real replay run — the offline-policy contradiction makes this doubtful, but it wasn't tested.
- Whether the `options_chain.py` sync `time.sleep()` calls inside what appears to be an async code path actually block the event loop in production, or whether they're properly isolated in an executor — the surrounding code wasn't fully read to confirm either way.
- Whether `crawler/sentiment.py`'s `SentimentAnalyser` is truly dead code or has an importer that wasn't caught by the greps run in this pass.
- The actual current values of any `RuntimeConfig`-overridden settings in the live database (only the `.env`/`config.py` defaults were verified; a DB row could be overriding any of them right now).
- Whether the FK-violation risk on the weekly news purge (`causal_events.news_id` with no visible `ON DELETE CASCADE`) has actually caused a failure — this depends on live migration DDL that wasn't inspected directly.

### What was not covered at full depth, and a proposed follow-up split

Not function-by-function audited in this pass: `engine/agent/strategies/*` (per-strategy candidate generation — needed to fully answer the "strategy-specific weighting" question in §11), `engine/agent/decision_engine.py`'s LLM tool-use functions (`_tool_*`, `llm_tooluse_candidate`, `llm_debate_candidate`), `engine/zerodha_executor.py`'s order-modification/GTT surface beyond what's cited, `engine/fno/contracts.py`/`adjustments.py`/`historical_ingest.py`/`options_pricing.py`/`strategies_vol.py` (module-purpose only), the remaining ~30 of 45 DB tables' column-level read/write audit, and most of the display-only dashboards (tax/SIP/MF/IPO/buyback/portfolio-doctor) which were confirmed out-of-scope for the trade-decision pipeline but not audited for their own internal correctness.

Suggested next passes, in priority order: (1) `engine/agent/strategies/*` — directly needed to close the strategy-weighting question and to verify what `StrategySelectorAgent.propose()` is actually choosing between; (2) full column audit of the remaining 30 tables, focused on anything touched by Flow A/B/E; (3) `engine/fno/*` internals now that the F&O flow is mapped at the orchestration level; (4) a live-run verification of the replay/backtest determinism question, which cannot be resolved by reading code alone.
