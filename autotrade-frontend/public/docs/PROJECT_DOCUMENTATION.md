# AutoTrade Pro Project Documentation

## Project Summary

AutoTrade Pro is a full-stack paper-trading platform. The system collects market candles and financial news, generates AI-assisted trading signals, runs those signals through risk controls, opens and closes simulated trades, tracks a virtual wallet, and exposes the results through a React dashboard.

The project is explicitly built for paper trading only. That design choice appears throughout the codebase:

- the backend startup banner states that only virtual currency is used
- the wallet and trade modules use simulated balances and simulated fills
- the frontend keeps a persistent paper-trading disclaimer in the navigation and top bar
- the API root, health endpoint, and go-live view all repeat that real money is not involved

The repository is split into two applications:

- `autotrade-frontend`: React + Vite user interface
- `autotrade-backend`: FastAPI + SQLAlchemy + Celery trading engine and API

## High-Level Architecture

The end-to-end flow is:

1. Celery periodic tasks fetch market candles and financial news.
2. Candle data is stored in PostgreSQL and news items are stored with sentiment scores.
3. The signal engine combines candlestick patterns, technical indicators, and news sentiment into a single confluence score.
4. The risk manager filters out weak or unsafe signals.
5. Approved signals are executed as simulated paper trades with slippage.
6. The virtual wallet and open positions are updated.
7. FastAPI exposes the wallet, trades, signals, news, analytics, simulation logs, and settings.
8. The React frontend polls those endpoints and renders dashboards, tables, charts, and simulation analysis views.

## Frontend Overview

The frontend is a Vite React application using:

- `react` and `react-dom` for the component model
- `react-router-dom` for page routing
- `axios` for REST calls
- `recharts` for analytics and equity charts
- `lightweight-charts` as an installed dependency, although the current UI mainly uses `recharts`
- `lucide-react` for iconography
- `react-hot-toast` for settings feedback
- Tailwind CSS v4 through `@tailwindcss/vite`

Why this stack is used:

- React is used for a dashboard-style UI with reusable panels and hooks.
- Vite gives a fast local development server and simple asset handling.
- Axios centralizes backend calls and response normalization.
- Recharts is used because the app needs multiple simple dashboard charts with fast setup.
- Tailwind is used for compact, utility-first styling across cards, tables, charts, and layout.

## Frontend Application Structure

The frontend entry path is:

- `src/main.jsx`: mounts the React app
- `src/App.jsx`: wraps the application in `BrowserRouter`, renders the sidebar, navbar, and route content

The main shared layout consists of:

- `Sidebar`: left navigation for all sections
- `Navbar`: current page title, live badge, virtual balance, and live clock
- `Toaster`: bottom-right toast notifications

### Frontend Routes

The app defines these routes:

- `/`: Dashboard
- `/trades`: Trades history and filters
- `/analytics`: Performance analytics
- `/news`: News feed and sentiment view
- `/simulation`: Simulation analysis center
- `/settings`: Paper-trading settings editor
- `/documentation`: project documentation reader

## Frontend Shared Data Layer

### API Client

`src/api/client.js` creates a single Axios instance with:

- base URL: `http://localhost:8000`
- JSON content type
- 10 second timeout
- response interceptor that returns `res.data`

This design keeps page hooks simple because each call receives decoded payload data instead of the full Axios response object.

Main REST helpers include:

- portfolio endpoints
- trades endpoints
- signal endpoints
- news endpoints
- analytics endpoints
- simulation endpoints
- settings endpoints

### Frontend Hooks

The shared hooks wrap polling logic:

- `usePortfolio`: polls portfolio summary every 10 seconds
- `useSignals`: polls signals every 5 seconds
- `useTrades`: polls trade history every 15 seconds
- `useWebSocket`: generic WebSocket connector with reconnect behavior

Why polling is used:

- it simplifies the UI state model
- the backend already exposes list-based summary endpoints
- it reduces coupling between page components and WebSocket message formats

The WebSocket hook exists for real-time extensions, but most current pages still rely on REST polling.

## Frontend Pages and Features

## Dashboard

The dashboard is the executive summary page for the simulation.

Main features:

- KPI cards for portfolio value, total P&L, win rate, and total trades
- equity curve chart using portfolio snapshots
- latest signal list
- open positions table

Data sources:

- `GET /api/v1/portfolio/`
- `GET /api/v1/signals/`
- `GET /api/v1/portfolio/snapshots`

