# AutoTrade Pro — Complete Project Documentation

> **Paper Trading Only** — This system uses virtual/simulated currency exclusively. No real money is ever involved at any stage. Real order execution requires `ZERODHA_PAPER_MODE=false` AND `ZERODHA_ENABLED=true` AND the `X-Confirm-Real-Order: yes` header simultaneously.

---

## Table of Contents

- Project Overview
- Architecture
- Technology Stack
- Backend — Structure and Modules
- Decision Router & Unified Trade Mode
- Signal Engine
- Technical Indicators
- Deep Analysis Engine
- Risk Management
- Paper Trading Simulation
- News and Sentiment
- LLM Integration
- Avishk AI Stock Analyst
- India Market Suite
- My Portfolio (Stocks + Mutual Funds + Zerodha sync)
- Portfolio Doctor — AI Health Analysis
- Earnings Call Analyzer — AI Transcript Summaries
- AI Trading Agent — Varsity-Grounded Autonomous System
- Zerodha KiteConnect v3 Integration
- Unified Market Data Layer
- Celery Background Tasks
- API Reference
- Database Schema
- Frontend — Structure and Pages
- Frontend Components
- Frontend Hooks
- Configuration and Environment Variables
- Infrastructure
- Development Setup
- Known Constraints and Design Decisions

---

## Project Overview

AutoTrade Pro is a full-stack automated paper-trading platform for Indian markets. It continuously pulls OHLCV price data from NSE, runs a multi-factor signal engine (candlestick patterns + technical indicators + FinBERT news sentiment), validates signals through a risk gate, and opens/manages simulated trades against a virtual wallet.

The platform covers the complete spectrum of Indian market tools:

- **Signal Engine** — multi-factor BUY/SELL/HOLD on NSE/BSE stocks
- **Decision Router** — single source of truth that routes every signal to paper or live execution through one unified confidence gate; runtime paper↔live toggle, no restart
- **Avishk AI Stock Analyst** — conversational AI with live NSE context (price, indicators, news, signals), powered by Groq LLM with rule-based fallback
- **India Market Suite** — FII/DII flows, options chain, sector heatmap, market breadth, India VIX, NSE signals, market calendar (F&O expiry, RBI MPC, holidays, earnings, IPOs)
- **My Portfolio** — real stock + mutual fund + Zerodha-synced holdings in one portfolio with live P&L, XIRR, allocation analytics; source-tagged (manual / mutual fund / Zerodha). MF NAV auto-fetches via mfapi.in
- **Portfolio Doctor** — AI-powered health diagnosis: 7 diagnostic modules + Groq narrative + 0-100 score with letter grade
- **Earnings Call Analyzer** — fetches BSE/NSE filed transcripts (any NSE-listed company via dynamic scrip resolution), extracts PDF text, generates structured AI summaries with management tone analysis
- **AI Trading Agent** — Varsity-grounded autonomous trading system: 4 strategies, regime classifier, fundamental + macro overlay, unconditional risk-manager veto, paper-by-default with backtester
- **Asset Allocation Analyzer** — target vs. actual allocation with rebalancing recommendations
- **SIP Goal Planner** — SIP projections with XIRR and scenario analysis
- **Tax Calculator** — STCG/LTCG under Budget 2024 rules with P&L worksheet
- **IPO Tracker** — live IPO status, GMP, subscription data
- **Mutual Fund Tracker** — NAV history, SIP analysis, signal scoring
- **Zerodha KiteConnect v3** — full paid-plan integration: OAuth, real holdings sync, 60 API endpoints, KiteTicker WebSocket, GTT/OCO orders, MF orders/SIPs, margin preview, virtual contract note, alerts. Legacy `/kite/*` endpoints transparently fall back to v3 credentials
- **Unified Market Data Layer** — `get_price()` resolves Zerodha KiteTicker (sub-second) first, then yfinance cache; returns a `source` + `age_seconds` label so the UI shows data freshness

All data flows through a FastAPI backend with Celery workers; the React SPA reads over REST and WebSocket.

**Two distinct portfolios, clearly named** — *Simulator* (`/portfolio`, the virtual paper-trading wallet) is now separate in name and intent from *My Portfolio* (`/portfolio-tracker`, real holdings: manual + mutual fund + Zerodha-synced). A trade-mode badge (PAPER / LIVE / DRY_RUN) in the Navbar reflects the live routing state and toggles it at runtime.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  React Frontend (Vite + Tailwind CSS)                        │
│  localhost:5173                                               │
└────────────────────┬─────────────────────────────────────────┘
                     │ REST + WebSocket
┌────────────────────▼─────────────────────────────────────────┐
│  FastAPI Backend (Uvicorn, async)                             │
│  localhost:8000                                               │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  API Routers │  │  Signal      │  │  Paper Trading   │    │
│  │  (60 routes) │  │  Engine      │  │  Simulation      │    │
│  └──────────────┘  └──────────────┘  └──────────────────┘    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  India Market│  │  Zerodha     │  │  Avishk AI Chat  │    │
│  │  Suite       │  │  KiteConnect │  │  Engine          │    │
│  └──────────────┘  └──────────────┘  └──────────────────┘    │
└────────────────────┬─────────────────────────────────────────┘
                     │ SQLAlchemy async
┌────────────────────▼────────────────┐
│  PostgreSQL (Supabase transaction    │
│  pooler)                             │
└─────────────────────────────────────┘

┌──────────────────────────────────────┐
│  Celery Workers + Beat               │
│  Core:                               │
│    scan_watchlist   (every 30s)      │
│    scan_news        (every 5 min)    │
│    paper_trade_loop (every 60s)      │
│  India Market:                       │
│    fii_dii          (daily 17:00)   │
│    options_chain    (every 15 min)   │
│    sector_breadth   (every 30 min)   │
│    india_signals    (daily 08:00)    │
│  Zerodha Kite:                       │
│    kite_sync_holdings  (daily 21:05) │
│    kite_sync_candles   (daily 15:30) │
│    kite_refresh_instruments (08:00)  │
│    kite_check_token    (daily 06:05) │
│    kite_start_ticker   (09:15)       │
│  Broker/Backend: Upstash Redis (TLS) │
└──────────────────────────────────────┘

External APIs:
  yfinance           — free, no key (primary price + news)
  Groq API           — Avishk AI chat + signal explanations
  NSE India          — FII/DII, options chain (public endpoints)
  MFAPI              — mutual fund NAV history (free)
  ipoalerts.in       — IPO data (free, 25 req/day)
  Zerodha KiteConnect v3 — OAuth, portfolio, all market data,
                           orders, GTT, MF (₹500/month paid plan)
