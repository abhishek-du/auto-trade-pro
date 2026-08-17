# Celery task: fetch news headlines, score sentiment, persist.

import asyncio

from tasks.celery_app import celery_app
from utils.logger import logger


def _run_async(coro):
    return asyncio.run(coro)


async def _crawl():
    from crawler.news_crawler import run_news_crawl
    from tasks._db import celery_session

    async with celery_session() as session:
        result = await run_news_crawl(session)
        await session.commit()

    logger.info(
        f"[news_scan] fetched={result['total_fetched']}  "
        f"saved={result['total_saved']}  "
        f"errors={len(result['errors'])}"
    )


# Third occurrence of the same bug class in this codebase (2026-08-17), after
# kite_live_candles_task and india_price_scan: a task with no time limit of its
# own, inheriting the 300s global default, whose real runtime had grown well
# past it -- and scheduled far more often than it can possibly complete.
#
# What happened: while the Bedrock key was invalid (2026-08-08 to 2026-08-17)
# every classify_event() call failed instantly through the circuit breaker, so
# crawls were fast and fit inside 300s. The moment the LLM was restored, each
# crawl started doing its real work again -- a busy crawl-minute needs up to 45
# classify_event() round-trips (measured on 2026-08-07's data), each 2-4s, each
# retried up to 4x on malformed JSON, all sharing one 90 RPM limiter with the
# trade loop and hub -- and every crawl began dying at the soft limit. 171
# SoftTimeLimitExceeded, and no news persisted at all after 12:09.
#
# Limits are set from a measured run rather than guessed; see the commit
# message. The Redis overlap guard is the same SET NX EX pattern as
# india_tasks.py's two, with the key TTL above the hard limit so a killed task
# can never strand the lock.
@celery_app.task(
    name="tasks.news_scan.scan_news",
    soft_time_limit=900,   # 15 min — headroom over the observed worst case
    time_limit=960,
)
def scan_news():
    """Celery task: crawl news and persist FinBERT-scored headlines."""
    from utils.config import settings
    import redis.asyncio as _aioredis

    async def _acquire_lock() -> bool:
        _r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            return bool(await _r.set("news_scan:running", "1", nx=True, ex=1020))
        finally:
            await _r.aclose()

    async def _release_lock():
        _r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await _r.delete("news_scan:running")
        finally:
            await _r.aclose()

    if not _run_async(_acquire_lock()):
        logger.info("[news_scan] previous crawl still running — skipping this tick")
        return {"skipped": "already_running"}

    logger.info("[news_scan] Starting news crawl")
    try:
        _run_async(_crawl())
    finally:
        # Released even on SoftTimeLimitExceeded, so one overrun doesn't lock
        # the crawl out until the key's TTL expires.
        _run_async(_release_lock())
