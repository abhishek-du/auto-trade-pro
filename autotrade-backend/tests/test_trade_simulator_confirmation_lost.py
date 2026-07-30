"""Regression test for paper_trading/trade_simulator.py's DIRECT_NEWS
post-entry re-confirmation exit (added 2026-07-29).

CARTRADE.NS incident: news hit ~9:29 IST, price spiked to ₹3066 (genuinely
passing the entry-time check_price_volume_confirmation() gate), then
reversed and kept falling to ₹2800 -- nothing re-checked whether that early
move was still holding before Target 1. This test exercises the new
CONFIRMATION_LOST early-exit block inside
update_positions_with_current_prices(), which re-runs the same entry-time
check periodically during the first _DIRECT_NEWS_RECHECK_WINDOW after entry.

update_positions_with_current_prices() has no prior test coverage at all
(confirmed: SECTOR_REVERSAL and T1_REVERSAL_EXIT, its two existing sibling
proactive-exit paths, are also untested) -- this test mocks just enough of
its many external dependencies (live price fetch, sector context, the new
block's own confirmation check, close_paper_trade) to exercise the new
block in isolation without touching a real DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import paper_trading.trade_simulator as ts
from db.models import TradeDirection


class _NestedTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_position(*, strategy_name="DIRECT_NEWS", opened_at=None, direction=TradeDirection.BUY):
    trade = SimpleNamespace(
        strategy_name=strategy_name,
        indicator_snapshot={},
        stop_loss=90.0,
        opened_at=opened_at or (datetime.utcnow() - timedelta(minutes=30)),
    )
    pos = SimpleNamespace(
        id=1, trade_id=42, symbol="CARTRADE.NS", direction=direction,
        entry_price=100.0, current_price=100.0, stop_loss=90.0, take_profit=150.0,
        size_units=10.0, instrument_type="EQUITY", unrealised_pnl=0.0,
        opened_at=opened_at or (datetime.utcnow() - timedelta(minutes=30)),
        trade=trade,
    )
    return pos


@pytest.fixture(autouse=True)
def _reset_recheck_state():
    ts._DIRECT_NEWS_RECHECK_STATE.clear()
    yield
    ts._DIRECT_NEWS_RECHECK_STATE.clear()


class TestConfirmationLostExit:
    @pytest.mark.asyncio
    async def test_faded_direct_news_position_closed_with_confirmation_lost(self):
        pos = _fake_position()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[pos])))))
        session.begin_nested = MagicMock(return_value=_NestedTxn())

        closed_trade = SimpleNamespace(
            id=42, symbol="CARTRADE.NS", pnl=-500.0, entry_price=100.0, size_units=10.0,
        )

        with patch("crawler.zerodha_market.get_live_prices", AsyncMock(return_value={
                 "CARTRADE.NS": {"price": 93.0}})), \
             patch("engine.intelligence_hub.build_sector_context",
                   MagicMock(return_value=SimpleNamespace(sector_moods={}))), \
             patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=object())), \
             patch("engine.entry_confirmation.check_price_volume_confirmation",
                   return_value=(False, "price only +0.10% on the day — not enough follow-through")), \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=closed_trade)) as mock_close, \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
                   AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("paper_trading.virtual_wallet.VirtualWallet.update_unrealised_pnl", AsyncMock()):
            auto_closed = await ts.update_positions_with_current_prices(session)

        mock_close.assert_called_once()
        call_args = mock_close.call_args.args
        assert call_args[0] is pos
        assert call_args[2] == "CONFIRMATION_LOST"
        assert len(auto_closed) == 1
        assert auto_closed[0]["reason"] == "CONFIRMATION_LOST"

    @pytest.mark.asyncio
    async def test_still_confirmed_position_is_not_closed(self):
        pos = _fake_position()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[pos])))))

        with patch("crawler.zerodha_market.get_live_prices", AsyncMock(return_value={
                 "CARTRADE.NS": {"price": 105.0}})), \
             patch("engine.intelligence_hub.build_sector_context",
                   MagicMock(return_value=SimpleNamespace(sector_moods={}))), \
             patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=object())), \
             patch("engine.entry_confirmation.check_price_volume_confirmation",
                   return_value=(True, "confirmed: 2.0% day move")), \
             patch("paper_trading.trade_simulator.close_paper_trade") as mock_close, \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
                   AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("paper_trading.virtual_wallet.VirtualWallet.update_unrealised_pnl", AsyncMock()):
            await ts.update_positions_with_current_prices(session)

        mock_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_direct_news_strategy_is_never_rechecked(self):
        """Scoping check: a PRE_EVENT_EXPECTATION_GAP position should never
        hit this block at all, even if its price has technically faded --
        this early-exit is DIRECT_NEWS-specific."""
        pos = _fake_position(strategy_name="PRE_EVENT_EXPECTATION_GAP")
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[pos])))))

        with patch("crawler.zerodha_market.get_live_prices", AsyncMock(return_value={
                 "CARTRADE.NS": {"price": 93.0}})), \
             patch("engine.intelligence_hub.build_sector_context",
                   MagicMock(return_value=SimpleNamespace(sector_moods={}))), \
             patch("engine.entry_confirmation.check_price_volume_confirmation") as mock_check, \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
                   AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("paper_trading.virtual_wallet.VirtualWallet.update_unrealised_pnl", AsyncMock()):
            await ts.update_positions_with_current_prices(session)

        mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_position_outside_recheck_window_is_skipped(self):
        """A DIRECT_NEWS position held longer than _DIRECT_NEWS_RECHECK_WINDOW
        falls back to normal SL/TP handling -- no re-confirmation attempted."""
        pos = _fake_position(opened_at=datetime.utcnow() - timedelta(hours=5))
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[pos])))))

        with patch("crawler.zerodha_market.get_live_prices", AsyncMock(return_value={
                 "CARTRADE.NS": {"price": 93.0}})), \
             patch("engine.intelligence_hub.build_sector_context",
                   MagicMock(return_value=SimpleNamespace(sector_moods={}))), \
             patch("engine.entry_confirmation.check_price_volume_confirmation") as mock_check, \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
                   AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("paper_trading.virtual_wallet.VirtualWallet.update_unrealised_pnl", AsyncMock()):
            await ts.update_positions_with_current_prices(session)

        mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_recheck_is_not_repeated_within_the_cooldown_interval(self):
        """Second call within _DIRECT_NEWS_RECHECK_INTERVAL_SEC of the first
        should not re-hit the network check again for the same trade_id."""
        pos = _fake_position()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[pos])))))

        with patch("crawler.zerodha_market.get_live_prices", AsyncMock(return_value={
                 "CARTRADE.NS": {"price": 105.0}})), \
             patch("engine.intelligence_hub.build_sector_context",
                   MagicMock(return_value=SimpleNamespace(sector_moods={}))), \
             patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=object())), \
             patch("engine.entry_confirmation.check_price_volume_confirmation",
                   return_value=(True, "confirmed: 2.0% day move")) as mock_check, \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
                   AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("paper_trading.virtual_wallet.VirtualWallet.update_unrealised_pnl", AsyncMock()):
            await ts.update_positions_with_current_prices(session)
            await ts.update_positions_with_current_prices(session)

        assert mock_check.call_count == 1
