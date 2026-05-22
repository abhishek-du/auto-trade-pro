# AutoTrade Pro — Complete Project Documentation

> **Paper Trading Only** — This system uses virtual/simulated currency exclusively. No real money is ever involved at any stage.

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
- India Market Suite
- Zerodha KiteConnect Integration
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

AutoTrade Pro is a full-stack automated paper-trading platform. It continuously pulls OHLCV price data and financial news from free public sources, runs that data through a multi-factor signal engine (candlestick patterns + technical indicators + FinBERT news sentiment), validates the resulting signals through a risk gate, and opens/manages simulated trades — all against a virtual wallet.

The system also includes a comprehensive **India market module** covering NSE stocks, FII/DII institutional flows, options chain analysis, mutual funds, sector performance, and fundamentals. A full **Zerodha KiteConnect v3 integration** provides OAuth-based authentication, real Demat holdings tracking, live market data (when a paid plan is active), per-stock deep analysis with AI commentary, an auto market scanner, and mutual fund signal scoring.

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
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  API Routers │  │  Signal      │  │  Paper Trading   │    │
│  │  (REST)      │  │  Engine      │  │  Simulation      │    │
│  └──────────────┘  └──────────────┘  └──────────────────┘    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │  India Market│  │  Zerodha     │  │  Deep Analysis   │    │
│  │  Module      │  │  KiteConnect │  │  Engine          │    │
│  └──────────────┘  └──────────────┘  └──────────────────┘    │
└────────────────────┬─────────────────────────────────────────┘
                     │ SQLAlchemy async
┌────────────────────▼────────────────┐
│  PostgreSQL (Supabase transaction    │
│  pooler)                             │
└─────────────────────────────────────┘

┌──────────────────────────────────────┐
│  Celery Workers + Beat               │
│  Tasks:                              │
│    scan_watchlist   (every 30s)      │
│    scan_news        (every 5 min)    │
│    paper_trade_loop (every 60s)      │
│  Broker/Backend: Upstash Redis (TLS) │
└──────────────────────────────────────┘

External APIs (read-only, price/news data):
  yfinance           — free, no key required (primary price source)
  Alpha Vantage      — free tier, optional fallback
  Finnhub            — optional news + per-stock company news
  NewsAPI            — optional news source
  RSS feeds          — free, no key required
  Groq API           — LLM explanations (llama-3.1-8b-instant)
  NSE India          — institutional flows, options chain (public endpoints)
  MFAPI              — mutual fund NAV history (free, no key)
  Zerodha KiteConnect v3 — OAuth, portfolio, orders (free plan);
                           market data requires paid plan (₹2000/month)
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
| TA-Lib | optional | C-accelerated technical indicators |
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
│   ├── india.py             — India market: FII/DII, options, MF, signals, backtest
│   ├── kite.py              — Legacy Kite portfolio tracker (holdings sync)
│   ├── news.py              — News feed + per-symbol sentiment
│   ├── portfolio.py         — Wallet summary, positions, snapshots, reset
│   ├── schemas.py           — Pydantic request/response models
│   ├── settings.py          — Read/write runtime configuration
│   ├── signals.py           — Latest signals, per-symbol signal history
│   ├── simulation.py        — Simulation logs, performance eval, go-live check
│   ├── trades.py            — Trade history, open/close endpoints
│   ├── websocket.py         — Real-time push over WebSocket
│   └── zerodha.py           — Zerodha KiteConnect v3 (auth, data, analysis, scanner)
│
├── crawler/                 — Data ingestion
│   ├── price_feed.py        — yfinance + Alpha Vantage OHLCV fetcher
│   ├── news_crawler.py      — NewsAPI + Finnhub + RSS + FinBERT sentiment
│   ├── india_price_feed.py  — NSE-specific price ingestion (full symbol coverage)
│   ├── fii_dii_crawler.py   — NSE institutional flow scraper
│   ├── options_chain.py     — NSE options chain snapshot scraper
│   ├── zerodha_client.py    — Async KiteConnect v3 HTTP client (singleton)
│   └── zerodha_market.py    — Live prices, historical candles, instrument tokens
│
├── db/
│   ├── database.py          — Engine, session factory, Base, init_db
│   └── models.py            — All ORM models (11 tables)
│
├── engine/                  — Trading logic
│   ├── candlestick.py       — Pattern detection (Doji, Hammer, Engulfing, …)
│   ├── deep_analysis.py     — Reasoning, trade setup, news fetch, AI commentary
│   ├── indicators.py        — Full indicator suite: RSI, MACD, BB, EMA, ATR,
│   │                          Stochastic, Supertrend, Ichimoku, ADX, VWAP+bands
│   ├── mutual_fund_analyzer.py — MF NAV trend analysis + signal scoring
│   ├── signal_generator.py  — Confluence scorer + TradingSignal dataclass
│   ├── risk_manager.py      — 6-check pre-trade gate + position sizing
│   ├── llm_explainer.py     — Groq API + fallback explanation generator
│   └── zerodha_portfolio.py — Zerodha holdings → portfolio analytics
│
├── paper_trading/
│   ├── virtual_wallet.py    — Virtual balance CRUD + daily snapshots
│   ├── trade_simulator.py   — Open/close trade lifecycle
│   ├── pnl_calculator.py    — Mark-to-market PnL computation
│   ├── position_tracker.py  — Open position queries and bulk price refresh
│   └── simulation_logger.py — Audit log writer + performance analyser
│
├── services/
│   └── kite_service.py      — Kite holdings sync, XIRR calculation
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

