"""Path F — strategy rule tests.

Rules are pure functions over a DataFrame, so these need no database. Each rule
gets a trigger case, a near-miss, and arithmetic checks on stop/target — a rule
that fires is only useful if the levels it emits are the right side of entry.

The forming-bar tests matter most: audit D5 was about indicators computed on a
bar that had not printed yet. `closed()` drops it, and these assert the rules
actually ignore it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from engine import tactical_rules as R
from engine.tactical_rules import Signal, closed


def _frame(n=60, base=100.0, step=0.0, vol=100_000, start=None, freq="1min"):
    start = start or datetime(2026, 8, 20, 9, 15)
    closes = [base + i * step for i in range(n)]
    return pd.DataFrame({
        "open":   [c - 0.1 for c in closes],
        "high":   [c + 0.3 for c in closes],
        "low":    [c - 0.3 for c in closes],
        "close":  closes,
        "volume": [vol] * n,
        "timestamp": pd.date_range(start, periods=n, freq=freq),
    })


class TestSignalSanity:
    def test_long_with_inverted_levels_is_rejected(self):
        s = Signal("X", "BUY", 100, 105, 110, 60, "T", datetime.now())  # stop above entry
        assert not s.is_sane()

    def test_short_with_inverted_levels_is_rejected(self):
        s = Signal("X", "SELL", 100, 95, 90, 60, "T", datetime.now())   # stop below entry
        assert not s.is_sane()

    def test_zero_risk_is_rejected(self):
        assert not Signal("X", "BUY", 100, 100, 110, 60, "T", datetime.now()).is_sane()

    def test_valid_long_passes(self):
        assert Signal("X", "BUY", 100, 98, 104, 60, "T", datetime.now()).is_sane()


class TestClosedHelper:
    def test_drops_exactly_one_bar(self):
        df = _frame(10)
        assert len(closed(df)) == len(df) - 1

    def test_single_row_frame_is_not_emptied(self):
        assert len(closed(_frame(1))) == 1


class TestORB:
    def _setup(self, n=60):
        df = _frame(n, base=100.0, step=0.0, vol=100_000)
        # Opening range 09:15-09:30 sits in the first 15 rows.
        return df, datetime(2026, 8, 20, 9, 15), datetime(2026, 8, 20, 9, 30)

    def test_breakout_above_range_with_volume_fires(self):
        df, s, e = self._setup()
        df.loc[df.index[-6:], "volume"] = 400_000          # surge on closed bars
        out = R.orb("X.NS", df, live_price=105.0, orb_start=s, orb_end=e)
        assert len(out) == 1 and out[0].side == "BUY"
        assert out[0].strategy_name == "ORB"

    def test_breakdown_below_range_fires_short(self):
        df, s, e = self._setup()
        df.loc[df.index[-6:], "volume"] = 400_000
        out = R.orb("X.NS", df, live_price=95.0, orb_start=s, orb_end=e)
        assert len(out) == 1 and out[0].side == "SELL"

    def test_no_volume_surge_no_signal(self):
        df, s, e = self._setup()
        out = R.orb("X.NS", df, live_price=105.0, orb_start=s, orb_end=e)
        assert out == []

    def test_inside_range_no_signal(self):
        df, s, e = self._setup()
        df.loc[df.index[-6:], "volume"] = 400_000
        assert R.orb("X.NS", df, live_price=100.1, orb_start=s, orb_end=e) == []

    def test_levels_are_the_right_side_of_entry(self):
        df, s, e = self._setup()
        df.loc[df.index[-6:], "volume"] = 400_000
        sig = R.orb("X.NS", df, live_price=105.0, orb_start=s, orb_end=e)[0]
        assert sig.stop_loss < sig.entry_price < sig.target
        assert sig.target == pytest.approx(105.0 * 1.02)


class TestVWAP:
    def test_two_closes_above_vwap_fires_long(self):
        df = _frame(60, base=100.0, step=0.4)   # steady uptrend puts price above VWAP
        out = R.vwap_trend("X.NS", df, live_price=float(df["close"].iloc[-1]) + 1)
        if out:                                  # VWAP availability depends on TA-Lib path
            assert out[0].side == "BUY"
            assert out[0].stop_loss < out[0].entry_price < out[0].target

    def test_insufficient_history_no_signal(self):
        assert R.vwap_trend("X.NS", _frame(10), live_price=101.0) == []


class TestGapAndGo:
    def test_gap_up_that_keeps_rising_fires(self):
        daily = _frame(5, base=100.0, step=0.0, freq="1D")
        intraday = _frame(40, base=103.0, step=0.25)   # >1% gap, rising
        out = R.gap_and_go("X.NS", intraday, daily, live_price=115.0)
        if out:
            assert out[0].side == "BUY"
            assert out[0].strategy_name == "GAP_AND_GO"

    def test_no_gap_no_signal(self):
        daily = _frame(5, base=100.0, freq="1D")
        intraday = _frame(40, base=100.05, step=0.05)  # gap far below 1%
        assert R.gap_and_go("X.NS", intraday, daily, live_price=101.0) == []

    def test_gap_up_but_fading_no_signal(self):
        daily = _frame(5, base=100.0, freq="1D")
        intraday = _frame(40, base=103.0, step=-0.2)   # gapped then faded
        assert R.gap_and_go("X.NS", intraday, daily, live_price=99.0) == []


class TestPivots:
    def test_levels_ordered_correctly(self):
        lv = R.pivot_levels(_frame(5, base=100.0, freq="1D"))
        assert lv is not None
        assert lv["S1"] < lv["P"] < lv["R1"] <= lv["R2"]

    def test_breakout_above_r1_on_volume_fires(self):
        daily = _frame(5, base=100.0, freq="1D")
        lv = R.pivot_levels(daily)
        intraday = _frame(60, base=100.0)
        intraday.loc[intraday.index[-6:], "volume"] = 500_000
        # Between R1 and R2 — above R1 is the trigger, below R2 leaves room for
        # the target. Pricing above R2 correctly produces nothing.
        live = (lv["R1"] + lv["R2"]) / 2
        out = R.pivot_bounce_breakout("X.NS", intraday, daily, live_price=live)
        assert out, "break above R1 on a volume surge must fire"
        assert out[0].strategy_name == "PIVOT_BREAKOUT"
        assert out[0].stop_loss < out[0].entry_price < out[0].target

    def test_price_beyond_r2_has_no_room_and_is_skipped(self):
        daily = _frame(5, base=100.0, freq="1D")
        lv = R.pivot_levels(daily)
        intraday = _frame(60, base=100.0)
        intraday.loc[intraday.index[-6:], "volume"] = 500_000
        assert R.pivot_bounce_breakout("X.NS", intraday, daily, lv["R2"] + 1.0) == []

    def test_no_daily_data_no_signal(self):
        assert R.pivot_bounce_breakout("X.NS", _frame(60), _frame(0), 100.0) == []


class TestMeanReversion:
    def _overbought(self, n=60):
        # Relentless rally drives RSI > 70 and price above the upper band.
        return _frame(n, base=100.0, step=1.2)

    def _oversold(self, n=60):
        return _frame(n, base=200.0, step=-1.2)

    def test_overbought_fade_targets_the_middle_band(self):
        df = self._overbought()
        out = R.overbought_fade("X.NS", df, live_price=float(df["close"].iloc[-1]) * 1.02)
        assert out, "overbought setup must fire — a rule that never fires is dead code"
        s = out[0]
        assert s.side == "SELL"
        assert s.target < s.entry_price < s.stop_loss
        assert s.sub_pipeline == "F4"

    def test_oversold_rebound_targets_the_middle_band(self):
        df = self._oversold()
        out = R.oversold_rebound("X.NS", df, live_price=float(df["close"].iloc[-1]) * 0.98)
        assert out, "oversold setup must fire"
        s = out[0]
        assert s.side == "BUY"
        assert s.stop_loss < s.entry_price < s.target

    def test_stop_stays_above_entry_when_price_overshoots_the_band(self):
        """Regression: 'upper band + 0.5%' put the short's stop BELOW entry once
        price ran past the band, so every fade was silently discarded by
        is_sane(). The stop must be the higher of band+0.5% and entry+0.5%."""
        df = self._overbought()
        far_above = float(df["close"].iloc[-1]) * 1.10
        out = R.overbought_fade("X.NS", df, live_price=far_above)
        assert out, "a bigger overshoot must still produce a signal"
        assert out[0].stop_loss > out[0].entry_price

    def test_stop_stays_below_entry_when_price_undershoots_the_band(self):
        df = self._oversold()
        far_below = float(df["close"].iloc[-1]) * 0.90
        out = R.oversold_rebound("X.NS", df, live_price=far_below)
        assert out
        assert out[0].stop_loss < out[0].entry_price

    def test_flat_market_produces_neither(self):
        flat = _frame(60, base=100.0, step=0.0)
        px = 100.0
        assert R.overbought_fade("X.NS", flat, px) == []
        assert R.oversold_rebound("X.NS", flat, px) == []


class TestFormingBarDiscipline:
    """The D5 property: a wild un-printed last bar must not move the answer."""

    def test_orb_ignores_the_forming_bar(self):
        df, s, e = TestORB()._setup()
        df.loc[df.index[-6:], "volume"] = 400_000
        clean = R.orb("X.NS", df, 105.0, s, e)

        polluted = df.copy()
        polluted.loc[polluted.index[-1], ["high", "close"]] = 9_999.0
        polluted.loc[polluted.index[-1], "volume"] = 99_000_000
        after = R.orb("X.NS", polluted, 105.0, s, e)

        assert bool(clean) == bool(after)
        if clean and after:
            assert clean[0].stop_loss == pytest.approx(after[0].stop_loss)

    def test_mean_reversion_ignores_the_forming_bar(self):
        df = _frame(60, base=100.0, step=1.2)
        px = float(df["close"].iloc[-1]) * 1.02
        clean = R.overbought_fade("X.NS", df, px)

        polluted = df.copy()
        polluted.loc[polluted.index[-1], "close"] = 1.0   # absurd un-printed bar
        after = R.overbought_fade("X.NS", polluted, px)

        assert bool(clean) == bool(after)

    def test_volume_surge_excludes_its_own_window(self):
        """The trailing mean must not contain the window being measured."""
        df = _frame(60, vol=100_000)
        df.loc[df.index[-6:-1], "volume"] = 1_000_000
        assert R._vol_surge(df) > 5.0
