# Celery task: fetch OHLCV candles for all watchlist symbols every 30 s.

import asyncio

from tasks.celery_app import celery_app
from utils.logger import logger


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _scan():
    from db.database import AsyncSessionLocal
    from crawler.price_feed import run_price_crawl

    async with AsyncSessionLocal() as session:
        result = await run_price_crawl(session)
        await session.commit()

    logger.info(
        f"[market_scan] symbols={result['total_symbols']}  "
        f"fetched={result['total_candles_fetched']}  "
        f"saved={result['total_candles_saved']}  "
        f"errors={len(result['errors'])}"
    )


@celery_app.task(name="tasks.market_scan.scan_watchlist")
def scan_watchlist():
    """Celery task: crawl OHLCV candles for every watchlist symbol."""
    logger.info("[market_scan] Starting price crawl")
    _run_async(_scan())
