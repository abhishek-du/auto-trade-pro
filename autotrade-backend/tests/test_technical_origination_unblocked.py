"""TECHNICAL origination re-enabled — contract §10b (2026-08-20).

This reverses the News-Only premise for one family. These tests pin BOTH
directions: that the flag actually opens the gate, and that the code default
stays closed so a fresh checkout is still News-Only.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestDefaultsStayNewsOnly:
    """A fresh checkout must not originate technical trades. The paper run
    enables this in .env; the code default is the guarantee."""

    def test_code_defaults_are_still_blocking(self):
        from utils.config import Settings

        assert Settings.model_fields["TECHNICAL_ORIGINATION_BLOCKED"].default is True
        assert Settings.model_fields["NEWS_ONLY_BLOCKS_HUB_ENTRIES"].default is True

    def test_paper_mode_is_still_on(self):
        """§10b permits technical origination in PAPER only."""
        from utils.config import settings

        assert settings.PAPER_MODE is True


class TestGateHonoursTheFlag:

    @pytest.mark.asyncio
    async def test_blocked_when_flag_is_true(self):
        from engine.decision_router import (
            ConfidenceSource, EventDirectness, StrategyFamily, TradeIntent,
            authorize_trade_intent,
        )
        from db.database import AsyncSessionLocal

        intent = TradeIntent(
            strategy="HUB_TECHNICAL", symbol="RELIANCE.NS", action="BUY",
            instrument_type="EQUITY", entry_price=1400.0, stop_loss=1386.0,
            take_profit=1435.0, confidence=72.0,
            confidence_source=ConfidenceSource.CALCULATED,
            strategy_family=StrategyFamily.TECHNICAL,
            event_directness=EventDirectness.NOT_APPLICABLE,
        )
        with patch("utils.config.settings.TECHNICAL_ORIGINATION_BLOCKED", True):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(intent, s)
        assert r.approved is False
        assert "hard-blocked" in (r.reason or "").lower()

    @pytest.mark.asyncio
    async def test_unblocked_when_flag_is_false(self):
        """With the flag off, the News-Only rejection must no longer be the
        reason. Market hours may still stop it — that is a different gate."""
        from engine.decision_router import (
            ConfidenceSource, EventDirectness, StrategyFamily, TradeIntent,
            authorize_trade_intent,
        )
        from db.database import AsyncSessionLocal

        intent = TradeIntent(
            strategy="HUB_TECHNICAL", symbol="RELIANCE.NS", action="BUY",
            instrument_type="EQUITY", entry_price=1400.0, stop_loss=1386.0,
            take_profit=1435.0, confidence=72.0,
            confidence_source=ConfidenceSource.CALCULATED,
            strategy_family=StrategyFamily.TECHNICAL,
            event_directness=EventDirectness.NOT_APPLICABLE,
        )
        with patch("utils.config.settings.TECHNICAL_ORIGINATION_BLOCKED", False):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(intent, s)
        reason = (r.reason or "").lower()
        assert "technical strategy_family trade origination is hard-blocked" not in reason, (
            "News-Only block still firing with the flag off"
        )


class TestContractRecordsTheReversal:

    def test_contract_documents_10b(self):
        from pathlib import Path

        c = (Path(__file__).resolve().parents[2] / "docs"
             / "NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md").read_text(encoding="utf-8")
        assert "§10b" in c
        assert "TECHNICAL_ORIGINATION_BLOCKED" in c
        # The cost must be stated, not buried.
        assert "no longer holds" in c
        assert "no per-family daily risk bucket" in c

    def test_runtime_kill_switch_exists(self):
        """§10b's reversibility claim must be real."""
        from utils.runtime_config import RuntimeConfig

        assert hasattr(RuntimeConfig, "technical_origination_blocked")
