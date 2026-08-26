"""The exit experiment must be incapable of trading.

Phase 24 found that the signalled subset is net -0.052% at 60 minutes and
+0.342% held to the close, replicated out of sample. That is a reason to
COMPARE exit horizons, not to change them, and the comparison must not be able
to become an action by accident.

CONTROL — the existing exit stack — remains the only thing that closes a
position. These tests enforce that the shadow module cannot.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import scripts.research.exit_horizon_shadow as shadow

SRC = pathlib.Path(shadow.__file__).read_text()
TREE = ast.parse(SRC)


class TestCannotTrade:
    def test_imports_nothing_that_can_close_a_position(self):
        banned = {
            "paper_trading.trade_simulator",
            "engine.decision_router",
            "engine.zerodha_executor",
            "engine.agent.execution",
            "engine.risk_manager",
            "paper_trading.virtual_wallet",
        }
        found = set()
        for n in ast.walk(TREE):
            if isinstance(n, ast.ImportFrom) and n.module:
                found.add(n.module)
            elif isinstance(n, ast.Import):
                found.update(a.name for a in n.names)
        overlap = found & banned
        assert not overlap, f"shadow module imports a trading path: {overlap}"

    def test_never_calls_a_close_or_order_function(self):
        banned = ("close_paper_trade", "open_paper_trade", "place_real_order",
                  "execute_trade_intent", "route_decision", "scale_out_paper_trade",
                  "deduct_margin", "return_margin")
        for name in banned:
            assert name not in SRC, f"shadow module references {name}"

    def test_writes_only_simulation_logs(self):
        """Its only INSERT target may be the research log."""
        models = {n.id for n in ast.walk(TREE) if isinstance(n, ast.Name)}
        for forbidden in ("PaperTrade", "OpenPosition", "TacticalSignal",
                          "VirtualWallet", "AgentDecision"):
            assert forbidden not in models, (
                f"shadow module constructs {forbidden}; it may only write SimulationLog"
            )
        assert "SimulationLog" in SRC

    def test_sql_is_read_only_apart_from_the_research_log(self):
        lowered = SRC.lower()
        for verb in ("update ", "delete ", "insert into", "drop ", "alter "):
            assert verb not in lowered, f"shadow module contains SQL '{verb.strip()}'"

    def test_is_not_imported_by_any_production_module(self):
        """Nothing in the trading path may reach this file."""
        root = pathlib.Path(shadow.__file__).parents[2]
        hits = []
        for p in root.rglob("*.py"):
            parts = p.parts
            if "tests" in parts or "scripts" in parts or ".venv" in parts:
                continue
            if "exit_horizon_shadow" in p.read_text(errors="ignore"):
                hits.append(str(p.relative_to(root)))
        assert not hits, f"production modules reference the shadow experiment: {hits}"

    def test_is_not_wired_into_the_beat_schedule(self):
        root = pathlib.Path(shadow.__file__).parents[2]
        beat = (root / "tasks" / "celery_app.py").read_text()
        assert "exit_horizon_shadow" not in beat, (
            "the shadow experiment must be run deliberately, not on a schedule"
        )


class TestControlRemainsDefault:
    """Phase 25 REPLACED this class's original assertion, deliberately.

    It used to assert that no exit-mode switch existed anywhere in the trading
    path, because on 2026-08-26 CONTROL was the only behaviour that existed and
    the shadow script was the whole experiment. The V2 implementation makes that
    assertion false on purpose: there is now a real mode, selected by
    TRADING_STRATEGY_MODE.

    What must remain true is narrower and more useful: the shadow research
    module is still not reachable from the trading path, and the mode is decided
    in exactly one place with CONTROL as the code-level default.
    """

    def test_the_trading_path_cannot_reach_the_research_script(self):
        import paper_trading.trade_simulator as ts

        assert "exit_horizon_shadow" not in inspect.getsource(ts)

    def test_control_is_the_code_default(self):
        """A process that cannot read .env must get the OLD behaviour."""
        import utils.config as cfg

        field = cfg.Settings.model_fields["TRADING_STRATEGY_MODE"]
        assert field.default == "CONTROL", (
            "the experiment must be opt-in; a missing .env may never silently "
            "enable V2"
        )

    def test_the_mode_is_decided_in_one_place(self):
        """No call site may re-derive the mode from settings itself."""
        import pathlib as _pl

        root = _pl.Path(shadow.__file__).parents[2]
        offenders = []
        for rel in ("paper_trading/trade_simulator.py", "tasks/india_tasks.py",
                    "paper_trading/position_tracker.py"):
            # Comments explain the gate and name the settings; only executable
            # lines count as reading them.
            code = "\n".join(
                ln for ln in (root / rel).read_text().splitlines()
                if not ln.strip().startswith("#")
            )
            if "TRADING_STRATEGY_MODE" in code or "V2_MIN_HOLD_MINUTES" in code:
                offenders.append(rel)
        assert not offenders, (
            f"these read the mode directly instead of via engine.exit_policy: {offenders}"
        )


class TestCostsAreProductAware:
    def test_uses_the_corrected_cost_model(self):
        """The research report must never use the old flat delivery rate."""
        assert shadow.COST_PCT["MIS"] < shadow.COST_PCT["CNC"], (
            "intraday must cost less than delivery; the pre-2026-08-26 model "
            "charged both the same"
        )
        assert 0.0008 <= shadow.COST_PCT["MIS"] <= 0.0016
        assert 0.0028 <= shadow.COST_PCT["CNC"] <= 0.0031

    def test_every_reported_figure_is_net(self):
        assert "_net(" in SRC
        assert "def _net" in SRC


class TestHorizons:
    def test_covers_the_horizons_phase_24_identified(self):
        assert set(shadow.HORIZONS_MIN) >= {30, 60, 90, 120}

    def test_windows_start_at_the_actual_exit(self):
        """The question is 'what if we had held longer', so t=0 is the real exit."""
        assert "t.closed_at" in SRC
        assert "timestamp > :a" in SRC, "window must be exclusive of the exit bar"