```

---

## Technology Stack

### Backend

| Technology | Version | Why |
|---|---|---|
| Python | 3.11+ | Modern async features, type hints |
| FastAPI | 0.110+ | Async-native, automatic OpenAPI docs |
| Uvicorn | 0.29+ | ASGI server |
| SQLAlchemy | 2.0 | Async ORM with `AsyncSession` |
| asyncpg | 0.29+ | Fastest PostgreSQL async driver |
| Celery | 5.3+ | Distributed background task queue |
| Redis / Upstash | — | Celery broker and result backend |
| PostgreSQL | 15 via Supabase | Hosted relational database |
| yfinance | 0.2+ | Free OHLCV + news (primary source) |
| kiteconnect | 4.2+ | Official Zerodha KiteConnect v3 library |
| pandas + numpy | 2.x | Time-series, indicator calculations |
| httpx | 0.27+ | Async HTTP for external APIs |
| Pydantic v2 | 2.x | Settings + schema validation |
| Groq via httpx | — | LLM inference (llama-3.1-8b-instant) |

### Frontend

| Technology | Version | Why |
|---|---|---|
| React | 19 | Concurrent mode, hooks |
| Vite | 5 | Sub-second HMR, optimal bundling |
| Tailwind CSS | 4 | Utility-first styling |
| React Router | 6 | Client-side SPA routing |
| Recharts | 2.x | Composable charts |
| Lucide React | — | SVG icon set |
| react-hot-toast | — | Non-intrusive notifications |

---

## Backend — Structure and Modules

```
autotrade-backend/
├── main.py                  — FastAPI app, lifespan, router registration
├── requirements.txt
│
├── api/                     — REST API routers
│   ├── agent.py             — AI Trading Agent: status, cycle, backtest,
│   │                          decisions, trades, performance, signal, config, rulebook
│   ├── allocation.py        — Asset allocation analysis + rebalancing
│   ├── analytics.py         — Performance stats + chart data
│   ├── earnings.py          — Earnings call AI analyzer: summary, list, history,
│   │                          recent, refresh, compare
│   ├── india.py             — India market: FII/DII, options, calendar,
│   │                          breadth, heatmap, signals, backtest
│   ├── ipo_tracker.py       — IPO status, GMP, subscription data
│   ├── kite.py              — Legacy Kite portfolio tracker (transparent
│   │                          fallback to Zerodha v3 when KITE_API_KEY unset)
│   ├── mf_tracker.py        — Mutual fund tracker (holdings, SIP analysis)
│   ├── news.py              — News feed + per-symbol sentiment
│   ├── portfolio.py         — Virtual wallet: summary, positions, snapshots
│   ├── portfolio_doctor.py  — AI Portfolio Doctor: diagnose, history,
│   │                          quick-check, delete
│   ├── portfolio_tracker.py — Real personal portfolio: stocks + MFs, holdings,
│   │                          XIRR, P&L, MF search via mfapi.in
│   ├── schemas.py           — Pydantic request/response models
│   ├── settings.py          — Read/write runtime configuration
│   ├── signals.py           — Latest signals, per-symbol history
│   ├── simulation.py        — Simulation logs, performance, go-live check
│   ├── sip_tracker.py       — SIP goals and projections
│   ├── stock_chat.py        — Avishk AI chat endpoints
│   ├── tax_calculator.py    — STCG/LTCG calculator (Budget 2024)
│   ├── trades.py            — Trade history, open/close
│   ├── websocket.py         — Real-time WebSocket push
│   └── zerodha.py           — Zerodha KiteConnect v3 (60 endpoints)
│
├── crawler/                 — Data ingestion
│   ├── earnings_crawler.py  — BSE/NSE earnings transcript PDF crawler with
│   │                          dynamic scrip-code resolution + pdfplumber extractor
│   ├── fii_dii_crawler.py   — NSE institutional flow scraper
│   ├── india_price_feed.py  — NSE-specific price ingestion
│   ├── ipo_crawler.py       — IPO data scraper (ipoalerts.in + Chittorgarh fallback)
│   ├── live_prices.py       — In-memory PRICE_CACHE, broadcast
│   ├── market_breadth.py    — A/D ratio, new highs/lows, breadth mood scoring
│   ├── news_crawler.py      — NewsAPI + Finnhub + RSS + FinBERT
│   ├── options_chain.py     — NSE options chain (circuit breaker for 404)
│   ├── price_feed.py        — yfinance + Alpha Vantage OHLCV
│   ├── sector_data.py       — SECTOR_DEFINITIONS + SECTOR_CACHE (mood scoring)
│   ├── sentiment.py         — FinBERT sentiment scoring wrapper
│   ├── zerodha_client.py    — Async KiteConnect HTTP client (singleton)
│   ├── zerodha_historical.py — Official Kite candle sync → save_candles_to_db
│   ├── zerodha_instruments.py — Hardcoded token map + async cache refresh
│   ├── zerodha_kite_lib.py  — kiteconnect library wrapper (40+ methods)
│   ├── zerodha_market.py    — NSE/INDEX_TOKENS, live prices, instrument map
│   ├── zerodha_ticker.py    — KiteTicker WebSocket → LIVE_TICKS + PRICE_CACHE
│   └── zerodha_websocket.py — KiteTicker WebSocket connection management
│
├── db/
│   ├── database.py          — Engine, session factory, Base, init_db
│   └── models.py            — All ORM models (32 tables incl. agent/doctor/earnings)
│
├── engine/                  — Trading logic
│   ├── agent/               — AI Trading Agent (Varsity-grounded multi-agent)
│   │   ├── __init__.py
│   │   ├── agent_loop.py            — Main orchestrator: per-bar cycle
│   │   ├── analyzer.py              — MarketAnalyzerAgent: features + regime
│   │   ├── backtester.py            — Event-bar backtester with Indian cost model
│   │   ├── decision_engine.py       — Fuses candidate + bear-case check (M12)
│   │   ├── execution.py             — Paper/live order placement
│   │   ├── fundamentals.py          — FundamentalsAgent: 0-100 grade (M3, 24h cache)
│   │   ├── indicators_agent.py      — Pure-numpy indicators for hot loops
│   │   ├── macro.py                 — MacroSectorAgent: -2..+2 bias (M8+M15)
│   │   ├── portfolio_context.py     — Open positions, drawdowns, cash
│   │   ├── risk_manager.py          — Unconditional veto: 7 gate types (M9)
│   │   ├── selector.py              — Strategy selector with R:R ≥ 1.5 gate
│   │   └── strategies/              — 4 Varsity-grounded strategies
│   │       ├── base.py              — Strategy ABC + TradeCandidate dataclass
│   │       ├── mean_reversion.py    — Short at BB upper (M2.3)
│   │       ├── pullback_trend.py    — Pullback to 20EMA (M2.2)
│   │       ├── range_reversal.py    — Long at BB lower with hammer
│   │       └── trend_breakout.py    — 20-bar breakout + volume (M2.1)
│   ├── allocation_engine.py — Asset allocation analyzer + risk profiler
│   ├── backtester.py        — Single-symbol historical backtest
│   ├── calendar_engine.py   — Indian market calendar (F&O, RBI, holidays)
│   ├── candlestick.py       — Pattern detection (Doji, Hammer, Engulfing…)
│   ├── deep_analysis.py     — Reasoning, trade setup, yfinance news, AI commentary
│   ├── earnings_summarizer.py — AI transcript summarizer (Groq → structured JSON)
│   ├── fundamental_analyzer.py — yfinance + Screener.in fundamental data
│   ├── india_signal_generator.py — NSE-specific signal generator
│   ├── india_specific.py    — India-specific signal adjustments
│   ├── indicators.py        — Full suite: RSI, MACD, BB, EMA, ATR, Stochastic,
│   │                          Supertrend, Ichimoku, ADX, VWAP+bands
│   ├── ipo_analyzer.py      — IPO scoring + Groq verdict
│   ├── llm_explainer.py     — Groq API + fallback explanation generator
│   ├── ml_predictor.py      — ML model predictor
│   ├── mutual_fund_analyzer.py — MF NAV trend + signal scoring
│   ├── portfolio_doctor.py  — Portfolio Doctor: 7 diagnostic modules +
│   │                          Dr. Arjun AI narrative + 0-100 health score
│   ├── portfolio_service.py — XIRR calculation, portfolio analytics, MF NAV cache
│   ├── risk_manager.py      — 6-check pre-trade gate + position sizing
│   ├── signal_generator.py  — Confluence scorer + TradingSignal dataclass
│   ├── sip_engine.py        — SIP projection engine
│   ├── stock_chat.py        — Avishk AI chat engine (intent, context, Groq)
│   ├── stock_context_builder.py — Live context assembly for AI chat
│   ├── tax_engine.py        — Indian capital gains tax engine (Budget 2024)
│   ├── zerodha_executor.py  — 10-rule real-order safety gate
│   └── zerodha_portfolio.py — Real holdings sync, P&L summary
│
├── paper_trading/
│   ├── virtual_wallet.py    — Virtual balance CRUD + daily snapshots
│   ├── trade_simulator.py   — Open/close trade lifecycle
│   ├── pnl_calculator.py    — Mark-to-market PnL
│   ├── position_tracker.py  — Open position queries + bulk price refresh
│   └── simulation_logger.py — Audit log writer + performance analyser
│
├── services/
│   └── kite_service.py      — Legacy Kite OAuth helper
│
├── tasks/
│   ├── celery_app.py        — Celery app + beat schedule (27 scheduled tasks)
│   ├── _db.py               — NullPool session factory for workers
│   ├── india_tasks.py       — India market + Kite + agent + earnings tasks
│   ├── market_scan.py       — OHLCV candle crawl task
│   ├── news_scan.py         — News + FinBERT task
│   └── paper_trade_loop.py  — Full trading cycle task
│
└── utils/
    ├── config.py            — Pydantic settings loaded from .env (incl. AGENT_*)
    ├── llm.py               — Shared Groq/Anthropic LLM helper utilities
    ├── logger.py            — Structured Python logging
    └── runtime_config.py    — Runtime-mutable config (used by /settings API)
```

### New backend modules summary (added recently)

| Module | Purpose |
|---|---|
| `api/agent.py` | 11 endpoints for the AI Trading Agent |
| `api/earnings.py` | 6 endpoints for AI earnings call analyzer |
| `api/portfolio_doctor.py` | 5 endpoints for AI Portfolio Doctor |
| `crawler/earnings_crawler.py` | BSE/NSE transcript fetch + dynamic BSE scrip resolver + PDF text extraction |
| `engine/agent/` | Full Varsity-grounded multi-agent system (12 files in package) |
| `engine/earnings_summarizer.py` | Groq-driven structured transcript summarizer |
| `engine/portfolio_doctor.py` | 7 diagnostic modules + Dr. Arjun narrative |
| `engine/portfolio_service.py` (updated) | MF support: `MF:{scheme_code}` symbol prefix, mfapi.in NAV cache |
| `engine/decision_router.py` (NEW) | Single paper/live routing gate for every signal |
| `api/portfolio_tracker.py` (updated) | `/search/mf`, `/search/mf/{code}/nav`, `/sync-zerodha` endpoints |
| `api/settings.py` (updated) | `GET/POST /settings/mode` runtime trade-mode toggle |
| `api/kite.py` (updated) | Transparent fallback to Zerodha v3 when legacy `KITE_API_KEY` unset |
| `crawler/live_prices.py` (updated) | `get_price()` Zerodha-first unified resolver with `source`+`age_seconds` |
| `engine/zerodha_portfolio.py` (updated) | `sync_zerodha_into_tracker()` mirrors Demat into tracker portfolio |
| `engine/portfolio_service.py` (updated) | `_holding_to_dict()` exposes `source` (MANUAL / MUTUAL_FUND / ZERODHA) |
| `utils/runtime_config.py` (updated) | `paper_mode` + confidence-threshold keys, runtime-mutable |
| `db/models.py` (updated) | 7 new tables: `portfolio_diagnoses`, `earnings_call_summaries`, `agent_decisions`, `agent_trades`, `agent_positions`, `agent_performance` |
| `tasks/india_tasks.py` (updated) | 3 new tasks: `run_agent_cycle`, `agent_eod_reconcile`, `fetch_earnings_transcripts` |
| `utils/config.py` (updated) | `AGENT_*` settings, `PAPER/LIVE_CONFIDENCE_THRESHOLD`, NSE watchlist + ₹1L paper balance defaults |

---

## Decision Router & Unified Trade Mode

`engine/decision_router.py` is the **single source of truth** for whether a trading signal becomes a paper trade or a real Zerodha order. Every execution path — the signal engine, the AI Trading Agent, and manual triggers — funnels through one function so behaviour is consistent and auditable.

### Routing flow

```
signal ─▶ route_decision(signal, session)
            │
            ├─ resolve_mode()         → PAPER | LIVE | DRY_RUN
            ├─ unified confidence gate (60% paper / 70% live, configurable)
            │
            ├─ PAPER   → paper_trading.trade_simulator.open_paper_trade()
            ├─ LIVE    → engine.zerodha_executor.place_real_order()
            └─ DRY_RUN → log decision to SimulationLog, never execute
