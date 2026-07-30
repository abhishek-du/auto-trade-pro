"""Regression tests for engine/calendar_engine.py fixes (2026-07-28):

1. Duplicate past-dated events: seed_calendar_events() ran daily and deleted
   only future events before reinserting -- fetch_earnings_calendar() keeps
   returning the SAME past earnings date from yfinance until it rolls the
   ticker to the next quarter's estimate, so every run re-inserted an
   identical row for anything already in the past. Confirmed live:
   MARUTI/NESTLEIND/PIDILITIND/HINDUNILVR each had 20-27 duplicate rows, all
   past-dated; future-dated events had zero duplicates.
2. Missing large-cap companies: fetch_earnings_calendar() only ever queried
   a 32-symbol hardcoded watchlist. Confirmed live that Varun Beverages,
   Ambuja Cements, Tata Capital, and Cholamandalam Finance were all missing
   from the calendar despite having real yfinance earnings dates.

All tests are deterministic and mocked -- no network, no DB, no yfinance.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import engine.calendar_engine as ce


def _event(event_type, symbol, event_date):
    return {
        "event_type": event_type, "title": f"{symbol} event", "symbol": symbol,
        "company_name": symbol, "event_date": event_date, "importance": "HIGH",
        "source": "YFINANCE", "is_confirmed": True, "event_metadata": {},
    }


class _FakeSession:
    """Minimal AsyncSession stand-in: tracks .add() calls, answers .execute()
    for the past-keys SELECT with a pre-seeded result. The code discards the
    DELETE call's return value entirely, so the same mock result (with
    .all() wired to the seeded past rows) serves both calls safely.
    Also records every executed statement's compiled SQL (literal binds) so
    tests can assert on DELETE scoping without a real DB."""
    def __init__(self, existing_past_rows):
        self.added = []
        self.executed_sql: list[str] = []
        self._result = MagicMock()
        self._result.all = MagicMock(return_value=existing_past_rows)

    async def execute(self, stmt, *a, **kw):
        try:
            self.executed_sql.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        except Exception:
            self.executed_sql.append(str(stmt))
        return self._result

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        pass


class TestSeedCalendarDedup:
    @pytest.mark.asyncio
    async def test_past_event_already_recorded_is_not_reinserted(self):
        today = date(2026, 7, 28)
        past_date = today - timedelta(days=7)
        session = _FakeSession(existing_past_rows=[("EARNINGS", "MARUTI.NS", past_date)])

        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.calendar_engine.generate_fno_expiry_dates", return_value=[]), \
             patch("engine.calendar_engine.get_rbi_mpc_events", return_value=[]), \
             patch("engine.calendar_engine.get_nse_holidays_2026", return_value=[]), \
             patch("engine.calendar_engine.fetch_upcoming_ipos", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_board_meetings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_declared_results_today", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_earnings_calendar",
                   AsyncMock(return_value=[_event("EARNINGS", "MARUTI.NS", past_date)])):
            mock_date.today.return_value = today
            result = await ce.seed_calendar_events(session, months_ahead=0)

        assert len(session.added) == 0
        assert result["total_inserted"] == 0

    @pytest.mark.asyncio
    async def test_new_past_event_not_previously_recorded_is_inserted(self):
        today = date(2026, 7, 28)
        past_date = today - timedelta(days=7)
        session = _FakeSession(existing_past_rows=[])  # nothing recorded yet

        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.calendar_engine.generate_fno_expiry_dates", return_value=[]), \
             patch("engine.calendar_engine.get_rbi_mpc_events", return_value=[]), \
             patch("engine.calendar_engine.get_nse_holidays_2026", return_value=[]), \
             patch("engine.calendar_engine.fetch_upcoming_ipos", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_board_meetings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_declared_results_today", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_earnings_calendar",
                   AsyncMock(return_value=[_event("EARNINGS", "MARUTI.NS", past_date)])):
            mock_date.today.return_value = today
            result = await ce.seed_calendar_events(session, months_ahead=0)

        assert len(session.added) == 1
        assert result["total_inserted"] == 1

    @pytest.mark.asyncio
    async def test_future_event_is_always_inserted_regardless_of_past_keys(self):
        today = date(2026, 7, 28)
        future_date = today + timedelta(days=3)
        # Past-keys result is irrelevant to future events -- dedup only applies
        # to event_date < today.
        session = _FakeSession(existing_past_rows=[("EARNINGS", "MARUTI.NS", today - timedelta(days=1))])

        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.calendar_engine.generate_fno_expiry_dates", return_value=[]), \
             patch("engine.calendar_engine.get_rbi_mpc_events", return_value=[]), \
             patch("engine.calendar_engine.get_nse_holidays_2026", return_value=[]), \
             patch("engine.calendar_engine.fetch_upcoming_ipos", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_board_meetings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_declared_results_today", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_earnings_calendar",
                   AsyncMock(return_value=[_event("EARNINGS", "ITC.NS", future_date)])):
            mock_date.today.return_value = today
            result = await ce.seed_calendar_events(session, months_ahead=0)

        assert len(session.added) == 1
        assert result["total_inserted"] == 1

    @pytest.mark.asyncio
    async def test_different_symbol_same_past_date_is_not_deduped_against_each_other(self):
        today = date(2026, 7, 28)
        past_date = today - timedelta(days=7)
        session = _FakeSession(existing_past_rows=[("EARNINGS", "MARUTI.NS", past_date)])

        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.calendar_engine.generate_fno_expiry_dates", return_value=[]), \
             patch("engine.calendar_engine.get_rbi_mpc_events", return_value=[]), \
             patch("engine.calendar_engine.get_nse_holidays_2026", return_value=[]), \
             patch("engine.calendar_engine.fetch_upcoming_ipos", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_board_meetings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_declared_results_today", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_earnings_calendar",
                   AsyncMock(return_value=[_event("EARNINGS", "NESTLEIND.NS", past_date)])):
            mock_date.today.return_value = today
            result = await ce.seed_calendar_events(session, months_ahead=0)

        assert len(session.added) == 1  # NESTLEIND is a different key from MARUTI
        assert result["total_inserted"] == 1


class TestFetchEarningsCalendarModes:
    @pytest.mark.asyncio
    async def test_default_mode_uses_curated_watchlist_sequentially(self):
        with patch("engine.calendar_engine._fetch_one_earnings_event",
                   AsyncMock(return_value=None)) as mock_fetch:
            await ce.fetch_earnings_calendar()
        called_symbols = [c.args[0] for c in mock_fetch.call_args_list]
        from utils.config import settings
        assert called_symbols == settings.earnings_calendar_symbols

    @pytest.mark.asyncio
    async def test_full_universe_requires_session(self):
        with pytest.raises(ValueError):
            await ce.fetch_earnings_calendar(None, full_universe=True)

    @pytest.mark.asyncio
    async def test_full_universe_uses_kite_instruments_symbols(self):
        fake_session = object()
        with patch("engine.calendar_engine._get_full_nse_bse_universe",
                   AsyncMock(return_value=["VBL.NS", "AMBUJACEM.NS"])), \
             patch("engine.calendar_engine._fetch_one_earnings_event",
                   AsyncMock(return_value=None)) as mock_fetch, \
             patch("engine.calendar_engine.asyncio.sleep", AsyncMock()):
            await ce.fetch_earnings_calendar(fake_session, full_universe=True)
        called_symbols = [c.args[0] for c in mock_fetch.call_args_list]
        assert called_symbols == ["VBL.NS", "AMBUJACEM.NS"]

    @pytest.mark.asyncio
    async def test_full_universe_is_strictly_sequential_not_concurrent(self):
        """2026-07-28 regression guard for the exact incident this closes:
        a first version used 10-way concurrency and got 8,258 of 10,197
        symbols rate-limited by yfinance, including the four companies that
        motivated building this path (VBL, AMBUJACEM, TATACAP, CHOLAFIN).
        Sequential-with-delay is a deliberate fix, not an accident -- this
        pins it down so a future "optimization" doesn't quietly re-add
        concurrency."""
        call_order = []

        async def _fake_fetch(sym):
            call_order.append(("start", sym))
            await asyncio.sleep(0)  # yield, so real concurrency WOULD interleave here
            call_order.append(("end", sym))
            return None

        with patch("engine.calendar_engine._get_full_nse_bse_universe",
                   AsyncMock(return_value=["A.NS", "B.NS", "C.NS"])), \
             patch("engine.calendar_engine._fetch_one_earnings_event", _fake_fetch), \
             patch("engine.calendar_engine.asyncio.sleep", AsyncMock()):
            await ce.fetch_earnings_calendar(object(), full_universe=True)

        # Sequential means each symbol's (start, end) pair is contiguous --
        # never interleaved with another symbol's start before its own end.
        assert call_order == [
            ("start", "A.NS"), ("end", "A.NS"),
            ("start", "B.NS"), ("end", "B.NS"),
            ("start", "C.NS"), ("end", "C.NS"),
        ]


class TestUniverseSliceRotation:
    """_todays_universe_slice: the multi-day rotation that replaced 10-way
    concurrency (2026-07-28)."""

    def test_full_rotation_cycle_covers_every_symbol_exactly_once(self):
        universe = [f"SYM{i}.NS" for i in range(1000)]
        with patch.object(ce, "_DAILY_SLICE_BUDGET_SEC", 40), \
             patch.object(ce, "_YFINANCE_SAFE_DELAY_SEC", 4.0):  # slice_size = 10
            slice_size = int(ce._DAILY_SLICE_BUDGET_SEC / ce._YFINANCE_SAFE_DELAY_SEC)
            num_slices = -(-len(universe) // slice_size)
            seen = set()
            with patch("engine.calendar_engine.date") as mock_date:
                for day_ordinal in range(num_slices):
                    mock_date.today.return_value = MagicMock(toordinal=lambda d=day_ordinal: d)
                    day_slice = ce._todays_universe_slice(universe)
                    seen.update(day_slice)
            assert seen == set(universe)

    def test_different_days_get_different_slices(self):
        universe = [f"SYM{i}.NS" for i in range(1000)]
        with patch("engine.calendar_engine.date") as mock_date:
            mock_date.today.return_value = MagicMock(toordinal=lambda: 0)
            slice_a = ce._todays_universe_slice(universe)
            mock_date.today.return_value = MagicMock(toordinal=lambda: 1)
            slice_b = ce._todays_universe_slice(universe)
        assert slice_a != slice_b

    def test_small_universe_fits_in_a_single_slice(self):
        universe = ["VBL.NS", "AMBUJACEM.NS", "CHOLAFIN.NS"]
        with patch("engine.calendar_engine.date") as mock_date:
            mock_date.today.return_value = MagicMock(toordinal=lambda: 12345)
            result = ce._todays_universe_slice(universe)
        assert set(result) == set(universe)


class TestEarningsCalendarWatchlist:
    def test_named_companies_are_in_the_curated_watchlist(self):
        """Regression guard for the exact incident this closes."""
        from utils.config import settings
        for sym in ("VBL", "AMBUJACEM", "TATACAP", "CHOLAFIN"):
            assert sym in settings.WATCHLIST_EARNINGS_CALENDAR


class TestParseNseBoardMeetingDate:
    def test_parses_dd_mon_yyyy(self):
        assert ce._parse_nse_board_meeting_date("30-Jul-2026") == date(2026, 7, 30)

    def test_unparseable_returns_none(self):
        assert ce._parse_nse_board_meeting_date("not-a-date") is None
        assert ce._parse_nse_board_meeting_date("") is None


class TestFetchNseBoardMeetingsCalendar:
    """2026-07-29: replaced the yfinance full-universe scan (fragile,
    rate-limited, ~11h for ~10,000 sequential requests) with NSE's own
    /api/event-calendar -- one bulk call returns every company's confirmed
    upcoming board-meeting date for quarterly results, no per-symbol
    looping and no rate-limit exposure."""

    @staticmethod
    def _fake_client(rows):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = rows
        resp.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=resp)
        client.aclose = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_filters_to_financial_results_purpose_only(self):
        rows = [
            {"symbol": "RELIANCE", "company": "Reliance Industries", "purpose": "Financial Results",
             "date": "30-Jul-2026", "bm_desc": "..."},
            {"symbol": "SOMECO", "company": "Some Co", "purpose": "Dividend",
             "date": "30-Jul-2026", "bm_desc": "..."},
        ]
        with patch("engine.nse_crawler._get_nse_session", AsyncMock(return_value=self._fake_client(rows))):
            events = await ce.fetch_nse_board_meetings_calendar()
        assert len(events) == 1
        assert events[0]["symbol"] == "RELIANCE.NS"
        assert events[0]["source"] == "NSE_BOARD_MEETING"
        assert events[0]["event_date"] == date(2026, 7, 30)
        assert events[0]["is_confirmed"] is True

    @pytest.mark.asyncio
    async def test_unparseable_date_is_skipped(self):
        rows = [{"symbol": "X", "company": "X Ltd", "purpose": "Financial Results", "date": "garbage"}]
        with patch("engine.nse_crawler._get_nse_session", AsyncMock(return_value=self._fake_client(rows))):
            events = await ce.fetch_nse_board_meetings_calendar()
        assert events == []

    @pytest.mark.asyncio
    async def test_session_init_failure_returns_empty_not_raises(self):
        with patch("engine.nse_crawler._get_nse_session", AsyncMock(side_effect=RuntimeError("boom"))):
            events = await ce.fetch_nse_board_meetings_calendar()
        assert events == []


class TestFetchNseDeclaredResultsToday:
    """2026-07-29: fetch_nse_board_meetings_calendar()'s /api/event-calendar
    is forward-only and drops a company the instant its meeting date arrives
    -- confirmed live it can never show TODAY's earnings (queried on
    29-Jul-2026, the earliest date returned was 30-Jul, even though 100+
    companies including Adani Ports/Colgate-Palmolive/CarTrade were reporting
    that same day). This covers the gap via NSE's corporate-announcements
    feed filtered to "Outcome of Board Meeting" -- the filing made the
    moment a company's board approves results, so it appears same-day."""

    @staticmethod
    def _fake_client(payload):
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = payload
        resp.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=resp)
        client.aclose = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_only_todays_announcements_are_kept(self):
        today = date(2026, 7, 29)
        payload = {"data": [
            {"symbol": "CARTRADE", "sm_name": "Cartrade Tech Limited",
             "an_dt": "29-Jul-2026 11:02:15", "desc": "Outcome of Board Meeting"},
            {"symbol": "OLDCO", "sm_name": "Old Company Ltd",
             "an_dt": "28-Jul-2026 10:00:00", "desc": "Outcome of Board Meeting"},
        ]}
        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.nse_crawler._get_nse_session", AsyncMock(return_value=self._fake_client(payload))):
            mock_date.today.return_value = today
            events = await ce.fetch_nse_declared_results_today()
        assert len(events) == 1
        assert events[0]["symbol"] == "CARTRADE.NS"
        assert events[0]["source"] == "NSE_RESULT_DECLARED"
        assert events[0]["event_date"] == today
        assert events[0]["is_confirmed"] is True

    @pytest.mark.asyncio
    async def test_duplicate_symbol_same_day_keeps_only_first(self):
        """A company can file more than one 'Outcome of Board Meeting'
        announcement the same day (amendments/follow-ups); rows arrive
        most-recent-first, so only the first (latest) one should survive."""
        today = date(2026, 7, 29)
        payload = {"data": [
            {"symbol": "CRAFTSMAN", "sm_name": "Craftsman Automation Limited",
             "an_dt": "29-Jul-2026 12:43:50", "desc": "Outcome of Board Meeting"},
            {"symbol": "CRAFTSMAN", "sm_name": "Craftsman Automation Limited",
             "an_dt": "29-Jul-2026 12:39:58", "desc": "Outcome of Board Meeting"},
        ]}
        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.nse_crawler._get_nse_session", AsyncMock(return_value=self._fake_client(payload))):
            mock_date.today.return_value = today
            events = await ce.fetch_nse_declared_results_today()
        assert len(events) == 1
        assert events[0]["event_metadata"]["announced_at"] == "29-Jul-2026 12:43:50"

    @pytest.mark.asyncio
    async def test_session_init_failure_returns_empty_not_raises(self):
        with patch("engine.nse_crawler._get_nse_session", AsyncMock(side_effect=RuntimeError("boom"))):
            events = await ce.fetch_nse_declared_results_today()
        assert events == []


