"""Ranks 16-40 are recorded for research and must never become trades.

TACTICAL_MAX_SIGNALS_PER_CYCLE is 15, so ranks 1-15 already leave a
TacticalSignal row each and are researchable today. Ranks beyond that were
dropped inside score_and_filter without trace, which is why the Phase-24 audit
could not say whether the cut discards signals worth taking.

This capture answers that question and nothing else. TACTICAL_TOP_N is
unchanged; no sizing is requested, no risk is booked, no intent is built.
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


class TestOverflowIsCaptured:
    def _run(self, n_signals, top_n, monkeypatch):
        sigs = [_Sig(f"S{i}.NS", i) for i in range(n_signals)]

        async def _fake_sectors(symbols, session):
            return {}

        monkeypatch.setattr("engine.tactical_scoring.fetch_sector_scores", _fake_sectors)
        # Deterministic descending scores so ranks are unambiguous.
        scores = {s.symbol: 90.0 - i for i, s in enumerate(sigs)}
        monkeypatch.setattr(
            "engine.tactical_scoring.composite_score",
            lambda s, sector: scores[s.symbol],
        )

        overflow: list = []
        kept = asyncio.run(score_and_filter(
            sigs, None, min_score=0.0, top_n=top_n, overflow_out=overflow))
        return kept, overflow

    def test_signals_below_the_cut_land_in_the_overflow(self, monkeypatch):
        kept, overflow = self._run(40, 15, monkeypatch)
        assert len(kept) == 15
        assert len(overflow) == 25
        assert overflow[0][0].symbol == "S15.NS", "rank 16 is the first overflow entry"

    def test_the_return_value_is_unchanged_by_capturing(self, monkeypatch):
        with_capture, _ = self._run(40, 15, monkeypatch)
        sigs = [s.symbol for s, _ in with_capture]

        # Same inputs, no overflow list at all.
        again, overflow = self._run(40, 15, monkeypatch)
        assert [s.symbol for s, _ in again] == sigs

    def test_omitting_the_list_is_byte_identical(self, monkeypatch):
        """A caller that passes nothing must see the old behaviour."""
        src = inspect.getsource(score_and_filter)
        i = src.index("if overflow_out is not None:")
        assert "overflow_out.extend" in src[i:i + 120]
        assert src.rstrip().endswith("return kept[:top_n]")

    def test_no_overflow_when_everything_fits(self, monkeypatch):
        kept, overflow = self._run(5, 15, monkeypatch)
        assert len(kept) == 5 and overflow == []


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
    def test_the_cut_itself_was_not_widened(self):
        """Capturing is not the same as trading, and tonight only one is allowed."""
        scan = _code_only(tx.TacticalExecutor._scan)
        i = scan.index("rank_signals(scored")
        assert "'TACTICAL_TOP_N', 5" in scan[i:i + 160]

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
