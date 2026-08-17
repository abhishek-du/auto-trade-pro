"""Semantic / distribution invariants for the pre-event strategy (P2-3).

WHY THIS FILE EXISTS
--------------------
On 2026-08-17 a change shipped to main and deployed to production that would
have discarded the profitable 90% of the book and retained only the single
worst-performing sector. It added `MIN_NOWCAST_CONFIDENCE = 0.15`, on the
reasoning that a nc.confidence of 0.06-0.11 meant "the model is guessing".

**The full 55-test suite passed it.** Every test was green, because the suite
only ever asserted MECHANICS — "does this function return the shape I expect,
does this gate fire when I hand it a failing value". Nothing asserted anything
about what the VALUES MEAN:

  * no fixture used a confidence below the new threshold, so nothing tripped;
  * nothing asserted how many distinct values nc.confidence can take, which
    would have shown it is a per-sector constant (8 values across 199 live
    trades, 1:1 with sector) and therefore useless as a per-trade filter;
  * nothing asserted that LONG_SCORE_BAR actually rejects anything, which
    would have shown it was decorative (minimum live score 62.3 vs a bar of
    60 — it had never rejected a single candidate).

Green tests validated mechanics, not semantics. These tests assert the
semantics. They are deliberately written to FAIL LOUDLY with an explanation
rather than silently pass, because their whole job is to catch a plausible-
looking future change that repeats one of these mistakes.

See docs/2026-08-17_FORENSIC_POST_MORTEM.md §4 and §8.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime

import pytest

from engine.pre_event_expectation_gap import decision as dec
from engine.pre_event_expectation_gap.decision import LONG_SCORE_BAR, decide
from engine.pre_event_expectation_gap.scoring import compute_score, WEIGHTS
from engine.pre_event_expectation_gap.sector_adapters import base as adapter_base
from engine.pre_event_expectation_gap.types import (
    NowcastResult, ExpectationEstimate, PriceDiscount, RelativeStrength,
    ScheduledEvent, PreEventType, PreEventDecision, NowcastStatus, Direction,
    PriceDiscountStatus,
)


def _nc(conf=0.10, d=Direction.POSITIVE, comp=0.25, status=NowcastStatus.OK):
    return NowcastResult(status=status, profit_direction=d, revenue_direction=d,
                         margin_direction=d, confidence=conf, data_completeness=comp)


def _exp(gap=0.10, market=True):
    return ExpectationEstimate(our_expected_pat_growth=0.2, expectation_gap=gap,
                               gap_available=True, anchor_used="CONSENSUS",
                               anchor_type="CONSENSUS", is_market_expectation=market)


def _pd(status=PriceDiscountStatus.NOT_DISCOUNTED):
    return PriceDiscount(returns={"20d": 0.01}, rel_strength_nifty=0.0, status=status)


def _event(conf=0.95):
    return ScheduledEvent(symbol="X.NS", event_type=PreEventType.QUARTERLY_RESULT,
                          event_date=date(2026, 10, 25), event_confidence=conf, source="cal")


def _grid():
    """A realistic sweep of candidate inputs, spanning the ranges each field
    actually takes in production (confidences are the real observed sector
    constants; gaps span thin-to-strong)."""
    out = []
    for conf in (0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.13, 0.24):
        for gap in (0.0, 0.01, 0.05, 0.10, 0.20, 0.35):
            for disc in (PriceDiscountStatus.NOT_DISCOUNTED,
                         PriceDiscountStatus.MODERATELY_DISCOUNTED):
                for rel in (-0.5, 0.0, 0.5):
                    out.append(compute_score(
                        _nc(conf=conf), _exp(gap=gap), _pd(disc),
                        RelativeStrength(vs_nifty=0.0, vs_sector=0.0, score=rel),
                        regime_score=0.7))
    return out


class TestNoGateIsVacuous:
    """A threshold that never rejects anything is not a filter — it is a
    comment. LONG_SCORE_BAR was exactly this: 60, against a live minimum of
    62.3, so it had never once rejected a candidate."""

    def test_long_score_bar_rejects_part_of_a_realistic_grid(self):
        scores = [b.total for b in _grid()]
        rejected = [s for s in scores if s < LONG_SCORE_BAR]
        assert rejected, (
            f"LONG_SCORE_BAR={LONG_SCORE_BAR} rejected NOTHING across {len(scores)} "
            f"realistic candidates (range {min(scores):.1f}-{max(scores):.1f}). "
            "The bar is decorative — either it is set wrong or the scoring no "
            "longer discriminates. See post-mortem §4.")

    def test_long_score_bar_accepts_part_of_a_realistic_grid(self):
        """The mirror failure: a bar so high nothing passes would silently
        disable the strategy rather than filtering it."""
        scores = [b.total for b in _grid()]
        accepted = [s for s in scores if s >= LONG_SCORE_BAR]
        assert accepted, (
            f"LONG_SCORE_BAR={LONG_SCORE_BAR} accepted NOTHING across "
            f"{len(scores)} realistic candidates (range {min(scores):.1f}-"
            f"{max(scores):.1f}) — the strategy is switched off, not filtered.")

    def test_score_actually_spans_a_range(self):
        """If the composite barely moves across wildly different inputs, it is
        a constant wearing a number's clothes — which is what a stack of 0.5
        floors had made it."""
        scores = [b.total for b in _grid()]
        spread = max(scores) - min(scores)
        assert spread >= 20.0, (
            f"composite score spans only {spread:.1f} points across the full "
            f"input grid ({min(scores):.1f}-{max(scores):.1f}); it is not "
            "discriminating between good and bad candidates.")


class TestNowcastConfidenceIsNotPerTradeConviction:
    """nc.confidence LOOKS like a per-trade conviction score. It is not: it is
    a per-sector constant. Gating on it is gating on sector.

    This is the exact trap that produced the reverted 2026-08-17 change, and
    these assertions exist so the next person who reaches for it as a filter
    gets told why, in a failing test, before it reaches production.
    """

    def test_confidence_is_a_function_of_sector_not_of_the_symbol_s_numbers(self):
        """The adapter computes confidence from ceiling x history x coarseness
        x completeness — NONE of which depend on how strong the symbol's actual
        growth is. Two symbols in the same sector with identical data shape but
        opposite fortunes get the SAME confidence.

        Asserted structurally (against the formula's inputs) rather than by
        running the adapter, so it holds without a DB.
        """
        src = inspect.getsource(adapter_base.SectorNowcastAdapter)
        from engine.pre_event_expectation_gap.sector_adapters.common import (
            FinancialsTrendAdapter,
        )
        formula = inspect.getsource(FinancialsTrendAdapter.nowcast)
        conf_line = [ln for ln in formula.splitlines() if "confidence = round(min(" in ln]
        assert conf_line, "confidence formula moved — re-verify this invariant by hand"

        # The magnitude of the growth (implied_profit_growth / p_growth) must
        # NOT appear in the confidence computation. If a future change makes
        # confidence genuinely per-trade, this test should be updated
        # DELIBERATELY, together with re-validating anything that gates on it.
        window = formula.split("confidence = round(min(")[1].split("), 3)")[0]
        for per_trade_term in ("p_growth", "r_growth", "implied_profit_growth", "spread"):
            assert per_trade_term not in window, (
                f"confidence now depends on {per_trade_term!r}, i.e. it may have "
                "become per-trade. Anything gating/scoring on nc.confidence must "
                "be re-validated against historical trades before relying on it "
                "— see post-mortem §4 and §8.")

    def test_confidence_takes_few_distinct_values_across_sectors(self):
        """Cardinality check: one value per sector adapter, not a continuum.
        A field with ~8 possible values is a categorical label; a numeric
        threshold on it selects categories, not quality."""
        from engine.pre_event_expectation_gap.sector_adapters import (  # noqa: F401
            auto, banking, consumer, energy, fmcg, infra, it, metals, pharma, telecom,
        )
        ceilings = {a.confidence_ceiling for a in adapter_base._REGISTRY.values()}
        assert len(ceilings) <= 10, (
            f"{len(ceilings)} distinct confidence ceilings — if this has become "
            "a continuum the sector-label reasoning below may no longer hold.")
        # And every live confidence is bounded by its sector's ceiling, so the
        # observable set is at most this small.
        assert all(0.0 < c <= 1.0 for c in ceilings)

    def test_gating_on_confidence_would_select_by_sector(self):
        """Demonstrates the failure concretely: any threshold placed inside the
        observed confidence range partitions SECTORS, not trade quality."""
        observed = {0.06: "Banking", 0.07: "Metals", 0.08: "Energy", 0.09: "Pharma",
                    0.11: "Consumer", 0.13: "Telecom", 0.24: "IT"}
        threshold = 0.15
        passing = {s for c, s in observed.items() if c >= threshold}
        assert passing == {"IT"}, (
            "the documented example of this trap has changed; re-check that a "
            "confidence threshold still amounts to sector selection")


class TestGateThresholdsAreExercisedOnBothSides:
    """Every numeric gate must have a candidate that passes it AND one that
    fails it. The reverted change slipped through precisely because no fixture
    sat on the failing side of the new threshold."""

    def test_min_event_confidence_both_sides(self):
        below, _ = decide(_grid()[0], _nc(), _exp(), _pd(), RelativeStrength(score=0.0),
                          _event(conf=dec.MIN_EVENT_CONFIDENCE - 0.05))
        assert below == PreEventDecision.NO_TRADE
        above, _ = decide(_grid()[-1], _nc(), _exp(), _pd(), RelativeStrength(score=0.5),
                          _event(conf=dec.MIN_EVENT_CONFIDENCE + 0.05))
        assert above != PreEventDecision.NO_TRADE

    def test_market_expectation_gate_both_sides(self):
        strong = max(_grid(), key=lambda b: b.total)
        blocked, reason = decide(strong, _nc(), _exp(market=False), _pd(),
                                 RelativeStrength(score=0.5), _event())
        assert blocked == PreEventDecision.NO_TRADE and "market expectation" in reason
        allowed, _ = decide(strong, _nc(), _exp(market=True), _pd(),
                            RelativeStrength(score=0.5), _event())
        assert allowed == PreEventDecision.LONG

    def test_no_gate_constant_sits_outside_its_field_s_range(self):
        """A threshold above every attainable value silently disables a path;
        below every attainable value makes it vacuous. Both are bugs that look
        like working code."""
        assert 0.0 < dec.MIN_EVENT_CONFIDENCE < 1.0
        assert 0.0 < dec.MIN_DATA_QUALITY < 1.0
        assert 0.0 < dec.WAIT_SCORE_FLOOR < dec.LONG_SCORE_BAR < 100.0


class TestGateStackAcceptanceRate:
    """THE regression test for the 2026-08-17 incident.

    The reverted change added a gate that rejected 90.5% of real trades. No
    existing test noticed, because none of them asked "how much does the gate
    stack, as a whole, let through?" — they only checked individual gates in
    isolation with hand-picked fixtures.

    This asserts the aggregate. Any single new gate that quietly excludes most
    realistic candidates trips it, whatever field that gate is on, without the
    test needing to know the new gate exists.

    Verified 2026-08-17 to FAIL (12.5% acceptance) when the reverted
    MIN_NOWCAST_CONFIDENCE=0.15 gate is re-introduced.
    """

    def _acceptance_rate(self):
        accepted = 0
        total = 0
        for conf in (0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.13, 0.24):
            for gap in (0.01, 0.05, 0.10, 0.20, 0.35):
                for disc in (PriceDiscountStatus.NOT_DISCOUNTED,
                             PriceDiscountStatus.MODERATELY_DISCOUNTED):
                    for rel in (0.0, 0.5):
                        nc, exp = _nc(conf=conf), _exp(gap=gap)
                        rs = RelativeStrength(vs_nifty=0.0, vs_sector=0.0, score=rel)
                        b = compute_score(nc, exp, _pd(disc), rs, regime_score=0.7)
                        d, _ = decide(b, nc, exp, _pd(disc), rs, _event())
                        total += 1
                        if d == PreEventDecision.LONG:
                            accepted += 1
        return accepted / total, accepted, total

    def test_gate_stack_is_not_over_restrictive(self):
        rate, accepted, total = self._acceptance_rate()
        assert rate >= 0.30, (
            f"the decision gates accept only {rate:.1%} ({accepted}/{total}) of a "
            "realistic candidate grid. A gate is excluding most valid setups. If "
            "this is intentional, REPLAY the change against historical trades "
            "before shipping it — the 2026-08-17 incident shipped a gate that "
            "looked correct, passed all 55 tests, and discarded 90% of the "
            "profitable book. See post-mortem §8.")

    def test_gate_stack_is_not_vacuous(self):
        rate, accepted, total = self._acceptance_rate()
        assert rate <= 0.95, (
            f"the decision gates accept {rate:.1%} ({accepted}/{total}) of every "
            "candidate thrown at them, including the weakest — they are not "
            "filtering anything.")


class TestScoringWeightsStayHonest:
    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_no_single_component_dominates(self):
        """One factor worth more than half the score means the other five are
        decoration — and if that factor turns out to be a sector label (as
        nowcast did), the whole score becomes one."""
        assert max(WEIGHTS.values()) <= 0.5, (
            f"component weights {WEIGHTS} — one factor dominates the composite")
