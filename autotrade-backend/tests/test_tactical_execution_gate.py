"""Path F — Phase 2 execution guarantees.

REPLACES tests/test_tactical_shadow_mode.py.

That file AST-scanned the tactical package and failed if it referenced any
execution symbol. It was the right guarantee for Phase 1, where the contract
forbade an event-less originator outright and the only safe posture was "this
code physically cannot trade". Phase 2 deliberately wires execution, with §6 and
§10 of the contract amended in the same commit — so that test's premise is gone.

Deleting it outright would trade a real safety net for nothing, so it is
replaced rather than removed. These tests pin the guarantees that now matter:

  1. execution is OFF by default,
  2. the code and the contract cannot drift apart,
  3. the flag genuinely gates the router call,
  4. paper mode and the risk bucket still bind.
"""
from __future__ import annotations

import pathlib
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = REPO.parent / "docs" / "NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md"


class TestOffByDefault:

    def test_execution_flag_defaults_false(self):
        """The CODE default must stay False — what a fresh checkout does.

        Deliberately asserts the pydantic field default, NOT
        `settings.TACTICAL_EXECUTION_ENABLED`, because that reflects `.env`:
        the flag was enabled there on 2026-08-20 for the paper run. Asserting
        the runtime value would have forced whoever started that run to either
        delete this test or weaken it, and the guarantee §10a condition 1
        actually makes is about the default, not the deployment.
        """
        from utils.config import Settings

        assert Settings.model_fields["TACTICAL_EXECUTION_ENABLED"].default is False

    def test_live_trading_flag_defaults_false(self):
        """Paper only until Path F has a track record.

        This one checks BOTH the code default and the live runtime value: no
        deployment may ever turn it on without amending §10a first, so unlike
        the execution flag there is no legitimate `.env` override to tolerate.
        """
        from utils.config import Settings, settings

        assert Settings.model_fields["TACTICAL_LIVE_TRADING"].default is False
        assert settings.TACTICAL_LIVE_TRADING is False, (
            "TACTICAL_LIVE_TRADING is enabled in the running config — §10a "
            "condition 2 forbids this until the contract is amended"
        )

    def test_executor_reports_shadow_when_disabled(self):
        """Patched explicitly — `.env` currently enables execution."""
        from engine.tactical_executor import TacticalExecutor

        with patch("utils.config.settings.TACTICAL_EXECUTION_ENABLED", False):
            assert TacticalExecutor().mode == "shadow"

    def test_executor_reports_execute_when_enabled(self):
        from engine.tactical_executor import TacticalExecutor
        with patch("utils.config.settings.TACTICAL_EXECUTION_ENABLED", True):
            assert TacticalExecutor().mode == "execute"


class TestCodeAndContractCannotDrift:
    """The enum exists ONLY because the contract was amended. If someone later
    reverts the doc, or adds a family without amending it, this fails."""

    def test_contract_file_exists(self):
        assert CONTRACT.exists(), f"contract not found at {CONTRACT}"

    def test_contract_lists_tactical_as_an_allowed_originator(self):
        text = CONTRACT.read_text(encoding="utf-8")
        assert "TACTICAL" in text, (
            "StrategyFamily.TACTICAL exists in code but the contract does not "
            "mention it — the two have drifted, and the code is wrong."
        )

    def test_contract_records_the_amendment_date(self):
        assert "2026-08-20" in CONTRACT.read_text(encoding="utf-8")

    def test_enum_member_exists(self):
        from engine.decision_router import StrategyFamily
        assert StrategyFamily.TACTICAL.value == "TACTICAL"


