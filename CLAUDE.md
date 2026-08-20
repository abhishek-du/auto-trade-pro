# CLAUDE.md — AutoTrade Pro ("Prajna")

Indian **equity** (cash-segment) algo-trading system. News-driven origination,
LLM decision agent, paper-trading by default. FastAPI + Celery + Postgres +
Redis; Zerodha Kite and Upstox for market data; AWS Bedrock for the agent.

> **Deep reference:** [`docs/DEEP_AUDIT_2026-08-19.md`](docs/DEEP_AUDIT_2026-08-19.md)
> — full function inventory, complete route/schema/beat tables, and the full
> merits/demerits matrix with `file:line` citations. Read this file first; go
> there when you need detail.

---

## 1. Read this before changing anything

| Rule | Why |
|---|---|
| **Use `.venv/bin/python`, never bare `python3`** | Host python is 3.14; the venv is 3.11. The ABI mismatch breaks `pydantic_core` and every compiled extension. |
| **Run `pytest tests/`, never bare `pytest`** | There is **no pytest config file**. Bare `pytest` from the backend root collects 54 scratch `test_*.py` files that call `asyncio.run()` at module scope — it fires live HTTPS crawls during *collection* (3m26s, 8 errors vs 12s, 2 errors). |
| **Restart services after any `.env` change** | `watchmedo` watches `*.py` only. Settings and the boto3 client are cached per process. An `.env` fix that is never loaded is exactly how the 2026-08-08→17 Bedrock outage lasted 8.75 days. |
| **`NO EVENT → NO TRADE`** | Every automatic trade must trace to a canonical `CausalEvent.id`. Enforced fail-closed in `engine/decision_router.py::authorize_trade_intent`. Contract: `docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md`. |
| **Never `git add`** unless asked | The working tree carries other people's in-flight files; a broad `git commit` once swept staged files into a colleague's commit. |
| **PAPER MODE is the default and the safe state** | See §6. Do not flip it as a side effect of anything. |

### Name collisions that will bite you

- **`news_discovery_engine.py`** — the **root-level** file (1499 lines) is the
  live production trade engine. `engine/news_discovery_engine.py` is a
  *different* 1.5KB stub holding `DuplicateEventEngine`.
- **`risk_manager.py`** — `engine/risk_manager.py` holds `validate_signal()`
  (the 12-check equity gate). `engine/agent/risk_manager.py` holds
  `RiskManagerAgent`. Different files, different jobs.
- **`analytics` and `attribution`** routers share the `/api/v1/analytics`
  prefix.

### The root-level script sprawl

`autotrade-backend/` has **157 `.py` files at its root, 137 of them
git-tracked**. Exactly **two are production**:

- `main.py` — the FastAPI app
- `news_discovery_engine.py` — the live news trade engine

Everything else (`check_*`, `query_*`, `dump_*`, `analyze_*`, `diagnose_*`,
and 54 `test_*`) is committed scratch. Do not treat a root-level file as
important because of where it lives.

---

## 2. System architecture

```
                            EXTERNAL
  Zerodha Kite (WS + REST) · Upstox · yfinance · Alpha Vantage
  NewsAPI/Finnhub/NewsData/RSS/RBI/PIB/SEBI · AMFI · NSE · Tavily
  AWS Bedrock (Converse API)                      · Telegram · Google Sheets
                                │
 ┌──────────────────────────────┼───────────────────────────────────────────┐
 │  FOUR long-running services (deploy/systemd/)                            │
 │                              │                                           │
 │  ┌────────────────────┐  ┌───┴────────────────┐  ┌────────────────────┐  │
 │  │ autotrade-uvicorn  │  │ autotrade-celery-  │  │ autotrade-news-    │  │
 │  │ main.py            │  │ worker (conc=2)    │  │ engine             │  │
 │  │                    │  │ + celery-beat      │  │                    │  │
 │  │ 26 routers /api/v1 │  │                    │  │ news_discovery_    │  │
 │  │ 7 WS endpoints /ws │  │ ~60 beat entries   │  │ engine.py          │  │
 │  │                    │  │ single default     │  │                    │  │
 │  │ 4 asyncio loops:   │  │ queue, no routing  │  │ 24/7 DB-queue poll │  │
 │  │  · DDL w/ retries  │  │                    │  │ ↓ THE trade path   │  │
 │  │  · price hydrate   │  │ Redis SET-NX-EX    │  │                    │  │
 │  │  · breadth 120s    │  │ overlap guards     │  │ auto-reload: YES   │  │
 │  │  · info-cache warm │  │                    │  │                    │  │
 │  │                    │  │ auto-reload: YES   │  └─────────┬──────────┘  │
 │  │ KiteTicker on a    │  └───┬────────────────┘            │             │
 │  │ DAEMON thread ─────┼──┐   │                             │             │
 │  │ MemoryMax=3G       │  │   │      + autotrade-zerodha-refresh.timer     │
 │  │ auto-reload: NO    │  │   │        (token refresh, 02:30 UTC)          │
 │  └────────┬───────────┘  │   │                             │             │
 └───────────┼──────────────┼───┼─────────────────────────────┼─────────────┘
             │              │   │                             │
   ┌─────────┴──────────────┴───┴─────────────────────────────┴───────────┐
   │ STATE                                                                │
   │  Postgres  52 ORM models · candles · paper_trades · open_positions   │
   │            causal_events · agent_decisions · master_intelligence_*   │
   │  Redis     Celery broker · cross-process price snapshot · LLM RPM    │
   │            limiter · alert dedup · overlap locks                     │
   │  In-proc   PRICE_CACHE / LIVE_TICKS / SECTOR_CACHE / INFO_CACHE      │
   │            ⚠ per-process, unlocked, written from the ticker thread   │
   └──────────────────────────────────────────────────────────────────────┘
```