```

`route_decision()` never raises — it always returns a `RoutingResult` with an `outcome` enum (`EXECUTED_PAPER`, `EXECUTED_LIVE`, `DRY_RUN_LOGGED`, `BLOCKED_LOW_CONFIDENCE`, `BLOCKED_NO_ZERODHA_TOKEN`, `BLOCKED_SAFETY_GATE`, `ERROR`) plus a human-readable `reason`.

### Mode resolution priority

1. `AGENT_DRY_RUN` env flag (always wins — used to validate new strategies)
2. `paper_mode` runtime-config DB override (set via `/api/v1/settings/mode`)
3. `.env` defaults — LIVE only when `PAPER_MODE=false` AND `ZERODHA_PAPER_MODE=false` AND `ZERODHA_ENABLED=true`

### Unified confidence gate

A single, configurable threshold replaces the three divergent thresholds that previously existed across the codebase:

| Mode | Default threshold | Setting key |
|---|---|---|
| PAPER | 60 | `paper_confidence_threshold` |
| LIVE | 70 (tighter) | `live_confidence_threshold` |
| DRY_RUN | none (logs all) | — |

Both are runtime-mutable via `PATCH /api/v1/settings`.

### Runtime mode toggle

- `GET /api/v1/settings/mode` → `{mode, is_paper, is_live, is_dry_run}`
- `POST /api/v1/settings/mode` → switch paper↔live **without restarting**
  - Going LIVE requires `confirm: "I_UNDERSTAND_REAL_MONEY"` **and** a valid Zerodha session, otherwise returns 400/409
- The Navbar `TradeModeBadge` shows the current mode (PAPER blue / LIVE red-pulsing / DRY_RUN amber) and toggles it with a double-confirmation dialog

---

## Signal Engine

The signal engine is the core of AutoTrade Pro. Located in `engine/signal_generator.py`, it combines three independent sources of evidence into a single directional decision.

### Scoring Weights

| Source | Weight | Range |
|---|---|---|
| Candlestick patterns | 35% | -100 to +100 |
| Technical indicators | 45% | -100 to +100 |
| News sentiment (FinBERT) | 20% | -100 to +100 |

A weighted sum above +30 triggers BUY; below -30 triggers SELL; everything else is HOLD.

### Candlestick Pattern Analysis (`engine/candlestick.py`)

Detects: Doji, Hammer, Inverted Hammer, Bullish/Bearish Engulfing, Morning Star, Evening Star, Shooting Star, Three White Soldiers, Three Black Crows. Each pattern has a reliability rating (LOW/MEDIUM/HIGH) contributing to a normalised score.

### Guard Clauses

BUY is blocked when RSI = OVERBOUGHT. SELL is blocked when RSI = OVERSOLD.

### Stop-Loss and Take-Profit

Stop-loss at `entry ± ATR × ATR_MULTIPLIER` (default 2.0). Take-profit at `entry ± risk × MIN_RISK_REWARD` (default 2.0), giving minimum 2:1 reward-to-risk.

---

## Technical Indicators

All indicators in `engine/indicators.py`. TA-Lib used when installed; pandas/numpy fallbacks always available.

| Indicator | Period | Score Contribution |
|---|---|---|
| RSI | 14 | ±20 (oversold/overbought) |
| MACD | 12/26/9 | ±25 (zero-line crossover) |
| Bollinger Bands | 20, 2σ | ±15 (position vs. bands) |
| EMA Trend | 20/50/200 | ±25 (alignment) |
| Stochastic | 14/3/3 | ±15 |
| Supertrend | 7, 3×ATR | ±20 (+ ±5 on direction flip) |
| Ichimoku | 9/26/52 | ±20 (price vs. cloud, cross, chikou) |
| ADX | 14 | ±10 modifier (amplifies/dampens direction) |
| VWAP ±1σ/±2σ | session | ±15 (intraday only; 0 on daily bars) |
| ATR | 14 | SL/TP sizing only |

---

## Deep Analysis Engine

`engine/deep_analysis.py` powers per-stock deep analysis.

### `generate_reasoning(sig, ltp)`
Returns three bullet lists: `bullish`, `bearish`, `neutral` — one reason per indicator, covering RSI, MACD, EMA trend, Ichimoku, Supertrend, ADX, Bollinger Bands, and VWAP.

### `build_trade_setup(sig, ltp, signal)`
Returns `entry_low/high`, `stop_loss`, `target_1/2`, `risk_reward`, `when_to_buy`, `when_to_sell`, `hold_strategy`.

### `fetch_stock_news(symbol)`
Uses **yfinance** as the primary source (nested under `content` key in the response). Falls back to Finnhub for US-listed stocks. Returns the 5 most recent headlines with title, source, URL, and sentiment.

### `groq_commentary(symbol, signal, score, reasoning, news)`
Sends a compact prompt to Groq `llama-3.1-8b-instant` for a 2–3 sentence AI outlook. Returns empty string on any failure.

---

## Risk Management

`engine/risk_manager.py` runs six sequential checks:

1. **Max concurrent positions** — rejects if open positions ≥ `MAX_OPEN_POSITIONS` (default 5)
2. **Daily loss circuit-breaker** — blocks all new trades if today's cumulative PnL loss exceeds `MAX_DAILY_LOSS × balance` (default 5%)
3. **Minimum confidence** — signals below 40% are rejected
4. **Risk:Reward ratio** — TP must be ≥ `MIN_RISK_REWARD × risk` (default 2×)
5. **Sufficient virtual balance** — 10% margin must not exceed 50% of balance
6. **No duplicate positions** — one open position per symbol

### Position Sizing

```
units     = (balance × risk_fraction) / |entry_price − stop_loss|
inr_value = units × entry_price
```

---

## Paper Trading Simulation

All simulation logic in `paper_trading/`. Virtual wallet starts at `PAPER_TRADING_BALANCE` (default ₹1,000). On every Celery tick, open positions are marked to market and SL/TP hits close them automatically. Daily performance snapshots power the equity curve chart.

---

## News and Sentiment

### News Crawler (`crawler/news_crawler.py`)

1. **yfinance** — primary source for Indian stocks (nested `content` key)
2. **NewsAPI** (`NEWSAPI_KEY`) — general financial headlines
3. **Finnhub** (`FINNHUB_KEY`) — useful for US-listed stocks only
4. **Free RSS feeds** — Yahoo Finance, ForexFactory (no key required)

### FinBERT Sentiment Scoring

When `torch` and `transformers` are installed, `ProsusAI/finbert` scores headlines POSITIVE/NEGATIVE/NEUTRAL. Headlines below 60% confidence or matching "wait-and-see" patterns are forced to NEUTRAL. Keyword heuristic used as fallback.

---

## LLM Integration

`engine/llm_explainer.py` — Groq `llama-3.1-8b-instant` for trade explanations. Full signal context sent as user message. Fallback joins top-three reasoning points into plain English when Groq is unavailable or not configured.

---

## Avishk AI Stock Analyst

The AI chat feature ("Avishk") is a conversational NSE stock analyst accessible via the `/chat` full page and the floating FAB present on every page.

### Architecture

```
User message
    │
    ▼
engine/stock_chat.py
    │  detect_intent()    — classifies: BUY_SELL, PRICE_CHECK, TECHNICAL,
    │                        FUNDAMENTAL, NEWS, SIGNAL, COMPARISON, GENERAL
    │  extract_symbols()  — finds .NS symbols and common name aliases
    │
    ▼
engine/stock_context_builder.py
    │  build_stock_context()  — parallel asyncio.gather() for:
    │    ├── PRICE_CACHE         (live price + change)
    │    ├── get_latest_candles  (200 candles for indicators)
    │    ├── compute_indicators  (full indicator suite)
    │    ├── detect_patterns     (candlestick patterns)
    │    ├── get_signal          (latest DB signal)
    │    ├── fetch_stock_news    (yfinance news)
    │    └── fundamentals        (yfinance info)
    │
    ▼
_call_groq()  — llama-3.1-8b-instant with context-packed system prompt
    │
    ▼  (fallback when no GROQ_API_KEY)
generate_no_ai_response()  — rule-based reply using indicator data
```

### SYMBOL_ALIASES

Common name to ticker mapping (e.g. `"reliance" → "RELIANCE.NS"`, `"hdfc bank" → "HDFCBANK.NS"`, `"sensex" → "^BSESN"`) allows natural language symbol references.

### Context Cards (`StockDataCard`)

Each AI response that references a stock includes a collapsible `StockDataCard` showing: live price + change%, metric pills (RSI, MACD trend, pattern, signal, composite score), up to 2 news headlines with sentiment dots, and a "View Chart" link.

### Chat Endpoints (`api/stock_chat.py`)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/chat/message` | Send message; returns reply + contexts dict |
| GET | `/api/v1/chat/suggest/{partial}` | Stock autocomplete from PRICE_CACHE + aliases |
| GET | `/api/v1/chat/quick-analysis/{symbol}` | Structured context without chat interface |

### Floating Chat Button

`FloatingChatButton` is mounted in `App.jsx` outside `<Routes>`, so it appears on every page except `/chat`. It maintains up to 20 mini messages with an unread badge.

---

## India Market Suite

Located in `api/india.py` and related crawlers.

### Market Status
Returns NSE open/closed state, current IST time, next open/close, and human-readable status.

### India VIX
yfinance `^INDIAVIX` — returns current VIX, 52-week range, volatility label (Low/Moderate/High/Extreme).

### FII/DII Flows
NSE institutional activity scraped daily. Returns 30 days of FII/DII net buy/sell in INR Crores with 5-day rolling summary.

### Options Chain (Circuit Breaker)
NSE options chain for NIFTY/BANKNIFTY. A module-level circuit breaker (`_last_nse_failure`, 30-minute backoff) prevents log spam when NSE's Akamai CDN blocks requests with HTTP 404. The API endpoint reads from cached DB snapshots rather than triggering live fetches.

### Sector Heatmap (`/api/v1/india/sectors`)
NSE sector index performance via yfinance sector indices. Drill-down to constituent stocks per sector. Sector rotation analysis shows momentum shift across 11 sectors.

### Market Breadth (`/api/v1/india/breadth`)
Advance/Decline ratio, new highs/lows, % of stocks above 200-DMA. Returns `nse_market_mood` label (STRONGLY_BULLISH → STRONGLY_BEARISH) for Sidebar indicator.

### Market Calendar (`/api/v1/india/calendar`)
Events in a rolling 90-day window:
- NSE F&O monthly and weekly expiry dates
- RBI Monetary Policy Committee meetings
- NSE trading holidays (from hardcoded IST calendar)
- Earnings announcements (from yfinance)
- IPO listings and subscription windows

### NSE Signals
Full technical signal scan on NSE large-cap and mid-cap symbols. Runs `compute_indicators()` + composite scoring. Filterable by category (`largecap`, `midcap`, `fno`, `all`).

### Backtest
Vectorised backtest over 1 year of daily data. Simulates paper trades on each signal crossover with configurable SL/TP multipliers. Returns per-symbol and aggregate statistics.

---

## My Portfolio (Stocks + Mutual Funds + Zerodha sync)

`api/portfolio_tracker.py` — manages the user's **real** holdings (distinct from the *Simulator* paper-trading wallet at `/portfolio`). Sidebar label: **My Portfolio** (`/portfolio-tracker`). Holds stocks/ETFs, mutual funds, and Zerodha-synced Demat positions in one unified view.

### Three sources, one ledger
Every row in `tracker_holdings` is tagged via `_holding_to_dict()` with a `source`:

| Source | Origin | Badge |
|---|---|---|
| `MANUAL` | User-entered stock/ETF | gray **M** |
| `MUTUAL_FUND` | `MF:{scheme_code}` rows | green **MF** |
| `ZERODHA` | Auto-synced from Demat (`notes="source:zerodha"`) | blue **Z** |

### Portfolios
Multiple named portfolios. A reserved `"Zerodha Demat"` portfolio is auto-created/updated by the sync. Each has holdings, total invested, current value, unrealised P&L, and XIRR.

### XIRR Calculation
`engine/portfolio_service.py` computes Extended Internal Rate of Return using cash-flow dates (buy transactions) and current market value as the final cash flow. Newton-Raphson iteration to 0.0001% tolerance.

### Live P&L
Current prices resolve through the unified `get_price()` layer — Zerodha KiteTicker first, then `PRICE_CACHE` (15-second yfinance refresh).

### Mutual Fund Holdings
Mutual fund units are stored in the same `tracker_holdings` table using a `MF:{scheme_code}` symbol prefix. NAV is fetched from mfapi.in with a 1-hour in-process cache. The Add Holding modal has two tabs:

- **Stock / ETF** — searches NSE symbols via the existing `/search/stocks` endpoint
- **Mutual Fund** — searches AMFI fund database via `/api/v1/portfolios/search/mf?q=<query>` (returns up to 15 matches with scheme code, name, and inferred category). On fund selection, current NAV auto-fetches via `/api/v1/portfolios/search/mf/{scheme_code}/nav` and pre-fills the purchase NAV field (editable for historical entries).

### Zerodha Demat sync
`POST /api/v1/portfolios/sync-zerodha` calls `engine/zerodha_portfolio.sync_zerodha_into_tracker()`, which mirrors live Demat holdings into the `"Zerodha Demat"` tracker portfolio (idempotent upsert, NSE-suffix normalised, tagged `source:zerodha`). A **Sync Zerodha** button in the My Portfolio header triggers it. Returns 409 if Zerodha is not connected.

