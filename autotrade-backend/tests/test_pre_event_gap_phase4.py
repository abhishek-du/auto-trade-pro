"""Phase 4 tests: scoring, deterministic decision gates, orchestrator engine.

Scoring/decision are pure functions (no I/O) tested directly. The engine is
tested with mocked component reads. Emphasis: the gates are fail-closed and a
high score can NEVER override a failed data-quality / event-timing / price-
extension gate; short-side stays disabled.
"""
from __future__ import annotations

from datetime import datetime, date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import json
import pytest

from engine.pre_event_expectation_gap.types import (
    NowcastResult, ExpectationEstimate, PriceDiscount, RelativeStrength,
    ScheduledEvent, PreEventType, PreEventDecision, NowcastStatus, Direction,
    PriceDiscountStatus,
)
from engine.pre_event_expectation_gap.scoring import compute_score, WEIGHTS, ScoreBreakdown
from engine.pre_event_expectation_gap.decision import (
    decide, MIN_EVENT_CONFIDENCE, LONG_SCORE_BAR,
)
from engine.pre_event_expectation_gap.engine import PreEventExpectationGapEngine, _regime_score


# ── factories ────────────────────────────────────────────────────────────────

def _nc(status=NowcastStatus.OK, profit=Direction.POSITIVE, conf=0.4, completeness=0.25,
        implied=0.40, baseline=0.20):
    return NowcastResult(status=status, profit_direction=profit, revenue_direction=profit,
                         margin_direction=profit, confidence=conf, data_completeness=completeness,
                         sector="AUTO", implied_profit_growth=implied, baseline_profit_growth=baseline)

def _exp(gap=0.20, available=True, anchor="CONSENSUS", is_market_expectation=True):
    """Default is now a CONSENSUS anchor: since P2-1 a candidate whose anchor is
    not a real market expectation is rejected up front, so tests exercising the
    DOWNSTREAM gates need a valid market-expectation anchor to reach them.
    Pass is_market_expectation=False to exercise that gate itself."""
    return ExpectationEstimate(our_expected_pat_growth=0.40, expectation_gap=gap,
                               gap_available=available, anchor_used=anchor,
                               anchor_type=anchor,
                               is_market_expectation=is_market_expectation)

def _pd(status=PriceDiscountStatus.NOT_DISCOUNTED, returns=None):
    return PriceDiscount(returns=returns if returns is not None else {"20d": 0.02},
                         rel_strength_nifty=0.02, status=status)

def _rs(score=0.3):
    return RelativeStrength(vs_nifty=0.03, vs_sector=0.02, score=score)

def _event(conf=0.95):
    return ScheduledEvent(symbol="MARUTI.NS", event_type=PreEventType.QUARTERLY_RESULT,
                          event_date=date(2026, 10, 25), event_confidence=conf, source="cal")


# ── Scoring (pure) ───────────────────────────────────────────────────────────

class TestScoring:
    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_total_in_0_100(self):
        b = compute_score(_nc(), _exp(), _pd(), _rs(), regime_score=0.7)
        assert 0.0 <= b.total <= 100.0

    def test_components_present_and_sum_to_total(self):
        b = compute_score(_nc(), _exp(), _pd(), _rs(), regime_score=0.7)
        assert set(b.components.keys()) == set(WEIGHTS.keys())
        assert abs(sum(b.components.values()) - b.total) < 0.05

    def test_positive_nowcast_scores_higher_than_negative(self):
        pos = compute_score(_nc(profit=Direction.POSITIVE), _exp(), _pd(), _rs(), 0.7)
        neg = compute_score(_nc(profit=Direction.NEGATIVE), _exp(), _pd(), _rs(), 0.7)
        assert pos.subscores["nowcast"] > neg.subscores["nowcast"]

    def test_overextended_discount_scores_low(self):
        low = compute_score(_nc(), _exp(), _pd(PriceDiscountStatus.OVEREXTENDED), _rs(), 0.7)
        high = compute_score(_nc(), _exp(), _pd(PriceDiscountStatus.NOT_DISCOUNTED), _rs(), 0.7)
        assert low.subscores["discount"] < high.subscores["discount"]

    def test_data_quality_zero_when_nowcast_unavailable(self):
        b = compute_score(_nc(status=NowcastStatus.UNAVAILABLE), _exp(), _pd(), _rs(), 0.7)
        assert b.data_quality_score == 0.0

    def test_data_quality_drops_without_price_or_anchor(self):
        full = compute_score(_nc(), _exp(available=True), _pd(returns={"20d": 0.02}), _rs(), 0.7)
        no_price = compute_score(_nc(), _exp(available=True), _pd(returns={}), _rs(), 0.7)
        no_anchor = compute_score(_nc(), _exp(available=False), _pd(returns={"20d": 0.02}), _rs(), 0.7)
        assert no_price.data_quality_score < full.data_quality_score
        assert no_anchor.data_quality_score < full.data_quality_score


