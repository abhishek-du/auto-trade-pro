"""STEP 2C — daily-candle session-date contract.

THE CONTRACT
------------
`candles.timestamp` is the instant the bar's interval OPENS, in naive UTC.
For a DAILY NSE equity bar that instant is the session open, 09:15 IST, i.e.
03:45 UTC **on the session date**. Therefore:

    candles.timestamp::date  ==  NSE trading-session date

THE BUG THIS GUARDS
-------------------
Upstox stamps a daily bar at MIDNIGHT IST of the session date
('2026-09-01T00:00:00+05:30'). That is a DATE LABEL, not an instant — no trading
happens at 00:00 IST. Converting it as an instant yields 2026-08-31 18:30 UTC,
whose .date() is the day BEFORE the session it describes.

The failure is quiet because it is usually invisible: only a MONDAY session
lands on a weekend date. On the other four weekdays the wrong date is still a
plausible trading day, so a point-in-time join silently reads the previous
session and looks entirely correct. Hence the Monday cases below.
"""
from __future__ import annotations

import datetime as dt

import pytest

from crawler.upstox_candles import _INTERVAL_MAP, _to_naive_utc

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Real Upstox daily payloads captured 2026-09-04, with the session each
# describes confirmed against Upstox's own /v2/market/holidays calendar.
DAILY_SAMPLES = [
    # (raw upstox timestamp,          session date,  weekday)
    ("2026-08-28T00:00:00+05:30", dt.date(2026, 8, 28), "Fri"),
    ("2026-08-31T00:00:00+05:30", dt.date(2026, 8, 31), "Mon"),   # the Monday case
    ("2026-09-01T00:00:00+05:30", dt.date(2026, 9, 1),  "Tue"),
    ("2026-09-02T00:00:00+05:30", dt.date(2026, 9, 2),  "Wed"),
    ("2026-09-03T00:00:00+05:30", dt.date(2026, 9, 3),  "Thu"),
]


def session_date(ts: dt.datetime) -> dt.date:
    """Canonical session date from a stored daily timestamp.

    Under the contract this is simply `.date()`. It exists as a named function
    so research code has ONE place to call and this test can pin its behaviour.
    """
    return ts.date()


class TestDailyTimestampContract:

    @pytest.mark.parametrize("raw,expected,weekday", DAILY_SAMPLES)
    def test_daily_label_maps_to_its_own_session_date(self, raw, expected, weekday):
        ts = _to_naive_utc(raw, daily=True)
        assert ts.date() == expected, (
            f"{raw} must map to session {expected} ({weekday}), got {ts.date()}"
        )
        assert ts.time() == dt.time(3, 45), "daily bars anchor to 09:15 IST session open"
        assert ts.tzinfo is None, "candles table is naive UTC"

    def test_no_silent_one_day_shift(self):
        """The regression itself: the old conversion produced the previous day."""
        raw = "2026-09-01T00:00:00+05:30"
        buggy = dt.datetime.fromisoformat(raw).astimezone(dt.timezone.utc).replace(tzinfo=None)
        assert buggy.date() == dt.date(2026, 8, 31), "captures the OLD behaviour"
        fixed = _to_naive_utc(raw, daily=True)
        assert fixed.date() == dt.date(2026, 9, 1)
        assert fixed.date() != buggy.date(), "the one-day shift must be gone"

    def test_monday_session_never_lands_on_a_weekend(self):
        """The only case the old bug made *visible*. 31-Aug-2026 is a Monday."""
        ts = _to_naive_utc("2026-08-31T00:00:00+05:30", daily=True)
        assert ts.weekday() == 0, "must be Monday"
        assert ts.weekday() < 5, "a session date can never be Sat/Sun"

    def test_every_sample_session_is_a_weekday(self):
        for raw, expected, _ in DAILY_SAMPLES:
            assert _to_naive_utc(raw, daily=True).weekday() < 5

    def test_daily_bar_is_not_written_into_either_legacy_series(self):
        """00:00 UTC is the dead pre-split series; 18:30 UTC is the offset one.
        A correctly normalised daily bar must land in neither."""
        for raw, _, _ in DAILY_SAMPLES:
            t = _to_naive_utc(raw, daily=True).time()
            assert t != dt.time(0, 0), "would join the dead pre-split series"
            assert t != dt.time(18, 30), "would join the one-day-offset series"


