"""Module 6: Relative Strength.

Stock return vs Nifty and vs its sector, over a window ending at the prediction
cutoff. Used as CONFIRMATION only — never a standalone signal (per the spec).
All reads are point-in-time (through the snapshot).
"""
from __future__ import annotations

from engine.pre_event_expectation_gap.types import RelativeStrength
from engine.pre_event_expectation_gap.point_in_time import PointInTimeSnapshot


def _window_return(candles_newest_first: list, window: int) -> float | None:
    """Fractional return over `window` bars, computed from newest-first candles.
    None if there aren't enough bars or the base price is non-positive."""
    if not candles_newest_first or len(candles_newest_first) <= window:
        return None
    latest = candles_newest_first[0].close
    base = candles_newest_first[window].close
    if not base or base <= 0:
        return None
    return (latest - base) / base


async def compute_relative_strength(snapshot: PointInTimeSnapshot, window: int = 20) -> RelativeStrength:
    stock = await snapshot.self_candles(limit=window + 5)
    nifty = await snapshot.nifty_candles(limit=window + 5)
    sector = await snapshot.sector_index_candles(limit=window + 5)

    stock_ret = _window_return(stock, window)
    nifty_ret = _window_return(nifty, window)
    sector_ret = _window_return(sector, window)

    vs_nifty = None if (stock_ret is None or nifty_ret is None) else round(stock_ret - nifty_ret, 4)
    vs_sector = None if (stock_ret is None or sector_ret is None) else round(stock_ret - sector_ret, 4)

    # Normalized confirmation score in [-1, 1]: average of the available excess
    # returns, scaled so ~+10pp excess ≈ +1. Confirmation, not a trade trigger.
    parts = [v for v in (vs_nifty, vs_sector) if v is not None]
    score = 0.0
    if parts:
        raw = sum(parts) / len(parts)
        score = round(max(-1.0, min(1.0, raw / 0.10)), 3)

    return RelativeStrength(vs_nifty=vs_nifty, vs_sector=vs_sector, score=score)