**Cross-process price flow:** the Celery `price_cache` task (30s) fetches and
publishes to Redis; every other process hydrates from Redis (uvicorn every
15s). The KiteTicker writes sub-second ticks straight into the uvicorn
process's `PRICE_CACHE`, and hydration deliberately will not clobber them.

---

## 3. Trade lifecycle

### The live path (news-driven) — this is the only path that opens trades

```
 news sources ──► crawler/news_crawler.py  run_news_crawl()      11 sources
                     │
                     ├─► crawler/news_router.py  route_headline()      regex Tier-0
                     └─► crawler/event_pipeline.py  process_latest_events()
                                                    └─► CausalEvent row
                     │
 news_discovery_engine.py::run_news_discovery_loop()   :1284   24/7 poll
   └─ process_ticker(ticker, side, headline, summary)  :1118
        │
        ├─ _build_evidence()                           :678
        │     └─ no canonical event ──► RETURN, no LLM call spent      :1125
        │
        ├─ maybe_direct_trade()   engine/direct_news_strategy.py   ── DIRECT_NEWS
        │                          (fires on classified evidence, no LLM debate)
        │
        ├─ llm_tooluse_candidate()  engine/agent/decision_engine.py :1268
        │     ReAct loop, ≤20 rounds, force-decide at 12
        │     10 tools: fundamentals · company_intelligence · news · options
        │              price_action · market_depth · intraday_candles
        │              sector · macro · predict_candle · expert_research
        │     ├─ TA-Lib indicators via engine/indicators.py compute_indicators()
        │     ├─ AWS Bedrock Converse  utils/llm.py::call_mantle_chat()
        │     └─ _check_grounding()                    :1142   hallucination gate
        │
        ├─ validate_evidence_consistency()             :1158   thesis vs evidence
        │
        ├─ _execute_news_trade()                       :349
        │     ├─ get_market_snapshot()   ONE tick shared by decision + execution
        │     ├─ 30-min late-entry gate  :409   fail-OPEN
        │     ├─ multi-session gate (1d) :448   fail-OPEN
        │     ├─ _compute_news_trade_levels()   SL / T1 / T2 / ATR
        │     └─ TradeIntent ──► engine/decision_router.py
        │            execute_trade_intent() ──► authorize_trade_intent()  :492
        │              ├─ NSE market-hours gate
        │              ├─ TECHNICAL-origination hard block          :564
        │              ├─ _verify_canonical_event()   NO EVENT → NO TRADE  :395
        │              └─ engine/risk_manager.py validate_signal()  12 checks
        │            route_decision()                               :245
        │              ├─ PAPER ──► paper_trading/trade_simulator.py
        │              │              open_paper_trade()   :229
        │              │              slippage 2-8bps adverse + full NSE costs
        │              └─ LIVE  ──► engine/zerodha_executor.py
        │                             place_real_order()  10 safety rules
        │                             ⚠ BROKEN — see §5 D2
        │
        └─ get_second_order_trades()  engine/sector_graph.py  :1184  cascade
```

### Exits — two independent loops

- **`fast_sl_check`** every **5s** (`tasks/india_tasks.py:1337`) — Kite REST
  LTP with a yfinance backstop, corporate-action guard, then
  `scale_out_paper_trade` at T1 or `close_paper_trade` on SL/TP.
