# AutoTrade Pro — Complete Project Documentation

> **Paper Trading Only** — This system uses virtual/simulated currency exclusively. No real money is ever involved at any stage.

---

## Table of Contents

- Project Overview
- Architecture
- Technology Stack
- Backend — Structure and Modules
- Signal Engine
- Risk Management
- Paper Trading Simulation
- News and Sentiment
- LLM Integration
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

AutoTrade Pro is a full-stack automated paper-trading platform. It continuously pulls OHLCV price data and financial news from free public sources, runs that data through a multi-factor signal engine (candlestick patterns + technical indicators + FinBERT news sentiment), validates the resulting signals through a risk gate, and opens/manages simulated trades — all against a virtual $1,000 wallet.

The frontend is a React single-page application that renders portfolio metrics, live positions, trade history, signal analytics, news sentiment feed, and simulation audit logs. All data is fetched from the FastAPI backend over REST + WebSocket.

**Why paper trading?** The system is designed to help users prototype, test, and evaluate trading strategies without any financial risk. The term "paper trading" or "simulated trading" means every trade, balance, and PnL figure represents virtual money only.

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
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐  │
│  │  API Routers │   │  Signal      │   │  Paper Trading   │  │
│  │  (REST)      │   │  Engine      │   │  Simulation      │  │
│  └──────────────┘   └──────────────┘   └──────────────────┘  │
└────────────────────┬─────────────────────────────────────────┘
                     │ SQLAlchemy async
┌────────────────────▼────────────────┐
│  PostgreSQL (Supabase transaction    │
│  pooler)                             │
└─────────────────────────────────────┘

┌──────────────────────────────────────┐
│  Celery Workers + Beat               │
│  Tasks:                              │
│    scan_watchlist  (every 30s)       │
│    scan_news       (every 5 min)     │
│    paper_trade_loop (every 60s)      │
│  Broker/Backend: Upstash Redis (TLS) │
└──────────────────────────────────────┘

External APIs (read-only, price/news data):
  yfinance        — free, no key required
  Alpha Vantage   — free tier, optional fallback
  Finnhub         — optional news source
  NewsAPI         — optional news source
  RSS feeds       — free, no key required
  Groq API        — LLM explanations (llama-3.1-8b-instant)
  NSE India       — institutional flows, options chain
