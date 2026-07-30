"""Regression tests for engine/risk_manager.py::validate_signal (2026-07-29
changes, user request):

1. The position-count safety ceiling (Check 1a) was raised from 25 to 500 --
   it's a bug-guard, not the real limiter (MAX_PORTFOLIO_RISK/MIN_CASH_BUFFER
   already do that job independent of position count).
2. Checks 1b/1c (portfolio risk budget, cash buffer) now attempt ONE
   thesis-based reallocation (engine/portfolio_reallocation.py) before
   rejecting a capital-blocked candidate outright.

All tests are deterministic and mocked -- no network, no real DB.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.risk_manager import validate_signal
from engine.signal_generator import TradingSignal


def _signal(symbol="FOO.NS", confidence=70.0, entry=100.0, stop=95.0, target=115.0):
    return TradingSignal(
        symbol=symbol, timeframe="1h", action="BUY", confidence=confidence,
        entry_price=entry, stop_loss=stop, take_profit=target,
        pattern_score=0.0, indicator_score=0.0, sentiment_score=0.0, final_score=0.0,
    )


def _open_position(size_usd=50_000.0, entry=100.0, stop=95.0, size_units=10.0, unrealised_pnl=0.0,
                    symbol="OTHER.NS"):
    return SimpleNamespace(
        symbol=symbol, size_usd=size_usd, entry_price=entry, stop_loss=stop,
        size_units=size_units, unrealised_pnl=unrealised_pnl,
    )


@pytest.fixture(autouse=True)
def _no_daily_loss_and_no_other_gates():
    """Everything past checks 1a/1b/1c passes by default so each test only
    needs to exercise the one thing it's testing."""
    with patch("engine.risk_manager._today_closed_pnl", AsyncMock(return_value=0.0)), \
         patch("engine.risk_manager.RuntimeConfig.load", AsyncMock(return_value=SimpleNamespace(
             max_open_positions=500, max_daily_loss=0.05, min_risk_reward=0.0,
             max_portfolio_risk=0.15, min_cash_buffer=0.10,
         ))):
        yield


class TestPositionCountCeilingRelaxed:
    @pytest.mark.asyncio
    async def test_26_open_positions_no_longer_blocked(self):
        """Previously hard-capped at 25 -- must not reject at 26 now that
        the ceiling is 500."""
        # Small positions so checks 1b/1c don't also trip for unrelated reasons.
        positions = [_open_position(size_usd=1000.0, unrealised_pnl=0.0) for _ in range(26)]
        session = AsyncMock()
        ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                            open_positions=positions, session=session)
        assert ok is True, reason
        assert "Safety ceiling" not in reason


class TestReallocationHook:
    @pytest.mark.asyncio
    async def test_reallocation_not_attempted_when_capital_checks_pass(self):
        """No capital pressure at all -- the reallocation module must never
        even be imported/called."""
        positions = [_open_position(size_usd=1000.0)]
        session = AsyncMock()
        with patch("engine.portfolio_reallocation.try_reallocate_for_candidate") as mock_realloc:
            ok, _ = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                           open_positions=positions, session=session)
        assert ok is True
        mock_realloc.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_reallocation_lets_candidate_through(self):
        """Portfolio-risk-blocked candidate; reallocation frees a position;
        after recomputing, the SAME candidate is approved."""
        big_position = _open_position(size_usd=900_000.0, entry=1000.0, stop=100.0, size_units=1000.0)
        session = AsyncMock()
        # After reallocation "closes" the position, the re-fetch should
        # return an empty list (nothing left deployed).
        session.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

        with patch("engine.portfolio_reallocation.try_reallocate_for_candidate",
                   AsyncMock(return_value=True)) as mock_realloc:
            ok, reason = await validate_signal(
                _signal(confidence=90.0), wallet_balance=100_000.0,
                open_positions=[big_position], session=session,
            )
        assert ok is True, reason
        mock_realloc.assert_called_once()

    @pytest.mark.asyncio
    async def test_reallocation_finding_nothing_still_rejects(self):
        big_position = _open_position(size_usd=900_000.0, entry=1000.0, stop=100.0, size_units=1000.0)
        session = AsyncMock()
        with patch("engine.portfolio_reallocation.try_reallocate_for_candidate",
                   AsyncMock(return_value=False)) as mock_realloc:
            ok, reason = await validate_signal(
                _signal(confidence=90.0), wallet_balance=100_000.0,
                open_positions=[big_position], session=session,
            )
        assert ok is False
        assert mock_realloc.call_count == 1  # tried exactly once, didn't loop forever

    @pytest.mark.asyncio
    async def test_reallocation_freeing_one_position_still_insufficient_rejects(self):
        """Reallocation succeeds (closes one position) but the candidate is
        SO large that even the freed capital isn't enough -- must reject
        rather than trying a second reallocation (at-most-one-per-event)."""
        huge_position = _open_position(size_usd=1_800_000.0, entry=1000.0, stop=100.0, size_units=2000.0)
        session = AsyncMock()
        # After "closing" one position, still one massive one left deployed.
        session.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[huge_position])))))

        with patch("engine.portfolio_reallocation.try_reallocate_for_candidate",
                   AsyncMock(return_value=True)) as mock_realloc:
            ok, reason = await validate_signal(
                _signal(confidence=90.0), wallet_balance=50_000.0,
                open_positions=[huge_position], session=session,
            )
        assert ok is False
        assert mock_realloc.call_count == 1  # never attempts a second reallocation

    @pytest.mark.asyncio
    async def test_reallocation_exception_fails_safe_to_rejection(self):
        big_position = _open_position(size_usd=900_000.0, entry=1000.0, stop=100.0, size_units=1000.0)
        session = AsyncMock()
        with patch("engine.portfolio_reallocation.try_reallocate_for_candidate",
                   AsyncMock(side_effect=RuntimeError("boom"))):
            ok, reason = await validate_signal(
                _signal(confidence=90.0), wallet_balance=100_000.0,
                open_positions=[big_position], session=session,
            )
        assert ok is False  # doesn't crash validate_signal, just rejects