Why it exists:

- it gives an immediate view of portfolio health
- it surfaces the most recent AI signals
- it shows live exposure through open positions

## Trades

The trades page focuses on paper-trade history.

Main features:

- summary metrics for total trades, win rate, total P&L, and best versus worst trade
- filters by symbol, direction, and status
- paginated trade table
- formatted timestamps and P&L coloring

Why it exists:

- it separates historical execution review from the higher-level dashboard
- it makes it easier to inspect how the strategy is behaving over time

## Analytics

The analytics page consumes the backend analytics aggregate.

Main features:

- win rate and total P&L summary
- average reward-to-risk display
- equity curve
- distribution style views for strategy performance

Data source:

- `GET /api/v1/analytics/`

Why it exists:

- it concentrates derived performance metrics in one place
- it is intended for strategy evaluation rather than live monitoring

## News

The news page presents the cached financial headline feed.

Main features:

- recent news cards
- sentiment labels
- source and publication time
- external article links

Data source:

- `GET /api/v1/news/`

Why it exists:

- news sentiment is part of the signal engine
- the user needs to inspect whether the sentiment inputs match current market context

## Simulation

The simulation page is the deepest analysis screen in the UI.

Main features:

- performance summary cards
- equity curve over time
- rejection reason breakdown
- signal quality analysis
- simulation log viewer
- go-live readiness checker

Data sources:

- `GET /api/v1/simulation/performance`
- `GET /api/v1/portfolio/`
- `GET /api/v1/portfolio/snapshots`
- `GET /api/v1/simulation/logs`
- `GET /api/v1/simulation/analysis`
- `GET /api/v1/simulation/should-go-live`

Why it exists:

- it shows not only what trades happened, but also what the engine considered and rejected
- it helps measure readiness before any real-world usage

## Settings

The settings page edits runtime paper-trading configuration stored in JSON.

Main features:

- starting balance
- max position size
- stop loss percentage
- take profit percentage
- max daily loss percentage
- max open positions
- editable watchlist

Data sources:

- `GET /api/v1/settings/`
- `POST /api/v1/settings/`

Why it exists:

- it allows non-code changes to simulation parameters
- it avoids requiring a backend restart for simple paper-trading configuration changes

## Documentation

The documentation page added in this update reads this markdown file from the frontend public assets and renders it in the app.

Why it exists:

- the project now has built-in technical documentation access
- users can review the architecture without leaving the product

## Frontend Components

Important reusable components include:

- `MetricCard`: generic KPI card with trend display
- `OpenPositions`: open position table
- `TradeLog`: trade history table
- `CandlestickChart`: equity curve visualization from snapshots
- `AnalyticsPanel`: compact analytics summary panel
- `NewsPanel`: headline list renderer
- `SimulationLogViewer`: log console with filters and auto-scroll
- `GoLiveChecker`: checks simulation results against readiness thresholds
- `SignalBadge`: BUY, SELL, HOLD badge styling
- `LoadingSpinner`: shared loading state component
- `PortfolioCard`: compact wallet summary card

## Frontend Styling System

The app uses a dark, finance-dashboard oriented visual system.

Core theme colors are declared in `src/index.css`:

- profit green
- loss red
- accent blue
- cyan highlight
- panel and surface backgrounds
- border and muted text colors

Other style characteristics:

- fixed sidebar layout
- card-based information grouping
- consistent border treatment
- subtle gradients and glow effects
- compact typography for dense information

## Backend Overview

The backend is a FastAPI application with asynchronous database access and background processing.

Core backend technologies:

- `fastapi` and `uvicorn`
- `sqlalchemy[asyncio]` with `asyncpg`
- `alembic`
- `celery` with Redis broker and backend
- `pandas` and `numpy`
- `yfinance`
- `ta-lib`
- `transformers` and `torch`
- `feedparser`
- `loguru`
- `httpx`
- `slowapi` is installed although not visibly wired into the current router layer

Why this stack is used:

- FastAPI provides typed APIs and async support.
- SQLAlchemy async supports PostgreSQL-backed state for trades, news, candles, and logs.
- Celery handles repeated scans and automated trading loops outside request-response latency.
- TA-Lib and pandas support indicator and candlestick calculations.
- FinBERT via transformers supports finance-aware news sentiment scoring.

## Backend Application Entry

