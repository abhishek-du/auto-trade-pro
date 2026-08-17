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


def _nowcast_subscore(nc: NowcastResult) -> float:
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
    contributions = {k: round(WEIGHTS[k] * subs[k] * 100, 2) for k in WEIGHTS}
    total = round(sum(contributions.values()), 2)
    return ScoreBreakdown(
        total=total,
        data_quality_score=subs["data_quality"],
        components=contributions,
        subscores={k: round(v, 3) for k, v in subs.items()},
    )