### Guard Clauses

After computing the final score, two guards prevent contradictory signals:
- BUY signal is blocked when RSI = OVERBOUGHT
- SELL signal is blocked when RSI = OVERSOLD

### Stop-Loss and Take-Profit

Stop-loss is placed at `entry_price ± ATR × ATR_MULTIPLIER` (default 2.0).
Take-profit is placed at `entry_price ± risk × MIN_RISK_REWARD` (default 2.0), giving a minimum 2:1 reward-to-risk ratio.

### Batch Analyser

`analyze_all_symbols()` iterates every symbol in the watchlist, fetches the last 200 hourly candles from the database, generates a signal, persists it, and returns only the actionable (BUY or SELL) signals sorted by absolute score descending.

---

## Technical Indicators

All indicators are computed in `engine/indicators.py`. TA-Lib is used when installed; pure pandas/numpy fallbacks are always available.

### RSI (14-period)
Relative Strength Index. Classifies as OVERSOLD (<30), OVERBOUGHT (>70), or NEUTRAL. Score contribution: ±20.

### MACD (12/26/9)
Moving Average Convergence Divergence. Detects histogram zero-line crossovers. BULLISH_CROSS or BEARISH_CROSS each contribute ±25.

### Bollinger Bands (20-period, 2σ)
Classifies price position relative to bands. Score contribution: ±15.

### EMA Trend (20/50/200)
Exponential moving average alignment. STRONG_BULL (price above all 3) contributes +25; STRONG_BEAR contributes -25.

### ATR (14-period)
Average True Range. Used for stop-loss and take-profit placement only, not the composite score.

### Stochastic (14/3/3)
Momentum oscillator. OVERSOLD/OVERBOUGHT classification. Score contribution: ±15.

### Supertrend (7-period, 3× ATR multiplier)
Trend-following overlay. BULLISH contributes +20; BEARISH -20. A direction flip adds an additional ±5.

### Ichimoku Cloud (9/26/52)
Measures trend, support, resistance, and momentum simultaneously. Score is derived from price vs. cloud (Kumo), Tenkan/Kijun cross, and Chikou span position. Contribution: ±20.

### ADX (14-period)
Average Directional Index. Measures trend strength, not direction. Strong trend (ADX > 25) amplifies the directional score from other indicators. Weak trend (ADX < 20) dampens it. Contribution: ±10 modifier.

### VWAP with ±1σ and ±2σ Bands
Volume-Weighted Average Price, meaningful on intraday data (≤30-min bars) only. When computed on daily data, score is set to 0 and a debug log is emitted (not a warning). The ±1σ and ±2σ bands are calculated from the cumulative daily standard deviation of typical prices, reset at midnight IST each session. Price outside ±2σ bands signals potential mean-reversion. Score contribution: ±15.

---

## Deep Analysis Engine

`engine/deep_analysis.py` powers the per-stock deep analysis available in the Zerodha Watchlist and Auto Scanner pages.

### `generate_reasoning(sig, ltp) -> dict`
Takes an `IndicatorSignals` dataclass and last traded price and returns structured reasoning in three lists:
- `bullish` — list of strings describing bullish evidence (e.g. "MACD bullish crossover — momentum shifting upward")
- `bearish` — list of strings describing bearish evidence
- `neutral` — list of strings describing neutral/conflicting signals

Covers all computed indicators: RSI, MACD, EMA trend, Ichimoku, Supertrend, ADX, Bollinger Bands, VWAP.

### `build_trade_setup(sig, ltp, signal) -> dict`
Constructs a concrete trade plan:
- `entry_low` / `entry_high` — suggested entry price range
- `stop_loss` — calculated stop-loss price
- `target_1` / `target_2` — two take-profit targets
- `risk_reward` — reward-to-risk ratio
- `when_to_buy` — markdown string explaining the ideal entry condition
- `when_to_sell` — markdown string explaining exit triggers
- `hold_strategy` — markdown string for position management

