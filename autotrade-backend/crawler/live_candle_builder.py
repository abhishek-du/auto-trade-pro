"""Path F — a fast 1-minute candle lane built from the live tick stream.

WHY THIS EXISTS
---------------
`kite_live_candles` bulk-fetches thousands of symbols per run, so the newest 1m
bar in `candles` normally trails 15-20 minutes and was measured at 37 minutes
during the 2026-08-20 audit. F1 (ORB, VWAP, pivots, scalping) is nominally
"1-minute intraday momentum" but was computing indicators on half-hour-old bars
— and its own freshness guard was rejecting whole stretches of the session.

The KiteTicker WebSocket is already live and already pushing sub-second ticks
into `LIVE_TICKS`. Aggregating those into 1-minute bars costs no Kite REST quota
(so it does not compete with the D6 rate limiter), adds no Celery task to the
2-slot worker queue, and is current to within one sampling interval.

CROSS-PROCESS
-------------
`LIVE_TICKS` is a module-level dict in the **uvicorn** process, where the ticker
thread runs. The tactical pipeline runs in the **Celery worker** — a different
OS process that cannot see it. Redis is therefore the transport, exactly as it
is for the price snapshot: this builder writes, `tactical_data_fetcher` reads.

HONEST LIMITS — read before trusting these bars
-----------------------------------------------
1. **Sampled, not tick-exact.** We poll `LIVE_TICKS` every
   `TACTICAL_FAST_CANDLE_INTERVAL_SEC` (default 5s), so a minute is built from
   ~12 observations rather than every trade. High/low are therefore slightly
   *understated* versus the true bar — an extreme between two samples is missed.
   Good enough for VWAP/RSI/breakout levels; not a substitute for real OHLC if
   you ever need exact wick data.
2. **Fills forward only.** State starts empty on process start, so the Redis
   history covers only minutes observed since then. It closes the DB gap after
   roughly `FAST_CANDLE_HISTORY` minutes of uptime, and self-heals.
3. **Volume is a delta.** `volume_traded` on a tick is cumulative for the day,
   so per-minute volume is the difference across the bucket. The first bucket
   after startup has no prior reading and reports 0 rather than a bogus
   day-to-date total.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from utils.config import settings
from utils.logger import logger

# Redis key per symbol: a capped list of finalised 1-minute bars, newest first.
KEY_PREFIX = "fast_candle"
FAST_CANDLE_HISTORY = 120        # minutes retained per symbol
FAST_CANDLE_TTL_SEC = 3600       # whole list evicts an hour after the last write


def fast_candle_key(symbol: str) -> str:
    return f"{KEY_PREFIX}:{symbol}:1m"


def _cfg(name: str, default):
    return getattr(settings, name, default)


@dataclass
class _Bucket:
    """The minute currently being accumulated for one symbol."""

    minute: datetime
    open: float
    high: float
    low: float
    close: float
    first_cum_volume: float | None = None
    last_cum_volume: float | None = None
    samples: int = field(default=1)

    def update(self, price: float, cum_volume: float | None) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.samples += 1
        if cum_volume is not None:
            if self.first_cum_volume is None:
                self.first_cum_volume = cum_volume
            self.last_cum_volume = cum_volume

    def to_candle(self) -> dict[str, Any]:
        vol = 0.0
        if self.first_cum_volume is not None and self.last_cum_volume is not None:
            vol = max(0.0, self.last_cum_volume - self.first_cum_volume)
        return {
            "timestamp": self.minute.isoformat(),
            "open": round(self.open, 4),
            "high": round(self.high, 4),
            "low": round(self.low, 4),
            "close": round(self.close, 4),
            "volume": float(vol),
            "samples": self.samples,      # kept so a reader can judge the bar's quality
            "source": "tick_builder",
        }


class LiveCandleBuilder:
    """Accumulates ticks into 1-minute bars and publishes them to Redis.

    One instance per process, driven by a background loop. Holds only the
    in-progress minute per symbol; finalised bars go straight to Redis.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._published = 0

    # ── tick ingestion ───────────────────────────────────────────────────────

    @staticmethod
    def _minute_of(ts: float) -> datetime:
        """Floor a unix timestamp to its UTC minute.

        UTC deliberately: the `candles` table stores naive UTC (verified — 1m
        bars run 03:45-09:59, i.e. 09:15-15:29 IST), so bars from both sources
        must share one clock or a merge would interleave them wrongly.
        """
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(
            second=0, microsecond=0, tzinfo=None
        )

    def observe(self, symbol: str, price: float, cum_volume: float | None,
                ts: float | None = None) -> dict[str, Any] | None:
        """Fold one observation in. Returns a finalised candle on minute rollover."""
        if not price or price <= 0:
            return None

        minute = self._minute_of(ts if ts is not None else time.time())
        bucket = self._buckets.get(symbol)

        if bucket is None:
            self._buckets[symbol] = _Bucket(minute, price, price, price, price,
                                            cum_volume, cum_volume)
            return None

        if bucket.minute == minute:
            bucket.update(price, cum_volume)
            return None

        # Rollover: the previous minute is complete.
        finished = bucket.to_candle()
        self._buckets[symbol] = _Bucket(minute, price, price, price, price,
                                        cum_volume, cum_volume)
        return finished

    # ── publication ──────────────────────────────────────────────────────────

    async def publish(self, symbol: str, candle: dict[str, Any]) -> bool:
        """LPUSH the finalised bar, trim, refresh TTL. Best-effort."""
        from utils.cache import get_redis

        try:
            r = get_redis()
            key = fast_candle_key(symbol)
            await r.lpush(key, json.dumps(candle))
            await r.ltrim(key, 0, FAST_CANDLE_HISTORY - 1)
            await r.expire(key, FAST_CANDLE_TTL_SEC)
            self._published += 1
            return True
        except Exception as exc:
            logger.debug(f"[fast_candle] publish {symbol} failed: {exc}")
            return False

    async def sample_once(self, symbols: list[str]) -> dict[str, int]:
        """One sampling pass over the universe. Never raises."""
        from crawler.zerodha_ticker import LIVE_TICKS
        from crawler.zerodha_market import INDEX_TOKENS, NSE_TOKENS

        observed = published = 0
        for symbol in symbols:
            try:
                token = NSE_TOKENS.get(symbol) or INDEX_TOKENS.get(symbol)
                if token is None:
                    continue
                tick = LIVE_TICKS.get(int(token))
                if not tick:
                    continue
                price = float(tick.get("last_price") or 0.0)
                if price <= 0:
                    continue
                cum_vol = tick.get("volume_traded", tick.get("volume"))
                cum_vol = float(cum_vol) if cum_vol is not None else None

                observed += 1
                finished = self.observe(symbol, price, cum_vol, ts=tick.get("_ts"))
                if finished and await self.publish(symbol, finished):
                    published += 1
            except Exception as exc:
                logger.debug(f"[fast_candle] {symbol} sample failed: {exc}")
                continue

        return {"observed": observed, "published": published,
                "tracking": len(self._buckets)}


# Module-level instance so the background loop keeps its buckets across ticks.
_builder: LiveCandleBuilder | None = None


def get_builder() -> LiveCandleBuilder:
    global _builder
    if _builder is None:
        _builder = LiveCandleBuilder()
    return _builder


async def read_fast_candles(symbol: str, limit: int = 120) -> list[dict[str, Any]]:
    """Finalised fast candles for a symbol, OLDEST-first. Empty on any failure.

    Oldest-first to match what the indicator layer expects, so callers can
    concatenate directly onto a DB frame without re-sorting.
    """
    from utils.cache import get_redis

    try:
        raw = await get_redis().lrange(fast_candle_key(symbol), 0, limit - 1)
    except Exception as exc:
        logger.debug(f"[fast_candle] read {symbol} failed: {exc}")
        return []

    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except Exception:
            continue
    out.reverse()          # LPUSH stores newest-first
    return out
