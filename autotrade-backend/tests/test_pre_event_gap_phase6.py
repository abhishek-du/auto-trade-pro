"""Phase 6 tests for Pre-Event Expectation Gap Strategy live production wiring.

Covers:
1. Central execution gate gating checks (master enable, paper mode gate, live mode gate).
2. Explicit trade attribution in TradingSignal and PaperTrade (source="AI Predict").
3. Live task loop orchestration (_pre_event_gap_scan_loop and run_pre_event_gap_scan)
   ensuring isolation from News Strategy and accurate TradeIntent construction.
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from engine.pre_event_expectation_gap import (
    STRATEGY_ID, TRADE_SOURCE,
    PreEventDecision, PreEventType, NowcastStatus, PriceDiscountStatus,
    ScheduledEvent, NowcastResult, ExpectationEstimate, PriceDiscount,
    RelativeStrength, PreEventPrediction,
)
from engine.decision_router import (
    TradeIntent, TradeMode, RoutingOutcome, ConfidenceSource, EventDirectness,
    StrategyFamily, authorize_trade_intent, _intent_to_signal,
)
from utils.config import settings


def _make_pre_event_intent(**overrides) -> TradeIntent:
    defaults = dict(
        strategy=STRATEGY_ID,
        symbol="TVSMOTOR.NS",
        action="BUY",
        instrument_type="EQUITY",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=80.0,
        confidence_source=ConfidenceSource.CALCULATED,
        strategy_family=StrategyFamily.PRE_EVENT,
        event_directness=EventDirectness.NOT_APPLICABLE,
        evidence_ids=[],
        event_id=None,
        extra={"source": TRADE_SOURCE},
    )
    defaults.update(overrides)
    return TradeIntent(**defaults)


def _make_session() -> AsyncMock:
    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=exec_result)
    return session


@pytest.mark.asyncio
async def test_gate_blocks_when_master_switch_disabled():
    intent = _make_pre_event_intent()
    session = _make_session()
    with patch.object(settings, "PRE_EVENT_GAP_ENABLED", False), \
         patch("engine.decision_router.resolve_mode", AsyncMock(return_value=TradeMode.PAPER)), \
         patch("engine.decision_router._log_intent_audit", AsyncMock()):
        res = await authorize_trade_intent(intent, session)
        assert res.approved is False
        assert res.outcome == RoutingOutcome.BLOCKED_DISABLED
        assert "PRE_EVENT_GAP_ENABLED=False" in res.reason


@pytest.mark.asyncio
async def test_gate_blocks_when_paper_trading_disabled():
    intent = _make_pre_event_intent()
    session = _make_session()
    with patch.object(settings, "PRE_EVENT_GAP_ENABLED", True), \
         patch.object(settings, "PRE_EVENT_GAP_PAPER_TRADING", False), \
         patch("engine.decision_router.resolve_mode", AsyncMock(return_value=TradeMode.PAPER)), \
         patch("engine.decision_router._log_intent_audit", AsyncMock()):
        res = await authorize_trade_intent(intent, session)
        assert res.approved is False
        assert res.outcome == RoutingOutcome.BLOCKED_GATE
        assert "PRE_EVENT_GAP_PAPER_TRADING=False" in res.reason


@pytest.mark.asyncio
async def test_gate_blocks_when_live_trading_disabled():
    intent = _make_pre_event_intent()
    session = _make_session()
    with patch.object(settings, "PRE_EVENT_GAP_ENABLED", True), \
         patch.object(settings, "PRE_EVENT_GAP_LIVE_TRADING", False), \
         patch("engine.decision_router.resolve_mode", AsyncMock(return_value=TradeMode.LIVE)), \
         patch("engine.decision_router._log_intent_audit", AsyncMock()):
        res = await authorize_trade_intent(intent, session)
        assert res.approved is False
        assert res.outcome == RoutingOutcome.BLOCKED_GATE
        assert "PRE_EVENT_GAP_LIVE_TRADING=False" in res.reason


def test_intent_to_signal_preserves_strategy_and_source():
    intent = _make_pre_event_intent()
    sig = _intent_to_signal(intent)
    assert sig.strategy == STRATEGY_ID
    assert sig.source == TRADE_SOURCE


def test_intent_to_signal_default_source():
    intent = _make_pre_event_intent(extra={})
    sig = _intent_to_signal(intent)
    assert sig.strategy == STRATEGY_ID
    assert sig.source == "AI Predict"


@pytest.mark.asyncio
async def test_pre_event_gap_scan_loop_skips_outside_window():
    from tasks.india_tasks import _pre_event_gap_scan_loop
    with patch.object(settings, "PRE_EVENT_GAP_ENABLED", False):
        # Should exit immediately without throwing or querying
        await _pre_event_gap_scan_loop(min_days_until=1, max_days_until=15)


@pytest.mark.asyncio
async def test_pre_event_gap_scan_loop_executes_long():
    from tasks.india_tasks import _pre_event_gap_scan_loop
    from crawler.market_snapshot import MarketSnapshot

    pred = PreEventPrediction(
        symbol="TVSMOTOR.NS",
        event=ScheduledEvent("TVSMOTOR.NS", PreEventType.QUARTERLY_RESULT, date(2026, 8, 15)),
        prediction_cutoff=datetime(2026, 7, 27, 10, 0),
        decision=PreEventDecision.LONG,
        nowcast=NowcastResult(status=NowcastStatus.OK, confidence=0.8),
        expectation=ExpectationEstimate(our_expected_pat_growth=0.15, anchor_type="CONSENSUS", anchor_value=0.10, gap_available=True),
        price_discount=PriceDiscount(status=PriceDiscountStatus.NOT_DISCOUNTED),
        relative_strength=RelativeStrength(vs_nifty=1.05),
        pre_event_score=85.0,
        data_quality_score=0.9,
        score_breakdown={},
        decision_reason="Strong bullish setup",
    )

    mock_session = _make_session()
    mock_celery_session = MagicMock()
    mock_celery_session.return_value.__aenter__.return_value = mock_session

    mock_snap = MarketSnapshot(
        symbol="TVSMOTOR.NS", ltp=1500.0, source="yfinance", fetched_at=1.0, fetched_at_ist="10:00:00"
    )

    mock_exec = AsyncMock(return_value=MagicMock(outcome=RoutingOutcome.EXECUTED_PAPER))
    mock_levels = AsyncMock(return_value={"stop_loss": 1425.0, "take_profit": 1650.0, "target_2": 1725.0, "atr": 25.0})

    with patch.object(settings, "PRE_EVENT_GAP_ENABLED", True), \
         patch("tasks.india_tasks._is_india_trading_window", return_value=True), \
         patch("tasks._db.celery_session", mock_celery_session), \
         patch("crawler.live_snapshot.fetch_live_snapshot", AsyncMock()), \
         patch("engine.pre_event_expectation_gap.scan", AsyncMock(return_value=[pred])), \
         patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=mock_snap)), \
         patch("tasks.india_tasks._compute_pre_event_trade_levels", mock_levels), \
         patch("engine.decision_router.execute_trade_intent", mock_exec):
        await _pre_event_gap_scan_loop(1, 15)

    assert mock_exec.called
    intent = mock_exec.call_args[0][0]
    assert intent.strategy == STRATEGY_ID
    assert intent.symbol == "TVSMOTOR.NS"
    assert intent.action == "BUY"
    assert intent.confidence == 85.0
    assert intent.strategy_family == StrategyFamily.PRE_EVENT
    assert intent.extra["source"] == TRADE_SOURCE
