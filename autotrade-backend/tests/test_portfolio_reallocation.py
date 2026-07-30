"""Regression tests for engine/portfolio_reallocation.py (added 2026-07-29,
user request): a PURE reallocation trigger, not general loss-cutting --
when a good new candidate is blocked by capital constraints, sell the
single worst open position whose OWN strategy no longer endorses it,
regardless of whether its stop-loss or Target 1 has fired. A position that's
down but whose thesis still holds must never be touched by this mechanism.

All tests are deterministic and mocked -- no network, no real DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import engine.portfolio_reallocation as pr
from db.models import TradeDirection


class _NestedTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _fake_position(id_, symbol, unrealised_pct, strategy_name="DIRECT_NEWS",
                    opened_at=None, direction=TradeDirection.BUY):
    trade = SimpleNamespace(strategy_name=strategy_name)
    return SimpleNamespace(
        id=id_, trade_id=id_, symbol=symbol, direction=direction,
        entry_price=100.0, current_price=95.0, stop_loss=90.0,
        unrealised_pct=unrealised_pct, size_units=10.0,
        opened_at=opened_at or (datetime.utcnow() - timedelta(hours=1)),
        trade=trade,
    )


def _session_returning(positions):
    """A fake session whose .execute() returns `positions` for the
    re-fetch-with-selectinload query inside try_reallocate_for_candidate."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=positions)))))
    session.begin_nested = MagicMock(return_value=_NestedTxn())
    return session


class TestEligibilityFiltering:
    @pytest.mark.asyncio
    async def test_healthy_position_never_considered(self):
        """A position that's flat/profitable must never be touched, even if
        its thesis would come back invalid -- filtered out before any
        thesis re-check is even attempted."""
        pos = _fake_position(1, "FOO.NS", unrealised_pct=1.5)  # profitable
        session = _session_returning([pos])
        with patch("engine.portfolio_reallocation._thesis_still_valid") as mock_thesis:
            freed = await pr.try_reallocate_for_candidate([pos], session)
        assert freed is False
        mock_thesis.assert_not_called()

    @pytest.mark.asyncio
    async def test_small_loss_below_threshold_not_considered(self):
        pos = _fake_position(1, "FOO.NS", unrealised_pct=-1.0)  # above -2% floor
        session = _session_returning([pos])
        with patch("engine.portfolio_reallocation._thesis_still_valid") as mock_thesis:
            freed = await pr.try_reallocate_for_candidate([pos], session)
        assert freed is False
        mock_thesis.assert_not_called()

    @pytest.mark.asyncio
    async def test_recently_opened_position_not_yet_eligible(self):
        """A big loser closed minutes after opening would be thrashing, not
        reallocation -- must wait out MIN_HOLD_BEFORE_ELIGIBLE."""
        pos = _fake_position(1, "FOO.NS", unrealised_pct=-10.0,
                              opened_at=datetime.utcnow() - timedelta(minutes=2))
        session = _session_returning([pos])
        with patch("engine.portfolio_reallocation._thesis_still_valid") as mock_thesis:
            freed = await pr.try_reallocate_for_candidate([pos], session)
        assert freed is False
        mock_thesis.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_open_positions_returns_false_immediately(self):
        session = _session_returning([])
        freed = await pr.try_reallocate_for_candidate([], session)
        assert freed is False