`autotrade-backend/main.py` is the backend entry point.

It performs these responsibilities:

- creates the FastAPI app
- registers the startup and shutdown lifespan
- initializes database tables when possible
- configures permissive CORS
- mounts all REST and WebSocket routers
- exposes `/` and `/health`

The backend is intentionally verbose about paper-trading mode during startup so the operating mode cannot be mistaken for live trading.

## Backend Configuration

`utils/config.py` defines the settings model using `pydantic-settings`.

Important configuration groups:

- database URL
- Redis URL
- Alpha Vantage key
- Finnhub key
- NewsAPI key
- Groq API key
- Anthropic API key placeholder
- forex watchlist string
- stock watchlist string
- paper trading balance
- max risk per trade
- max open positions
- max daily loss
- ATR multiplier
- minimum risk reward ratio

Why configuration is structured this way:

- secrets and infrastructure endpoints belong in environment variables
- market watchlists can be managed centrally
- risk behavior can be kept deterministic across the engine

## Database Layer

`db/database.py` creates:

- async SQLAlchemy engine
- async session factory
- `get_db()` FastAPI dependency
- `init_db()` startup helper

Important implementation detail:

- `statement_cache_size=0` is set for Supabase transaction-mode compatibility

For Celery tasks, a separate database helper in `tasks/_db.py` creates a `NullPool` engine because Celery prefork workers create and destroy event loops, which can otherwise leave async database connections bound to dead loops.

## Database Models

## VirtualWallet

Tracks the paper-trading account state:

- balance
- equity
- realised and unrealised P&L
- total trades
- winning trades
- peak balance
- max drawdown

Why it exists:

- the system needs one authoritative summary of the simulated account

## PaperTrade

Stores the complete lifecycle of each trade:

- symbol
- direction
- status
- entry and exit prices
- stop loss and take profit
- size in units and USD
- realized P&L and P&L percentage
- AI reasoning metadata
- sentiment score
- slippage applied
- timestamps

Why it exists:

- the app needs durable trade history and analytics inputs

## OpenPosition

Stores only currently open trades:

- current mark price
- unrealized P&L
- unrealized percentage
- live stop loss and take profit

Why it exists:

- open-state queries are common and should not require reconstructing position state from the full trade ledger

## Candle

Stores cached OHLCV bars keyed by symbol, timeframe, and timestamp.

Why it exists:

- signal generation requires recent market history
- caching prevents repeated expensive fetches

## Signal

Stores generated BUY, SELL, and HOLD decisions with:

- timeframe
- signal type
- confidence
- pattern name
- indicator data
- sentiment score
- final score

Why it exists:

- the UI needs recent signal history
- the engine needs auditable outputs for each analysis cycle

## NewsItem

Stores crawled headlines with:

- source
- URL
- sentiment label
- sentiment score
- affected tickers
- publication and crawl timestamps

Why it exists:

- sentiment is part of trade decision confluence
- the news feed UI reads directly from this cache

## SimulationLog

Stores append-only engine events.

Examples:

- wallet created
- margin deducted
- trade opened
- trade closed
- analysis cycle recorded
- engine errors

Why it exists:

- it provides auditability and post-run debugging context

## PerformanceSnapshot

Stores one daily performance record for the equity curve.

Why it exists:

- charts should not need to reconstruct long-term equity history from every individual trade

## Backend API Modules

## Portfolio API

`api/portfolio.py` provides:

- current wallet summary
- open positions
- recent performance snapshots
- full portfolio stats
- reset endpoint

Why it exists:

- it centralizes account-level views separate from individual trades

## Trades API

`api/trades.py` provides:

- open trades
- aggregate trade summary
- list of trades with filters
- single trade lookup
- manual close action

Why it exists:

- operations and review of executed trades are a separate concern from signal generation

## Signals API

`api/signals.py` provides:

- latest global signals
- manual trigger for signal generation
- seed endpoint that fetches candles and runs analysis
- per-symbol signal history

Why it exists:

- it supports both UI visibility and operational testing

## News API

`api/news.py` provides:

- recent news with optional sentiment filter
- aggregated sentiment score by symbol

Why it exists:

- it lets the UI and signal engine inspect stored sentiment context

## Analytics API

`api/analytics.py` computes:

- win rate
- average reward-to-risk
- total trades
- total P&L
- equity curve
- P&L by symbol
- trades by direction
- daily P&L chart
- best and worst trade
- average trade duration

