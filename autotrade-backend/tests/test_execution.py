"""Regression tests for the execution leg of the News-Only pipeline:
crawler/market_snapshot.py (brand new this session, zero prior tests by
definition) and news_discovery_engine.py::_execute_news_trade() (also zero
prior coverage per the 2026-07-21 coverage audit).

This is the last hop before real money(-shaped, currently paper-mode)
movement: TradeIntent construction, entry-price resolution, and the
Zerodha-REST-fallback fix shipped earlier this session (the root cause of
the 2026-07-21 TVS Motor "no live price available" incident).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import crawler.market_snapshot as market_snapshot
from crawler.market_snapshot import get_market_snapshot
from engine.decision_router import (
    ConfidenceSource,
    EventDirectness,
    RoutingOutcome,
    RoutingResult,
    StrategyFamily,
    TradeMode,
)
from news_discovery_engine import (
    _compute_second_order_confidence,
    _execute_news_trade,
    _get_market_confirmation,
)


@pytest.fixture(autouse=True)
def _clear_snapshot_cache():
    market_snapshot._snapshot_cache.clear()
    yield
    market_snapshot._snapshot_cache.clear()


# ── crawler/market_snapshot.py::get_market_snapshot ────────────────────────────

class TestMarketSnapshotPriorityChain:
    @pytest.mark.asyncio
    async def test_websocket_tick_used_when_available(self):
        with patch("crawler.zerodha_ticker.get_live_tick",
                   MagicMock(return_value={"last_price": 100.0, "ohlc": {"close": 98.0}})), \
             patch("crawler.zerodha_market.get_full_quote", AsyncMock()) as mock_rest, \
             patch("crawler.live_prices.yfinance_ltp_batch", AsyncMock()) as mock_yf:
            snap = await get_market_snapshot("TESTCO.NS")
        assert snap is not None
        assert snap.ltp == 100.0
        assert snap.source == "zerodha_ws"
        mock_rest.assert_not_called()
        mock_yf.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_rest_when_no_websocket_tick(self):
        with patch("crawler.zerodha_ticker.get_live_tick", MagicMock(return_value=None)), \
             patch("crawler.zerodha_market.get_full_quote",
                   AsyncMock(return_value={"last_price": 200.0, "ohlc": {}, "volume": 1000})), \
             patch("crawler.live_prices.yfinance_ltp_batch", AsyncMock()) as mock_yf:
            snap = await get_market_snapshot("TESTCO.NS")
        assert snap is not None
        assert snap.ltp == 200.0
        assert snap.source == "zerodha_rest"
        mock_yf.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_yfinance_when_ws_and_rest_both_fail(self):
        # THE exact scenario that produced the 2026-07-21 TVS Motor bug: a
        # standalone process with no WebSocket tick AND (in the old code) no
        # REST fallback either. Confirms yfinance is now a real third leg,
        # not that the bug is reproduced -- REST failing here to prove the
        # chain still resolves via yfinance.
        with patch("crawler.zerodha_ticker.get_live_tick", MagicMock(return_value=None)), \
             patch("crawler.zerodha_market.get_full_quote", AsyncMock(return_value={})), \
             patch("crawler.live_prices.yfinance_ltp_batch", AsyncMock(return_value={"TESTCO.NS": 150.0})):
            snap = await get_market_snapshot("TESTCO.NS")
        assert snap is not None
        assert snap.ltp == 150.0
        assert snap.source == "yfinance"

    @pytest.mark.asyncio
    async def test_all_sources_exhausted_returns_none(self):
        with patch("crawler.zerodha_ticker.get_live_tick", MagicMock(return_value=None)), \
             patch("crawler.zerodha_market.get_full_quote", AsyncMock(return_value={})), \
             patch("crawler.live_prices.yfinance_ltp_batch", AsyncMock(return_value={})):
            snap = await get_market_snapshot("TESTCO.NS")
        assert snap is None

    @pytest.mark.asyncio
    async def test_zero_or_negative_price_treated_as_unavailable(self):
        with patch("crawler.zerodha_ticker.get_live_tick", MagicMock(return_value=None)), \
             patch("crawler.zerodha_market.get_full_quote", AsyncMock(return_value={"last_price": 0.0})), \
             patch("crawler.live_prices.yfinance_ltp_batch", AsyncMock(return_value={"TESTCO.NS": 0.0})):
            snap = await get_market_snapshot("TESTCO.NS")
        assert snap is None


class TestMarketSnapshotCaching:
    @pytest.mark.asyncio
    async def test_second_call_within_ttl_uses_cache_not_refetch(self):
        rest_mock = AsyncMock(return_value={"last_price": 300.0, "ohlc": {}, "volume": 0})
        with patch("crawler.zerodha_ticker.get_live_tick", MagicMock(return_value=None)), \
             patch("crawler.zerodha_market.get_full_quote", rest_mock):
            snap1 = await get_market_snapshot("TESTCO.NS")
            snap2 = await get_market_snapshot("TESTCO.NS")
        assert snap1.ltp == snap2.ltp == 300.0
        rest_mock.assert_called_once()  # second call served from cache, no re-fetch

    @pytest.mark.asyncio
    async def test_cache_is_per_symbol(self):
        rest_mock = AsyncMock(side_effect=[
            {"last_price": 100.0, "ohlc": {}, "volume": 0},
            {"last_price": 200.0, "ohlc": {}, "volume": 0},
        ])
        with patch("crawler.zerodha_ticker.get_live_tick", MagicMock(return_value=None)), \
             patch("crawler.zerodha_market.get_full_quote", rest_mock):
            snap_a = await get_market_snapshot("AAA.NS")
            snap_b = await get_market_snapshot("BBB.NS")
        assert snap_a.ltp == 100.0
        assert snap_b.ltp == 200.0
        assert rest_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_expired_ttl_triggers_refetch(self):
        rest_mock = AsyncMock(return_value={"last_price": 400.0, "ohlc": {}, "volume": 0})
        with patch("crawler.zerodha_ticker.get_live_tick", MagicMock(return_value=None)), \
             patch("crawler.zerodha_market.get_full_quote", rest_mock):
            await get_market_snapshot("TESTCO.NS")
            await get_market_snapshot("TESTCO.NS", max_age_sec=0.0)  # force immediate expiry
        assert rest_mock.call_count == 2


# ── news_discovery_engine.py::_execute_news_trade ──────────────────────────────

def _mock_session_ctx():
    session = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _make_snapshot(ltp=100.0, source="zerodha_rest"):
    return SimpleNamespace(ltp=ltp, source=source, fetched_at_ist="2026-07-21T14:30:00+05:30")


class TestExecuteNewsTrade:
    @pytest.mark.asyncio
    async def test_no_price_available_skips_execution(self):
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=None)):
            result = await _execute_news_trade("TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"})
        assert result is False

    @pytest.mark.asyncio
    async def test_zero_price_snapshot_skips_execution(self):
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=0.0))):
            result = await _execute_news_trade("TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"})
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_execution_returns_true_and_builds_correct_intent(self):
        captured_intent = {}

        async def _fake_execute(intent, session):
            captured_intent["intent"] = intent
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="paper trade opened")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=250.0))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 240.0, "target_1": 270.0, "target_2": 280.0, "atr": 5.0, "source": "atr", "gap_pct": 0.01})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _execute_news_trade(
                "TESTCO.NS", "BUY", "TVS Motor Q1 results",
                {"confidence": 82, "bull": "strong earnings", "thesis": "grounded thesis"},
                event_id=42, evidence_ids=["1"],
            )

        assert result is True
        intent = captured_intent["intent"]
        assert intent.symbol == "TESTCO.NS"
        assert intent.action == "BUY"
        assert intent.entry_price == 250.0
        assert intent.stop_loss == 240.0
        assert intent.take_profit == 270.0
        assert intent.strategy_family == StrategyFamily.EVENT_DRIVEN
        assert intent.event_directness == EventDirectness.DIRECT  # default
        assert intent.confidence_source == ConfidenceSource.CALCULATED  # default
        assert intent.strategy == "NEWS_DIRECT"
        assert intent.product == "CNC"
        assert intent.event_id == 42
        assert any("TVS Motor Q1 results" in p for p in intent.extra["reasoning_points"])
        assert any("grounded thesis" in p for p in intent.extra["reasoning_points"])

    @pytest.mark.asyncio
    async def test_sell_side_uses_mis_product(self):
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=100.0))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 105.0, "target_1": 90.0, "target_2": 85.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)) as mock_exec, \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            await _execute_news_trade("TESTCO.NS", "SELL", "headline", {"confidence": 80, "bull": "b"})
        intent = mock_exec.call_args[0][0]
        assert intent.product == "MIS"

    @pytest.mark.asyncio
    async def test_second_order_uses_news_cascade_strategy_name(self):
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=100.0))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)) as mock_exec, \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            await _execute_news_trade(
                "TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"},
                event_directness=EventDirectness.SECOND_ORDER,
            )
        intent = mock_exec.call_args[0][0]
        assert intent.strategy == "NEWS_CASCADE"

    @pytest.mark.asyncio
    async def test_gate_rejection_returns_false(self):
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN, mode=TradeMode.PAPER, reason="blocked")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=100.0))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _execute_news_trade("TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"})
        assert result is False

    @pytest.mark.asyncio
    async def test_explicit_event_directness_and_confidence_source_override_defaults(self):
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=100.0))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)) as mock_exec, \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            await _execute_news_trade(
                "TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"},
                event_directness=EventDirectness.SECOND_ORDER, confidence_source=ConfidenceSource.HARDCODED,
            )
        intent = mock_exec.call_args[0][0]
        assert intent.event_directness == EventDirectness.SECOND_ORDER
        assert intent.confidence_source == ConfidenceSource.HARDCODED

    @pytest.mark.asyncio
    async def test_confidence_factors_default_built_from_verdict_when_not_supplied(self):
        # Regression guard for the "confidence hardcoded to a fake 80%,
        # invisible until the raw code was read" incident (2026-07-22): a
        # DIRECT verdict with no explicit confidence_factors must still get a
        # real breakdown built from the verdict itself, not silently omitted.
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=100.0))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)) as mock_exec, \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            await _execute_news_trade(
                "TESTCO.NS", "BUY", "headline",
                {"confidence": 82, "bull": "strong beat", "bear": "risk", "key_risk": "liquidity",
                 "thesis": "grounded thesis", "market_confirmation": "POSITIVE",
                 "tools_used": ["fundamentals", "sector"], "grounding": {"grounded": True},
                 "model_reasoning": "SWING_AGENT: ... FINAL_JUDGE: ..."},
            )
        intent = mock_exec.call_args[0][0]
        cf = intent.confidence_factors
        assert cf["kind"] == "llm_tooluse"
        assert cf["confidence"] == 82.0
        assert cf["bull"] == "strong beat"
        assert cf["key_risk"] == "liquidity"
        assert cf["tools_used"] == ["fundamentals", "sector"]
        assert cf["grounding"] == {"grounded": True}
        assert "FINAL_JUDGE" in cf["model_reasoning"]

    @pytest.mark.asyncio
    async def test_explicit_confidence_factors_passed_through_unmodified(self):
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        explicit = {"kind": "second_order_formula", "confidence": 42.0, "relationship_strength": 0.7}
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=100.0))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)) as mock_exec, \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            await _execute_news_trade(
                "TESTCO.NS", "BUY", "headline", {"confidence": 42, "bull": "b"},
                confidence_factors=explicit,
            )
        intent = mock_exec.call_args[0][0]
        assert intent.confidence_factors == explicit


# ── Second-order confidence formula (2026-07-22) ──────────────────────────────
# Root-caused the same day: a cascade trade's confidence was found hardcoded
# to a fake 80% with zero real evaluation behind it -- these guard the
# replacement formula (event_strength x relationship_strength x
# company_exposure x market_confirmation) and the live-price-based
# market_confirmation check that feeds it.

class TestSecondOrderConfidenceFormula:
    def test_all_strong_factors_yields_high_confidence(self):
        conf, mult = _compute_second_order_confidence(90.0, 0.9, 0.9, "POSITIVE")
        assert mult == 1.0
        assert conf == round(90.0 * 0.9 * 0.9 * 1.0, 1)

    def test_negative_confirmation_heavily_discounts(self):
        conf_pos, _ = _compute_second_order_confidence(90.0, 0.8, 0.8, "POSITIVE")
        conf_neg, mult_neg = _compute_second_order_confidence(90.0, 0.8, 0.8, "NEGATIVE")
        assert mult_neg == 0.2
        assert conf_neg < conf_pos
        assert conf_neg == round(90.0 * 0.8 * 0.8 * 0.2, 1)

    def test_neutral_confirmation_sits_between(self):
        _, mult_pos = _compute_second_order_confidence(90.0, 0.8, 0.8, "POSITIVE")
        _, mult_neu = _compute_second_order_confidence(90.0, 0.8, 0.8, "NEUTRAL")
        _, mult_neg = _compute_second_order_confidence(90.0, 0.8, 0.8, "NEGATIVE")
        assert mult_neg < mult_neu < mult_pos

    def test_unknown_confirmation_label_falls_back_to_0_5_multiplier(self):
        conf, mult = _compute_second_order_confidence(100.0, 1.0, 1.0, "SOMETHING_UNEXPECTED")
        assert mult == 0.5

    def test_result_clamped_to_0_100_range(self):
        conf, _ = _compute_second_order_confidence(1000.0, 5.0, 5.0, "POSITIVE")
        assert conf <= 100.0
        conf, _ = _compute_second_order_confidence(-50.0, 0.5, 0.5, "POSITIVE")
        assert conf >= 0.0

    def test_weak_relationship_strength_alone_can_sink_below_bar(self):
        # Even a very strong primary event with a barely-qualifying relationship
        # (right at the sector_graph.py quality floor) should land well below
        # SECOND_ORDER_MIN_CONFIDENCE=70 by design -- conservative on purpose.
        conf, _ = _compute_second_order_confidence(95.0, 0.6, 0.3, "POSITIVE")
        assert conf < 70.0


class TestMarketConfirmation:
    @pytest.mark.asyncio
    async def test_price_moved_up_confirms_buy(self):
        candles = _candles(100.0, 100.0, 100.0)  # ref close = candles[-3] = 100.0
        with patch("crawler.market_snapshot.get_market_snapshot",
                    AsyncMock(return_value=_make_snapshot(ltp=101.0))), \
             patch("crawler.zerodha_market.get_kite_historical", AsyncMock(return_value=candles)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _get_market_confirmation("TESTCO.NS", "BUY")
        assert result == "POSITIVE"

    @pytest.mark.asyncio
    async def test_price_moved_down_against_buy_is_negative(self):
        candles = _candles(100.0, 100.0, 100.0)
        with patch("crawler.market_snapshot.get_market_snapshot",
                    AsyncMock(return_value=_make_snapshot(ltp=99.0))), \
             patch("crawler.zerodha_market.get_kite_historical", AsyncMock(return_value=candles)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _get_market_confirmation("TESTCO.NS", "BUY")
        assert result == "NEGATIVE"

    @pytest.mark.asyncio
    async def test_flat_price_is_neutral(self):
        candles = _candles(100.0, 100.0, 100.0)
        with patch("crawler.market_snapshot.get_market_snapshot",
                    AsyncMock(return_value=_make_snapshot(ltp=100.05))), \
             patch("crawler.zerodha_market.get_kite_historical", AsyncMock(return_value=candles)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _get_market_confirmation("TESTCO.NS", "BUY")
        assert result == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_sell_side_direction_is_inverted(self):
        candles = _candles(100.0, 100.0, 100.0)
        with patch("crawler.market_snapshot.get_market_snapshot",
                    AsyncMock(return_value=_make_snapshot(ltp=99.0))), \
             patch("crawler.zerodha_market.get_kite_historical", AsyncMock(return_value=candles)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _get_market_confirmation("TESTCO.NS", "SELL")
        assert result == "POSITIVE"  # price fell, confirming a SELL thesis

    @pytest.mark.asyncio
    async def test_no_price_snapshot_fails_neutral_not_positive(self):
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=None)):
            result = await _get_market_confirmation("TESTCO.NS", "BUY")
        assert result == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_no_candles_fails_neutral(self):
        with patch("crawler.market_snapshot.get_market_snapshot",
                    AsyncMock(return_value=_make_snapshot(ltp=100.0))), \
             patch("crawler.zerodha_market.get_kite_historical", AsyncMock(return_value=[])), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _get_market_confirmation("TESTCO.NS", "BUY")
        assert result == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_exception_fails_neutral_not_positive(self):
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _get_market_confirmation("TESTCO.NS", "BUY")
        assert result == "NEUTRAL"


# ── Late-entry gate (2026-07-22 post-mortem) ──────────────────────────────────
# NESTLEIND was bought at the exact top of an 11:19 IST spike that ran
# 10:45-11:15 IST; TVSMOTOR at the day high after a 2-session +10% run.
# _execute_news_trade() now checks the ~30-minute-old candle close against
# the live entry price and skips a chase entry beyond NEWS_MAX_PRE_ENTRY_
# SPIKE_PCT (default 2.0%) in the trade's own direction.

def _candles(*closes: float) -> list[dict]:
    return [{"open": c, "high": c, "low": c, "close": c} for c in closes]


class TestLateEntryGate:
    @pytest.mark.asyncio
    async def test_buy_skipped_after_large_prior_spike(self):
        # Reference close (3rd-from-last bar) far below current entry -- a
        # >2% run already happened before we're trying to buy.
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=1500.0))), \
             patch("crawler.zerodha_market.get_kite_historical",
                   AsyncMock(return_value=_candles(1450, 1460, 1470, 1480, 1490))), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock()) as mock_exec:
            result = await _execute_news_trade("TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"})
        assert result is False
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_skipped_after_large_prior_drop(self):
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=1400.0))), \
             patch("crawler.zerodha_market.get_kite_historical",
                   AsyncMock(return_value=_candles(1500, 1480, 1460, 1440, 1420))), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock()) as mock_exec:
            result = await _execute_news_trade("TESTCO.NS", "SELL", "headline", {"confidence": 80, "bull": "b"})
        assert result is False
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_buy_allowed_within_normal_move(self):
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        # Reference close only ~0.5% below entry -- normal noise, not a chase.
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=1005.0))), \
             patch("crawler.zerodha_market.get_kite_historical",
                   AsyncMock(return_value=_candles(1000, 1001, 1000, 1002, 1003))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 950.0, "target_1": 1100.0, "target_2": 1200.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _execute_news_trade("TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"})
        assert result is True

    @pytest.mark.asyncio
    async def test_sell_not_blocked_by_a_rally_against_the_short(self):
        # A SELL is only chasing when price already dropped a lot -- a prior
        # RALLY (opposite direction) must not trip the gate.
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=1500.0))), \
             patch("crawler.zerodha_market.get_kite_historical",
                   AsyncMock(return_value=_candles(1450, 1460, 1470, 1480, 1490))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 1550.0, "target_1": 1400.0, "target_2": 1300.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _execute_news_trade("TESTCO.NS", "SELL", "headline", {"confidence": 80, "bull": "b"})
        assert result is True

    @pytest.mark.asyncio
    async def test_candle_fetch_failure_fails_open(self):
        # A data outage on the timing check must not silently halt ALL news
        # trading -- only the central execution gate is meant to fail-closed.
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=1500.0))), \
             patch("crawler.zerodha_market.get_kite_historical", AsyncMock(side_effect=RuntimeError("feed down"))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 1400.0, "target_1": 1600.0, "target_2": 1700.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _execute_news_trade("TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"})
        assert result is True

    @pytest.mark.asyncio
    async def test_no_candles_available_fails_open(self):
        async def _fake_execute(intent, session):
            return RoutingResult(outcome=RoutingOutcome.EXECUTED_PAPER, mode=TradeMode.PAPER, reason="ok")

        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=_make_snapshot(ltp=1500.0))), \
             patch("crawler.zerodha_market.get_kite_historical", AsyncMock(return_value=[])), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 1400.0, "target_1": 1600.0, "target_2": 1700.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()):
            result = await _execute_news_trade("TESTCO.NS", "BUY", "headline", {"confidence": 80, "bull": "b"})
        assert result is True