class TestEvidenceRecentering:
    """P2-5 (2026-08-17 forensic post-mortem): the 0.5-centered subscores must
    contribute 0 — not half their factor — when they carry no information.

    Before this, the weakest candidate that could reach the LONG branch scored
    67.81 against a LONG_SCORE_BAR of 60, and the MINIMUM score across all 224
    executed trades was 62.3: the bar rejected nothing at all.
    """

    def _weakest_long_candidate(self):
        """Barely-positive gap, weakest sector confidence, not overextended —
        i.e. structurally eligible but carrying essentially no evidence."""
        return compute_score(
            _nc(conf=0.06, implied=0.0, baseline=0.0),
            _exp(gap=0.001),
            _pd(PriceDiscountStatus.NOT_DISCOUNTED),
            RelativeStrength(vs_nifty=0.0, vs_sector=0.0, score=0.0),
            regime_score=0.7,
        )

    def test_zero_evidence_candidate_falls_below_the_long_bar(self):
        b = self._weakest_long_candidate()
        assert b.total < LONG_SCORE_BAR, (
            f"a no-evidence candidate scored {b.total}, still clearing the "
            f"{LONG_SCORE_BAR} bar — the bar is decorative again")

    def test_neutral_reads_contribute_nothing(self):
        """A dead-neutral nowcast/gap/relative must add 0, not half."""
        b = compute_score(
            _nc(profit=Direction.NEUTRAL, conf=0.0),
            _exp(gap=0.0),
            _pd(PriceDiscountStatus.NOT_DISCOUNTED),
            RelativeStrength(vs_nifty=0.0, vs_sector=0.0, score=0.0),
            regime_score=0.7,
        )
        assert b.components["nowcast"] == 0.0
        assert b.components["gap"] == 0.0
        assert b.components["relative"] == 0.0

    def test_contrary_evidence_clamps_to_zero_not_negative(self):
        """Readings below neutral are already NO_TRADE at the direction gate;
        they must not earn partial credit here, nor go negative."""
        b = compute_score(
            _nc(profit=Direction.NEGATIVE, conf=0.4),
            _exp(gap=-0.20),
            _pd(PriceDiscountStatus.NOT_DISCOUNTED),
            RelativeStrength(vs_nifty=0.0, vs_sector=0.0, score=-0.8),
            regime_score=0.7,
        )
        for k in ("nowcast", "gap", "relative"):
            assert b.components[k] == 0.0, f"{k} = {b.components[k]}"

    def test_stronger_evidence_still_scores_strictly_higher(self):
        """Re-centering must preserve ordering — it rescales, not flattens."""
        weak = compute_score(_nc(conf=0.10), _exp(gap=0.02), _pd(), _rs(score=0.0), 0.7)
        strong = compute_score(_nc(conf=0.10), _exp(gap=0.20), _pd(), _rs(score=0.6), 0.7)
        assert strong.total > weak.total

    def test_raw_subscores_are_still_reported_uncentered(self):
        """The audit trail / UI explain from the raw 0.5-centered values, and
        they must stay comparable to historical rows — only the CONTRIBUTION
        is re-centered."""
        b = compute_score(
            _nc(profit=Direction.NEUTRAL, conf=0.0), _exp(gap=0.0), _pd(),
            RelativeStrength(vs_nifty=0.0, vs_sector=0.0, score=0.0), 0.7)
        assert b.subscores["nowcast"] == 0.5     # raw, unchanged
        assert b.components["nowcast"] == 0.0    # contribution, re-centered

    def test_sector_confidence_bias_is_reduced(self):
        """nc.confidence is a per-sector constant (0.06 Banking … 0.24 IT), so
        identical evidence used to score ~8.4 points apart purely by sector.
        Re-centering can't remove that entirely (confidence IS the nowcast
        magnitude) but must materially shrink it."""
        common = dict(expectation=_exp(gap=0.10), price_discount=_pd(),
                      relative_strength=RelativeStrength(vs_nifty=0.0, vs_sector=0.0, score=0.0),
                      regime_score=0.7)
        banking = compute_score(nowcast=_nc(conf=0.06), **common)
        it      = compute_score(nowcast=_nc(conf=0.24), **common)
        spread = it.total - banking.total
        assert 0 < spread < 8.44, f"sector spread {spread:.2f} not reduced from 8.44"


