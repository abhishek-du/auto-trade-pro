"""Module 4: Expectation Engine (Phase 5.5 anchor semantics).

Estimates what the market EXPECTS and computes the gap vs our nowcast — with
STRICT, explicit anchor semantics the strategy spec demands:

Anchor priority (only fall to the next when the higher one is unavailable):
  1. point-in-time CONSENSUS            (is_market_expectation=True)
  2. point-in-time MANAGEMENT_GUIDANCE  (is_market_expectation=True)
  3. point-in-time HISTORICAL_BASELINE_3Y_CAGR  (is_market_expectation=FALSE)

The 3-year CAGR is a HISTORICAL BASELINE — it is NOT consensus, NOT market
expectation, NOT analyst expectation, and `is_market_expectation` is False for
it. It carries a strictly LOWER confidence ceiling than genuine consensus.

POINT-IN-TIME ENFORCEMENT (mandatory): the historical baseline is used ONLY when
its `anchor_known_at <= as_of` can be demonstrated. Because
FundamentalData.profit_growth_3yr is stored in place (its only timestamp is the
recent last_updated), this means the baseline is usable for LIVE prediction
(as_of≈now) but is CORRECTLY REJECTED in historical replay (a past cutoff cannot
have known a value stamped now). We never assume a currently-stored published
metric was known historically.

Nothing here fabricates a value; when no anchor is usable, gap_available=False.
"""
from __future__ import annotations

from datetime import datetime

from engine.pre_event_expectation_gap.types import (
    NowcastResult, ExpectationEstimate, NowcastStatus,
)
from engine.pre_event_expectation_gap.point_in_time import PointInTimeSnapshot
from engine.pre_event_expectation_gap.financials import get_historical_baseline_3y_cagr

# Confidence ceilings by anchor strength. A weaker anchor caps overall
# confidence lower — "final confidence must not exceed the weakest material input".
_CEILING_CONSENSUS = 0.85
_CEILING_GUIDANCE  = 0.70
_CEILING_BASELINE  = 0.40


async def _fetch_consensus(symbol: str, snapshot: PointInTimeSnapshot):
    """Point-in-time public analyst consensus for expected PAT growth ->
    (value, known_at) or None. No provider integrated — returns None. When a
    real consensus feed (with a revision timestamp <= as_of) is added it plugs
    in here and automatically becomes the preferred anchor."""
    return None


async def _fetch_guidance(symbol: str, snapshot: PointInTimeSnapshot):
    """Point-in-time company guidance -> (value, known_at) or None. No structured
    provider yet."""
    return None


def _as_datetime(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except Exception:
        return None


async def compute_expectation(
    nowcast: NowcastResult, symbol: str, snapshot: PointInTimeSnapshot,
) -> ExpectationEstimate:
    """Build the ExpectationEstimate with explicit anchor semantics + point-in-
    time enforcement. gap_available is True only when a usable anchor exists."""
    if nowcast.status != NowcastStatus.OK:
        return ExpectationEstimate(gap_available=False)

    our_expected = nowcast.implied_profit_growth
    as_of = snapshot.as_of

    # ── Resolve the anchor by priority, enforcing point-in-time on each ──────
    anchor_type = anchor_value = anchor_known_at = gap_type = None
    is_market_expectation = False
    confidence_ceiling = None
    consensus_val = guidance_val = None

    consensus = await _fetch_consensus(symbol, snapshot)
    guidance  = await _fetch_guidance(symbol, snapshot)

    def _usable(pair):
        if not pair:
            return None
        val, known_at = pair
        kdt = _as_datetime(known_at)
        if val is None or kdt is None or kdt > as_of:   # point-in-time gate
            return None
        return val, kdt

    c = _usable(consensus)
    g = _usable(guidance)
    if c is not None:
        anchor_value, kdt = c
        consensus_val = anchor_value
        anchor_type, gap_type, is_market_expectation = "CONSENSUS", "VS_CONSENSUS", True
        confidence_ceiling = _CEILING_CONSENSUS
        anchor_known_at = kdt.isoformat()
    elif g is not None:
        anchor_value, kdt = g
        guidance_val = anchor_value
        anchor_type, gap_type, is_market_expectation = "MANAGEMENT_GUIDANCE", "VS_GUIDANCE", True
        confidence_ceiling = _CEILING_GUIDANCE
        anchor_known_at = kdt.isoformat()
    else:
        baseline = await get_historical_baseline_3y_cagr(symbol, snapshot.session)
        b = _usable((baseline.value, baseline.known_at))
        if b is not None:
            anchor_value_annual, kdt = b
            anchor_type = "HISTORICAL_BASELINE_3Y_CAGR"
            is_market_expectation = False               # NEVER a market expectation
            confidence_ceiling = _CEILING_BASELINE
            anchor_known_at = kdt.isoformat()
            # Dimensional alignment: the baseline is ANNUAL; our recent trend may
            # be annual (YoY) or coarse quarterly (QoQ). Compare like-for-like.
            if nowcast.implied_is_annual:
                anchor_value = anchor_value_annual
                gap_type = "ANNUAL_TREND_VS_HISTORICAL_BASELINE_3Y_CAGR"
            else:
                anchor_value = round((1.0 + anchor_value_annual) ** 0.25 - 1.0, 4)  # -> quarterly-equivalent
                gap_type = "QUARTERLY_TREND_VS_QUARTERLIZED_HISTORICAL_BASELINE_3Y_CAGR"

    gap = None
    gap_available = False
    if our_expected is not None and anchor_value is not None:
        gap = round(our_expected - anchor_value, 4)
        gap_available = True

    return ExpectationEstimate(
        our_expected_pat_growth=(None if our_expected is None else round(our_expected, 4)),
        consensus_pat_growth=consensus_val,
        company_guidance=guidance_val,
        expectation_gap=gap,
        gap_available=gap_available,
        anchor_used=anchor_type,
        anchor_type=anchor_type,
        anchor_value=(None if anchor_value is None else round(anchor_value, 4)),
        anchor_known_at=anchor_known_at,
        is_market_expectation=is_market_expectation,
        gap_type=gap_type,
        confidence_ceiling=confidence_ceiling,
    )