```

The Celery workers and the FastAPI server are completely decoupled. Workers write to the database; the API reads from the same database and pushes updates over WebSocket.

---

## Technology Stack

### Backend

| Technology | Version | Why |
|---|---|---|
| Python | 3.11+ | Modern async features, type hints |
| FastAPI | 0.110+ | Async-native, automatic OpenAPI docs, dependency injection |
| Uvicorn | 0.29+ | ASGI server, production-grade async I/O |
| SQLAlchemy | 2.0 | Async ORM with `AsyncSession`, type-mapped models |
| asyncpg | 0.29+ | Fastest PostgreSQL driver for async Python |
| Alembic | 1.13+ | Database migrations |
| Celery | 5.3+ | Distributed background task queue |
| Redis / Upstash | — | Celery broker and result backend |
| PostgreSQL | 15 via Supabase | Reliable relational DB, hosted |
| yfinance | 0.2+ | Free OHLCV price data — no API key needed |
| pandas + numpy | 2.x | Time-series manipulation, indicator calculations |
| TA-Lib | optional | C-accelerated technical indicators (RSI, MACD, BB, etc.) |
| httpx | 0.27+ | Async HTTP client for external API calls |
| Pydantic v2 | 2.x | Settings management and API schema validation |
| transformers (HuggingFace) | optional | FinBERT sentiment model inference |
| Groq SDK via httpx | — | Fast LLM inference (llama-3.1-8b-instant) |

### Frontend

| Technology | Version | Why |
|---|---|---|
| React | 19 | Concurrent mode, hooks-first architecture |
| Vite | 5 | Sub-second HMR, optimal build bundling |
| Tailwind CSS | 4 | Utility-first; no separate CSS files to maintain |
| React Router | 6 | Client-side SPA routing |
| Recharts | 2.x | Composable charting library built on React + D3 |
| Axios | 1.x | HTTP client with interceptors and base URL config |
| Lucide React | — | Consistent SVG icon set |
| react-hot-toast | — | Non-intrusive notification toasts |

---

## Backend — Structure and Modules

```
autotrade-backend/
├── main.py                  — FastAPI app, lifespan, router registration
├── start.sh                 — Dev startup script (cleans stale processes)
├── requirements.txt
│
├── api/                     — REST API routers
│   ├── analytics.py         — Performance stats + chart data
│   ├── news.py              — News feed + per-symbol sentiment
│   ├── portfolio.py         — Wallet summary, positions, snapshots, reset
│   ├── schemas.py           — Pydantic request/response models
│   ├── settings.py          — Read/write runtime configuration
│   ├── signals.py           — Latest signals, per-symbol signal history
│   ├── simulation.py        — Simulation logs, performance eval, go-live check
│   ├── trades.py            — Trade history, open/close endpoints
│   └── websocket.py         — Real-time push over WebSocket
│
├── crawler/                 — Data ingestion
│   ├── price_feed.py        — yfinance + Alpha Vantage OHLCV fetcher
│   ├── news_crawler.py      — NewsAPI + Finnhub + RSS + FinBERT sentiment
│   ├── india_price_feed.py  — NSE-specific price ingestion
│   ├── fii_dii_crawler.py   — NSE institutional flow scraper
│   └── options_chain.py     — NSE options chain snapshot scraper
│
├── db/
│   ├── database.py          — Engine, session factory, Base, init_db
│   └── models.py            — All ORM models (10 tables)
│
├── engine/                  — Trading logic
│   ├── candlestick.py       — Pattern detection (Doji, Hammer, Engulfing, …)
│   ├── indicators.py        — RSI, MACD, BB, EMA, ATR, Stochastic, Supertrend
│   ├── signal_generator.py  — Confluence scorer + TradingSignal dataclass
│   ├── risk_manager.py      — 6-check pre-trade gate + position sizing
│   └── llm_explainer.py     — Groq API + fallback explanation generator
│
├── paper_trading/
│   ├── virtual_wallet.py    — Virtual balance CRUD + daily snapshots
│   ├── trade_simulator.py   — Open/close trade lifecycle
│   ├── pnl_calculator.py    — Mark-to-market PnL computation
│   ├── position_tracker.py  — Open position queries and bulk price refresh
│   └── simulation_logger.py — Audit log writer + performance analyser
│
├── tasks/
│   ├── celery_app.py        — Celery app object + beat schedule
│   ├── _db.py               — NullPool session factory for workers
│   ├── market_scan.py       — Task: crawl OHLCV candles
│   ├── news_scan.py         — Task: crawl news + run FinBERT
│   └── paper_trade_loop.py  — Task: one full trading cycle
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

The weighted sum is the `final_score`. A score above +30 triggers a BUY signal; below -30 triggers SELL; everything else is HOLD.

### Candlestick Pattern Analysis (`engine/candlestick.py`)

Detects the following patterns on the most recent 1–3 candles:

- **Doji** — body < 5% of range; indecision
- **Hammer / Inverted Hammer** — long shadow reversal signals
- **Bullish / Bearish Engulfing** — current candle body completely engulfs previous
- **Morning Star / Evening Star** — three-candle reversal
- **Shooting Star** — bearish single-candle reversal at tops
- **Three White Soldiers / Three Black Crows** — sustained momentum confirmation

Each pattern has a reliability rating (LOW / MEDIUM / HIGH) and a directional score contribution. The aggregate raw score is normalised to -100..+100 using a practical maximum of 9 (three HIGH-reliability bullish patterns agreeing).

### Technical Indicators (`engine/indicators.py`)

All indicators use TA-Lib when installed; fall back to pandas/numpy equivalents otherwise.