Why it exists:

- this endpoint packages a strategy review dataset for the analytics page

## Simulation API

`api/simulation.py` provides:

- simulation logs
- analysis history
- performance summary
- should-go-live gate
- pause and resume flags
- simulation status

The go-live gate currently checks:

- win rate at least 55%
- ROI at least 10%
- at least 30 trades
- max drawdown no more than 20%

Why it exists:

- this is the operator-facing control and review surface for the automated simulation

## Settings API

`api/settings.py` reads and writes `paper_trading_config.json`.

Why a JSON file is used:

- settings need to be editable from the UI
- these values are runtime simulation preferences rather than immutable environment configuration

## WebSocket API

`api/websocket.py` provides channels for:

- portfolio updates
- trade events
- live price updates
- log tail streaming

Why it exists:

- the platform is designed to support more live behavior even though many pages still use polling

## Market Data Pipeline

`crawler/price_feed.py` handles price ingestion.

Primary design:

- yfinance is the first source
- Alpha Vantage is the fallback when configured
- candles are normalized before persistence
- inserts use PostgreSQL `ON CONFLICT DO NOTHING`

Why this approach is used:

- yfinance offers zero-key initial access
- Alpha Vantage gives a backup path when primary fetches fail
- upsert logic avoids duplicate candle rows

Batch crawling behavior:

- fetches 1-hour candles for the configured forex and stock watchlists
- processes symbols sequentially to avoid flooding the upstream provider

## News and Sentiment Pipeline

`crawler/news_crawler.py` is the news ingestion engine.

Source order:

- NewsAPI when a key exists
- Finnhub when a key exists
- free RSS feeds always attempted

Sentiment behavior:

- FinBERT is the preferred scorer
- keyword scoring is the fallback
- uncertain headlines are forced to neutral before model inference
- low-confidence FinBERT classifications are also forced to neutral

Why this is useful:

- finance headlines often contain cautious or event-preview language that generic polarity scoring can misread
- the two guards reduce noisy directional sentiment

Ticker extraction behavior:

- stock tickers are matched against the configured watchlist
- common forex currency codes are also extracted

## Signal Generation Engine

`engine/signal_generator.py` builds the trading decision.

Inputs:

- candlestick pattern detection
- technical indicators
- recent news sentiment

Scoring weights:

- patterns: 35%
- indicators: 45%
- sentiment: 20%

Decision thresholds:

- BUY when final score is above `30` and the market is not already overbought
- SELL when final score is below `-30` and the market is not already oversold
- otherwise HOLD

Output includes:

- action
- confidence
- entry price
- stop loss
- take profit
- pattern score
- indicator score
- sentiment score
- final score
- reasoning points

Why this design is used:

- it avoids single-signal decisions
- it produces both a machine decision and a human-readable explanation trail

## Technical Indicators Engine

`engine/indicators.py` computes:

- RSI
- MACD
- Bollinger Bands
- EMA 20, 50, and 200 trend context
- ATR
- stochastic oscillator
- composite score

Why this mix is used:

- RSI and stochastic represent momentum extremes
- MACD represents directional momentum shifts
- Bollinger Bands represent volatility extremes
- EMAs represent trend regime
- ATR supports stop-loss sizing

The module prefers TA-Lib and falls back to pandas or numpy calculations where needed.

## Candlestick Pattern Engine

`engine/candlestick.py` uses TA-Lib pattern recognition and maps patterns into:

- direction
- reliability tier
- signed score
- human-readable description

Why it exists:

- raw TA-Lib pattern flags are not enough for downstream strategy logic
- the project needs standardized bullish and bearish weights

## Risk Management

`engine/risk_manager.py` applies the pre-trade gate.

Checks include:

- max open positions
- daily loss circuit breaker
- minimum confidence of 40%
- minimum reward-to-risk ratio
- sufficient available virtual balance
- no duplicate open position for the same symbol

Position sizing:

- risk amount is `MAX_RISK_PER_TRADE * balance`
- units are derived from stop distance
- the output includes units, USD value, risk amount, and risk percent

Why it exists:

- the system should not convert every signal into a trade
- the account-level guardrails are as important as the signal model

## Paper Trade Execution

`paper_trading/trade_simulator.py` is the high-level paper execution module.

Open trade flow:

1. apply simulated adverse slippage
2. create `PaperTrade`
3. create `OpenPosition`
4. deduct 10% margin from the virtual wallet
5. write a simulation log entry

