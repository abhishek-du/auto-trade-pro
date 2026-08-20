"""Path F — the shadow-mode guarantee.

This is the load-bearing test of the whole pipeline. Path F originates trades
from technical conditions with no news event, which
docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md forbids (§1 line 49, §6 line 281,
§10 line 347). Shadow mode is the reason Path F is allowed to exist at all: it
produces and records signals but has no execution path.

§6 line 285 is explicit that a flag-guarded execution call is NOT safely
disabled — "disabled by configuration ... is reversible by anyone who flips the
flag without knowing this contract exists." So the guarantee has to be
structural, and structural properties need a structural test: these AST-scan the
tactical modules for any reference to an execution symbol.

If a future change wires execution, these fail loudly. That is the point.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ENGINE = pathlib.Path(__file__).resolve().parent.parent / "engine"
TACTICAL_FILES = sorted(ENGINE.glob("tactical_*.py"))

# Anything that can open a position, directly or transitively.
FORBIDDEN_SYMBOLS = {
    "execute_trade_intent",
    "route_decision",
    "authorize_trade_intent",
    "open_paper_trade",
    "open_option_paper_trade",
    "open_spread_paper_trade",
    "open_future_paper_trade",
    "place_real_order",
    "AgentExecutionManager",
    "TradeIntent",
    "StrategyFamily",
}


def _names_referenced(path: pathlib.Path) -> set[str]:
    """Every identifier the module references, from imports and calls alike."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                found.add(a.name)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def _docstring_only(path: pathlib.Path, symbol: str) -> bool:
    """True when the symbol appears only in prose, not in code.

    The modules discuss these names in their docstrings on purpose — that is
    how the next reader learns why they are absent — so prose mentions must not
    trip the test.
    """
    return symbol not in _names_referenced(path)


class TestNoExecutionPath:

    def test_tactical_modules_exist(self):
        assert TACTICAL_FILES, "no engine/tactical_*.py modules found — wrong path?"

    @pytest.mark.parametrize("path", TACTICAL_FILES, ids=lambda p: p.name)
    def test_module_references_no_execution_symbol(self, path):
        referenced = _names_referenced(path)
        leaked = sorted(referenced & FORBIDDEN_SYMBOLS)
        assert not leaked, (
            f"{path.name} references execution symbol(s) {leaked}. Path F must have "
            f"no execution path — see the module docstring and "
            f"docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md §6 line 285."
        )

    def test_executor_docstring_documents_the_constraint(self):
        """The prose must survive too — a silent constraint gets removed."""
        src = (ENGINE / "tactical_executor.py").read_text(encoding="utf-8")
        assert "no path to execution" in src
        assert "NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT" in src

    def test_tasks_module_has_no_execution_path(self):
        tasks = pathlib.Path(__file__).resolve().parent.parent / "tasks" / "tactical_tasks.py"
        leaked = sorted(_names_referenced(tasks) & FORBIDDEN_SYMBOLS)
        assert not leaked, f"tactical_tasks.py references {leaked}"


class TestExecutionModeGuard:

    def test_non_shadow_mode_raises_rather_than_silently_shadowing(self):
        from unittest.mock import patch

        from engine.tactical_executor import TacticalExecutor

        with patch("utils.config.settings.TACTICAL_EXECUTION_MODE", "paper"):
            with pytest.raises(NotImplementedError, match="shadow-only"):
                TacticalExecutor()

    def test_shadow_mode_constructs(self):
        from engine.tactical_executor import TacticalExecutor

        assert TacticalExecutor().mode == "shadow"

    def test_default_config_is_shadow(self):
        from utils.config import settings

        assert settings.TACTICAL_EXECUTION_MODE == "shadow"


class TestPersistedRowsAreNeverExecuted:

    @pytest.mark.asyncio
    async def test_persist_always_writes_executed_false(self):
        from datetime import datetime
        from unittest.mock import MagicMock

        from engine.tactical_data_fetcher import MarketContext
        from engine.tactical_executor import ScanResult, TacticalExecutor
        from engine.tactical_rules import Signal

        added = []
        session = MagicMock()
        session.add = lambda obj: added.append(obj)

        sig = Signal("TESTSYM.NS", "BUY", 100.0, 98.0, 104.0, 70.0, "ORB",
                     datetime.now(), "F1")
        ex = TacticalExecutor()
        await ex._persist(sig, 72.0, 0.5, {}, MarketContext(vix=14.0), session,
                          ScanResult("F1"))

        assert len(added) == 1
        row = added[0]
        assert row.executed is False, "a tactical row must never be marked executed"
        assert "shadow mode" in row.reason
        assert row.symbol == "TESTSYM.NS"