**RSI (14-period)** — Relative Strength Index. Classifies as OVERSOLD (<30), OVERBOUGHT (>70), or NEUTRAL. Score contribution: ±20.

**MACD (12/26/9)** — Moving Average Convergence Divergence. Detects histogram zero-line crossovers. BULLISH_CROSS or BEARISH_CROSS each contribute ±25.

**Bollinger Bands (20-period, 2σ)** — Classifies price position relative to bands. Score contribution: ±15.

**EMA Trend (20/50/200)** — Exponential moving average alignment. STRONG_BULL (price above all 3) contributes +25; STRONG_BEAR contributes -25.

**ATR (14-period)** — Average True Range. Used for stop-loss and take-profit placement only, not the composite score.

**Stochastic (14/3/3)** — Momentum oscillator. OVERSOLD/OVERBOUGHT classification. Score contribution: ±15.

**Supertrend (7-period, 3× ATR multiplier)** — Trend-following overlay. BULLISH contributes +20; BEARISH -20. A direction flip adds an additional ±5.

### Guard Clauses

After computing the final score, two guards prevent contradictory signals:
- BUY signal is blocked when RSI = OVERBOUGHT
- SELL signal is blocked when RSI = OVERSOLD

### Stop-Loss and Take-Profit

Stop-loss is placed at `entry_price ± ATR × ATR_MULTIPLIER` (default 2.0).
Take-profit is placed at `entry_price ± risk × MIN_RISK_REWARD` (default 2.0), giving a minimum 2:1 reward-to-risk ratio.

### Batch Analyser

`analyze_all_symbols()` iterates every symbol in the watchlist (forex + stocks), fetches the last 200 hourly candles from the database, generates a signal, persists it, and returns only the actionable (BUY or SELL) signals sorted by absolute score descending.

---

## Risk Management

`engine/risk_manager.py` runs six sequential checks before any trade is opened.

**Check 1 — Maximum concurrent positions** — Rejects if the number of open positions equals or exceeds `MAX_OPEN_POSITIONS` (default 5).

**Check 2 — Daily loss circuit-breaker** — If today's cumulative closed PnL is negative and exceeds `MAX_DAILY_LOSS × balance` (default 5%), all new trades are blocked for the rest of the day.

**Check 3 — Minimum confidence** — Signals below 40% confidence are rejected.

**Check 4 — Risk:Reward ratio** — The reward (distance from entry to take-profit) must be at least `MIN_RISK_REWARD × risk` (default 2×). Signals that fail this are rejected.

**Check 5 — Sufficient virtual balance** — The 10% margin required for this position must not exceed 50% of current balance.

**Check 6 — No duplicate positions** — One open position per symbol at a time.

### Position Sizing

Position size is calculated using fixed-fractional risk: the trade is sized so that if stop-loss is hit exactly, the loss equals exactly `MAX_RISK_PER_TRADE × balance` (default 2%). This keeps every trade a constant fraction of the portfolio.

```
units     = (balance × risk_fraction) / |entry_price − stop_loss|
usd_value = units × entry_price
```

---

## Paper Trading Simulation

All simulation logic lives in `paper_trading/`.

### Virtual Wallet (`virtual_wallet.py`)

A single row in `virtual_wallet` tracks the entire paper account state:
- `balance` — available cash (decreases when a trade is opened)
- `equity` — balance + unrealised PnL
- `realised_pnl` — total closed PnL since inception
- `unrealised_pnl` — live mark-to-market PnL on open positions
- `total_trades` / `winning_trades` — trade counter
- `peak_balance` — high-water mark (for drawdown calculation)
- `max_drawdown` — peak-to-trough drawdown percentage

The wallet starts at $1,000 virtual. A `WALLET_RESET` operation closes all open positions at zero PnL and restores the wallet to $1,000.

### Trade Lifecycle (`trade_simulator.py`)

