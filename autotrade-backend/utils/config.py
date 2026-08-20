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

    # Public Telegram channel usernames (no @ / no t.me prefix) to scrape for
    # narrative/theme signals via the public t.me/s/<channel> web preview — no
    # bot token or login needed, works for any public channel. Comma-separated.
    # Investigated 2026-07-06: narrative_engine.py's docstring always described
    # Telegram as a source, but only RSS was ever actually implemented.
    NARRATIVE_TELEGRAM_CHANNELS: str = "eagleeyesmarketanalysis"

    # ── Indian market watchlists ──────────────────────────────────────────────
    WATCHLIST_NSE_LARGE_CAP: list[str] = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
        "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK",
        "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO", "HCLTECH",
        "ULTRACEMCO", "NESTLEIND", "POWERGRID", "SUNPHARMA", "DRREDDY",
        # NIFTYBEES: not a stock, but the market_regime.py 5-state engine and
        # intelligence_hub's macro context both read NIFTYBEES.NS daily candles
        # as the Nifty proxy. It's outside the 8200-symbol Hub universe backfill
        # (which routinely times out before reaching most symbols), so it needs
        # a spot in this small, reliably-completing daily sync to stay fresh.
        "NIFTYBEES",
    ]
    WATCHLIST_NSE_MID_CAP: list[str] = [
        "PIDILITIND", "VOLTAS", "MUTHOOTFIN", "PERSISTENT", "COFORGE",
        "LTTS", "TATAELXSI", "METROPOLIS", "LALPATHLAB", "ASTRAL",
    ]
    # ── Earnings calendar universe ────────────────────────────────────────────
    # Deliberately SEPARATE from WATCHLIST_NSE_LARGE_CAP/MID_CAP above (added
    # 2026-07-28) -- those two feed several other subsystems (candle sync,
    # info-cache refresh) that specifically need a SMALL list to reliably
    # finish within their time budget (see the NIFTYBEES comment above).
    # fetch_earnings_calendar() was reusing that same tiny 32-symbol list,
    # which meant genuinely large, actively-traded NSE names outside it --
    # confirmed live: Varun Beverages (VBL), Ambuja Cements, Tata Capital
    # (TATACAP), Cholamandalam Investment & Finance (CHOLAFIN), all with
    # real yfinance earnings dates -- never appeared on the calendar at all.
    # This list is only consumed by the once-daily calendar reseed, which has
    # all day to finish, so it can safely be much broader than the 32-symbol
    # list without risking the timeout problems that motivated keeping that
    # one small.
    WATCHLIST_EARNINGS_CALENDAR: list[str] = [
        # Nifty 50 core (banking, IT, energy, auto, FMCG, pharma, materials)
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
        "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK",
        "ASIANPAINT", "MARUTI", "BAJFINANCE", "WIPRO", "HCLTECH",
        "ULTRACEMCO", "NESTLEIND", "POWERGRID", "SUNPHARMA", "DRREDDY",
        "TITAN", "M&M", "NTPC", "TECHM", "JSWSTEEL", "INDUSINDBK",
        "TATAMOTORS", "BAJAJFINSV", "TATASTEEL", "ADANIENT", "ADANIPORTS",
        "CIPLA", "COALINDIA", "DIVISLAB", "EICHERMOT", "GRASIM",
        "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "BAJAJ-AUTO", "BPCL",
        "BRITANNIA", "APOLLOHOSP", "ONGC", "SBILIFE", "SHRIRAMFIN",
        "TATACONSUM", "TRENT", "UPL",
        # Widely-held large/mid caps that kept coming up missing from the
        # calendar -- banking/NBFC, cement, auto, chemicals, consumer.
        "VBL", "AMBUJACEM", "TATACAP", "CHOLAFIN", "PIDILITIND",
        "VOLTAS", "MUTHOOTFIN", "PERSISTENT", "COFORGE", "LTTS",
        "TATAELXSI", "METROPOLIS", "LALPATHLAB", "ASTRAL", "DLF",
        "GODREJCP", "DABUR", "HAVELLS", "SIEMENS", "PIIND",
        "ABB", "BOSCHLTD", "COLPAL", "MARICO", "PAGEIND",
        "BANKBARODA", "PNB", "CANBK", "IDFCFIRSTB", "AUBANK",
        "ZOMATO", "NAUKRI", "PAYTM", "IRCTC", "INDIGO",
        "VEDL", "SAIL", "NMDC", "JINDALSTEL", "ACC",
        "SHREECEM", "AMBER", "POLYCAB", "CUMMINSIND", "SRF",
        "GLAND", "TORNTPHARM", "LUPIN", "AUROPHARMA", "ALKEM",
    ]
    # ── BSE / Sensex watchlists ───────────────────────────────────────────────
    WATCHLIST_BSE_LARGE_CAP: list[str] = [
        # Sensex 30 core
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
        "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK",
        "BAJFINANCE", "MARUTI", "TITAN", "SUNPHARMA", "WIPRO", "HCLTECH",
        "ULTRACEMCO", "NESTLEIND", "M&M", "POWERGRID", "NTPC", "TECHM",
        "JSWSTEEL", "INDUSINDBK", "TATAMOTORS", "BAJAJFINSV", "TATASTEEL",
        "ASIANPAINT",
    ]
    WATCHLIST_BSE_MID_CAP: list[str] = [
        # BSE-listed mid/small-caps with good liquidity
        "CDSL", "BSE", "CAMS", "MFSL", "MOTILALOFS", "HDFCAMC",
        "ANGELONE", "ICICIGI", "SBICARD", "MANAPPURAM",
        "FINPIPE", "ELGIEQUIP", "SUPRAJIT", "GREENPANEL", "ORIENTELEC",
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
    ENABLE_INDIA_VIX: bool = True
    ENABLE_MUTUAL_FUNDS: bool = True
    ENABLE_ML_PREDICTIONS: bool = False

    INDIAN_MARKET_MAX_RISK: float = 0.015
    INDIAN_INTRADAY_SL_PCT: float = 0.005
    SWING_CONFIDENCE_THRESHOLD: int = 40

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

    # ── Alert bus (integrations/alerts/) ──────────────────────────────────────
    # Events below this severity are dropped by the router before rendering/
    # sending. Order: INFO < SUCCESS < WARNING < CRITICAL < EMERGENCY.
    TELEGRAM_MIN_SEVERITY: str = "INFO"

    # ── Web research (Tavily) ─────────────────────────────────────────────────
    # Real-time news enrichment for small-caps + shortlist alert AI notes.
    # 1000 free credits/month (basic search = 1 credit, advanced = 2 credits).
    TAVILY_API_KEY: str = ""   # required — set in .env

    # ── LLM ───────────────────────────────────────────────────────────────────
    # "Mantle" naming kept for backward compat (MANTLE_API_KEY etc. are read
    # in ~8 other files) even though, as of 2026-07-24, the underlying provider
    # is Amazon Nova Pro via boto3's native Bedrock Runtime Converse API, NOT
    # the Mantle OpenAI-compatible gateway -- AWS's own model card confirms
    # Nova Pro does NOT support the bedrock-mantle endpoint (only
    # bedrock-runtime), unlike the previous provider (openai.gpt-oss-120b).
    # Same Bedrock long-term API key works for both (AWS_BEARER_TOKEN_BEDROCK),
    # confirmed live. MANTLE_BASE_URL is now unused (boto3 resolves the
    # bedrock-runtime endpoint from MANTLE_REGION instead) but left in place
    # since nothing reads it as a hard dependency.
    # Key lives in .env only (never hardcoded / committed).
    MANTLE_API_KEY:  str  = ""
    MANTLE_BASE_URL: str  = "https://bedrock-mantle.us-east-1.api.aws/v1"
    # Cross-region inference profile ("us." prefix), not the bare in-region
    # model ID -- confirmed live 2026-07-24 via AWS's service-quotas API that
    # on-demand Nova Pro is capped at just 13 requests/minute, but the
    # cross-region profile gets 25 RPM (AWS load-balances the same model
    # across multiple US regions). The ReAct tool-use loop can burn many
    # calls per single candidate, so this ceiling was the dominant cause of
    # "3 consecutive empty/unparseable responses" rejections under normal
    # production load, not just this session's own testing burst.
    MANTLE_MODEL:    str  = "nvidia.nemotron-super-3-120b"
    MANTLE_REGION:   str  = "us-east-1"
    MANTLE_PROJECT:  str  = "default"
    MANTLE_ENABLED:  bool = True
    # Floor every request here so a caller can't accidentally request a
    # budget too small to get a useful answer back.
    MANTLE_MIN_TOKENS: int = 512
    # Switched 2026-07-27 to nvidia.nemotron-super-3-120b (Bedrock Converse,
    # bare model id -- no us./cross-region prefix needed, authenticates with
    # the same AWS_BEARER_TOKEN_BEDROCK key). Verified live: maxTokens up to
    # 64000 accepted with no ValidationException (far above Nova Pro's hard
    # 10000 ceiling). Although Nemotron Super is a reasoning-capable model,
    # live probes showed it does NOT leak reasoning into the text block and
    # follows "reply with ONLY JSON" precisely (single text content block,
    # clean JSON) -- so the gpt-oss "reasoning consumed the budget -> empty
    # content" failure mode is not observed here. The generous ceiling below
    # still gives ample headroom for any internal reasoning + the answer so a
    # long ReAct turn can never be truncated mid-output.
    MANTLE_MAX_OUTPUT_TOKENS: int = 16000
    # Updated 2026-07-27 for nemotron-super-3-120b: this model's quota is
    # RPM 100 / TPM 100M (far higher than Nova Pro's 25 RPM). Every process
    # sharing this AWS account (news-engine service, Celery workers, uvicorn
    # API) still coordinates through the Redis-backed shared rate limiter
    # (see utils/llm.py::_acquire_llm_rate_slot) that proactively caps
    # combined LLM request volume at this number per 60s window. Set to 90 --
    # a 10% safety margin below the real 100 RPM ceiling (Redis coordination
    # isn't perfectly precise, and other ad hoc scripts/tests also share the
    # quota). TPM is effectively a non-constraint at this volume.
    MANTLE_MAX_RPM: int = 90
    # Ollama: local inference — no rate limits, runs on localhost
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL:    str = "qwen2.5:3b"
    OLLAMA_TIMEOUT:  float = 120.0
    # Gemini: cloud (PRIMARY) — Google Generative Language API
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL:   str = "gemini-2.5-flash"
    # Groq: cloud fast inference (SECONDARY — fallback when Gemini unavailable)
    GROQ_API_KEY:   str = ""
    GROQ_MODEL:     str = "nvidia.nemotron-super-3-120b"
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
    # Comma-separated RSS feed URLs for free news (no key required).
    # moneycontrol.com/rss/latestnews.xml removed 2026-07-31: returns a 403
    # WAF/block-page (HTML, not the feed) regardless of User-Agent/Referer/
    # Accept-Language — confirmed via direct curl, not a code-side header bug.
    # Zero successful fetches from it in the 2026-07-31 session log despite
    # ~2,676 attempts (a 24/7 poll loop hitting it every cycle) — pure wasted
    # requests and log noise, not a source that's ever coming back on its own.
    RSS_FEED_URLS: str = (
        "https://www.business-standard.com/rss/markets-106.rss,"
        "https://www.livemint.com/rss/markets,"
        "https://economictimes.indiatimes.com/markets/rss.cms"
    )

    # ── Upstox (data-only: news, fundamentals, corporate actions, shareholding) ──
    # Trading stays on Zerodha. Upstox is used exclusively for data not available
    # in Kite Connect: news, company overview, financials, shareholding, events.
    # For overlapping data (prices, candles, options) Zerodha is primary and
    # Upstox acts as cross-check / gap-fill fallback.
    UPSTOX_API_KEY:       str  = ""
    UPSTOX_API_SECRET:    str  = ""
    UPSTOX_ACCESS_TOKEN:  str  = ""
    UPSTOX_REDIRECT_URL:  str  = "http://127.0.0.1:8000/api/v1/upstox/callback"
    UPSTOX_BASE_URL:      str  = "https://api.upstox.com"

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
    # ── Path F — Tactical pipeline (shadow mode) ──────────────────────────────
    # An independent technical-signal pipeline (ORB/VWAP/pivots/mean-reversion).
    # It runs in SHADOW mode: signals are scored, sized and written to
    # tactical_signals, but nothing is executed — Path F has no execution code
    # path at all. See engine/tactical_executor.py and README_TACTICAL.md.
    # TACTICAL_EXECUTION_MODE only accepts "shadow" in Phase 1; anything else
    # raises, rather than silently shadow-running when execution was expected.
    TACTICAL_PIPELINE_ENABLED:    bool  = True
    TACTICAL_EXECUTION_MODE:      str   = "shadow"
    TACTICAL_CAPITAL:             float = 500_000.0
    TACTICAL_MAX_TOTAL_RISK:      float = 0.02    # 2% of capital, whole bucket
    TACTICAL_MAX_PER_TRADE_RISK:  float = 0.005   # 0.5% per trade
    TACTICAL_VIX_THRESHOLD:       float = 25.0
    TACTICAL_VIX_SIZE_SCALE:      float = 0.5     # halve size above the threshold
    TACTICAL_F1_UNIVERSE_SIZE:    int   = 50      # top-N by hub_universe turnover rank
    TACTICAL_F4_UNIVERSE_SIZE:    int   = 150     # 5m data covers ~1,250 symbols
    TACTICAL_MIN_COMPOSITE_SCORE: float = 50.0
    TACTICAL_MAX_SIGNALS_PER_CYCLE: int = 15
    TACTICAL_TOP_N:               int   = 5

    # ── Kite REST rate limiting (audit D6) ────────────────────────────────────
    # Kite Connect allows ~1 req/s on quote endpoints and ~10 req/s on orders.
    # Before this there was no limiter at all while five producers hit /quote
    # concurrently from separate processes. See crawler/zerodha_kite_limiter.py.
    # KITE_EXIT_RPS is a RESERVED bucket for stop-loss/exit price reads so a
    # quote flood can never delay an exit — do not merge it into KITE_QUOTE_RPS.
    KITE_LIMITER_ENABLED:  bool  = True
    KITE_QUOTE_RPS:        int   = 1
    KITE_ORDER_RPS:        int   = 10
    KITE_EXIT_RPS:         int   = 1
    KITE_LIMITER_MAX_WAIT: float = 5.0   # then fail open rather than wedge

    ZERODHA_ENABLED:       bool = False
    ZERODHA_PAPER_MODE:    bool = True
    ZERODHA_USER_ID:       str  = ""
    ZERODHA_PASSWORD:      str  = ""
    ZERODHA_TOTP_SECRET:   str  = ""

    # ── Unified decision router ──────────────────────────────────────────────
    # Single confidence gate used by paper, live, and agent execution paths
    PAPER_CONFIDENCE_THRESHOLD: float = 20.0   # min confidence for paper trade
    LIVE_CONFIDENCE_THRESHOLD:  float = 70.0   # tighter gate for live Zerodha orders
    AGENT_DRY_RUN:              bool  = False  # if true, agent logs but never executes

    # ── AI Trading Agent (Varsity-grounded) ──────────────────────────────────
    AGENT_ENABLED:              bool  = True
    AGENT_PAPER_MODE:           bool  = True
    # Base for ALL position sizing: the trade_simulator hard guard, F&O margin
    # blocking and AGENT_MAX_POSITION_WEIGHT all multiply against this. Must
    # track the wallet — leaving it at ₹20L against a ₹5L wallet on 2026-08-19
    # would have let the guard pass a 5% position of ₹1L, i.e. 20% of the real
    # book. Aligned to ₹5L with the capital reset.
    AGENT_EQUITY:               float = 500_000.0

    # Allow SELL (short) signals from the Hub 7-factor score.
    # NSE rule: equity short-selling is intraday-only (MIS product).
    # Enabled: agent can act on MEAN_REVERSION_SHORT signals (RANGE regime only).
    # Hub SELL signals also require NIFTY below EMA50 (checked in agent_loop).
    EQUITY_SHORT_ENABLED:       bool  = True
    # Extra guards for the short leg — applied even when EQUITY_SHORT_ENABLED=True.
    # Hub SELL: only allowed when Nifty is at or below its EMA50 (macro downtrend).
    # MeanReversionShort: allowed in RANGE/HIGH_VOL_RANGE regardless of Nifty trend.
    SHORT_HUB_SELL_NIFTY_GATE:  bool  = True   # block Hub SELL when Nifty > EMA50
    SHORT_MAX_VIX:              float = 28.0   # block ALL shorts when panic (VIX > 28)

    # Annualised risk-free rate (India ~6.5%). Originally added for Black-Scholes
    # Greeks, but engine/agent/performance_engine.py uses it for Sharpe/Treynor/
    # Jensen — so it survives the 2026-08-19 F&O teardown.
    RISK_FREE_RATE:             float = 0.065

    # ── Pre-Event Expectation Gap strategy (parallel to News Strategy) ─────────
    # Independent, scheduled-event-anticipation strategy (engine/pre_event_expectation_gap/).
    # Three-level gating, all default OFF (fail-closed): the master flag gates
    # whether the engine runs its ANALYSIS at all; paper/live independently gate
    # trade execution. Per the strategy spec, live must stay OFF until the replay
    # harness + paper validation produce a documented positive verdict — none of
    # these being True is what guarantees the News Strategy is completely
    # unaffected while this is built out phase by phase. Flip PRE_EVENT_GAP_ENABLED
    # on first to observe predictions with no execution.
    # ── Strong-news regime override (2026-07-27, user request) ───────────────
    # In a legitimate WEAK/BEAR regime the Phase-9 gates block ALL new longs, so
    # even a stock with a strong bullish news catalyst (Q1 beat, big order win)
    # never trades. When enabled, a BUY whose Hub news sub-score is strongly
    # positive (>= MIN_NEWS_SCORE, scale -100..100) is allowed to bypass the
    # regime + technical-pullback entry gates and the portfolio-brain halt.
    # It does NOT bypass capital/risk sizing (validate_signal), the drawdown
    # circuit-breaker, or the LLM reasoning gate — those still vet every trade.
    # Capped per cycle so a noisy news burst can't open the whole book.
    NEWS_REGIME_OVERRIDE_ENABLED: bool  = True
    NEWS_OVERRIDE_MIN_NEWS_SCORE: float = 75.0   # Hub news sub-score threshold (-100..100)
    NEWS_OVERRIDE_MAX_PER_CYCLE:  int   = 2

    # ── Grounding soft-fail for canonical-event (news) trades (2026-07-27) ───
    # When a candidate is backed by a real canonical CausalEvent (the News-Only
    # EVENT_DRIVEN path), a grounding-check failure flags only PERIPHERAL LLM
    # claims (price action, market depth, secondary metrics) — the core news
    # catalyst lives in the given event context and is never what gets flagged.
    # Hard-rejecting the whole trade over peripheral fabrications was killing
    # valid strong-news trades (LT, AUBANK, RATNAVEER …). With this on, the
    # flagged claims are stripped from the verdict, a confidence haircut is
    # applied, and the trade proceeds — the canonical-event verification +
    # materiality floor at the execution gate still vet it. Set False to restore
    # the old always-hard-reject behavior. Only affects candidates that HAVE a
    # canonical event; pure technical/model-thesis candidates stay fail-closed.
    NEWS_GROUNDING_SOFT_FAIL:        bool  = True
    NEWS_GROUNDING_SOFT_FAIL_PENALTY: float = 15.0   # confidence points to deduct

    PRE_EVENT_GAP_ENABLED:        bool  = True    # master: run the analysis engine
    PRE_EVENT_GAP_PAPER_TRADING:  bool  = True    # allow paper (virtual) execution
    PRE_EVENT_GAP_LIVE_TRADING:   bool  = True    # allow live capital (keep OFF until validated)

    # ── Direct News strategy (2026-07-27, user-validated observation) ────────
    # Trades DIRECTLY off the already-classified event materiality/sentiment
    # direction (the same classify_event() output the News strategy's LLM
    # debate consumes) -- skips the LLM-debate/grounding/technical-confirmation
    # gate entirely. Rationale: live-checked that stocks tagged BULLISH by the
    # event-intelligence system were moving up (and BEARISH ones down)
    # regardless of impact tier, while the News strategy's demand for
    # volume/momentum confirmation was skipping many of those correct-
    # direction candidates. Fully isolated from the News strategy (separate
    # StrategyFamily, separate source tag "Direct News", separate 3-flag
    # gate) -- both may fire on the same event; the duplicate-open-position
    # guard in risk_manager prevents a double entry. Sized deliberately small
    # (DIRECT_NEWS_RISK_PCT) since this is a newer, simpler strategy running
    # without the debate's confirmation layer.
    DIRECT_NEWS_ENABLED:          bool  = True
    DIRECT_NEWS_PAPER_TRADING:    bool  = True
    DIRECT_NEWS_LIVE_TRADING:     bool  = True
    # Only HIGH/MEDIUM materiality events qualify -- LOW-materiality headlines
    # (routine filings, minor announcements) are excluded even if sentiment
    # score is strong, since the classifier itself flags them as unlikely to
    # move the stock materially.
    DIRECT_NEWS_MIN_MATERIALITY: tuple = ("HIGH", "MEDIUM")
    DIRECT_NEWS_MIN_CONFIDENCE:  float = 0.65   # classify_event's own 0-1 confidence
    DIRECT_NEWS_RISK_PCT:        float = 0.5    # % of balance risked per trade (well below the normal 1-2% band)

    # Exit policy — validated OOS in Phase 2 backtest
    # partial_fixed: book 50% at T1, hold remaining to fixed T2 target (no trailing)
    # current:       book 50% at T1, trail remaining to T2 with 1×ATR trailing stop
    AGENT_EXIT_POLICY: str = "partial_fixed"

    # Risk limits — Varsity Module 9
    AGENT_MAX_RISK_PER_TRADE:   float = 0.01
    # Portfolio open-risk cap. With ≤1% risk/trade this allows ~15 concurrent
    # positions (15 × 1% = 15%), so the capital-utilization model can deploy the
    # full equity instead of being starved at the old 6% (≈6 positions).
    AGENT_MAX_OPEN_RISK:        float = 0.15
    AGENT_MAX_POSITIONS:        int   = 15   # hard cap on concurrent open positions
    AGENT_DAILY_DD_STOP:        float = 0.03
    AGENT_WEEKLY_DD_STOP:       float = 0.05
    AGENT_MONTHLY_DD_STOP:      float = 0.10
    AGENT_CASH_BUFFER_MIN:      float = 0.20
    AGENT_MAX_NEW_ENTRIES_DAY:  int   = 20   # per-day new-trade ceiling (paper: bypassed)
    AGENT_CONSEC_LOSS_LOCKOUT:  int   = 2
    AGENT_CONFIDENCE_THRESHOLD: int   = 30
    # Level-1 LLM reasoning gate: when True, candidates that clear the arithmetic
    # confidence threshold are additionally reasoned over by the LLM (bull/bear/
    # risk → TAKE/SKIP + confidence), which can veto or blend the decision. Default
    # OFF — opt-in until A/B validated. Runs only on already-qualified candidates.
    AGENT_LLM_REASONING_ENABLED: bool = True
    # Level-2: multi-agent debate. When True (and reasoning is enabled), the gate
    # runs a Bull / Bear / Risk analyst panel (in parallel) and a Judge synthesises
    # the verdict — instead of the single-pass Level-1 reasoning. ~4 LLM calls per
    # qualified candidate, so keep it for high-conviction names. Default OFF.
    AGENT_LLM_DEBATE_ENABLED:    bool = True
    AGENT_LLM_DEVILS_ADVOCATE_ENABLED: bool = True
    # Level-3: agentic tool-use. When True, the gate lets the LLM call data tools
    # (news / options / fundamentals / price_action) to investigate a candidate
    # before deciding (a ReAct loop, ≤3 tool calls). Takes priority over debate.
    # ~2-5 LLM calls + DB queries per candidate. Default OFF.
    AGENT_LLM_TOOLUSE_ENABLED:   bool = True
    # Shadow mode: when True, the reasoning gate still runs and LOGS its verdict,
    # but NEVER vetoes or blends — the arithmetic decision passes through unchanged.
    # So would-be-SKIP trades are still taken and get real outcomes, enabling an
    # unbiased A/B (did the gate's SKIPs actually avoid losers?). Default OFF.
    AGENT_LLM_SHADOW_MODE:       bool = False
    # Level-4: reflection / memory. When True, the agent reflects on each CLOSED
    # trade (entry thesis vs outcome) and stores a transferable lesson; the
    # reasoning gate then injects the most relevant recent lessons into its prompt,
    # so the agent learns from its own history. Default OFF.
    AGENT_LLM_REFLECTION_ENABLED: bool = True
    AGENT_LLM_LESSONS_IN_PROMPT:  int  = 5   # how many lessons to inject per decision
    # Portfolio-level cognitive cycle: once per trade-loop cycle, an LLM forms a
    # top-down "veteran trader" thesis over the whole book + market (regime, VIX,
    # P&L, open-position load) and returns a stance that can HALT new entries or
    # CAP how many open this cycle. SHADOW logs the thesis without acting; flip
    # shadow off to let it gate. Both default OFF — opt-in, A/B before enforcing.
    AGENT_PORTFOLIO_BRAIN_ENABLED: bool = True
    AGENT_PORTFOLIO_BRAIN_SHADOW:  bool = False
    # Include a technical/chart read (candlestick patterns, indicator states,
    # support/resistance, ML next-day forecast) in the per-candidate LLM reasoning
    # context, so the model reasons over the chart like a trader — not just the
    # numeric factors. Cheap (built from already-computed local data, no new LLM
    # call); only takes effect when AGENT_LLM_REASONING_ENABLED is also on.
    AGENT_CHART_BRIEF_ENABLED:     bool = True
    # Max fraction of equity that can be deployed in any single sector (IT, BANKING, etc.).
    # 20% means at most ₹4L of a ₹20L book can be in, say, Banking at once.
    AGENT_MAX_SECTOR_EXPOSURE:  float = 0.20
    # Max number of concurrent open positions in the same sector (count-based cap).
    # 2 means at most 2 IT stocks, 2 Banking stocks, etc. simultaneously.
    AGENT_MAX_SECTOR_POSITIONS: int   = 2
    # Momentum rotation: only trade stocks in the top N% by 63-day price momentum
    # within the scan universe. 0.50 = top half; 0.33 = top third.
    AGENT_MOMENTUM_TOP_PCT:     float = 0.50
    # Hard per-position cap as a fraction of equity.
    # 5% means at most ₹1L of a ₹20L book in any single stock (prevents a single
    # overnight gap-down from causing catastrophic drawdown).
    AGENT_MAX_POSITION_WEIGHT:  float = 0.05
    # Volatility-adjusted sizing: when India VIX rises above the high threshold,
    # position sizes are linearly reduced toward VIX_SIZE_SCALE_MIN.
    # At VIX=22 → 100% size; at VIX=30 → 50%; above 30 → floor at 50%.
    VIX_HIGH_THRESHOLD:         float = 22.0
    VIX_SIZE_SCALE_MIN:         float = 0.50
    VIX_EXTREME_THRESHOLD:      float = 30.0   # VIX at which floor kicks in

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
    HUB_UNIVERSE_MIN_TURNOVER_CR: float = 5.0   # min ₹ Cr/day to qualify

    # Price-feed watchdog: alert if no intraday (5m) candle has been written in
    # this many minutes during NSE hours (catches a frozen/stale live feed).
    CANDLE_STALENESS_ALERT_MIN: int   = 20

    # ── Fast market-shock guard ───────────────────────────────────────────────
    # A geopolitical/news shock (e.g. war headline) can gap the index down in
    # minutes — far faster than the 15-min hub cycle or score-based exits react.
    # This guard runs every 30 s: if NIFTY/BANKNIFTY falls sharply over a short
    # window (or a burst of high-severity market news lands), it proactively
    # de-risks open longs BEFORE the price-based stop is reached. Gated OFF by
    # default; enable once the thresholds are tuned to your risk appetite.
    ENABLE_SHOCK_GUARD:         bool  = False
    # Index tickers whose intraday drop defines a market-wide shock (yfinance).
    SHOCK_INDEX_SYMBOLS:        str   = "^NSEI,^NSEBANK"
    SHOCK_INDEX_WINDOW_MIN:     int   = 15     # look-back window for the index drop
    SHOCK_TIGHTEN_PCT:          float = 1.0    # index −X% in window → tighten stops to lock
    SHOCK_FLATTEN_PCT:          float = 2.0    # index −X% in window → flatten all open longs
    # High-severity market news: count negative market-shock headlines in the
    # last N minutes; at/above the threshold escalates the shock one level.
    SHOCK_NEWS_WINDOW_MIN:      int   = 20
    SHOCK_NEWS_MIN_HITS:        int   = 3
    # After a FLATTEN, block new entries for this many minutes (don't re-buy the
    # crash on the next 60 s trade-loop / 15-min hub cycle).
    SHOCK_COOLDOWN_MIN:         int   = 30

    # High-impact news alert — push a Telegram alert when a market-shock headline
    # (geopolitics / panic) with strong negative sentiment lands, so a crash-
    # capable event (e.g. the Iran ceasefire news) is never silently buried in
    # the chronological /news feed. Runs every 5 min, ALSO outside market hours
    # (news breaks after-hours). Conservative by default to avoid Telegram spam.
    ENABLE_NEWS_ALERTS:         bool  = True
    NEWS_ALERT_MIN_ABS_SCORE:   float = 0.75   # min |sentiment| to flag/alert (strict)
    NEWS_ALERT_MAX_PER_CYCLE:   int   = 5      # cap headlines per alert message

    # Universe / timing
    # Daily bars: this is the basis the strategies were designed on and the ONLY
    # basis validated by the backtest (scripts/run_backtest.py runs on 1d). ATR,
    # regime, and all entry signals are computed on this timeframe, so it also
    # sets the scale of every stop/target. Was "5m": once 5m candles backfilled
    # (2026-06-09) the agent silently switched to intraday levels (~20× smaller
    # stops/targets than daily) — an unvalidated scalping basis. Pinned back to
    # "1d" to keep live behaviour consistent with the validated backtest.
    # Intraday/scalping is a separate engine with its own backtest, not a default.
    AGENT_TIMEFRAME:            str   = "1d"
    AGENT_WARMUP_BARS:          int   = 75
    # NSE regular session: 9:15 AM – 3:30 PM IST
    # MIS (intraday) auto-squareoff: 3:20 PM IST (Zerodha)
    # Agent starts scanning at 9:15; initiates MIS close sweep at 3:15 (5 min buffer)
    AGENT_SESSION_START:        str   = "09:15"
    AGENT_SESSION_END:          str   = "15:30"
    AGENT_MIS_SQUAREOFF_TIME:   str   = "15:15"   # close MIS positions by this time
    # CNC = delivery (long only, no expiry); MIS = intraday (short selling allowed)
    # Strategies: MEAN_REVERSION_SHORT → always MIS; everything else → DEFAULT_PRODUCT
    AGENT_DEFAULT_PRODUCT:      str   = "CNC"

    # ── Intraday (MIS) daily trading ──────────────────────────────────────────
    # Morning burst: top Hub signals placed as MIS at 09:30 IST; auto-squareoff at 15:10 IST.
    # These positions are budgeted SEPARATELY from the positional CNC book.
    INTRADAY_ENABLED:              bool  = False  # swing-only mode — intraday MIS tasks self-guard on this flag
    INTRADAY_MAX_TRADES_PER_DAY:   int   = 3       # equity MIS slots per day
    INTRADAY_POSITION_SIZE_INR:    float = 150_000.0  # ₹1.5L per equity intraday trade
    INTRADAY_SL_PCT:               float = 0.005      # 0.5% stop-loss (tight intraday)
    INTRADAY_TP_PCT:               float = 0.010      # 1.0% take-profit (quick scalp)
    INTRADAY_CONFIDENCE_MIN:       float = 40.0       # min Hub score for intraday entry

    # ── Paper trading parameters ──────────────────────────────────────────────
    # Matches AGENT_EQUITY so the simulator wallet and the AI Trading Agent
    # equity start from the same base.
    # Fallback only — runtime_settings.paper_trading_balance in the DB wins,
    # and .env overrides this default. Aligned to ₹5L on 2026-08-19 so a fresh
    # DB starts at the same capital as the live one instead of ₹25L.
    PAPER_TRADING_BALANCE: float = 500_000.0
    # Late-entry gate (2026-07-22 post-mortem, news_discovery_engine.py::
    # _execute_news_trade): skip a news-triggered entry if price already moved
    # more than this % in the trade's own direction over the prior ~30min —
    # NESTLEIND was bought at the exact top of an 11:19 IST spike, TVSMOTOR at
    # a 2-session +10% extended high. Chasing a move that already happened is
    # buying someone else's exit liquidity, not anticipating the catalyst.
    NEWS_MAX_PRE_ENTRY_SPIKE_PCT: float = 2.0
    # Multi-session companion to the 30-minute check above (2026-08-18). The
    # intraday window cannot see a move that happened on PREVIOUS sessions or
    # in the opening gap, and that is exactly how BSE.NS was shorted: Jefferies
    # downgraded it on 17-Aug, the stock had already fallen 7.1% across four
    # sessions (11-Aug -2.18%, 12-Aug -0.66%, 13-Aug -1.62%, 16-Aug -2.86%),
    # and we entered the NEXT morning at 3263 — below the 3283 the news itself
    # reported as the low. It promptly bounced. The docstring on the intraday
    # gate already named this failure ("TVSMOTOR at the day high after a
    # 2-session +10% run") but only the 30-minute half was ever implemented.
    NEWS_MAX_MULTISESSION_MOVE_PCT: float = 5.0
    NEWS_MULTISESSION_LOOKBACK_DAYS: int = 3
    MAX_RISK_PER_TRADE: float = 0.02       # legacy flat risk (now superseded by conviction band)
    # Scaled 500 -> 125 on 2026-08-19 with the ₹20L -> ₹5L capital reset, to
    # keep the runaway-loop ceiling at the same multiple of the real book.
    # Still NOT the binding limit: MAX_CONCURRENT_POSITIONS (10) is.
    MAX_OPEN_POSITIONS: int = 125          # SAFETY CEILING (bug guard against a runaway loop) —
                                            # NOT a real capital limiter; MAX_PORTFOLIO_RISK/
                                            # MIN_CASH_BUFFER below do that job. Raised from 25
                                            # (2026-07-29, user request) since the count ceiling
                                            # was the actual thing rejecting new trades, not
                                            # capital exhaustion — "25/25 positions" rejections
                                            # were common while portfolio-risk utilization stayed
                                            # well under its 15% cap.
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

    # ── Diversification limits (2026-08-17 forensic post-mortem) ──────────────
    # The capital model above is deliberately count-agnostic, and MAX_OPEN_
    # POSITIONS is only a runaway-loop guard — so nothing constrained how
    # CORRELATED the book was. Measured over 3-17 Aug: peak 101 concurrent
    # positions (~69% of equity), 99% of them long, and outcomes dominated by
    # sector rather than selection —
    #     IT -18,653 | Infra -15,223 | Energy -5,446   (= -39,322)
    #     Pharma +19,310 | Metals +15,162              (= +34,472)
    # A single sector rotation therefore propagated straight to P&L. These caps
    # bound concentration WITHOUT touching the capital model: a trade is
    # rejected when its sector is already too large, or the book already holds
    # too many concurrent names, even if risk budget and cash buffer are fine.
    #
    # MAX_CONCURRENT_POSITIONS is intentionally separate from MAX_OPEN_POSITIONS
    # (500). That one guards against a runaway loop and was deliberately raised
    # from 25 on 2026-07-29 because the COUNT ceiling — not capital — was
    # rejecting trades. This is a different constraint with a different job:
    # diversification. Set it to 0 to disable.
    #
    # Scaled down 40 -> 10 on 2026-08-19 alongside the capital reset from
    # ₹20L to ₹5L. At ₹5L a 40-position book averages ~₹12.5k per name, where
    # brokerage and slippage eat the edge before the thesis can play out.
    MAX_CONCURRENT_POSITIONS:  int   = 10
    MAX_POSITIONS_PER_SECTOR:  int   = 2      # ~20% of a full 10-position book
    MAX_SECTOR_CAPITAL_PCT:    float = 0.20   # ≤20% of equity deployed into one sector

    # No single strategy may become the whole book while its edge is unproven
    # (P2-2). PRE_EVENT_EXPECTATION_GAP ran at 91% of trades / profit factor
    # 1.069. Deliberately set ABOVE current utilisation (~19% of equity, all of
    # it one strategy) so it does not disrupt today's book — it is a ceiling
    # that binds if flow recovers, not an immediate cut. Enforced on deployed
    # capital, not trade count. 0 disables.
    MAX_STRATEGY_CAPITAL_PCT:  float = 0.35

    # ── Pre-event expectation anchor (P2-1, 2026-08-17) ───────────────────────
    # The strategy trades the gap vs MARKET expectation, but both consensus and
    # guidance providers were unimplemented stubs, so every trade fell back to a
    # 3yr-CAGR proxy the code itself flags as "not a market expectation" —
    # leaving the premise unevaluated (profit factor 1.069 over 223 trades).
    # The gate below restricts trading to symbols where a real consensus exists.
    # Coverage of Indian small/mid-caps is thin (~17% of the forensic window's
    # symbols clear MIN_ANALYSTS), so this deliberately shrinks the universe.
    # Set to False to restore the old permissive behaviour.
    ENABLE_PRE_EVENT_MARKET_EXPECTATION_GATE: bool = True

    # Tier 2 macro-risk overlay on the market regime (2026-08-18). Lets macro
    # news that has not yet reached price lower the regime composite. Can only
    # ever ADD caution — see engine/macro_context.py. Set False to fall back to
    # the pure price/breadth/VIX regime.
    ENABLE_MACRO_REGIME_OVERLAY: bool = True
    CONSENSUS_MIN_ANALYSTS: int = 3        # below this it isn't a "consensus"
    CONSENSUS_CACHE_TTL:    int = 86_400   # estimates move slowly; stay off the rate limiter

    # Worker processes for score_universe's per-symbol scoring (Ichimoku/ADX/EMA-ribbon/
    # candlestick pattern detection is CPU-bound — asyncio.gather gives no real parallelism
    # for it, only a process pool does). Kept conservative on a 4-core box: this task shares
    # the machine with 4 other celery worker processes, so leave headroom rather than using
    # every core for this one job.
    HUB_SCORE_WORKERS: int = 2

    # ── Risk / trade sizing ───────────────────────────────────────────────────
    ATR_MULTIPLIER: float = 2.0       # stop = entry ± ATR × this
    MIN_RISK_REWARD: float = 2.0      # take-profit = entry ± risk × this

    # ── Breakout Auto-Discovery (breakout_screener.py) ────────────────────────
    # Runs every 5 min during NSE hours — auto-promotes small/mid-cap breakout
    # stocks (like ROTO +7%) into the Hub universe so agent never misses them.
    BREAKOUT_PCT:     float = 4.0    # min % single-day price move to qualify
    BREAKOUT_VOL_MIN: float = 2.0    # min today-vol / 20d-avg-vol ratio
    BREAKOUT_RSI_MAX: float = 85.0   # ignore blow-off tops above this RSI
    BREAKOUT_MAX_INJ: int   = 20     # max breakouts injected per 5-min cycle

    # ── Slow-Momentum Discovery (momentum_screener.py) ───────────────────────
    # Runs every 30 min — finds stocks that have been gradually rising 10-100%
    # over the past 30 days (the Eagle Eyes / multi-week trend type).
    # These are missed by breakout_screener which only catches single-day spikes.
    # Examples: SAKSOFT +55% 30d, JTEKTINDIA +16% 30d, SIGNPOST +26% 30d.
    MOMENTUM_MIN_RETURN_PCT: float = 10.0   # min % 30-day gain to qualify
    MOMENTUM_MAX_RETURN_PCT: float = 100.0  # max % 30-day gain (pump&dump guard)
    MOMENTUM_RSI_MAX:        float = 80.0   # ignore overbought stocks
    MOMENTUM_MAX_INJ:        int   = 30     # max injections per scan cycle

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
    def upstox_available(self) -> bool:
        return bool(self.UPSTOX_API_KEY and self.UPSTOX_API_SECRET)

    @property
    def upstox_authenticated(self) -> bool:
        return bool(self.UPSTOX_ACCESS_TOKEN)

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
    def mantle_available(self) -> bool:
        return bool(self.MANTLE_ENABLED and self.MANTLE_API_KEY)

    @property
    def gemini_available(self) -> bool:
        return bool(self.GEMINI_API_KEY)

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
    def narrative_telegram_channels(self) -> list[str]:
        return [s.strip().lstrip("@") for s in self.NARRATIVE_TELEGRAM_CHANNELS.split(",") if s.strip()]

    @property
    def nse_symbols(self) -> list[str]:
        return [f"{s}.NS" for s in self.WATCHLIST_NSE_LARGE_CAP]

    @property
    def nse_mid_symbols(self) -> list[str]:
        return [f"{s}.NS" for s in self.WATCHLIST_NSE_MID_CAP]

    @property
    def earnings_calendar_symbols(self) -> list[str]:
        return [f"{s}.NS" for s in self.WATCHLIST_EARNINGS_CALENDAR]

    @property
    def bse_symbols(self) -> list[str]:
        return [f"{s}.BO" for s in self.WATCHLIST_BSE_LARGE_CAP]

    @property
    def bse_mid_symbols(self) -> list[str]:
        return [f"{s}.BO" for s in self.WATCHLIST_BSE_MID_CAP]

    @property
    def all_indian_symbols(self) -> list[str]:
        return (
            self.nse_symbols
            + self.nse_mid_symbols
            + self.bse_symbols
            + self.bse_mid_symbols
            + self.WATCHLIST_NIFTY_INDICES
            + self.WATCHLIST_INDIAN_FOREX
        )


settings = Settings()
