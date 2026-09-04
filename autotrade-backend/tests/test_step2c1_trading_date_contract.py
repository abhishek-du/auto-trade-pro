"""STEP 2C.1 — trading-date contract regression tests.

These PIN MEASURED BEHAVIOUR. They do not assert what the code ought to do where
it currently does something else — a test that fails on arrival is a bug report,
not a regression guard. Where behaviour is wrong, the test says so explicitly and
locks the wrongness in place so a later fix is a deliberate, visible change.

Three date concepts are distinguished throughout:

    calendar_date         a wall-clock date with no market meaning
    candle_timestamp      the stored instant (naive UTC)
    trading_session_date  the NSE session the bar belongs to
"""
from __future__ import annotations

import datetime as dt

import pytest

from crawler.upstox_candles import _to_naive_utc
from engine.agent.performance_engine import _trading_date

# NSE holidays confirmed against Upstox /v2/market/holidays on 2026-09-04.
NSE_HOLIDAYS_2026 = {"2026-08-26", "2026-09-14"}


def legacy_session_date(ts: dt.datetime) -> dt.date:
    """Session date for a bar stored under the LEGACY 18:30 UTC convention.

    18:30 UTC + 05:30 = 00:00 IST of the NEXT day, so the session is stored_date
    + 1, then rolled forward over weekends and holidays.
    """
    d = ts.date() + dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in NSE_HOLIDAYS_2026:
        d += dt.timedelta(days=1)
    return d


class TestCanonicalDailyMapping:
    """The FIXED writer: Upstox daily label -> 03:45 UTC -> correct session."""

    @pytest.mark.parametrize("raw,session", [
        ("2026-08-28T00:00:00+05:30", dt.date(2026, 8, 28)),
        ("2026-08-31T00:00:00+05:30", dt.date(2026, 8, 31)),
        ("2026-09-01T00:00:00+05:30", dt.date(2026, 9, 1)),
        ("2026-09-02T00:00:00+05:30", dt.date(2026, 9, 2)),
        ("2026-09-03T00:00:00+05:30", dt.date(2026, 9, 3)),
    ])
    def test_label_maps_to_session_at_session_open(self, raw, session):
        ts = _to_naive_utc(raw, daily=True)
        assert ts.date() == session
        assert ts.time() == dt.time(3, 45)

    def test_no_accidental_weekend_rollback(self):
        """A Monday session must stay Monday. Under the legacy convention a
        Monday bar was stored on Sunday, and a naive weekend-rollback would
        push it to the previous Friday — losing a whole session."""
        ts = _to_naive_utc("2026-08-31T00:00:00+05:30", daily=True)   # Monday
        assert ts.weekday() == 0
        assert ts.date() == dt.date(2026, 8, 31)
        assert ts.date() != dt.date(2026, 8, 28), "must NOT roll back to Friday"

    def test_point_in_time_ordering(self):
        """feature session D  <  prediction session D+1."""
        feat = _to_naive_utc("2026-09-02T00:00:00+05:30", daily=True).date()
        pred = _to_naive_utc("2026-09-03T00:00:00+05:30", daily=True).date()
        assert feat < pred and (pred - feat).days == 1


class TestTradingDateMeasuredBehaviour:
    """performance_engine._trading_date() — MEASURED, not modified.

    Measured 2026-09-04: it is wrong for EVERY input tested (8/8), by -1 to -4
    days. It only shifts Sat/Sun back to Friday and has no holiday awareness and
    no knowledge of the +1 offset the legacy 18:30 convention carries.

    These tests lock that behaviour in so that changing it is deliberate.
    """

    @pytest.mark.parametrize("stored,expected_output,correct_session,off_by", [
        (dt.datetime(2026, 8, 27, 18, 30), dt.date(2026, 8, 27), dt.date(2026, 8, 28), -1),
        (dt.datetime(2026, 8, 28, 18, 30), dt.date(2026, 8, 28), dt.date(2026, 8, 31), -3),
        (dt.datetime(2026, 8, 29, 18, 30), dt.date(2026, 8, 28), dt.date(2026, 8, 31), -3),
        (dt.datetime(2026, 8, 30, 18, 30), dt.date(2026, 8, 28), dt.date(2026, 8, 31), -3),
        (dt.datetime(2026, 8, 31, 18, 30), dt.date(2026, 8, 31), dt.date(2026, 9, 1),  -1),
        (dt.datetime(2026, 9, 1, 18, 30),  dt.date(2026, 9, 1),  dt.date(2026, 9, 2),  -1),
    ])
    def test_current_behaviour_is_pinned(self, stored, expected_output, correct_session, off_by):
        got = _trading_date(stored)
        assert got == expected_output, "measured behaviour changed — was this intended?"
        assert got != correct_session, "if this now passes, the bug was fixed: update this test"
        assert (got - correct_session).days == off_by

    def test_it_has_no_holiday_awareness(self):
        """26-Aug-2026 is Id-E-Milad (NSE closed). A bar stored 25-Aug 18:30
        belongs to the next OPEN session, 27-Aug — not 25-Aug."""
        stored = dt.datetime(2026, 8, 25, 18, 30)
        assert _trading_date(stored) == dt.date(2026, 8, 25)
        assert legacy_session_date(stored) == dt.date(2026, 8, 27)

    def test_weekend_rollback_direction_is_backwards(self):
        """The core defect: a Sunday-stored bar is MONDAY's session (+1),
        but the function snaps it to the preceding Friday (-2)."""
        sunday = dt.datetime(2026, 8, 30, 18, 30)
        assert _trading_date(sunday) == dt.date(2026, 8, 28)      # Friday
        assert legacy_session_date(sunday) == dt.date(2026, 8, 31)  # Monday
        assert (_trading_date(sunday) - legacy_session_date(sunday)).days == -3


class TestNonDailyUnaffected:
    """The 2C fix must not have touched intraday semantics."""

    @pytest.mark.parametrize("raw,expected", [
        ("2026-09-01T09:15:00+05:30", dt.datetime(2026, 9, 1, 3, 45)),
        ("2026-09-01T15:29:00+05:30", dt.datetime(2026, 9, 1, 9, 59)),
    ])
    def test_intraday_is_a_plain_offset_shift(self, raw, expected):
        assert _to_naive_utc(raw, daily=False) == expected

    def test_intraday_session_date_needs_no_correction(self):
        """1m bars run 03:45-09:59 UTC, entirely inside one UTC date, so
        timestamp::date is already the session date for intraday."""
        for raw in ("2026-09-01T09:15:00+05:30", "2026-09-01T15:29:00+05:30"):
            assert _to_naive_utc(raw, daily=False).date() == dt.date(2026, 9, 1)