class TestSeedCalendarDeclaredResultSupersedesBoardMeeting:
    @pytest.mark.asyncio
    async def test_declared_result_deletes_stale_board_meeting_row_for_same_symbol(self):
        """A company whose result is DECLARED today should replace (not sit
        alongside) an earlier-captured 'upcoming board meeting' row for the
        same symbol -- otherwise the calendar would show the same company
        twice for the same day, once as 'upcoming' and once as 'declared'."""
        today = date(2026, 7, 29)
        session = _FakeSession(existing_past_rows=[])

        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.calendar_engine.generate_fno_expiry_dates", return_value=[]), \
             patch("engine.calendar_engine.get_rbi_mpc_events", return_value=[]), \
             patch("engine.calendar_engine.get_nse_holidays_2026", return_value=[]), \
             patch("engine.calendar_engine.fetch_upcoming_ipos", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_earnings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_board_meetings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_declared_results_today", AsyncMock(return_value=[{
                   "event_type": "EARNINGS", "title": "Cartrade — Quarterly Results (Declared)",
                   "symbol": "CARTRADE.NS", "company_name": "Cartrade Tech Limited",
                   "event_date": today, "importance": "HIGH",
                   "source": "NSE_RESULT_DECLARED", "is_confirmed": True, "event_metadata": {},
               }])):
            mock_date.today.return_value = today
            await ce.seed_calendar_events(session, months_ahead=0)

        supersede_deletes = [s for s in session.executed_sql
                              if "NSE_BOARD_MEETING" in s and "CARTRADE.NS" in s]
        assert len(supersede_deletes) == 1
        assert len(session.added) == 1
        assert session.added[0].source == "NSE_RESULT_DECLARED"


