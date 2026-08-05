import asyncio

import redis.asyncio as redis
from utils.config import settings

_redis_client = None
_redis_client_loop = None

def get_redis() -> redis.Redis:
    """Returns a shared redis.asyncio client, recreated whenever the current
    event loop differs from the one the cached client was built for.

    Celery workers run each task in its own asyncio.run() call, which
    creates AND DESTROYS a fresh event loop per invocation (see
    tasks/_db.py's celery_session() docstring for the identical problem with
    asyncpg). Without this check, a client created during task A's loop
    silently raises "RuntimeError: Event loop is closed" on every I/O call
    from task B's (new) loop -- and every caller of get_redis() that treats
    Redis as best-effort (utils/llm.py's rate limiter, integrations/alerts/
    dedup.py) catches that broadly and fails open, so the breakage never
    surfaces as an error, just as rate-limiting/dedup silently never
    triggering again after the first task in a worker's lifetime.
    """
    global _redis_client, _redis_client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if _redis_client is None or _redis_client_loop is not current_loop:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        _redis_client_loop = current_loop
    return _redis_client