### `fetch_stock_news(symbol) -> list[dict]`
Calls Finnhub `/company-news` with the symbol mapped to `NSE:{symbol}`. Returns the 5 most recent news items with headline, source, summary, URL, and publication time. Gracefully returns an empty list on any error.

### `groq_commentary(symbol, signal, score, reasoning, news) -> str`
Sends a compact prompt to Groq `llama-3.1-8b-instant` requesting a 2–3 sentence AI commentary on the stock's outlook. The prompt includes the signal direction, composite score, top bullish/bearish reasons, and recent headline headlines. Returns an empty string on any failure (network, API error, or key not configured).

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
4. On close: margin + PnL is returned to balance; `OpenPosition` row is deleted; `PaperTrade` row is updated (status = CLOSED or STOPPED)

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

---

## LLM Integration

`engine/llm_explainer.py` generates a 2–3 sentence human-readable explanation of why a signal was triggered.

**Primary: Groq API** — `llama-3.1-8b-instant` is used for fast, cost-effective inference. The full signal context (direction, confidence, indicator scores, pattern names, reasoning points, entry/SL/TP levels) is sent as a user message with a system prompt that instructs the model to explain in beginner-friendly terms that this is a simulated trade using fake money.

**Fallback** — When Groq is unavailable, the top three reasoning points from the signal generator are joined into a plain-English sentence. The explanation is always produced; the trade never fails due to LLM unavailability.

The `deep_analysis.py` engine uses the same Groq model for stock-level AI commentary on the Zerodha analysis pages (see Deep Analysis Engine above).

---

## India Market Suite

Located in `api/india.py` and related crawlers/engines.

### Market Status (`GET /api/v1/india/market-status`)
Returns NSE market open/closed state, current IST time, next open/close time, and a human-readable status message.

### India VIX (`GET /api/v1/india/vix`)
Fetches India VIX data via yfinance (`^INDIAVIX`). Returns current VIX, 52-week high/low, and a volatility label (Low / Moderate / High / Extreme).

### FII/DII Flows (`GET /api/v1/india/fii-dii`)
Scrapes NSE's institutional activity page for the most recent 30 days of FII and DII net buy/sell data in INR Crores. Stored in the `fii_dii_flows` table. Returns daily rows plus a 5-day rolling summary.

### Options Chain (`GET /api/v1/india/options-chain/{symbol}`)
Supports `NIFTY` and `BANKNIFTY`. Fetches the live NSE options chain for the nearest expiry, computes Put-Call Ratio (PCR), max pain strike, total call/put OI, and the top support/resistance levels from OI concentration. Stored in `options_chain_snapshots`.

### Mutual Funds (`GET /api/v1/india/mutual-funds`)
Returns a curated list of direct-plan equity mutual funds with their MFAPI scheme codes, categories, and last-known NAV. Used as the source list for both SIP projection and the Zerodha MF analysis.

### SIP Projection (`GET /api/v1/india/mutual-funds/{code}/sip` and `POST /api/v1/india/sip/project`)
Fetches historical NAV from MFAPI, calculates actual XIRR on a hypothetical monthly SIP, and projects forward returns under three scenarios (conservative / base / optimistic growth).

### Fundamentals (`GET /api/v1/india/fundamentals/{symbol}`)
Fetches company fundamentals via yfinance: P/E ratio, P/B ratio, ROE, ROCE, debt-to-equity, earnings growth, dividend yield, and sector/industry classification. Also fetches a 52-week price range and market cap.

### Sector Performance (`GET /api/v1/india/sector-performance`)
Returns NSE sector index performance using Nifty sector indices (^CNXFMCG, ^CNXAUTO, ^CNXBANK, etc.) via yfinance. Shows 1-day, 1-month, and 3-month returns per sector.

### India Signals (`GET /api/v1/india/signals`)
Runs the full signal engine against a curated list of NSE large-cap and mid-cap stocks. Returns signals categorised as `largecap`, `midcap`, `fno`, or `all`. Fetches 90 days of daily candles from yfinance and runs `compute_indicators()` + scoring logic.

### Backtest (`POST /api/v1/india/backtest`)
Runs a vectorised backtest over 1 year of daily data for specified NSE stocks. For each signal crossover, simulates a paper trade with configurable stop-loss and take-profit multiples. Returns per-symbol and aggregate statistics (total return, win rate, max drawdown, Sharpe ratio).

---

## Zerodha KiteConnect Integration

Located in `api/zerodha.py`, `crawler/zerodha_client.py`, and `crawler/zerodha_market.py`.

### Plan Limitations

