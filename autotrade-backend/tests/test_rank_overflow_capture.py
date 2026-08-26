"""Ranks 16-40 are recorded for research and must never become trades.

CORRECTED IN PHASE 25.1. The first version of this file asserted TOP_N = 5 and
MAX_SIGNALS_PER_CYCLE = 15. Both were wrong: those numbers are the DEAD
fallback literals inside the _cfg() calls in tactical_executor, and Settings
defines both fields, so getattr never reaches them. The effective values are
TACTICAL_TOP_N = 15 and TACTICAL_MAX_SIGNALS_PER_CYCLE = 40.

That mistake pointed the capture at the wrong band. The pipeline cuts twice:

    score_and_filter   keeps the best 40 above the composite floor
    rank_signals       selects 15 of those; only those 15 are persisted

so ranks 16-40 are dropped at rank_signals, NOT inside score_and_filter. The
capture now derives its band from `scored` minus `ranked`, by object identity,
which is correct whether or not the ML ranker is loaded.

TACTICAL_TOP_N is read and never written; TestTopNIsUnchanged pins that.
"""
from __future__ import annotations

import ast
import asyncio
import textwrap
import inspect
import pathlib

import pytest

from engine import tactical_executor as tx
from engine.tactical_scoring import score_and_filter


class _Sig:
    def __init__(self, symbol, score_hint, side="BUY", entry=100.0, sl=98.0, tgt=104.0):
        self.symbol = symbol
        self.side = side
        self.strategy_name = "ORB"
        self.sub_pipeline = "F1"
        self.entry_price = entry
        self.stop_loss = sl
        self.target = tgt
        self.confidence = 60.0
        self.timestamp = None
        self.meta = {"score_hint": score_hint}


class TestScoringIsUntouched:
    """Phase 25.1 reverted the change to score_and_filter entirely.

    Deriving the band from `scored` minus `ranked` in the executor needs no
    hook inside the scorer, and the hook it originally grew was capturing
    ranks 41+ rather than 16-40.
    """

    def test_score_and_filter_has_no_research_hook(self):
        sig = inspect.signature(score_and_filter)
        assert "overflow_out" not in sig.parameters
        src = inspect.getsource(score_and_filter)
        assert src.rstrip().endswith("return kept[:top_n]")


class TestOverflowIsTheCorrectBand:
    """`scored` minus `ranked` — the signals that were ordered, then not taken."""

    def _band(self, scored, ranked):
        selected = {id(s) for s, _, _ in ranked}
        return [(s, sc) for s, sc in scored if id(s) not in selected]

    def test_first_overflow_entry_is_rank_sixteen(self):
        scored = [(_Sig(f"S{i}.NS", i), 90.0 - i) for i in range(40)]
        ranked = [(s, sc, 0.5) for s, sc in scored[:15]]
        band = self._band(scored, ranked)
        assert len(band) == 25
        assert band[0][0].symbol == "S15.NS", "0-indexed 15 is rank 16"
        assert band[-1][0].symbol == "S39.NS", "the band ends at rank 40"

    def test_nothing_selected_is_ever_in_the_band(self):
        scored = [(_Sig(f"S{i}.NS", i), 90.0 - i) for i in range(40)]
        ranked = [(s, sc, 0.5) for s, sc in scored[:15]]
        band_syms = {s.symbol for s, _ in self._band(scored, ranked)}
        taken = {s.symbol for s, _, _ in ranked}
        assert not (band_syms & taken)

    def test_it_survives_an_ml_ranker_that_reorders(self):
        """rank_signals sorts by ml_prob when a model exists, so a constant
        slice would capture the wrong signals. Identity does not care."""
        scored = [(_Sig(f"S{i}.NS", i), 90.0 - i) for i in range(20)]
        ranked = [(scored[7][0], 83.0, 0.9), (scored[2][0], 88.0, 0.8)]
        band_syms = {s.symbol for s, _ in self._band(scored, ranked)}
        assert "S7.NS" not in band_syms and "S2.NS" not in band_syms
        assert len(band_syms) == 18

    def test_empty_band_when_everything_was_selected(self):
        scored = [(_Sig(f"S{i}.NS", i), 90.0 - i) for i in range(5)]
        ranked = [(s, sc, 0.5) for s, sc in scored]
        assert self._band(scored, ranked) == []

    def test_the_executor_uses_exactly_this_derivation(self):
        src = _code_only(tx.TacticalExecutor._scan)
        i = src.index("_selected = ")
        seg = src[i:i + 260]
        assert "id(sig) for sig, _, _ in ranked" in seg
        assert "if id(sig) not in _selected" in seg

    def test_the_rank_offset_starts_from_the_selected_count(self):
        """kept_n must be len(ranked), not len(scored), or every recorded rank
        is wrong by 25."""
        src = _code_only(tx.TacticalExecutor._scan)
        i = src.index("_capture_rank_overflow")
        assert "len(ranked)" in src[i:i + 120]


