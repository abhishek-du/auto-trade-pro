# Prajna — Main FastAPI application entry point
# PAPER TRADING MODE ONLY — No real money is ever involved.

# Load .env into os.environ FIRST so that any module-level os.getenv() calls
# (e.g. api/auth.py hashing the password at import time) see the correct values.
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import agent, allocation, analytics, attribution, auth, buyback, earnings, india, intelligence, ipo_tracker, kite, mf_tracker, news, portfolio, portfolio_doctor, portfolio_tracker, settings as settings_api, signals, simulation, sip_tracker, stock_chat, tax_calculator, trades, websocket, zerodha
import db.models  # noqa: F401 — registers all ORM models on Base.metadata
from db.database import engine, init_db
from utils.config import settings
from utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    print("=" * 62)
    print("  Prajna — PAPER TRADING MODE ACTIVE — Virtual Balance: $1000")
    print("  ⚠  FAKE/VIRTUAL CURRENCY ONLY — Real money is NEVER involved")
    print("=" * 62)

    logger.info("Prajna starting — PAPER TRADING MODE")
    logger.info(f"Virtual balance       : ${settings.PAPER_TRADING_BALANCE:,.2f}")
    logger.info(f"Max risk per trade    : {settings.MAX_RISK_PER_TRADE * 100:.1f}%")
    logger.info(f"Max open positions    : {settings.MAX_OPEN_POSITIONS}")

    import asyncio as _asyncio
    for _attempt in range(5):
        try:
            await init_db()
            logger.info("Database tables ready")
            break
        except Exception as exc:
            if _attempt < 4:
                _wait = 5 * (_attempt + 1)
                logger.warning(f"DB init attempt {_attempt+1} failed ({type(exc).__name__}) — retrying in {_wait}s")
                await _asyncio.sleep(_wait)
            else:
                logger.warning(f"DB init skipped after 5 attempts — will retry on first request: {exc}")

    # ── Preload NSE token map from kite_instruments ──────────────────────────
    # The hardcoded NSE_TOKENS dict only covers ~30 large-caps. If the daily
    # refresh hasn't run yet (fresh deploy, post-truncate, weekend), historical
    # fetches for everything else log "No instrument token" warnings. Hydrate
    # the in-memory map from the DB on startup so every persisted instrument
    # is immediately resolvable.
    try:
        from db.database import AsyncSessionLocal
        from crawler.zerodha_market import hydrate_tokens_from_db
        async with AsyncSessionLocal() as _sess:
            await hydrate_tokens_from_db(_sess)
    except Exception as exc:
        logger.debug(f"[startup] kite token hydration skipped: {exc}")

    # ── Refresh live instrument token cache from Kite ────────────────────────
    # hydrate_tokens_from_db fills NSE_TOKENS (zerodha_market); this fills
    # INSTRUMENT_CACHE (zerodha_instruments) used by zerodha_historical.
    try:
        from crawler.zerodha_instruments import refresh_instrument_cache
        _n = await refresh_instrument_cache()
        logger.info(f"[startup] Kite instrument cache loaded: {_n} symbols")
    except Exception as exc:
        logger.debug(f"[startup] Kite instrument cache skipped: {exc}")

    # ── Live price refresh background task ───────────────────────────────────
    import asyncio as _asyncio
    from crawler.india_price_feed import is_nse_market_open
    from crawler.live_prices import refresh_all_prices
    from api.websocket import live_price_manager

    _stop_event = _asyncio.Event()

    async def _live_price_loop():
        # Initial warm-up fetch
        try:
            await refresh_all_prices()
        except Exception as exc:
            logger.warning(f"[live_prices] Initial fetch failed: {exc}")

        while not _stop_event.is_set():
            interval = 15 if is_nse_market_open() else 60
            try:
                await _asyncio.sleep(interval)
                if _stop_event.is_set():
                    break
                updated = await refresh_all_prices()
                if live_price_manager.connections:
                    await live_price_manager.broadcast_prices(updated)
                    logger.debug(
                        f"[live_prices] Broadcast to "
                        f"{len(live_price_manager.connections)} clients"
                    )
            except Exception as exc:
                logger.warning(f"[live_prices] Refresh error: {exc}")

    _bg_task = _asyncio.create_task(_live_price_loop())

    # Warm up INFO_CACHE (PE, market cap, beta…) in the background so first
    # watchlist page load has fundamental data without waiting 24 h.
    async def _warmup_info_cache():
        await _asyncio.sleep(10)  # let the price loop do its first fetch first
        try:
            from crawler.live_prices import refresh_info_cache
            nse = settings.nse_symbols + settings.nse_mid_symbols
            await refresh_info_cache(nse)
        except Exception as exc:
            logger.warning(f"[info_cache] Warmup failed: {exc}")

    _asyncio.create_task(_warmup_info_cache())

    # ── Kite WebSocket ticker ────────────────────────────────────────────────
    # Start whenever Zerodha is enabled + token is present — market-hours check
    # was removed so a mid-session backend restart auto-reconnects the feed.
    # The `kite-start-ticker-on-open` Celery cron (03:45 UTC = 09:15 IST) also
    # fires at market open as a belt-and-suspenders guarantee.
    if settings.ZERODHA_ENABLED and settings.ZERODHA_ACCESS_TOKEN:
        try:
            from crawler.zerodha_ticker import start_kite_ticker
            await _asyncio.to_thread(start_kite_ticker)
            logger.info("Kite WebSocket ticker started on app startup")
        except Exception as exc:
            logger.warning(f"Kite ticker startup failed: {exc}")

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    _stop_event.set()
    _bg_task.cancel()
    try:
        await _bg_task
    except _asyncio.CancelledError:
        pass
    logger.info("Prajna shutting down")
    await engine.dispose()


