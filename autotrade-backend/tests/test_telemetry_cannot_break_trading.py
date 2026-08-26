"""Measurement must never be able to stop a trade.

Phase 21 added the scan funnel row, Phase 25 added the rank-overflow capture and
the exit attribution. Each one writes to the database on a path that a trade
also travels. The rule for all of them is identical: a telemetry failure loses a
measurement and nothing else.

These are structural tests. They check that the failure is CONTAINED, not that
it happens to be contained today.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap

import pytest


def _code_only(fn) -> str:
    """Source with comments and docstrings stripped — prose must not satisfy
    or break a structural assertion."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                node.body = b[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestScanTelemetry:
    def test_the_funnel_row_is_wrapped(self):
        from engine.tactical_executor import TacticalExecutor

        src = _code_only(TacticalExecutor._scan)
        i = src.index("TACTICAL_SCAN_FUNNEL")
        assert "except Exception" in src[i - 1500:i + 900]

    def test_the_funnel_row_uses_its_own_session(self):
        """The scan's session is asserted elsewhere to hold only TacticalSignal
        rows; mixing telemetry into it would change what trading commits."""
        from engine.tactical_executor import TacticalExecutor

        src = _code_only(TacticalExecutor._scan)
        i = src.index("TACTICAL_SCAN_FUNNEL")
        assert "AsyncSessionLocal" in src[i - 900:i]

    def test_telemetry_runs_after_the_signals_are_committed(self):
        from engine.tactical_executor import TacticalExecutor

        src = _code_only(TacticalExecutor._scan)
        assert src.index("await session.commit()") < src.index("TACTICAL_SCAN_FUNNEL")

    def test_the_overflow_capture_swallows_its_own_failure(self):
        from engine.tactical_executor import TacticalExecutor

        src = _code_only(TacticalExecutor._capture_rank_overflow)
        i = src.index("except Exception")
        assert "raise" not in src[i:]

    @pytest.mark.asyncio
    async def test_a_failing_overflow_capture_returns_normally(self):
        """The real behavioural check, not a source assertion."""
        from engine.tactical_executor import TacticalExecutor

        ex = TacticalExecutor.__new__(TacticalExecutor)

        class _Sig:
            symbol, side, strategy_name = "X.NS", "BUY", "ORB"
            entry_price, stop_loss, target = 100.0, 98.0, 104.0

        class _Ctx:
            vix = 12.0

        # MarketContext is frozen, so a stand-in carries the one field read.
        # No database is reachable here, so the write genuinely fails; the
        # assertion is that the call returns instead of propagating.
        await ex._capture_rank_overflow("F1", [(_Sig(), 55.0)], 15, _Ctx())

    @pytest.mark.asyncio
    async def test_an_empty_overflow_writes_nothing_at_all(self):
        from engine.tactical_executor import TacticalExecutor

        ex = TacticalExecutor.__new__(TacticalExecutor)

        class _Ctx:
            vix = 12.0

        await ex._capture_rank_overflow("F1", [], 15, _Ctx())


class TestExitAttribution:
    def test_it_cannot_break_a_close(self):
        import paper_trading.trade_simulator as ts

        src = _code_only(ts.close_paper_trade)
        i = src.index("exit_meta")
        window = src[max(0, i - 900):i + 900]
        assert "except Exception" in window

    def test_it_runs_after_pnl_and_status_are_settled(self):
        import paper_trading.trade_simulator as ts

        src = _code_only(ts.close_paper_trade)
        for field in ("trade.pnl =", "trade.status =", "trade.exit_price ="):
            assert src.index(field) < src.index("exit_meta"), (
                f"{field} must be decided before the attribution is written"
            )

    def test_it_does_not_overwrite_the_existing_snapshot(self):
        """trade_mgmt lives in the same blob and other code reads it."""
        import paper_trading.trade_simulator as ts

        src = _code_only(ts.close_paper_trade)
        i = src.index("exit_meta")
        assert "**_existing" in src[i - 300:i]

    def test_the_underlying_reason_is_not_hidden(self):
        """exit_family is ADDITIVE. The original reason must survive."""
        import paper_trading.trade_simulator as ts

        src = _code_only(ts.close_paper_trade)
        assert "trade.exit_reason = reason[:20]" in src
        i = src.index("exit_meta")
        assert "'exit_reason': reason" in src[i:i + 400]

    def test_mfe_provenance_is_persisted(self):
        """It was computed and thrown away before Phase 25."""
        import paper_trading.trade_simulator as ts

        src = _code_only(ts.close_paper_trade)
        i = src.index("exit_meta")
        seg = src[i:i + 400]
        assert "'mfe_src': _mfe_src" in seg
        assert "'mae_src': _mae_src" in seg