- **`india_trade_loop`** every **60s** (`:477`) — snapshot hot-patch,
  `update_positions_with_current_prices()`, LLM dynamic SL/TP, drawdown
  breakers.

Exits keep running when trading is halted. That is deliberate.

### Origination authority

Only four strategy families can reach an executor:

| Family | Origin | Status |
|---|---|---|
| `EVENT_DRIVEN` | `news_discovery_engine.py:522`, `engine/agent/event_arbitrage.py:124` | **live** |
| `DIRECT_NEWS` | `engine/direct_news_strategy.py:285` | **live** |
| `PRE_EVENT` | `tasks/india_tasks.py:4128` (Pre-Event Expectation Gap) | **live** |
| `TECHNICAL` | `india_trade_loop` (`tasks/india_tasks.py:1262`) | **OPEN in the working tree**, blocked at HEAD — see §5 D1 |
| `TECHNICAL` | `agent_loop` (`:312`), `intraday_entry` (`india_tasks.py:1737`) | **hard-blocked** in both HEAD and working tree |

---

## 4. Where things live

| Path | What |
|---|---|
| `main.py` | FastAPI app, 26 routers, lifespan, boot safety checks |
| `news_discovery_engine.py` | **the live trade engine** (root-level) |
| `api/` | 26 route modules. Biggest: `india.py` (2485), `zerodha.py` (1789) |
| `engine/decision_router.py` | **the central execution gate** — every intent passes here |
| `engine/agent/decision_engine.py` | LLM ReAct tool-use loop, grounding, `fuse()` |
| `engine/agent/execution.py` | paper/live dispatch, DB writes |
| `engine/risk_manager.py` | `validate_signal()` 12 checks, sizing, `compute_trade_levels()` |
| `engine/indicators.py` | TA-Lib indicators (+ pandas fallback for every one) |
| `engine/intelligence_hub.py` | 7-factor scoring engine (**scores, does not execute**) |
| `engine/zerodha_executor.py` | real-broker orders, 10 safety rules |
| `paper_trading/` | simulator, virtual wallet, P&L — the default execution sink |
| `crawler/` | Kite ticker/REST, Upstox, yfinance, news, event pipeline |
| `tasks/celery_app.py` | broker config + the full ~60-entry beat schedule |
| `tasks/india_tasks.py` | 4195 lines — most scheduled trading work |
| `utils/llm.py` | AWS Bedrock Converse client, breaker, Redis RPM limiter |
| `utils/config.py` | `Settings` — every flag and risk limit |
| `db/models.py` | 52 ORM models |
| `docs/` | architecture contract, forensic post-mortems, phase reports |

---

## 5. Known defects — the four that matter

Full matrix in the deep-audit doc. All four below are **CONFIRMED** by reading
the code path.

**D1 — Technical origination is LIVE in the working tree, contradicting the news-only contract.**
**The working tree diverges from HEAD on this — uncommitted, changed 2026-08-19 16:44.**

| | `_TECHNICAL_TRADE_ORIGINATION_BLOCKED` | `_NEWS_ONLY_BLOCKS_HUB_ENTRIES` (`india_tasks.py:572`) |
|---|---|---|
| **HEAD** (`91457d7`) | `True` — `decision_router.py:564` | `False` |
| **Working tree** (what is running) | **`False`** — `decision_router.py:566` | `False` |

At HEAD the two flags cancel out: `india_trade_loop` builds its intent with
`strategy_family=TECHNICAL` (`india_tasks.py:1262`) and the central gate
rejects it, so the loop burns the full pipeline every 60s and opens nothing.

**In the working tree both blocks are off, so `india_trade_loop` can originate
TECHNICAL trades.** `watchmedo` hot-reloads `*.py`, so the running worker uses
the working-tree version. The binding contract in
`docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md` §5-6 is therefore **not in
force right now**, and performance data will pool news-driven and technical
trades together.

This is someone's uncommitted change, not the audit's. Confirm it is
intentional, then either commit it with the contract doc updated, or revert it.

**D2 — Every live order routed through `decision_router` fails silently.**
`engine/decision_router.py:308-315` passes `signal_id=` and `confidence=` to
`place_real_order()`, whose signature (`engine/zerodha_executor.py:300-314`)
accepts neither. The `TypeError` is swallowed by the broad `except` at `:330`
and surfaces as a generic `RoutingOutcome.ERROR`. Latent only because
`PAPER_MODE=true` — but D1's working-tree state now routes TECHNICAL intents
here too, widening the blast radius. **Fix before considering live trading.**

