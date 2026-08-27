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


# The cycle body was split out of run_news_discovery_loop() on 2026-08-25
# (Phase 1B task A) so that run_news_discovery_loop() owns only task lifecycle —
# start the NSE poller, run the cycles, cancel the poller.
#
# On 2026-08-27 the announcement half was split out AGAIN, into
# _process_nse_announcements(), and moved to the head of the cycle. It had been
# sitting behind an unbounded `for article in new_articles: await
# process_ticker(...)` loop where each iteration is a full LLM ReAct pass:
# measured that day, 33 filings fetched, queue static at 33/200, 3 stored, and
# the consumer ran twice — both before the open, when the RSS section does no
# LLM work.
#
# NONE of the guarantees below changed. Only their address did. So the anchor
# spans BOTH functions, exactly as this helper's own assertion message asked
# for: keep checking them somewhere rather than deleting them.
_CYCLE_FNS = ("_news_discovery_cycles", "_process_nse_announcements")


def _loop_fn() -> ast.Module:
    """The cycle body and the announcement consumer, as one walkable unit."""
    tree = ast.parse(SRC.read_text())
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.AsyncFunctionDef) and n.name in _CYCLE_FNS]
    names = {n.name for n in found}
    missing = set(_CYCLE_FNS) - names
    assert not missing, (
        f"{sorted(missing)} not found — if the loop body was renamed or "
        f"re-merged, point _CYCLE_FNS at it so these reachability guarantees "
        f"keep being checked somewhere"
    )
    return ast.Module(body=found, type_ignores=[])


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


def test_the_nse_fetch_is_not_reachable_from_the_rss_error_path():
    """Section 2 must not be skippable by anything section 1 does.

    Originally this asserted the fetch sat outside section 1's try block. That
    guaranteed an RSS *exception* could not skip it — but not that a slow
    section 1 could starve it, which is what actually happened: section 1 awaits
    a full LLM ReAct loop per article, and on 2026-08-25 the fetch did not run
    between 09:14:50 and 16:05:29 IST while 619 agent decisions ran in that gap.

    Phase 1B moved the fetch into _nse_announcement_poller(), an independent
    task. That satisfies the original guarantee strictly more strongly — a
    separate task cannot be reached by section 1's control flow at all — so the
    assertion follows the fetch rather than being deleted.
    """
    tree = ast.parse(SRC.read_text())

    def contains_nse_fetch(node) -> bool:
        return any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "fetch_nse_corporate_announcements"
            for n in ast.walk(node)
        )

    poller = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_nse_announcement_poller"),
        None,
    )
    assert poller is not None, "_nse_announcement_poller() not found"
    assert contains_nse_fetch(poller), (
        "the poller no longer fetches NSE announcements — nothing does"
    )

    # and the cycle body must NOT fetch: doing so puts it back behind the LLM work
    assert not contains_nse_fetch(_loop_fn()), (
        "the cycle body fetches announcements again — section 1's LLM work "
        "will starve it, which is the defect this separation removes"
    )

    # the consumer drain must also sit outside the RSS guard, or an RSS error
    # still skips the announcements that were successfully fetched
    def contains_drain(node) -> bool:
        return any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_drain_nse_queue"
            for n in ast.walk(node)
        )

    assert contains_drain(_loop_fn()), "the cycle body never drains the queue"
    for node in ast.walk(_loop_fn()):
        if not isinstance(node, ast.Try):
            continue
        if "_rss_exc" not in {h.name for h in node.handlers if h.name}:
            continue
        for stmt in node.body:
            assert not contains_drain(stmt), (
                "the queue drain is inside the RSS guard — an RSS error would "
                "skip announcements the poller already captured"
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


# ── direction / suppression for NSE announcements ────────────────────────────

def test_announcement_direction_comes_from_the_exchange_category():
    """The keyword scan must be the fallback, not the primary signal.

    Replayed over 4,500 historical announcements the scan agreed with NSE's
    category on direction almost always (9 disagreements, 0.2%) — but because
    it defaults to BUY it also turned 3,504 of them (77.9%) into bullish
    candidates from categories that carry no direction at all. Those are NSE's
    routine filings, measured at -0.737% mean excess with a 36.3% win rate over
    1,169 observations. The value of this wiring is suppression, not direction.
    """
    fn = _loop_fn()
    calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "resolve_nse_direction"
    ]
    assert calls, (
        "the announcement loop no longer consults resolve_nse_direction — "
        "every routine filing is a BUY candidate again"
    )


def test_neutral_announcements_are_skipped_not_traded():
    """A NEUTRAL category must `continue`, not fall through to a side.

    Assigning BUY/SELL to a filing NSE files as routine is exactly the
    behaviour measured as loss-making.
    """
    fn = _loop_fn()
    guard = None
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.dump(node.test)
        if "NEUTRAL" in test_src and "_res" in test_src:
            guard = node
            break
    assert guard is not None, "no NEUTRAL guard found in the announcement loop"
    assert any(isinstance(s, ast.Continue) for s in ast.walk(guard)), (
        "a NEUTRAL category must skip the announcement entirely — falling "
        "through assigns it a tradable side"
    )


def test_the_keyword_scan_survives_only_as_a_fallback():
    """`side` must be derived from the exchange category, with the scan behind it.

    An earlier version of this test only looked for "_res is not None" near the
    keyword scan — which the NEUTRAL guard above it already satisfies. It
    therefore passed with the scan promoted back to the primary signal, the
    exact regression it was written to catch. It now asserts on the assignment.
    """
    fn = _loop_fn()

    # every `side = ...` assignment inside the announcement loop
    assigns = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "side" for t in n.targets)
    ]
    assert assigns, "no `side` assignment found in the loop"

    from_res = [a for a in assigns
                if any(isinstance(x, ast.Name) and x.id == "_res" for x in ast.walk(a.value))]
    from_kw = [a for a in assigns
               if any(isinstance(x, ast.Name) and x.id == "_ANNOUNCEMENT_BEARISH_KEYWORDS"
                      for x in ast.walk(a.value))]

    assert from_res, (
        "`side` is never derived from resolve_nse_direction — the exchange "
        "category is being ignored and the keyword scan is primary again"
    )
    assert from_kw, "the fallback heuristic is gone — unmapped categories lose their direction"

    # the keyword assignment must be reachable only when _res gave nothing,
    # i.e. it sits inside an `else` or a branch testing _res
    guarded = False
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and any(a in node.orelse for a in from_kw):
            if any(isinstance(x, ast.Name) and x.id == "_res" for x in ast.walk(node.test)):
                guarded = True
    assert guarded, (
        "the keyword scan is not in the else-branch of a test on _res — it can "
        "run even when the exchange supplied a direction"
    )
