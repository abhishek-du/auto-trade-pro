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

from engine.direct_news_strategy import maybe_direct_trade, _is_stale_repeat_news


def _evidence(materiality="HIGH", confidence=0.85, direction="BULLISH"):
    return SimpleNamespace(materiality=materiality, confidence=confidence, direction=direction)


@pytest.fixture(autouse=True)
def _base_mocks():
    """Everything up to the confirmation gate succeeds by default, so each
    test only needs to override the one thing it's testing."""
    snap = SimpleNamespace(ltp=100.0, change_pct=1.0, buy_depth=[], sell_depth=[])
    with patch("crawler.market_snapshot.get_market_snapshot", AsyncMock(return_value=snap)), \
         patch("engine.direct_news_strategy._is_stale_repeat_news",
               AsyncMock(return_value=(False, ""))):
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


def _row(created_at, headline):
    return (created_at, headline)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *a, **kw):
        return _FakeResult(self._rows)


class TestStaleRepeatNewsUnit:
    """2026-07-28: _is_stale_repeat_news() -- the exact mechanism that closes
    the ASIIL.BO/MOLDTKPAC.NS incident (same underlying fact re-classified as
    'fresh' a full calendar day after the real event, because the existing
    dedup only searches a 6h window over news_id-linked rows this module's
    own classify_event() calls never produce)."""

    @pytest.mark.asyncio
    async def test_no_prior_decisions_is_not_stale(self):
        session = _FakeSession([])
        is_stale, reason = await _is_stale_repeat_news("FOO.NS", "FOO rises 10%", session)
        assert is_stale is False

    @pytest.mark.asyncio
    async def test_similar_headline_from_yesterday_is_stale(self):
        from datetime import datetime, timedelta
        yesterday = datetime.utcnow() - timedelta(days=1, hours=6)
        session = _FakeSession([_row(yesterday, "Mold-Tek Packaging Limited: Outcome of Board Meeting")])
        is_stale, reason = await _is_stale_repeat_news(
            "MOLDTKPAC.NS",
            "Mold-Tek Packaging Limited: Outcome of Board Meeting — results approved",
            session,
        )
        assert is_stale is True
        assert "already seen" in reason

    @pytest.mark.asyncio
    async def test_dissimilar_headline_from_yesterday_is_not_stale(self):
        from datetime import datetime, timedelta
        yesterday = datetime.utcnow() - timedelta(days=1, hours=6)
        session = _FakeSession([_row(yesterday, "Totally unrelated company wins a contract")])
        is_stale, _ = await _is_stale_repeat_news("FOO.NS", "FOO rises 10% on Q1 results", session)
        assert is_stale is False

    @pytest.mark.asyncio
    async def test_similar_headline_from_today_is_not_stale(self):
        """Same-day re-mentions (e.g. multiple RSS feeds syndicating the same
        story within one day) are NOT what this guards against -- only a
        story that's already a day (or more) old."""
        from datetime import datetime, timedelta
        earlier_today = datetime.utcnow() - timedelta(minutes=30)
        session = _FakeSession([_row(earlier_today, "FOO rises 10% on Q1 results")])
        is_stale, _ = await _is_stale_repeat_news("FOO.NS", "FOO rises 10% on Q1 results", session)
        assert is_stale is False

    @pytest.mark.asyncio
    async def test_db_error_fails_open_not_stale(self):
        class _BrokenSession:
            async def execute(self, *a, **kw):
                raise RuntimeError("db down")
        is_stale, _ = await _is_stale_repeat_news("FOO.NS", "FOO rises 10%", _BrokenSession())
        assert is_stale is False

    @pytest.mark.asyncio
    async def test_null_headline_row_is_skipped_not_crashed(self):
        from datetime import datetime, timedelta
        yesterday = datetime.utcnow() - timedelta(days=1)
        session = _FakeSession([_row(yesterday, None)])
        is_stale, _ = await _is_stale_repeat_news("FOO.NS", "FOO rises 10%", session)
        assert is_stale is False


class TestStaleRepeatNewsWiring:
    @pytest.mark.asyncio
    async def test_stale_repeat_is_not_traded(self):
        with patch("engine.direct_news_strategy._is_stale_repeat_news",
                   AsyncMock(return_value=(True, "same story already seen on 2026-07-27"))), \
             patch("crawler.market_snapshot.get_market_snapshot") as mock_snap:
            opened = await maybe_direct_trade("FOO.NS", "BUY", 123, _evidence(), "FOO rises on results")
        assert opened is False
        mock_snap.assert_not_called()  # short-circuits before the network call too
