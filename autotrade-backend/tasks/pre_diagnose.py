import asyncio
from utils.logger import logger
from tasks.celery_app import celery_app

async def _pre_diagnose_symbols(symbols: list[str]):
    from api.zerodha import _run_deep_analysis_core
    from utils.cache import get_redis
    import json
    redis = get_redis()
    
    for sym in symbols:
        sym = sym.strip().upper().replace(".NS", "")
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

@celery_app.task(name="tasks.pre_diagnose.run_pre_diagnose")
def run_pre_diagnose(symbols: list[str]):
    asyncio.run(_pre_diagnose_symbols(symbols))
