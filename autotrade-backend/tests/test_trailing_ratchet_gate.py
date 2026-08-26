"""The trailing ratchet is profit management, so V2 has to defer the MOVE.

The ratchet and the hard stop share one column, pos.stop_loss. A stop moved to
breakeven at +2% closes the position at +0% and records itself as STOP_LOSS —
Layer 3 wearing Layer 1's label. If V2 deferred only the exits it can see by
name, the trailing stop would keep closing winners early and the experiment
would measure nothing.

Tracking of the extreme must continue throughout, so the chandelier applies from
the true peak the moment the window opens rather than restarting from wherever
price happens to be.
"""
from __future__ import annotations

import pytest

from paper_trading.position_tracker import update_trailing_stop


class _Pos:
    def __init__(self, entry=100.0, sl=95.0, long=True):
        self.entry_price = entry
        self.stop_loss = sl
        self.highest_high = None
        self.lowest_low = None
        self.direction = "BUY" if long else "SELL"


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    from utils.config import settings

    monkeypatch.setattr(settings, "ENABLE_TRAILING_STOP", True, raising=False)
    monkeypatch.setattr(settings, "TRAILING_BREAKEVEN_TRIGGER_PCT", 2.0, raising=False)
    monkeypatch.setattr(settings, "TRAILING_STOP_ATR_MULT", 2.5, raising=False)


class TestRatchetOn:
    def test_breakeven_move_happens_by_default(self):
        """+3% clears the 2% trigger, so the stop leaves the original 95.

        It lands at 100.5, not 100: breakeven raises it to entry and the
        chandelier (peak 103 - 2.5 x ATR 1.0) then raises it again in the same
        call. Either way the position can no longer lose — which is exactly the
        behaviour V2 defers.
        """
        pos = _Pos()
        moved, note = update_trailing_stop(pos, 103.0, atr=1.0)
        assert moved
        assert pos.stop_loss >= pos.entry_price
        assert pos.stop_loss == pytest.approx(100.5)

    def test_breakeven_alone_when_there_is_no_atr(self):
        pos = _Pos()
        moved, note = update_trailing_stop(pos, 103.0, atr=0.0)
        assert moved and pos.stop_loss == pytest.approx(100.0)
        assert "breakeven" in (note or "")

    def test_default_is_unchanged_for_existing_callers(self):
        """No keyword: the pre-Phase-25 signature and behaviour."""
        pos = _Pos()
        update_trailing_stop(pos, 103.0, 1.0)
        assert pos.stop_loss == pytest.approx(100.5)


class TestRatchetOff:
    def test_the_stop_is_left_exactly_where_it_was(self):
        pos = _Pos()
        moved, note = update_trailing_stop(pos, 103.0, atr=1.0, ratchet=False)
        assert not moved and note is None
        assert pos.stop_loss == pytest.approx(95.0), (
            "V2 must leave the ORIGINAL risk stop in place, not a profit stop"
        )

    def test_the_peak_is_still_tracked(self):
        """So the chandelier is correct the moment the window opens."""
        pos = _Pos()
        update_trailing_stop(pos, 108.0, atr=1.0, ratchet=False)
        update_trailing_stop(pos, 104.0, atr=1.0, ratchet=False)
        assert pos.highest_high == pytest.approx(108.0)

    def test_a_short_tracks_its_trough(self):
        pos = _Pos(entry=100.0, sl=105.0, long=False)
        update_trailing_stop(pos, 92.0, atr=1.0, ratchet=False)
        assert pos.lowest_low == pytest.approx(92.0)
        assert pos.stop_loss == pytest.approx(105.0)

    def test_releasing_the_gate_trails_from_the_tracked_peak(self):
        """The whole point of tracking through the deferral."""
        pos = _Pos()
        update_trailing_stop(pos, 108.0, atr=1.0, ratchet=False)   # peak recorded
        assert pos.stop_loss == pytest.approx(95.0)

        moved, _ = update_trailing_stop(pos, 106.0, atr=1.0, ratchet=True)
        assert moved
        # Chandelier from the 108 peak (108 - 2.5*1.0), not from 106.
        assert pos.stop_loss == pytest.approx(105.5)

    def test_a_stop_never_loosens_in_either_mode(self):
        pos = _Pos(sl=99.0)
        update_trailing_stop(pos, 90.0, atr=1.0, ratchet=False)
        assert pos.stop_loss == pytest.approx(99.0)
        update_trailing_stop(pos, 90.0, atr=1.0, ratchet=True)
        assert pos.stop_loss >= 99.0
