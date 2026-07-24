"""Tests for the Phase 5.5 evidence funnel (diagnostic instrumentation).

The funnel reuses the real pipeline; these tests verify it classifies the FIRST
failing stage correctly and diagnoses coverage-limited (B) vs strategy-limited
(A) from the numbers. Fully mocked.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import engine.pre_event_expectation_gap.evidence_funnel as funnel
from engine.pre_event_expectation_gap.evidence_funnel import (
    funnel_for_prediction, _diagnose, FUNNEL_STAGES,
)
from engine.pre_event_expectation_gap.types import ScheduledEvent, PreEventType


def _event():
    return ScheduledEvent(symbol="MARUTI.NS", event_type=PreEventType.QUARTERLY_RESULT,
                          event_date=date(2026, 7, 24), event_confidence=0.9)


class TestFunnelStageClassification:
    @pytest.mark.asyncio
    async def test_no_candles_excluded_at_candle_stage(self):
        snap = SimpleNamespace(self_candles=AsyncMock(return_value=[]))
        with patch.object(funnel, "build_snapshot", return_value=snap):
            rec = await funnel_for_prediction("MARUTI.NS", _event(), datetime(2026, 6, 24), AsyncMock())
        assert rec.exclusion_reason == "no_historical_candles"
        assert rec.reached["candles_available"] is False

    @pytest.mark.asyncio
    async def test_unresolved_sector_excluded(self):
        snap = SimpleNamespace(self_candles=AsyncMock(return_value=[SimpleNamespace(close=100)]))
        with patch.object(funnel, "build_snapshot", return_value=snap), \
             patch.object(funnel, "resolve_strategy_sector", return_value=None):
            rec = await funnel_for_prediction("X.NS", _event(), datetime(2026, 6, 24), AsyncMock())
        assert rec.exclusion_reason == "sector_unresolved"

    @pytest.mark.asyncio
    async def test_no_adapter_excluded(self):
        snap = SimpleNamespace(self_candles=AsyncMock(return_value=[SimpleNamespace(close=100)]))
        with patch.object(funnel, "build_snapshot", return_value=snap), \
             patch.object(funnel, "resolve_strategy_sector", return_value="ZINC"), \
             patch.object(funnel, "get_adapter", return_value=None):
            rec = await funnel_for_prediction("X.NS", _event(), datetime(2026, 6, 24), AsyncMock())
        assert rec.exclusion_reason.startswith("no_adapter_for_sector")


class TestDiagnosis:
    def test_coverage_limited_when_few_reach_nowcast(self):
        stage_counts = {"nowcast_available": 50, "reached_decision": 0}
        reasons = Counter({"no_adapter_for_sector": 400})
        d = _diagnose(stage_counts, reasons, total=572)
        assert d.startswith("COVERAGE-LIMITED (B)")

    def test_strategy_limited_when_many_reach_decision(self):
        stage_counts = {"nowcast_available": 500, "reached_decision": 300}
        reasons = Counter({"decision": 300})
        d = _diagnose(stage_counts, reasons, total=572)
        assert d.startswith("STRATEGY-LIMITED (A)")

    def test_empty(self):
        assert "nothing to diagnose" in _diagnose({}, Counter(), total=0)


def test_funnel_stages_are_ordered_and_complete():
    # sanity: the documented stages match what the record populates
    assert FUNNEL_STAGES[0] == "total"
    assert FUNNEL_STAGES[-1] == "reached_decision"
    assert "expectation_anchor_available" in FUNNEL_STAGES
