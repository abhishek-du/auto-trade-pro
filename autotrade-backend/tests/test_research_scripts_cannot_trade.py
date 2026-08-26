"""Every research script must be structurally incapable of trading.

tests/test_exit_shadow_isolation.py proves this for the shadow horizon script.
Phase 25 added a second script (the V2 replay), so the guarantee is generalised
here rather than copied: any new file under scripts/research/ is picked up
automatically and must satisfy the same rules.

The rules are structural, not stylistic. A research tool that CAN place an
order will eventually place one.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

RESEARCH_DIR = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "research"
BACKEND = pathlib.Path(__file__).resolve().parents[1]

SCRIPTS = sorted(p for p in RESEARCH_DIR.glob("*.py") if p.name != "__init__.py")


def _ids(paths):
    return [p.name for p in paths]


assert SCRIPTS, "no research scripts found — this test would silently pass"


@pytest.mark.parametrize("path", SCRIPTS, ids=_ids(SCRIPTS))
class TestNoOrderPath:
    def test_imports_no_execution_module(self, path):
        banned = {
            "engine.decision_router",
            "engine.zerodha_executor",
            "engine.agent.execution",
            "engine.tactical_executor",
            "paper_trading.position_tracker",
            "paper_trading.virtual_wallet",
            "tasks.india_tasks",
            "tasks.celery_app",
        }
        tree = ast.parse(path.read_text())
        found = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                found.add(n.module)
            elif isinstance(n, ast.Import):
                found.update(a.name for a in n.names)
        overlap = found & banned
        assert not overlap, f"{path.name} imports an execution path: {overlap}"

    def test_calls_no_order_or_close_function(self, path):
        banned = (
            "close_paper_trade", "open_paper_trade", "scale_out_paper_trade",
            "place_real_order", "execute_trade_intent", "route_decision",
            "authorize_trade_intent", "deduct_margin", "return_margin",
            "place_order", "kite.place", "cancel_order",
        )
        tree = ast.parse(path.read_text())
        called = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
        hits = {b for b in banned if b.split(".")[-1] in called}
        assert not hits, f"{path.name} calls {hits}"

    def test_constructs_no_trade_or_position_row(self, path):
        """SimulationLog is the only row a research script may create."""
        tree = ast.parse(path.read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for forbidden in ("PaperTrade", "OpenPosition", "TacticalSignal",
                          "AgentTrade", "AgentDecision", "TradeIntent"):
            assert forbidden not in names, f"{path.name} constructs {forbidden}"


# The three rules below are stricter and apply to the scripts Phase 25 owns.
#
# They are NOT applied to the 28 pre-existing research scripts, and that is a
# deliberate scoping decision rather than an oversight: those files were not
# part of this change and running new structural rules over them retroactively
# produced only false positives. Short stems like "react", "sweep", "entity"
# and "consumed" occur as ordinary English in production comments ("reacts far
# faster", "a closing sweep", "Upstox identity"), so a substring search for the
# stem finds them everywhere and proves nothing.
PHASE_25_SCRIPTS = [p for p in SCRIPTS
                    if p.stem in ("exit_horizon_shadow", "v2_exit_replay")]
assert len(PHASE_25_SCRIPTS) == 2, PHASE_25_SCRIPTS


@pytest.mark.parametrize("path", PHASE_25_SCRIPTS, ids=_ids(PHASE_25_SCRIPTS))
class TestPhase25ScriptsAreFullyIsolated:
    def test_writes_no_mutating_sql(self, path):
        """Reads and one INSERT via the ORM; never a hand-written mutation."""
        lowered = path.read_text().lower()
        for verb in ("update ", "delete ", "insert into", "drop ", "alter ",
                     "truncate "):
            assert verb not in lowered, f"{path.name} contains SQL '{verb.strip()}'"

    def test_no_production_module_imports_it(self, path):
        """Checked on the IMPORT GRAPH, not on a substring of the filename."""
        target = f"scripts.research.{path.stem}"
        hits = []
        for p in BACKEND.rglob("*.py"):
            parts = p.parts
            if "tests" in parts or "scripts" in parts or ".venv" in parts:
                continue
            try:
                tree = ast.parse(p.read_text(errors="ignore"))
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                mods = []
                if isinstance(n, ast.ImportFrom) and n.module:
                    mods = [n.module]
                elif isinstance(n, ast.Import):
                    mods = [a.name for a in n.names]
                if any(m == target or m.endswith(f".{path.stem}") for m in mods):
                    hits.append(str(p.relative_to(BACKEND)))
        assert not hits, f"production modules import {path.name}: {hits}"

    def test_not_scheduled(self, path):
        """Research must be run deliberately, never by beat."""
        beat = (BACKEND / "tasks" / "celery_app.py").read_text()
        assert f"scripts.research.{path.stem}" not in beat
        assert path.stem not in beat


class TestReplayUsesCorrectedCosts:
    def test_the_replay_charges_product_aware_costs(self):
        """A replay on the pre-2026-08-26 flat rate would overstate MIS costs
        by ~0.18% of notional per round trip and could invert a verdict."""
        src = (RESEARCH_DIR / "v2_exit_replay.py").read_text()
        assert "estimate_trade_cost" in src
        assert "product" in src

    def test_the_replay_derives_the_original_stop_not_the_stored_one(self):
        """paper_trades.stop_loss is mutated in place by the trailing stop, so a
        trailed winner's stored stop is a PROFIT stop. Using it would leak
        CONTROL's behaviour into every variant and flatten the comparison."""
        src = (RESEARCH_DIR / "v2_exit_replay.py").read_text()
        assert "initial_risk_inr" in src
        assert "risk_per_unit" in src

    def test_every_variant_keeps_the_hard_stop_and_the_squareoff(self):
        """'Hold longer' must never become 'hold through an unbounded loss'."""
        from scripts.research import v2_exit_replay as rp

        import inspect

        src = inspect.getsource(rp._simulate)
        i_stop = src.index("HARD_STOP")
        i_pm = src.index("pm_from_minute is not None")
        assert i_stop < i_pm, "the hard stop must be evaluated before profit management"
        assert "MARKET_SQUAREOFF" in src
        # The squareoff branch carries no horizon condition.
        j = src.index("if ts >= squareoff:")
        assert "pm_from_minute" not in src[j:j + 200]