Close trade flow:

1. compute realized P&L
2. update the paper trade
3. remove the open position
4. return margin plus P&L to the wallet
5. write a close log entry

Price refresh flow:

- reads the latest 1-hour candle
- updates unrealized P&L for open positions
- auto-closes positions that hit stop loss or take profit

Why simulated slippage is used:

- paper trading should not assume frictionless fills
- the system is trying to model execution more realistically

## Virtual Wallet Engine

`paper_trading/virtual_wallet.py` owns the cash and equity model.

Responsibilities:

- create or fetch the wallet row
- deduct margin
- return margin and realized P&L
- update unrealized P&L
- generate wallet summary
- take daily snapshots
- reset the simulation wallet

Why it exists:

- the wallet is the financial core of the simulation
- centralizing it prevents inconsistent balance mutations across modules

## Simulation Logging and Analysis

`paper_trading/simulation_logger.py` has two layers:

- `SimulationLogger`: low-level event logger
- `SimLogger`: strategy-analysis logger and performance summarizer

Important behaviors:

- every analysis cycle can be recorded, even if no trade is taken
- performance summary is computed from logged and stored trade data
- rejected signals are counted indirectly through analysis cycle count minus trade count

Why it exists:

- the project is designed to explain not just successful trades, but also skipped opportunities and decision rationale

## LLM Explanation Layer

`engine/llm_explainer.py` generates beginner-friendly trade explanations.

Current design:

- primary path: Groq chat completions with `llama-3.1-8b-instant`
- fallback path: build text from signal reasoning points

Why it exists:

- the engine already produces structured scores
- this module turns those scores into plain-English explanations suitable for notifications or user-facing trade commentary

## Background Task Scheduling

Celery tasks are defined in `tasks/`.

Scheduled jobs:

- price scan every 30 seconds
- news crawl every 5 minutes
- paper-trade loop every 60 seconds

Main task roles:

- `market_scan.py`: fetches watchlist candles
- `news_scan.py`: fetches and stores news plus sentiment
- `paper_trade_loop.py`: updates positions, generates signals, runs risk checks, opens trades, and snapshots wallet state

Why Celery is used:

- these jobs are periodic, stateful, and not tied to direct user requests
- running them outside HTTP request handling keeps the API responsive

## Deployment and Runtime Notes

The backend includes:

- a `Dockerfile` for the FastAPI app
- a `docker-compose.yml` for local app, Celery worker, and Celery beat
- a `start.sh` script that starts Uvicorn plus Celery processes together

Important infrastructure assumptions from the code:

- PostgreSQL is expected to be external, commonly Supabase
- Redis is expected to be external, commonly Upstash
- the frontend local dev server proxies `/api` and `/ws` to `localhost:8000`

## Design Strengths

- clear separation between frontend, API layer, trading engine, and background jobs
- strong paper-trading disclaimers throughout the product
- auditable event logging through `SimulationLog`
- realistic execution features such as slippage and margin accounting
- mixed-data confluence approach instead of a single technical indicator trigger
- explicit strategy review tooling through analytics and go-live evaluation

## Current Gaps and Observations

- the frontend API client uses a hardcoded backend URL instead of reusing the Vite proxy configuration, which may reduce deployment flexibility
- the settings JSON file is editable from the UI, but the trading engine mainly reads environment-based settings; that means not every UI setting is guaranteed to affect all engine behaviors unless additional wiring is added
- WebSocket support exists, but much of the frontend still uses polling
- `lightweight-charts` is installed, but the visible charting code currently leans on `recharts`
- `slowapi` is installed, but rate limiting is not visibly applied in the router layer

## Final Technical Summary

AutoTrade Pro is a simulated algorithmic trading platform centered on explainable paper trading. The backend collects market and news data, transforms that data into scored signals, filters them through risk rules, executes virtual trades, and stores a detailed audit trail. The frontend turns that state into an operator dashboard with dedicated views for portfolio monitoring, trade review, analytics, news sentiment, settings, simulation analysis, and now built-in project documentation.

The most important architectural choice in this project is that it treats paper trading as a first-class domain rather than a placeholder. The wallet, margin, slippage, audit logging, analytics, and disclaimers are all implemented around that assumption, which gives the project a coherent operating model and makes it useful for testing strategy behavior before any real-world usage.
