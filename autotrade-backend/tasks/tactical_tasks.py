"""Path F — Celery entry points (shadow mode).

These tasks run the Tactical pipeline on a schedule. They open no positions;
see engine/tactical_executor.py for why that is structural rather than
configurable.

Two operational constraints shaped this file:

1. **Overlap guards.** Redis `SET NX EX`, released in `finally`, with a TTL
   above the task's hard limit — the same idiom as `price_cache`,
   `india_price_scan`, `kite_live_candles` and `news_scan`. F1 fires every
   minute, so a run that outlives its tick absolutely will stack without this.

2. **The worker is `--concurrency=2` on a single queue**, shared with the 5s
   `fast_sl_check` exit loop. Tactical scans must therefore be time-boxed and
   give the slot back promptly. Soft limit 50s / hard 60s, and the executor
   catches `SoftTimeLimitExceeded` and abandons the cycle cleanly rather than
   being killed mid-transaction.

The brief's sample code imported `utils.redis_client.redis_client`, which does
not exist in this repo. `utils.cache.get_redis()` is the real helper, but it
returns an **async** client keyed on the running event loop; Celery prefork runs
each task in its own `asyncio.run()`, so we build a short-lived client inside
the coroutine exactly as `price_cache` does.
"""
from __future__ import annotations

import asyncio

from tasks.celery_app import celery_app
from utils.logger import logger

_LOCK_TTL = 65  # > time_limit (60s) so a killed task's lock still expires


def _run_guarded(lock_key: str, coro_factory) -> dict:
    """Run an async scan under a Redis overlap guard. Never raises."""
    from utils.config import settings
    import redis.asyncio as _aioredis

    async def _main() -> dict:
        r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            acquired = bool(await r.set(lock_key, "1", nx=True, ex=_LOCK_TTL))
            if not acquired:
                logger.debug(f"[tactical] {lock_key} held — previous cycle still running")
                return {"skipped": "overlap guard"}
            try:
                return await coro_factory()
            finally:
                await r.delete(lock_key)
        finally:
            await r.aclose()

    try:
        return asyncio.run(_main())
    except Exception as exc:
        # A scan failure must never poison the worker or retry-storm a 1-minute
        # beat; log and return.
        logger.error(f"[tactical] {lock_key} failed: {type(exc).__name__}: {exc}")
        return {"error": str(exc)}


@celery_app.task(
    name="tasks.tactical_tasks.run_tactical_intraday",
    soft_time_limit=50,
    time_limit=60,
)
def run_tactical_intraday() -> dict:
    """F1 — intraday momentum scan on 1-minute candles. Every minute."""

    async def _scan():
        from engine.tactical_executor import TacticalExecutor

        return await TacticalExecutor().run_intraday_scan()

    return _run_guarded("tactical_intraday:running", _scan)


@celery_app.task(
    name="tasks.tactical_tasks.run_tactical_mean_reversion",
    soft_time_limit=50,
    time_limit=60,
)
def run_tactical_mean_reversion() -> dict:
    """F4 — mean-reversion scan on 5-minute candles. Every 5 minutes."""

    async def _scan():
        from engine.tactical_executor import TacticalExecutor

        return await TacticalExecutor().run_mean_reversion_scan()

    return _run_guarded("tactical_mean_reversion:running", _scan)
