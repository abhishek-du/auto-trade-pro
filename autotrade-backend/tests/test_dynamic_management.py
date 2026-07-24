"""Regression tests for engine/agent/dynamic_management.py's guardrails.

Root-caused 2026-07-22: this module ran every 60s per open position (the
india_trade_loop cadence) with a prompt biased toward tightening ("trail the
SL up... if struggling, tighten the SL") and NO constraints on the LLM's
output. Iterating every minute, the SL ratcheted to (or past) the entry
price within 5-15 minutes on every position that day:
  - TVSMOTOR: SL landed exactly AT entry (3941.35 == 3941.35)
  - NESTLEIND/HINDUNILVR: SL moved ABOVE entry on a BUY (immediate stop-out
    risk on any tick down, before the position had earned it)
  - TATACHEM: SL == TP (both 693.9 -- a config with no room to do anything)
  - PARAS: TP ended BELOW entry (a BUY that cannot exit at a profit)
Normal intraday noise then stopped every position out within minutes, and
~0.3% round-trip fees turned even above-entry exits into net losses --
the day's 0% win rate.

These tests cover clamp_sl_tp() directly (a pure function, no I/O) plus the
manage-cadence gate.
"""
from __future__ import annotations

import time as _time

import pytest

import engine.agent.dynamic_management as dm
from engine.agent.dynamic_management import (
    _BREAKEVEN_MIN_PROFIT_PCT,
    _MAX_SL_STEP_PCT,
    _MIN_SL_GAP_PCT,
    _MIN_TP_EDGE_PCT,
    clamp_sl_tp,
)


class TestMinimumSlGap:
    def test_buy_sl_too_close_to_current_is_floored(self):
        # THE TVSMOTOR regression: LLM proposed SL == current price exactly.
        final_sl, final_tp, notes = clamp_sl_tp(
            direction="BUY", entry=3900.0, current=3941.35,
            cur_sl=3939.5, cur_tp=4700.0,
            new_sl=3941.35, new_tp=5000.0,
            unrealised_pct=1.06,
        )
        assert final_sl <= 3941.35 * (1 - _MIN_SL_GAP_PCT) + 0.01
        assert notes

    def test_sell_sl_too_close_to_current_is_floored(self):
        final_sl, final_tp, notes = clamp_sl_tp(
            direction="SELL", entry=5000.0, current=4950.0,
            cur_sl=5100.0, cur_tp=4500.0,
            new_sl=4950.0, new_tp=4400.0,
            unrealised_pct=1.0,
        )
        assert final_sl >= 4950.0 * (1 + _MIN_SL_GAP_PCT) - 0.01
        assert notes

    def test_reasonable_gap_passes_through_unmodified(self):
        # 2% away from current -- well clear of the 0.75% floor -- should
        # not be touched (aside from the step cap, so keep it small).
        final_sl, final_tp, notes = clamp_sl_tp(
            direction="BUY", entry=100.0, current=105.0,
            cur_sl=99.9, cur_tp=120.0,
            new_sl=100.0, new_tp=125.0,
            unrealised_pct=5.0,
        )
        assert abs(final_sl - 100.0) < 0.01
        assert abs(final_tp - 125.0) < 0.01
        assert notes == []


class TestStepCap:
    def test_buy_sl_step_capped_even_with_valid_gap(self):
        # LLM wants to jump the SL up by 2% in one update -- must be capped
        # to _MAX_SL_STEP_PCT even though the resulting gap-to-current would
        # otherwise be fine.
        current = 1000.0
        cur_sl = 950.0
        huge_jump = 990.0  # +4.2% from cur_sl in one shot
        final_sl, _, notes = clamp_sl_tp(
            direction="BUY", entry=970.0, current=current,
            cur_sl=cur_sl, cur_tp=1100.0,
            new_sl=huge_jump, new_tp=1100.0,
            unrealised_pct=3.0,
        )
        assert final_sl <= cur_sl + _MAX_SL_STEP_PCT * current + 0.01
        assert any("step capped" in n for n in notes)

    def test_sell_sl_step_capped(self):
        current = 1000.0
        cur_sl = 1050.0
        huge_drop = 1005.0
        final_sl, _, notes = clamp_sl_tp(
            direction="SELL", entry=1030.0, current=current,
            cur_sl=cur_sl, cur_tp=900.0,
            new_sl=huge_drop, new_tp=900.0,
            unrealised_pct=2.5,
        )
        assert final_sl >= cur_sl - _MAX_SL_STEP_PCT * current - 0.01
        assert any("step capped" in n for n in notes)