class TestNonDailyIsolation:
    """The fix must not alter 1m / 5m / 15m / 1h semantics."""

    @pytest.mark.parametrize("raw,expected", [
        ("2026-09-01T09:15:00+05:30", dt.datetime(2026, 9, 1, 3, 45)),   # session open
        ("2026-09-01T09:16:00+05:30", dt.datetime(2026, 9, 1, 3, 46)),
        ("2026-09-01T15:29:00+05:30", dt.datetime(2026, 9, 1, 9, 59)),   # last 1m bar
        ("2026-09-01T15:30:00+05:30", dt.datetime(2026, 9, 1, 10, 0)),
    ])
    def test_intraday_conversion_is_a_plain_offset_shift(self, raw, expected):
        assert _to_naive_utc(raw, daily=False) == expected

    def test_intraday_is_unchanged_by_the_daily_branch_existing(self):
        """daily=False must behave exactly as the function did before the fix."""
        for raw in ("2026-09-01T09:15:00+05:30", "2026-09-01T12:00:00+05:30"):
            legacy = dt.datetime.fromisoformat(raw).astimezone(
                dt.timezone.utc).replace(tzinfo=None)
            assert _to_naive_utc(raw, daily=False) == legacy

    def test_only_the_days_unit_triggers_daily_handling(self):
        """The call site keys off `unit == 'days'`; confirm the map agrees."""
        assert _INTERVAL_MAP["1d"][0] == "days" and _INTERVAL_MAP["day"][0] == "days"
        for intraday in ("1m", "5m", "15m", "30m", "1h", "minute", "15minute", "60minute"):
            assert _INTERVAL_MAP[intraday][0] != "days", intraday


class TestPointInTimeJoin:
    """§9 — a feature date must never silently resolve to the previous session."""

    SESSIONS = [dt.date(2026, 8, 31), dt.date(2026, 9, 1),
                dt.date(2026, 9, 2),  dt.date(2026, 9, 3)]

    def test_feature_date_equals_the_actual_session(self):
        for raw, expected, _ in DAILY_SAMPLES:
            assert session_date(_to_naive_utc(raw, daily=True)) == expected

    def test_label_date_is_strictly_after_feature_date(self):
        """Next-session research shape: features from T, label from T+1.

        Under the OLD behaviour every feature date was one session early, so a
        'next session' label silently became the SAME session — lookahead
        leakage that a date-alignment check would not catch, because the dates
        still differ by one row.
        """
        bars = {session_date(_to_naive_utc(r, daily=True)): r for r, _, _ in DAILY_SAMPLES}
        ordered = sorted(bars)
        for feat, label in zip(ordered, ordered[1:]):
            assert label > feat, "label session must follow the feature session"
            assert (label - feat).days >= 1

    def test_the_old_mapping_would_have_leaked(self):
        """Demonstrates the leak concretely, so the guard cannot be argued away."""
        raw_feature = "2026-09-02T00:00:00+05:30"        # session 02-Sep
        old = dt.datetime.fromisoformat(raw_feature).astimezone(
            dt.timezone.utc).replace(tzinfo=None).date()
        assert old == dt.date(2026, 9, 1)
        # A pipeline asking for "features as of 01-Sep" would receive 02-Sep's
        # completed bar — a full session of future information.
        assert old < dt.date(2026, 9, 2)

    def test_no_session_maps_into_the_future(self):
        for raw, expected, _ in DAILY_SAMPLES:
            assert _to_naive_utc(raw, daily=True).date() <= dt.date(2026, 9, 4)