1. Signal passes risk checks
2. Position size is calculated
3. Margin (10% of notional) is deducted from balance via `VirtualWallet.deduct_margin()`
4. A `PaperTrade` row is inserted (status = OPEN)
5. An `OpenPosition` row is inserted as a live snapshot

On every Celery tick (`update_positions_with_current_prices()`):
1. The latest price is fetched from yfinance
2. Unrealised PnL is recalculated
3. If stop-loss or take-profit is hit, the position is closed
4. On close: margin + PnL is returned to balance, `OpenPosition` row is deleted, `PaperTrade` row is updated (status = CLOSED or STOPPED)

### Performance Snapshots

Once per Celery cycle, `VirtualWallet.take_daily_snapshot()` upserts a row in `performance_snapshots` with today's balance, equity, daily PnL, trade count, and win rate. These rows power the equity curve chart on the frontend.

### Simulation Logger

`simulation_logger.py` writes append-only audit entries to `simulation_logs` for every decision (wallet created, margin deducted, trade opened, trade closed, signal rejected). These appear verbatim in the Simulation page's log viewer.

---

## News and Sentiment

### News Crawler (`crawler/news_crawler.py`)

News is fetched from three sources in priority order:

1. **NewsAPI** (`NEWSAPI_KEY`) — general financial headlines
2. **Finnhub** (`FINNHUB_KEY`) — company-specific news with ticker extraction
3. **Free RSS feeds** — Yahoo Finance and ForexFactory (no key required)

### FinBERT Sentiment Scoring

When `torch` and `transformers` are installed, the crawler loads `ProsusAI/finbert` — a BERT model fine-tuned on financial text. It outputs POSITIVE, NEGATIVE, or NEUTRAL with a confidence score.

Two accuracy guards prevent known failure modes:
- Headlines below 60% confidence are forced to NEUTRAL
- Headlines matching "wait-and-see" patterns (e.g. "holds steady", "ahead of decision") are forced to NEUTRAL regardless of model output

When FinBERT is unavailable, a keyword heuristic is used: positive words (rally, surge, bullish, etc.) score +0.3; negative words (crash, bearish, plunge, etc.) score -0.3.

The signal generator calls `get_market_sentiment(symbol, session)` to fetch the average FinBERT score across the 10 most recent headlines mentioning the symbol.

---

## LLM Integration

`engine/llm_explainer.py` generates a 2–3 sentence human-readable explanation of why a signal was triggered.

**Primary: Groq API** — `llama-3.1-8b-instant` is used for fast, cost-effective inference. When `GROQ_API_KEY` is configured, the full signal context (direction, confidence, indicator scores, pattern names, reasoning points, entry/SL/TP levels) is sent as a user message with a system prompt that instructs the model to explain in beginner-friendly terms that this is a simulated trade using fake money.

**Fallback** — When Groq is unavailable, the top three reasoning points from the signal generator are joined into a plain-English sentence. The explanation is always produced; the trade never fails due to LLM unavailability.

**Claude (Anthropic) ready** — The `_call_groq()` function is isolated. Swapping to the Claude API requires only changing that one function.

---

## Celery Background Tasks

Three scheduled tasks run continuously via Celery Beat.

### Market Scan (`tasks/market_scan.py`)

- **Schedule**: every 30 seconds
- **Action**: calls `run_price_crawl()` which fetches 1h OHLCV bars for all 15 watchlist symbols (6 forex + 9 stocks) via yfinance, saves new bars to the `candles` table using upsert (ON CONFLICT DO NOTHING)

### News Scan (`tasks/news_scan.py`)

- **Schedule**: every 5 minutes
- **Action**: calls `run_news_crawl()` which fetches headlines from all configured news sources, runs FinBERT scoring, and persists new `NewsItem` rows

### Paper Trade Loop (`tasks/paper_trade_loop.py`)

- **Schedule**: every 60 seconds
- **Steps**:
  1. Update all open positions with current prices; auto-close SL/TP hits
  2. Run `analyze_all_symbols()` — generate signals for all watchlist symbols
  3. For each actionable (BUY/SELL) signal, run risk checks
  4. Open trades that pass all checks
  5. Generate AI explanation via Groq for each opened trade
  6. Take a daily performance snapshot

