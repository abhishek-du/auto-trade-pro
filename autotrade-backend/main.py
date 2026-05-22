# AutoTrade Pro — Main FastAPI application entry point
# PAPER TRADING MODE ONLY — No real money is ever involved.

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import analytics, india, kite, news, portfolio, settings as settings_api, signals, simulation, trades, websocket, zerodha
import db.models  # noqa: F401 — registers all ORM models on Base.metadata
from db.database import engine, init_db
from utils.config import settings
from utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    print("=" * 62)
    print("  AutoTrade Pro — PAPER TRADING MODE ACTIVE — Virtual Balance: $1000")
    print("  ⚠  FAKE/VIRTUAL CURRENCY ONLY — Real money is NEVER involved")
    print("=" * 62)

    logger.info("AutoTrade Pro starting — PAPER TRADING MODE")
    logger.info(f"Virtual balance       : ${settings.PAPER_TRADING_BALANCE:,.2f}")
    logger.info(f"Max risk per trade    : {settings.MAX_RISK_PER_TRADE * 100:.1f}%")
    logger.info(f"Max open positions    : {settings.MAX_OPEN_POSITIONS}")

    try:
        await init_db()
        logger.info("Database tables ready")
    except Exception as exc:
        logger.warning(f"DB init skipped — will retry on first request: {exc}")

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

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    _stop_event.set()
    _bg_task.cancel()
    try:
        await _bg_task
    except _asyncio.CancelledError:
        pass
    logger.info("AutoTrade Pro shutting down")
    await engine.dispose()


app = FastAPI(
    title="AutoTrade Pro",
    description=(
        "Paper Trading Simulation System — **VIRTUAL CURRENCY ONLY**. "
        "No real money is ever used or at risk."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(portfolio.router,  prefix="/api/v1/portfolio")
app.include_router(trades.router,     prefix="/api/v1/trades")
app.include_router(signals.router,    prefix="/api/v1/signals")
app.include_router(news.router,       prefix="/api/v1/news")
app.include_router(analytics.router,  prefix="/api/v1/analytics")
app.include_router(simulation.router,   prefix="/api/v1/simulation")
app.include_router(settings_api.router, prefix="/api/v1/settings")
app.include_router(websocket.router,    prefix="/ws")
app.include_router(india.router,        prefix="/api/v1/india")
app.include_router(kite.router,         prefix="/api/v1/kite")
app.include_router(zerodha.router,      prefix="/api/v1/zerodha")


# ── Core endpoints ────────────────────────────────────────────────────────────

@app.get("/", tags=["Root"])
async def root():
    """Landing info — confirms paper-trading mode to any caller."""
    return {
        "app": "AutoTrade Pro",
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
