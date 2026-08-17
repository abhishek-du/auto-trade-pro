"""Regression tests for paper_trading/trade_simulator.py's post-event exits.

History
-------
2026-08-06 (EPACKPEB.NS): PRE_EVENT_EXPECTATION_GAP bought ahead of a quarterly
result with nowcast POSITIVE (conf 0.10). The reaction gapped down ~7%,
contradicting the nowcast, and the position sat on its original wide stop for
6+ days with nothing re-evaluating the thesis. The first fix TIGHTENED the stop
to the midpoint between price and the original stop, once per position.

2026-08-17 (forensic post-mortem, docs/2026-08-17_FORENSIC_POST_MORTEM.md):
that gentle version was measured over 10 live firings and went **0-for-10 for
-16,275**. Because it only triggers once a position is already >=3% under water
and then merely halves the remaining room, it is mathematically incapable of
producing a winner -- a "lose more slowly" device. It was replaced by two
exits that actually close the position:

  P0-2  POST_EVENT_REVERSAL  -- event resolved against the nowcast -> exit now.
  P0-1  POST_EVENT_TIME_EXIT -- >2 TRADING days past the event -> exit
        regardless of direction. All of the strategy's profit is made in the
        0-2 day post-event window (+30,432); everything held longer returned
        -27,885.

These tests therefore assert CLOSURE, not stop mutation. A test that expects
the stop to be tightened is asserting the old, disproven behaviour.

Follows the same mocking pattern as test_trade_simulator_confirmation_lost.py.
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


def _event_n_trading_days_ago(n: int) -> str:
    """Event date that is exactly `n` NSE trading days before today (IST).

    Computed via the production helper rather than a fixed calendar offset so
    these tests can't go flaky depending on which weekday they run — a plain
    "3 days ago" is 3 trading days midweek but only 1 across a weekend, which
    would silently flip the P0-1 time-based exit on and off.
    """
    today = (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()
    d = today
    while ts._trading_days_since(d, today) < n:
        d -= timedelta(days=1)
    return d.isoformat()


# 1 trading day back: past the event, but still INSIDE the 2-day hold window,
# so only the adverse (P0-2) rule can fire here.
_PAST_EVENT = _event_n_trading_days_ago(1)
# 3 trading days back: outside the window, so the P0-1 time exit fires.
_STALE_EVENT = _event_n_trading_days_ago(3)
_FUTURE_EVENT = (datetime.utcnow() + timedelta(days=3)).date().isoformat()


def _closed_stub():
    return SimpleNamespace(
        id=42, symbol="EPACKPEB.NS", pnl=-210.0, entry_price=100.0, size_units=10.0,
    )


class TestPostEventReversalExit:
    @pytest.mark.asyncio
    async def test_adverse_reaction_after_event_exits_immediately(self):
        """P0-2 (2026-08-17): entry=100, stop=70, price=90 (-10%, past the -3%
        threshold, against a POSITIVE nowcast) -> close NOW at the live price.

        Supersedes the original stop-tightening behaviour, which went 0-for-10
        for -16,275 live because it could only fire once a position was already
        >=3% down and then merely halved the remaining stop room -- it could
        never produce a winner. The exit must happen on the FIRST cycle that
        sees the adverse reaction, not after a second cycle drifts into a
        tightened stop."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, event_date=_PAST_EVENT)
        session = _session_for(pos)
        patches = _patches(90.0)
        with patches[0], patches[1], patches[2], patches[3], \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=_closed_stub())) as mock_close:
            auto_closed = await ts.update_positions_with_current_prices(session)

        mock_close.assert_called_once()
        assert mock_close.call_args.args[2] == "POST_EVENT_REVERSAL"
        assert mock_close.call_args.args[1] == 90.0        # exits at the live price
        assert len(auto_closed) == 1
        assert auto_closed[0]["reason"] == "POST_EVENT_REVERSAL"
        # and it must NOT have quietly tightened the stop on the way out
        assert pos.stop_loss == 70.0

    @pytest.mark.asyncio
    async def test_stale_position_time_exits_even_when_profitable(self):
        """P0-1 (2026-08-17): once the event is >2 TRADING days old the position
        is closed regardless of direction -- here it's +10% and matching the
        nowcast, so the adverse rule does NOT apply, yet it still exits.

        Rationale (forensic post-mortem): exits <=2 days post-event returned
        +30,432 while everything held longer returned -27,885. Holding past the
        window is unprofitable whichever way the trade is currently pointing."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, event_date=_STALE_EVENT)
        session = _session_for(pos)
        patches = _patches(110.0)
        with patches[0], patches[1], patches[2], patches[3], \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=_closed_stub())) as mock_close:
            auto_closed = await ts.update_positions_with_current_prices(session)

        mock_close.assert_called_once()
        assert mock_close.call_args.args[2] == "POST_EVENT_TIME_EXIT"
        assert len(auto_closed) == 1
        assert auto_closed[0]["reason"] == "POST_EVENT_TIME_EXIT"

    @pytest.mark.asyncio
    async def test_trading_days_helper_skips_weekends(self):
        """The window is in TRADING days, not calendar days: a Friday event
        checked the following Monday is 1 day elapsed, not 3 -- otherwise every
        position spanning a weekend would be force-exited a session early."""
        from datetime import date
        friday, monday = date(2026, 8, 14), date(2026, 8, 17)
        assert friday.weekday() == 4 and monday.weekday() == 0
        assert ts._trading_days_since(friday, monday) == 1
        # same date / future date must never report elapsed time
        assert ts._trading_days_since(monday, monday) == 0
        assert ts._trading_days_since(monday, friday) == 0

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
    async def test_within_window_and_favorable_is_left_alone(self):
        """The complement of the two exit rules: inside the 2-trading-day
        window AND moving with the nowcast -> the position must be left to run.
        This is the cohort that produced all of the strategy's profit, so it
        must not be swept up by either new exit."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0, event_date=_PAST_EVENT)
        session = _session_for(pos)
        patches = _patches(110.0)
        with patches[0], patches[1], patches[2], patches[3], \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=_closed_stub())) as mock_close:
            auto_closed = await ts.update_positions_with_current_prices(session)

        mock_close.assert_not_called()
        assert auto_closed == []
        assert pos.stop_loss == 70.0

    @pytest.mark.asyncio
    async def test_stale_event_exits_even_without_nowcast_direction(self):
        """Legacy rows may carry event_date but no nowcast_direction. The
        adverse rule can't evaluate without it, but the time-based exit must
        still apply -- otherwise those positions would hold forever."""
        pos = _fake_position(entry_price=100.0, stop_loss=70.0,
                             event_date=_STALE_EVENT, nowcast_direction=None)
        session = _session_for(pos)
        patches = _patches(95.0)
        with patches[0], patches[1], patches[2], patches[3], \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=_closed_stub())) as mock_close:
            auto_closed = await ts.update_positions_with_current_prices(session)

        mock_close.assert_called_once()
        assert mock_close.call_args.args[2] == "POST_EVENT_TIME_EXIT"
        assert len(auto_closed) == 1