### NullPool Pattern (Critical Architecture Decision)

Celery workers run in separate OS processes using a prefork pool. Each call to `asyncio.run()` creates and destroys a new event loop. SQLAlchemy's default connection pool caches connections across calls, which causes connections to become attached to the old (destroyed) event loop — triggering `MissingGreenlet`, `RuntimeError: Future attached to a different loop`, and asyncpg SSL transport errors.

The fix is `NullPool` in `tasks/_db.py`: every Celery task call creates a fresh database engine and tears it down when done. This is intentionally inefficient per-call but correct across event loops.

```python
engine = create_async_engine(url, poolclass=NullPool, ...)
# engine is always fresh — never shares a connection across asyncio.run() calls
```

---

## API Reference

All endpoints are prefixed with `/api/v1/`. Full interactive docs at `/docs` (Swagger) or `/redoc`.

### Portfolio (`/api/v1/portfolio`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Virtual wallet summary (balance, equity, PnL, win rate, ROI) |
| GET | `/positions` | All currently open virtual positions |
| GET | `/snapshots` | Last 30 daily equity snapshots (for equity curve chart) |
| GET | `/stats` | Aggregated performance stats from simulation logger |
| POST | `/reset?confirm=true` | Reset wallet to $1,000 starting balance |

### Trades (`/api/v1/trades`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Full trade history (paginated) |
| GET | `/{id}` | Single trade detail |

### Signals (`/api/v1/signals`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Latest signal for each watchlist symbol |
| GET | `/{symbol:path}` | Signal history for a specific symbol (`:path` handles `EUR/USD`) |
| POST | `/trigger` | Manually trigger one signal-generation cycle |

### News (`/api/v1/news`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Recent news items with sentiment scores |
| GET | `/sentiment/{symbol:path}` | Average sentiment for a specific symbol |

### Analytics (`/api/v1/analytics`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | PnL by symbol, win rate by asset class, drawdown history |

### Simulation (`/api/v1/simulation`)

| Method | Path | Description |
|---|---|---|
| GET | `/logs` | Simulation audit log entries |
| GET | `/performance` | Win rate, PnL, Sharpe-style metrics |
| GET | `/analysis` | Full strategy evaluation |
| GET | `/should-go-live` | Go-live readiness check (win rate, Sharpe, drawdown thresholds) |

### Settings (`/api/v1/settings`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Current runtime configuration |
| POST | `/` | Update runtime parameters (risk %, max positions, watchlists) |

### WebSocket (`/ws`)

Real-time price and portfolio updates pushed to connected frontend clients. The frontend uses this for live position PnL updates without polling.

---

## Database Schema

PostgreSQL database managed via SQLAlchemy ORM (models in `db/models.py`).

### `virtual_wallet`
Single-row table. Tracks the paper account state: balance, equity, realised/unrealised PnL, trade counters, peak balance, max drawdown.

### `paper_trades`
One row per simulated trade (open and closed). Contains entry/exit price, stop-loss, take-profit, size, PnL, AI reasoning, pattern name, indicator snapshot, and news sentiment score at open time.

### `open_positions`
Live snapshot rows for currently open trades. Contains current price and unrealised PnL. Deleted when the trade is closed. Has a unique FK to `paper_trades`.

### `candles`
OHLCV bars cached from yfinance/Alpha Vantage. Unique constraint on `(symbol, timeframe, timestamp)` enables safe upsert. Indexed on `(symbol, timestamp)` and `(symbol, timeframe)` for fast signal generation queries.

### `signals`
Every generated signal persisted for audit and analytics. Includes the raw scores for patterns, indicators, and sentiment, plus the JSON indicator snapshot and reasoning list.

### `news_items`
Crawled headlines with FinBERT score (-1 to +1), sentiment label (positive/negative/neutral), source, URL, tickers_affected JSON, and publication time.

