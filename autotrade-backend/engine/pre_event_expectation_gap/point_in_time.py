"""Module 2: Point-in-Time Snapshot — the strategy's look-ahead firewall.

The single most important requirement: every prediction must be reproducible
using ONLY data available at the prediction timestamp. Rather than trusting each
module to remember to pass an `as_of` cutoff, the pipeline passes a
PointInTimeSnapshot — an immutable(-by-convention) carrier of `as_of` that
exposes only cutoff-safe data accessors. A module that reads through the
snapshot cannot accidentally see the future.

Phase 3 covers point-in-time CANDLE reads (via crawler.price_feed's `before=`
support). Fundamentals remain a known limitation (no historical as-of table —
documented since the audit); the fundamental-derived nowcast handles its own
conservative quarter-availability filtering inside the sector adapter.

Reads are cached per (symbol, timeframe, limit) so several modules
(price-discount, relative-strength) reading the same series don't re-fetch and
can never diverge on what "the data at as_of" was.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from engine.pre_event_expectation_gap.types import ScheduledEvent


NIFTY_SYMBOL = "^NSEI"


@dataclass
class PointInTimeSnapshot:
    symbol:  str
    as_of:   datetime
    session: AsyncSession
    event:   ScheduledEvent | None = None
    _candle_cache: dict = field(default_factory=dict, repr=False)

    async def candles(self, symbol: str, timeframe: str = "1d", limit: int = 90) -> list:
        """Cutoff-safe candles (strictly before `as_of`), newest-first — exactly
        what crawler.price_feed.get_latest_candles returns, but with the
        point-in-time `before=` filter always applied and results cached."""
        key = (symbol, timeframe, limit)
        if key in self._candle_cache:
            return self._candle_cache[key]
        from crawler.price_feed import get_latest_candles
        rows = await get_latest_candles(symbol, timeframe, limit, self.session, before=self.as_of)
        self._candle_cache[key] = rows
        return rows

    async def self_candles(self, timeframe: str = "1d", limit: int = 90) -> list:
        return await self.candles(self.symbol, timeframe, limit)

    async def nifty_candles(self, timeframe: str = "1d", limit: int = 90) -> list:
        return await self.candles(NIFTY_SYMBOL, timeframe, limit)

    async def sector_index_candles(self, timeframe: str = "1d", limit: int = 90) -> list:
        """Candles for this symbol's sector index (via india_specific maps), or
        [] when the sector/index can't be resolved."""
        idx = self.sector_index_symbol()
        if not idx:
            return []
        return await self.candles(idx, timeframe, limit)

    def sector_index_symbol(self) -> str | None:
        try:
            from engine.india_specific import SECTOR_MAP, SECTOR_INDEX
            sector = SECTOR_MAP.get(self.symbol) or SECTOR_MAP.get(f"{self.symbol}.NS")
            if not sector:
                from engine.pre_event_expectation_gap.sector_adapters import resolve_strategy_sector
                sector = resolve_strategy_sector(self.symbol)
            return SECTOR_INDEX.get((sector or "").upper()) if sector else None
        except Exception:
            return None


def build_snapshot(
    symbol: str, as_of: datetime, session: AsyncSession, event: ScheduledEvent | None = None,
) -> PointInTimeSnapshot:
    return PointInTimeSnapshot(symbol=symbol, as_of=as_of, session=session, event=event)
