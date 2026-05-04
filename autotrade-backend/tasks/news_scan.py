# Celery task: fetch news headlines, score sentiment, persist — every 5 min.

import asyncio

from tasks.celery_app import celery_app
from utils.logger import logger


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _crawl():
    from db.database import AsyncSessionLocal
    from crawler.news_crawler import run_news_crawl

    async with AsyncSessionLocal() as session:
        result = await run_news_crawl(session)
        await session.commit()

    logger.info(
        f"[news_scan] fetched={result['total_fetched']}  "
        f"saved={result['total_saved']}  "
        f"errors={len(result['errors'])}"
    )


@celery_app.task(name="tasks.news_scan.scan_news")
def scan_news():
    """Celery task: crawl news and persist FinBERT-scored headlines."""
    logger.info("[news_scan] Starting news crawl")
    _run_async(_crawl())
