"""Every EXECUTION_GATE audit row must say who wrote it.

simulation_logs records no emitting process, so production traffic and test
traffic are indistinguishable at query time. 144 rows for the fixture symbol
TESTCO.NS sit in the production table, and attributing them required tracing an
error string that happened to land in a payload — a code trace, not a query.

Additive JSON only: no schema change, no routing change, no execution change.
Historical rows are untouched and are NOT retroactively labelled.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


def test_emitter_reports_pytest_true_while_running_under_pytest():
    from engine.decision_router import _emitter_identity
    e = _emitter_identity()
    assert e["pytest"] is True, (
        "PYTEST_CURRENT_TEST is set during a test, so this row must be "
        "identifiable as test traffic"
    )


def test_emitter_reports_pytest_false_outside_pytest(monkeypatch):
    """Simulate production: the variable is absent outside a pytest run."""
    from engine.decision_router import _emitter_identity
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    e = _emitter_identity()
    assert e["pytest"] is False


def test_emitter_carries_a_meaningful_process_identity():
    from engine.decision_router import _emitter_identity
    e = _emitter_identity()
    assert isinstance(e["process"], str) and e["process"], "process must be non-empty"
    assert isinstance(e["pid"], int) and e["pid"] > 0
    assert e["pid"] == os.getpid()
    assert "/" not in e["process"], "basename only — do not leak a full path"


def test_the_audit_payload_actually_includes_the_emitter():
    """AST: a comment mentioning `emitter` cannot satisfy this."""
    src = Path("engine/decision_router.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_log_intent_audit")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "SimulationLog"]
    assert calls, "no SimulationLog(...) construction in _log_intent_audit"
    data = next((k.value for k in calls[0].keywords if k.arg == "data"), None)
    assert isinstance(data, ast.Dict), "data= is not a dict literal"
    keys = [k.value for k in data.keys if isinstance(k, ast.Constant)]
    assert "emitter" in keys, "the audit payload does not carry an emitter"
    val = data.values[keys.index("emitter")]
    assert isinstance(val, ast.Call) and getattr(val.func, "id", "") == "_emitter_identity"


def test_emitter_does_not_alter_routing_or_execution():
    """It must be a pure metadata read: no DB, no branching on its value."""
    src = Path("engine/decision_router.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_emitter_identity")
    body = ast.unparse(fn)
    for banned in ("session", "execute", "commit", "await", "RoutingOutcome", "intent"):
        assert banned not in body, f"_emitter_identity references {banned!r}"
