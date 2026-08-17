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
from utils.config import settings

# ── Universe restriction (P2-1, 2026-08-17) ─────────────────────────────────
# This strategy's premise is trading the gap between what we infer and what the
# MARKET expects. Until 2026-08-17 both market-expectation providers
# (_fetch_consensus / _fetch_guidance) were stubs returning None, so every
# trade silently fell back to a 3-year CAGR baseline that expectation.py itself
# marks `is_market_expectation = False` — i.e. the premise was never actually
# evaluated. Measured result over 223 trades: profit factor 1.069, statistically
# indistinguishable from noise (docs/2026-08-17_FORENSIC_POST_MORTEM.md §3).
#
# With a real consensus provider now wired in, this gate restricts the strategy
# to the universe where its premise is MEASURABLE, instead of letting it keep
# trading a proxy and calling it an expectation gap.
#
# The cost is deliberate and large: analyst coverage of Indian small/mid-caps is
# thin (measured 17% of the forensic window's symbols clear the minimum-analyst
# bar; 0 of the 11-name loss cluster have any coverage at all), so this cuts the
# tradable universe substantially and biases it toward larger names. That is the
# intended trade-off — a smaller universe where the edge is checkable beats a
# large one where it is not. Set ENABLE_PRE_EVENT_MARKET_EXPECTATION_GATE=false
# to fall back to the old permissive behaviour.
REQUIRE_MARKET_EXPECTATION: bool = bool(
    getattr(settings, "ENABLE_PRE_EVENT_MARKET_EXPECTATION_GATE", True)
)

# Deterministic gate thresholds (v0.1, tunable).
MIN_EVENT_CONFIDENCE = 0.6      # below → event timing too uncertain
MIN_DATA_QUALITY     = 0.20     # below → not enough to decide
LONG_SCORE_BAR       = 60.0     # A+ long bar
WAIT_SCORE_FLOOR     = 45.0     # below → edge too small even for a WAIT
GAP_NEG_THRESHOLD    = 0.02     # gap below −2pp counts as a bearish anchor


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
    if not price_discount.returns:
        return PreEventDecision.NO_TRADE, "recent price history unavailable — cannot verify positioning/R:R"
    if breakdown.data_quality_score < MIN_DATA_QUALITY:
        return PreEventDecision.NO_TRADE, (
            f"data quality insufficient ({breakdown.data_quality_score:.2f} < {MIN_DATA_QUALITY})")
    if not expectation.gap_available:
        return PreEventDecision.NO_TRADE, "no expectation anchor available — gap cannot be established"
    if REQUIRE_MARKET_EXPECTATION and not expectation.is_market_expectation:
        return PreEventDecision.NO_TRADE, (
            f"anchor is {expectation.anchor_type or 'unknown'}, not a market expectation — "
            "cannot measure an expectation gap for this symbol")

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
