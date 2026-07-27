"""Phase 5.5 v0.2 tests: expanded sector adapters + anchor semantics.

Verifies the coverage-expansion work honors every constraint: adapters declare
required/available/missing inputs, carry sector-appropriate confidence ceilings
(commodity/FDA-driven sectors lowest), never fabricate drivers, and the
historical-baseline anchor is correctly labelled (NOT a market expectation) and
point-in-time-gated.
"""
from __future__ import annotations

from datetime import datetime, date

import pytest

from engine.pre_event_expectation_gap.sector_adapters import registered_sectors, get_adapter
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter


ALL_SECTORS = ("FMCG", "IT", "AUTO", "METALS", "PHARMA")


class TestAdapterCoverage:
    def test_all_five_registered(self):
        for s in ALL_SECTORS:
            assert s in registered_sectors()
            assert get_adapter(s) is not None

    @pytest.mark.parametrize("sector", ALL_SECTORS)
    def test_each_adapter_declares_inputs(self, sector):
        a = get_adapter(sector)
        assert a.REQUIRED_INPUTS, f"{sector} must declare required inputs"
        assert a.AVAILABLE_INPUTS == ("quarterly_financials",), \
            f"{sector} should honestly declare only quarterly_financials is available"
        # every adapter is honest that most real drivers are MISSING
        assert len(a.missing_inputs) >= 1
        assert "quarterly_financials" in a.REQUIRED_INPUTS

    @pytest.mark.parametrize("sector", ALL_SECTORS)
    def test_each_adapter_has_rationale(self, sector):
        assert get_adapter(sector).economic_rationale.strip()

    def test_confidence_ceilings_reflect_predictability(self):
        # Commodity/FDA-event-driven sectors (trailing financials are a poor
        # predictor) must have the LOWEST ceilings; IT/AUTO (more predictable
        # from financials) higher. This encodes economic rationale, not tuning.
        ceils = {s: get_adapter(s).confidence_ceiling for s in ALL_SECTORS}
        assert ceils["METALS"] < ceils["FMCG"]
        assert ceils["PHARMA"] < ceils["IT"]
        assert ceils["METALS"] <= ceils["PHARMA"]
        assert all(0.0 < c <= 0.5 for c in ceils.values())

    def test_it_treats_qoq_as_meaningful_auto_does_not(self):
        # Economic rationale: IT is low-seasonality (QoQ meaningful); auto is
        # highly seasonal (QoQ misleading → penalized).
        assert get_adapter("IT").qoq_is_meaningful is True
        assert get_adapter("AUTO").qoq_is_meaningful is False


class TestAdapterNowcastHonesty:
    @pytest.mark.asyncio
    async def test_insufficient_quarters_is_unavailable(self):
        from unittest.mock import AsyncMock, patch
        from engine.pre_event_expectation_gap.types import NowcastStatus, ScheduledEvent, PreEventType
        ev = ScheduledEvent(symbol="INFY.NS", event_type=PreEventType.QUARTERLY_RESULT, event_date=date(2026, 10, 25))
        income = {"income_statement": [{"category": "net_profit", "history": [{"period": "Jun 2025", "value": 10}]}]}
        with patch("crawler.upstox_data.get_income_statement", AsyncMock(return_value=income)):
            r = await get_adapter("IT").nowcast("INFY.NS", ev, datetime(2026, 10, 1), AsyncMock())
        assert r.status == NowcastStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_confidence_never_exceeds_ceiling(self):
        from unittest.mock import AsyncMock, patch
        from engine.pre_event_expectation_gap.types import ScheduledEvent, PreEventType
        # plenty of history, strong trend → confidence should still be capped by the ceiling
        def q(p, v): return {"period": p, "value": v}
        hist = [q("Jun 2026", 30), q("Mar 2026", 24), q("Dec 2025", 20), q("Sep 2025", 16),
                q("Jun 2025", 12), q("Mar 2025", 10)]
        income = {"income_statement": [{"category": "net_profit", "history": hist},
                                       {"category": "revenue", "history": hist}]}
        ev = ScheduledEvent(symbol="TATASTEEL.NS", event_type=PreEventType.QUARTERLY_RESULT, event_date=date(2026, 10, 25))
        adapter = get_adapter("METALS")
        with patch("crawler.upstox_data.get_income_statement", AsyncMock(return_value=income)):
            r = await adapter.nowcast("TATASTEEL.NS", ev, datetime(2026, 10, 1), AsyncMock())
        assert r.confidence <= adapter.confidence_ceiling


class TestAnchorSemantics:
    @pytest.mark.asyncio
    async def test_historical_baseline_is_not_market_expectation(self):
        from unittest.mock import AsyncMock, patch
        from engine.pre_event_expectation_gap.types import NowcastResult, NowcastStatus
        from engine.pre_event_expectation_gap.expectation import compute_expectation
        from engine.pre_event_expectation_gap.point_in_time import build_snapshot
        from engine.pre_event_expectation_gap.financials import HistoricalBaseline

        nc = NowcastResult(status=NowcastStatus.OK, implied_profit_growth=0.30, implied_is_annual=True)
        snap = build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())
        with patch("engine.pre_event_expectation_gap.expectation.get_historical_baseline_3y_cagr",
                   AsyncMock(return_value=HistoricalBaseline(value=0.20, known_at=datetime(2026, 9, 1)))):
            exp = await compute_expectation(nc, "MARUTI.NS", snap)
        assert exp.anchor_type == "HISTORICAL_BASELINE_3Y_CAGR"
        assert exp.is_market_expectation is False
        assert exp.anchor_type not in ("CONSENSUS", "MARKET_EXPECTATION", "ANALYST_EXPECTATION")
        assert exp.anchor_known_at is not None

    @pytest.mark.asyncio
    async def test_future_known_at_is_rejected_point_in_time(self):
        from unittest.mock import AsyncMock, patch
        from engine.pre_event_expectation_gap.types import NowcastResult, NowcastStatus
        from engine.pre_event_expectation_gap.expectation import compute_expectation
        from engine.pre_event_expectation_gap.point_in_time import build_snapshot
        from engine.pre_event_expectation_gap.financials import HistoricalBaseline

        nc = NowcastResult(status=NowcastStatus.OK, implied_profit_growth=0.30, implied_is_annual=True)
        snap = build_snapshot("MARUTI.NS", datetime(2026, 5, 1), AsyncMock())   # cutoff in the past
        with patch("engine.pre_event_expectation_gap.expectation.get_historical_baseline_3y_cagr",
                   AsyncMock(return_value=HistoricalBaseline(value=0.20, known_at=datetime(2026, 7, 1)))):  # known later
            exp = await compute_expectation(nc, "MARUTI.NS", snap)
        assert exp.gap_available is False
        assert exp.anchor_type is None
