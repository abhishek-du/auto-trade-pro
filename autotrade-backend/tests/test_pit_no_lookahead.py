"""The point-in-time dataset must be incapable of look-ahead.

A backtest that peeks is not merely wrong, it is CONFIDENTLY wrong — it
produces a fake-good number that survives review because nobody can see the
leak. These tests make the leak paths structural rather than a matter of care.

THREE LEAK PATHS, EACH CLOSED:

  1. A candle bar stamped after T's close. Every candle query is bounded by a
     PARAMETER, never now(), so re-running tomorrow yields the identical row.

  2. News that claims an early publication time. This codebase has already had
     exactly that bug: _parse_nse_announcement_dt once wrote IST wall-clock
     into a UTC column, putting 4,159 rows (15.3% of all news_items with a
     published_at) 5h30m in the FUTURE relative to their own crawled_at. So
     admissibility requires published_at <= close AND crawled_at <= close: we
     must be able to prove we had SEEN it.

  3. A stale price reference. The candles table carries two parallel 1d series
     (00:00 and 18:30 UTC). The 00:00 series has 4.85M rows but only FOUR
     symbols updated in the last ten days, and its stale bars are PRE-corporate
     -action. Selecting it gave JLHL.NS a close of 1348 against a real 320 and
     manufactured a +1.9% median overnight edge across the whole dataset. That
     result was retracted; these tests stop it recurring.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect
import textwrap

import pytest

import scripts.research.pit_dataset as pit


def _code(fn) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                node.body = b[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestFeatureQueriesAreBounded:
    def test_daily_bars_bounded_by_a_parameter(self):
        src = _code(pit._daily_bars)
        assert "timestamp <= :b" in src
        assert "now()" not in src and "utcnow" not in src, (
            "a wall-clock bound makes the row depend on when it was built"
        )

    def test_events_bounded_at_both_ends(self):
        src = _code(pit._events_at_t)
        assert "ce.created_at <= :close" in src

    def test_news_must_have_been_SEEN_not_merely_stamped(self):
        """published_at alone is not proof — see the 4,159-row timezone bug."""
        src = _code(pit._events_at_t)
        assert "ni.published_at <= :close" in src
        assert "ni.crawled_at <= :close" in src


class TestLabelsComeOnlyFromT1:
    def test_label_window_is_the_t1_session(self):
        src = _code(pit._t1_bars)
        assert "timestamp >= :a" in src and "timestamp <= :b" in src

    def test_features_never_touch_t1(self):
        """_feat receives daily bars only; it has no access to a T+1 handle."""
        sig = inspect.signature(pit._feat)
        assert list(sig.parameters) == ["bars"]
        src = _code(pit._feat)
        for banned in ("t1", "_t1_bars", "T1"):
            assert banned not in src, f"feature builder references {banned}"

    def test_feature_fields_are_all_t_suffixed(self):
        """Naming carries the contract: every feature ends _t, every label t1_."""
        names = [f for f in pit.Row.__dataclass_fields__]
        feats = [n for n in names if n.endswith("_t")]
        labels = [n for n in names if n.startswith("t1_")]
        assert len(feats) >= 8 and len(labels) >= 8
        assert not (set(feats) & set(labels))


class TestStaleReferenceGuard:
    def test_the_dead_daily_series_is_not_selected_by_hour(self):
        src = _code(pit._daily_bars)
        assert "extract(hour from timestamp) = 0" not in src, (
            "hour=0 is the DEAD series carrying pre-split prices"
        )
        assert "DISTINCT ON" in src, "must take the latest bar per calendar date"

    def test_an_implausible_gap_is_dropped_not_recorded(self):
        src = _code(pit.build)
        assert "stale_reference" in src
        assert "0.35" in src

    def test_exclusions_are_counted_not_silent(self):
        src = _code(pit.build)
        for k in ("unresolved", "ambiguous", "no_features", "no_labels"):
            assert k in src


class TestIdentityDiscipline:
    def test_unresolved_symbols_are_excluded_never_guessed(self):
        src = _code(pit.build)
        assert "resolve_identity" in src
        assert "if not r.ok:" in src

    def test_ambiguous_is_tracked_separately_from_unresolved(self):
        src = _code(pit.build)
        # ast.unparse normalises quotes, so match on the semantics not the text.
        assert "needs_review" in src
        assert "'ambiguous'" in src and "'unresolved'" in src


class TestDeterminism:
    def test_the_ist_boundary_is_fixed_not_derived_from_today(self):
        assert pit._CLOSE_IST == dt.time(15, 30)
        assert pit._OPEN_IST == dt.time(9, 15)

    def test_ist_to_utc_is_pure(self):
        d = dt.date(2026, 8, 27)
        a = pit.ist_to_utc(d, pit._CLOSE_IST)
        assert a == dt.datetime(2026, 8, 27, 10, 0)
        assert pit.ist_to_utc(d, pit._CLOSE_IST) == a

    def test_costs_match_the_live_simulator(self):
        from paper_trading.trade_simulator import estimate_trade_cost

        qty, px = 100, 500.0
        rt = (estimate_trade_cost(qty, px, "BUY", "MIS")
              + estimate_trade_cost(qty, px, "SELL", "MIS")) / (qty * px)
        assert abs(rt - pit.COST_PCT["MIS"]) < 0.0004


class TestResearchOnly:
    def test_imports_no_execution_path(self):
        tree = ast.parse(inspect.getsource(pit))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module)
            elif isinstance(n, ast.Import):
                mods.update(a.name for a in n.names)
        for banned in ("engine.decision_router", "engine.zerodha_executor",
                       "paper_trading.position_tracker", "engine.tactical_executor"):
            assert banned not in mods

    def test_writes_no_database_rows(self):
        src = inspect.getsource(pit).lower()
        for verb in ("insert into", "update ", "delete ", "drop ", "alter "):
            assert verb not in src
