import asyncio
from utils.logger import logger
from tasks.celery_app import celery_app

async def _pre_diagnose_symbols(symbols: list[str]):
    from api.zerodha import _run_deep_analysis_core
    import json
    import redis.asyncio as aioredis
    from utils.config import settings

    # Celery prefork runs this task via asyncio.run() (see run_pre_diagnose
    # below), which creates AND destroys a fresh event loop per invocation --
    # same situation tasks/_db.py documents for DB connections. utils.cache
    # .get_redis() caches its client at process level forever, so a client
    # created on invocation N's loop is already bound to a dead loop by
    # invocation N+1, raising "Event loop is closed" (observed 11x/day).
    # Scope a fresh client to this call instead, mirroring celery_session()'s
    # NullPool-per-call fix for the same class of bug.
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        for sym in symbols:
            sym = sym.strip().upper().replace(".NS", "")
            
            # Skip SME and illiquid stocks (which lack historical data and throw 404/422)
            if sym.endswith(("-SM", "-ST", "-BE")):
                logger.debug(f"[pre_diagnose] Skipping SME/illiquid stock: {sym}")
                continue

            cache_key = f"deep_analysis:{sym}"
            try:
                # We skip if already cached within the last 10 minutes to save Groq credits
                if await redis.exists(cache_key):
                    continue

                logger.info(f"[pre_diagnose] Running deep analysis for {sym}")
                result = await _run_deep_analysis_core(sym)
                await redis.setex(cache_key, 900, json.dumps(result))
                await asyncio.sleep(2) # rate limit protection
            except Exception as exc:
                logger.error(f"[pre_diagnose] Failed for {sym}: {exc}")
    finally:
        await redis.aclose()

@celery_app.task(name="tasks.pre_diagnose.run_pre_diagnose")
def run_pre_diagnose(symbols: list[str]):
    asyncio.run(_pre_diagnose_symbols(symbols))
