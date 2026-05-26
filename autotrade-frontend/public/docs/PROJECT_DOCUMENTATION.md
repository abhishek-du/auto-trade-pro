# AutoTrade Pro — Complete Project Documentation

> **Paper Trading Only** — This system uses virtual/simulated currency exclusively. No real money is ever involved at any stage. Real order execution requires `ZERODHA_PAPER_MODE=false` AND `ZERODHA_ENABLED=true` AND the `X-Confirm-Real-Order: yes` header simultaneously.

---

## Table of Contents

- Project Overview
- Architecture
- Technology Stack
- Backend — Structure and Modules
- Signal Engine
- Technical Indicators
- Deep Analysis Engine
- Risk Management
- Paper Trading Simulation
- News and Sentiment
- LLM Integration
- Avishk AI Stock Analyst
- India Market Suite
- Zerodha KiteConnect v3 Integration
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
- **Avishk AI Stock Analyst** — conversational AI with live NSE context (price, indicators, news, signals), powered by Groq LLM with rule-based fallback
- **India Market Suite** — FII/DII flows, options chain, sector heatmap, market breadth, India VIX, NSE signals, market calendar (F&O expiry, RBI MPC, holidays, earnings, IPOs)
- **Personal Portfolio Tracker** — real holdings with live P&L, XIRR, allocation and risk analytics
- **Asset Allocation Analyzer** — target vs. actual allocation with rebalancing recommendations
- **SIP Goal Planner** — SIP projections with XIRR and scenario analysis
- **Tax Calculator** — STCG/LTCG under Budget 2024 rules with P&L worksheet
- **IPO Tracker** — live IPO status, GMP, subscription data
- **Mutual Fund Tracker** — NAV history, SIP analysis, signal scoring
- **Zerodha KiteConnect v3** — full paid-plan integration: OAuth, real holdings sync, 60 API endpoints, KiteTicker WebSocket, GTT/OCO orders, MF orders/SIPs, margin preview, virtual contract note, alerts

All data flows through a FastAPI backend with Celery workers; the React SPA reads over REST and WebSocket.

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
│   ├── analytics.py         — Performance stats + chart data
│   ├── allocation.py        — Asset allocation analysis + rebalancing
│   ├── india.py             — India market: FII/DII, options, calendar,
│   │                          breadth, heatmap, signals, backtest
│   ├── ipo_tracker.py       — IPO status, GMP, subscription data
│   ├── kite.py              — Legacy Kite portfolio tracker
│   ├── mf_tracker.py        — Mutual fund tracker (holdings, SIP analysis)
│   ├── news.py              — News feed + per-symbol sentiment
│   ├── portfolio.py         — Virtual wallet: summary, positions, snapshots
│   ├── portfolio_tracker.py — Real personal portfolio: holdings, XIRR, P&L
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
│   ├── price_feed.py        — yfinance + Alpha Vantage OHLCV
│   ├── india_price_feed.py  — NSE-specific price ingestion
│   ├── live_prices.py       — In-memory PRICE_CACHE, broadcast
│   ├── news_crawler.py      — NewsAPI + Finnhub + RSS + FinBERT
│   ├── fii_dii_crawler.py   — NSE institutional flow scraper
│   ├── options_chain.py     — NSE options chain (circuit breaker for 404)
│   ├── zerodha_client.py    — Async KiteConnect HTTP client (singleton)
│   ├── zerodha_market.py    — NSE/INDEX_TOKENS, live prices, instrument map
│   ├── zerodha_kite_lib.py  — kiteconnect library wrapper (40+ methods)
│   ├── zerodha_instruments.py — Hardcoded token map + async cache refresh
│   ├── zerodha_ticker.py    — KiteTicker WebSocket → LIVE_TICKS + PRICE_CACHE
│   └── zerodha_historical.py — Official Kite candle sync → save_candles_to_db
│
├── db/
│   ├── database.py          — Engine, session factory, Base, init_db
│   └── models.py            — All ORM models
│
├── engine/                  — Trading logic
│   ├── candlestick.py       — Pattern detection (Doji, Hammer, Engulfing…)
│   ├── deep_analysis.py     — Reasoning, trade setup, yfinance news, AI commentary
│   ├── indicators.py        — Full suite: RSI, MACD, BB, EMA, ATR, Stochastic,
│   │                          Supertrend, Ichimoku, ADX, VWAP+bands
│   ├── llm_explainer.py     — Groq API + fallback explanation generator
│   ├── mutual_fund_analyzer.py — MF NAV trend + signal scoring
│   ├── portfolio_service.py — XIRR calculation, portfolio analytics
│   ├── risk_manager.py      — 6-check pre-trade gate + position sizing
│   ├── signal_generator.py  — Confluence scorer + TradingSignal dataclass
│   ├── stock_chat.py        — Avishk AI chat engine (intent, context, Groq)
│   ├── stock_context_builder.py — Live context assembly for AI chat
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
├── tasks/
│   ├── celery_app.py        — Celery app + beat schedule (13 tasks)
│   ├── _db.py               — NullPool session factory for workers
│   ├── india_tasks.py       — India market + Kite Celery tasks
│   ├── market_scan.py       — OHLCV candle crawl task
│   ├── news_scan.py         — News + FinBERT task
│   └── paper_trade_loop.py  — Full trading cycle task
│
└── utils/
    ├── config.py            — Pydantic settings loaded from .env
    └── logger.py            — Structured Python logging
