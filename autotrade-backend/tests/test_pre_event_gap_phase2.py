"""Phase 2 tests: event discovery + sector adapter architecture + AUTO adapter.

Fully mocked (no network/DB). Emphasis on the two things that must be right:
(1) the fail-closed / NOWCAST_UNAVAILABLE contract is honored on every path,
and (2) the AUTO adapter is point-in-time-safe — it never uses the quarter
being reported (look-ahead).
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.pre_event_expectation_gap.types import (
    ScheduledEvent, PreEventType, NowcastStatus, Direction, NowcastResult,
)
from engine.pre_event_expectation_gap.discovery import (
    discover_scheduled_events, _looks_like_results_meeting,
)
from engine.pre_event_expectation_gap.sector_adapters import (
    run_nowcast, get_adapter, registered_sectors, resolve_strategy_sector,
)
from engine.pre_event_expectation_gap.sector_adapters import base as adapters_base
from engine.pre_event_expectation_gap.sector_adapters.auto import (
    AutoNowcastAdapter, _period_end_date, _available_series, _direction,
)


def _q(period, value):
    return {"period": period, "value": value}


def _income(rev, profit):
    return {"income_statement": [
        {"category": "revenue", "history": rev},
        {"category": "net_profit", "history": profit},
    ]}


def _event(symbol="MARUTI.NS", d=date(2026, 10, 25)):
    return ScheduledEvent(symbol=symbol, event_type=PreEventType.QUARTERLY_RESULT, event_date=d)


# ── Registry / resolution ────────────────────────────────────────────────────

class TestRegistryAndResolution:
    def test_auto_is_registered(self):
        assert "AUTO" in registered_sectors()
        assert get_adapter("AUTO") is not None

    def test_get_adapter_case_insensitive(self):
        assert get_adapter("auto") is not None

    def test_unknown_sector_has_no_adapter(self):
        assert get_adapter("QUANTUM_WIDGETS") is None
        assert get_adapter(None) is None

    def test_known_auto_symbol_resolves(self):
        assert resolve_strategy_sector("MARUTI.NS") == "AUTO"

    def test_unknown_symbol_resolves_to_none(self):
        assert resolve_strategy_sector("TOTALLYUNKNOWN.NS") is None


# ── Point-in-time helpers (look-ahead safety) ────────────────────────────────

class TestPointInTime:
    def test_period_parsing(self):
        assert _period_end_date("Jun 2026") == date(2026, 6, 30)
        assert _period_end_date("Mar 2025") == date(2025, 3, 31)
        assert _period_end_date("Dec 2024") == date(2024, 12, 31)

    def test_unparseable_period_is_none(self):
        assert _period_end_date("garbage") is None
        assert _period_end_date(None) is None

    def test_pending_quarter_excluded_from_series(self):
        # as_of 2026-07-10: the 'Jun 2026' quarter (ended 06-30) is only ~10
        # days old — its results are NOT public yet, so it MUST be excluded
        # (using it to predict the Jun-2026 result would be look-ahead).
        hist = [_q("Jun 2026", 130), _q("Mar 2026", 120), _q("Dec 2025", 115)]
        series = _available_series(hist, date(2026, 7, 10))
        periods = [d for d, _ in series]
        assert date(2026, 6, 30) not in periods
        assert date(2026, 3, 31) in periods

    def test_older_quarter_included_once_lag_passed(self):
        # Same 'Jun 2026' quarter, but as_of is 2026-09-01 — now >40 days past
        # quarter-end, so its results would be public and it's usable.
        hist = [_q("Jun 2026", 130), _q("Mar 2026", 120)]
        series = _available_series(hist, date(2026, 9, 1))
        assert date(2026, 6, 30) in [d for d, _ in series]

    def test_series_returned_oldest_first(self):
        hist = [_q("Jun 2025", 100), _q("Mar 2025", 95), _q("Dec 2024", 90)]
        series = _available_series(hist, date(2026, 1, 1))
        dates = [d for d, _ in series]
        assert dates == sorted(dates)


class TestDirectionClassification:
    def test_positive_negative_neutral(self):
        assert _direction(0.10) == Direction.POSITIVE
        assert _direction(-0.10) == Direction.NEGATIVE
        assert _direction(0.0) == Direction.NEUTRAL
        assert _direction(None) == Direction.NEUTRAL


# ── AUTO adapter behavior ────────────────────────────────────────────────────

class TestAutoAdapter:
    @pytest.mark.asyncio
    async def test_accelerating_growth_is_positive_ok(self):
        adapter = AutoNowcastAdapter()
        income = _income(
            rev=[_q("Jun 2026", 130), _q("Mar 2026", 120), _q("Dec 2025", 115),
                 _q("Sep 2025", 110), _q("Jun 2025", 100)],
            profit=[_q("Jun 2026", 20), _q("Mar 2026", 16), _q("Dec 2025", 14),
                    _q("Sep 2025", 12), _q("Jun 2025", 10)],
        )
        with patch("crawler.upstox_data.get_income_statement", AsyncMock(return_value=income)):
            r = await adapter.nowcast("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert r.status == NowcastStatus.OK
        assert r.revenue_direction == Direction.POSITIVE
        assert r.profit_direction == Direction.POSITIVE

    @pytest.mark.asyncio
    async def test_declining_profit_is_negative(self):
        adapter = AutoNowcastAdapter()
        income = _income(
            rev=[_q("Jun 2026", 100), _q("Mar 2026", 105), _q("Dec 2025", 108),
                 _q("Sep 2025", 110), _q("Jun 2025", 115)],
            profit=[_q("Jun 2026", 6), _q("Mar 2026", 8), _q("Dec 2025", 10),
                    _q("Sep 2025", 12), _q("Jun 2025", 14)],
        )
        with patch("crawler.upstox_data.get_income_statement", AsyncMock(return_value=income)):
            r = await adapter.nowcast("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert r.status == NowcastStatus.OK
        assert r.profit_direction == Direction.NEGATIVE

    @pytest.mark.asyncio
    async def test_insufficient_history_is_unavailable(self):
        adapter = AutoNowcastAdapter()
        income = _income(rev=[_q("Jun 2025", 100)], profit=[_q("Jun 2025", 10)])
        with patch("crawler.upstox_data.get_income_statement", AsyncMock(return_value=income)):
            r = await adapter.nowcast("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert r.status == NowcastStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_completeness_is_honestly_low(self):
        # The AUTO adapter must NOT claim high data completeness when the real
        # operational inputs (monthly volumes, EV mix, ASP…) are missing.
        adapter = AutoNowcastAdapter()
        income = _income(
            rev=[_q("Jun 2026", 130), _q("Mar 2026", 120), _q("Dec 2025", 115), _q("Sep 2025", 110)],
            profit=[_q("Jun 2026", 20), _q("Mar 2026", 16), _q("Dec 2025", 14), _q("Sep 2025", 12)],
        )
        with patch("crawler.upstox_data.get_income_statement", AsyncMock(return_value=income)):
            r = await adapter.nowcast("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert r.data_completeness <= 0.4
        assert r.confidence <= 0.5

    @pytest.mark.asyncio
    async def test_fetch_failure_is_unavailable_not_raise(self):
        adapter = AutoNowcastAdapter()
        with patch("crawler.upstox_data.get_income_statement", AsyncMock(side_effect=Exception("upstox down"))):
            r = await adapter.nowcast("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert r.status == NowcastStatus.UNAVAILABLE


# ── Fail-closed dispatcher ───────────────────────────────────────────────────

class TestRunNowcastFailClosed:
    @pytest.mark.asyncio
    async def test_unresolved_sector_is_unavailable(self):
        r = await run_nowcast("TOTALLYUNKNOWN.NS", _event("TOTALLYUNKNOWN.NS"), datetime(2026, 10, 1), AsyncMock())
        assert r.status == NowcastStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_adapter_exception_fails_closed(self):
        boom = MagicMock()
        boom.nowcast = AsyncMock(side_effect=Exception("kaboom"))
        with patch.object(adapters_base, "resolve_strategy_sector", return_value="AUTO"), \
             patch.object(adapters_base, "get_adapter", return_value=boom):
            r = await run_nowcast("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert r.status == NowcastStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_adapter_returning_none_fails_closed(self):
        noneish = MagicMock()
        noneish.nowcast = AsyncMock(return_value=None)
        with patch.object(adapters_base, "resolve_strategy_sector", return_value="AUTO"), \
             patch.object(adapters_base, "get_adapter", return_value=noneish):
            r = await run_nowcast("MARUTI.NS", _event(), datetime(2026, 10, 1), AsyncMock())
        assert r.status == NowcastStatus.UNAVAILABLE


# ── Event discovery ──────────────────────────────────────────────────────────

class TestDiscovery:
    def test_results_meeting_classification(self):
        assert _looks_like_results_meeting("To consider and approve Q1 Financial Results")
        assert _looks_like_results_meeting("Unaudited results for the quarter ended June 2026")
        assert not _looks_like_results_meeting("To consider a proposal for fund raising")

    @pytest.mark.asyncio
    async def test_discovers_from_market_events(self):
        rows = [
            SimpleNamespace(symbol="MARUTI.NS", event_date=date.today(), time_ist="15:00",
                            is_confirmed=True, event_type="EARNINGS"),
        ]
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(return_value=rows)), \
             patch("engine.nse_crawler.fetch_board_meetings_for_symbols", AsyncMock(return_value={})):
            out = await discover_scheduled_events(
                AsyncMock(), universe=["MARUTI.NS"], min_days_until=-5, max_days_until=5,
            )
        assert any(e.symbol == "MARUTI.NS" and e.event_type == PreEventType.QUARTERLY_RESULT for e in out)

    @pytest.mark.asyncio
    async def test_board_meeting_results_discovered_and_universe_filtered(self):
        by_symbol = {
            "MARUTI.NS": [{"symbol": "MARUTI.NS", "meeting_date": date.today(),
                           "purpose": "To approve unaudited financial results"}],
            "OFFUNIVERSE.NS": [{"symbol": "OFFUNIVERSE.NS", "meeting_date": date.today(),
                                "purpose": "financial results"}],
        }
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(return_value=[])), \
             patch("engine.nse_crawler.fetch_board_meetings_for_symbols", AsyncMock(return_value=by_symbol)):
            out = await discover_scheduled_events(
                AsyncMock(), universe=["MARUTI.NS"], min_days_until=-2, max_days_until=2,
            )
        syms = {e.symbol for e in out}
        assert "MARUTI.NS" in syms
        # OFFUNIVERSE isn't in the requested universe -> board fetch was scoped to it,
        # but even if returned, discovery only keeps universe symbols.
        assert "OFFUNIVERSE.NS" not in syms

    @pytest.mark.asyncio
    async def test_discovery_fail_soft_on_source_error(self):
        # A raising discoverer source must not crash discovery.
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(side_effect=Exception("db down"))), \
             patch("engine.nse_crawler.fetch_board_meetings_for_symbols", AsyncMock(return_value={})):
            out = await discover_scheduled_events(AsyncMock(), universe=["MARUTI.NS"])
        assert out == []