### Endpoints
- `GET  /api/v1/portfolios/` — list all portfolios with summaries
- `POST /api/v1/portfolios/` — create portfolio
- `GET  /api/v1/portfolios/{id}` — full portfolio detail
- `POST /api/v1/portfolios/{id}/holdings` — add stock/MF holding (body accepts `symbol`, `quantity`, `price`, `trade_date`, `company_name`, `sector`)
- `POST /api/v1/portfolios/{id}/holdings/{hid}/sell` — sell holding
- `POST /api/v1/portfolios/sync-zerodha` — mirror Zerodha Demat into tracker
- `GET  /api/v1/portfolios/search/stocks?q=` — NSE stock search
- `GET  /api/v1/portfolios/search/mf?q=` — mutual fund search via mfapi.in
- `GET  /api/v1/portfolios/search/mf/{scheme_code}/nav` — fetch current NAV

---

## Portfolio Doctor — AI Health Analysis

`engine/portfolio_doctor.py` + `api/portfolio_doctor.py` — runs 7 deterministic diagnostic modules over a portfolio and produces a 0–100 health score with an AI-generated narrative.

### Diagnostic Modules
1. **Concentration** — flags single stocks > 25%, sectors > 40%, and all-equity portfolios
2. **Risk Quality** — checks fundamentals per holding (PE > 80, D/E > 3.0, negative ROE, revenue decline)
3. **Diversification** — minimum 8 holdings; missing asset classes (debt, gold, international)
4. **Tax Efficiency** — STCG liability, loss-harvesting opportunities, LTCG exemption utilisation, timing suggestions for the 12-month threshold
5. **Performance** — XIRR vs NIFTY 50 benchmark; persistent losers held > 6 months
6. **Sector Timing** — cross-references portfolio weights against current SECTOR_CACHE momentum
7. **Position Sizing** — dead weight (<1% positions), inconsistent sizing ratios

### Severity & Scoring
Each finding has severity: `CRITICAL` (-25 points), `WARNING` (-10), `INFO` (-3), or `GOOD` (+2). Final 0–100 score maps to letter grades A/B/C/D/F.

### AI Narrative
A "Dr. Arjun" persona is sent the structured findings via Groq llama-3.1-8b-instant. The model writes a 3-4 paragraph doctor's-style assessment with specific stock names and numbers. Falls back to rule-based summary when `GROQ_API_KEY` is unset.

### Endpoints
- `POST   /api/v1/doctor/diagnose` — full diagnosis (15–30s; calls fundamentals + AI)
- `GET    /api/v1/doctor/diagnose/{portfolio_id}` — latest cached diagnosis
- `GET    /api/v1/doctor/history/{portfolio_id}` — last 5 diagnoses for trend chart
- `GET    /api/v1/doctor/quick-check/{portfolio_id}` — fast check (no AI, < 3s)
- `DELETE /api/v1/doctor/diagnose/{diagnosis_id}` — delete a cached diagnosis

### Storage
`portfolio_diagnoses` table holds the score, grade, findings JSON, AI narrative, and quick wins. Sidebar polls `/diagnose/{id}` every 5 minutes to surface the current letter grade as a coloured badge.

---

## Earnings Call Analyzer — AI Transcript Summaries

`crawler/earnings_crawler.py` + `engine/earnings_summarizer.py` + `api/earnings.py` — fetches earnings call transcript PDFs from BSE/NSE filings under SEBI LODR regulations, extracts text, and produces structured AI summaries.

### Source Priority
1. **BSE filing API** — `api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData` with `SUBCATNAME='Earnings Call Transcript'` filter. PDFs live at `bseindia.com/xml-data/corpfiling/AttachLive/{uuid}.pdf`
2. **NSE announcements** — `nseindia.com/api/corp-info-equities-announcement?category=transcript` (uses the same two-step session pattern as `fii_dii_crawler.py`)
3. **Trendlyne fallback** — scrapes the conference-calls page when neither BSE nor NSE return results

### Dynamic BSE Scrip Resolution
For any NSE ticker outside the ~40-stock hardcoded `BSE_SCRIP_MAP`, `_resolve_bse_scrip_code()` calls BSE's `listofscripdata` search API and matches on the `scrip_id` field. Resolved codes are cached in-process. This means any NSE-listed company works without code changes.

### PDF Text Extraction
Primary: **pdfplumber** (text-layer PDFs). Fallback: **PyPDF2**. Cleaning step removes page numbers, merges hyphenated line breaks, collapses repeated newlines, and trims trailing disclaimers ("Forward-Looking Statements", "DISCLAIMER", "Safe Harbour Statement").

### AI Summarization
Sends the cleaned transcript text to Groq llama-3.1-8b-instant with a system prompt as Dr. Arjun (Indian equity research analyst). Returns a strict JSON object with:

- `financial_highlights` — 5 bullets with specific numbers (revenue, margins, segment perf, balance sheet, key operating metric)
- `management_guidance` — 4 bullets (revenue/margin/capex/strategic timeline)
- `key_risks` — 4 bullets (macro, margin pressure, competitive/regulatory, balance sheet)
- `analyst_questions` — 3 most-important Q&A concerns
- `strategic_updates` — 3 developments (acquisitions, new verticals, partnerships)
- `revenue_guidance`, `margin_guidance`, `capex_guidance`, `dividend_info`
- `management_tone` — `OPTIMISTIC` / `CAUTIOUS` / `NEUTRAL` / `NEGATIVE` with `tone_reason`
- `ai_confidence` — `HIGH` / `MEDIUM` / `LOW`

Transcripts > 80k chars are split: first 70% + last 30% retained (typical concall structure: management remarks + Q&A).

### Endpoints
- `GET  /api/v1/earnings/summary/{symbol}?quarter=Q4FY26&refresh=false`
- `GET  /api/v1/earnings/list/{symbol}` — available transcripts without summarization
- `GET  /api/v1/earnings/history/{symbol}` — all cached summaries
- `GET  /api/v1/earnings/recent?limit=10` — latest summaries across all companies
- `POST /api/v1/earnings/refresh/{symbol}?quarter=` — force re-summarize
- `GET  /api/v1/earnings/compare/{symbol}?quarters=Q4FY26&quarters=Q3FY26` — side-by-side trend

### Storage
`earnings_call_summaries` table — unique constraint on `(symbol, quarter)`, indexed by `(symbol, created_at)`. Daily Celery task `tasks.fetch_earnings_transcripts` runs at 20:00 IST to auto-summarize new filings for the top 10 NSE stocks.

---

## AI Trading Agent — Varsity-Grounded Autonomous System

`engine/agent/` — multi-agent cooperative system that trades NSE equities like a disciplined human professional. **Every rule is derived from the 17 Zerodha Varsity modules.** Paper-mode by default; live trading requires `AGENT_PAPER_MODE=false` AND `AGENT_ENABLED=true`.

### Architecture
```
MarketAnalyzer → StrategySelector → DecisionEngine
      ↓               ↓                ↓
FundamentalsAgent  MacroSectorAgent  Memory
      └─────────→ RiskManager ←────────┘
                       ↓
                ExecutionManager → Zerodha Kite
```

Per-bar flow (every 15 min during market hours):
1. `MarketAnalyzerAgent.compute_features(df)` → regime + 17 features
2. `MacroSectorAgent.bias(symbol)` → −2 … +2 (Varsity M8 + M15)
3. `FundamentalsAgent.get_cached_grade(symbol)` → 0–100 score + INVESTMENT/WATCHLIST/REJECT (M3)
4. `StrategySelectorAgent.propose(...)` → best candidate from 4 strategies
5. `DecisionEngine.fuse(...)` → final decision + bear-case check (M12)
6. `RiskManagerAgent.can_take_trade(...)` → unconditional veto (M9)
7. `AgentExecutionManager.execute(...)` → paper log or live order

### Regime Classifier (M2)
Dow Theory + ADX-based: `BULL_TRENDING` (ADX≥25 + EMA-aligned + +DI>−DI), `BEAR_TRENDING`, `HIGH_VOL_RANGE` (ATR > 1.5× 50-avg), `LOW_VOL_RANGE`, `RANGE`.

### Strategies
- **TrendBreakoutLong** (M2.1) — bull regime + breakout 20-bar high + volume spike + RSI 55-75 + ADX ≥ 20 + EMA20>EMA50
- **PullbackTrendLong** (M2.2) — bull regime + prev low touched 20EMA + close back above + RSI ≥ 40
- **MeanReversionShort** (M2.3) — range regime + close > BB upper + RSI ≥ 70 + bearish rejection candle
- **RangeReversalLong** — range regime + close ≤ BB lower + RSI ≤ 35 + hammer/bullish pattern

Selector picks the highest-confidence candidate with **R:R ≥ 1.5** (M9.4).

### Risk Manager (M9) — Unconditional Veto
7 gate types:
1. **Drawdown stops**: daily 3%, weekly 5%, monthly 10%
2. **Consecutive loss lockout**: 2 losses → halt new entries today
3. **Max daily entries**: 5 per day
4. **Position sizing**: max 1% equity at risk per trade
5. **Portfolio risk cap**: max 6% total open risk
6. **Cash buffer**: minimum 20% cash post-trade (M11)
7. **Correlation cluster**: blocks symbols correlated > 0.70 with open positions (M16)

### Decision Engine — Innerworth Check (M12)
Before finalising any decision, the engine writes the bear case. STRONG bear cases reduce confidence by 10 points. Examples:
- Buying into `BEAR_TRENDING` regime
- Macro bias ≤ −2 against the trade direction
- RSI > 70 at entry on a long signal

### Indian Cost Model (M7)
Backtester deducts realistic costs: brokerage min(₹20, 0.03%), STT 0.1%, NSE turnover 0.00345%, SEBI 0.0001%, stamp 0.015% (buy only), GST 18% on (brokerage + exchange + SEBI).

### Endpoints
- `GET  /api/v1/agent/status` — enabled flag, portfolio, decisions today
- `POST /api/v1/agent/cycle/trigger` — manual one-shot cycle
- `POST /api/v1/agent/backtest` — body: `{symbol, timeframe, fund_grade, macro_bias, days_back}`
- `GET  /api/v1/agent/decisions?limit=20&symbol=&action=`
- `GET  /api/v1/agent/trades?open_only=false`
- `GET  /api/v1/agent/performance` — win rate, profit factor, expectancy, equity curve
- `GET  /api/v1/agent/positions` — currently open positions
- `POST /api/v1/agent/positions/{symbol}/close` — manual exit at LTP
- `POST /api/v1/agent/signal/{symbol}` — on-demand signal without execution
- `PUT  /api/v1/agent/config` — requires header `X-Agent-Config-Update: yes`
- `GET  /api/v1/agent/rulebook` — all Varsity-derived rules as JSON

### Storage
- `agent_decisions` — every evaluation (traded, blocked, or skipped) with reasoning chain
- `agent_trades` — open + closed positions with P&L
- `agent_positions` — currently open (one row per symbol)
- `agent_performance` — daily snapshots

