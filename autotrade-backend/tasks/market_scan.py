# Celery task: fetch OHLCV candles for all watchlist symbols every 30 s.

import asyncio

from tasks.celery_app import celery_app
from utils.logger import logger


def _run_async(coro):
    return asyncio.run(coro)


async def _scan():
    from db.database import engine, AsyncSessionLocal
    from crawler.price_feed import run_price_crawl

    try:
        async with AsyncSessionLocal() as session:
            result = await run_price_crawl(session)
            await session.commit()

        logger.info(
            f"[market_scan] symbols={result['total_symbols']}  "
            f"fetched={result['total_candles_fetched']}  "
            f"saved={result['total_candles_saved']}  "
            f"errors={len(result['errors'])}"
        )
    finally:
        # Close all pooled asyncpg connections while the event loop is still
        # running.  Without this, asyncio.run() shuts the loop first and
        # SQLAlchemy's synchronous teardown raises MissingGreenlet / "Event
        # loop is closed" trying to close the async connections.
        await engine.dispose()


@celery_app.task(name="tasks.market_scan.scan_watchlist")
def scan_watchlist():
    """Celery task: crawl OHLCV candles for every watchlist symbol."""
    logger.info("[market_scan] Starting price crawl")
    _run_async(_scan())