Zerodha KiteConnect has two tiers:

| Feature | Free Plan | Paid Plan (₹2000/month) |
|---|---|---|
| OAuth login | ✓ | ✓ |
| Holdings, positions, orders | ✓ | ✓ |
| Place/cancel orders | ✓ | ✓ |
| Live quotes (LTP) | ✗ | ✓ |
| Full quote + market depth | ✗ | ✓ |
| Historical OHLCV data | ✗ | ✓ |

When a market-data endpoint returns HTTP 403, a module-level boolean flag (`_kite_historical_available` or `_kite_quotes_available`) is set to `False`. All subsequent calls to that endpoint return immediately without making an HTTP request, logging a single INFO message. yfinance is used as the fallback for all historical data.

### OAuth Flow

1. Frontend calls `GET /api/v1/zerodha/login-url` → receives KiteConnect OAuth URL
2. URL is opened in a popup window (`window.open` — must not use `noopener`)
3. User logs in on Kite; Kite redirects to `ZERODHA_REDIRECT_URL` with a `request_token`
4. Backend `GET /api/v1/zerodha/callback` exchanges the token for an `access_token` via Kite's session API
5. `access_token` is stored in memory on the `ZerodhaClient` singleton
6. Callback page posts `zerodha_connected` message back to the opener via `window.postMessage`
7. Frontend closes the popup and re-fetches status

On error (invalid token, missing credentials, Kite API error), the callback renders an HTML page with a `window.opener.postMessage('zerodha_error:…', '*')` call so the frontend can show the error and close the popup.

### Authentication Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/zerodha/login-url` | Returns KiteConnect OAuth URL + redirect URL for setup |
| GET | `/api/v1/zerodha/callback` | OAuth callback; exchanges token; renders HTML close page |
| GET | `/api/v1/zerodha/status` | Connection status, user info, plan flags |
| GET | `/api/v1/zerodha/token-status` | Token expiry and validity |
| POST | `/api/v1/zerodha/logout` | Invalidates access token |

### Portfolio and Market Data Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/zerodha/holdings` | Demat holdings with LTP, P&L, day change |
| GET | `/api/v1/zerodha/positions` | Intraday + overnight positions |
| GET | `/api/v1/zerodha/orders` | Order book (all orders for today) |
| GET | `/api/v1/zerodha/trades` | Today's executed trades |
| GET | `/api/v1/zerodha/margins` | Available margin breakdown |
| GET | `/api/v1/zerodha/pnl` | Realised + unrealised P&L summary |
| GET | `/api/v1/zerodha/live-prices` | LTP for a list of symbols (paid plan) |
| GET | `/api/v1/zerodha/market-depth/{sym}` | Order book top-5 bids/asks (paid plan) |

### Analysis Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/zerodha/watchlist-analysis` | Technical analysis for a comma-separated list of symbols |
| GET | `/api/v1/zerodha/deep-analysis/{symbol}` | Full deep analysis: reasoning, trade setup, news, AI commentary |
| GET | `/api/v1/zerodha/auto-scan` | Scan ~60 NSE stocks; returns ranked buy signals + mutual fund analysis |
| GET | `/api/v1/zerodha/mf-analysis` | Signal scoring for all configured mutual fund schemes |

### Watchlist Analysis (`/api/v1/zerodha/watchlist-analysis`)

Accepts a `symbols` query parameter (comma-separated `.NS` symbols or bare NSE symbols). For each symbol:
1. Fetches 120 days of daily candles — Kite historical if available, yfinance otherwise
2. Runs `compute_indicators()` from `engine/indicators.py`
3. Computes a composite score using the same weights as the main signal engine
4. Maps the score to a signal: STRONG_BUY (≥60), BUY (≥25), NEUTRAL (>-25), SELL (>-60), STRONG_SELL

Uses `asyncio.gather()` for parallel analysis of all symbols.

### Deep Analysis (`/api/v1/zerodha/deep-analysis/{symbol}`)

Runs a comprehensive analysis of a single stock:
1. Fetches 120 days of candles and computes all indicators
2. Calls `generate_reasoning()` — bullish/bearish/neutral reason lists
3. Calls `build_trade_setup()` — entry range, SL, TP, R:R, when to buy/sell/hold
4. Fetches stock news from Finnhub and runs Groq AI commentary in parallel
5. Returns a combined response with indicator snapshot, signal, score, reasoning, trade setup, news, and AI commentary

### Auto Market Scanner (`/api/v1/zerodha/auto-scan`)

