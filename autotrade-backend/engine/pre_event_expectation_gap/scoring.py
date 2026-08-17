"""Scoring layer.

Combines the pipeline's component outputs into ONE transparent 0-100 score, used
for ranking/analysis. Per the spec, the score does NOT by itself authorize a
trade — the deterministic gates in decision.py have the final say.

Weights are a documented v0.1 BASELINE, explicitly not "permanent production
truth" — they're here to be inspected, backtested (Phase 5) and tuned. Every
component's contribution is returned separately so a score is always auditable.

This is a PURE function of already-computed inputs (no I/O), so it's trivially
testable and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.pre_event_expectation_gap.types import (
    NowcastResult, ExpectationEstimate, PriceDiscount, RelativeStrength,
    NowcastStatus, Direction, PriceDiscountStatus,
)

# v0.1 baseline weights (sum to 1.0). Tunable; not production truth.
WEIGHTS = {
    "nowcast":      0.25,
    "gap":          0.25,
    "discount":     0.20,
    "relative":     0.10,
    "regime":       0.10,
    "data_quality": 0.10,
}

# Gap magnitude (fractional) that maps to a full gap sub-score.
_GAP_FULL = 0.20

_DISCOUNT_SUBSCORE = {
    PriceDiscountStatus.NOT_DISCOUNTED:        1.0,   # most room to run
    PriceDiscountStatus.MODERATELY_DISCOUNTED: 0.7,
    PriceDiscountStatus.HEAVILY_DISCOUNTED:    0.4,
    PriceDiscountStatus.OVEREXTENDED:          0.1,   # already priced in
}


@dataclass
class ScoreBreakdown:
    total: float = 0.0                    # 0-100
    data_quality_score: float = 0.0       # 0-1 (also a gate input)
    components: dict = field(default_factory=dict)  # component -> weighted contribution (0-100 scale)
    subscores:  dict = field(default_factory=dict)  # component -> raw 0-1 subscore


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ── Evidence re-centering (P2-5, 2026-08-17 forensic post-mortem) ────────────
# Three subscores below are SIGNED reads centered on 0.5: nowcast, gap and
# relative each return 0.5 for "no information" and move up/down from there.
# Feeding that 0.5 straight into a weighted sum meant a completely
# uninformative read still earned HALF its factor's points, so the composite
# was mostly a constant. Measured consequences on the live book:
#
#   * The weakest candidate that can even reach the LONG branch scored 67.81
#     against a LONG_SCORE_BAR of 60 — the bar was unreachable from below.
#   * Across all 224 executed trades the MINIMUM score was 62.3. The bar
#     rejected nothing; it was decorative.
#   * Only ~15 of those ~68 points were evidence-sensitive; the rest were
#     floors and constants.
#   * Worse, nowcast's own movement off 0.5 is scaled by nc.confidence, which
#     is a per-SECTOR constant (8 distinct values across 199 trades, 1:1 with
#     sector — 0.06 Banking … 0.24 IT). So an IT candidate outscored a Banking
#     candidate on identical evidence: a sector label laundered into 25% of
#     the score.
#
# _evidence_above_neutral() rescales a 0.5-centered read onto [0, 1] measuring
# only evidence ABOVE neutral, so "no information" contributes 0 rather than
# half. Readings BELOW neutral (i.e. contrary evidence) clamp to 0 — they are
# already handled as NO_TRADE by decision.py's direction gates, so they must
# not also earn partial credit here.
#
# The raw 0.5-centered subscores are still reported in ScoreBreakdown.subscores
# for auditability; only their CONTRIBUTION to the total is re-centered.
_NEUTRAL_CENTERED = ("nowcast", "gap", "relative")


def _evidence_above_neutral(subscore: float, neutral: float = 0.5) -> float:
    """Map a `neutral`-centered [0,1] read onto [0,1] evidence-above-neutral."""
    if neutral >= 1.0:
        return 0.0
    return _clamp01((subscore - neutral) / (1.0 - neutral))


def _nowcast_subscore(nc: NowcastResult) -> float:
    """Directional read on the pending period, 0.5-centered.

    NOTE (P2-5): the magnitude here is nc.confidence, which is a per-sector
    constant, NOT per-trade conviction — see the _evidence_above_neutral()
    block above. It is deliberately left as the adapter reports it so the
    raw value stays auditable and comparable to historical rows; the
    re-centering in compute_score() is what stops it from contributing a
    sector-shaped floor to the total. Per-trade magnitude for this factor
    would need `implied_profit_growth`, which already drives the `gap`
    subscore — using it here too would double-count the same evidence.
    """
    if nc.status != NowcastStatus.OK:
        return 0.0
    dir_val = {Direction.POSITIVE: 1.0, Direction.NEGATIVE: -1.0, Direction.NEUTRAL: 0.0}[nc.profit_direction]
    # 0.5 neutral baseline, pushed by direction × confidence.
    return _clamp01(0.5 + 0.5 * dir_val * nc.confidence)


def _gap_subscore(exp: ExpectationEstimate) -> float:
    if not exp.gap_available or exp.expectation_gap is None:
        return 0.5   # neutral — the decision gate handles "no anchor" as NO_TRADE
    return _clamp01(0.5 + exp.expectation_gap / (2 * _GAP_FULL))


def _discount_subscore(pd: PriceDiscount) -> float:
    return _DISCOUNT_SUBSCORE.get(pd.status, 0.5)


def _relative_subscore(rs: RelativeStrength) -> float:
    return _clamp01((rs.score + 1.0) / 2.0)   # map [-1,1] -> [0,1]


def _data_quality(nc: NowcastResult, exp: ExpectationEstimate, pd: PriceDiscount) -> float:
    """0-1: is enough available to decide at all? Combines nowcast availability
    /completeness, price-history availability, and whether a real expectation
    anchor exists. Also fed to the decision gate's NO_TRADE floor."""
    if nc.status != NowcastStatus.OK:
        return 0.0
    # AUTO's max realistic completeness is ~0.25 (operational inputs missing), so
    # normalize against that ceiling: "as complete as this adapter can be" -> 1.0
    # for the completeness term. The nowcast's own low confidence still caps the
    # overall score elsewhere, so this doesn't overstate conviction.
    completeness_term = _clamp01(nc.data_completeness / 0.25)
    price_term = 1.0 if pd.returns else 0.0
    anchor_term = 1.0 if exp.gap_available else 0.0
    return round(0.4 * completeness_term + 0.3 * price_term + 0.3 * anchor_term, 3)


