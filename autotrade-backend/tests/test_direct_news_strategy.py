"""Regression tests for engine/direct_news_strategy.py's confirmation gate
(added 2026-07-28). This strategy has no LLM/debate step at all -- it trades
directly off classify_event()'s materiality/direction output -- so it was the
one path with zero confirmation of any kind before
engine.entry_confirmation.check_price_volume_confirmation() was wired in
after live data showed its first two closed trades (ASIIL.BO, MOLDTKPAC.NS)
both stopped out with near-zero favorable excursion.

All tests are deterministic and mocked -- no network, no DB.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from engine.direct_news_strategy import maybe_direct_trade


def _evidence(materiality="HIGH", confidence=0.85, direction="BULLISH"):
    return SimpleNamespace(materiality=materiality, confidence=confidence, direction=direction)


@pytest.fixture(autouse=True)
def _base_mocks():
    """Everything up to the confirmation gate succeeds by default, so each
    test only needs to override the one thing it's testing."""
    snap = SimpleNamespace(ltp=100.0, change_pct=1.0, buy_depth=[], sell_depth=[])
    with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=snap)):
        yield snap


class TestConfirmationGate:
    @pytest.mark.asyncio
    async def test_unconfirmed_signal_is_not_traded(self):
        with patch("engine.entry_confirmation.check_price_volume_confirmation",
                   return_value=(False, "price only +0.10% on the day — not enough follow-through")), \
             patch("engine.decision_router.execute_trade_intent") as mock_execute:
            opened = await maybe_direct_trade("FOO.NS", "BUY", 123, _evidence(), "FOO rises on results")
        assert opened is False
        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmed_signal_proceeds_to_execution(self):
        exec_result = SimpleNamespace(outcome=__import__(
            "engine.decision_router", fromlist=["RoutingOutcome"]
        ).RoutingOutcome.EXECUTED_PAPER, reason="ok")
        with patch("engine.entry_confirmation.check_price_volume_confirmation",
                   return_value=(True, "confirmed: 1.0% day move")), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 95.0, "target_1": 110.0,
                                            "target_2": 115.0, "atr": 2.0})), \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary",
                   AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("engine.decision_router.execute_trade_intent",
                   AsyncMock(return_value=exec_result)) as mock_execute:
            opened = await maybe_direct_trade("FOO.NS", "BUY", 123, _evidence(), "FOO rises on results")
        assert opened is True
        mock_execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirmation_runs_before_position_sizing(self):
        """The gate must short-circuit before any wallet/DB work happens for
        an unconfirmed signal -- not just before the final execute call."""
        with patch("engine.entry_confirmation.check_price_volume_confirmation",
                   return_value=(False, "order book skewed toward sellers")), \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary") as mock_wallet:
            opened = await maybe_direct_trade("FOO.NS", "BUY", 123, _evidence(), "FOO rises on results")
        assert opened is False
        mock_wallet.assert_not_called()