Scans approximately 60 NSE stocks from a combined universe of `NSE_TOKENS` (static map) and `_EXTRA_NSE` (additional mid/small caps: TITAN, IRCTC, HAL, BEL, BHEL, RECLTD, IRFC, NHPC, ESCORTS, SUZLON, etc.). All stocks are analysed in parallel. Accepts a `min_score` query parameter (default 25). Returns:
- `buy_signals` — stocks scoring ≥ `min_score`, sorted by score descending
- `all_signals` — all scanned stocks with their scores
- `kite_historical_available` — flag indicating whether Kite data or yfinance was used

Also triggers `_analyse_mf_all()` and includes mutual fund results in the response.

### Mutual Fund Analysis (`/api/v1/zerodha/mf-analysis`)

Fetches NAV history from MFAPI for each configured fund scheme. For each fund:
- Computes 1-week, 1-month, 3-month, and 1-year returns
- Computes SMA5 and SMA20 of NAV to assess trend direction
- Returns a signal: STRONG_BUY, BUY, HOLD, or REVIEW

Runs all fund analyses in parallel via `asyncio.gather()`.

### Instrument Token Cache

`crawler/zerodha_market.py` maintains an `NSE_TOKENS` dict mapping `.NS` symbols to Kite integer instrument tokens. A static fallback map covers ~30 large-caps. `refresh_instrument_tokens()` downloads the full NSE instrument master from Kite daily (scheduled at 08:00 IST) and upserts into the `kite_instruments` database table, also updating the in-memory map.

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
| GET | `/positions` | All currently open virtual positions with unrealised P&L |
| GET | `/snapshots` | Last 30 daily equity snapshots (for equity curve chart) |
| GET | `/stats` | Aggregated performance stats from simulation logger |
| POST | `/reset?confirm=true` | Reset wallet to starting balance |

### Trades (`/api/v1/trades`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Full trade history with invested amount, P&L, P&L % |
| GET | `/open` | Only open trades |
| GET | `/summary` | Aggregate counts and total P&L |
| GET | `/{id}` | Single trade detail |
| POST | `/{id}/close?price=X` | Manually close an open trade at a given price |

### Signals (`/api/v1/signals`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Latest signal for each watchlist symbol |
| GET | `/{symbol:path}` | Signal history for a specific symbol |
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
| GET | `/should-go-live` | Go-live readiness check |

### India Market (`/api/v1/india`)

| Method | Path | Description |
|---|---|---|
| GET | `/market-status` | NSE market open/closed state |
| GET | `/vix` | India VIX with volatility label |
| GET | `/fii-dii` | FII/DII institutional flows (30 days) |
| GET | `/options-chain/{symbol}` | NSE options chain (NIFTY / BANKNIFTY) |
| GET | `/mutual-funds` | Curated MF list with NAV |
| GET | `/mutual-funds/{code}/sip` | SIP projection with XIRR |
| POST | `/sip/project` | Custom SIP projection |
| GET | `/fundamentals` | List of available fundamental symbols |
| GET | `/fundamentals/{symbol}` | Full company fundamentals |
| GET | `/sector-performance` | NSE sector index returns |
| GET | `/signals` | Technical signals for NSE stocks |
| POST | `/seed` | Seed initial India market data |
| POST | `/backtest` | Run strategy backtest on NSE stocks |

### Zerodha KiteConnect (`/api/v1/zerodha`)

| Method | Path | Description |
|---|---|---|
| GET | `/login-url` | KiteConnect OAuth URL |
| GET | `/callback` | OAuth callback (called by Kite redirect) |
| GET | `/status` | Connection + plan status |
| GET | `/token-status` | Token validity |
| POST | `/logout` | Invalidate session |
| GET | `/holdings` | Demat holdings |
| GET | `/positions` | Intraday + overnight positions |
| GET | `/orders` | Today's orders |
| GET | `/trades` | Today's executed trades |
| GET | `/margins` | Available margin |
| GET | `/pnl` | P&L summary |
| GET | `/live-prices` | Live LTP for symbols (paid plan) |
| GET | `/market-depth/{sym}` | Order book (paid plan) |
| GET | `/watchlist-analysis` | Technical analysis for custom symbol list |
| GET | `/deep-analysis/{symbol}` | Full deep analysis with AI commentary |
| GET | `/auto-scan` | Auto market scanner (stocks + MF) |
| GET | `/mf-analysis` | Mutual fund signal scoring |

### Legacy Kite Portfolio Tracker (`/api/v1/kite`)

| Method | Path | Description |
|---|---|---|
| GET | `/status` | Legacy Kite connection status |
| GET | `/login-url` | Legacy Kite login URL |
| GET | `/holdings` | Holdings synced to local DB |
| POST | `/sync` | Sync holdings from Kite |
| POST | `/disconnect` | Clear Kite session |
| POST | `/holdings/manual` | Manually add a holding |

