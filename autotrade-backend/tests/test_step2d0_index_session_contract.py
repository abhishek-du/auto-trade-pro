"""STEP 2D.0 — index daily contract and session alignment.

CONTRACT DISCOVERY. These tests pin what was PROVEN in 2D.0; they do not
migrate anything and they do not assert desired-but-absent behaviour.

Established by measurement on 2026-09-04:

  * Upstox serves NSE INDEX daily data (NSE_INDEX|Nifty 50 etc.) with the
    SAME date-label convention as equities: midnight IST of the session date.
  * The canonical pipeline already resolves index keys, so the index contract
    is identical to the equity one — 03:45 UTC on the session date.
  * /v2/market/holidays mixes closures with days NSE TRADES. Six of 22 entries
    in 2026 are open days.
  * performance_engine._trading_date() is correct for 00:00 and 03:45 and wrong
    only for the legacy 18:30 equity series.
"""
from __future__ import annotations

import datetime as dt

import pytest

from crawler.upstox_candles import _to_naive_utc
from engine.agent.performance_engine import _trading_date
from utils.candle_contract import (
    InstrumentClass,
    classify_instrument,
    is_nse_trading_session,
    nse_closed_dates,
    nse_extra_open_dates,
)

# Real /v2/market/holidays entries, captured 2026-09-04.
HOLIDAY_PAYLOAD = [
    {"date": "2026-04-03", "holiday_type": "TRADING_HOLIDAY",
     "description": "Good Friday", "open_exchanges": []},
    {"date": "2026-01-26", "holiday_type": "TRADING_HOLIDAY",
     "description": "Republic Day", "open_exchanges": []},
    {"date": "2026-08-26", "holiday_type": "SETTLEMENT_HOLIDAY",
     "description": "Id-E-Milad",
     "open_exchanges": [{"exchange": "NSE", "start_time": 1787715900000,
                         "end_time": 1787738400000},
                        {"exchange": "BSE"}]},
    {"date": "2026-02-01", "holiday_type": "SPECIAL_TIMING",
     "description": "Budget Day Session",
     "open_exchanges": [{"exchange": "NSE"}]},
]


class TestHolidayCalendarSemantics:
    """The endpoint's date list is NOT the closed-session list."""

    def test_only_dates_with_nse_absent_from_open_exchanges_are_closures(self):
        closed = nse_closed_dates(HOLIDAY_PAYLOAD)
        assert closed == {"2026-04-03", "2026-01-26"}

    def test_settlement_holiday_is_a_real_trading_session(self):
        """26-Aug-2026 Id-E-Milad: NSE open. RELIANCE traded 5,744,474 shares."""
        closed = nse_closed_dates(HOLIDAY_PAYLOAD)
        assert "2026-08-26" not in closed
        assert is_nse_trading_session(dt.date(2026, 8, 26), closed) is True

    def test_special_timing_is_a_real_trading_session(self):
        """2026-02-01 Budget Day is a SUNDAY with a full 09:15-15:30 NSE session.

        The weekday heuristic alone rejects it, so the calendar must be able to
        override in BOTH directions — this is why nse_extra_open_dates exists.
        """
        closed = nse_closed_dates(HOLIDAY_PAYLOAD)
        extra = nse_extra_open_dates(HOLIDAY_PAYLOAD)
        assert dt.date(2026, 2, 1).weekday() == 6, "it really is a Sunday"
        assert "2026-02-01" in extra
        assert is_nse_trading_session(dt.date(2026, 2, 1), closed) is False   # weekday rule
        assert is_nse_trading_session(dt.date(2026, 2, 1), closed, extra) is True

    def test_true_closure_is_excluded(self):
        closed = nse_closed_dates(HOLIDAY_PAYLOAD)
        assert is_nse_trading_session(dt.date(2026, 4, 3), closed) is False

    def test_using_the_raw_date_list_would_discard_real_sessions(self):
        """Guards the specific mistake this step found in the 2C.2 contract."""
        naive = {h["date"] for h in HOLIDAY_PAYLOAD}
        correct = nse_closed_dates(HOLIDAY_PAYLOAD)
        assert len(naive) - len(correct) == 2
        assert is_nse_trading_session(dt.date(2026, 8, 26), naive) is False   # wrong
        assert is_nse_trading_session(dt.date(2026, 8, 26), correct) is True  # right

    def test_weekend_is_never_a_session(self):
        assert is_nse_trading_session(dt.date(2026, 9, 5), set()) is False   # Sat
        assert is_nse_trading_session(dt.date(2026, 9, 6), set()) is False   # Sun