**D3 — `PRICE_CACHE` staleness checks are a no-op.**
`crawler/live_prices.py:86` computes
`age = now - cached.get("_ts", now)` — and `_ts` is written in exactly one
place (`crawler/live_snapshot.py:162`), never by `on_ticks`,
`fetch_prices_batch`, or `hydrate_prices_from_redis`. For those entries `age`
is always `0.0` and the 30s guard always passes. Worse,
`crawler/market_snapshot.py:104` — the entry-price source for every news
trade — performs no age check at all, and `engine/zerodha_executor.py:359`
reads `PRICE_CACHE` unchecked to compute the LIMIT buffer.

**D4 — Auth covers 6 handlers; the kill switch does not cross processes.**
`require_auth` guards only `agent.trigger_cycle`, `agent.halt`,
`agent.resume`, `settings.set_trade_mode`, `zerodha.place_order`. Everything
else is anonymous, including `POST /agent/kill-switch` (flattens the book,
header-only guard), `PATCH /api/v1/settings/` (can flip `paper_mode` and
`max_risk_per_trade` with no token, bypassing every safeguard its sibling
`POST /settings/mode` enforces), all 7 GTT routes, and all 7 WebSockets.
And `api/agent.py:424` sets `settings.AGENT_ENABLED = False` **in-process** —
uvicorn and Celery are separate processes, so the kill switch does not stop
the worker. `halt`/`resume` do it correctly via `RuntimeConfig` in the DB.

**Also live:** three beat entries (`tasks/celery_app.py:127,136,149`) still
point at tasks deleted with F&O and raise `NotRegistered` on every tick.

---

## 5b. Strategy execution toggles (admin UI)

Six DB-backed switches, one per origination path, at **`/settings` → Strategy
Execution**. Stored in `RuntimeConfig`, so a change takes effect in every
process at its next decision — **no restart**.

| UI name | Path | Gates |
|---|---|---|
| `master_intelligence` | A | scoring + 2 discretionary exits (**does not originate trades**) |
| `india_trade_loop` | B | entries only — exits keep running |
| `news_engine` | C | execution only — news is still crawled and classified |
| `pre_event_gap` | D | the whole task |
| `direct_news` | E | `maybe_direct_trade` |
| `tactical` | F | execution only — signals are still scored and persisted |

```
GET  /api/v1/settings/strategies          # anonymous, read-only
POST /api/v1/settings/strategies          # JWT required; {"flags":{"tactical":false}}
```

- **Fail-open**: a missing row or an unreachable DB reads as *enabled*. These
  are enable switches — a database blip must not silently halt trading. This is
  the opposite posture to `tactical_risk`, which fails closed because its flag
  caps risk.
- **Exits are never gated.** `fast_sl_check` (5s) carries no toggle, so open
  positions still exit on SL/TP with every strategy off.
- Checked alongside the existing `.env` flags (`TACTICAL_EXECUTION_ENABLED`,
  `PRE_EVENT_GAP_ENABLED`, `DIRECT_NEWS_ENABLED`), which remain the deploy-time
  defaults — the toggle is the operator's runtime override, not a replacement.
- `paper_trades.strategy_family` records which path opened each trade, so P&L
  can be grouped by strategy. The older `source`/`strategy_name` pair was
  free-text and inconsistent.

## 6. Paper mode

Default and safe. `PAPER_MODE=true`, `ZERODHA_ENABLED=false`,
`AGENT_PAPER_MODE=true` (`utils/config.py`). Going live requires **all** of:

1. `POST /api/v1/settings/mode` with JWT auth **and**
   `confirm == "I_UNDERSTAND_REAL_MONEY"`, plus `ZERODHA_ENABLED` and a live
   access token — else 409.
2. `POST /api/v1/zerodha/orders` additionally needs JWT **and** the
   `X-Confirm-Real-Order: yes` header.
3. `place_real_order()` then enforces 10 rules: paper-mode off, valid token,
   confidence ≥ `max(60, LIVE_CONFIDENCE_THRESHOLD)`, ≤5% of live balance,
   NSE open, daily-loss check, a 3s abort window, MARKET forced to LIMIT with
   a ±0.5% buffer, <5 open positions, tagged `ATP_{signal_id}`.

Boot-time rails: `main.py:36-60` refuses to start (`SystemExit`) if
`MAX_RISK_PER_TRADE > 5%`, `MAX_PORTFOLIO_RISK > 50%`, or the conviction band
exceeds 5%.

Capital is **₹5,00,000** (reset from ₹20L in `712106a`).

---

## 7. Commands

