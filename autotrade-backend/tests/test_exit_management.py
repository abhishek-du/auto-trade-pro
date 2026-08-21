"""Exit management — trailing stop and exhaustion detection (2026-08-21).

THE GAP
-------
After the T1 50% scale-out, fast_sl_check sets `take_profit = 0.0` and moves the
stop to breakeven, with a comment deferring to "trailing logic in
update_positions_with_current_prices". That logic did not exist — the whole exit
path contained exactly one stop_loss reassignment, the breakeven line itself.
The runner therefore had no upside management at all: it could only end at
breakeven or at the 15:10 squareoff.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from engine.indicators import detect_exhaustion
from paper_trading.position_tracker import check_time_exit, update_trailing_stop


def _pos(entry=100.0, sl=98.0, direction="BUY", product="MIS"):
    return SimpleNamespace(entry_price=entry, stop_loss=sl, direction=direction,
                           highest_high=None, lowest_low=None, product=product)


class TestTrailingStop:

    def test_moves_to_breakeven_at_the_trigger(self):
        p = _pos()
        changed, note = update_trailing_stop(p, 102.5, atr=0.0)   # +2.5%
        assert changed and p.stop_loss == pytest.approx(100.0)
        assert "breakeven" in note

    def test_does_not_move_before_the_trigger(self):
        p = _pos()
        changed, _ = update_trailing_stop(p, 100.5, atr=0.0)      # +0.5%
        assert not changed and p.stop_loss == pytest.approx(98.0)

    def test_trails_behind_the_peak(self):
        p = _pos()
        update_trailing_stop(p, 110.0, atr=1.0)                   # peak 110
        first = p.stop_loss
        assert first == pytest.approx(107.5)                      # 110 - 2.5*1
        update_trailing_stop(p, 120.0, atr=1.0)                   # new peak
        assert p.stop_loss == pytest.approx(117.5)
        assert p.stop_loss > first

    def test_stop_never_loosens_when_price_falls_back(self):
        """A trailing stop that can move down is not a stop."""
        p = _pos()
        update_trailing_stop(p, 120.0, atr=1.0)
        locked = p.stop_loss
        update_trailing_stop(p, 112.0, atr=1.0)                   # pullback
        assert p.stop_loss == pytest.approx(locked)
        assert p.highest_high == pytest.approx(120.0)             # peak remembered

    def test_never_trails_above_the_current_price(self):
        """A stop set above the live price would fire instantly."""
        p = _pos()
        update_trailing_stop(p, 110.0, atr=50.0)                  # absurd ATR
        assert p.stop_loss < 110.0

    def test_no_atr_still_gives_breakeven(self):
        """Without a usable ATR the chandelier is skipped rather than guessed
        from a percentage that ignores volatility — but breakeven still applies."""
        p = _pos()
        changed, _ = update_trailing_stop(p, 105.0, atr=0.0)
        assert changed and p.stop_loss == pytest.approx(100.0)

    def test_short_side_is_mirrored(self):
        p = _pos(entry=100.0, sl=102.0, direction="SELL")
        update_trailing_stop(p, 95.0, atr=1.0)                    # +5% for a short
        assert p.stop_loss <= 100.0 and p.stop_loss > 95.0
        assert p.lowest_low == pytest.approx(95.0)

    def test_flag_disables_it(self):
        p = _pos()
        with patch("utils.config.settings.ENABLE_TRAILING_STOP", False):
            changed, _ = update_trailing_stop(p, 120.0, atr=1.0)
        assert not changed and p.stop_loss == pytest.approx(98.0)


def _bars(n=10, base=100.0, vol=1000.0):
    return pd.DataFrame({"open":[base]*n, "high":[base+0.5]*n, "low":[base-0.5]*n,
                         "close":[base]*n, "volume":[vol]*n})


class TestExhaustion:

    def test_rejection_wick(self):
        d = _bars(); d.loc[8, ["open","high","low","close"]] = [100, 110, 99.5, 100.5]
        hit, why = detect_exhaustion(d, atr=1.0)
        assert hit and "wick" in why

    def test_absorption(self):
        d = _bars(); d.loc[8, "volume"] = 5000
        d.loc[8, ["open","close"]] = [100, 100.05]
        hit, why = detect_exhaustion(d, atr=1.0)
        assert hit and "absorption" in why

    def test_momentum_failure_on_a_parabolic_run(self):
        """loss == 0 means an unbroken run of up-bars — RSI 100, the most
        overbought state there is. Guarding the division by skipping that case
        made this tell unable to fire on exactly the move it exists to catch."""
        d = _bars()
        for i in range(3, 9):
            d.loc[i, "close"] = 100 + (i - 2) * 2
        d.loc[8, "close"] = d.loc[7, "close"]        # stalls
        hit, why = detect_exhaustion(d, atr=1.0)
        assert hit and "RSI" in why

    def test_quiet_tape_is_not_exhaustion(self):
        assert detect_exhaustion(_bars(), atr=1.0) == (False, None)

    def test_too_few_bars_is_silence_not_a_guess(self):
        assert detect_exhaustion(_bars(n=3), atr=1.0) == (False, None)
        assert detect_exhaustion(None, atr=1.0) == (False, None)

    def test_never_raises(self):
        """An exit heuristic must not be able to take down the exit loop."""
        assert detect_exhaustion(pd.DataFrame({"open":[1]}), atr=1.0) == (False, None)


class TestTimeExit:

    @pytest.mark.parametrize("h,m,expected", [(14,59,False), (15,10,True), (15,20,True)])
    def test_mis_flattens_from_the_cutoff(self, h, m, expected):
        assert check_time_exit(_pos(), SimpleNamespace(hour=h, minute=m)) is expected

    def test_delivery_positions_are_untouched(self):
        assert check_time_exit(_pos(product="CNC"),
                               SimpleNamespace(hour=15, minute=25)) is False
