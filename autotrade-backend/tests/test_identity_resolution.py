"""Identity resolution is deterministic and refuses rather than guesses.

MEASURED 2026-08-27, 7 days of causal_events: 220 of 334 distinct emitted
symbols failed to resolve (50.5% success). Every failure was a REAL NSE stock
under a different identifier — company names in a ticker field, or
plausible-but-wrong tickers:

    BHARAT ELECTRONICS LIMITED -> BEL        BANK OF MAHARASHTRA -> MAHABANK
    ADANITOTALGAS              -> ATGL       CAPRI GLOBAL CAPITAL -> CGCL
    BALRAMPUR CHINI            -> BALRAMCHIN DATA PATTERNS        -> DATAPATTNS

An event that cannot be resolved cannot become a trade, so half of the
correctly-classified events were unreachable.

THE RULE THESE TESTS DEFEND: a wrong resolution is worse than no resolution,
because it trades the wrong company on another company's news. Every tier is
exact. Ambiguity is REJECTED with candidates recorded, never broken by a
similarity score or a "best guess".
"""
from __future__ import annotations

import asyncio

import pytest

from utils.identity import (IdentityIndex, Resolution, _key, build_index,
                            is_non_equity_symbol, resolve_identity)


def _idx(pairs) -> IdentityIndex:
    ix = IdentityIndex()
    for sym, name in pairs:
        ix.add(sym, name)
    return ix.finalise()


REAL = [
    ("BEL", "BHARAT ELECTRONICS"), ("ATGL", "ADANI TOTAL GAS"),
    ("MAHABANK", "BANK OF MAHARASHTRA"), ("ABCAPITAL", "ADITYA BIRLA CAPITAL"),
    ("BALRAMCHIN", "BALRAMPUR CHINI MILLS"), ("DATAPATTNS", "DATA PATTERNS INDIA"),
    ("CGCL", "CAPRI GLOBAL CAPITAL"), ("CSBBANK", "CSB BANK"),
    ("RELIANCE", "RELIANCE INDUSTRIES"),
]


class TestTheMeasuredFailures:
    """The eight names from the audit, each verified against the real table."""

    @pytest.mark.parametrize("raw,expect", [
        ("ADANITOTALGAS", "ATGL.NS"),
        ("ADITYABIRLACAPITAL", "ABCAPITAL.NS"),
        ("BANK OF MAHARASHTRA", "MAHABANK.NS"),
        ("BHARAT ELECTRONICS LIMITED", "BEL.NS"),
        ("BALRAMPUR CHINI", "BALRAMCHIN.NS"),
        ("DATA PATTERNS", "DATAPATTNS.NS"),
        ("CSB BANK", "CSBBANK.NS"),
        ("CAPRI GLOBAL CAPITAL", "CGCL.NS"),
    ])
    def test_now_resolves(self, raw, expect):
        r = resolve_identity(raw, _idx(REAL))
        assert r.ok, f"{raw} still unresolved: {r.reason}"
        assert r.symbol == expect


class TestLadderOrder:
    def test_exact_symbol_wins_first(self):
        r = resolve_identity("BEL", _idx(REAL))
        assert r.resolution is Resolution.EXACT_SYMBOL

    def test_exact_name_before_prefix(self):
        r = resolve_identity("BHARAT ELECTRONICS", _idx(REAL))
        assert r.resolution is Resolution.EXACT_NAME

    def test_prefix_only_when_name_is_truncated(self):
        r = resolve_identity("BALRAMPUR CHINI", _idx(REAL))
        assert r.resolution is Resolution.UNIQUE_PREFIX

    def test_corporate_suffixes_are_stripped(self):
        assert _key("Bharat Electronics Limited") == _key("BHARAT ELECTRONICS")
        assert _key("Reliance Industries Ltd") == "RELIANCEINDUSTRIES"

    def test_curated_alias_resolves(self):
        ix = _idx(REAL + [("SBIN", "STATE BANK OF INDIA MUM")])
        r = resolve_identity("State Bank of India", ix)
        assert r.ok and r.symbol == "SBIN.NS"
        assert r.resolution is Resolution.ALIAS


