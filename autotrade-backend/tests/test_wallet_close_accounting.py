"""Regression — closing a trade must not leave phantom unrealised P&L.

`return_margin` credited realised P&L but reused the stale
`wallet.unrealised_pnl`, which still held the just-closed position's final
mark-to-market value. Since a closed position's last `current_price` IS its exit
price, that phantom equals the trade's GROSS pnl exactly — so the same trade was
counted twice.

Observed live 2026-08-19: one trade closed +145.51 net / +181.73 gross with no
other position open, and the daily snapshot stored equity
500,327.23 == 500,000 + 145.51 + 181.73. Daily P&L read 327.23 instead of
145.51. take_daily_snapshot() runs on-demand right after a close, so this fired
on every close, and the inflated equity propagated into peak_balance —
understating max_drawdown, which is a risk-control input.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paper_trading.virtual_wallet import VirtualWallet


def _wallet(balance=500_000.0, realised=0.0, unrealised=0.0):
    w = MagicMock()
    w.balance = balance
    w.realised_pnl = realised
    w.unrealised_pnl = unrealised
    w.equity = balance
    w.total_trades = 0
    w.winning_trades = 0
    w.peak_balance = balance
    w.max_drawdown = 0.0
    return w


def _session(open_positions_unrealised: float):
    """Session whose SUM(open_positions.unrealised_pnl) returns the given total."""
    s = MagicMock()
    res = MagicMock()
    res.scalar.return_value = open_positions_unrealised
    s.execute = AsyncMock(return_value=res)
    s.flush = AsyncMock()
    s.add = MagicMock()
    return s


class TestNoPhantomUnrealisedAfterClose:

    @pytest.mark.asyncio
    async def test_last_position_closed_zeroes_unrealised(self):
        """The exact 2026-08-19 case: only position closes, nothing left open."""
        wallet = _wallet(unrealised=181.7271)   # stale value from the closing position
        session = _session(0.0)                 # nothing open any more

        with patch.object(VirtualWallet, "get_or_create", AsyncMock(return_value=wallet)), \
             patch.object(VirtualWallet, "_start_balance", AsyncMock(return_value=500_000.0)), \
             patch.object(VirtualWallet, "_simlog", AsyncMock()), \
             patch.object(VirtualWallet, "_log", MagicMock()):
            await VirtualWallet.return_margin(session, 12_406.6872, 145.5071, "TURTLEMINT.NS")

        assert wallet.unrealised_pnl == 0.0, "phantom unrealised survived the close"
        assert wallet.equity == pytest.approx(500_145.5071), (
            f"equity {wallet.equity} should be start + realised only; "
            f"500,327.23 would be the double-counted value"
        )

    @pytest.mark.asyncio
    async def test_remaining_positions_are_still_counted(self):
        """Closing one of several must keep the others' unrealised."""
        wallet = _wallet(unrealised=999.0)      # stale
        session = _session(250.0)               # what is genuinely still open

        with patch.object(VirtualWallet, "get_or_create", AsyncMock(return_value=wallet)), \
             patch.object(VirtualWallet, "_start_balance", AsyncMock(return_value=500_000.0)), \
             patch.object(VirtualWallet, "_simlog", AsyncMock()), \
             patch.object(VirtualWallet, "_log", MagicMock()):
            await VirtualWallet.return_margin(session, 10_000.0, 100.0, "X.NS")

        assert wallet.unrealised_pnl == pytest.approx(250.0)
        assert wallet.equity == pytest.approx(500_000.0 + 100.0 + 250.0)

    @pytest.mark.asyncio
    async def test_realised_and_balance_still_credited(self):
        """The fix must not disturb what return_margin already did correctly."""
        wallet = _wallet()
        session = _session(0.0)

        with patch.object(VirtualWallet, "get_or_create", AsyncMock(return_value=wallet)), \
             patch.object(VirtualWallet, "_start_balance", AsyncMock(return_value=500_000.0)), \
             patch.object(VirtualWallet, "_simlog", AsyncMock()), \
             patch.object(VirtualWallet, "_log", MagicMock()):
            await VirtualWallet.return_margin(session, 10_000.0, 145.5071, "X.NS")

        assert wallet.balance == pytest.approx(510_145.5071)   # margin + pnl returned
        assert wallet.realised_pnl == pytest.approx(145.5071)
        assert wallet.total_trades == 1
        assert wallet.winning_trades == 1

    @pytest.mark.asyncio
    async def test_flushes_before_reading_open_positions(self):
        """autoflush=False, so a pending DELETE would otherwise be invisible."""
        wallet = _wallet(unrealised=500.0)
        session = _session(0.0)

        with patch.object(VirtualWallet, "get_or_create", AsyncMock(return_value=wallet)), \
             patch.object(VirtualWallet, "_start_balance", AsyncMock(return_value=500_000.0)), \
             patch.object(VirtualWallet, "_simlog", AsyncMock()), \
             patch.object(VirtualWallet, "_log", MagicMock()):
            await VirtualWallet.return_margin(session, 1_000.0, 10.0, "X.NS")

        assert session.flush.await_count >= 1, "must flush so pending deletes are visible"