### `simulation_logs`
Append-only audit log. One row per event (WALLET_CREATED, MARGIN_DEDUCTED, POSITION_CLOSED, SIGNAL_REJECTED, WALLET_RESET). Contains a structured JSON `data` field with event-specific details.

### `performance_snapshots`
Daily equity curve data points. Unique constraint on `date` enables safe upsert. Powers the equity curve chart. Contains balance, equity, daily PnL, trade count, and win rate for each calendar day.

### `fii_dii_flows`
Daily NSE institutional flow data (FII/DII net buy/sell in INR Crores). Used for Indian market analysis. Unique constraint on date.

### `options_chain_snapshots`
NSE index options chain data: ATM strike, PCR (Put-Call Ratio), max pain strike, total call/put OI, support and resistance levels (JSON arrays). Used for Indian market analysis.

---

## Frontend — Structure and Pages

```
autotrade-frontend/
├── index.html               — Vite entry, Inter font, meta tags
├── vite.config.js
├── tailwind.config.js       — Extended theme colours
├── package.json
│
├── public/
│   └── docs/
│       └── PROJECT_DOCUMENTATION.md  — This file (served as static asset)
│
└── src/
    ├── App.jsx              — Router, layout (Sidebar + Navbar + main), Toaster
    ├── main.jsx             — React root mount
    ├── index.css            — Tailwind imports, CSS custom properties
    │
    ├── api/
    │   └── client.js        — Axios instance, all API functions
    │
    ├── components/          — Reusable UI components
    │   ├── Navbar.jsx        — Page title, live balance ticker, live clock
    │   ├── Sidebar.jsx       — Navigation links, paper-mode badge
    │   ├── AnalyticsPanel.jsx — PnL/trade charts + stats grid
    │   ├── CandlestickChart.jsx — Equity curve chart (Recharts)
    │   ├── GoLiveChecker.jsx  — Go-live readiness indicator
    │   ├── LoadingSpinner.jsx  — Centered spinner with message
    │   ├── MetricCard.jsx     — Single metric display tile
    │   ├── NewsPanel.jsx      — News feed with sentiment badges
    │   ├── OpenPositions.jsx  — Live positions table with PnL
    │   ├── PortfolioCard.jsx  — 6-metric portfolio summary grid
    │   ├── SignalBadge.jsx    — BUY/SELL/HOLD coloured badge
    │   ├── SimulationLogViewer.jsx — Scrollable audit log table
    │   └── TradeLog.jsx       — Closed trades history table
    │
    ├── hooks/
    │   ├── usePortfolio.js   — Polls portfolio summary every 10s
    │   ├── useSignals.js     — Polls latest signals every 30s
    │   ├── useTrades.js      — Polls trade history every 15s
    │   └── useWebSocket.js   — WebSocket connection + message handler
    │
    └── pages/
        ├── Dashboard.jsx     — Main overview: portfolio + chart + positions + signals
        ├── Trades.jsx        — Full trade log page
        ├── Analytics.jsx     — Analytics panel page
        ├── News.jsx          — News feed page
        ├── Simulation.jsx    — Simulation logs + go-live checker
        ├── Settings.jsx      — Runtime config editor
        └── Documentation.jsx — This documentation page (renders PROJECT_DOCUMENTATION.md)
```

---

## Frontend Components

### Navbar (`components/Navbar.jsx`)
- Renders the page title based on the current route (from `PAGE_TITLES` map)
- **LiveClock** sub-component updates every second via `setInterval` — shows time and date
- **BalanceTicker** sub-component reads from `usePortfolio()` and shows balance, total PnL, ROI percentage, and a trending-up/down icon

### Sidebar (`components/Sidebar.jsx`)
- Fixed-width (240px) vertical navigation panel
- Seven navigation items with active-state highlight (gradient background + cyan indicator dot)
- Paper Mode badge at the bottom pulses amber