def compute_score(
    nowcast: NowcastResult,
    expectation: ExpectationEstimate,
    price_discount: PriceDiscount,
    relative_strength: RelativeStrength,
    regime_score: float,
) -> ScoreBreakdown:
    subs = {
        "nowcast":      _nowcast_subscore(nowcast),
        "gap":          _gap_subscore(expectation),
        "discount":     _discount_subscore(price_discount),
        "relative":     _relative_subscore(relative_strength),
        "regime":       _clamp01(regime_score),
        "data_quality": _data_quality(nowcast, expectation, price_discount),
    }
    # Re-center the 0.5-centered reads so "no information" contributes 0 rather
    # than half the factor (P2-5 — see _evidence_above_neutral above). The other
    # three are already natural 0-1 magnitudes, not signed reads, so they pass
    # through unchanged.
    effective = {
        k: (_evidence_above_neutral(v) if k in _NEUTRAL_CENTERED else v)
        for k, v in subs.items()
    }
    contributions = {k: round(WEIGHTS[k] * effective[k] * 100, 2) for k in WEIGHTS}
    total = round(sum(contributions.values()), 2)
    return ScoreBreakdown(
        total=total,
        data_quality_score=subs["data_quality"],
        components=contributions,
        # Raw, un-recentered subscores — these stay comparable to historical
        # rows and are what the audit trail / UI explain from.
        subscores={k: round(v, 3) for k, v in subs.items()},
    )
