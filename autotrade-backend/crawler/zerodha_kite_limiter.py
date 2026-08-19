"""Cross-process rate limiting for Zerodha Kite REST calls (audit D6).

Why this exists
---------------
Kite Connect enforces roughly 1 req/s on the quote endpoints and 10 req/s on
orders. Before this module there was **no limiter anywhere** on the Kite REST
surface, while five independent producers hammered `/quote` and `/quote/ltp`
concurrently from *separate processes*:

    tasks.fast_sl_check            every   5s   (exit checks)
    tasks.refresh_live_prices      every  15s
    tasks.price_cache.refresh_*    every  30s
    crawler.live_snapshot          every  60s
    compute_live_pnl()             on every portfolio API read

Nothing coordinated them, so the aggregate rate was unbounded and 403/429
responses were handled only by a blunt 60s cooldown in `zerodha_market`.

Design
------
Same proven shape as `utils.llm._acquire_llm_rate_slot`: a Redis Lua
fixed-window counter shared by every process, with a bounded wait and a
**fail-open** posture.

Three deliberate properties:

1. **Fail-open.** If Redis is unreachable, or the bucket stays saturated past
   `KITE_LIMITER_MAX_WAIT`, we log once and proceed. A coordination outage must
   never wedge the trading loop — an un-throttled call that might get a 429 is
   strictly better than a stop-loss that never fires.

2. **The exit path gets its own bucket.** `Bucket.EXIT` is a separate Redis key
   from `Bucket.QUOTE`, so a burst of dashboard reads or a universe-wide scan
   can never starve `_fast_sl_check`. This is the whole reason the limiter is
   not a single global bucket: throttling exits to protect a vendor quota would
   trade a rate-limit error for a real loss.

3. **Redis client via `utils.cache.get_redis`.** That helper re-creates the
   client when the running event loop changes. Celery prefork runs each task in
   its own `asyncio.run()`, so a module-cached client would raise
   "Event loop is closed" — which the fail-open `except` would swallow, quietly
   turning the limiter off after the first task in each worker's lifetime.

Both an async and a sync variant are provided. The sync one is required because
`crawler.live_prices._fetch_kite` runs inside `run_in_executor` with no event
loop, as does everything in `crawler.zerodha_kite_lib`.
"""
from __future__ import annotations

import time as _time
from enum import Enum

from utils.config import settings
from utils.logger import logger

# Identical to utils/llm.py's script — INCR within a fixed window, EXPIRE on
# first write. Returns 1 when a slot was taken, 0 when the window is full.
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = redis.call('GET', key)
if current and tonumber(current) >= limit then
    return 0
end
local new = redis.call('INCR', key)
if new == 1 then
    redis.call('EXPIRE', key, ttl)
end
return 1
"""

_rate_limit_script = None       # async client script handle (module-cached)
_warned_once: set[str] = set()  # so a Redis outage logs once per bucket, not per call


class Bucket(str, Enum):
    """Which quota a call draws from."""

    QUOTE = "quote"   # /quote, /quote/ltp, /quote/ohlc — the noisy majority
    ORDER = "order"   # order placement/modify/cancel — higher vendor allowance
    EXIT  = "exit"    # RESERVED for stop-loss/exit price reads. Never shared.


def _limit_for(bucket: Bucket) -> int:
    """Requests per second allowed for this bucket, from settings."""
    if bucket is Bucket.ORDER:
        return int(getattr(settings, "KITE_ORDER_RPS", 10))
    if bucket is Bucket.EXIT:
        return int(getattr(settings, "KITE_EXIT_RPS", 1))
    return int(getattr(settings, "KITE_QUOTE_RPS", 1))


def _max_wait() -> float:
    return float(getattr(settings, "KITE_LIMITER_MAX_WAIT", 5.0))


def _key(bucket: Bucket) -> str:
    """One Redis key per bucket per wall-clock second."""
    return f"kite:rps:{bucket.value}:{int(_time.time())}"


def _warn_once(bucket: Bucket, msg: str) -> None:
    if bucket.value not in _warned_once:
        _warned_once.add(bucket.value)
        logger.warning(f"[kite.rate_limit] {msg}")
    else:
        logger.debug(f"[kite.rate_limit] {msg}")


async def acquire(bucket: Bucket = Bucket.QUOTE) -> None:
    """Block briefly until a slot is free in the current 1s window.

    Never raises. Fails open after `KITE_LIMITER_MAX_WAIT` seconds or on any
    Redis error.
    """
    global _rate_limit_script
    if not getattr(settings, "KITE_LIMITER_ENABLED", True):
        return

    limit = _limit_for(bucket)
    try:
        import asyncio

        from utils.cache import get_redis

        r = get_redis()
        if _rate_limit_script is None:
            _rate_limit_script = r.register_script(_RATE_LIMIT_LUA)

        deadline = _time.monotonic() + _max_wait()
        while _time.monotonic() < deadline:
            # ttl 2 (not 1) so the key cannot expire between INCR and the next
            # reader inside the same window.
            if await _rate_limit_script(keys=[_key(bucket)], args=[limit, 2]):
                return
            await asyncio.sleep(0.05)
        _warn_once(
            bucket,
            f"{bucket.value} bucket saturated for {_max_wait():.1f}s — proceeding "
            f"un-throttled (limit={limit}/s)",
        )
    except Exception as exc:
        _warn_once(
            bucket,
            f"Redis coordination unavailable for {bucket.value}, proceeding "
            f"without shared throttle: {exc}",
        )


def acquire_sync(bucket: Bucket = Bucket.QUOTE) -> None:
    """Blocking variant for executor threads and the sync `zerodha_kite_lib`.

    Creates its own short-lived client because there is no event loop here and
    `utils.cache.get_redis` returns an async client.
    """
    if not getattr(settings, "KITE_LIMITER_ENABLED", True):
        return

    limit = _limit_for(bucket)
    try:
        import redis as _redis_sync

        r = _redis_sync.from_url(settings.REDIS_URL, decode_responses=True)
        script = r.register_script(_RATE_LIMIT_LUA)
        deadline = _time.monotonic() + _max_wait()
        while _time.monotonic() < deadline:
            if script(keys=[_key(bucket)], args=[limit, 2]):
                return
            _time.sleep(0.05)
        _warn_once(
            bucket,
            f"sync {bucket.value} bucket saturated for {_max_wait():.1f}s — "
            f"proceeding un-throttled (limit={limit}/s)",
        )
    except Exception as exc:
        _warn_once(
            bucket,
            f"sync Redis coordination unavailable for {bucket.value}, "
            f"proceeding without shared throttle: {exc}",
        )
