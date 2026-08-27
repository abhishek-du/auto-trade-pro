"""The NSE announcement consumer must not sit behind the RSS LLM loop.

MEASURED 2026-08-27:
    NSE published            203 filings
    crawler fetched           33 high-impact
    queue depth              33/200, STATIC across polls 62, 63, 64
    news_items stored          3

The consumer ran twice all day — 08:30 and 08:54 — both BEFORE the open, which
is exactly when section 1 does no LLM work because market_open is False. From
09:15 onward section 1 never finished a pass and the announcement block was
never reached.

The cause was ordering, not capacity. Section 1 is an unbounded
`for article in new_articles: await process_ticker(...)` where every iteration
is a full LLM ReAct loop (up to 20 rounds, force-decide at 12). The
announcement block sat after it.

The file's own comment already recorded the hazard — "this block sits after
section 1 and section 1 awaits an LLM ReAct loop per article" — and the FETCH
was moved out to _nse_announcement_poller() for that reason. The CONSUMER was
left behind. This pins the other half of that fix.

Same failure shape as the Celery starvation this codebase has fixed three times
(fast_sl_check, tactical scans, india_trade_loop): a latency-sensitive consumer
sharing a serial lane with a slow one.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import news_discovery_engine as nde


def _cycle_tree():
    return ast.parse(textwrap.dedent(inspect.getsource(nde._news_discovery_cycles)))


class TestAnnouncementsGoFirst:
    def test_the_consumer_is_its_own_function(self):
        """Extracted so it can be ordered, tested and called independently."""
        assert callable(getattr(nde, "_process_nse_announcements", None))

    def test_called_before_the_rss_article_loop(self):
        """The whole fix, as one assertion. Uses the AST, not a substring —
        the surrounding comments mention both by name."""
        tree = _cycle_tree()
        call_lines, loop_lines = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "_process_nse_announcements":
                call_lines.append(node.lineno)
            if isinstance(node, ast.For):
                for t in ast.walk(node.iter):
                    if isinstance(t, ast.Name) and t.id == "new_articles":
                        loop_lines.append(node.lineno)
        assert call_lines, "the announcement consumer is never called in the cycle"
        assert loop_lines, "the RSS article loop vanished — check this test's anchors"
        assert min(call_lines) < min(loop_lines), (
            "announcements are behind the RSS LLM loop again; that is the "
            "33-fetched/3-stored bug"
        )

    def test_it_drains_the_queue(self):
        src = inspect.getsource(nde._process_nse_announcements)
        assert "_drain_nse_queue()" in src

    def test_the_old_inline_block_is_gone(self):
        """Exactly one drain call in the cycle path — no duplicate consumer
        that could double-process a filing."""
        whole = inspect.getsource(nde)
        assert whole.count("_drain_nse_queue()") == 1


class TestFailureIsolation:
    def test_a_consumer_failure_cannot_kill_the_cycle(self):
        """It runs first now, so an unhandled raise would cost the ENTIRE
        cycle — RSS, anomaly scan and re-entry watches included."""
        tree = _cycle_tree()
        guarded = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                        and sub.func.id == "_process_nse_announcements":
                    guarded = any(
                        h.type is None
                        or (isinstance(h.type, ast.Name) and h.type.id == "Exception")
                        for h in node.handlers
                    )
        assert guarded, "the announcement call must be wrapped in try/except"

    def test_the_poller_is_still_independent(self):
        """Supply and consumption stay decoupled; this fix only reorders the
        consumer."""
        assert callable(getattr(nde, "_nse_announcement_poller", None))
        src = inspect.getsource(nde._nse_announcement_poller)
        assert "put_nowait" in src


class TestUnchanged:
    def test_market_hours_gate_is_still_strict(self):
        """The consumer still receives market_open and must not invent its own
        definition -- 15:30-16:00 IST once opened a live position."""
        sig = inspect.signature(nde._process_nse_announcements)
        assert "market_open" in sig.parameters
        src = inspect.getsource(nde._process_nse_announcements)
        assert "is_nse_market_open" not in src

    def test_closed_market_still_queues_rather_than_trades(self):
        src = inspect.getsource(nde._process_nse_announcements)
        assert "PreMarketNewsQueue" in src
        assert "if market_open:" in src

    def test_nse_category_still_decides_direction(self):
        """ORDER_WIN under NSE's label measured +1.053% vs -0.245% under ours.
        Reordering must not disturb that."""
        src = inspect.getsource(nde._process_nse_announcements)
        assert "resolve_nse_direction" in src or "ann['category']" in src

    def test_seq_id_dedup_survives(self):
        src = inspect.getsource(nde._process_nse_announcements)
        assert "_processed_seq_ids" in src
