"""Regression test for paper_trading/trade_simulator.py's post-event reversal
stop-tightening (added 2026-08-06).

EPACKPEB.NS incident: PRE_EVENT_EXPECTATION_GAP bought ahead of a quarterly
result with nowcast POSITIVE (conf 0.10). The result reaction gapped down
~7%, directly contradicting the nowcast -- and the position just sat on its
original, wide entry-time stop-loss for 6+ days with nothing re-evaluating
the thesis once the event it was betting on had actually resolved.

This test exercises the new post-event-reversal block inside
update_positions_with_current_prices(): once `event_date` (from
indicator_snapshot.confidence_factors) is in the past and the live P&L has
moved >=3% against the nowcast's predicted direction, the position's
stop-loss is tightened to the midpoint between current price and the
original stop -- once per position (`trade_mgmt.post_event_handled`) -- and
never loosened. A subsequent close triggered by that tightened stop reports
exit_reason="POST_EVENT_REVERSAL" instead of the generic "STOP_LOSS", so it's
distinguishable in trade history / Telegram alerts.

Follows the same mocking pattern as test_trade_simulator_confirmation_lost.py
(no prior coverage existed for this whole function before that file).
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


def _fake_position(
    *,
    strategy_name="PRE_EVENT_EXPECTATION_GAP",
    entry_price=100.0,
    stop_loss=70.0,
    take_profit=200.0,
    event_date=None,
    nowcast_direction="POSITIVE",
    direction=TradeDirection.BUY,
    trade_mgmt_extra=None,
    confidence_factors_override=None,
):
    if confidence_factors_override is not None:
        cf = confidence_factors_override
    else:
        cf = {}
        if event_date is not None:
            cf["event_date"] = event_date
        if nowcast_direction is not None:
            cf["nowcast_direction"] = nowcast_direction

    snap = {
        "confidence_factors": cf,
        "trade_mgmt": {**(trade_mgmt_extra or {})},
    }
    trade = SimpleNamespace(
        strategy_name=strategy_name,
        indicator_snapshot=snap,
        stop_loss=stop_loss,
        opened_at=datetime.utcnow() - timedelta(days=6),
    )
    pos = SimpleNamespace(
        id=1, trade_id=42, symbol="EPACKPEB.NS", direction=direction,
        entry_price=entry_price, current_price=entry_price,
        stop_loss=stop_loss, take_profit=take_profit,
        size_units=10.0, instrument_type="EQUITY", unrealised_pnl=0.0,
        opened_at=datetime.utcnow() - timedelta(days=6),
        trade=trade,
    )
    return pos


def _session_for(pos):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[pos])))))
    session.begin_nested = MagicMock(return_value=_NestedTxn())
    return session


def _patches(price: float):
    return [
        patch("crawler.zerodha_market.get_live_prices", AsyncMock(return_value={
            "EPACKPEB.NS": {"price": price}})),
        patch("engine.intelligence_hub.build_sector_context",
              MagicMock(return_value=SimpleNamespace(sector_moods={}))),
        patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
              AsyncMock(return_value={"balance": 1_000_000.0})),
        patch("paper_trading.virtual_wallet.VirtualWallet.update_unrealised_pnl", AsyncMock()),
    ]


_PAST_EVENT = (datetime.utcnow() - timedelta(days=3)).date().isoformat()
_FUTURE_EVENT = (datetime.utcnow() + timedelta(days=3)).date().isoformat()


class TestPostEventReversalExit:
    @pytest.mark.asyncio
    async def test_adverse_reaction_after_event_tightens_stop_without_closing(self):
        """entry=100, stop=70, price=90 (-10%, past the -3% threshold, against
        a POSITIVE nowcast). New stop = (90+70)/2 = 80 -- tighter than 70, but
        price (90) hasn't reached it yet, so the position stays open."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, event_date=_PAST_EVENT)
        session = _session_for(pos)
        patches = _patches(90.0)
        with patches[0], patches[1], patches[2], patches[3]:
            auto_closed = await ts.update_positions_with_current_prices(session)

        assert auto_closed == []
        assert pos.stop_loss == 80.0
        assert pos.trade.stop_loss == 80.0
        assert pos.trade.indicator_snapshot["trade_mgmt"]["post_event_handled"] is True

    @pytest.mark.asyncio
    async def test_tightened_stop_later_hit_reports_post_event_reversal(self):
        """Cycle 1 tightens 70 -> 80 (price 90, doesn't close). Cycle 2: price
        drifts to 79, now below the TIGHTENED stop -- closes, and the reason
        must be POST_EVENT_REVERSAL, not the generic STOP_LOSS, since the
        close only happened because of this rule's tightened stop."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, event_date=_PAST_EVENT)
        session = _session_for(pos)

        p1 = _patches(90.0)
        with p1[0], p1[1], p1[2], p1[3]:
            await ts.update_positions_with_current_prices(session)
        assert pos.stop_loss == 80.0

        closed_trade = SimpleNamespace(
            id=42, symbol="EPACKPEB.NS", pnl=-210.0, entry_price=100.0, size_units=10.0,
        )
        p2 = _patches(79.0)
        with p2[0], p2[1], p2[2], p2[3], \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=closed_trade)) as mock_close:
            auto_closed = await ts.update_positions_with_current_prices(session)

        mock_close.assert_called_once()
        assert mock_close.call_args.args[2] == "POST_EVENT_REVERSAL"
        assert len(auto_closed) == 1
        assert auto_closed[0]["reason"] == "POST_EVENT_REVERSAL"

    @pytest.mark.asyncio
    async def test_favorable_reaction_leaves_stop_untouched(self):
        """Price UP 10% after the event, matching the POSITIVE nowcast --
        nothing adverse happened, so the stop must not move."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, event_date=_PAST_EVENT)
        session = _session_for(pos)
        patches = _patches(110.0)
        with patches[0], patches[1], patches[2], patches[3]:
            await ts.update_positions_with_current_prices(session)

        assert pos.stop_loss == 70.0
        assert not pos.trade.indicator_snapshot["trade_mgmt"].get("post_event_handled")

    @pytest.mark.asyncio
    async def test_event_still_in_the_future_is_not_touched(self):
        """Event date hasn't arrived yet -- this is still genuinely
        pre-event, even if price has already dipped >=3%."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, event_date=_FUTURE_EVENT)
        session = _session_for(pos)
        patches = _patches(90.0)
        with patches[0], patches[1], patches[2], patches[3]:
            await ts.update_positions_with_current_prices(session)

        assert pos.stop_loss == 70.0
        assert not pos.trade.indicator_snapshot["trade_mgmt"].get("post_event_handled")

    @pytest.mark.asyncio
    async def test_missing_event_metadata_is_a_safe_noop(self):
        """Legacy / pre-fix trades have no event_date or nowcast_direction in
        confidence_factors -- must not crash, must not touch the stop."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, confidence_factors_override={})
        session = _session_for(pos)
        patches = _patches(90.0)
        with patches[0], patches[1], patches[2], patches[3]:
            auto_closed = await ts.update_positions_with_current_prices(session)

        assert auto_closed == []
        assert pos.stop_loss == 70.0

    @pytest.mark.asyncio
    async def test_non_pre_event_gap_strategy_is_never_touched(self):
        """Scoping check: a DIRECT_NEWS position must never hit this block,
        even with event-shaped metadata sitting in its snapshot."""
        pos = _fake_position(strategy_name="DIRECT_NEWS", entry_price=100.0,
                              stop_loss=70.0, event_date=_PAST_EVENT)
        session = _session_for(pos)
        patches = _patches(90.0)
        with patches[0], patches[1], patches[2], patches[3]:
            await ts.update_positions_with_current_prices(session)

        assert pos.stop_loss == 70.0
        assert not pos.trade.indicator_snapshot["trade_mgmt"].get("post_event_handled")

    @pytest.mark.asyncio
    async def test_second_cycle_at_unchanged_price_does_not_retighten(self):
        """Once post_event_handled is set, a second cycle at the same price
        must not recompute/re-tighten again (one-shot, not every-cycle)."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, event_date=_PAST_EVENT)
        session = _session_for(pos)

        p1 = _patches(90.0)
        with p1[0], p1[1], p1[2], p1[3]:
            await ts.update_positions_with_current_prices(session)
        assert pos.stop_loss == 80.0

        p2 = _patches(90.0)
        with p2[0], p2[1], p2[2], p2[3]:
            await ts.update_positions_with_current_prices(session)
        assert pos.stop_loss == 80.0  # unchanged, not re-tightened to (90+80)/2=85
