"""Upstox rate limiting — replaces crawler/zerodha_kite_limiter.py.

WHY A SEPARATE EXIT BUCKET STILL EXISTS
---------------------------------------
Under Kite this codebase ran KITE_QUOTE_RPS=1 alongside a RESERVED
KITE_EXIT_RPS=1, deliberately not merged, so that a universe scan or a
dashboard burst could never make a stop-loss check queue behind it. That
isolation is load-bearing for exits and is preserved here unchanged in spirit,
even though Upstox's limits are far more generous:

    Upstox standard APIs : 50/sec, 500/min, 2000/30min
    Upstox order APIs    : 10/sec (regular)
    Kite (what we had)   : 1/sec quotes

The headroom means throttling will rarely bite. The bucket split is kept anyway
because the failure it prevents -- a delayed stop-loss -- is expensive and the
cost of keeping it is one counter.

Redis-backed when available so the limit is shared across the five worker
processes; falls back to a per-process limiter when Redis is unreachable. Fails
OPEN after UPSTOX_LIMITER_MAX_WAIT seconds: a coordination outage must never
wedge trading, exactly as the Kite limiter documented.
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum

from utils.config import settings
from utils.logger import logger


class Bucket(str, Enum):
    QUOTE = "quote"
    EXIT = "exit"
    ORDER = "order"


def _rps(bucket: Bucket) -> int:
    if bucket is Bucket.ORDER:
        return int(getattr(settings, "UPSTOX_ORDER_RPS", 10))
    if bucket is Bucket.EXIT:
        return int(getattr(settings, "UPSTOX_EXIT_RPS", 10))
    return int(getattr(settings, "UPSTOX_QUOTE_RPS", 40))


def _max_wait() -> float:
    return float(getattr(settings, "UPSTOX_LIMITER_MAX_WAIT", 5.0))


# Per-process fallback state: bucket -> (window_start, count)
_LOCAL: dict[str, list] = {}
_LOCK = asyncio.Lock()


async def _acquire_local(bucket: Bucket) -> bool:
    """Fixed-window counter. Coarse, but only the fallback path."""
    rps = _rps(bucket)
    deadline = time.monotonic() + _max_wait()
    while True:
        async with _LOCK:
            now = time.monotonic()
            win = _LOCAL.setdefault(bucket.value, [now, 0])
            if now - win[0] >= 1.0:
                win[0], win[1] = now, 0
            if win[1] < rps:
                win[1] += 1
                return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.02)


async def acquire(*, bucket: Bucket | None = None, exit_bucket: bool = False) -> bool:
    """Take one slot. Returns True if granted, False if it failed open.

    `exit_bucket=True` is the compatibility spelling used throughout the
    existing quote call sites; it selects the reserved exit quota.
    """
    if not bool(getattr(settings, "UPSTOX_LIMITER_ENABLED", True)):
        return True
    b = bucket or (Bucket.EXIT if exit_bucket else Bucket.QUOTE)

    try:
        from utils.cache import get_redis

        redis = get_redis()
        key = f"upstox:rl:{b.value}:{int(time.time())}"
        deadline = time.monotonic() + _max_wait()
        rps = _rps(b)
        while True:
            n = await redis.incr(key)
            if n == 1:
                await redis.expire(key, 2)
            if n <= rps:
                return True
            if time.monotonic() >= deadline:
                logger.debug(f"[upstox_limiter] {b.value} bucket wait exceeded — failing open")
                return False
            await asyncio.sleep(0.02)
            key = f"upstox:rl:{b.value}:{int(time.time())}"
    except Exception:
        # Redis unavailable — degrade to the per-process limiter rather than
        # blocking. Same posture as the Kite limiter it replaces.
        return await _acquire_local(b)