class TestBreakevenGate:
    def test_buy_breakeven_sl_denied_below_profit_threshold(self):
        # THE NESTLEIND/HINDUNILVR regression: SL moved to/above entry while
        # barely in profit (well under 1%). current=1512 keeps the proposed
        # SL (1500, at entry) clear of the min-gap floor on its own, so this
        # isolates the breakeven-specific denial from the gap floor.
        final_sl, _, notes = clamp_sl_tp(
            direction="BUY", entry=1500.0, current=1512.0,
            cur_sl=1495.0, cur_tp=1650.0,  # close enough that the step cap won't mask this
            new_sl=1500.0, new_tp=1650.0,  # proposing SL at entry
            unrealised_pct=0.8,  # under the 1% breakeven bar
        )
        assert final_sl < 1500.0
        assert any("breakeven" in n for n in notes)

    def test_buy_breakeven_sl_allowed_above_profit_threshold(self):
        final_sl, _, notes = clamp_sl_tp(
            direction="BUY", entry=1500.0, current=1520.0,
            cur_sl=1454.0, cur_tp=1650.0,
            new_sl=1502.0, new_tp=1650.0,
            unrealised_pct=_BREAKEVEN_MIN_PROFIT_PCT + 0.5,
        )
        # Should NOT be denied for being at/above entry (step cap may still
        # apply, but not the breakeven-specific denial).
        assert not any("breakeven" in n for n in notes)

    def test_sell_breakeven_sl_denied_below_profit_threshold(self):
        # current=988 keeps the proposed SL (1000, at entry) clear of the
        # min-gap floor on its own, isolating the breakeven-specific denial.
        final_sl, _, notes = clamp_sl_tp(
            direction="SELL", entry=1000.0, current=988.0,
            cur_sl=1002.0, cur_tp=900.0,  # close enough that the step cap won't mask this
            new_sl=1000.0, new_tp=900.0,
            unrealised_pct=0.8,
        )
        assert final_sl > 1000.0
        assert any("breakeven" in n for n in notes)


class TestNoLoosening:
    def test_buy_sl_cannot_be_moved_down(self):
        # A proposal that would LOOSEN an already-trailed stop must be
        # rejected outright -- the current SL is the floor, never the ceiling.
        final_sl, _, notes = clamp_sl_tp(
            direction="BUY", entry=1000.0, current=1050.0,
            cur_sl=1030.0, cur_tp=1200.0,
            new_sl=1010.0, new_tp=1200.0,  # tries to loosen from 1030 to 1010
            unrealised_pct=5.0,
        )
        assert final_sl == 1030.0
        assert any("loosening denied" in n for n in notes)

    def test_sell_sl_cannot_be_moved_up(self):
        final_sl, _, notes = clamp_sl_tp(
            direction="SELL", entry=1000.0, current=950.0,
            cur_sl=970.0, cur_tp=800.0,
            new_sl=990.0, new_tp=800.0,  # tries to loosen from 970 to 990
            unrealised_pct=5.0,
        )
        assert final_sl == 970.0
        assert any("loosening denied" in n for n in notes)


class TestTpFloor:
    def test_buy_tp_cannot_land_below_entry(self):
        # THE PARAS regression: TP ended up BELOW entry -- a BUY that
        # structurally cannot exit at a profit.
        final_sl, final_tp, notes = clamp_sl_tp(
            direction="BUY", entry=1229.87, current=1225.0,
            cur_sl=1192.6, cur_tp=1321.71,
            new_sl=1220.0, new_tp=1228.0,  # TP below entry
            unrealised_pct=-0.4,
        )
        assert final_tp > 1229.87
        assert any("TP floored" in n for n in notes)

    def test_sell_tp_cannot_land_above_entry(self):
        final_sl, final_tp, notes = clamp_sl_tp(
            direction="SELL", entry=1000.0, current=1005.0,
            cur_sl=1050.0, cur_tp=900.0,
            new_sl=1030.0, new_tp=1002.0,  # TP above entry
            unrealised_pct=-0.5,
        )
        assert final_tp < 1000.0
        assert any("TP capped" in n for n in notes)


class TestSlEqualsTpBug:
    def test_buy_sl_and_tp_are_never_equal_after_clamp(self):
        # THE TATACHEM regression: SL and TP both landed at 693.9.
        final_sl, final_tp, _ = clamp_sl_tp(
            direction="BUY", entry=692.43, current=693.9,
            cur_sl=691.8, cur_tp=695.0,
            new_sl=693.9, new_tp=693.9,
            unrealised_pct=0.21,
        )
        assert final_sl != final_tp
        assert final_tp > final_sl


class TestExitBypassesClamps:
    def test_exit_action_sets_sl_and_tp_to_current_unmodified(self):
        final_sl, final_tp, notes = clamp_sl_tp(
            direction="BUY", entry=1000.0, current=990.0,
            cur_sl=970.0, cur_tp=1100.0,
            new_sl=990.0, new_tp=990.0,
            unrealised_pct=-1.0,
            action="EXIT",
        )
        assert final_sl == 990.0
        assert final_tp == 990.0
        assert notes == []


class TestManageCadenceGate:
    def test_second_call_within_interval_is_skipped(self, monkeypatch):
        # THE core cadence fix: the manager must not re-evaluate every 60s
        # (the india_trade_loop tick) -- only every _MANAGE_INTERVAL_SEC.
        calls = []

        async def fake_execute(*a, **kw):
            calls.append(1)
            class _Result:
                scalars = lambda self: self
                all = lambda self: []
            return _Result()

        session = type("S", (), {"execute": fake_execute, "commit": fake_execute})()
        dm._last_manage_ts = _time.monotonic()  # just ran
        import asyncio
        asyncio.run(dm.llm_dynamic_sl_tp(session))
        assert calls == []  # skipped -- interval not elapsed

    def test_call_after_interval_elapsed_proceeds(self, monkeypatch):
        dm._last_manage_ts = _time.monotonic() - (dm._MANAGE_INTERVAL_SEC + 1)

        async def fake_execute(*a, **kw):
            class _Result:
                def scalars(self):
                    return self
                def all(self):
                    return []
            return _Result()

        session = type("S", (), {"execute": fake_execute, "commit": fake_execute})()
        import asyncio
        asyncio.run(dm.llm_dynamic_sl_tp(session))
        # _last_manage_ts should have been refreshed to "now"
        assert _time.monotonic() - dm._last_manage_ts < 1.0
