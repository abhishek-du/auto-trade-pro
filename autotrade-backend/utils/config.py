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
    WATCHLIST_FOREX:  str = "EUR/USD,GBP/USD,USD/JPY,AUD/USD,USD/CHF,USD/CAD"
    WATCHLIST_STOCKS: str = "AAPL,TSLA,NVDA,MSFT,AMZN,META,GOOGL,SPY,QQQ"

    # ── News ──────────────────────────────────────────────────────────────────
    # Finnhub is primary.  NewsAPI is optional secondary.
    FINNHUB_KEY:  str = ""
    NEWSAPI_KEY:  str = ""

    # ── LLM ───────────────────────────────────────────────────────────────────
    # Groq: fast inference — signal commentary, quick market analysis
    GROQ_API_KEY: str = ""
    # Claude: detailed explanations — strategy breakdowns, deeper reasoning
    ANTHROPIC_API_KEY: str = ""

    # ── Paper trading parameters ──────────────────────────────────────────────
    PAPER_TRADING_BALANCE: float = 1000.0
    MAX_RISK_PER_TRADE: float = 0.02       # fraction of balance risked per trade
    MAX_OPEN_POSITIONS: int = 5
    MAX_DAILY_LOSS: float = 0.05           # halt trading when day loss hits 5 % of balance
    PAPER_MODE: bool = True

    # ── Risk / trade sizing ───────────────────────────────────────────────────
    ATR_MULTIPLIER: float = 2.0       # stop = entry ± ATR × this
    MIN_RISK_REWARD: float = 2.0      # take-profit = entry ± risk × this

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
    def redis_uses_tls(self) -> bool:
        return self.REDIS_URL.startswith("rediss://")

    @property
    def forex_symbols(self) -> list[str]:
        return [s.strip() for s in self.WATCHLIST_FOREX.split(",") if s.strip()]

    @property
    def stock_symbols(self) -> list[str]:
        return [s.strip() for s in self.WATCHLIST_STOCKS.split(",") if s.strip()]


settings = Settings()
