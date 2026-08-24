"""Section 2 of the news-discovery loop must be reachable.

The loop fetches RSS (section 1) and then NSE corporate announcements
(section 2). Both sat inside one try/except whose handler sleeps to the next
cycle, so anything raised in section 1 skipped section 2 entirely.

That is what happened: `uq_news_items_headline_day` is a unique index on
(md5(headline), date), RSS feeds re-serve the same story every cycle, and the
ORM insert raised UniqueViolationError on the first repeat. Measured
consequence — NSE announcements stopped being ingested after 2026-08-21 03:29
while the loop logged its RSS fetches normally and looked healthy. Called
directly, the exchange endpoint returned that day's filings immediately.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "news_discovery_engine.py"


def _loop_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse(SRC.read_text())
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_news_discovery_loop"),
        None,
    )
    assert fn is not None, "run_news_discovery_loop() not found"
    return fn


def test_rss_insert_tolerates_duplicate_headlines():
    """A repeated headline must not raise — it must be skipped.

    Without ON CONFLICT DO NOTHING one duplicate aborts the transaction AND,
    because of the shared handler, the rest of the cycle. The celery crawler
    was given this treatment on 2026-08-20; the engine's own insert was missed.
    """
    src = SRC.read_text()
    assert "on_conflict_do_nothing" in src, (
        "the engine's news_items insert is back to a plain ORM add — one "
        "duplicate RSS headline will abort the cycle before the NSE fetch"
    )
    fn = _loop_fn()
    adds = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add"
        and any(isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                and a.func.id == "NewsItem" for a in n.args)
    ]
    assert not adds, (
        "session.add(NewsItem(...)) found in the loop — that is the path that "
        "raised UniqueViolationError and starved the announcement feed"
    )


def test_the_nse_fetch_is_not_nested_inside_the_rss_error_path():
    """Section 2 must sit outside whatever guards section 1.

    If the NSE fetch is nested inside section 1's try block, an RSS failure
    still skips it and the fix is cosmetic.
    """
    fn = _loop_fn()

    def contains_nse_fetch(node) -> bool:
        return any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "fetch_nse_corporate_announcements"
            for n in ast.walk(node)
        )

    assert contains_nse_fetch(fn), "the loop no longer fetches NSE announcements at all"

    # Any try whose handler is the RSS guard must NOT contain the NSE fetch.
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        names = {
            h.name for h in node.handlers if h.name
        }
        if "_rss_exc" not in names:
            continue
        for stmt in node.body:
            assert not contains_nse_fetch(stmt), (
                "the NSE announcement fetch is inside the RSS guard — an RSS "
                "error would skip it, which is the bug this guard exists to fix"
            )


def test_rss_handling_is_guarded_at_all():
    """There must be a guard, or the next unexpected RSS error repeats history."""
    fn = _loop_fn()
    guards = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Try)
        and any(h.name == "_rss_exc" for h in n.handlers if h.name)
    ]
    assert guards, (
        "RSS article handling is unguarded — any error there will again skip "
        "every later section of the cycle, including the NSE fetch"
    )