class TestFlagGatesTheRouterCall:

    @staticmethod
    def _signal():
        from engine.tactical_rules import Signal
        return Signal("TESTSYM.NS", "BUY", 100.0, 98.0, 104.0, 70.0, "ORB",
                      datetime.now(), "F1")

    @staticmethod
    def _sizing():
        from engine.tactical_risk import SizingDecision
        return SizingDecision(True, 10, 20.0, "approved", notional=1000.0)

    @staticmethod
    def _session():
        added = []
        s = MagicMock()
        s.add = lambda o: added.append(o)
        s.commit = AsyncMock()
        s._added = added
        return s

    @pytest.mark.asyncio
    async def test_disabled_never_calls_the_router(self):
        from engine.tactical_data_fetcher import MarketContext
        from engine.tactical_executor import ScanResult, TacticalExecutor

        sess = self._session()
        with patch("utils.config.settings.TACTICAL_EXECUTION_ENABLED", False), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock()) as router:
            ex = TacticalExecutor()
            with patch.object(ex.risk, "size", AsyncMock(return_value=self._sizing())), \
                 patch.object(ex.risk, "commit", AsyncMock(return_value=True)):
                await ex._persist(self._signal(), 72.0, 0.5, {}, MarketContext(vix=14.0),
                                  sess, ScanResult("F1"))
        router.assert_not_awaited()
        assert sess._added[0].executed is False
        assert "shadow mode" in sess._added[0].reason

    @pytest.mark.asyncio
    async def test_enabled_calls_the_router_and_records_the_outcome(self):
        from engine.decision_router import RoutingOutcome
        from engine.tactical_data_fetcher import MarketContext
        from engine.tactical_executor import ScanResult, TacticalExecutor

        ok = MagicMock()
        ok.outcome = RoutingOutcome.EXECUTED_PAPER
        ok.order_id = "PAPER-123"
        ok.reason = "ok"

        sess = self._session()
        with patch("utils.config.settings.TACTICAL_EXECUTION_ENABLED", True), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(return_value=ok)):
            ex = TacticalExecutor()
            with patch.object(ex.risk, "size", AsyncMock(return_value=self._sizing())), \
                 patch.object(ex.risk, "commit", AsyncMock(return_value=True)):
                await ex._persist(self._signal(), 72.0, 0.5, {}, MarketContext(vix=14.0),
                                  sess, ScanResult("F1"))

        row = sess._added[0]
        assert row.executed is True
        assert row.order_ref == "PAPER-123"
        assert row.routing_outcome == "EXECUTED_PAPER"
        assert row.executed_at is not None

    @pytest.mark.asyncio
    async def test_router_rejection_is_recorded_not_swallowed(self):
        from engine.decision_router import RoutingOutcome
        from engine.tactical_data_fetcher import MarketContext
        from engine.tactical_executor import ScanResult, TacticalExecutor

        nope = MagicMock()
        nope.outcome = RoutingOutcome.BLOCKED_MARKET_CLOSED
        nope.order_id = None
        nope.reason = "NSE not open"

        sess = self._session()
        with patch("utils.config.settings.TACTICAL_EXECUTION_ENABLED", True), \
             patch("engine.decision_router.execute_trade_intent", AsyncMock(return_value=nope)):
            ex = TacticalExecutor()
            with patch.object(ex.risk, "size", AsyncMock(return_value=self._sizing())), \
                 patch.object(ex.risk, "commit", AsyncMock(return_value=True)):
                await ex._persist(self._signal(), 72.0, 0.5, {}, MarketContext(vix=14.0),
                                  sess, ScanResult("F1"))

        row = sess._added[0]
        assert row.executed is False
        assert row.routing_outcome == "BLOCKED_MARKET_CLOSED"
        assert "NSE not open" in row.reason

    @pytest.mark.asyncio
    async def test_routing_exception_does_not_abort_the_cycle(self):
        from engine.tactical_data_fetcher import MarketContext
        from engine.tactical_executor import ScanResult, TacticalExecutor

        sess = self._session()
        with patch("utils.config.settings.TACTICAL_EXECUTION_ENABLED", True), \
             patch("engine.decision_router.execute_trade_intent",
                   AsyncMock(side_effect=RuntimeError("boom"))):
            ex = TacticalExecutor()
            with patch.object(ex.risk, "size", AsyncMock(return_value=self._sizing())), \
                 patch.object(ex.risk, "commit", AsyncMock(return_value=True)):
                await ex._persist(self._signal(), 72.0, 0.5, {}, MarketContext(vix=14.0),
                                  sess, ScanResult("F1"))
        row = sess._added[0]
        assert row.executed is False and "routing error" in row.reason


class TestKillSwitch:
    """RuntimeConfig is DB-backed, so flipping it halts every process at once —
    unlike settings.AGENT_ENABLED, which was audit finding D4."""

    def test_runtime_key_is_whitelisted(self):
        from utils.runtime_config import _KNOWN_KEYS
        assert "tactical_execution_enabled" in _KNOWN_KEYS

    def test_runtime_override_wins(self):
        from utils.runtime_config import RuntimeConfig
        assert RuntimeConfig({"tactical_execution_enabled": True}).tactical_execution_enabled
        assert not RuntimeConfig({"tactical_execution_enabled": False}).tactical_execution_enabled

    def test_router_has_an_explicit_tactical_branch(self):
        """Every family check is an `==` test, so a new member is allowed by
        default. The branch makes the authority findable and killable."""
        src = (REPO / "engine" / "decision_router.py").read_text(encoding="utf-8")
        assert "StrategyFamily.TACTICAL" in src
        assert "tactical_execution_enabled" in src