```

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

## Personal Portfolio Tracker

`api/portfolio_tracker.py` — manages user's real holdings portfolios (separate from the paper trading virtual wallet).

### Portfolios
Multiple named portfolios (e.g. "Zerodha Demat", "HDFC Securities"). Each has holdings, total invested, current value, unrealised P&L, and XIRR.

### XIRR Calculation
`engine/portfolio_service.py` computes Extended Internal Rate of Return using cash-flow dates (buy transactions) and current market value as the final cash flow. Newton-Raphson iteration to 0.0001% tolerance.

### Live P&L
Current prices fetched from `PRICE_CACHE` (updated every 15 seconds during market hours by the live price feed). Falls back to yfinance if symbol not cached.

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

## Celery Background Tasks

13 scheduled tasks via Celery Beat.

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

### Personal Portfolio Tracker (`/api/v1/portfolios`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | All portfolios with summary |
| POST | `/` | Create new portfolio |
| GET | `/{id}` | Portfolio detail + holdings |
| PUT | `/{id}` | Update portfolio name/type |
| DELETE | `/{id}` | Delete portfolio |
| POST | `/{id}/holdings` | Add holding |
| PUT | `/{id}/holdings/{hid}` | Update holding |
| DELETE | `/{id}/holdings/{hid}` | Delete holding |
| GET | `/{id}/xirr` | Compute XIRR for portfolio |

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
| PATCH | `/` | Update runtime parameters |

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
`portfolios` — named portfolios with type and currency.
`holdings` — individual stock holdings linked to portfolio.
`transactions` — buy/sell transactions for XIRR computation.

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
│   ├── Navbar.jsx        — Live clock, balance/PnL ticker, Kite token expiry warning
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
    ├── Portfolio.jsx        — Legacy Kite holdings tracker
    ├── PortfolioTracker.jsx — Personal holdings with live P&L + XIRR
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

---

## Frontend Components

### Sidebar (`components/Sidebar.jsx`)
Fixed-width navigation with live status indicators per item:
- **Live Market** — pulsing green/red dot (NSE market open/closed)
- **Watchlist** — BUY signal count badge
- **Breadth** — market mood dot (green/red/gray)
- **Sector Heatmap** — 4-column sector strip (colored bars)
- **My Holdings** — total portfolio value badge
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
PAPER_TRADING_BALANCE=1000.0
MAX_RISK_PER_TRADE=0.02              # 2% of balance per trade
MAX_OPEN_POSITIONS=5
MAX_DAILY_LOSS=0.05                  # halt when down 5% on the day

# Signal / trade sizing
ATR_MULTIPLIER=2.0
MIN_RISK_REWARD=2.0

# Watchlists (comma-separated)
WATCHLIST_FOREX=EUR/USD,GBP/USD,USD/JPY,AUD/USD,USD/CHF,USD/CAD
WATCHLIST_STOCKS=AAPL,TSLA,NVDA,MSFT,AMZN,META,GOOGL,SPY,QQQ
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
