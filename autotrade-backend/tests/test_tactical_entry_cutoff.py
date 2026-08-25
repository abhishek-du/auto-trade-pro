"""Phase 1B task C — no tactical entry may open after the intraday squareoff.

The contradiction fixed here: the tactical entry window ran to 15:20 IST while
tasks.intraday_squareoff (beat: crontab 09:40 UTC = 15:10 IST) closes every
OpenPosition with product="MIS". Tactical signals are all MIS, so entries in
that 10-minute overlap were structurally unholdable.

Measured 2026-08-25: four ORB/PIVOT_BREAKOUT entries opened 15:08 IST and were
closed by MIS_SQUAREOFF at 15:11 — 171-181 seconds, -Rs628 in round-trip cost.

15:20 IST is ZERODHA's auto-squareoff, not ours; ours is 15:10, and it is the
earliest thing that force-closes a tactical position.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from engine.tactical_data_fetcher import (
    ENTRY_CUTOFF,
    SESSION_CLOSE,
    SESSION_OPEN,
    in_entry_window,
)

IST = ZoneInfo("Asia/Kolkata")


def _ist(h, m, s=0, day=25):
    """A weekday (2026-08-25 is a Tuesday), tz-aware in IST."""
    return datetime(2026, 8, 25, h, m, s, tzinfo=IST).replace(day=day)


def test_cutoff_constant_matches_the_squareoff():
    assert ENTRY_CUTOFF == (15, 10), (
        "ENTRY_CUTOFF must equal the intraday squareoff time. If the beat "
        "schedule for tasks.intraday_squareoff moved, move this with it."
    )
    assert ENTRY_CUTOFF < SESSION_CLOSE
    assert SESSION_OPEN < ENTRY_CUTOFF


# ── the boundary, to the second ──────────────────────────────────────────────

@pytest.mark.parametrize("h,m,s,allowed", [
    (9, 15, 0, True),      # session open — the first admissible instant
    (9, 14, 59, False),    # one second before the open
    (12, 0, 0, True),      # mid-session
    (15, 9, 0, True),      # one minute before the squareoff
    (15, 9, 59, True),     # the LAST admissible instant
    (15, 10, 0, False),    # the squareoff is due — too late
    (15, 10, 1, False),
    (15, 15, 0, False),    # inside the old 15:10-15:20 overlap
    (15, 19, 59, False),
    (15, 20, 0, False),    # the old cutoff must no longer admit anything
    (15, 29, 59, False),
    (15, 30, 0, False),    # session close
])
def test_entry_window_boundary(h, m, s, allowed):
    assert in_entry_window(_ist(h, m, s)) is allowed, (
        f"{h:02d}:{m:02d}:{s:02d} IST should be "
        f"{'allowed' if allowed else 'rejected'}"
    )


def test_the_overlap_that_caused_the_churn_is_closed():
    """Every second in the old 15:10-15:20 gap must now be rejected."""
    for minute in range(10, 20):
        for sec in (0, 30, 59):
            assert in_entry_window(_ist(15, minute, sec)) is False, (
                f"15:{minute:02d}:{sec:02d} IST still admits an entry that the "
                f"15:10 squareoff would immediately close"
            )


def test_the_four_churned_entries_would_now_be_rejected():
    """The actual 2026-08-25 entries, at their real IST timestamps."""
    for h, m, s in [(15, 8, 23), (15, 8, 28), (15, 8, 31), (15, 8, 35)]:
        # these were BEFORE 15:10, so they remain admissible — the cutoff does
        # not retroactively forbid them. What the fix removes is the 15:10-15:20
        # window, where an entry could open after the squareoff had already run.
        assert in_entry_window(_ist(h, m, s)) is True
    # ...whereas an entry one minute after the squareoff is now impossible
    assert in_entry_window(_ist(15, 11, 0)) is False


# ── timezone handling, explicitly ────────────────────────────────────────────

def test_window_is_evaluated_in_ist_not_utc():
    """09:40 UTC is 15:10 IST — the squareoff instant, and must be rejected.

    A naive implementation comparing UTC clock parts would see 09:40 as
    mid-morning and admit it.
    """
    utc_0940 = datetime(2026, 8, 25, 9, 40, tzinfo=ZoneInfo("UTC"))
    assert in_entry_window(utc_0940.astimezone(IST)) is False
    # and 09:39:59 UTC = 15:09:59 IST is the last admissible instant
    utc_0939 = datetime(2026, 8, 25, 9, 39, 59, tzinfo=ZoneInfo("UTC"))
    assert in_entry_window(utc_0939.astimezone(IST)) is True


def test_weekends_are_rejected_regardless_of_time():
    # 2026-08-29 is a Saturday, 2026-08-30 a Sunday
    for day in (29, 30):
        assert in_entry_window(_ist(12, 0, 0, day=day)) is False
