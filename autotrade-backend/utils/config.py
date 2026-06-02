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

    # ── LLM ───────────────────────────────────────────────────────────────────
    # Groq: fast inference — signal commentary, quick market analysis
    GROQ_API_KEY: str = ""
    # Claude: detailed explanations — strategy breakdowns, deeper reasoning
    ANTHROPIC_API_KEY: str = ""

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

    # ── Unified decision router ──────────────────────────────────────────────
    # Single confidence gate used by paper, live, and agent execution paths
    PAPER_CONFIDENCE_THRESHOLD: float = 60.0   # min confidence for paper trade
    LIVE_CONFIDENCE_THRESHOLD:  float = 70.0   # tighter gate for live Zerodha orders
    AGENT_DRY_RUN:              bool  = False  # if true, agent logs but never executes

    # ── AI Trading Agent (Varsity-grounded) ──────────────────────────────────
    AGENT_ENABLED:              bool  = False
    AGENT_PAPER_MODE:           bool  = True
    AGENT_EQUITY:               float = 500_000.0

    # Risk limits — Varsity Module 9
    AGENT_MAX_RISK_PER_TRADE:   float = 0.01
    AGENT_MAX_OPEN_RISK:        float = 0.06
    AGENT_DAILY_DD_STOP:        float = 0.03
    AGENT_WEEKLY_DD_STOP:       float = 0.05
    AGENT_MONTHLY_DD_STOP:      float = 0.10
    AGENT_CASH_BUFFER_MIN:      float = 0.20
    AGENT_MAX_NEW_ENTRIES_DAY:  int   = 5
    AGENT_CONSEC_LOSS_LOCKOUT:  int   = 2
    AGENT_CONFIDENCE_THRESHOLD: int   = 70

    # Universe / timing
    AGENT_TIMEFRAME:            str   = "15m"
    AGENT_WARMUP_BARS:          int   = 210
    AGENT_SESSION_START:        str   = "09:20"
    AGENT_SESSION_END:          str   = "15:20"

    # ── Paper trading parameters ──────────────────────────────────────────────
    # Default ₹5,00,000 — matches AGENT_EQUITY so the simulator wallet and the
    # AI Trading Agent equity start from the same base.
    PAPER_TRADING_BALANCE: float = 500000.0
    MAX_RISK_PER_TRADE: float = 0.02       # fraction of balance risked per trade
    MAX_OPEN_POSITIONS: int = 5
    MAX_DAILY_LOSS: float = 0.05           # halt trading when day loss hits 5 % of balance
    PAPER_MODE: bool = True

    # ── Risk / trade sizing ───────────────────────────────────────────────────
    ATR_MULTIPLIER: float = 2.0       # stop = entry ± ATR × this
    MIN_RISK_REWARD: float = 2.0      # take-profit = entry ± risk × this

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