### PortfolioCard (`components/PortfolioCard.jsx`)
- 6-metric grid: Balance, Equity, Total PnL, Unrealised PnL, Win Rate, ROI
- Colour-coded: profit values in green, loss values in red

### CandlestickChart (`components/CandlestickChart.jsx`)
- Recharts `AreaChart` rendering the equity curve from `/api/v1/portfolio/snapshots`
- Shows equity over the last 30 days with a gradient fill
- Axis labels use short date format; tooltip shows date + equity value

### OpenPositions (`components/OpenPositions.jsx`)
- Table of all currently open virtual positions
- Columns: symbol, direction badge, entry price, current price, stop-loss, take-profit, size (USD), unrealised PnL, opened time
- PnL values colour-coded; direction shown as green BUY / red SELL badge

### TradeLog (`components/TradeLog.jsx`)
- Full trade history table
- Columns: symbol, direction, entry/exit price, size, PnL (with percentage), status badge, opened/closed time
- Status badges: OPEN (cyan), CLOSED (green), STOPPED (red/amber)

### NewsPanel (`components/NewsPanel.jsx`)
- Card-based news feed
- Each card shows headline, source, sentiment badge (positive/negative/neutral), FinBERT score, and publication time
- Sentiment badge colours: green for positive, red for negative, slate for neutral

### AnalyticsPanel (`components/AnalyticsPanel.jsx`)
- Two Recharts bar charts: PnL by symbol and trade distribution
- Stats grid below charts: total trades, win rate, best trade, worst trade, average PnL

### SignalBadge (`components/SignalBadge.jsx`)
- Compact coloured chip: BUY (green), SELL (red), HOLD (slate)
- Reads `signal_type` field from backend response

### SimulationLogViewer (`components/SimulationLogViewer.jsx`)
- Scrollable table of audit log entries from `/api/v1/simulation/logs`
- Columns: timestamp, event type, symbol, message

### GoLiveChecker (`components/GoLiveChecker.jsx`)
- Calls `/api/v1/simulation/should-go-live`
- Shows a readiness card: green checkmark when thresholds pass (win rate > 55%, max drawdown < 20%, Sharpe > 0.5), amber warning with blocking reasons when not

### MetricCard (`components/MetricCard.jsx`)
- Reusable single-value display tile used across dashboard and analytics pages

---

## Frontend Hooks

### `usePortfolio.js`
Polls `GET /api/v1/portfolio/` every 10 seconds. Returns `{ portfolio, loading, error }`. Used by Navbar (balance ticker) and Dashboard.

### `useSignals.js`
Polls `GET /api/v1/signals/` every 30 seconds. Returns current signals for all watchlist symbols.

### `useTrades.js`
Polls `GET /api/v1/trades/` every 15 seconds. Returns the full trade history.

### `useWebSocket.js`
Manages a WebSocket connection to `ws://localhost:8000/ws`. Handles reconnection, message parsing, and exposes the latest message to consuming components.

---

## Configuration and Environment Variables

All configuration is loaded from a `.env` file in the backend root via `utils/config.py` (Pydantic `BaseSettings`).

```
# Database (Supabase transaction-mode pooler — required)
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db

# Redis / Upstash (required for Celery)
REDIS_URL=rediss://default:token@host:6380   # TLS URL for Upstash

# Market data (optional — yfinance works without keys)
ALPHA_VANTAGE_KEY=

# News (optional — RSS works without keys)
FINNHUB_KEY=
NEWSAPI_KEY=

# LLM (optional — fallback explanation used when absent)
GROQ_API_KEY=
ANTHROPIC_API_KEY=

# Paper trading parameters
PAPER_TRADING_BALANCE=1000.0
MAX_RISK_PER_TRADE=0.02        # 2% of balance per trade
MAX_OPEN_POSITIONS=5
MAX_DAILY_LOSS=0.05            # halt when down 5% on the day

# Signal / trade sizing
ATR_MULTIPLIER=2.0             # stop = entry ± ATR × 2
MIN_RISK_REWARD=2.0            # TP = entry ± risk × 2

# Watchlists (comma-separated)
WATCHLIST_FOREX=EUR/USD,GBP/USD,USD/JPY,AUD/USD,USD/CHF,USD/CAD
WATCHLIST_STOCKS=AAPL,TSLA,NVDA,MSFT,AMZN,META,GOOGL,SPY,QQQ
```

