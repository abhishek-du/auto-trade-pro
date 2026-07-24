"""Module 4: Expectation Engine.

Estimates what the market EXPECTS, and computes the gap between that and our own
nowcast. The spec is emphatic about not conflating the distinct notions of
"expected outcome" and about NOT fabricating consensus when it doesn't exist.

Honest reality (from the architecture audit): this codebase has NO structured
analyst-consensus feed and NO company-guidance feed. So:
  * `_fetch_consensus` / `_fetch_guidance` are present as the correct extension
    points, but return None in Phase 3 — no provider exists, and we do not
    invent one.
  * The available market anchor is the HISTORICAL BASELINE: the company's own
    established growth norm (nowcast.baseline_profit_growth). The expectation
    gap is then "recent-trend forward proxy vs the company's established norm" —
    a positive gap means the business is accelerating ABOVE what the market has
    come to expect, i.e. room for a positive surprise.

This is a genuinely weaker anchor than true consensus, and `anchor_used` +
`gap_available` say so plainly, so downstream scoring can discount it. A gap
computed against no anchor at all is never produced (gap_available stays False).
"""
from __future__ import annotations

from engine.pre_event_expectation_gap.types import (
    NowcastResult, ExpectationEstimate, NowcastStatus,
)
from engine.pre_event_expectation_gap.point_in_time import PointInTimeSnapshot


async def _fetch_consensus(symbol: str, snapshot: PointInTimeSnapshot) -> float | None:
    """Public analyst consensus for expected PAT growth, point-in-time.
    No provider integrated yet — returns None. This is the extension point:
    when a real consensus feed (with a revision timestamp <= as_of) is added,
    it plugs in here and automatically becomes the preferred anchor below."""
    return None


async def _fetch_company_guidance(symbol: str, snapshot: PointInTimeSnapshot) -> float | None:
    """Company's own guidance for expected PAT growth, point-in-time. No
    structured provider yet — returns None (LLM extraction from public
    guidance documents is a candidate future source, Phase 4+)."""
    return None


async def compute_expectation(
    nowcast: NowcastResult, symbol: str, snapshot: PointInTimeSnapshot,
) -> ExpectationEstimate:
    """Build the structured ExpectationEstimate + gap. Never fabricates an
    anchor; gap_available is True only when a real anchor exists."""
    if nowcast.status != NowcastStatus.OK:
        # No nowcast → no 'our_expected' → no gap. Explicit, not defaulted.
        return ExpectationEstimate(gap_available=False)

    our_expected = nowcast.implied_profit_growth

    consensus = await _fetch_consensus(symbol, snapshot)
    guidance  = await _fetch_company_guidance(symbol, snapshot)
    baseline  = nowcast.baseline_profit_growth

    # Anchor priority: real consensus > company guidance > historical baseline.
    anchor_value = None
    anchor_used = None
    if consensus is not None:
        anchor_value, anchor_used = consensus, "consensus"
    elif guidance is not None:
        anchor_value, anchor_used = guidance, "guidance"
    elif baseline is not None:
        anchor_value, anchor_used = baseline, "historical_baseline"

    gap = None
    gap_available = False
    if our_expected is not None and anchor_value is not None:
        gap = round(our_expected - anchor_value, 4)
        gap_available = True

    return ExpectationEstimate(
        our_expected_pat_growth=(None if our_expected is None else round(our_expected, 4)),
        consensus_pat_growth=consensus,
        company_guidance=guidance,
        expectation_gap=gap,
        gap_available=gap_available,
        anchor_used=anchor_used,
    )
