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
from news_discovery_engine import _execute_news_trade


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