### Celery Schedule
- `tasks.run_agent_cycle` — every 15 min during NSE hours (Mon-Fri 03:45-10:00 UTC)
- `tasks.agent_eod_reconcile` — 15:25 IST (closes remaining positions, resets daily counters)

### Deployment Gate
Before flipping `AGENT_PAPER_MODE=false`:
1. Backtest all universe symbols → confirm positive expectancy
2. Paper trade for ≥ 30 days
3. Win rate > 45% AND profit factor > 1.3
4. Max paper drawdown < 8%
5. Start live at 10% of real capital

---

## Asset Allocation Analyzer

`api/allocation.py` — compares target vs. actual allocation for a given portfolio and risk profile (conservative/moderate/aggressive/custom).

Each risk profile has recommended % ranges for equity, debt, gold, and cash. The analyzer computes deviation from target for each asset class and generates rebalancing recommendations: BUY/SELL/HOLD per asset class with suggested INR amounts.

---

## SIP Goal Planner

`api/sip_tracker.py` — manages recurring SIP goals with projected corpus calculation.

### Projection Scenarios
Three scenarios computed per SIP goal:
- **Conservative** — historical CAGR minus 3%
- **Base** — historical CAGR
- **Optimistic** — historical CAGR plus 3%

Corpus projected using future-value-of-annuity formula. XIRR computed on completed instalments for performance tracking.

---

## Tax Calculator

`api/tax_calculator.py` — computes STCG/LTCG liability under Indian Budget 2024 rules.

### Budget 2024 Rules

| Holding Period | Type | Rate |
|---|---|---|
| < 12 months (equity/MF) | STCG | 20% |
| ≥ 12 months (equity/MF) | LTCG | 12.5% (above ₹1.25L exemption) |
| < 36 months (debt/other) | STCG | Slab rate |
| ≥ 36 months (debt/other) | LTCG | 12.5% |

Grandfathering for pre-2018 holdings (31 Jan 2018 fair market value as cost). P&L worksheet exports with per-trade STCG/LTCG breakdown.

---

## IPO Tracker

`api/ipo_tracker.py` — tracks upcoming, open, and recently listed IPOs.

Data source: `ipoalerts.in` free plan (750 req/month, 25 req/day, 1 IPO per request). When daily quota is exceeded (`ERR:QTAEXCEEDED`), cached data is returned with a rate-limit badge. Frontend shows a "loading" state distinguishable from "no IPOs found".

---

## Zerodha KiteConnect v3 Integration

A full paid-plan integration using the official `kiteconnect` Python library.

### Plan Details

| Feature | Free Plan | Paid Plan (₹500/month) |
|---|---|---|
| OAuth login | ✓ | ✓ |
| Holdings, positions, orders | ✓ | ✓ |
| Place/cancel orders | ✓ | ✓ |
| GTT (Good Till Triggered) | ✓ | ✓ |
| Mutual fund orders + SIPs | ✓ | ✓ |
| Live quotes + market depth | ✓ | ✓ |
| Historical OHLCV data | ✓ | ✓ |
| KiteTicker WebSocket | ✓ | ✓ |
| Order margin preview | ✓ | ✓ |
| Virtual contract note | ✓ | ✓ |

### Module Architecture

```
crawler/zerodha_kite_lib.py
    KiteClient — wraps kiteconnect.KiteConnect + KiteTicker
    get_kite() — module-level singleton

crawler/zerodha_instruments.py
    HARDCODED_TOKENS — 39 NSE equities + indices
    INSTRUMENT_CACHE — refreshed daily from Kite
    get_token(symbol)  — symbol → int token
    symbol_to_kite(s)  — "RELIANCE.NS" → "NSE:RELIANCE"

crawler/zerodha_ticker.py
    LIVE_TICKS     — {instrument_token: tick_data}
    on_ticks()     — updates LIVE_TICKS + PRICE_CACHE
    on_connect()   — subscribes all tokens in MODE_FULL
    start_kite_ticker() / stop_kite_ticker()

crawler/zerodha_historical.py
    sync_kite_candles()      — fetch + save to DB
    sync_all_nse_candles()   — all nse_symbols, 0.3s delay
    INTERVAL_MAP             — 1m/3m/5m/10m/15m/30m/1h/1d

engine/zerodha_executor.py
    place_real_order()           — 10-rule safety gate
    calculate_order_margins_preview()
    place_gtt_with_oco()         — full bracket setup

engine/zerodha_portfolio.py
    sync_real_holdings()         — Kite holdings → DB
    get_real_positions()         — day + net positions
    get_full_pnl_summary()       — demat + positions + margins
```

### OAuth Flow

1. `GET /api/v1/zerodha/login-url` → returns Kite OAuth URL
2. Frontend opens URL in new tab/popup
3. User logs in with Zerodha credentials + TOTP
4. Kite redirects to `ZERODHA_REDIRECT_URL` with `request_token`
5. `GET /api/v1/zerodha/callback` exchanges token → `access_token`
6. `access_token` persisted to `.env` via `_write_env()`
7. `ZERODHA_ENABLED=true` written to `.env`
8. Green success HTML page shown; user can close window

Token expires at 6:00 AM IST daily. `kite_check_token` Celery task runs at 6:05 AM to detect expiry and flag re-login.

### Real Order Safety Gate (`engine/zerodha_executor.py`)

10 rules checked in sequence before any live order:

1. `ZERODHA_PAPER_MODE` must be `false`
2. Zerodha connected + token valid
3. Signal confidence ≥ 60%
4. Order value ≤ 5% of available cash
5. NSE market must be open
6. Daily loss limit not breached
7. 3-second abort window with `logger.critical()` log
8. LIMIT orders with 0.5% slippage buffer (BUY: +0.5%, SELL: -0.5%)
9. Max 5 open positions
10. Tag every order `ATP_{signal_id}`

### GTT (Good Till Triggered)

**Single-leg GTT** — fires one LIMIT order when price crosses a threshold.

**Two-leg OCO GTT** — fires stoploss + target simultaneously; when one leg triggers, the other is cancelled automatically. Used by `place_gtt_with_oco()` to set up a complete bracket trade: BUY order + automatic SL/target exit.

### KiteTicker WebSocket

Subscribes all NSE symbols + indices in `MODE_FULL`. Each tick contains last_price, volume, OHLC, OI, OI day high/low, and 5-level market depth. `on_ticks()` syncs into `LIVE_TICKS` and updates the existing `PRICE_CACHE` so all other modules (signal engine, AI chat, API endpoints) benefit from real-time data when the ticker is running.

### Zerodha API Endpoints (`api/zerodha.py`) — 60 routes

**Auth:**

| Method | Path | Description |
|---|---|---|
| GET | `/login-url` | KiteConnect OAuth URL |
| GET | `/callback` | OAuth callback — exchanges token, returns HTML |
| GET | `/status` | Connection, paper mode, ticker, user info, cash |
| GET | `/token-status` | Token validity + expiry |
| POST | `/logout` | Invalidate session |
| GET | `/profile` | Kite user profile |
| GET | `/margins` | Equity + commodity margins |

**Orders:**

| Method | Path | Description |
|---|---|---|
| GET | `/orders` | Today's order book |
| GET | `/orders/{order_id}` | Order history |
| POST | `/orders` | Place real order (requires `X-Confirm-Real-Order: yes` + `PAPER_MODE=false`) |
| PUT | `/orders/{order_id}` | Modify pending order |
| DELETE | `/orders/{order_id}` | Cancel pending order |
| GET | `/trades` | Today's executed trades |
| GET | `/trades/{order_id}` | Trades for one order |

**GTT:**

| Method | Path | Description |
|---|---|---|
| GET | `/gtt` | All GTT triggers |
| GET | `/gtt/{trigger_id}` | One GTT |
| POST | `/gtt/single` | Single-leg GTT |
| POST | `/gtt/oco` | Two-leg OCO GTT |
| POST | `/gtt/bracket` | Full bracket (BUY + OCO GTT) |
| PUT | `/gtt/{trigger_id}` | Modify GTT |
| DELETE | `/gtt/{trigger_id}` | Delete GTT |

**Portfolio:**

| Method | Path | Description |
|---|---|---|
| GET | `/holdings` | Demat holdings with live P&L |
| GET | `/positions` | Intraday + overnight positions |
| POST | `/positions/convert` | Convert MIS ↔ CNC |
| GET | `/pnl` | Full P&L summary |
| POST | `/sync` | Force holdings sync to DB |

**Market Data:**

| Method | Path | Description |
|---|---|---|
| GET | `/quote?symbols=NSE:RELIANCE` | Full quote + market depth |
| GET | `/ohlc?symbols=NSE:TCS` | OHLC + last price |
| GET | `/ltp?symbols=NSE:INFY` | Last traded price only |
| GET | `/depth/{symbol}` | Top-5 bid/ask levels |
| GET | `/instruments` | Instrument list (exchange param) |
| GET | `/live-prices` | All LIVE_TICKS (WebSocket data) |

**Historical:**

| Method | Path | Description |
|---|---|---|
| GET | `/historical/{symbol}` | Candles for date range + interval |
| POST | `/historical/sync` | Force candle sync for all NSE symbols |

**Margins:**

| Method | Path | Description |
|---|---|---|
| POST | `/margins/preview` | Margin required for order list |
| POST | `/margins/basket` | Basket margins with F&O spread benefit |
| POST | `/charges/preview` | Virtual contract note (exact brokerage + STT + GST) |

**Ticker:**

| Method | Path | Description |
|---|---|---|
| POST | `/ticker/start` | Start KiteTicker WebSocket |
| POST | `/ticker/stop` | Stop KiteTicker |
| GET | `/ticker/status` | Running status + subscribed count |

**Mutual Funds:**

| Method | Path | Description |
|---|---|---|
| GET | `/mf/instruments` | All MF schemes on Kite |
| GET | `/mf/orders` | MF order book |
| GET | `/mf/orders/{order_id}` | One MF order |
| POST | `/mf/orders` | Place MF order (BUY by amount / SELL by units) |
| DELETE | `/mf/orders/{order_id}` | Cancel MF order |
| GET | `/mf/holdings` | Current MF holdings |
| GET | `/mf/sips` | All SIPs |
| GET | `/mf/sips/{sip_id}` | One SIP |
| POST | `/mf/sips` | Create recurring SIP |
| PUT | `/mf/sips/{sip_id}` | Modify / pause / cancel SIP |
| DELETE | `/mf/sips/{sip_id}` | Cancel SIP permanently |

**Alerts:**

| Method | Path | Description |
|---|---|---|
| GET | `/alerts` | All price alerts |
| POST | `/alerts` | Create alert |
| PUT | `/alerts/{alert_id}` | Modify alert |
| DELETE | `/alerts/{alert_id}` | Delete alert |

---

## Unified Market Data Layer

`crawler/live_prices.get_price(symbol)` is the single price resolver every module should call. It removes the drift that occurred when some pages read yfinance and others read the paid Zerodha feed.

### Priority chain

