# Celery task: refresh narrative intelligence cache every 5 minutes.
# Fetches RSS + Telegram → LLM decode → updates NARRATIVE_BOOST_CACHE
# which Intelligence Hub reads to apply sector theme boosts.

import asyncio
from tasks.celery_app import celery_app
from utils.logger import logger


def _run_async(coro):
    return asyncio.run(coro)


async def _refresh():
    from engine.narrative_engine import refresh_narrative_cache
    cache = await refresh_narrative_cache()
    return cache


@celery_app.task(name="tasks.refresh_narrative_intelligence")
def refresh_narrative_intelligence():
    """Celery task: refresh the narrative intelligence (sector boost) cache."""
    logger.info("[narrative_task] Starting narrative intelligence refresh")
    result = _run_async(_refresh())
    if result:
        logger.info(f"[narrative_task] Hot sectors: {list(result.keys())}")
    else:
        logger.info("[narrative_task] No active sector narratives detected")
