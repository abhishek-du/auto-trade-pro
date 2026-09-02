"""LIVE mode must reach no broker at all.

HISTORY
-------
This file began as the D2 regression test. `route_decision`'s LIVE branch called
`place_real_order(signal_id=..., confidence=...)`, and neither is a parameter of
that function, so every live order raised TypeError — swallowed by a broad
`except` and reported as a generic RoutingOutcome.ERROR, indistinguishable from a
broker outage. It was latent only because PAPER_MODE=true.

WHAT CHANGED (2026-09-02)
-------------------------
The live branch is gone. This deployment is paper-only: Zerodha is disabled
everywhere and there is no Upstox order executor, so `route_decision` now
hard-blocks LIVE before touching any broker code.

That makes D2 unreachable rather than fixed, so the old call-site binding test no
longer has a call site to bind. The tests below replace it with the stronger
property: **no broker call is reachable from route_decision at all.**

`engine/zerodha_executor.place_real_order` still exists and is still imported
here on purpose — if someone builds a live path again, the signature guard is
ready to be re-pointed at it.
"""
from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.decision_router import RoutingOutcome, TradeMode, route_decision
from engine.zerodha_executor import place_real_order


def _signal(**kw):
    s = MagicMock()
    s.symbol = kw.get("symbol", "TESTCO.NS")
    s.action = kw.get("action", "BUY")
    s.confidence = kw.get("confidence", 85.0)
    s.entry_price = kw.get("entry_price", 100.0)
    s.id = kw.get("id", "sig-123")
    return s


class TestNoBrokerCallIsReachable:

    def test_route_decision_does_not_call_place_real_order(self):
        """The strongest form of the D2 guarantee: there is no call to break."""
        tree = ast.parse(inspect.cleandoc(inspect.getsource(route_decision)))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == "place_real_order"
        ]
        assert not calls, (
            "route_decision calls place_real_order again. If a live executor was "
            "deliberately re-introduced, restore the signature-binding test from "
            "git history — D2 was a kwarg mismatch that failed silently."
        )

    def test_route_decision_imports_no_zerodha_module(self):
        """A credential check would re-arm live trading without a code review.

        The old gate was `if not kite.access_token`, which made 'can we trade
        live?' depend on a CREDENTIAL rather than a DECISION — dropping a valid
        token into .env would have silently re-enabled real orders.

        Asserted against the AST, not the raw text: the function's own comments
        legitimately name these modules while explaining why they are NOT used,
        and a substring search over source would match that prose and fail.
        """
        tree = ast.parse(inspect.cleandoc(inspect.getsource(route_decision)))

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(a.name for a in node.names)
        # Attribute/name references that survive comment-stripping.
        called = {
            getattr(n.func, "id", getattr(n.func, "attr", None))
            for n in ast.walk(tree) if isinstance(n, ast.Call)
        }

        for banned in ("crawler.zerodha_client", "engine.zerodha_executor"):
            assert banned not in imported, (
                f"route_decision imports {banned!r}. Live execution must not "
                f"depend on broker credentials being present or absent."
            )
        assert "get_kite_client" not in called and "get_kite_client" not in imported


class TestLiveModeIsHardBlocked:

    @pytest.mark.asyncio
    async def test_live_is_blocked_even_with_a_valid_token(self):
        """A working Zerodha token must NOT be enough to place a real order."""
        with patch("engine.decision_router.resolve_mode",
                   AsyncMock(return_value=TradeMode.LIVE)), \
             patch("engine.decision_router._log_decision_audit", AsyncMock()), \
             patch("utils.config.settings.ZERODHA_ACCESS_TOKEN", "a-valid-looking-token"), \
             patch("utils.config.settings.LIVE_CONFIDENCE_THRESHOLD", 10.0):
            result = await route_decision(
                _signal(), MagicMock(), position_size={"units": 3, "usd_value": 300.0},
            )

        assert result.outcome is RoutingOutcome.BLOCKED_NO_TOKEN
        assert result.metadata.get("paper_only") is True
        # Not an ERROR: this is a deliberate refusal, and callers must be able to
        # tell it apart from a broker outage.
        assert result.outcome is not RoutingOutcome.ERROR

    @pytest.mark.asyncio
    async def test_live_block_happens_before_any_executor_import(self):
        """If the block leaked, this patch would be hit and the test would fail."""
        with patch("engine.decision_router.resolve_mode",
                   AsyncMock(return_value=TradeMode.LIVE)), \
             patch("engine.decision_router._log_decision_audit", AsyncMock()), \
             patch("engine.zerodha_executor.place_real_order",
                   AsyncMock(side_effect=AssertionError(
                       "place_real_order was reached — LIVE is not blocked"))):
            result = await route_decision(
                _signal(), MagicMock(), position_size={"units": 1, "usd_value": 100.0},
            )

        assert result.outcome is RoutingOutcome.BLOCKED_NO_TOKEN


class TestExecutorStillIntactForFutureUse:

    def test_place_real_order_signature_is_unchanged(self):
        """Kept so a future live path can be re-pinned against it."""
        params = set(inspect.signature(place_real_order).parameters)
        assert {"symbol", "transaction_type", "quantity", "session"} <= params
        # The two kwargs that caused D2 must still NOT be accepted.
        assert "signal_id" not in params and "confidence" not in params
