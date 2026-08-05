"""Tests for alert threading (Phase 3): a trade's exit alert replies onto
its entry alert's Telegram message_id.

Exercises the real local dev DB (same reachability already relied on by
alter_notification_columns.py) rather than mocking the session -- the
write-back logic's whole point is "does the persisted value round-trip
correctly," which a fully-mocked session can't actually verify. Each test
creates its own throwaway PaperTrade row and deletes it in a finally block.

Run:
    cd autotrade-backend
    .venv/bin/python -m pytest tests/test_alert_threading.py -v --tb=short
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from db.database import AsyncSessionLocal
from db.models import PaperTrade, TradeDirection, TradeStatus
from integrations.alerts import (
    AlertAction,
    AlertCategory,
    AlertEvent,
    Severity,
    TradeEntryPayload,
    TradeExitPayload,
    publish,
)


class _FakeDecision:
    def __init__(self, symbol="THREADTEST.NS"):
        self.symbol = symbol
        self.action = "BUY"
        self.entry = 100.0
        self.stop = 95.0
        self.target = 110.0
        self.confidence = 70.0
        self.master_score = 10.0
        self.strategy = "TEST"
        self.reasons = []
        self.hub_subscores = {}
        self.qty = 1


async def _make_paper_trade() -> int:
    async with AsyncSessionLocal() as session:
        trade = PaperTrade(
            symbol="THREADTEST.NS", direction=TradeDirection.BUY, status=TradeStatus.OPEN,
            entry_price=100.0, stop_loss=95.0, take_profit=110.0,
            size_units=1, size_usd=100.0,
        )
        session.add(trade)
        await session.flush()
        await session.commit()
        return trade.id


async def _delete_paper_trade(trade_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(PaperTrade).where(PaperTrade.id == trade_id))
        await session.commit()


async def _get_stored_message_id(trade_id: int) -> int | None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(PaperTrade.telegram_message_id).where(PaperTrade.id == trade_id)
        )).first()
        return row[0] if row else None


@pytest.fixture(autouse=True)
def _telegram_available():
    with patch("integrations.alerts.router.settings") as mock_settings:
        mock_settings.telegram_available = True
        mock_settings.TELEGRAM_MIN_SEVERITY = "INFO"
        yield mock_settings


@pytest_asyncio.fixture
async def paper_trade_id():
    tid = await _make_paper_trade()
    try:
        yield tid
    finally:
        await _delete_paper_trade(tid)


@pytest.mark.asyncio
async def test_entry_writes_message_id_back_to_db(paper_trade_id):
    with patch("integrations.telegram_service._post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = 555111
        await publish(AlertEvent(
            category=AlertCategory.TRADE, action=AlertAction.ENTRY, severity=Severity.SUCCESS,
            symbol="THREADTEST.NS", trade_id=paper_trade_id,
            payload=TradeEntryPayload(decision=_FakeDecision(), qty=1),
        ))
    assert await _get_stored_message_id(paper_trade_id) == 555111


@pytest.mark.asyncio
async def test_exit_replies_onto_stored_entry_message_id(paper_trade_id):
    # Simulate an entry alert having already stored a message_id.
    async with AsyncSessionLocal() as session:
        trade = await session.get(PaperTrade, paper_trade_id)
        trade.telegram_message_id = 777222
        await session.commit()

    with patch("integrations.telegram_service._post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = 999333
        await publish(AlertEvent(
            category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
            symbol="THREADTEST.NS", trade_id=paper_trade_id,
            payload=TradeExitPayload(symbol="THREADTEST.NS", side="BUY", entry=100.0, exit_price=105.0,
                                      qty=1, pnl=5.0, reason="TAKE_PROFIT"),
        ))
    mock_post.assert_awaited_once()
    assert mock_post.call_args.kwargs.get("reply_to_message_id") == 777222


@pytest.mark.asyncio
async def test_exit_sends_unthreaded_when_no_entry_was_ever_stored(paper_trade_id):
    """Telegram was down (or unconfigured) at entry time -- telegram_message_id
    stayed NULL. The exit alert must still send, just not as a reply."""
    with patch("integrations.telegram_service._post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = 111444
        await publish(AlertEvent(
            category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
            symbol="THREADTEST.NS", trade_id=paper_trade_id,
            payload=TradeExitPayload(symbol="THREADTEST.NS", side="BUY", entry=100.0, exit_price=95.0,
                                      qty=1, pnl=-5.0, reason="STOP_LOSS"),
        ))
    mock_post.assert_awaited_once()
    assert mock_post.call_args.kwargs.get("reply_to_message_id") is None
