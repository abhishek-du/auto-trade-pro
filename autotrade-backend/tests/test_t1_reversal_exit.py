"""Regression tests for paper_trading/trade_simulator.py::_t1_reversal_exit()
and the ReentryWatch registration it performs -- the side-effect half of the
T1-reanalysis feature (see tests/test_t1_reanalysis.py for the pure decision
logic, and tests/test_reentry_watch.py for the breakout-checking watcher).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.models import TradeDirection
from paper_trading.trade_simulator import _t1_reversal_exit


def _make_pos(direction=TradeDirection.BUY):
    trade = SimpleNamespace(id=1, signal_confidence=75.0, indicator_snapshot={})
    pos = SimpleNamespace(symbol="TESTCO.NS", direction=direction, trade=trade)
    return pos


def _closed_trade(event_id=None, evidence_ids=None):
    return SimpleNamespace(
        id=42, symbol="TESTCO.NS", pnl=150.0, entry_price=100.0, size_units=10,
        signal_confidence=80.0,
        indicator_snapshot={"trade_mgmt": {"event_id": event_id, "evidence_ids": evidence_ids or []}},
    )


class TestReentryWatchRegistration:
    @pytest.mark.asyncio
    async def test_watch_registered_when_event_id_present(self):
        pos = _make_pos()
        with patch("paper_trading.trade_simulator.close_paper_trade",
                    AsyncMock(return_value=_closed_trade(event_id=2848, evidence_ids=["2848"]))):
            session = AsyncMock()
            session.add = MagicMock()
            result = await _t1_reversal_exit(
                pos, 110.0, {"decision": "EXIT", "reasoning": "reversal", "watch_level": 105.0}, session,
            )
        assert result["reason"] == "T1_REVERSAL_EXIT"
        assert session.add.call_count == 1
        added = session.add.call_args[0][0]
        assert added.symbol == "TESTCO.NS"
        assert added.event_id == 2848
        assert added.watch_level == 105.0
        assert added.status == "WATCHING"

    @pytest.mark.asyncio
    async def test_no_watch_registered_when_event_id_absent(self):
        # A legacy/technical position with no canonical event on record --
        # the position still closes, but no watch is fabricated for it.
        pos = _make_pos()
        with patch("paper_trading.trade_simulator.close_paper_trade",
                    AsyncMock(return_value=_closed_trade(event_id=None))):
            session = AsyncMock()
            session.add = MagicMock()
            result = await _t1_reversal_exit(
                pos, 110.0, {"decision": "EXIT", "reasoning": "reversal", "watch_level": 105.0}, session,
            )
        assert result["reason"] == "T1_REVERSAL_EXIT"
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_watch_level_falls_back_deterministically_buy(self):
        pos = _make_pos(TradeDirection.BUY)
        with patch("paper_trading.trade_simulator.close_paper_trade",
                    AsyncMock(return_value=_closed_trade(event_id=2848))):
            session = AsyncMock()
            session.add = MagicMock()
            await _t1_reversal_exit(pos, 100.0, {"decision": "EXIT", "reasoning": "x", "watch_level": None}, session)
        added = session.add.call_args[0][0]
        assert added.watch_level == 101.0  # 100 * 1.01, a confirmation buffer above exit for a BUY

    @pytest.mark.asyncio
    async def test_missing_watch_level_falls_back_deterministically_sell(self):
        pos = _make_pos(TradeDirection.SELL)
        with patch("paper_trading.trade_simulator.close_paper_trade",
                    AsyncMock(return_value=_closed_trade(event_id=2848))):
            session = AsyncMock()
            session.add = MagicMock()
            await _t1_reversal_exit(pos, 100.0, {"decision": "EXIT", "reasoning": "x", "watch_level": None}, session)
        added = session.add.call_args[0][0]
        assert added.watch_level == 99.0  # 100 * 0.99, a confirmation buffer below exit for a SELL

    @pytest.mark.asyncio
    async def test_evidence_ids_carried_through(self):
        pos = _make_pos()
        with patch("paper_trading.trade_simulator.close_paper_trade",
                    AsyncMock(return_value=_closed_trade(event_id=2848, evidence_ids=["2848", "extra"]))):
            session = AsyncMock()
            session.add = MagicMock()
            await _t1_reversal_exit(pos, 110.0, {"decision": "EXIT", "reasoning": "x", "watch_level": 105.0}, session)
        added = session.add.call_args[0][0]
        assert added.evidence_ids == ["2848", "extra"]