### Settings (`/api/v1/settings`)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Current runtime configuration |
| POST | `/` | Update runtime parameters |

### WebSocket (`/ws`)

Real-time price and portfolio updates pushed to connected frontend clients.

---

## Database Schema

PostgreSQL database managed via SQLAlchemy ORM (models in `db/models.py`).

### `virtual_wallet`
Single-row table. Tracks the paper account state: balance, equity, realised/unrealised PnL, trade counters, peak balance, max drawdown.

### `paper_trades`
One row per simulated trade (open and closed). Contains entry/exit price, stop-loss, take-profit, size (units and USD value), PnL, PnL percent, AI reasoning, pattern name, indicator snapshot, news sentiment score at open time, and slippage applied.

### `open_positions`
Live snapshot rows for currently open trades. Contains current price and unrealised PnL, updated on every Celery tick. Deleted when the trade is closed. Has a unique FK to `paper_trades`.

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
Daily NSE institutional flow data (FII/DII net buy/sell in INR Crores). Unique constraint on date.

### `options_chain_snapshots`
NSE index options chain data: ATM strike, PCR, max pain strike, total call/put OI, support and resistance levels (JSON arrays).

### `kite_instruments`
Kite instrument token cache. Downloaded from KiteConnect's NSE instrument master daily at 08:00 IST. Columns: `instrument_token`, `exchange_token`, `tradingsymbol`, `name`, `last_price`, `expiry`, `strike`, `tick_size`, `lot_size`, `instrument_type`, `segment`, `exchange`, `refreshed_at`. Used by `zerodha_market.py` for symbol-to-token lookups when the static `NSE_TOKENS` map doesn't cover a symbol.

---

## Frontend — Structure and Pages

```
autotrade-frontend/
├── index.html
├── vite.config.js
├── tailwind.config.js
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
    │   ├── Navbar.jsx
    │   ├── Sidebar.jsx
    │   ├── AnalyticsPanel.jsx
    │   ├── CandlestickChart.jsx
    │   ├── GoLiveChecker.jsx
    │   ├── LoadingSpinner.jsx
    │   ├── MetricCard.jsx
    │   ├── NewsPanel.jsx
    │   ├── OpenPositions.jsx
    │   ├── PortfolioCard.jsx
    │   ├── SignalBadge.jsx
    │   ├── SimulationLogViewer.jsx
    │   └── TradeLog.jsx
    │
    ├── hooks/
    │   ├── usePortfolio.js   — Polls portfolio summary every 10s
    │   ├── useSignals.js     — Polls latest signals every 30s
    │   ├── useTrades.js      — Polls trade history every 15s
    │   └── useWebSocket.js   — WebSocket connection + message handler
    │
    └── pages/
        ├── Dashboard.jsx        — Overview: portfolio + chart + positions + signals
        ├── Trades.jsx           — Trade history with P&L breakdown + live positions
        ├── Portfolio.jsx        — Zerodha Kite holdings tracker (legacy)
        ├── Analytics.jsx        — Analytics charts and stats
        ├── News.jsx             — News feed page
        ├── Simulation.jsx       — Simulation logs + go-live checker
        ├── IndiaMarket.jsx      — India market: VIX, FII/DII, options, sectors
        ├── IndiaFundamentals.jsx — NSE stock fundamentals
        ├── IndiaSignals.jsx     — India-specific technical signals
        ├── MutualFunds.jsx      — Mutual fund analysis and SIP projections
        ├── Backtest.jsx         — Strategy backtesting on NSE stocks
        ├── Zerodha.jsx          — Zerodha KiteConnect: connect, watchlist, scanner
        ├── Settings.jsx         — Runtime config editor
        └── Documentation.jsx   — This documentation page
```

---

## Frontend Components

### Navbar (`components/Navbar.jsx`)
Renders the page title, a live clock (updated every second), and a balance ticker showing balance, total PnL, ROI percentage, and a trend icon. The ticker reads from `usePortfolio()`.

### Sidebar (`components/Sidebar.jsx`)
Fixed-width vertical navigation. Navigation items with active-state highlight. Paper Mode badge at the bottom pulses amber.

### PortfolioCard (`components/PortfolioCard.jsx`)
6-metric grid: Balance, Equity, Total PnL, Unrealised PnL, Win Rate, ROI. Colour-coded.

### CandlestickChart (`components/CandlestickChart.jsx`)
Recharts `AreaChart` rendering the equity curve from `/api/v1/portfolio/snapshots`.

### OpenPositions (`components/OpenPositions.jsx`)
Table of all currently open virtual positions. Columns: symbol, direction, entry price, current price, stop-loss, take-profit, size, unrealised PnL, opened time.

