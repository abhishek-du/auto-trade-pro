# Centralised application settings loaded from environment variables / .env file.
# Infrastructure: Supabase (PostgreSQL) + Upstash (Redis) + Vercel (API)
# LLM stack: Groq for fast inference, Claude for detailed explanations.

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Database (Supabase) ───────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/autotrade_pro"

    # ── Redis (Upstash — TLS) ──────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Market data ───────────────────────────────────────────────────────────
    # yfinance is primary (no key).  Alpha Vantage is secondary (free key).
    ALPHA_VANTAGE_KEY: str = ""

    # ── Watchlists (comma-separated) ──────────────────────────────────────────
    # NSE-focused defaults — this is an Indian markets app
    WATCHLIST_FOREX:  str = "USD/INR,EUR/INR,GBP/INR,JPY/INR"
    WATCHLIST_STOCKS: str = "RELIANCE.NS,TCS.NS,HDFCBANK.NS,INFY.NS,ICICIBANK.NS,SBIN.NS,BHARTIARTL.NS,KOTAKBANK.NS,LT.NS,ITC.NS"

    # ── Indian market watchlists ──────────────────────────────────────────────
    WATCHLIST_NSE_LARGE_CAP: list[str] = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
        "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK",
        "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO", "HCLTECH",
        "ULTRACEMCO", "NESTLEIND", "POWERGRID", "SUNPHARMA", "DRREDDY",
    ]
    WATCHLIST_NSE_MID_CAP: list[str] = [
        "PIDILITIND", "VOLTAS", "MUTHOOTFIN", "PERSISTENT", "COFORGE",
        "LTTS", "TATAELXSI", "METROPOLIS", "LALPATHLAB", "ASTRAL",
    ]
    WATCHLIST_NIFTY_INDICES: list[str] = ["^NSEI", "^BSESN", "^NSEBANK"]
    WATCHLIST_INDIAN_FOREX: list[str] = ["USDINR=X", "EURINR=X", "GBPINR=X"]
    WATCHLIST_COMMODITIES: list[str] = ["GC=F", "SI=F", "CL=F"]
    WATCHLIST_MUTUAL_FUND_SCHEMES: list[str] = [
        "120503",  # Mirae Asset Large Cap
        "119598",  # Axis Bluechip
        "100356",  # SBI Bluechip
        "120716",  # HDFC Top 100
        "118989",  # ICICI Pru Bluechip
    ]

    # ── Indian market timing / feature flags ─────────────────────────────────
    NSE_OPEN_HOUR: int = 9
    NSE_OPEN_MINUTE: int = 15
    NSE_CLOSE_HOUR: int = 15
    NSE_CLOSE_MINUTE: int = 30
    IST_TIMEZONE: str = "Asia/Kolkata"

    ENABLE_FII_DII_ANALYSIS: bool = True
    ENABLE_OPTIONS_CHAIN: bool = True
    ENABLE_INDIA_VIX: bool = True
    ENABLE_MUTUAL_FUNDS: bool = True
    ENABLE_ML_PREDICTIONS: bool = False

    INDIAN_MARKET_MAX_RISK: float = 0.015
    INDIAN_INTRADAY_SL_PCT: float = 0.005

    # ── News ──────────────────────────────────────────────────────────────────
    # India-first stack: free RSS (ET/MC/BS/Mint) is primary and needs no key.
    # NewsData.io (India business news, 200 req/day free) and Finnhub (global)
    # are optional enrichers — activate by setting their keys in .env.
    FINNHUB_KEY:  str = ""
    NEWSAPI_KEY:  str = ""
    NEWSDATA_KEY: str = ""

    # ── IPO data (ipoalerts.in) ───────────────────────────────────────────────
    IPOALERTS_API_KEY:     str  = ""
    IPOALERTS_BASE_URL:    str  = "https://api.ipoalerts.in"
    IPOALERTS_INCLUDE_GMP: bool = False

    # ── Telegram notifications ────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""   # from @BotFather
    TELEGRAM_CHAT_ID:   str = ""   # user/channel ID (e.g. 693584236)

    # ── Web research (Tavily) ─────────────────────────────────────────────────
    # Real-time news enrichment for small-caps + shortlist alert AI notes.
    # 1000 free credits/month (basic search = 1 credit, advanced = 2 credits).
    TAVILY_API_KEY: str = ""   # required — set in .env

    # ── LLM ───────────────────────────────────────────────────────────────────
    # Ollama: local inference (primary) — no rate limits, runs on localhost
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL:    str = "qwen2.5:3b"
    OLLAMA_TIMEOUT:  float = 120.0
    # Groq: cloud fast inference (fallback when Ollama unavailable)
    GROQ_API_KEY:   str = ""
    GROQ_MODEL:     str = "llama-3.3-70b-versatile"
    # Claude: detailed explanations — strategy breakdowns, deeper reasoning
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL:      str = "claude-sonnet-4-6"

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins. If empty, defaults to localhost
    # dev URLs. Override in .env for staging/production deployments.
    CORS_ORIGINS: str = ""

    # ── Third-party API base URLs ─────────────────────────────────────────────
    # Centralised so they can be overridden for testing or if vendors change URLs.
    NEWSAPI_BASE_URL:      str = "https://newsapi.org/v2/everything"
    FINNHUB_BASE_URL:      str = "https://finnhub.io/api/v1"
    ALPHA_VANTAGE_BASE_URL: str = "https://www.alphavantage.co"
    BSE_API_BASE_URL:      str = "https://api.bseindia.com"
    # Comma-separated RSS feed URLs for free news (no key required)
    RSS_FEED_URLS: str = (
        "https://www.moneycontrol.com/rss/latestnews.xml,"
        "https://www.business-standard.com/rss/markets-106.rss,"
        "https://www.livemint.com/rss/markets,"
        "https://economictimes.indiatimes.com/markets/rss.cms"
    )

    # ── Zerodha Kite (read-only portfolio tracking — legacy kiteconnect lib) ────
    KITE_API_KEY:      str = ""
    KITE_API_SECRET:   str = ""
    KITE_REDIRECT_URL: str = "http://localhost:8000/api/v1/kite/callback"

    # ── Zerodha KiteConnect v3 (raw HTTP — full integration) ─────────────────
    ZERODHA_API_KEY:       str  = ""
    ZERODHA_API_SECRET:    str  = ""
    ZERODHA_ACCESS_TOKEN:  str  = ""
    ZERODHA_REQUEST_TOKEN: str  = ""
    ZERODHA_REDIRECT_URL:  str  = "http://localhost:8000/api/v1/zerodha/callback"
    ZERODHA_ENABLED:       bool = False
    ZERODHA_PAPER_MODE:    bool = True
    ZERODHA_USER_ID:       str  = ""
    ZERODHA_PASSWORD:      str  = ""
    ZERODHA_TOTP_SECRET:   str  = ""

    # ── Unified decision router ──────────────────────────────────────────────
    # Single confidence gate used by paper, live, and agent execution paths
    PAPER_CONFIDENCE_THRESHOLD: float = 60.0   # min confidence for paper trade
    LIVE_CONFIDENCE_THRESHOLD:  float = 70.0   # tighter gate for live Zerodha orders
    AGENT_DRY_RUN:              bool  = False  # if true, agent logs but never executes

    # ── AI Trading Agent (Varsity-grounded) ──────────────────────────────────
    AGENT_ENABLED:              bool  = True
    AGENT_PAPER_MODE:           bool  = True
    AGENT_EQUITY:               float = 2_500_000.0

    # Allow SELL (short) signals from the Hub 7-factor score.
    # NSE rule: equity short-selling is intraday-only (MIS product).
    # Set True only when you want the agent to act on negative Hub scores.
    # F&O shorts come later; this flag is equity cash only.
    EQUITY_SHORT_ENABLED:       bool  = False

    # Risk limits — Varsity Module 9
    AGENT_MAX_RISK_PER_TRADE:   float = 0.01
    AGENT_MAX_OPEN_RISK:        float = 0.06
    AGENT_MAX_POSITIONS:        int   = 15   # hard cap on concurrent open positions
    AGENT_DAILY_DD_STOP:        float = 0.03
    AGENT_WEEKLY_DD_STOP:       float = 0.05
    AGENT_MONTHLY_DD_STOP:      float = 0.10
    AGENT_CASH_BUFFER_MIN:      float = 0.20
    AGENT_MAX_NEW_ENTRIES_DAY:  int   = 20   # per-day new-trade ceiling (paper: bypassed)
    AGENT_CONSEC_LOSS_LOCKOUT:  int   = 2
    AGENT_CONFIDENCE_THRESHOLD: int   = 30

    # Hub-score-driven exits — close held positions when intelligence changes.
    # When Hub 7-factor score for a held BUY drops to or below this value, the
    # position is exited even if price hasn't hit the ATR stop yet.
    # REVERSAL  = score crossed to negative side (company/market turned bearish)
    # FLOOR     = score still positive but too weak to justify holding (profit risk)
    # Set REVERSAL_THRESHOLD = -10 and FLOOR = 5 as safe defaults.
    AGENT_HUB_EXIT_ENABLED:             bool = True
    AGENT_HUB_EXIT_REVERSAL_THRESHOLD:  int  = -10   # BUY exits if score drops below this
    AGENT_HUB_EXIT_SCORE_FLOOR:         int  = 5     # BUY exits if score falls below this floor

    # Master Intelligence Hub universe. Empty → use the hub_universe DB table
    # (top-N by turnover, rebuilt daily). Set a comma-separated list to override.
    HUB_SYMBOLS:               str   = ""
    HUB_UNIVERSE_SIZE:         int   = 2000    # top-N NSE equities by 30-day turnover
    HUB_UNIVERSE_MIN_TURNOVER_CR: float = 20.0  # min ₹ Cr/day to qualify

    # Universe / timing
    # 1h matches what the candles table actually has (282k 1h rows, 0 rows at 15m).
    # 60 bars ≈ 8 NSE trading days — enough for RSI/EMA50 settling on liquid names
    # without locking out mid-caps that have less history persisted.
    AGENT_TIMEFRAME:            str   = "1h"
    AGENT_WARMUP_BARS:          int   = 60
    # NSE regular session: 9:15 AM – 3:30 PM IST
    # MIS (intraday) auto-squareoff: 3:20 PM IST (Zerodha)
    # Agent starts scanning at 9:15; initiates MIS close sweep at 3:15 (5 min buffer)
    AGENT_SESSION_START:        str   = "09:15"
    AGENT_SESSION_END:          str   = "15:30"
    AGENT_MIS_SQUAREOFF_TIME:   str   = "15:15"   # close MIS positions by this time
    # CNC = delivery (long only, no expiry); MIS = intraday (short selling allowed)
    # Strategies: MEAN_REVERSION_SHORT → always MIS; everything else → DEFAULT_PRODUCT
    AGENT_DEFAULT_PRODUCT:      str   = "CNC"

    # ── Paper trading parameters ──────────────────────────────────────────────
    # Default ₹25,00,000 — matches AGENT_EQUITY so the simulator wallet and the
    # AI Trading Agent equity start from the same base.
    PAPER_TRADING_BALANCE: float = 2_500_000.0
    MAX_RISK_PER_TRADE: float = 0.02       # legacy flat risk (now superseded by conviction band)
    MAX_OPEN_POSITIONS: int = 20           # SAFETY CEILING (bug guard) — not the primary limiter
    MAX_DAILY_LOSS: float = 0.05           # halt trading when day loss hits 5 % of balance
    PAPER_MODE: bool = True
    SCANNER_ENABLED: bool = False  # False = agent runs solo; True = SCAN paper trader also runs

    # ── Capital-utilization model (replaces the rigid "max 5 positions") ──────
    # The agent deploys capital by ANALYSIS, not a fixed count: it keeps opening
    # positions while total open risk stays under the budget and a cash buffer is
    # preserved, sizing each trade by conviction. Tuned "Aggressive".
    MAX_PORTFOLIO_RISK:    float = 0.15    # sum of all open-position risks ≤ 15% of equity
    MIN_CASH_BUFFER:       float = 0.10    # always keep ≥10% of equity as dry cash
    RISK_PER_TRADE_MIN:    float = 0.015   # risk on a floor-confidence signal
    RISK_PER_TRADE_MAX:    float = 0.030   # risk on a high-conviction signal
    CONVICTION_HIGH:       float = 70.0    # confidence at which risk hits RISK_PER_TRADE_MAX
    MAX_NEW_ENTRIES_PER_CYCLE: int = 8     # don't fill the whole budget in one 60s cycle

    # ── Risk / trade sizing ───────────────────────────────────────────────────
    ATR_MULTIPLIER: float = 2.0       # stop = entry ± ATR × this
    MIN_RISK_REWARD: float = 2.0      # take-profit = entry ± risk × this

    # ── Trade journal → spreadsheet ──────────────────────────────────────────
    # Logs every trade (why bought, targets, ETA, which target hit, duration,
    # P&L, AI expert note) to a spreadsheet. Backend is pluggable: "local" writes
    # an .xlsx file; "google" writes a Google Sheet. Switching is a one-line
    # config change — the column schema and sync logic are backend-agnostic.
    SHEET_LOG_ENABLED:  bool = True
    SHEET_LOG_BACKEND:  str  = "local"           # "local" | "google"
    SHEET_LOG_USE_LLM:  bool = True              # AI expert notes via Groq (template fallback)
    # Local Excel backend
    SHEET_LOG_LOCAL_PATH: str = "logs/trade_journal.xlsx"
    # Google Sheets backend (only used when SHEET_LOG_BACKEND="google")
    GOOGLE_SHEETS_ID:            str = "11JVm7QmkPadJvk_dsQZa_5WbIBjH66CPDzUPiznHlmc"
    GOOGLE_SHEETS_WORKSHEET:     str = "Trades"  # tab name within the spreadsheet
    # OAuth 2.0 Desktop credentials (your own Google account — no sheet sharing needed)
    GOOGLE_OAUTH_CLIENT_SECRET_JSON: str = ""    # path to downloaded client_secret_*.json
    GOOGLE_OAUTH_TOKEN_PATH:         str = "logs/google_token.pickle"  # saved after first auth
    # Legacy service-account path (kept for compatibility — OAuth is preferred)
    GOOGLE_SERVICE_ACCOUNT_JSON: str = ""

    @property
    def ollama_available(self) -> bool:
        return bool(self.OLLAMA_BASE_URL and self.OLLAMA_MODEL)

    @property
    def telegram_available(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)

    @property
    def google_sheets_available(self) -> bool:
        # OAuth (preferred) or service-account
        return bool(self.GOOGLE_SHEETS_ID and (
            self.GOOGLE_OAUTH_CLIENT_SECRET_JSON or self.GOOGLE_SERVICE_ACCOUNT_JSON
        ))

    @property
    def kite_available(self) -> bool:
        return bool(self.KITE_API_KEY and self.KITE_API_SECRET)

    @property
    def zerodha_available(self) -> bool:
        return bool(self.ZERODHA_API_KEY and self.ZERODHA_API_SECRET)

    @property
    def ipoalerts_available(self) -> bool:
        return bool(self.IPOALERTS_API_KEY)

    @property
    def tavily_available(self) -> bool:
        return bool(self.TAVILY_API_KEY)

    @property
    def groq_available(self) -> bool:
        return bool(self.GROQ_API_KEY)

    @property
    def claude_available(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)

    @property
    def alpha_vantage_available(self) -> bool:
        return bool(self.ALPHA_VANTAGE_KEY)

    @property
    def finnhub_available(self) -> bool:
        return bool(self.FINNHUB_KEY)

    @property
    def newsapi_available(self) -> bool:
        return bool(self.NEWSAPI_KEY)

    @property
    def newsdata_available(self) -> bool:
        return bool(self.NEWSDATA_KEY)

    @property
    def redis_uses_tls(self) -> bool:
        return self.REDIS_URL.startswith("rediss://")

    @property
    def forex_symbols(self) -> list[str]:
        return [s.strip() for s in self.WATCHLIST_FOREX.split(",") if s.strip()]

    @property
    def stock_symbols(self) -> list[str]:
        return [s.strip() for s in self.WATCHLIST_STOCKS.split(",") if s.strip()]

    @property
    def nse_symbols(self) -> list[str]:
        return [f"{s}.NS" for s in self.WATCHLIST_NSE_LARGE_CAP]

    @property
    def nse_mid_symbols(self) -> list[str]:
        return [f"{s}.NS" for s in self.WATCHLIST_NSE_MID_CAP]

    @property
    def all_indian_symbols(self) -> list[str]:
        return (
            self.nse_symbols
            + self.nse_mid_symbols
            + self.WATCHLIST_NIFTY_INDICES
            + self.WATCHLIST_INDIAN_FOREX
        )


settings = Settings()
