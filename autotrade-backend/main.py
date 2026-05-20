# AutoTrade Pro — Main FastAPI application entry point
# PAPER TRADING MODE ONLY — No real money is ever involved.

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import analytics, india, news, portfolio, settings as settings_api, signals, simulation, trades, websocket
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

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
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
