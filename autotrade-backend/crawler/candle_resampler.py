"""Derive 5m / 15m / 1h candles from the 1m candles already in Postgres.

Why this exists
---------------
Until 2026-08-24 the 5m and 1h bars were fetched from yfinance by
``india_price_scan``: one HTTP call per symbol per timeframe, ~1,400 symbols,
20 s timeout each. Measured runtimes were min 8 s / avg 657 s / max 1,793 s
against a 300 s beat, behind a 2,520 s overlap lock. It could not keep up, and
the symptom on 24 Aug was that the newest 5m bar in the database was 14:50 IST
and the newest 1h bar 14:15 IST — the last 40 and 75 minutes of the session
were simply not there. F4 mean-reversion reads 5m, so it was scoring a market
it could not see the end of.

Resampling removes the problem rather than tuning it. The 1m bars are already
in Postgres, written from Kite by ``sync_live_1m_candles``, so every coarser
timeframe is an aggregation over data we hold. No network, no rate limit, no
per-symbol fan-out: one SQL statement per timeframe covering every symbol at
once. The coarse bars can never be staler than the 1m bars they are built from.

Bucket alignment
----------------
NSE opens 09:15 IST = 03:45 UTC. 03:45 is a whole multiple of 5 and 15 minutes
past the hour, so plain epoch flooring lands 5m and 15m buckets on the session
grid. It does NOT for 60m — plain flooring would open the hourly bar at 03:00
UTC, half an hour before the exchange. The existing 1h bars in the table run on
:45 (03:45, 04:45, ...), so ``_OFFSET_S`` shifts the hourly grid by 45 minutes
to match what is already stored and what every consumer expects.

Partial buckets
---------------
The bucket covering *now* is still forming. Writing it would publish a bar whose
high/low/close change under a reader's feet, and ``compute_indicators`` is built
to exclude a forming bar it can identify by timestamp — it cannot identify one
that silently mutates. ``include_forming=False`` (the default) drops any bucket
whose window has not fully elapsed.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# timeframe -> (bucket seconds, grid offset seconds)
_SPECS: dict[str, tuple[int, int]] = {
    "5m":  (300,   0),
    "15m": (900,   0),
    "1h":  (3600, 2700),   # 45-minute offset: NSE hours open at 03:45 UTC
}

# One statement per timeframe. `array_agg(... ORDER BY timestamp)` gives a
# deterministic first/last for open/close; max/min/sum handle the rest.
# ON CONFLICT keeps the bar current when a later run sees more 1m data for a
# bucket that was already written (relevant only when include_forming=True).
_SQL = """
INSERT INTO candles (symbol, timeframe, open, high, low, close, volume, timestamp)
SELECT
    symbol,
    :tf                                                   AS timeframe,
    (array_agg(open  ORDER BY timestamp ASC ))[1]         AS open,
    MAX(high)                                             AS high,
    MIN(low)                                              AS low,
    (array_agg(close ORDER BY timestamp DESC))[1]         AS close,
    SUM(volume)                                           AS volume,
    to_timestamp(
        FLOOR((EXTRACT(EPOCH FROM timestamp) - :off) / :bucket) * :bucket + :off
    ) AT TIME ZONE 'UTC'                                  AS bucket_ts
FROM candles
WHERE timeframe = '1m'
  AND timestamp >= :since
  AND timestamp <  :until
GROUP BY symbol, bucket_ts
HAVING COUNT(*) > 0
ON CONFLICT (symbol, timeframe, timestamp) DO UPDATE SET
    open   = EXCLUDED.open,
    high   = EXCLUDED.high,
    low    = EXCLUDED.low,
    close  = EXCLUDED.close,
    volume = EXCLUDED.volume
"""
# Every column is overwritten, deliberately. The first version of this upsert
# left `open` alone and merged high/low with GREATEST/LEAST, reasoning that a
# bucket might be built up incrementally. It is not — each run re-aggregates the
# whole bucket from all of its 1m rows, so the computed values are already
# complete and authoritative. Worse, the older 5m/1h rows in this table came
# from yfinance, and a partial update welded a yfinance `open` onto a
# Kite-derived high/low/close: measured on 24 Aug, BLUESTONE.NS 08:15 kept
# open=834.60 from yfinance while its true open from the 1m bars was 835.00, and
# TIPSMUSIC.NS was off by 0.30. Bars mixed from two feeds are worse than either
# feed alone, because no consumer can tell which one it is holding.


def _floor(ts: dt.datetime, bucket: int, off: int) -> dt.datetime:
    epoch = ts.replace(tzinfo=dt.timezone.utc).timestamp()
    return dt.datetime.utcfromtimestamp(
        ((epoch - off) // bucket) * bucket + off
    )


async def resample_intraday(
    session: AsyncSession,
    *,
    timeframes: tuple[str, ...] = ("5m", "15m", "1h"),
    lookback_minutes: int = 180,
    include_forming: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """Rebuild `timeframes` from 1m bars over the trailing `lookback_minutes`.

    `lookback_minutes` only bounds how much 1m data is re-read; because the
    write is an upsert, re-covering ground that is already correct is harmless
    and costs one scan. The default spans the largest bucket comfortably.

    Returns per-timeframe rows written, plus the newest bucket produced, so a
    caller (and the test suite) can assert freshness rather than infer it.
    """
    now = now or dt.datetime.utcnow()
    since = now - dt.timedelta(minutes=lookback_minutes)
    out: dict[str, dict] = {}

    for tf in timeframes:
        spec = _SPECS.get(tf)
        if spec is None:
            out[tf] = {"error": "unsupported timeframe"}
            continue
        bucket, off = spec

        # Exclude the bucket that is still forming: cut the source 1m rows at
        # the start of the current bucket, so only fully-elapsed windows are
        # aggregated.
        until = now if include_forming else _floor(now, bucket, off)
        if until <= since:
            out[tf] = {"rows": 0, "skipped": "window_empty"}
            continue

        res = await session.execute(
            text(_SQL),
            {"tf": tf, "bucket": bucket, "off": off, "since": since, "until": until},
        )
        rows = res.rowcount or 0

        newest = (
            await session.execute(
                text(
                    "SELECT MAX(timestamp) FROM candles "
                    "WHERE timeframe = :tf AND timestamp >= :since"
                ),
                {"tf": tf, "since": since},
            )
        ).scalar()
        out[tf] = {"rows": rows, "newest": newest.isoformat() if newest else None}

    await session.commit()
    logger.info("[resample] %s", out)
    return out