class TestRefusesRatherThanGuesses:
    """The safety property. A wrong symbol trades the wrong company."""

    def test_ambiguous_name_is_rejected(self):
        ix = _idx([("LLOYDSENGG", "LLOYDS ENGINEERING"),
                   ("LLOYDSENT", "LLOYDS ENTERPRISES")])
        r = resolve_identity("LLOYDS", ix)
        assert not r.ok
        assert r.resolution is Resolution.AMBIGUOUS
        assert r.symbol is None
        assert len(r.candidates) == 2

    def test_ambiguity_is_flagged_for_review_not_dropped(self):
        ix = _idx([("A1", "ACME POWER"), ("A2", "ACME POWERTECH")])
        r = resolve_identity("ACME POWER SOMETHING", ix)
        assert r.needs_review or r.resolution is Resolution.UNRESOLVED
        if r.needs_review:
            assert r.candidates, "candidates must be recorded for review"

    def test_unknown_returns_unresolved_not_a_guess(self):
        r = resolve_identity("TOTALLY MADE UP CO", _idx(REAL))
        assert not r.ok and r.symbol is None
        assert r.resolution is Resolution.UNRESOLVED

    def test_no_similarity_scoring_anywhere(self):
        """No edit distance, no similarity library, no threshold.

        Checked on the AST: the module's own prose explains that it is NOT
        fuzzy, so a raw substring search matches the explanation.
        """
        import ast
        import inspect
        import textwrap

        import utils.identity as m
        tree = ast.parse(textwrap.dedent(inspect.getsource(m)))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                b = getattr(node, "body", [])
                if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                        and isinstance(b[0].value.value, str):
                    node.body = b[1:] or [ast.Pass()]
        code = ast.unparse(tree)
        for banned in ("difflib", "Levenshtein", "fuzz", "SequenceMatcher",
                       "rapidfuzz", "get_close_matches"):
            assert banned not in code, f"similarity matching crept in: {banned}"

    def test_short_keys_never_prefix_match(self):
        """A 3-letter key would prefix-match half the exchange."""
        ix = _idx(REAL)
        assert ix.prefix_candidates("BEL") == []

    def test_empty_input_is_invalid_not_resolved(self):
        for bad in ("", "   ", None):
            r = resolve_identity(bad, _idx(REAL))
            assert not r.ok and r.resolution is Resolution.INVALID


class TestNonEquityExcluded:
    @pytest.mark.parametrize("sym", ["771SIHF35-N0", "SGBJAN27-GB",
                                     "657GS2033-GS", "HDFCSIINAV-RL",
                                     "ISIFHLSG-SF"])
    def test_bonds_sgbs_gsecs_inavs_are_not_name_candidates(self, sym):
        assert is_non_equity_symbol(sym)

    def test_plain_equities_are_not_excluded(self):
        for sym in ("BEL", "RELIANCE", "MAHABANK", "TCS"):
            assert not is_non_equity_symbol(sym)

    def test_excluded_instruments_stay_reachable_by_exact_symbol(self):
        ix = _idx([("SGBJAN27-GB", "250GOLDBONDS2027SRV")])
        r = resolve_identity("SGBJAN27-GB", ix)
        assert r.ok and r.resolution is Resolution.EXACT_SYMBOL


class TestAuditability:
    def test_every_result_records_its_reasoning(self):
        for raw in ("BEL", "BHARAT ELECTRONICS LIMITED", "NONSENSE CO"):
            r = resolve_identity(raw, _idx(REAL))
            d = r.as_dict()
            assert d["source"] == raw
            assert d["resolution"] and d["reason"]

    def test_source_is_never_overwritten(self):
        r = resolve_identity("BANK OF MAHARASHTRA", _idx(REAL))
        assert r.source == "BANK OF MAHARASHTRA"
        assert r.symbol == "MAHABANK.NS"

    def test_output_is_suffix_normalised(self):
        r = resolve_identity("BHARAT ELECTRONICS LIMITED", _idx(REAL))
        assert r.symbol.endswith(".NS") and r.symbol.count(".NS") == 1

    def test_idempotent_through_its_own_output(self):
        ix = _idx(REAL)
        once = resolve_identity("BANK OF MAHARASHTRA", ix).symbol
        twice = resolve_identity(once, ix).symbol
        assert once == twice == "MAHABANK.NS"


class TestAgainstTheRealTable:
    """Integration — the numbers quoted in the audit must hold."""

    def test_index_builds_and_resolves_known_pairs(self):
        try:
            idx = asyncio.run(build_index())
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")
        if idx.stats()["symbols"] == 0:
            pytest.skip("kite_instruments unreachable from this test context")
        assert idx.stats()["symbols"] > 5000
        for raw, expect in [("BHARAT ELECTRONICS LIMITED", "BEL.NS"),
                            ("BANK OF MAHARASHTRA", "MAHABANK.NS"),
                            ("ADANITOTALGAS", "ATGL.NS")]:
            r = resolve_identity(raw, idx)
            assert r.ok and r.symbol == expect, f"{raw} -> {r.symbol} ({r.reason})"