1. **Zerodha KiteTicker** (`crawler/zerodha_ticker.get_live_tick`) — sub-second WebSocket ticks, used only when `ZERODHA_ENABLED` and a token is present. Returns `source="zerodha_ticker"`, `age_seconds=0`.
2. **PRICE_CACHE** — yfinance-backed, refreshed every 15 s during market hours (60 s when closed). Returns `source="yfinance_cache"` with the real `age_seconds`.
3. **None** — caller decides whether to make a synchronous yfinance call.

`get_prices_batch(symbols)` is a thin batch wrapper. Because the return dict carries `source` and `age_seconds`, the frontend can render a freshness label (e.g. "Live" vs "15s delayed") and prefer broker data whenever the Zerodha session is valid.

> Note: LIVE_TICKS is keyed by Zerodha `instrument_token`, so `get_price()` resolves the symbol→token mapping through `zerodha_ticker.get_live_tick()` rather than reading the dict directly.

---

## Celery Background Tasks

27 scheduled tasks via Celery Beat (core + India market + Kite + AI features).

### Core Tasks

| Task | Schedule | Action |
|---|---|---|
| `scan_watchlist` | Every 30s | Fetch OHLCV candles via yfinance |
| `scan_news` | Every 5 min | Fetch headlines, run FinBERT |
| `paper_trade_loop` | Every 60s | Full cycle: update positions → signals → risk → open → explain |

### India Market Tasks (`tasks/india_tasks.py`)

| Task | Schedule | Action |
|---|---|---|
| `fetch_fii_dii` | Daily 17:00 UTC | NSE institutional flow scrape |
| `refresh_options_chain` | Every 15 min (market hours) | NSE options snapshot (with circuit breaker) |
| `refresh_sector_breadth` | Every 30 min | Sector performance + breadth data |
| `refresh_india_signals` | Daily 08:00 UTC | Full signal scan of NSE stocks |

### Kite Tasks (`tasks/india_tasks.py`)

| Task | Schedule (UTC) | IST equivalent | Action |
|---|---|---|---|
| `kite_sync_holdings` | Daily 15:35 | 21:05 IST | Sync real Zerodha holdings to DB |
| `kite_sync_candles` | Daily 10:00 | 15:30 IST | Sync official Kite candles for all NSE symbols |
| `kite_refresh_instruments` | Daily 02:30 | 08:00 IST | Refresh instrument token cache |
| `kite_check_token` | Daily 00:35 | 06:05 IST | Verify token validity; flag expired |
| `kite_start_ticker` | Daily 03:45 | 09:15 IST | Start KiteTicker WebSocket at market open |

### AI Feature Tasks

| Task | Schedule (UTC) | IST equivalent | Action |
|---|---|---|---|
| `fetch_earnings_transcripts` | Daily 14:30 | 20:00 IST | Auto-fetch + AI-summarize new transcripts for top 10 NSE stocks |
| `run_agent_cycle` | Every 15 min during market hours | 09:14-15:59 IST (Mon-Fri) | One AI Trading Agent evaluation cycle |
| `agent_eod_reconcile` | Daily 09:55 | 15:25 IST | Close remaining open positions + reset daily counters |

### NullPool Pattern

Celery workers use `NullPool` in `tasks/_db.py`. Standard connection pooling fails across `asyncio.run()` boundaries (each creates a new event loop; pooled connections become attached to the destroyed loop). NullPool creates a fresh engine per task call — intentionally less efficient but always correct.

---

## API Reference

All endpoints prefixed `/api/v1/`. Interactive docs at `/docs` (Swagger) or `/redoc`.

### Portfolio (`/api/v1/portfolio`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Virtual wallet: balance, equity, PnL, win rate, ROI |
| GET | `/positions` | Open virtual positions with unrealised P&L |
| GET | `/snapshots` | Last 30 daily equity snapshots |
| GET | `/stats` | Aggregated performance stats |
| POST | `/reset?confirm=true` | Reset wallet to starting balance |

### My Portfolio (`/api/v1/portfolios`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | All portfolios with summary |
| POST | `/` | Create new portfolio |
| GET | `/{id}` | Portfolio detail + holdings (each tagged with `source`) |
| PUT | `/{id}` | Update portfolio name/type |
| DELETE | `/{id}` | Delete portfolio |
| POST | `/{id}/holdings` | Add stock/MF holding |
| PUT | `/{id}/holdings/{hid}` | Update holding |
| DELETE | `/{id}/holdings/{hid}` | Delete holding |
| POST | `/{id}/holdings/{hid}/sell` | Sell holding |
| GET | `/{id}/xirr` | Compute XIRR for portfolio |
| POST | `/sync-zerodha` | Mirror live Zerodha Demat holdings into tracker |
| GET | `/search/stocks?q=` | NSE stock search |
| GET | `/search/mf?q=` | Mutual fund search (mfapi.in) |
| GET | `/search/mf/{code}/nav` | Latest NAV for a scheme |

### Trades (`/api/v1/trades`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Full trade history with P&L |
| GET | `/open` | Open trades only |
| GET | `/summary` | Aggregate counts and total P&L |
| GET | `/{id}` | Single trade detail |
| POST | `/{id}/close?price=X` | Manually close at given price |

### Signals (`/api/v1/signals`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Latest signal per watchlist symbol |
| GET | `/{symbol:path}` | Signal history for symbol |
| POST | `/trigger` | Manually trigger one signal cycle |

### News (`/api/v1/news`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Recent news with sentiment |
| GET | `/sentiment/{symbol:path}` | Average sentiment for symbol |

### Analytics (`/api/v1/analytics`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | PnL by symbol, win rate, drawdown history, ROI % |

### Simulation (`/api/v1/simulation`)

| Method | Path | Description |
|---|---|---|
| GET | `/logs` | Audit log entries |
| GET | `/performance` | Win rate, PnL, Sharpe-style metrics |
| GET | `/analysis` | Full strategy evaluation |
| GET | `/should-go-live` | Go-live readiness check |

### AI Chat (`/api/v1/chat`)

| Method | Path | Description |
|---|---|---|
| POST | `/message` | Chat with Avishk AI |
| GET | `/suggest/{partial}` | Stock symbol autocomplete |
| GET | `/quick-analysis/{symbol}` | Structured context without chat |

### India Market (`/api/v1/india`)

| Method | Path | Description |
|---|---|---|
| GET | `/market-status` | NSE open/closed state |
| GET | `/vix` | India VIX with volatility label |
| GET | `/fii-dii` | FII/DII flows (30 days) |
| GET | `/options-chain/{symbol}` | NSE options (NIFTY/BANKNIFTY) |
| GET | `/sectors/summary` | Sector performance summary |
| GET | `/sectors/{sector}` | Sector drill-down with constituents |
| GET | `/breadth/summary` | Market breadth + mood |
| GET | `/calendar/upcoming` | Upcoming market events |
| GET | `/mutual-funds` | Curated MF list with NAV |
| GET | `/mutual-funds/{code}/sip` | SIP projection with XIRR |
| POST | `/sip/project` | Custom SIP projection |
| GET | `/fundamentals/{symbol}` | Company fundamentals |
| GET | `/signals` | Technical signals for NSE stocks |
| POST | `/backtest` | Strategy backtest on NSE stocks |

### IPO Tracker (`/api/v1/ipo`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | IPO list (filter by status) |
| GET | `/stats/summary` | Count by status |

### Mutual Fund Tracker (`/api/v1/mf-tracker`)

| Method | Path | Description |
|---|---|---|
| GET | `/schemes` | Available MF schemes |
| GET | `/holdings` | User MF holdings |
| POST | `/holdings` | Add MF holding |
| GET | `/performance` | NAV performance + returns |

### SIP Tracker (`/api/v1/sip`)

| Method | Path | Description |
|---|---|---|
| GET | `/goals` | All SIP goals |
| POST | `/goals` | Create SIP goal |
| PUT | `/goals/{id}` | Update goal |
| DELETE | `/goals/{id}` | Delete goal |
| GET | `/goals/{id}/projection` | Future corpus projection |

### Tax Calculator (`/api/v1/tax`)

| Method | Path | Description |
|---|---|---|
| POST | `/calculate` | Compute STCG/LTCG for trade list |
| GET | `/worksheet` | Full P&L worksheet |

### Asset Allocation (`/api/v1/allocation`)

| Method | Path | Description |
|---|---|---|
| GET | `/analysis` | Actual vs. target allocation |
| GET | `/rebalancing` | Rebalancing recommendations |

### Settings (`/api/v1/settings`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Current runtime configuration |
| PATCH | `/` | Update runtime parameters (incl. `paper_mode`, confidence thresholds) |
| DELETE | `/{key}` | Reset one setting to its `.env` default |
| GET | `/keys` | List configurable keys and value types |
| GET | `/mode` | Current trade mode (PAPER / LIVE / DRY_RUN) |
| POST | `/mode` | Toggle paper↔live at runtime (live requires confirm string) |

### Portfolio Doctor (`/api/v1/doctor`)

| Method | Path | Description |
|---|---|---|
| POST | `/diagnose` | Full AI diagnosis (15–30s) |
| GET | `/diagnose/{portfolio_id}` | Latest cached diagnosis |
| GET | `/history/{portfolio_id}` | Last 5 diagnoses |
| GET | `/quick-check/{portfolio_id}` | Fast no-AI check |
| DELETE | `/diagnose/{diagnosis_id}` | Delete cached diagnosis |

### Earnings Call Analyzer (`/api/v1/earnings`)

| Method | Path | Description |
|---|---|---|
| GET | `/summary/{symbol}` | Fetch + AI-summarize latest earnings call |
| GET | `/list/{symbol}` | Available transcripts (no AI) |
| GET | `/history/{symbol}` | All cached summaries |
| GET | `/recent` | Latest summaries across all companies |
| POST | `/refresh/{symbol}` | Force re-summarize |
| GET | `/compare/{symbol}` | Side-by-side quarter comparison |

### AI Trading Agent (`/api/v1/agent`)

| Method | Path | Description |
|---|---|---|
| GET | `/status` | Agent enabled flag, portfolio, decisions count |
| POST | `/cycle/trigger` | Manual one-shot evaluation cycle |
| POST | `/backtest` | Run backtest with realistic costs |
| GET | `/decisions` | Recent decisions feed |
| GET | `/trades` | Open + closed trades |
| GET | `/performance` | Win rate, profit factor, equity curve |
| GET | `/positions` | Currently open positions |
| POST | `/positions/{symbol}/close` | Manual exit at LTP |
| POST | `/signal/{symbol}` | On-demand signal (no execution) |
| PUT | `/config` | Update config (requires `X-Agent-Config-Update: yes` header) |
| GET | `/rulebook` | All Varsity-derived rules as JSON |

### WebSocket (`/ws`)

Real-time price and portfolio updates pushed to connected frontend clients every 15 seconds (15 minutes when market is closed).

---

## Database Schema

PostgreSQL managed via SQLAlchemy ORM (`db/models.py`).

### `virtual_wallet`
Single-row paper account: balance, equity, realised/unrealised PnL, trade counters, peak balance, max drawdown.

