"""Deterministic decision gates → LONG / SHORT / WAIT / NO_TRADE.

The score (scoring.py) is an input, NOT the decision. This module applies
explicit, auditable gates in a fixed order — a high score can never buy its way
past a failed data-quality / event-timing / price-extension check. Pure function;
no I/O.

Phase-1 posture (per spec):
  * Short-side auto-execution is DISABLED. A negative expectation gap / bearish
    nowcast resolves to NO_TRADE (avoid-long), never an automatic SHORT.
  * WAIT and NO_TRADE are valid, correct outcomes — not failures. A great setup
    that has already run into the event is a WAIT, not a chase.
"""
from __future__ import annotations

from engine.pre_event_expectation_gap.types import (
    NowcastResult, ExpectationEstimate, PriceDiscount, RelativeStrength,
    ScheduledEvent, PreEventDecision, NowcastStatus, Direction, PriceDiscountStatus,
)
from engine.pre_event_expectation_gap.scoring import ScoreBreakdown

# Deterministic gate thresholds (v0.1, tunable).
MIN_EVENT_CONFIDENCE   = 0.6      # below → event timing too uncertain
MIN_NOWCAST_CONFIDENCE = 0.15     # below → direction call itself too weak to trade (see note below)
MIN_DATA_QUALITY       = 0.20     # below → not enough to decide
LONG_SCORE_BAR         = 60.0     # A+ long bar
WAIT_SCORE_FLOOR       = 45.0     # below → edge too small even for a WAIT
GAP_NEG_THRESHOLD      = 0.02     # gap below −2pp counts as a bearish anchor

# MIN_NOWCAST_CONFIDENCE note (added 2026-08-17, loss investigation): unlike
# MIN_EVENT_CONFIDENCE (which gates whether the EVENT DATE is real), nothing
# previously gated nc.confidence itself -- the direction call's own
# trustworthiness. scoring.py's old _nowcast_subscore floored at 0.5 (neutral)
# for ANY confidence including near-zero, so a 6-11% confidence guess (the
# large majority of live nowcasts -- most sector adapters cap well under
# their confidence_ceiling when point-in-time history is thin) still bought
# ~half that factor's max score, unopposed by any gate. Audited 203 trades
# closed 3-17 Aug 2026: win rate was NOT monotonic with confidence_bucket
# (60->46%, 70->39%, 80->36% -- the highest-confidence bucket performed
# WORST), and every one of the period's largest single losses (GENESYS
# -17.8%, GODREJIND -7.2%, LLOYDSENT -9.0%, CPPLUS -8.8% ...) carried a raw
# nowcast confidence of 0.06-0.11. 0.15 is set below every sector adapter's
# achievable range with adequate history (confidence_ceiling floor across
# adapters is 0.20, banking) so it only excludes the genuinely-thin-data
# tail, not the strategy as a whole.


def decide(
    breakdown: ScoreBreakdown,
    nowcast: NowcastResult,
    expectation: ExpectationEstimate,
    price_discount: PriceDiscount,
    relative_strength: RelativeStrength,
    event: ScheduledEvent,
) -> tuple[PreEventDecision, str]:
    """Return (decision, human-readable reason). Fail-closed at every gate."""

    # ── 1. Hard NO_TRADE gates (fail-closed) ─────────────────────────────────
    if nowcast.status != NowcastStatus.OK:
        return PreEventDecision.NO_TRADE, "nowcast unavailable — no operational read"
    if (event.event_confidence or 0.0) < MIN_EVENT_CONFIDENCE:
        return PreEventDecision.NO_TRADE, (
            f"event timing uncertain (confidence {event.event_confidence:.2f} < {MIN_EVENT_CONFIDENCE})")
    if (nowcast.confidence or 0.0) < MIN_NOWCAST_CONFIDENCE:
        return PreEventDecision.NO_TRADE, (
            f"nowcast direction too weak to trade (confidence {nowcast.confidence:.2f} < {MIN_NOWCAST_CONFIDENCE})")
    if not price_discount.returns:
        return PreEventDecision.NO_TRADE, "recent price history unavailable — cannot verify positioning/R:R"
    if breakdown.data_quality_score < MIN_DATA_QUALITY:
        return PreEventDecision.NO_TRADE, (
            f"data quality insufficient ({breakdown.data_quality_score:.2f} < {MIN_DATA_QUALITY})")
    if not expectation.gap_available:
        return PreEventDecision.NO_TRADE, "no expectation anchor available — gap cannot be established"

    # ── 2. Direction bias ────────────────────────────────────────────────────
    gap = expectation.expectation_gap or 0.0
    bullish = nowcast.profit_direction == Direction.POSITIVE and gap > 0
    bearish = nowcast.profit_direction == Direction.NEGATIVE or gap < -GAP_NEG_THRESHOLD

    # ── 3. Bearish → Phase-1 no short → avoid long ───────────────────────────
    if bearish and not bullish:
        return PreEventDecision.NO_TRADE, (
            "negative expectation gap / bearish nowcast — avoid long; short-side disabled in Phase 1")

    # ── 4. Not clearly bullish → nothing to do ───────────────────────────────
    if not bullish:
        return PreEventDecision.NO_TRADE, "no positive expectation gap — neutral, no edge"

    # ── 5. Bullish: price-extension gate (score can't override) ───────────────
    if price_discount.status == PriceDiscountStatus.OVEREXTENDED:
        return PreEventDecision.WAIT, (
            "positive expectation gap but price is overextended into the event — poor risk/reward, wait for a pullback")

    if breakdown.total < WAIT_SCORE_FLOOR:
        return PreEventDecision.NO_TRADE, f"positive bias but edge too small (score {breakdown.total:.0f})"

    # ── 6. A+ LONG: bullish, not overextended, score clears the bar ──────────
    if breakdown.total >= LONG_SCORE_BAR and price_discount.status in (
        PriceDiscountStatus.NOT_DISCOUNTED, PriceDiscountStatus.MODERATELY_DISCOUNTED,
    ):
        return PreEventDecision.LONG, (
            f"positive expectation gap, not overextended ({price_discount.status.value}), "
            f"score {breakdown.total:.0f} ≥ {LONG_SCORE_BAR:.0f}")

    # ── 7. Bullish but not A+ (heavily discounted, or mid score) → WAIT ──────
    return PreEventDecision.WAIT, (
        f"positive but not A+ (score {breakdown.total:.0f}, discount {price_discount.status.value}) — watch, don't chase")