### TradeLog (`components/TradeLog.jsx`)
Full trade history table with entry/exit price, size, P&L, status.

---

## Frontend Pages

### Trades (`pages/Trades.jsx`)

Four-card **Investment Summary** at the top:
- **Capital Deployed** — sum of `size_usd` across all trades
- **Portfolio Value** — live wallet equity (cash + open positions)
- **Total Return** — realised + unrealised P&L with breakdown subtitle
- **Return on Investment** — ROI % on capital deployed

**Open Positions panel** (visible only when positions exist): one card per open position showing symbol, direction badge, elapsed time, unrealised P&L as the hero figure, P&L %, entry → current price with delta, invested → current value row, and stop-loss / take-profit distances.

**Trade History table** columns: Date, Symbol, Direction, Invested (`size_usd`), Entry, Current/Exit price, Current Value (`size_usd + pnl`), P&L, P&L %, Status. Open trade rows are highlighted with a subtle tint, show a ⚡ icon, display live `unrealised_pnl` / `unrealised_pct` from the positions endpoint (matched by `trade_id`), and show a pulsing LIVE status badge. Positions and wallet data poll every 10 seconds.

### Zerodha (`pages/Zerodha.jsx`)

Three sections on one page:

**ConnectionCard** — Handles the full OAuth flow. Shows connection status, user info, token expiry, and plan capability flags. Displays a redirect-URL helper for Zerodha Developer Console setup. Opens the Kite login in a popup and listens for `postMessage` to detect success or error. Shows raw status JSON in a diagnostics expander.

**WatchlistAnalysis** — localStorage-backed custom watchlist (`zerodha_watchlist_v1`). Add symbols manually or import from Zerodha holdings. Each symbol row shows a `ScoreBar` (−100 to +100), signal badge, EMA trend, individual indicator pills, and an expandable `DeepPanel`. The DeepPanel shows: AI summary, Key Levels card (entry, SL, TP, R:R), When to Buy / When to Sell / Hold Strategy cards, bullish/bearish/neutral bullet columns, indicator snapshot grid, and news cards.

**AutoScanner** — Auto-runs on mount. Three tabs: Buy Signals, Mutual Funds, All Signals. Buy Signals tab shows a ranked list of NSE stocks with buy scores, expandable DeepPanel per stock. Mutual Funds tab shows NAV, return badges (1W/1M/3M/1Y), and signal score per fund. All Signals tab is a chip grid of all scanned symbols colour-coded by signal. Amber warning badge appears when `kite_historical_available === false` (yfinance fallback active). "Re-scan Now" button triggers a fresh scan.

### IndiaMarket (`pages/IndiaMarket.jsx`)
Dashboard for India-specific data: NSE market status, India VIX gauge, FII/DII flow chart, options chain (PCR, max pain, support/resistance), and sector performance heatmap.

### IndiaFundamentals (`pages/IndiaFundamentals.jsx`)
Stock-level fundamentals: P/E, P/B, ROE, ROCE, debt/equity, earnings growth, dividend yield. Symbol picker with search.

### IndiaSignals (`pages/IndiaSignals.jsx`)
Technical signal results for NSE large-cap and mid-cap stocks. Filterable by category. Shows score, signal badge, and key indicator values.

### MutualFunds (`pages/MutualFunds.jsx`)
Mutual fund list with NAV, category, SIP projection calculator (monthly amount, months, scenario). Shows XIRR and projected corpus.

### Backtest (`pages/Backtest.jsx`)
UI for the backtest endpoint: symbol picker, date range, SL/TP multiplier inputs. Results show per-symbol and aggregate stats including total return, win rate, max drawdown, and Sharpe ratio.

### Portfolio (`pages/Portfolio.jsx`)
Legacy Kite holdings tracker. Connection banner (connect/disconnect/sync). Summary cards: Holdings count, Invested, Current Value, Total P&L. Holdings table: symbol, qty, avg price, LTP, current value, P&L (amount + %), day change %, XIRR. Manual "Add Holding" form. Polls every 30 seconds.

---

## Frontend Hooks

### `usePortfolio.js`
Polls `GET /api/v1/portfolio/` every 10 seconds alongside `GET /api/v1/portfolio/positions`. Returns `{ portfolio, loading, error }`. Used by Navbar and Dashboard.

### `useSignals.js`
Polls `GET /api/v1/signals/` every 30 seconds.

### `useTrades.js`
Polls `GET /api/v1/trades/` every 15 seconds.

### `useWebSocket.js`
Manages a WebSocket connection to `ws://localhost:8000/ws`. Handles reconnection, message parsing, and exposes the latest message.

---

## Configuration and Environment Variables