class TestThesisReCheck:
    @pytest.mark.asyncio
    async def test_still_valid_thesis_is_never_closed(self):
        """Down a lot, but the position's own strategy STILL endorses it --
        must be left alone. This is the core guarantee: reallocation never
        becomes general loss-cutting."""
        pos = _fake_position(1, "FOO.NS", unrealised_pct=-8.0, strategy_name="DIRECT_NEWS")
        session = _session_returning([pos])
        with patch("engine.portfolio_reallocation._thesis_still_valid", AsyncMock(return_value=True)), \
             patch("paper_trading.trade_simulator.close_paper_trade") as mock_close:
            freed = await pr.try_reallocate_for_candidate([pos], session)
        assert freed is False
        mock_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_unverifiable_thesis_is_never_closed(self):
        """_thesis_still_valid returning None (couldn't verify) must be
        treated the same as 'still valid' -- fail toward NOT selling."""
        pos = _fake_position(1, "FOO.NS", unrealised_pct=-8.0, strategy_name="PRE_EVENT_EXPECTATION_GAP")
        session = _session_returning([pos])
        with patch("engine.portfolio_reallocation._thesis_still_valid", AsyncMock(return_value=None)), \
             patch("paper_trading.trade_simulator.close_paper_trade") as mock_close:
            freed = await pr.try_reallocate_for_candidate([pos], session)
        assert freed is False
        mock_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalidated_thesis_closes_with_reallocated_reason(self):
        pos = _fake_position(1, "FOO.NS", unrealised_pct=-8.0, strategy_name="DIRECT_NEWS")
        session = _session_returning([pos])
        closed_trade = SimpleNamespace(id=1, symbol="FOO.NS", pnl=-800.0)
        with patch("engine.portfolio_reallocation._thesis_still_valid", AsyncMock(return_value=False)), \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=closed_trade)) as mock_close:
            freed = await pr.try_reallocate_for_candidate([pos], session)
        assert freed is True
        mock_close.assert_called_once()
        assert mock_close.call_args.args[0] is pos
        assert mock_close.call_args.args[2] == "REALLOCATED"

    @pytest.mark.asyncio
    async def test_unsupported_strategy_never_selected(self):
        """A legacy trade with no strategy_name (pre-dates strategy
        tagging) must never be picked for reallocation -- v1 only supports
        PRE_EVENT_EXPECTATION_GAP and DIRECT_NEWS."""
        pos = _fake_position(1, "FOO.NS", unrealised_pct=-8.0, strategy_name=None)
        session = _session_returning([pos])
        with patch("engine.portfolio_reallocation._thesis_still_valid") as mock_thesis, \
             patch("paper_trading.trade_simulator.close_paper_trade") as mock_close:
            freed = await pr.try_reallocate_for_candidate([pos], session)
        assert freed is False
        mock_thesis.assert_not_called()
        mock_close.assert_not_called()


class TestWorstFirstSelection:
    @pytest.mark.asyncio
    async def test_worst_performing_eligible_position_is_checked_first(self):
        """Two losing positions; only the WORSE one's thesis should be
        re-checked first -- if it's invalidated, the better-performing loser
        is never touched at all."""
        worse  = _fake_position(1, "WORSE.NS", unrealised_pct=-9.0)
        better = _fake_position(2, "BETTER.NS", unrealised_pct=-3.0)
        session = _session_returning([worse, better])
        closed_trade = SimpleNamespace(id=1, symbol="WORSE.NS", pnl=-900.0)

        async def _thesis(pos, _session):
            assert pos.symbol == "WORSE.NS"  # must be checked first
            return False

        with patch("engine.portfolio_reallocation._thesis_still_valid", _thesis), \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=closed_trade)) as mock_close:
            freed = await pr.try_reallocate_for_candidate([worse, better], session)
        assert freed is True
        assert mock_close.call_args.args[0].symbol == "WORSE.NS"

    @pytest.mark.asyncio
    async def test_falls_through_to_next_worst_if_worst_thesis_still_valid(self):
        worse  = _fake_position(1, "WORSE.NS", unrealised_pct=-9.0)
        better = _fake_position(2, "BETTER.NS", unrealised_pct=-3.0)
        session = _session_returning([worse, better])
        closed_trade = SimpleNamespace(id=2, symbol="BETTER.NS", pnl=-300.0)

        async def _thesis(pos, _session):
            return pos.symbol != "BETTER.NS"  # only BETTER.NS is invalidated

        with patch("engine.portfolio_reallocation._thesis_still_valid", _thesis), \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=closed_trade)) as mock_close:
            freed = await pr.try_reallocate_for_candidate([worse, better], session)
        assert freed is True
        assert mock_close.call_args.args[0].symbol == "BETTER.NS"

    @pytest.mark.asyncio
    async def test_at_most_one_position_closed_per_call(self):
        worse  = _fake_position(1, "WORSE.NS", unrealised_pct=-9.0)
        better = _fake_position(2, "BETTER.NS", unrealised_pct=-3.0)
        session = _session_returning([worse, better])
        closed_trade = SimpleNamespace(id=1, symbol="WORSE.NS", pnl=-900.0)

        with patch("engine.portfolio_reallocation._thesis_still_valid", AsyncMock(return_value=False)), \
             patch("paper_trading.trade_simulator.close_paper_trade",
                   AsyncMock(return_value=closed_trade)) as mock_close:
            await pr.try_reallocate_for_candidate([worse, better], session)
        assert mock_close.call_count == 1


