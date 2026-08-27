"""The pre-market queue drain must only replay genuinely overnight news.

`premarket_news_queue` exists, per its own model docstring, to hold "high-impact
news captured outside of trading hours for processing at market open". Nothing
bounded how old a PENDING row could be, and the drain had no cutoff.

Measured in production on 2026-08-26:

  * 2,451 PENDING rows, the oldest captured 2026-08-14 — twelve days.
  * The engine log announced "Processing 2611 queued" on 24 separate occasions,
    i.e. it re-read the same backlog from the start every cycle.
  * 65,270 queue items were drained against only 570 process_ticker
    invocations, so the loop never approached the end of the backlog.
  * Live NSE corporate announcements reached process_ticker 4 times in 7 days.

Each drained item costs a full LLM ReAct loop, so the engine was spending its
budget re-deciding twelve-day-old headlines instead of reaching live news.

These tests pin the cutoff. They assert the SQL contract of the drain, not the
network or the LLM.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect

import news_discovery_engine as nde


class TestCutoffConstant:
    def test_constant_exists_and_is_generous_enough_for_a_long_weekend(self):
        """A Friday-evening filing drained on Monday morning is ~66 hours old."""
        assert hasattr(nde, "_PREMARKET_MAX_AGE_DAYS")
        days = nde._PREMARKET_MAX_AGE_DAYS
        assert isinstance(days, int)
        assert days >= 3, (
            f"{days} days is too tight: Friday 15:30 to Monday 09:15 is ~66 "
            f"hours, so a shorter window would silently drop genuine weekend news"
        )
        assert days <= 7, (
            f"{days} days is too loose: the production backlog that motivated "
            f"this reached twelve days"
        )

    def test_three_days_covers_a_friday_to_monday_gap(self):
        """Concrete instance of the boundary the constant has to clear."""
        friday_close = dt.datetime(2026, 8, 21, 15, 30)
        monday_open = dt.datetime(2026, 8, 24, 9, 15)
        gap = monday_open - friday_close
        assert gap < dt.timedelta(days=nde._PREMARKET_MAX_AGE_DAYS), (
            f"the weekend gap is {gap}, which the cutoff must not exclude"
        )


class TestDrainQuery:
    """The drain must filter on captured_at, not just status."""

    def _drain_source(self) -> str:
        """The drain lives in _news_discovery_cycles(), not the outer loop."""
        src = inspect.getsource(nde._news_discovery_cycles)
        assert "PreMarketNewsQueue" in src, "drain block not found"
        return src

    def test_drain_filters_on_captured_at(self):
        src = self._drain_source()
        assert "captured_at" in src, (
            "the drain still selects every PENDING row regardless of age — the "
            "exact defect this guards"
        )

    def test_drain_uses_the_named_constant_not_a_literal(self):
        src = self._drain_source()
        assert "_PREMARKET_MAX_AGE_DAYS" in src, (
            "the cutoff must come from the named constant so it is greppable "
            "and changeable in one place"
        )

    def test_drain_still_filters_on_pending_status(self):
        """The age cutoff must be an ADDITIONAL filter, not a replacement."""
        src = self._drain_source()
        assert '"PENDING"' in src or "'PENDING'" in src

    def test_drain_does_not_delete_or_expire_rows(self):
        """Skipped rows stay PENDING — the change must be reversible by revert.

        Marking or deleting them would make the fix irreversible and would be a
        production data mutation, which this deliberately is not.
        """
        src = self._drain_source()
        tree = ast.parse(src.lstrip())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                assert "delete" not in name.lower(), (
                    f"the drain must not delete queue rows, found {name}"
                )
        assert "EXPIRED" not in src, (
            "skipped rows must be left PENDING, not re-labelled"
        )


class TestProcessedMarking:
    """Each item is marked PROCESSED in its own transaction (2026-08-26).

    Before that, a single commit at the end of the loop meant a failure on item
    N rolled back items 1..N-1 whose trades had already been placed — they were
    left PENDING and re-processed on the next cycle. That is the mechanism that
    kept the backlog pinned at 2,611.
    """

    def test_each_item_is_committed_individually(self):
        """Comment-proof. A fixed character window silently broke when a
        provenance comment was added above the marking on 2026-08-27 — the
        invariant held, the window just stopped reaching it. Comments and
        docstrings are stripped so only executable code is measured."""
        import ast
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(nde._news_discovery_cycles)))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                b = node.body
                if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                        and isinstance(b[0].value.value, str):
                    node.body = b[1:] or [ast.Pass()]
        src = ast.unparse(tree)
        drain_idx = src.find("queued night/pre-market")
        assert drain_idx > 0, "drain log line not found"
        window = src[drain_idx: drain_idx + 2000]
        assert "PROCESSED" in window
        assert "commit" in window, (
            "the per-item PROCESSED marking must commit inside the loop, or a "
            "mid-loop failure loses all progress"
        )