```bash
BE=/home/cis/windows/auto-trade-pro/autotrade-backend
FE=/home/cis/windows/auto-trade-pro/autotrade-frontend
```

### Quickstart (START_COMMANDS.txt)
```bash
sudo docker compose -f $BE/docker-compose.yml up -d postgres
cd $BE && bash start.sh
cd $FE && npm run dev
```
Frontend `:5173` · Backend `:8000` · Docs `:8000/docs` · Adminer `:8080`

### Run services individually
```bash
cd $BE
PYTHONPATH=$PWD .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 \
    --timeout-graceful-shutdown 12
PYTHONPATH=$PWD .venv/bin/python -m celery -A tasks.celery_app worker --loglevel=info --concurrency=2
PYTHONPATH=$PWD .venv/bin/python -m celery -A tasks.celery_app beat   --loglevel=info
PYTHONPATH=$PWD .venv/bin/python news_discovery_engine.py     # the trade engine
```

### Tests
```bash
cd $BE
.venv/bin/python -m pytest tests/ -q                 # ~1277 tests
.venv/bin/python -m pytest tests/ --collect-only -q  # 1277 collected, 2 errors, ~12s
.venv/bin/python -m pytest tests/test_decision_router.py -q
```
The 2 collection errors are stale imports, pre-existing:
`tests/test_fundamental_analyzer.py:18` (`fetch_fundamentals_yfinance`) and
`tests/test_paper_trading.py:10` (`SignalGenerator`).
Telegram is hard-disabled under pytest (`integrations/telegram_service.py:36`).

### Frontend
```bash
cd $FE
npm run dev        # vite --host, :5173, proxies /api and /ws to :8000
npm run build      # → dist/
npm run lint
VITE_HMR_HOST=vnad5173.elb.cisinlive.com npm run dev   # behind the ELB
```

### systemd (production — user units, no root)
```bash
cp /home/cis/windows/auto-trade-pro/deploy/systemd/*.service \
   /home/cis/windows/auto-trade-pro/deploy/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now autotrade-uvicorn autotrade-celery-worker \
    autotrade-celery-beat autotrade-news-engine autotrade-zerodha-refresh.timer

systemctl --user restart autotrade-celery-worker   # REQUIRED after any .env change
journalctl --user -u autotrade-news-engine -f
```
Use `deploy/systemd/` at the repo root. `autotrade-backend/deploy/systemd/` is
a stale 4-file duplicate missing the uvicorn and news-engine units.
Logs go to `/tmp/{uvicorn,celery_worker,celery_beat,news-engine}.log` and
**do not rotate** — `celery_worker.log` has reached 2.6 GB.

### Database
```bash
sudo docker compose -f $BE/docker-compose.yml up -d postgres   # autotrade_postgres :5432
cd $BE && .venv/bin/python -m alembic upgrade head
```
Note: schema is managed **two ways** — Alembic (`db/migrations/versions/0001-0005`)
*and* a ~60-statement inline DDL block in `db/database.py::init_db()` that
partially duplicates it. Check both before adding a column.

---

## 8. Stack

Python 3.11 (`.venv`) · FastAPI 0.136 · SQLAlchemy 2.0.50 async + asyncpg
0.31 (`NullPool`) · Celery 5.6.3 · Redis 6.4 · pandas 3.0 · TA-Lib 0.6.8 ·
kiteconnect 5.2 · boto3 (Bedrock Converse, `nvidia.nemotron-super-3-120b`) ·
TensorFlow/Torch for the LSTM predictor.
Frontend: React 19 + Vite 8 + Tailwind 4, plain JS, 41 pages.

`uvloop` is pinned but never explicitly installed — uvicorn may pick it up via
`loop="auto"`, but it is not a deliberate control. There is no raw `asyncpg`
usage; everything goes through SQLAlchemy.

---

## 9. Related docs

| Doc | What |
|---|---|
| `docs/DEEP_AUDIT_2026-08-19.md` | **full audit** — functions, routes, schema, beat, matrix, fixes |
| `docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md` | binding contract: authority matrix, forbidden patterns |
| `docs/2026-08-17_FORENSIC_POST_MORTEM.md` | 4 data-backed root causes + P0/P1/P2 backlog |
| `docs/NEWS_INGESTION_LATENCY_FORENSIC_AUDIT.md` | news latency |
| `docs/STRATEGY_CONSOLIDATION_REPORT.md` | strategy classification |
| `autotrade-pro-codebase-map.md` | prior audit, 2026-07-17 — **partly stale** (documents the now-deleted F&O subsystem, pre-dates the ₹20L→₹5L reset) |