class TestThesisReCheckDispatch:
    """_thesis_still_valid()'s own per-strategy dispatch, unmocked."""

    @pytest.mark.asyncio
    async def test_pre_event_long_verdict_is_still_valid(self):
        pos = _fake_position(1, "FOO.NS", -5.0, strategy_name="PRE_EVENT_EXPECTATION_GAP")
        from engine.pre_event_expectation_gap.types import PreEventDecision
        prediction = SimpleNamespace(decision=PreEventDecision.LONG)
        with patch("engine.pre_event_expectation_gap.engine.scan",
                   AsyncMock(return_value=[prediction])):
            result = await pr._thesis_still_valid(pos, AsyncMock())
        assert result is True

    @pytest.mark.asyncio
    async def test_pre_event_no_trade_verdict_is_invalidated(self):
        pos = _fake_position(1, "FOO.NS", -5.0, strategy_name="PRE_EVENT_EXPECTATION_GAP")
        from engine.pre_event_expectation_gap.types import PreEventDecision
        prediction = SimpleNamespace(decision=PreEventDecision.NO_TRADE)
        with patch("engine.pre_event_expectation_gap.engine.scan",
                   AsyncMock(return_value=[prediction])):
            result = await pr._thesis_still_valid(pos, AsyncMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_pre_event_no_event_found_is_unverifiable(self):
        pos = _fake_position(1, "FOO.NS", -5.0, strategy_name="PRE_EVENT_EXPECTATION_GAP")
        with patch("engine.pre_event_expectation_gap.engine.scan", AsyncMock(return_value=[])):
            result = await pr._thesis_still_valid(pos, AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_direct_news_confirmed_is_still_valid(self):
        pos = _fake_position(1, "FOO.NS", -5.0, strategy_name="DIRECT_NEWS")
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=object())), \
             patch("engine.entry_confirmation.check_price_volume_confirmation",
                   return_value=(True, "confirmed")):
            result = await pr._thesis_still_valid(pos, AsyncMock())
        assert result is True

    @pytest.mark.asyncio
    async def test_direct_news_unconfirmed_is_invalidated(self):
        pos = _fake_position(1, "FOO.NS", -5.0, strategy_name="DIRECT_NEWS")
        with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=object())), \
             patch("engine.entry_confirmation.check_price_volume_confirmation",
                   return_value=(False, "faded")):
            result = await pr._thesis_still_valid(pos, AsyncMock())
        assert result is False

    @pytest.mark.asyncio
    async def test_unsupported_strategy_is_unverifiable(self):
        pos = _fake_position(1, "FOO.NS", -5.0, strategy_name="SOME_OTHER_STRATEGY")
        result = await pr._thesis_still_valid(pos, AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_thesis_check_exception_fails_open_unverifiable(self):
        pos = _fake_position(1, "FOO.NS", -5.0, strategy_name="DIRECT_NEWS")
        with patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(side_effect=RuntimeError("network down"))):
            result = await pr._thesis_still_valid(pos, AsyncMock())
        assert result is None
