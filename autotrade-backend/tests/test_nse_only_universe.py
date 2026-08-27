"""BSE is out of the universe (operator decision, 2026-08-27).

EVIDENCE: 807 .BO symbols sat in hub_universe carrying ZERO intraday data while
an NSE twin existed, and 29 of 136 LLM evaluations that session were .BO
symbols structurally incapable of price/volume validation — each one consumed a
decision slot and could never produce a verifiable signal.

TRADE-OFF, stated rather than hidden: 2,476 BSE listings had an NSE twin and
lose nothing. 10,559 were BSE-only and leave the universe entirely. That is the
intended consequence of the flag, not an accident.

The flag is a flag, not a deletion, so the scope is reversible in code even
though the rows are gone.
"""
from __future__ import annotations

import ast
import inspect
import textwrap


def _code_only(obj) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                node.body = b[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestFlag:
    def test_default_is_nse_only(self):
        from utils.config import Settings

        assert Settings.model_fields["NSE_ONLY_UNIVERSE"].default is True

    def test_runtime_is_nse_only(self):
        from utils.config import settings

        assert settings.NSE_ONLY_UNIVERSE is True


class TestUniverseRebuild:
    def test_scope_is_driven_by_the_flag_not_hardcoded(self):
        import engine.hub_universe as hu

        src = _code_only(hu.rebuild_hub_universe)
        assert "NSE_ONLY_UNIVERSE" in src

    def test_nse_only_branch_excludes_bo(self):
        import engine.hub_universe as hu

        src = _code_only(hu.rebuild_hub_universe)
        i = src.index("_scope")
        seg = src[i:i + 260]
        assert "symbol LIKE '%.NS'" in seg
        assert "OR symbol LIKE '%.BO'" in seg, (
            "the dual-exchange branch must still exist so the scope is reversible"
        )

    def test_the_scope_is_logged(self):
        import engine.hub_universe as hu

        assert "exchange scope" in inspect.getsource(hu.rebuild_hub_universe)


class TestF1ReadPath:
    def test_f1_filters_bo_defensively(self):
        """hub_universe is rebuilt daily; without this a stale .BO row would
        keep being scanned until the next rebuild lands."""
        import engine.tactical_data_fetcher as tdf

        src = _code_only(tdf.get_f1_universe)
        assert "nse_only" in src
        assert "h.symbol LIKE '%.NS'" in src

    def test_the_filter_is_parameterised_not_a_literal(self):
        import engine.tactical_data_fetcher as tdf

        src = _code_only(tdf.get_f1_universe)
        assert "NOT :nse_only" in src


class TestNormaliserStillHandlesBo:
    """The rows are gone; the CODE must still parse .BO safely.

    Historical paper_trades and tactical_signals retain .BO symbols as audit
    history, and anything reading them must not crash or double-suffix.
    """

    def test_bo_symbols_still_normalise(self):
        from utils.symbols import normalize, strip_suffix

        assert normalize("NHCFOODS.BO") == "NHCFOODS.BO"
        assert strip_suffix("NHCFOODS.BO") == "NHCFOODS"

    def test_no_double_suffix_on_historical_bo(self):
        from utils.symbols import normalize

        assert normalize("NHCFOODS.BO").count(".") == 1
