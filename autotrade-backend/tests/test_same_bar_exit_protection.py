"""A profit-management exit may not fire against its own entry bar (F4).

MEASURED: nine trades closed 1-9 seconds after entry for a combined -Rs1,776.
Eight were EXHAUSTION; one was a genuine STOP_LOSS. detect_exhaustion reads the
5m frame and drops the forming bar, so an entry and an "exhaustion reversal"
priced seconds apart were reading THE SAME COMPLETED BAR. The reversal did not
happen; the arithmetic just had nothing new to look at.

    BHEL.NS       EXHAUSTION  5s  -95      COSMOFIRST.NS EXHAUSTION 4s -529
    BHEL.NS       EXHAUSTION  5s -161      AUTOBEES.NS   EXHAUSTION 5s -150
    SMLMAH.NS     EXHAUSTION  1s -226      GODREJCP.NS   EXHAUSTION 8s -153
    ETERNAL.NS    EXHAUSTION  1s -158      TVSMOTOR.NS   EXHAUSTION 3s -136
    CANBK.NS      STOP_LOSS   9s -168   <- a REAL stop; must still fire

This guard is deliberately MODE-INDEPENDENT. V2's 120-minute gate happens to
cover it today, but a rollback to CONTROL must not reopen the hole.
"""
from __future__ import annotations

import datetime as dt

import pytest

from engine import exit_policy as ep

# 09:20:00 IST == 03:50:00 UTC, aligned to a 5m boundary.
ENTRY = dt.datetime(2026, 8, 27, 3, 50, 0)


@pytest.fixture
def bars1(monkeypatch):
    monkeypatch.setattr(ep, "min_completed_bars", lambda: 1)
    monkeypatch.setattr(ep, "_bar_minutes", lambda: 5)


@pytest.fixture
def control(monkeypatch, bars1):
    monkeypatch.setattr(ep, "is_v2", lambda: False)
    monkeypatch.setattr(ep, "strategy_mode", lambda: ep.MODE_CONTROL)


class TestTheNineTrades:
    """Each measured same-bar exit, replayed."""

    @pytest.mark.parametrize("secs", [1, 3, 4, 5, 8])
    def test_exhaustion_within_seconds_is_blocked(self, control, secs):
        allowed, family, note = ep.exit_allowed(
            "EXHAUSTION", ENTRY, ENTRY + dt.timedelta(seconds=secs))
        assert not allowed
        assert family == ep.ExitFamily.PROFIT_MANAGEMENT
        assert "same-bar" in note

    def test_the_real_stop_loss_at_9s_still_fires(self, control):
        """CANBK.NS. Suppressing this would be the dangerous failure."""
        allowed, family, _ = ep.exit_allowed(
            "STOP_LOSS", ENTRY, ENTRY + dt.timedelta(seconds=9))
        assert allowed
        assert family == ep.ExitFamily.HARD_STOP


class TestBarBoundaries:
    def test_blocked_anywhere_inside_the_entry_bar(self, control):
        for secs in (0, 60, 299):
            assert not ep.exit_allowed(
                "EXHAUSTION", ENTRY, ENTRY + dt.timedelta(seconds=secs))[0]

    def test_allowed_once_the_entry_bar_completes(self, control):
        assert ep.exit_allowed(
            "EXHAUSTION", ENTRY, ENTRY + dt.timedelta(seconds=301))[0]

    def test_counts_boundaries_not_elapsed_minutes(self, control):
        """A trade opened at 09:19:58 has seen 09:15-09:20 complete by 09:20:01
        after only three seconds. Elapsed time cannot express that."""
        late = dt.datetime(2026, 8, 27, 3, 49, 58)     # 09:19:58 IST
        assert ep.completed_bars_since(late, late + dt.timedelta(seconds=3)) == 1
        assert ep.exit_allowed("EXHAUSTION", late, late + dt.timedelta(seconds=3))[0]

    def test_early_in_a_bar_is_still_blocked_after_four_minutes(self, control):
        early = dt.datetime(2026, 8, 27, 3, 50, 1)     # 09:20:01 IST
        assert ep.completed_bars_since(early, early + dt.timedelta(minutes=4)) == 0
        assert not ep.exit_allowed("EXHAUSTION", early, early + dt.timedelta(minutes=4))[0]