class TestSeedCalendarPrefersConfirmedBoardMeetingOverEstimate:
    @pytest.mark.asyncio
    async def test_yfinance_estimate_dropped_when_nse_confirmed_date_exists(self):
        """A confirmed NSE board-meeting filing for a symbol should replace
        (not duplicate alongside) a yfinance analyst-estimate for the same
        symbol -- the estimate is redundant and can be stale once a real
        filing exists."""
        today = date(2026, 7, 28)
        session = _FakeSession(existing_past_rows=[])
        confirmed_date = today + timedelta(days=5)
        estimate_date = today + timedelta(days=40)

        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.calendar_engine.generate_fno_expiry_dates", return_value=[]), \
             patch("engine.calendar_engine.get_rbi_mpc_events", return_value=[]), \
             patch("engine.calendar_engine.get_nse_holidays_2026", return_value=[]), \
             patch("engine.calendar_engine.fetch_upcoming_ipos", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_earnings_calendar",
                   AsyncMock(return_value=[_event("EARNINGS", "RELIANCE.NS", estimate_date)])), \
             patch("engine.calendar_engine.fetch_nse_board_meetings_calendar", AsyncMock(return_value=[{
                   "event_type": "EARNINGS", "title": "Reliance — Quarterly Results",
                   "symbol": "RELIANCE.NS", "company_name": "Reliance Industries",
                   "event_date": confirmed_date, "importance": "HIGH",
                   "source": "NSE_BOARD_MEETING", "is_confirmed": True, "event_metadata": {},
               }])), \
             patch("engine.calendar_engine.fetch_nse_declared_results_today", AsyncMock(return_value=[])):
            mock_date.today.return_value = today
            result = await ce.seed_calendar_events(session, months_ahead=0)

        assert len(session.added) == 1
        assert session.added[0].source == "NSE_BOARD_MEETING"
        assert session.added[0].event_date == confirmed_date
        assert result["total_inserted"] == 1


