"""One normaliser, suffix-safe and idempotent (Phase 27, F2 + F3).

Two measured production defects share one root cause — code that appends an
exchange suffix without checking for one:

  * direct_news_strategy built f"{ticker}.NS" from "GKENERGY.NS", giving
    "GKENERGY.NS.NS". No candles exist for that, so hist_df was None and the
    entire block behind `if hist_df is not None` never ran. The 20-EMA trend
    filter and the volume-confirmation filter were not failing — they were
    never executing.
  * 807 .BO symbols carry no intraday data while an NSE twin exists; 29 of 136
    LLM evaluations on 2026-08-27 were structurally unable to validate
    price/volume.
"""
from __future__ import annotations

import asyncio

import pytest

from utils.symbols import (SymbolResolution, has_suffix, is_non_equity,
                           normalize, resolve, strip_suffix)


class TestNoDoubleSuffix:
    """The bug, stated as a test."""

    @pytest.mark.parametrize("sym", ["GKENERGY.NS", "GKENERGY", "GKENERGY.NS.NS",
                                     "gkenergy.ns", "  GKENERGY.NS  "])
    def test_always_exactly_one_suffix(self, sym):
        out = normalize(sym)
        assert out.count(".NS") + out.count(".BO") == 1
        assert not out.endswith(".NS.NS")
        assert not out.endswith(".BO.NS")

    def test_the_exact_production_failure(self):
        assert normalize("GKENERGY.NS") == "GKENERGY.NS"
        assert normalize(normalize("GKENERGY.NS")) == "GKENERGY.NS"

    def test_bse_is_not_silently_converted_by_normalize(self):
        """normalize() must not move an exchange; only resolve() may."""
        assert normalize("NHCFOODS.BO") == "NHCFOODS.BO"

    def test_strip_removes_repeated_suffixes(self):
        assert strip_suffix("GKENERGY.NS.NS") == "GKENERGY"
        assert strip_suffix("X.BO.NS") == "X"


class TestIdempotent:
    @pytest.mark.parametrize("sym", ["J&KBANK", "HUDCO.NS", "NHCFOODS.BO",
                                     "^NSEI", "USDINR=X", "GKENERGY.NS.NS"])
    def test_normalize_twice_equals_once(self, sym):
        assert normalize(normalize(sym)) == normalize(sym)


class TestNonEquity:
    @pytest.mark.parametrize("sym", ["^NSEI", "^INDIAVIX", "USDINR=X"])
    def test_indices_and_fx_get_no_suffix(self, sym):
        assert normalize(sym) == sym.strip().upper()
        assert is_non_equity(sym)


class TestEdges:
    def test_empty_is_not_given_a_suffix(self):
        assert normalize("") == ""
        assert normalize(None) == ""

    def test_has_suffix(self):
        assert has_suffix("X.NS") and has_suffix("X.BO")
        assert not has_suffix("X")


class _FakeSession:
    """Stands in for kite_instruments: `nse` is the set with an NSE listing."""

    def __init__(self, nse):
        self.nse = set(nse)
        self.asked = []

    async def execute(self, stmt, params=None):
        self.asked.append(params["s"])
        found = params["s"] in self.nse

        class _R:
            def first(_self):
                return (1,) if found else None
        return _R()


class TestExchangeResolution:
    """The five dual-listed names the brief names, plus a BSE-only control."""

    DUAL = ["J&KBANK", "HUDCO", "BERGEPAINT", "ADANIENSOL", "BALKRISIND"]

    @pytest.mark.parametrize("bare", DUAL)
    def test_dual_listed_bo_resolves_to_ns(self, bare):
        s = _FakeSession(self.DUAL)
        r = asyncio.run(resolve(f"{bare}.BO", s))
        assert r.canonical_trade_symbol == f"{bare}.NS"
        assert r.exchange_resolution == "NSE"
        assert "dual-listed" in r.resolution_reason

    def test_bse_only_keeps_bo(self):
        s = _FakeSession(self.DUAL)          # NHCFOODS deliberately absent
        r = asyncio.run(resolve("NHCFOODS.BO", s))
        assert r.canonical_trade_symbol == "NHCFOODS.BO"
        assert r.exchange_resolution == "BSE"

    def test_source_symbol_is_preserved_for_audit(self):
        s = _FakeSession(self.DUAL)
        r = asyncio.run(resolve("HUDCO.BO", s))
        assert r.source_symbol == "HUDCO.BO"
        assert r.canonical_trade_symbol == "HUDCO.NS"
        d = r.as_dict()
        assert set(d) == {"source_symbol", "canonical_trade_symbol",
                          "exchange_resolution", "resolution_reason"}

    def test_ns_input_never_hits_the_database(self):
        """Only .BO needs a twin lookup; anything else must not query."""
        s = _FakeSession([])
        asyncio.run(resolve("HUDCO.NS", s))
        assert s.asked == []

    def test_double_suffix_is_repaired_and_reported(self):
        s = _FakeSession([])
        r = asyncio.run(resolve("GKENERGY.NS.NS", s))
        assert r.canonical_trade_symbol == "GKENERGY.NS"
        assert "duplicate suffix" in r.resolution_reason

    def test_lookup_failure_keeps_the_original_exchange(self):
        """Fail OPEN: a DB blip must never move a trade to another exchange."""
        class _Boom:
            async def execute(self, *a, **k):
                raise RuntimeError("db down")

        r = asyncio.run(resolve("HUDCO.BO", _Boom()))
        assert r.canonical_trade_symbol == "HUDCO.BO"
        assert r.exchange_resolution == "BSE"
        assert "unavailable" in r.resolution_reason

    def test_resolution_is_idempotent_too(self):
        s = _FakeSession(self.DUAL)
        once = asyncio.run(resolve("HUDCO.BO", s)).canonical_trade_symbol
        twice = asyncio.run(resolve(once, s)).canonical_trade_symbol
        assert once == twice == "HUDCO.NS"


class TestDirectNewsUsesIt:
    """F3: the call site that broke must go through the normaliser."""

    def _src(self):
        """Executable source only — the comments EXPLAIN the old bug and would
        otherwise match a search for it."""
        import ast
        import inspect
        import textwrap

        import engine.direct_news_strategy as d
        tree = ast.parse(textwrap.dedent(inspect.getsource(d)))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                b = node.body
                if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                        and isinstance(b[0].value.value, str):
                    node.body = b[1:] or [ast.Pass()]
        return ast.unparse(tree)

    def test_no_blind_suffix_append_remains(self):
        src = self._src()
        assert "f'{ticker}.NS'" not in src and 'f"{ticker}.NS"' not in src, (
            "the double-suffix append is back; GKENERGY.NS becomes GKENERGY.NS.NS"
        )

    def test_the_candle_lookup_uses_the_canonical_symbol(self):
        src = self._src()
        i = src.index("to_thread(fetch_nse_candles")
        assert "_canon" in src[i:i + 90]

    def test_it_records_lookup_outcome(self):
        """A silent lookup failure is what hid this for four weeks."""
        src = self._src()
        assert "candle_lookup=" in src
        assert "canonical_symbol=" in src

    def test_filter_thresholds_are_unchanged(self):
        """This is a correctness fix, NOT a strategy change."""
        import inspect

        import engine.direct_news_strategy as d
        src = inspect.getsource(d)
        assert "span=20" in src                 # 20 EMA
        assert "window=20" in src               # 20-day volume baseline
        assert "avg_vol * 0.5" in src           # 50% volume floor
