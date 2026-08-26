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

    def test_flat_position_is_not_tightened(self):
        """A trailing stop protects PROFIT. It must not tighten a position that
        has not earned anything yet.

        Caught by a dry run on the live book (2026-08-21), not by the first
        version of these tests: an ungated chandelier moved CEIGALL's stop from
        285.81 to 314.72 while the position was DOWN 0.04% — 1% under the live
        price, so any ordinary pullback would have stopped it out.
        """
        p = _pos(entry=100.0, sl=90.0)
        changed, _ = update_trailing_stop(p, 100.0, atr=1.0)   # flat
        assert not changed and p.stop_loss == pytest.approx(90.0)

    def test_losing_position_is_not_tightened(self):
        p = _pos(entry=100.0, sl=90.0)
        changed, _ = update_trailing_stop(p, 97.0, atr=1.0)    # down 3%
        assert not changed and p.stop_loss == pytest.approx(90.0)

    def test_chandelier_needs_the_profit_trigger_first(self):
        """Just below the trigger: nothing moves. Just above: both stages apply."""
        below = _pos(entry=100.0, sl=90.0)
        update_trailing_stop(below, 101.5, atr=0.5)            # +1.5% < 2%
        assert below.stop_loss == pytest.approx(90.0)

        above = _pos(entry=100.0, sl=90.0)
        update_trailing_stop(above, 103.0, atr=0.5)            # +3% > 2%
        assert above.stop_loss > 90.0

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


class TestFastSlCheckSafety:
    """The advanced exits must never be able to disable the fixed stop-loss.

    fast_sl_check is the 5s loop protecting every open position. These assert
    the structural guarantees, because a runtime failure there is silent and
    costs real money.
    """

    def _src(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "tasks" / "india_tasks.py").read_text(
            encoding="utf-8")

    def test_advanced_block_is_fully_guarded(self):
        src = self._src()
        i = src.index("Advanced exit management")
        # Located by search, not by a fixed-width window. The window was 6000
        # chars and had already been "widened" once; adding ~1.3k of exhaustion
        # audit logging inside the block pushed the handler past it and failed
        # this test while the guarantee itself still held. A magic number that
        # has to grow whenever the block grows is not testing anything.
        assert "except Exception as _adv_exc" in src[i:], (
            "advanced exit checks are not wrapped — an error would abort the tick "
            "and the fixed stop-loss would never run"
        )
        j = src.index("except Exception as _adv_exc", i)
        assert "try:" in src[i:j], "the handler exists but nothing opens a try before it"

    def test_fixed_stop_loss_is_computed_after_the_guarded_block(self):
        """sl_hit must be evaluated AFTER the try/except, so a trailing stop
        tightened this tick is acted on immediately — and so a failure in the
        block cannot skip the computation."""
        src = self._src()
        i_try = src.index("Advanced exit management")
        i_exc = src.index("except Exception as _adv_exc", i_try)
        i_sl = src.index("sl_hit = (is_buy and price <= pos.stop_loss)", i_exc)
        assert i_exc < i_sl

    def test_exhaustion_routes_through_the_normal_close_path(self):
        """Reusing close_paper_trade keeps wallet accounting, P&L and the audit
        row identical to every other exit."""
        src = self._src()
        i = src.index('reason = _adv_reason or "STOP_LOSS"')
        assert "close_paper_trade(pos, price, reason, session)" in src[i:i + 400]

    def test_t1_advances_the_tier(self):
        """Without this the T2 branch can never fire — exit_tier would stay 1."""
        src = self._src()
        i = src.index("T1_HIT")
        assert "pos.exit_tier = 2" in src[i:i + 700]


class TestExhaustionRespectsExitEligibility:
    """A SWING position inside its minimum-hold window has fast exits
    deliberately suppressed, so an exit signal computed for it can never be
    acted on.

    Measured 2026-08-24: RUBICON.NS (SWING, swing_min_hold two days out) had
    exhaustion fire 358 times in one session — every 5 seconds — and closed
    nothing, because the swing bypass reset sl_hit each time. GLAND.NS, an
    ordinary position, fired once and closed once, which is how the mechanism
    is supposed to behave.

    Not merely log noise: every one of those 358 detections cost a 5m candle
    query plus an ATR computation on the 5-second exit loop, for a decision that
    was structurally impossible.
    """

    def _src(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / "tasks" / "india_tasks.py").read_text(
            encoding="utf-8")

    def test_eligibility_is_computed_before_the_advanced_block(self):
        src = self._src()
        i_flag = src.index("_exit_blocked = False")
        i_try = src.index("Advanced exit management")
        assert i_flag < src.index("except Exception as _adv_exc", i_try)

    def test_exhaustion_is_gated_on_it(self):
        src = self._src()
        # Anchor on the guarded call site itself, not a bare substring: the
        # setting name also appears in utils/config.py's own import surface.
        i = src.index("if _adv_reason is None and _c5 is not None")
        assert "not _exit_blocked" in src[i:i + 220]

    def test_trailing_is_NOT_gated_on_the_swing_hold(self):
        """Tightening a stop is not an exit. The ratcheted level must still be
        maintained through the SWING hold window so it applies the moment that
        window ends.

        Phase 25 added a SECOND, unrelated condition on the ratchet — the V2
        profit-management gate — which is asserted separately below. The SWING
        bypass must still not touch it.
        """
        src = self._src()
        i = src.index("update_trailing_stop(pos, price, _atr")
        seg = src[i - 400:i]
        assert "not _exit_blocked" not in seg

    def test_trailing_ratchet_carries_the_v2_gate(self):
        """V2 defers the ratchet, because the ratchet and the hard stop share
        pos.stop_loss.

        A stop moved to breakeven at +2% closes the position at +0% and reports
        itself as STOP_LOSS. That is the profit-management layer wearing Layer
        1's label, so deferring profit management has to defer the move with it.
        Tracking of the extreme continues regardless — see
        paper_trading/position_tracker.py::update_trailing_stop(ratchet=False).
        """
        src = self._src()
        i = src.index("update_trailing_stop(pos, price, _atr")
        assert "ratchet=_pm_ok" in src[i:i + 120]

    def test_the_v2_gate_is_computed_from_the_shared_policy_module(self):
        """One classifier, not a threshold re-derived per call site."""
        src = self._src()
        i = src.index("_pm_ok = ")
        assert "engine.exit_policy" in src[i - 400:i]

    def test_blocked_condition_matches_the_bypass_below(self):
        """If these two drift apart, the check is gated on the wrong thing."""
        src = self._src()
        i = src.index("_exit_blocked = False")
        seg = src[i:i + 500]
        assert 'pos.trade_style == "SWING"' in seg and "pos.swing_min_hold" in seg
        assert "< pos.swing_min_hold" in seg