### `paper_trades`
Simulated trade log: entry/exit price, SL, TP, size, PnL, AI reasoning, pattern, indicator snapshot, news sentiment at open.

### `open_positions`
Live snapshot for open trades (updated every Celery tick). Deleted on close.

### `candles`
OHLCV bars from yfinance/Kite. Unique on `(symbol, timeframe, timestamp)`. Indexed on `(symbol, timestamp)` and `(symbol, timeframe)`.

### `signals`
All generated signals with raw component scores, indicator snapshot JSON, and reasoning list.

### `news_items`
Headlines with FinBERT score (-1 to +1), label, source, URL, `tickers_affected` JSON array, publication time.

### `simulation_logs`
Append-only audit log: WALLET_CREATED, MARGIN_DEDUCTED, POSITION_CLOSED, SIGNAL_REJECTED, WALLET_RESET, REAL_ORDER_PLACED. Structured `details` JSON field per event type.

### `performance_snapshots`
Daily equity curve data. Unique on `date`. Columns: balance, equity, daily_pnl, trades, win_rate.

### `fii_dii_flows`
Daily NSE institutional flows. Unique on date.

### `options_chain_snapshots`
NIFTY/BANKNIFTY options data: ATM strike, PCR, max pain, total OI, support/resistance JSON arrays.

### `kite_instruments`
Kite instrument token cache. Downloaded daily from Kite instrument master. Columns: instrument_token, exchange_token, tradingsymbol, name, expiry, strike, instrument_type, segment, exchange, refreshed_at.

### Portfolio Tracker tables
`tracker_portfolios` — named portfolios with type and currency.
`tracker_holdings` — individual stock + MF holdings (MF rows use `MF:{scheme_code}` symbol prefix).
`tracker_transactions` — buy/sell transactions for XIRR computation.

### `portfolio_diagnoses`
Persisted Portfolio Doctor reports: overall_score, overall_grade, summary, findings JSON, ai_narrative, quick_wins, data_snapshot. Indexed by `(portfolio_id, created_at)` for history charts.

### `earnings_call_summaries`
AI-generated transcript summaries. Unique constraint on `(symbol, quarter)`. Stores full 5-section breakdown (financial_highlights, management_guidance, key_risks, analyst_questions, strategic_updates) + tone, confidence, source PDF URL.

### Agent tables
- `agent_decisions` — every evaluation the agent made (traded, blocked, skipped) with full reasoning chain
- `agent_trades` — open + closed positions with stop/target/exit_reason/pnl
- `agent_positions` — currently open positions, one row per symbol
- `agent_performance` — daily snapshots: total trades, win rate, profit factor, max DD, sharpe, equity_end

---

## Frontend — Structure and Pages

```
autotrade-frontend/src/
├── App.jsx              — Router, Sidebar + Navbar layout, FloatingChatButton
├── index.css            — Tailwind + CSS custom properties + chat/signal animations
│
├── api/
│   └── client.js        — All API fetch functions
│
├── components/
│   ├── Navbar.jsx        — Live clock, balance/PnL ticker, Kite token expiry warning,
│   │                       TradeModeBadge (PAPER/LIVE/DRY_RUN toggle)
│   ├── Sidebar.jsx       — Nav with live status dots (market, watchlist, breadth,
│   │                       sector strip, portfolio value, allocation, IPO, Zerodha)
│   ├── chat/
│   │   ├── ChatInput.jsx      — Textarea with stock autocomplete + suggestion pills
│   │   ├── ChatMessage.jsx    — Rich renderer (bold, ₹, %, BUY/SELL badges, .NS chips)
│   │   ├── ChatSidebar.jsx    — Market pulse tickers, active context cards, quick Qs
│   │   ├── FloatingChatButton.jsx — FAB with mini drawer, unread badge
│   │   └── StockDataCard.jsx  — Metric pills, news ticker, price in chat messages
│   ├── AnalyticsPanel.jsx
│   ├── CandlestickChart.jsx   — Equity curve (₹ formatted, INR locale)
│   ├── MetricCard.jsx         — format="count" / "plain" / default (₹)
│   ├── OpenPositions.jsx
│   ├── PortfolioCard.jsx
│   ├── TradeLog.jsx
│   └── ...
│
├── hooks/
│   ├── useStockChat.js   — Avishk AI chat state + sendMessage + clearChat
│   ├── useZerodha.js     — Kite status, holdings, positions, orders, GTT,
│   │                       P&L, margins, MF, SIPs — 30s auto-poll
│   ├── useLiveMarket.js  — Live price WebSocket state
│   ├── usePortfolio.js   — Virtual wallet, 10s poll
│   ├── useSignals.js     — Latest signals, 30s poll
│   ├── useTrades.js      — Trade history, 15s poll
│   └── useWebSocket.js   — WebSocket connection + handler
│
└── pages/
    ├── Dashboard.jsx        — Portfolio + equity chart + positions + signals
    ├── Trades.jsx           — Capital deployed, open positions, trade history
    ├── Portfolio.jsx        — "Simulator" — virtual paper-trading wallet (sidebar: Simulator)
    ├── PortfolioTracker.jsx — "My Portfolio" — real holdings (manual + MF + Zerodha-synced) with source badges, live P&L, XIRR, Sync Zerodha button, Doctor tab
    ├── PortfolioDoctor.jsx  — AI health diagnosis page: 7 modules + Dr. Arjun narrative
    ├── EarningsAnalyzer.jsx — Earnings call transcript AI analyzer with quarter comparison
    ├── TradingAgent.jsx     — AI Trading Agent: status, decisions, positions, backtest, rulebook
    ├── Analytics.jsx        — Charts: equity curve, P&L by symbol, win/loss pie
    ├── News.jsx             — News feed with sentiment
    ├── Simulation.jsx       — Simulation logs + go-live checker
    ├── StockChat.jsx        — Full Avishk AI chat page (9 tabs, sidebar)
    ├── IndiaMarket.jsx      — VIX, FII/DII, options, sectors
    ├── IndiaFundamentals.jsx — NSE fundamentals
    ├── IndiaSignals.jsx     — India technical signals
    ├── LiveMarket.jsx       — Live prices via WebSocket
    ├── MarketBreadth.jsx    — A/D ratio, new highs/lows, breadth heatmap
    ├── SectorHeatmap.jsx    — Sector heatmap with drill-down + rotation
    ├── MarketCalendar.jsx   — F&O expiry, RBI MPC, holidays, earnings, IPOs
    ├── MutualFunds.jsx      — MF NAV, returns, SIP calculator
    ├── SIPTracker.jsx       — SIP goals + projected corpus
    ├── TaxCalculator.jsx    — STCG/LTCG calculator (Budget 2024)
    ├── AssetAllocation.jsx  — Target vs. actual allocation + rebalancing
    ├── IPOTracker.jsx       — IPO status, subscription, GMP
    ├── Backtest.jsx         — NSE backtest
    ├── Watchlist.jsx        — Stock watchlist with signals
    ├── Chart.jsx            — Candlestick chart page
    ├── Zerodha.jsx          — Kite: connect, holdings, orders, scanner, MF
    ├── Settings.jsx         — Runtime config editor
    └── Documentation.jsx    — This documentation page (loads markdown)
```

### Component sub-packages (`src/components/`)

| Folder | Purpose |
|---|---|
| `agent/` | `DecisionCard`, `BacktestPanel` — AI Trading Agent UI |
| `allocation/` | Asset allocation widget + questionnaire modal |
| `breadth/` | Market breadth A/D bars + heatmap cells |
| `calendar/` | Calendar event cards + expiry countdown |
| `chart/` | Candlestick chart, OHLC tooltip, indicator overlays |
| `chat/` | Avishk AI chat: `ChatInput`, `ChatMessage`, `ChatSidebar`, `FloatingChatButton`, `StockDataCard` |
| `doctor/` | `HealthScoreCard`, `FindingCard`, `ScoreHistory`, `DiagnosisSettings`, `ProgressOverlay` |
| `earnings/` | `ToneIndicator`, `SummarySection`, `GuidanceCards`, `QuarterSelector`, `ComparisonView`, `EarningsCard` |
| `heatmap/` | Sector heatmap cells + drill-down |
| `ipo/` | IPO list cards + analysis panel |
| `market/` | Live market tickers, NSE index strip |
| `mutualfunds/` | MF list + SIP calculator |
| `portfolio/` | `AddHoldingModal` (two-tab: Stock/ETF + Mutual Fund), `HoldingsTable`, `SummaryCards`, `AllocationCharts`, `SellModal`, `TransactionsTab`, `TaxSummaryPanel`, `PortfolioSelector` |
| `sip/` | SIP goal cards + projection charts |
| `tax/` | Tax calculator: `StandaloneCalculator` |
| `watchlist/` | Watchlist toolbar, row, detail panel, alerts bar |

---

## Frontend Components

### Sidebar (`components/Sidebar.jsx`)
Fixed-width navigation with live status indicators per item:
- **Live Market** — pulsing green/red dot (NSE market open/closed)
- **Watchlist** — BUY signal count badge
- **Breadth** — market mood dot (green/red/gray)
- **Sector Heatmap** — 4-column sector strip (colored bars)
- **My Portfolio** — real-holdings value badge (renamed from "My Holdings")
- **Simulator** — paper-trading wallet (renamed from "My Portfolio" to disambiguate)
- **Portfolio Doctor** — health letter-grade badge (A–F)
- **Earnings AI** — recent summary count badge
- **Trading Agent** — agent status dot (gray=off, blue=paper, green pulsing=live)
- **Market Calendar** — upcoming events count
- **Asset Allocation** — deviation severity dot (green/amber/red)
- **IPO Tracker** — open IPO count badge
- **Zerodha** — connection dot (amber=disconnected, blue=paper, green pulsing=live)
- **Avishk AI Analyst** — accent gradient item with pulsing green dot (always at top)

### MetricCard (`components/MetricCard.jsx`)
Accepts `format` prop: `"count"` (no ₹, locale number), `"plain"` (decimal), default (₹ with L/Cr suffix).

### Chat Components (`components/chat/`)
- **ChatInput** — auto-resize textarea, stock autocomplete dropdown (280ms debounce, fetches `/api/v1/chat/suggest/{partial}`), suggestion pills when empty
- **ChatMessage** — parses `**bold**`, `*italic*`, BUY/SELL/HOLD badges, ₹price, ±% coloring, `.NS` clickable chips; includes collapsible `StockDataCard`
- **StockDataCard** — metric pills (RSI, MACD, Trend, Pattern, Signal, Score), news sentiment dots, "View Chart" footer link
- **ChatSidebar** — live NIFTY/BANKNIFTY/IT/USDINR tickers (15s refresh), active context cards, quick question shortcuts
- **FloatingChatButton** — FAB mounted outside `<Routes>` in App.jsx; hides on `/chat` page; mini drawer with up to 20 messages

---

## Frontend Hooks

### `useStockChat.js`
Manages Avishk AI chat state. `sendMessage(text)` posts to `/api/v1/chat/message` with last 10 messages as history. `clearChat()` resets to welcome message. Exposes `messages`, `input`, `loading`, `error`, `noAiBanner`, `activeContexts`, `suggestions`.

