# Celery task: own the live-price fetch and publish it for every other process.
#
# Why this exists (2026-08-17)
# ---------------------------
# refresh_all_prices() used to run inside the API process's own lifespan loop.
# It takes 4-23s per cycle (broker + vendor calls across the whole watchlist),
# and that cost was paid by the event loop serving requests: /health — a static
# dict with no I/O — answered in 1.3-3.8s on 25 of 25 consecutive probes, and
# /api/v1/intelligence/context intermittently timed out.
#
# PRICE_CACHE is a module-level dict with 141 read sites across 29 modules, so
# the read path is deliberately left alone. Only the expensive half moves: this
# task does the fetch once and publishes a snapshot to Redis; the API's loop
# becomes a cheap Redis GET that hydrates its own PRICE_CACHE
# (crawler.live_prices.hydrate_prices_from_redis).
#
# The API keeps its Kite WebSocket ticker, which writes sub-second prices
# straight into PRICE_CACHE during market hours — hydrate deliberately does not
# overwrite those. This task is the polling/enrichment tier, not the live tick.

import asyncio

from tasks.celery_app import celery_app
from utils.logger import logger


def _run_async(coro):
    return asyncio.run(coro)


async def _refresh_and_publish() -> dict:
    from crawler.live_prices import refresh_all_prices, publish_prices_to_redis

    prices = await refresh_all_prices()
    published = await publish_prices_to_redis(prices)
    return {"symbols": len(prices), "published": published}


@celery_app.task(
    name="tasks.price_cache.refresh_price_cache",
    # The fetch itself was measured at 4-23s; the ceiling covers a slow vendor
    # tier without inheriting the 300s global default, and stays under the
    # overlap-guard TTL below.
    soft_time_limit=120,
    time_limit=150,
)
def refresh_price_cache():
    """Fetch the live-price snapshot and publish it to Redis."""
    from utils.config import settings
    import redis.asyncio as _aioredis

    # Same SET NX EX overlap guard as india_price_scan / kite_live_candles /
    # news_scan: a run that outlives its tick must not stack with the next one.
    async def _acquire() -> bool:
        r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            return bool(await r.set("price_cache_refresh:running", "1", nx=True, ex=180))
        finally:
            await r.aclose()

    async def _release():
        r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await r.delete("price_cache_refresh:running")
        finally:
            await r.aclose()

    if not _run_async(_acquire()):
        return {"skipped": "already_running"}

    try:
        result = _run_async(_refresh_and_publish())
        logger.info(
            f"[price_cache] refreshed {result['symbols']} symbols, "
            f"published {result['published']} to redis"
        )
        return result
    finally:
        _run_async(_release())
