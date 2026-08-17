# Prajna — Main FastAPI application entry point
# PAPER TRADING MODE ONLY — No real money is ever involved.

# Load .env into os.environ FIRST so that any module-level os.getenv() calls
# (e.g. api/auth.py hashing the password at import time) see the correct values.
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import agent, allocation, analytics, attribution, auth, buyback, earnings, india, intelligence, ipo_tracker, kite, mf_tracker, news, portfolio, portfolio_doctor, portfolio_tracker, settings as settings_api, signals, simulation, sip_tracker, stock_chat, tax_calculator, trades, upstox, websocket, zerodha
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

    # ── Safety-bounds check — refuse to boot with dangerous config ────────────
    _config_errors: list[str] = []
    if settings.MAX_RISK_PER_TRADE > 0.05:
        _config_errors.append(
            f"MAX_RISK_PER_TRADE={settings.MAX_RISK_PER_TRADE} (>{5}%) — "
            f"max safe value is 0.05 (5%)"
        )
    if getattr(settings, "MAX_PORTFOLIO_RISK", 1.0) > 0.50:
        _config_errors.append(
            f"MAX_PORTFOLIO_RISK={getattr(settings, 'MAX_PORTFOLIO_RISK', 'N/A')} (>{50}%) — "
            f"max safe value is 0.50 (50%)"
        )
    _rmin = getattr(settings, "RISK_PER_TRADE_MIN", 0.01)
    _rmax = getattr(settings, "RISK_PER_TRADE_MAX", 0.02)
    if _rmin > 0.05 or _rmax > 0.05:
        _config_errors.append(
            f"RISK_PER_TRADE_MIN={_rmin}/MAX={_rmax} — "
            f"conviction band must not exceed 0.05 (5%)"
        )
    if _config_errors:
        for _err in _config_errors:
            logger.critical(f"[SAFETY] CONFIG VIOLATION: {_err}")
        raise SystemExit(
            "\n\n🚨 STARTUP BLOCKED — unsafe risk configuration detected.\n"
            + "\n".join(f"  • {e}" for e in _config_errors)
            + "\n\nFix the values in .env and restart.\n"
        )

    import asyncio as _asyncio

    async def _init_db_with_retries() -> None:
        """Run schema DDL OFF the startup path (2026-08-17).

        init_db() issues DDL (create_all / ADD COLUMN IF NOT EXISTS) needing an
        ACCESS EXCLUSIVE lock. Long-lived read transactions elsewhere hold
        conflicting locks — observed live: three connections idle-in-transaction
        on news_items for 1-4 min (crawler/news_crawler.py holds a read txn open
        across slow LLM sentiment work) with an ALTER TABLE blocked behind them
        for 76s. DDL then waits indefinitely.

        This previously ran INLINE and had been commented out entirely to stop
        it hanging boot — which silently meant a fresh deploy or wiped DB never
        got its schema. Awaiting it inline (even with a timeout) is also wrong:
        uvicorn cannot service SIGTERM while lifespan startup is still running,
        so a blocked DDL made the process unkillable except by SIGKILL and left
        it not serving. Backgrounding it keeps the schema work AND lets startup
        complete immediately.
        """
        for _attempt in range(5):
            try:
                await _asyncio.wait_for(init_db(), timeout=20)
                logger.info("Database tables ready")
                return
            except _asyncio.TimeoutError:
                logger.warning(
                    f"DB init attempt {_attempt+1} timed out after 20s — DDL is "
                    "blocked, most likely behind an idle-in-transaction session "
                    "(check pg_stat_activity for Lock/relation waits)"
                )
            except _asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    f"DB init attempt {_attempt+1} failed ({type(exc).__name__}): {exc}")
            if _attempt < 4:
                await _asyncio.sleep(5 * (_attempt + 1))
        logger.warning("DB init gave up after 5 attempts — schema may be stale")

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
    from crawler.live_prices import hydrate_prices_from_redis
    from api.websocket import live_price_manager

    _stop_event = _asyncio.Event()
    # Tracked so shutdown can cancel exactly these and nothing else.
    _bg_tasks: list[_asyncio.Task] = []

    # Schema DDL runs here, off the startup path — see _init_db_with_retries.
    _bg_tasks.append(_asyncio.create_task(_init_db_with_retries()))

    async def _live_price_loop():
        """Hydrate this process's PRICE_CACHE from Redis and fan out to clients.

        The actual broker/vendor fetch moved to tasks.price_cache (2026-08-17).
        It used to run HERE, taking 4-23s per cycle inside the same event loop
        that serves requests — /health, a static dict with no I/O, was answering
        in 1.3-3.8s on 25 of 25 probes because of it. What remains is one Redis
        GET plus a dict merge.

        PRICE_CACHE itself is unchanged: 141 read sites across 29 modules keep
        reading the same in-process dict, and the Kite WebSocket ticker still
        writes sub-second ticks into it directly during market hours (hydrate
        will not overwrite those).
        """
        try:
            await hydrate_prices_from_redis()
        except Exception as exc:
            logger.warning(f"[live_prices] Initial hydrate failed: {exc}")

        while not _stop_event.is_set():
            # Cheap now, so poll at the fast cadence regardless of session —
            # freshness is bounded by the publisher's 30s tick, not by this.
            interval = 15
            try:
                await _asyncio.sleep(interval)
                if _stop_event.is_set():
                    break
                updated = await hydrate_prices_from_redis()
                if live_price_manager.connections:
                    await live_price_manager.broadcast_prices(updated)
                    logger.debug(
                        f"[live_prices] Broadcast to "
                        f"{len(live_price_manager.connections)} clients"
                    )
            except Exception as exc:
                logger.warning(f"[live_prices] Hydrate error: {exc}")

    _bg_tasks.append(_asyncio.create_task(_live_price_loop()))

    # ── Breadth refresh background task (NSE advances/declines) ─────────────
    # Celery worker has its own in-memory BREADTH_CACHE that the regime engine
    # uses. Uvicorn needs its own background loop so the /breadth API endpoint
    # stays current in this process too.
    async def _breadth_loop():
        await _asyncio.sleep(20)   # wait for first price-cache fetch
        while not _stop_event.is_set():
            try:
                from crawler.market_breadth import refresh_breadth_data
                await refresh_breadth_data()
            except Exception as exc:
                logger.warning(f"[breadth] Refresh error: {exc}")
            try:
                await _asyncio.wait_for(_stop_event.wait(), timeout=120)
                break
            except _asyncio.TimeoutError:
                pass

    _bg_tasks.append(_asyncio.create_task(_breadth_loop()))

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

    _bg_tasks.append(_asyncio.create_task(_warmup_info_cache()))

    # ── Kite WebSocket ticker ────────────────────────────────────────────────
    # Start whenever Zerodha is enabled + token is present — market-hours check
    # was removed so a mid-session backend restart auto-reconnects the feed.
    # The `kite-start-ticker-on-open` Celery cron (03:45 UTC = 09:15 IST) also
    # fires at market open as a belt-and-suspenders guarantee.
    if settings.ZERODHA_ENABLED and settings.ZERODHA_ACCESS_TOKEN:
        try:
            import threading as _threading
            from crawler.zerodha_ticker import start_kite_ticker
            # Explicit DAEMON thread, not asyncio.to_thread (2026-08-17).
            # to_thread runs on the default ThreadPoolExecutor, whose workers
            # are non-daemon and are JOINED at interpreter exit. The Kite
            # ticker owns a long-lived WebSocket loop that never returns, so
            # that join blocked process exit indefinitely — contributing to
            # every shutdown needing SIGKILL. A daemon thread is torn down with
            # the process instead. (The ticker holds no un-flushed state: it
            # only writes to the in-memory LIVE_TICKS/PRICE_CACHE dicts.)
            _threading.Thread(
                target=start_kite_ticker, name="kite-ticker", daemon=True,
            ).start()
            logger.info("Kite WebSocket ticker started on app startup")
        except Exception as exc:
            logger.warning(f"Kite ticker startup failed: {exc}")

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    # Cancel only OUR OWN background tasks. The previous version cancelled
    # asyncio.all_tasks() indiscriminately, which also cancelled uvicorn's own
    # server/connection tasks mid-shutdown and left the loop unable to finish
    # its sequence — every stop then hit systemd's TimeoutStopSec and was
    # SIGKILLed ("State 'stop-sigterm' timed out. Killing." on 04-Aug, 06-Aug
    # and 17-Aug). A SIGKILLed process never runs this block at all, so
    # connections and the engine pool were never released cleanly either.
    _stop_event.set()
    for _task in _bg_tasks:
        _task.cancel()
    # Bounded: a task stuck in un-cancellable C code must not hold up shutdown.
    if _bg_tasks:
        await _asyncio.wait(_bg_tasks, timeout=5)
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
app.include_router(upstox.router,       prefix="/api/v1/upstox")


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