---

## Infrastructure

### PostgreSQL via Supabase
The database is hosted on Supabase and accessed through their **transaction-mode pooler** (port 6543). Transaction-mode pooling means each SQLAlchemy session checkout gets a fresh connection from the pool. This is required because Supabase's session-mode pooler doesn't support the prepared statements that asyncpg enables by default (`statement_cache_size=0` is set in the engine connect args to disable them).

### Redis via Upstash
Upstash provides a serverless Redis instance accessed over TLS (`rediss://` URL). The Celery app configures `ssl_cert_reqs=CERT_NONE` because Upstash uses SNI-based TLS without requiring a client certificate. Upstash has a 1 MB command-size limit, which is why Celery tasks carry minimal payloads.

### Celery Beat
The `celerybeat-schedule` file is the persistent state for beat's task scheduler. On startup, `start.sh` deletes this file to prevent stale schedule state causing task-timing drift after a crash.

---

## Development Setup

### Backend

```bash
cd autotrade-backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL and REDIS_URL at minimum
./start.sh             # starts Uvicorn + Celery worker + Celery beat
```

The `start.sh` script:
1. Kills any stale Uvicorn/Celery processes from a previous run
2. Deletes `celerybeat-schedule*` files
3. Starts Uvicorn on port 8000 in the background
4. Starts a Celery worker (prefork, concurrency 2)
5. Starts Celery beat

### Frontend

```bash
cd autotrade-frontend
npm install
npm run dev   # Vite dev server on localhost:5173
```

The Vite dev server proxies are not configured — API calls go directly to `http://localhost:8000`. If the backend is not running, the frontend will show loading states or error states in each component.

---

## Known Constraints and Design Decisions

### asyncpg 32,767 parameter limit
PostgreSQL allows a maximum of 32,767 bind parameters per statement. Saving 700 candles × 8 columns would produce 5,600 parameters — well within the limit. But a full historical backfill (17,000+ rows per symbol) would exceed it. The solution is to chunk bulk inserts into groups of 3,000 rows (3,000 × 8 = 24,000 parameters per chunk), which stays safely under the limit.

### NullPool for Celery workers
Standard connection pooling does not work across `asyncio.run()` boundaries in Celery's prefork workers. Each `asyncio.run()` creates a new event loop; pooled connections are attached to the previous loop and fail on reuse. Using `NullPool` forces a fresh connection on every task call. This is slightly slower but correct.

### `/{symbol:path}` route parameter
Forex symbols like `EUR/USD` contain a slash. FastAPI's default `{symbol}` path parameter treats the slash as a route separator, causing 404s for forex routes. The `:path` converter (`{symbol:path}`) captures slashes as part of the parameter value, fixing the routing.

### TA-Lib optional dependency
TA-Lib requires a native C library (`libta-lib`) that is not available in all environments. All indicator calculations have pandas/numpy fallbacks so the system works without TA-Lib, at the cost of slightly slower computation on large DataFrames.

### FinBERT optional dependency
Loading the FinBERT model requires `torch` (approximately 1 GB) and `transformers`. When absent, a keyword heuristic is used. The keyword heuristic is less accurate but adds no memory or startup overhead.

### Paper Trading Disclaimer
The phrase "PAPER TRADING — VIRTUAL CURRENCY ONLY" appears in: the startup banner, the health endpoint response, every wallet log line, the LLM system prompt, the API description, the Navbar disclaimer banner, and the Sidebar paper-mode badge. This is intentional — the system is never meant to be connected to a real brokerage.

---

*Documentation generated from source code — version current as of May 2026.*