app = FastAPI(
    title="Prajna",
    description=(
        "Paper Trading Simulation System — **VIRTUAL CURRENCY ONLY**. "
        "No real money is ever used or at risk."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────
# CORS spec rejects `allow_origins=["*"]` together with `allow_credentials=True`
# (the browser silently drops the response). Set CORS_ORIGINS in .env as a
# comma-separated list. Falls back to localhost dev URLs when not set.
from utils.config import settings as _settings
_cors_env = [o.strip() for o in (_settings.CORS_ORIGINS or "").split(",") if o.strip()]
_cors_origins = _cors_env or [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(portfolio.router,         prefix="/api/v1/portfolio")
app.include_router(portfolio_doctor.router, prefix="/api/v1/doctor")
app.include_router(earnings.router,        prefix="/api/v1/earnings")
app.include_router(agent.router,           prefix="/api/v1/agent")
app.include_router(intelligence.router,    prefix="/api/v1/intelligence")
app.include_router(portfolio_tracker.router, prefix="/api/v1/portfolios")
app.include_router(mf_tracker.router,       prefix="/api/v1/mf-tracker")
app.include_router(sip_tracker.router,      prefix="/api/v1/sip")
app.include_router(tax_calculator.router,   prefix="/api/v1/tax")
app.include_router(allocation.router,       prefix="/api/v1/allocation")
app.include_router(ipo_tracker.router,      prefix="/api/v1/ipo")
app.include_router(stock_chat.router,       prefix="/api/v1/chat")
app.include_router(trades.router,     prefix="/api/v1/trades")
app.include_router(signals.router,    prefix="/api/v1/signals")
app.include_router(news.router,       prefix="/api/v1/news")
app.include_router(analytics.router,    prefix="/api/v1/analytics")
app.include_router(attribution.router,  prefix="/api/v1/analytics")
app.include_router(simulation.router,   prefix="/api/v1/simulation")
app.include_router(settings_api.router, prefix="/api/v1/settings")
app.include_router(websocket.router,    prefix="/ws")
app.include_router(india.router,        prefix="/api/v1/india")
app.include_router(kite.router,         prefix="/api/v1/kite")
app.include_router(zerodha.router,      prefix="/api/v1/zerodha")
app.include_router(auth.router,         prefix="/api/v1/auth")
app.include_router(buyback.router,      prefix="/api/v1/buyback")


# ── Core endpoints ────────────────────────────────────────────────────────────

@app.get("/", tags=["Root"])
async def root():
    """Landing info — confirms paper-trading mode to any caller."""
    return {
        "app": "Prajna",
        "mode": "PAPER TRADING — VIRTUAL CURRENCY ONLY",
        "disclaimer": (
            "This system uses FAKE/VIRTUAL currency. "
            "No real money is involved at any stage."
        ),
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Lightweight liveness probe used by Docker / load-balancers."""
    return {
        "status": "ok",
        "mode": "PAPER_TRADING",
        "real_money_involved": False,
        "virtual_balance": settings.PAPER_TRADING_BALANCE,
        "max_open_positions": settings.MAX_OPEN_POSITIONS,
    }