### `useZerodha.js`
Zerodha data hub with 30-second auto-poll. `loadAllData()` uses `Promise.allSettled` to fetch 8 endpoints in parallel (holdings, positions, orders, GTT, P&L, margins, MF holdings, SIPs). Actions: `getLoginUrl`, `previewMargins`, `cancelOrder`, `deleteGtt`, `syncHoldings`, `startTicker/stopTicker`.

### `usePortfolio.js`
Polls `/api/v1/portfolio/` every 10 seconds. Used by Navbar and Dashboard.

### `useSignals.js`
Polls `/api/v1/signals/` every 30 seconds.

### `useTrades.js`
Polls `/api/v1/trades/` every 15 seconds.

### `useWebSocket.js`
Manages WebSocket to `ws://localhost:8000/ws`. Handles reconnection and message parsing.

### `useLiveMarket.js`
Live price state for the Live Market page. Combines WebSocket updates with REST polling fallback.

### `usePortfolioTracker.js`
Personal portfolio state: lists portfolios, holdings (stocks + MFs), transactions. Applies live prices client-side for instant P&L updates.

### `usePortfolioDoctor.js`
Doctor diagnosis state. `runDiagnosis()` triggers the full AI cycle (15-30s) with rotating progress messages. Auto-loads latest cached diagnosis + history on mount.

### `useEarnings.js`
Earnings analyzer state for a given symbol. `fetchSummary(quarter, refresh)` triggers AI summarization with progress messages. Includes `loadComparison(quarters)` for side-by-side trend analysis.

### `useAgent.js`
AI Trading Agent state: status, decisions, trades, positions, performance. Auto-polls every 30s. Exposes `triggerCycle()`, `closePosition()`, `runBacktest()`, `updateConfig()`.

### Additional hooks
- `useAllocation.js` — Asset allocation analyzer state
- `useBreadth.js` — Market breadth state
- `useCalendar.js` — Market calendar events
- `useIPOTracker.js` — IPO list + analysis state
- `useMFTracker.js` — Mutual fund tracker (separate from portfolio tracker)
- `useSectors.js` — Sector heatmap state
- `useSIPTracker.js` — SIP goals state
- `useTaxCalculator.js` — Tax calculator inputs + computed P&L
- `useWatchlist.js` — Watchlist state with signal scoring

---

## Configuration and Environment Variables

```
# Database (Supabase transaction-mode pooler — required)
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db

# Redis / Upstash (required for Celery)
REDIS_URL=rediss://default:token@host:6380

# LLM (optional — fallback used when absent)
GROQ_API_KEY=                        # for Avishk AI + signal explanations

# Market data (optional — yfinance works without keys)
ALPHA_VANTAGE_KEY=
FINNHUB_KEY=                         # useful for US stocks only on free tier

# News (optional — RSS works without keys)
NEWSAPI_KEY=

# Zerodha KiteConnect v3 (required for Zerodha page)
ZERODHA_API_KEY=ccmnshilnxxz9htr
ZERODHA_API_SECRET=s2434gtj3q9h2biubapi5ic8oypadt0b
ZERODHA_ACCESS_TOKEN=                # auto-filled after login
ZERODHA_REDIRECT_URL=http://localhost:8000/api/v1/zerodha/callback
ZERODHA_ENABLED=false                # set true after first successful login
ZERODHA_PAPER_MODE=true              # SAFETY: set false ONLY for real orders

# Paper trading parameters
PAPER_TRADING_BALANCE=100000.0       # ₹1L — realistic Indian retail starter
MAX_RISK_PER_TRADE=0.02              # 2% of balance per trade
MAX_OPEN_POSITIONS=5
MAX_DAILY_LOSS=0.05                  # halt when down 5% on the day

# Signal / trade sizing
ATR_MULTIPLIER=2.0
MIN_RISK_REWARD=2.0

# Decision router — unified paper/live confidence gate
PAPER_CONFIDENCE_THRESHOLD=60        # min confidence for a paper trade
LIVE_CONFIDENCE_THRESHOLD=70         # tighter gate for live Zerodha orders
AGENT_DRY_RUN=false                  # if true, agent logs decisions but never executes
# Runtime override: POST /api/v1/settings/mode flips paper_mode without restart

# AI Trading Agent (Varsity-grounded autonomous system)
AGENT_ENABLED=false                  # master kill-switch, off by default
AGENT_PAPER_MODE=true                # paper-trade by default
AGENT_EQUITY=500000                  # ₹5L starting capital
AGENT_MAX_RISK_PER_TRADE=0.01        # 1% per trade (Varsity M9.1)
AGENT_MAX_OPEN_RISK=0.06             # 6% total open risk
AGENT_DAILY_DD_STOP=0.03             # halt after 3% daily loss
AGENT_WEEKLY_DD_STOP=0.05            # halt after 5% weekly loss
AGENT_MONTHLY_DD_STOP=0.10           # halt after 10% monthly loss
AGENT_CASH_BUFFER_MIN=0.20           # always keep 20% cash
AGENT_MAX_NEW_ENTRIES_DAY=5
AGENT_CONSEC_LOSS_LOCKOUT=2
AGENT_CONFIDENCE_THRESHOLD=70
AGENT_TIMEFRAME=15m
AGENT_WARMUP_BARS=210
AGENT_SESSION_START=09:20
AGENT_SESSION_END=15:20

# Watchlists (comma-separated) — NSE-focused defaults
WATCHLIST_FOREX=USD/INR,EUR/INR,GBP/INR,JPY/INR
WATCHLIST_STOCKS=RELIANCE.NS,TCS.NS,HDFCBANK.NS,INFY.NS,ICICIBANK.NS,SBIN.NS,BHARTIARTL.NS,KOTAKBANK.NS,LT.NS,ITC.NS
```

### Zerodha Setup

1. Create an app at `https://developers.kite.trade`
2. Set redirect URL in Developer Console to `http://localhost:8000/api/v1/zerodha/callback`
3. Copy API key + secret to `.env`
4. Restart backend
5. Open `/zerodha` → click "Login with Zerodha" → complete OAuth
6. Green success page confirms connection; `ZERODHA_ACCESS_TOKEN` and `ZERODHA_ENABLED=true` auto-written to `.env`
7. For real trading (not paper): set `ZERODHA_PAPER_MODE=false` in `.env` and restart

---

## Infrastructure

### PostgreSQL via Supabase
Transaction-mode pooler (port 6543). `statement_cache_size=0` in engine connect args disables prepared statements (required by pgBouncer transaction mode).

### Redis via Upstash
Serverless Redis over TLS (`rediss://`). `ssl_cert_reqs=CERT_NONE` in Celery config. 1 MB command-size limit — task payloads are minimal.

### Celery Beat
`celerybeat-schedule` file persists beat scheduler state. `start.sh` deletes it on startup to prevent stale schedule.

---

## Development Setup

### Backend

```bash
cd autotrade-backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill DATABASE_URL and REDIS_URL at minimum
./start.sh             # starts Uvicorn + Celery worker + Celery beat
```

### Frontend

```bash
cd autotrade-frontend
npm install
npm run dev   # Vite dev server on localhost:5173
```

Vite proxies `/api/v1/` and `/ws/` to `localhost:8000` via `vite.config.js`.

---

## Known Constraints and Design Decisions

### asyncpg 32,767 parameter limit
Bulk candle inserts are chunked at 3,000 rows (3,000 × 8 = 24,000 params) to stay under the PostgreSQL bind-parameter limit.

### NullPool for Celery workers
`asyncio.run()` in each Celery task creates a new event loop. Standard connection pooling attaches connections to the previous loop and fails on reuse. `NullPool` (fresh engine per call) is intentionally less efficient but always correct.

### `/{symbol:path}` route parameter
Forex symbols like `EUR/USD` contain slashes. The `:path` converter captures slashes as part of the parameter value.

### NSE Options Chain circuit breaker
NSE's Akamai CDN returns HTTP 404 when rate-limited or geo-blocked. A module-level `_last_nse_failure` timestamp enforces a 30-minute backoff. The options chain API endpoint reads from cached DB snapshots (set by the Celery task) rather than triggering live fetches on every request.

### ipoalerts.in free plan limits
25 requests/day, 1 IPO per request. When quota is exceeded (`ERR:QTAEXCEEDED`), cached data is returned. The frontend distinguishes between "rate limited but data available" and "no IPOs found" using the `api_key_configured` flag in the response.

### Zerodha token expires daily at 6 AM IST
The `kite_check_token` Celery task runs at 6:05 AM UTC (12:05 PM IST). When the token is expired, `ZERODHA_ENABLED` is set to `false` in `.env` and a warning is logged. The Sidebar Zerodha dot turns amber; the `/zerodha` page shows a re-login prompt.

### `_write_env()` pattern
`crawler/zerodha_kite_lib.py` writes `ZERODHA_ACCESS_TOKEN` and `ZERODHA_ENABLED` directly to the `.env` file after a successful OAuth exchange. This allows the token to survive backend restarts without a database. The path is resolved relative to the `crawler/` directory (`Path(__file__).parent.parent / ".env"`).

### Kite `place_order()` paper mode gate
Every call to `KiteClient.place_order()` checks `settings.ZERODHA_PAPER_MODE` before making any HTTP request. Raising `RuntimeError` before the network call ensures no accidental orders even if `ZERODHA_ENABLED=true` and the token is valid. Real order placement additionally requires the `X-Confirm-Real-Order: yes` HTTP header at the API layer.

### VWAP on daily data
VWAP is an intraday metric that resets each session. On daily bars, the VWAP score contribution is set to 0 and a debug message is emitted (not a warning). Scans never fail due to this.

### yfinance news nested structure
The yfinance news API returns items with content nested under a `"content"` key: `item["content"]["title"]`, `item["content"]["canonicalUrl"]["url"]`, `item["content"]["provider"]["displayName"]`. The deep analysis engine handles both the nested format and the flat legacy format.

### Avishk AI falls back gracefully
When `GROQ_API_KEY` is not configured, `process_chat_message()` calls `generate_no_ai_response()` which returns a rule-based reply using the indicator data already assembled by `build_stock_context()`. The frontend shows an amber "Basic mode" banner but the chat remains functional.

### Paper Trading Disclaimer
"PAPER TRADING — VIRTUAL CURRENCY ONLY" appears in: startup banner, health endpoint, every wallet log line, LLM system prompt, API description, Navbar, Sidebar badge, and Avishk AI system prompt. Real order execution requires `ZERODHA_PAPER_MODE=false` AND `ZERODHA_ENABLED=true` AND `X-Confirm-Real-Order: yes` header simultaneously.

---

*Documentation last updated May 2026 — covers all features through the Zerodha KiteConnect v3 paid-plan integration, Avishk AI Stock Analyst, Personal Portfolio Tracker, Market Calendar, Sector Heatmap, SIP Goal Planner, Tax Calculator (Budget 2024), Asset Allocation Analyzer, and IPO Tracker.*