def _code_only(fn) -> str:
    """Source with comments AND docstrings stripped.

    A plain `in` test against raw source matches the prose that explains the
    guarantee, which passes or fails for the wrong reason. Unparsing the AST
    leaves only what actually executes.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestCaptureCannotExecute:
    """The reason this is allowed to exist at all."""

    SRC = _code_only(tx.TacticalExecutor._capture_rank_overflow)

    def test_it_builds_no_trade_intent(self):
        for banned in ("TradeIntent", "execute_trade_intent", "route_decision",
                       "place_real_order", "open_paper_trade", "_execute("):
            assert banned not in self.SRC, f"overflow capture references {banned}"

    def test_it_requests_no_sizing_and_books_no_risk(self):
        assert "self.risk" not in self.SRC
        assert ".commit(sizing)" not in self.SRC
        assert "size(" not in self.SRC

    def test_it_creates_no_tactical_signal_row(self):
        """A TacticalSignal row is the audit record of a tradeable candidate."""
        assert "TacticalSignal" not in self.SRC
        assert "SimulationLog" in self.SRC

    def test_it_writes_on_its_own_session(self):
        """The scan's session is asserted elsewhere to hold only signals."""
        assert "AsyncSessionLocal" in self.SRC

    def test_failure_cannot_break_the_scan(self):
        assert "except Exception" in self.SRC
        i = self.SRC.index("except Exception")
        assert "raise" not in self.SRC[i:]

    def test_it_is_bounded(self):
        assert "_RANK_OVERFLOW_CAP" in self.SRC
        assert isinstance(tx._RANK_OVERFLOW_CAP, int) and tx._RANK_OVERFLOW_CAP <= 50

    def test_it_runs_after_the_scan_has_committed(self):
        scan = _code_only(tx.TacticalExecutor._scan)
        assert scan.index("await session.commit()") < scan.index("_capture_rank_overflow")


class TestTopNIsUnchanged:
    """The effective cut is 15, in BOTH modes. Phase 25.1 pins the VALUE, not
    the fallback literal — asserting the literal is what hid the error."""

    def test_the_effective_value_is_fifteen(self):
        from utils.config import settings

        assert settings.TACTICAL_TOP_N == 15

    def test_the_scoring_cut_is_forty(self):
        from utils.config import settings

        assert settings.TACTICAL_MAX_SIGNALS_PER_CYCLE == 40

    @pytest.mark.parametrize("mode", ["CONTROL", "V2"])
    def test_top_n_is_identical_in_both_modes(self, monkeypatch, mode):
        """V2 changes exits. It must not touch the selection cut."""
        from utils.config import settings
        from engine.tactical_executor import _cfg

        monkeypatch.setattr(settings, "TRADING_STRATEGY_MODE", mode, raising=False)
        assert _cfg("TACTICAL_TOP_N", 15) == 15
        assert _cfg("TACTICAL_MAX_SIGNALS_PER_CYCLE", 40) == 40

    def test_the_fallback_literals_match_the_real_fields(self):
        """Dead code today — Settings defines both — but a stale literal here
        is what made Phase 25 report TOP_N as 5 and aim the capture at ranks
        41+ instead of 16-40."""
        from utils.config import settings

        scan = _code_only(tx.TacticalExecutor._scan)
        assert f"'TACTICAL_TOP_N', {settings.TACTICAL_TOP_N}" in scan
        assert (f"'TACTICAL_MAX_SIGNALS_PER_CYCLE', "
                f"{settings.TACTICAL_MAX_SIGNALS_PER_CYCLE}") in scan

    def test_no_write_to_the_cut_anywhere_in_the_executor(self):
        """Checked on the AST. The explanatory comments in the module name both
        settings with an '=' after them, so a raw substring search matches the
        prose and proves nothing."""
        import pathlib

        tree = ast.parse(pathlib.Path(tx.__file__).read_text())
        written = set()
        for n in ast.walk(tree):
            targets = []
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
                targets = [n.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    written.add(t.id)
                elif isinstance(t, ast.Attribute):
                    written.add(t.attr)
        for cut in ("TACTICAL_TOP_N", "TACTICAL_MAX_SIGNALS_PER_CYCLE"):
            assert cut not in written, f"the executor assigns {cut}"

    def test_ranked_still_feeds_persist_not_the_overflow(self):
        scan = _code_only(tx.TacticalExecutor._scan)
        i = scan.index("for signal, composite, ml_prob in ranked:")
        assert "self._persist(" in scan[i:i + 200]
        assert "overflow" not in scan[i:i + 200]


class TestRecordedFields:
    def test_records_what_the_research_needs(self):
        src = TestCaptureCannotExecute.SRC
        for field in ("symbol", "rank", "score", "signal_type", "reference_price",
                      "entry_eligible", "not_persisted_reason"):
            assert f"'{field}'" in src, f"overflow row is missing {field}"