All configuration is loaded from a `.env` file in the backend root via `utils/config.py` (Pydantic `BaseSettings`).

```
# Database (Supabase transaction-mode pooler — required)
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db

# Redis / Upstash (required for Celery)
REDIS_URL=rediss://default:token@host:6380

# Market data (optional — yfinance works without keys)
ALPHA_VANTAGE_KEY=

# News (optional — RSS works without keys)
FINNHUB_KEY=
NEWSAPI_KEY=

# LLM (optional — fallback explanation used when absent)
GROQ_API_KEY=
ANTHROPIC_API_KEY=

# Zerodha KiteConnect v3 (optional — enables Zerodha page)
ZERODHA_API_KEY=
ZERODHA_API_SECRET=
ZERODHA_REDIRECT_URL=http://localhost:8000/api/v1/zerodha/callback

# Legacy Kite portfolio tracker (optional — enables Portfolio page)
KITE_API_KEY=
KITE_API_SECRET=

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

### Zerodha Setup

1. Create an app at `https://developers.kite.trade`
2. Set the redirect URL in the Developer Console to `http://localhost:8000/api/v1/zerodha/callback`
3. Copy the API key and secret into `.env` as `ZERODHA_API_KEY` and `ZERODHA_API_SECRET`
4. Restart the backend
5. Open the Zerodha page and click "Login with Kite" — the popup will complete the OAuth flow

---

## Infrastructure

### PostgreSQL via Supabase
Hosted on Supabase, accessed through the **transaction-mode pooler** (port 6543). `statement_cache_size=0` is set in the engine connect args to disable prepared statements (required by transaction-mode pooling).

### Redis via Upstash
Serverless Redis over TLS (`rediss://` URL). The Celery app configures `ssl_cert_reqs=CERT_NONE` because Upstash uses SNI-based TLS without requiring a client certificate. Upstash has a 1 MB command-size limit — Celery tasks carry minimal payloads.

### Celery Beat
The `celerybeat-schedule` file is the persistent state for beat's task scheduler. `start.sh` deletes this file on startup to prevent stale schedule state.

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

---

## Known Constraints and Design Decisions

### asyncpg 32,767 parameter limit
PostgreSQL allows a maximum of 32,767 bind parameters per statement. Bulk candle inserts are chunked into groups of 3,000 rows (3,000 × 8 = 24,000 parameters per chunk) to stay safely under this limit.

### NullPool for Celery workers
Standard connection pooling does not work across `asyncio.run()` boundaries in Celery's prefork workers. Each `asyncio.run()` creates a new event loop; pooled connections are attached to the previous loop and fail on reuse. `NullPool` forces a fresh connection on every task call.

### `/{symbol:path}` route parameter
Forex symbols like `EUR/USD` contain a slash. FastAPI's default `{symbol}` path parameter treats the slash as a route separator. The `:path` converter (`{symbol:path}`) captures slashes as part of the parameter value.

### Kite 403 short-circuit flags
When Zerodha returns HTTP 403 on a market-data endpoint (meaning the free plan doesn't include that feature), the module-level flags `_kite_historical_available` and `_kite_quotes_available` are set to `False`. All subsequent calls return immediately — no HTTP request, no log noise. A single INFO message is logged explaining the limitation and the fallback. yfinance is always available as a fallback for historical data.

### VWAP on daily data
VWAP is an intraday metric that resets each trading session. Computing it on daily bars produces a meaningless result. The indicator engine detects when bar interval exceeds 30 minutes, sets the VWAP score contribution to 0, and logs a debug message. It does not block the scan or raise a warning.

### Zerodha OAuth popup — no `noopener`
The Kite OAuth popup uses `window.open` without the `noopener` flag. `noopener` would sever the `window.opener` reference, preventing the `window.postMessage` callback from reaching the parent window to signal successful connection. This is intentional.

### TA-Lib optional dependency
TA-Lib requires a native C library (`libta-lib`). All indicator calculations have pandas/numpy fallbacks so the system works without TA-Lib.

### FinBERT optional dependency
Loading the FinBERT model requires `torch` (~1 GB) and `transformers`. When absent, a keyword heuristic is used.

### Paper Trading Disclaimer
The phrase "PAPER TRADING — VIRTUAL CURRENCY ONLY" appears in the startup banner, health endpoint, every wallet log line, the LLM system prompt, the API description, the Navbar, and the Sidebar. Real order execution requires both `PAPER_MODE=false` AND `ZERODHA_ENABLED=true` AND the `X-Confirm-Real-Order: yes` header simultaneously.

---

*Documentation last updated May 2026 — covers all features through the Zerodha integration, deep analysis engine, auto market scanner, and Trades P&L overhaul.*