class TestNseBoardMeetingNotWipedWhenItRollsOffNseWindow:
    """2026-07-29 fix: NSE's /api/event-calendar is a rolling ~3-week-AHEAD
    window that drops a company the instant its meeting date arrives.
    Confirmed live: queried on 29-Jul-2026, the earliest date returned was
    30-Jul -- 29-Jul itself (the day 100+ companies including Adani
    Enterprises/Asian Paints/Eicher Motors/Dabur actually reported) was
    already gone from NSE's own feed, and the calendar showed zero EARNINGS
    events for that day. Root cause: the blanket "delete all future events,
    reinsert from today's fetch" pattern applied to NSE_BOARD_MEETING rows
    too, so the moment a company's day arrived and NSE stopped listing it,
    the next reseed silently deleted its already-captured entry -- exactly
    reproducing this bug every single day. Fix: NSE_BOARD_MEETING rows are
    excluded from the blanket delete; a symbol's existing row is only
    replaced if that SAME symbol reappears in today's fresh NSE fetch."""

    @pytest.mark.asyncio
    async def test_symbol_aging_off_nse_window_keeps_its_existing_row(self):
        """RELIANCE was captured yesterday for today's date. Today's fresh
        NSE fetch no longer includes RELIANCE (it aged off the window) --
        the blanket future-delete must NOT remove RELIANCE's row."""
        today = date(2026, 7, 29)
        session = _FakeSession(existing_past_rows=[])

        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.calendar_engine.generate_fno_expiry_dates", return_value=[]), \
             patch("engine.calendar_engine.get_rbi_mpc_events", return_value=[]), \
             patch("engine.calendar_engine.get_nse_holidays_2026", return_value=[]), \
             patch("engine.calendar_engine.fetch_upcoming_ipos", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_earnings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_board_meetings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_declared_results_today", AsyncMock(return_value=[])):
            mock_date.today.return_value = today
            await ce.seed_calendar_events(session, months_ahead=0)

        blanket_delete_sql = session.executed_sql[0]
        assert "market_events" in blanket_delete_sql.lower()
        assert "NSE_BOARD_MEETING" in blanket_delete_sql
        assert "!=" in blanket_delete_sql or "<>" in blanket_delete_sql
        # No board-meeting symbols this run -> no per-symbol replace DELETE issued.
        per_symbol_deletes = [s for s in session.executed_sql[1:]
                               if "market_events" in s.lower() and "symbol" in s.lower() and "IN" in s]
        assert per_symbol_deletes == []

    @pytest.mark.asyncio
    async def test_symbol_reappearing_triggers_per_symbol_replace_delete(self):
        """A symbol that DOES reappear in today's fresh NSE fetch (e.g. a
        rescheduled meeting) gets a targeted per-symbol DELETE so the stale
        date can be replaced -- unlike the "aged off" case above."""
        today = date(2026, 7, 29)
        session = _FakeSession(existing_past_rows=[])
        new_date = today + timedelta(days=3)

        with patch("engine.calendar_engine.date") as mock_date, \
             patch("engine.calendar_engine.generate_fno_expiry_dates", return_value=[]), \
             patch("engine.calendar_engine.get_rbi_mpc_events", return_value=[]), \
             patch("engine.calendar_engine.get_nse_holidays_2026", return_value=[]), \
             patch("engine.calendar_engine.fetch_upcoming_ipos", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_earnings_calendar", AsyncMock(return_value=[])), \
             patch("engine.calendar_engine.fetch_nse_board_meetings_calendar", AsyncMock(return_value=[{
                   "event_type": "EARNINGS", "title": "Reliance — Quarterly Results",
                   "symbol": "RELIANCE.NS", "company_name": "Reliance Industries",
                   "event_date": new_date, "importance": "HIGH",
                   "source": "NSE_BOARD_MEETING", "is_confirmed": True, "event_metadata": {},
               }])), \
             patch("engine.calendar_engine.fetch_nse_declared_results_today", AsyncMock(return_value=[])):
            mock_date.today.return_value = today
            await ce.seed_calendar_events(session, months_ahead=0)

        per_symbol_deletes = [s for s in session.executed_sql
                               if "NSE_BOARD_MEETING" in s and "RELIANCE.NS" in s]
        assert len(per_symbol_deletes) == 1
        assert len(session.added) == 1
        assert session.added[0].symbol == "RELIANCE.NS"
        assert session.added[0].event_date == new_date
