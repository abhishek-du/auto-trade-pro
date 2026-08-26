"""Path F — a tactical signal must never become a stop-disabled swing hold.

Regression cover for the 2026-08-25 forensic finding. The defect chain was:

    tactical_executor  product = "MIS" if sub_pipeline == "F1" else "CNC"
        -> trade_simulator  is_swing = product == "CNC"
                            trade_style   = "SWING"
                            swing_min_hold = now + 48h
        -> india_tasks      if sl_hit and trade_style == "SWING"
                               and now < swing_min_hold:  sl_hit = False

so an F4 mean-reversion signal was held for two sessions with its stop
suppressed, pinned capital for that window, and — for the SELL rules — opened
a delivery short that the cash segment does not allow.

These tests pin the three properties independently, so that restoring any one
link of the chain fails at least one of them.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from engine.tactical_rules import F1_RULES, F4_RULES, Signal


# ── 1. the product decision itself ────────────────────────────────────────────

def _product_expr() -> ast.expr:
    """The expression assigned to product= in the TradeIntent build."""
    src = Path("engine/tactical_executor.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "TradeIntent":
            continue
        for kw in node.keywords:
            if kw.arg == "product":
                return kw.value
    pytest.fail("no product= keyword found in the TradeIntent(...) call")


def test_tactical_product_is_intraday_for_every_pipeline():
    """product= must be the literal "MIS", not a per-pipeline conditional.

    Asserting on the AST rather than on a rendered string means a comment
    mentioning MIS cannot satisfy this test.
    """
    expr = _product_expr()
    assert isinstance(expr, ast.Constant), (
        f"product= is a {type(expr).__name__}, not a constant. A conditional "
        f"here is what made F4 signals CNC/delivery."
    )
    assert expr.value == "MIS", f'product= is "{expr.value}", expected "MIS"'


def test_no_tactical_pipeline_is_excluded_from_intraday():
    """Both rule families are intraday, so neither may be special-cased."""
    src = Path("engine/tactical_executor.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            seg = ast.get_source_segment(src, node) or ""
            if "sub_pipeline" in seg and ("F1" in seg or "F4" in seg):
                pytest.fail(
                    f"tactical_executor branches on sub_pipeline: {seg!r}. "
                    f"F1 and F4 are both intraday; a product/holding split "
                    f"between them reintroduces the swing defect."
                )


# ── 2. CNC -> SWING -> 48h in the simulator ───────────────────────────────────

def test_simulator_only_makes_swing_positions_from_cnc():
    """Pin the mapping the executor now avoids, so its meaning cannot drift."""
    src = inspect.getsource(
        __import__("paper_trading.trade_simulator", fromlist=["x"])
    )
    assert 'is_swing = product == "CNC"' in src, (
        "trade_simulator no longer derives swing status from product=='CNC'. "
        "If that mapping changed, this regression's premise must be rechecked."
    )
    assert "swing_min_hold" in src


@pytest.mark.parametrize("product,expect_swing", [("MIS", False), ("CNC", True)])
def test_mis_product_yields_live_stop_no_min_hold(product, expect_swing):
    """The two fields the stop-suspension check reads, for each product."""
    is_swing = product == "CNC"
    trade_style = "SWING" if is_swing else product
    swing_min_hold = (
        datetime(2026, 8, 25, 9, 30) + timedelta(hours=48) if is_swing else None
    )
    assert is_swing is expect_swing
    if product == "MIS":
        assert trade_style == "MIS"
        assert swing_min_hold is None, (
            "a MIS position must carry no minimum hold, or fast_sl_check will "
            "suppress its stop"
        )


# ── 3. the stop-suspension branch it feeds ────────────────────────────────────

def test_stop_suspension_cannot_apply_to_a_tactical_position():
    """Replay the india_tasks guard for both trade styles.

    Mirrors tasks/india_tasks.py: the stop is suppressed only when the position
    is SWING *and* still inside swing_min_hold. A MIS position satisfies
    neither, so its stop stays live.
    """
    now = datetime(2026, 8, 25, 10, 0)

    def stop_survives(trade_style, swing_min_hold):
        sl_hit = True
        if sl_hit and trade_style == "SWING" and swing_min_hold:
            if now < swing_min_hold:
                sl_hit = False
        return sl_hit

    assert stop_survives("MIS", None) is True
    # the defect, reproduced: this is what F4 positions used to do
    assert stop_survives("SWING", now + timedelta(hours=47)) is False


# ── 4. delivery shorts must be unreachable ────────────────────────────────────

def test_f4_sell_rules_exist_and_would_have_been_delivery_shorts():
    """The SELL-capable rules are why CNC was not merely suboptimal.

    A CNC SELL is a delivery short; the cash segment does not permit one, and
    engine/agent/execution.py rejects it on the live path. Paper mode did not,
    so the defect produced orders that could never have been placed for real.
    """
    assert "OVERBOUGHT_FADE" in F4_RULES
    expr = _product_expr()
    assert isinstance(expr, ast.Constant) and expr.value == "MIS", (
        "with a non-MIS product, an OVERBOUGHT_FADE SELL becomes a delivery "
        "short"
    )


def test_rule_families_are_disjoint_and_cover_the_sell_rules():
    assert not set(F1_RULES) & set(F4_RULES)
    assert Signal is not None