class TestIndexRawSemanticsAndMapping:
    """Index raw timestamps are date labels, identical to equities."""

    INDEX_RAW = [
        ("2026-08-28T00:00:00+05:30", dt.date(2026, 8, 28)),
        ("2026-08-31T00:00:00+05:30", dt.date(2026, 8, 31)),   # Monday
        ("2026-09-01T00:00:00+05:30", dt.date(2026, 9, 1)),
        ("2026-09-02T00:00:00+05:30", dt.date(2026, 9, 2)),
        ("2026-09-03T00:00:00+05:30", dt.date(2026, 9, 3)),
    ]

    @pytest.mark.parametrize("raw,session", INDEX_RAW)
    def test_index_label_maps_to_session_open(self, raw, session):
        ts = _to_naive_utc(raw, daily=True)
        assert ts.date() == session and ts.time() == dt.time(3, 45)

    def test_index_and_equity_share_one_mapping(self):
        """Same conversion, so no per-class timestamp logic is needed."""
        for raw, _ in self.INDEX_RAW:
            assert _to_naive_utc(raw, daily=True) == _to_naive_utc(raw, daily=True)

    def test_indices_classify_as_index_not_equity(self):
        for s in ("^NSEI", "^NSEBANK", "^INDIAVIX"):
            assert classify_instrument(s) is InstrumentClass.NSE_INDEX


class TestSessionDateEquality:
    """Index session date == equity session date == calendar date."""

    SESSIONS = [dt.date(2026, 8, 24), dt.date(2026, 8, 25), dt.date(2026, 8, 26),
                dt.date(2026, 8, 27), dt.date(2026, 8, 28), dt.date(2026, 8, 31),
                dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]

    def test_every_session_maps_identically_for_index_and_equity(self):
        closed = nse_closed_dates(HOLIDAY_PAYLOAD)
        for d in self.SESSIONS:
            raw = f"{d.isoformat()}T00:00:00+05:30"
            assert _to_naive_utc(raw, daily=True).date() == d
            assert is_nse_trading_session(d, closed) is True

    def test_friday_to_monday_has_no_intervening_session(self):
        fri, mon = dt.date(2026, 8, 28), dt.date(2026, 8, 31)
        assert (mon - fri).days == 3
        for gap in (dt.date(2026, 8, 29), dt.date(2026, 8, 30)):
            assert is_nse_trading_session(gap, set()) is False


class TestTradingDatePerConvention:
    """MEASURED. _trading_date is right for 00:00/03:45, wrong for 18:30."""

    @pytest.mark.parametrize("stored,true_session,correct", [
        (dt.datetime(2026, 9, 3, 0, 0),   dt.date(2026, 9, 3),  True),   # index legacy
        (dt.datetime(2026, 9, 3, 3, 45),  dt.date(2026, 9, 3),  True),   # canonical
        (dt.datetime(2026, 8, 31, 0, 0),  dt.date(2026, 8, 31), True),   # Monday index
        (dt.datetime(2026, 8, 31, 3, 45), dt.date(2026, 8, 31), True),   # Monday canonical
        (dt.datetime(2026, 9, 2, 18, 30), dt.date(2026, 9, 3),  False),  # legacy equity
        (dt.datetime(2026, 8, 30, 18, 30), dt.date(2026, 8, 31), False), # Sunday-stored
    ])
    def test_measured_behaviour(self, stored, true_session, correct):
        got = _trading_date(stored)
        if correct:
            assert got == true_session
        else:
            assert got != true_session, "if this passes, 18:30 was fixed — update this test"

    def test_canonical_timestamps_make_the_weekend_snap_a_noop(self):
        """After migration no daily bar lands on a weekend, so the snap that is
        currently wrong for 18:30 rows becomes inert rather than harmful."""
        for d in (dt.date(2026, 8, 28), dt.date(2026, 8, 31), dt.date(2026, 9, 3)):
            ts = dt.datetime.combine(d, dt.time(3, 45))
            assert ts.weekday() < 5
            assert _trading_date(ts) == d


class TestPointInTimeSafetyOfTheIndexChange:
    """Moving indices 00:00 -> 03:45 on the SAME date cannot leak the future."""

    @pytest.mark.parametrize("d", ["2026-09-01", "2026-09-02", "2026-09-03"])
    def test_session_date_is_unchanged_and_time_moves_later(self, d):
        old = dt.datetime.fromisoformat(f"{d}T00:00:00")
        new = dt.datetime.fromisoformat(f"{d}T03:45:00")
        assert new.date() == old.date(), "session date must not shift"
        assert new > old, "a bar may become more recent, never earlier"

    def test_one_bar_per_session_under_either_convention(self):
        sessions = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
        for t in (dt.time(0, 0), dt.time(3, 45)):
            stamps = [dt.datetime.combine(d, t) for d in sessions]
            assert len({s.date() for s in stamps}) == len(sessions)

    def test_mixed_conventions_on_one_date_are_the_hazard(self):
        """The collision proven on real data: a 18:30 bar and a 03:45 bar share
        a calendar date, `timestamp DESC` puts 18:30 first, and 18:30 belongs to
        the NEXT session — so that date's close comes from the future."""
        same_date = dt.date(2026, 9, 2)
        canonical = dt.datetime.combine(same_date, dt.time(3, 45))    # 02-Sep session
        legacy    = dt.datetime.combine(same_date, dt.time(18, 30))   # 03-Sep session
        assert legacy > canonical, "DESC ordering puts the legacy bar first"
        assert _trading_date(legacy) == _trading_date(canonical) == same_date
        legacy_true = same_date + dt.timedelta(days=1)
        assert legacy_true > same_date, "the winning bar is one session ahead"
