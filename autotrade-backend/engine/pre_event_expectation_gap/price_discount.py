"""Module 5: Price Already Priced-In Analysis.

A stock can post a great result and still be a poor long if it already rallied
hard into the event. This module measures how much of the anticipated outcome
the price appears to have discounted BEFORE the event, and classifies it.

Pre-event strength is treated as price-discovery / positioning — NOT proof of
information leakage (per the spec's explicit framing). It may reflect
institutional accumulation, sector rotation, short covering, public expectation,
or a technical breakout.

All reads are point-in-time (through the snapshot). Classification is driven by
excess return vs Nifty over the run-up window plus proximity to recent highs —
deliberately simple, transparent thresholds that Phase 4 scoring can refine.
"""
from __future__ import annotations

from engine.pre_event_expectation_gap.types import PriceDiscount, PriceDiscountStatus
from engine.pre_event_expectation_gap.point_in_time import PointInTimeSnapshot
from engine.pre_event_expectation_gap.relative_strength import _window_return

_RETURN_WINDOWS = (1, 3, 5, 10, 20, 60)
_RUNUP_WINDOW = 20   # primary window for the discount classification

# Excess-return (vs Nifty, over the run-up window) thresholds, fractional.
_MODERATE = 0.03
_HEAVY    = 0.08
_EXTREME  = 0.15
# A stock within this fraction of its recent high AND strongly outperforming is
# treated as overextended even if the excess return is only "heavy".
_NEAR_HIGH = 0.02


def _distance_from_high(candles_newest_first: list, lookback: int = 60) -> float | None:
    """% below the highest high over the last `lookback` bars (0 = at the high)."""
    if not candles_newest_first:
        return None
    window = candles_newest_first[:lookback]
    highs = [c.high for c in window if getattr(c, "high", None)]
    latest = window[0].close
    if not highs or not latest:
        return None
    peak = max(highs)
    if peak <= 0:
        return None
    return round((peak - latest) / peak, 4)


def _abnormal_volume(candles_newest_first: list) -> bool:
    """Recent 5-bar average volume vs the prior 20-bar baseline > 1.5x."""
    vols = [getattr(c, "volume", 0) or 0 for c in candles_newest_first]
    if len(vols) < 25:
        return False
    recent = sum(vols[:5]) / 5
    baseline = sum(vols[5:25]) / 20
    return baseline > 0 and (recent / baseline) >= 1.5


def _classify(excess_20d: float | None, distance_from_high: float | None) -> PriceDiscountStatus:
    if excess_20d is None:
        return PriceDiscountStatus.NOT_DISCOUNTED   # no evidence of a run-up
    near_high = distance_from_high is not None and distance_from_high <= _NEAR_HIGH
    if excess_20d >= _EXTREME or (excess_20d >= _HEAVY and near_high):
        return PriceDiscountStatus.OVEREXTENDED
    if excess_20d >= _HEAVY:
        return PriceDiscountStatus.HEAVILY_DISCOUNTED
    if excess_20d >= _MODERATE:
        return PriceDiscountStatus.MODERATELY_DISCOUNTED
    return PriceDiscountStatus.NOT_DISCOUNTED


async def analyze_price_discount(snapshot: PointInTimeSnapshot) -> PriceDiscount:
    stock = await snapshot.self_candles(limit=90)
    nifty = await snapshot.nifty_candles(limit=90)
    sector = await snapshot.sector_index_candles(limit=90)

    returns = {}
    for w in _RETURN_WINDOWS:
        r = _window_return(stock, w)
        if r is not None:
            returns[f"{w}d"] = round(r, 4)

    stock_20 = _window_return(stock, _RUNUP_WINDOW)
    nifty_20 = _window_return(nifty, _RUNUP_WINDOW)
    sector_20 = _window_return(sector, _RUNUP_WINDOW)

    rs_nifty = None if (stock_20 is None or nifty_20 is None) else round(stock_20 - nifty_20, 4)
    rs_sector = None if (stock_20 is None or sector_20 is None) else round(stock_20 - sector_20, 4)
    dist_high = _distance_from_high(stock)

    status = _classify(rs_nifty, dist_high)

    return PriceDiscount(
        returns=returns,
        rel_strength_nifty=rs_nifty,
        rel_strength_sector=rs_sector,
        distance_from_high=dist_high,
        abnormal_volume=_abnormal_volume(stock),
        status=status,
    )