# ── Decision gates (pure) ────────────────────────────────────────────────────

def _score(total=70.0, dq=1.0):
    return ScoreBreakdown(total=total, data_quality_score=dq, components={}, subscores={})


class TestMarketExpectationGate:
    """P2-1 (2026-08-17): the strategy trades the gap vs MARKET expectation, so
    a candidate anchored on anything else (the 3yr-CAGR proxy that every live
    trade silently used until now) must not trade at all."""

    def test_non_market_anchor_is_no_trade(self):
        d, reason = decide(
            _score(total=95.0),                     # score can't buy past it
            _nc(), _exp(anchor="HISTORICAL_BASELINE_3Y_CAGR", is_market_expectation=False),
            _pd(), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE
        assert "not a market expectation" in reason

    def test_consensus_anchor_passes_the_gate(self):
        d, _ = decide(_score(total=70.0), _nc(),
                      _exp(anchor="CONSENSUS", is_market_expectation=True),
                      _pd(), _rs(), _event())
        assert d == PreEventDecision.LONG

    def test_gate_can_be_disabled(self):
        """The restriction is a deliberate universe cut, not a correctness
        invariant — operators must be able to revert to the old behaviour."""
        import engine.pre_event_expectation_gap.decision as dec
        original = dec.REQUIRE_MARKET_EXPECTATION
        try:
            dec.REQUIRE_MARKET_EXPECTATION = False
            d, _ = dec.decide(
                _score(total=70.0), _nc(),
                _exp(anchor="HISTORICAL_BASELINE_3Y_CAGR", is_market_expectation=False),
                _pd(), _rs(), _event())
            assert d == PreEventDecision.LONG
        finally:
            dec.REQUIRE_MARKET_EXPECTATION = original

    def test_gate_runs_after_the_hard_data_gates(self):
        """Ordering check: a candidate that fails an EARLIER gate must report
        that failure, not the market-expectation one — the reason string is
        the audit trail for why a trade didn't happen."""
        d, reason = decide(
            _score(total=70.0), _nc(status=NowcastStatus.UNAVAILABLE),
            _exp(is_market_expectation=False), _pd(), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE
        assert "nowcast unavailable" in reason


class TestDecisionGates:
    def test_nowcast_unavailable_is_no_trade(self):
        d, _ = decide(_score(), _nc(status=NowcastStatus.UNAVAILABLE), _exp(), _pd(), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE

    def test_uncertain_event_timing_is_no_trade(self):
        d, _ = decide(_score(), _nc(), _exp(), _pd(), _rs(), _event(conf=0.3))
        assert d == PreEventDecision.NO_TRADE

    def test_missing_price_history_is_no_trade(self):
        d, _ = decide(_score(), _nc(), _exp(), _pd(returns={}), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE

    def test_low_data_quality_is_no_trade(self):
        d, _ = decide(_score(dq=0.1), _nc(), _exp(), _pd(), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE

    def test_no_anchor_is_no_trade(self):
        d, _ = decide(_score(), _nc(), _exp(available=False), _pd(), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE

    def test_bearish_is_no_trade_never_short(self):
        # negative nowcast + negative gap → must be NO_TRADE, never SHORT (Phase 1)
        d, reason = decide(_score(), _nc(profit=Direction.NEGATIVE),
                           _exp(gap=-0.15), _pd(), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE
        assert d != PreEventDecision.SHORT

    def test_no_positive_gap_is_no_trade(self):
        d, _ = decide(_score(), _nc(profit=Direction.NEUTRAL), _exp(gap=0.0), _pd(), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE

    def test_overextended_bullish_is_wait_even_with_high_score(self):
        # A high score MUST NOT buy past the price-extension gate.
        d, _ = decide(_score(total=95.0), _nc(), _exp(),
                      _pd(PriceDiscountStatus.OVEREXTENDED), _rs(), _event())
        assert d == PreEventDecision.WAIT

    def test_bullish_low_score_is_no_trade(self):
        d, _ = decide(_score(total=40.0), _nc(), _exp(), _pd(), _rs(), _event())
        assert d == PreEventDecision.NO_TRADE

    def test_a_plus_setup_is_long(self):
        d, _ = decide(_score(total=75.0), _nc(), _exp(),
                      _pd(PriceDiscountStatus.NOT_DISCOUNTED), _rs(), _event())
        assert d == PreEventDecision.LONG

    def test_bullish_mid_but_heavily_discounted_is_wait(self):
        d, _ = decide(_score(total=75.0), _nc(), _exp(),
                      _pd(PriceDiscountStatus.HEAVILY_DISCOUNTED), _rs(), _event())
        assert d == PreEventDecision.WAIT


# ── Regime helper ────────────────────────────────────────────────────────────

class TestRegimeScore:
    @pytest.mark.asyncio
    async def test_nifty_above_sma_is_bullish_regime(self):
        snap = SimpleNamespace(nifty_candles=AsyncMock(
            return_value=[SimpleNamespace(close=120)] + [SimpleNamespace(close=100) for _ in range(59)]))
        assert await _regime_score(snap) == 0.7

    @pytest.mark.asyncio
    async def test_insufficient_nifty_is_neutral(self):
        snap = SimpleNamespace(nifty_candles=AsyncMock(return_value=[SimpleNamespace(close=100)]))
        assert await _regime_score(snap) == 0.5


# ── Orchestrator engine ──────────────────────────────────────────────────────

def _c(close, high=None, vol=1000):
    return SimpleNamespace(close=close, high=high or close * 1.01, low=close * 0.99,
                           open=close, volume=vol, timestamp=datetime.now())

def _income_strong():
    def q(p, v): return {"period": p, "value": v}
    return {"income_statement": [
        {"category": "revenue", "history": [q("Jun 2026", 130), q("Mar 2026", 120), q("Dec 2025", 115),
                                            q("Sep 2025", 110), q("Jun 2025", 100), q("Mar 2025", 95)]},
        {"category": "net_profit", "history": [q("Jun 2026", 20), q("Mar 2026", 16), q("Dec 2025", 14),
                                               q("Sep 2025", 12), q("Jun 2025", 10), q("Mar 2025", 9)]},
    ]}


def _patch_baseline(value=0.20, known_at=datetime(2026, 9, 1)):
    # A valid, point-in-time (known_at < as_of) historical-baseline anchor so
    # the engine can reach a decision. Patched in expectation.py's namespace.
    from engine.pre_event_expectation_gap.financials import HistoricalBaseline
    return patch("engine.pre_event_expectation_gap.expectation.get_historical_baseline_3y_cagr",
                 AsyncMock(return_value=HistoricalBaseline(value=value, known_at=known_at)))


class TestEngine:
    @pytest.mark.asyncio
    async def test_predict_calm_price_is_long(self):
        stock = [_c(103 - i * 0.03) for i in range(90)]   # mild uptrend, not overextended
        flat = [_c(100) for _ in range(90)]

        async def fake(sym, tf="1d", limit=90, session=None, before=None):
            return (flat if sym.startswith("^") else stock)[:limit]

        eng = PreEventExpectationGapEngine()
        with patch("crawler.price_feed.get_latest_candles", side_effect=fake), \
             patch("crawler.upstox_data.get_income_statement", AsyncMock(return_value=_income_strong())), \
             _patch_baseline():
            p = await eng.predict("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert p.decision == PreEventDecision.LONG
        assert p.source == "AI Predict"
        assert json.dumps(p.to_audit_dict())   # audit serializable

    @pytest.mark.asyncio
    async def test_predict_overextended_is_wait(self):
        stock = [_c(130 - i * 0.5) for i in range(90)]    # big run-up → overextended
        flat = [_c(100) for _ in range(90)]

        async def fake(sym, tf="1d", limit=90, session=None, before=None):
            return (flat if sym.startswith("^") else stock)[:limit]

        eng = PreEventExpectationGapEngine()
        with patch("crawler.price_feed.get_latest_candles", side_effect=fake), \
             patch("crawler.upstox_data.get_income_statement", AsyncMock(return_value=_income_strong())), \
             _patch_baseline():
            p = await eng.predict("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert p.decision == PreEventDecision.WAIT

    @pytest.mark.asyncio
    async def test_predict_unknown_sector_is_no_trade(self):
        stock = [_c(100) for _ in range(90)]

        async def fake(sym, tf="1d", limit=90, session=None, before=None):
            return stock[:limit]

        eng = PreEventExpectationGapEngine()
        ev = ScheduledEvent(symbol="TOTALLYUNKNOWN.NS", event_type=PreEventType.QUARTERLY_RESULT,
                            event_date=date(2026, 10, 25), event_confidence=0.95)
        with patch("crawler.price_feed.get_latest_candles", side_effect=fake):
            p = await eng.predict("TOTALLYUNKNOWN.NS", ev, datetime(2026, 10, 1), AsyncMock())
        assert p.decision == PreEventDecision.NO_TRADE
        assert p.nowcast.status == NowcastStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_scan_disabled_returns_empty(self):
        eng = PreEventExpectationGapEngine()
        with patch("engine.pre_event_expectation_gap.engine.settings.PRE_EVENT_GAP_ENABLED", False):
            out = await eng.scan(AsyncMock(), universe=["MARUTI.NS"])
        assert out == []

    @pytest.mark.asyncio
    async def test_scan_fail_closed_on_discovery_error(self):
        eng = PreEventExpectationGapEngine()
        with patch("engine.pre_event_expectation_gap.engine.settings.PRE_EVENT_GAP_ENABLED", True), \
             patch("engine.pre_event_expectation_gap.engine.discover_scheduled_events",
                   AsyncMock(side_effect=Exception("boom"))):
            out = await eng.scan(AsyncMock(), universe=["MARUTI.NS"])
        assert out == []

    @pytest.mark.asyncio
    async def test_scan_isolates_per_symbol_failure(self):
        eng = PreEventExpectationGapEngine()
        events = [
            ScheduledEvent(symbol="GOOD.NS", event_type=PreEventType.QUARTERLY_RESULT,
                           event_date=date(2026, 10, 25), event_confidence=0.9),
            ScheduledEvent(symbol="BAD.NS", event_type=PreEventType.QUARTERLY_RESULT,
                           event_date=date(2026, 10, 25), event_confidence=0.9),
        ]

        async def flaky_predict(symbol, event, as_of, session):
            if symbol == "BAD.NS":
                raise RuntimeError("prediction blew up")
            return SimpleNamespace(symbol=symbol, pre_event_score=50.0)

        with patch("engine.pre_event_expectation_gap.engine.settings.PRE_EVENT_GAP_ENABLED", True), \
             patch("engine.pre_event_expectation_gap.engine.discover_scheduled_events",
                   AsyncMock(return_value=events)), \
             patch.object(eng, "predict", side_effect=flaky_predict):
            out = await eng.scan(AsyncMock(), universe=["GOOD.NS", "BAD.NS"])
        assert [p.symbol for p in out] == ["GOOD.NS"]   # BAD isolated, GOOD survives