class TestScope:
    """Exactly which exits this touches — and which it must not."""

    @pytest.mark.parametrize("reason", ["TAKE_PROFIT", "TRAIL_STOP", "EXHAUSTION",
                                        "T1_REVERSAL_EXIT", "T1_HIT", "T2_HIT"])
    def test_profit_management_is_gated(self, control, reason):
        assert not ep.exit_allowed("EXHAUSTION", ENTRY, ENTRY + dt.timedelta(seconds=5))[0]
        assert not ep.exit_allowed(reason, ENTRY, ENTRY + dt.timedelta(seconds=5))[0]

    @pytest.mark.parametrize("reason", [
        "STOP_LOSS", "MARKET_SHOCK_FLATTEN",
        "CONFIRMATION_LOST", "SECTOR_REVERSAL", "POST_EVENT_REVERSAL",
        "LLM_DYNAMIC_EXIT", "MIS_SQUAREOFF", "STALE_EXIT",
        "POST_EVENT_TIME_EXIT", "MANUAL", "KILL_SWITCH", "REALLOCATED",
    ])
    def test_everything_else_fires_immediately(self, control, reason):
        allowed, _, _ = ep.exit_allowed(reason, ENTRY, ENTRY + dt.timedelta(seconds=2))
        assert allowed, f"{reason} must never be delayed by same-bar protection"

    def test_setup_invalidation_is_not_weakened(self, control):
        """No same-bar case was measured here, so it is deliberately untouched."""
        blocked, _ = ep.same_bar_block("CONFIRMATION_LOST", ENTRY,
                                       ENTRY + dt.timedelta(seconds=1))
        assert not blocked


class TestModeIndependence:
    """The whole point: rollback to CONTROL must not reopen the hole."""

    def test_active_under_control(self, control):
        assert not ep.exit_allowed("EXHAUSTION", ENTRY, ENTRY + dt.timedelta(seconds=5))[0]

    def test_active_under_v2(self, monkeypatch, bars1):
        monkeypatch.setattr(ep, "is_v2", lambda: True)
        monkeypatch.setattr(ep, "min_hold_minutes", lambda: 120.0)
        allowed, _, note = ep.exit_allowed("EXHAUSTION", ENTRY, ENTRY + dt.timedelta(seconds=5))
        assert not allowed
        assert "same-bar" in note, "same-bar must be reported before the V2 reason"

    def test_v2_gate_survives_intact(self, monkeypatch, bars1):
        """F4 must not shorten or lengthen the 120-minute experiment."""
        monkeypatch.setattr(ep, "is_v2", lambda: True)
        monkeypatch.setattr(ep, "min_hold_minutes", lambda: 120.0)
        assert not ep.exit_allowed("EXHAUSTION", ENTRY, ENTRY + dt.timedelta(minutes=119))[0]
        assert ep.exit_allowed("EXHAUSTION", ENTRY, ENTRY + dt.timedelta(minutes=120))[0]

    def test_control_releases_after_one_bar_not_after_120m(self, control):
        """Under CONTROL the ONLY delay is the bar, never the V2 horizon."""
        assert ep.exit_allowed("EXHAUSTION", ENTRY, ENTRY + dt.timedelta(minutes=6))[0]


class TestFailSafe:
    def test_zero_bars_disables_the_guard(self, monkeypatch):
        monkeypatch.setattr(ep, "min_completed_bars", lambda: 0)
        monkeypatch.setattr(ep, "is_v2", lambda: False)
        assert ep.exit_allowed("EXHAUSTION", ENTRY, ENTRY + dt.timedelta(seconds=1))[0]

    def test_missing_opened_at_does_not_trap(self, control):
        assert ep.exit_allowed("EXHAUSTION", None, ENTRY)[0]

    def test_an_exception_allows_the_exit(self, monkeypatch, control):
        def _boom():
            raise RuntimeError("config gone")

        monkeypatch.setattr(ep, "min_completed_bars", _boom)
        assert ep.exit_allowed("EXHAUSTION", ENTRY, ENTRY + dt.timedelta(seconds=1))[0]

    def test_tz_aware_entry_is_handled(self, control):
        aware = ENTRY.replace(tzinfo=dt.timezone.utc)
        assert not ep.exit_allowed("EXHAUSTION", aware, ENTRY + dt.timedelta(seconds=5))[0]
