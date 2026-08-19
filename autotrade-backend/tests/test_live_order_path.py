"""D2 regression — the live order path must not raise TypeError.

`route_decision`'s LIVE branch called `place_real_order(signal_id=..., confidence=...)`,
but neither is a parameter of that function. Every live order therefore raised
TypeError, which the broad `except Exception` in route_decision swallowed and
reported as a generic RoutingOutcome.ERROR — indistinguishable from a broker
outage. It was latent only because PAPER_MODE=true.

The signature-binding test below is the important one: it fails if the call site
and the callee ever drift apart again, without needing a live broker.
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


class TestPlaceRealOrderCallSite:

    def test_call_site_kwargs_all_exist_on_the_callee(self):
        """Statically bind route_decision's call to place_real_order's signature."""
        src = inspect.getsource(route_decision)
        tree = ast.parse(inspect.cleandoc(src))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == "place_real_order"
        ]
        assert calls, "route_decision no longer calls place_real_order — update this test"

        accepted = set(inspect.signature(place_real_order).parameters)
        for call in calls:
            passed = {k.arg for k in call.keywords if k.arg}
            unknown = passed - accepted
            assert not unknown, (
                f"route_decision passes {sorted(unknown)} to place_real_order, which "
                f"accepts {sorted(accepted)}. This is exactly the D2 defect."
            )

    def test_signal_is_forwarded_so_the_confidence_gate_is_armed(self):
        """place_real_order's Rule 3 reads confidence off `signal`.

        With signal=None it defaults to 100.0 and the gate is a no-op, so the
        fix must pass the signal through rather than merely dropping the bad
        kwargs.
        """
        src = inspect.getsource(route_decision)
        tree = ast.parse(inspect.cleandoc(src))
        call = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == "place_real_order"
        )
        assert "signal" in {k.arg for k in call.keywords if k.arg}


class TestRouteDecisionLiveBranch:

    @pytest.mark.asyncio
    async def test_live_route_reaches_executor_without_typeerror(self):
        captured = {}

        async def _fake_place_real_order(symbol, transaction_type, quantity, session, **kw):
            # Bind against the REAL signature — a bad kwarg raises here, as in prod.
            inspect.signature(place_real_order).bind(
                symbol, transaction_type, quantity, session, **kw
            )
            captured.update(kw)
            return {"order_id": "ORDER-1", "symbol": symbol, "qty": quantity}

        with patch("engine.decision_router.resolve_mode",
                   AsyncMock(return_value=TradeMode.LIVE)), \
             patch("engine.zerodha_executor.place_real_order",
                   AsyncMock(side_effect=_fake_place_real_order)), \
             patch("engine.decision_router._log_decision_audit", AsyncMock()), \
             patch("utils.config.settings.ZERODHA_ACCESS_TOKEN", "tok"), \
             patch("utils.config.settings.LIVE_CONFIDENCE_THRESHOLD", 10.0):
            result = await route_decision(
                _signal(), MagicMock(), position_size={"units": 3, "usd_value": 300.0},
            )

        assert result.outcome is not RoutingOutcome.ERROR, (
            f"live route errored: {result.reason} — D2 has regressed"
        )
        assert "signal_id" not in captured and "confidence" not in captured
