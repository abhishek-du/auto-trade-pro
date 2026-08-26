"""Phase 1B task B — the NSE announcement poller must be independent of the
RSS/LLM workload.

Regression cover for the 2026-08-25 finding: section 2 (NSE fetch) sat after
section 1 (RSS) in the same loop body, and section 1 awaits a full LLM ReAct
loop per new article. Measured that day: the fetch ran at 09:14:50 IST and not
again until 16:05:29 — a 411-minute gap spanned by 619 agent decisions — so
every filing made during the session scrolled out of NSE's 20-item window
unseen. Zero in-session announcements were ingested on any trading day from
2026-08-17 onward.

These tests use a fake clock (asyncio.sleep patched to yield) so they are
deterministic and never touch the network.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import news_discovery_engine as nde


def _ann(seq, sym="ACME"):
    return {
        "seq_id": str(seq), "symbol": f"{sym}.NS", "company": sym,
        "category": "Financial Results", "summary": "s",
        "headline": f"{sym}: Financial Results", "pdf_url": "",
        "published_at": None, "source": "NSE-Announcements",
    }


@pytest.fixture(autouse=True)
def _clean_state():
    """Each test gets a fresh queue, dedup set and counters."""
    nde._NSE_QUEUE = asyncio.Queue(maxsize=nde._NSE_QUEUE_MAX)
    nde._processed_seq_ids.clear()
    for k in nde._NSE_POLL_STATS:
        if isinstance(nde._NSE_POLL_STATS[k], int):
            nde._NSE_POLL_STATS[k] = 0
    yield
    nde._processed_seq_ids.clear()


_REAL_SLEEP = asyncio.sleep          # captured before any patching


async def _run_poller(n_polls: int, fetch):
    """Run the poller until it has completed n_polls, then cancel it.

    The poller's inter-poll sleep is replaced with a bare yield so the test is
    deterministic and takes no wall-clock time. _REAL_SLEEP is captured at
    import so the replacement cannot recurse into itself.
    """
    done = asyncio.Event()

    async def _sleep(_delay):
        if nde._NSE_POLL_STATS["polls_total"] >= n_polls:
            done.set()
            await _REAL_SLEEP(3600)       # park; the cancel below ends it
        await _REAL_SLEEP(0)

    with patch.object(nde, "fetch_nse_corporate_announcements", fetch), \
         patch("news_discovery_engine.asyncio.sleep", _sleep):
        t = asyncio.create_task(nde._nse_announcement_poller())
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    return t


# ── 1 & 2. a long LLM job must not stop polling ──────────────────────────────

@pytest.mark.asyncio
async def test_polling_continues_while_a_long_llm_job_runs():
    """The poller must complete several polls while an LLM job is still awaiting."""
    llm_started = asyncio.Event()
    llm_release = asyncio.Event()

    async def slow_llm():
        llm_started.set()
        await llm_release.wait()          # never completes during the test
        return "done"

    seq = iter(range(1000))

    async def fetch():
        return [_ann(next(seq))]

    llm = asyncio.create_task(slow_llm())
    await llm_started.wait()

    await _run_poller(5, fetch)

    assert not llm.done(), "the LLM job should still be blocked — the test is invalid otherwise"
    assert nde._NSE_POLL_STATS["polls_total"] >= 5, (
        "the poller stopped while an LLM job was in flight — this is the "
        "starvation the decoupling exists to prevent"
    )
    assert nde._NSE_POLL_STATS["nse_items_enqueued"] >= 5
    llm_release.set()
    await llm


# ── 3. no duplicate persisted announcements ──────────────────────────────────

@pytest.mark.asyncio
async def test_two_polls_of_the_same_feed_enqueue_each_item_once():
    async def fetch():
        return [_ann(1), _ann(2), _ann(3)]

    await _run_poller(4, fetch)

    assert nde._NSE_POLL_STATS["nse_items_enqueued"] == 3, (
        f"expected 3 unique items, got "
        f"{nde._NSE_POLL_STATS['nse_items_enqueued']} — dedup is not holding"
    )
    assert nde._NSE_QUEUE.qsize() == 3
    assert nde._NSE_POLL_STATS["nse_items_duplicate"] >= 6   # 3 repeats x >=2 later polls


# ── 4. a failed LLM job must not stop polling ────────────────────────────────

@pytest.mark.asyncio
async def test_failing_llm_job_does_not_stop_polling():
    async def boom():
        raise RuntimeError("bedrock exploded")

    task = asyncio.create_task(boom())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError):
        await task

    seq = iter(range(1000))

    async def fetch():
        return [_ann(next(seq))]

    await _run_poller(3, fetch)
    assert nde._NSE_POLL_STATS["polls_total"] >= 3


# ── 5. a failed poll must not stop the poller or the consumer ────────────────

@pytest.mark.asyncio
async def test_failed_poll_is_recorded_and_polling_continues():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise RuntimeError("NSE 403")
        return [_ann(calls["n"])]

    await _run_poller(6, flaky)

    assert nde._NSE_POLL_STATS["nse_errors"] >= 2, "poll failures were not counted"
    assert nde._NSE_POLL_STATS["polls_total"] >= 6, "a failed poll ended the poller"
    assert nde._NSE_POLL_STATS["nse_items_enqueued"] >= 2, "recovery polls produced nothing"


@pytest.mark.asyncio
async def test_consumer_drain_is_unaffected_by_poll_failure():
    async def always_fails():
        raise RuntimeError("down")

    nde._NSE_QUEUE.put_nowait(_ann(99))
    await _run_poller(2, always_fails)
    drained = nde._drain_nse_queue()
    assert len(drained) == 1 and drained[0]["seq_id"] == "99"


# ── 5b. the queue is bounded ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_is_bounded_and_overflow_is_counted_not_swallowed():
    nde._NSE_QUEUE = asyncio.Queue(maxsize=3)
    seq = iter(range(1000))

    async def fetch():
        return [_ann(next(seq)) for _ in range(5)]

    await _run_poller(1, fetch)

    assert nde._NSE_QUEUE.qsize() == 3, "the queue grew past its bound"
    assert nde._NSE_POLL_STATS["nse_items_dropped"] == 2
    # a dropped item must NOT be marked processed, or it can never be retried
    assert len(nde._processed_seq_ids) == 3


# ── 6 & 7. clean shutdown and cancellation ───────────────────────────────────

@pytest.mark.asyncio
async def test_cancellation_is_clean():
    async def fetch():
        return []

    with patch.object(nde, "fetch_nse_corporate_announcements", fetch):
        t = asyncio.create_task(nde._nse_announcement_poller())
        await _REAL_SLEEP(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    assert t.cancelled() or t.done()


@pytest.mark.asyncio
async def test_loop_shutdown_cancels_the_poller():
    """run_news_discovery_loop must not leave the poller pending."""
    created = {}

    async def fake_cycles():
        raise KeyboardInterrupt("stop")

    real_create = asyncio.create_task

    def capture(coro, **kw):
        t = real_create(coro, **kw)
        if kw.get("name") == "nse_announcement_poller":
            created["task"] = t
        return t

    with patch.object(nde, "_news_discovery_cycles", fake_cycles), \
         patch.object(nde, "fetch_nse_corporate_announcements", AsyncMock(return_value=[])), \
         patch.object(nde.asyncio, "create_task", capture):
        with pytest.raises(KeyboardInterrupt):
            await nde.run_news_discovery_loop()

    assert "task" in created, "the poller task was never started"
    assert created["task"].done(), "the poller was left pending after shutdown"


# ── structural: the fetch must not return to the main loop body ──────────────

def test_main_loop_body_does_not_fetch_announcements():
    """AST guard — if the fetch call reappears inside the cycle body, the
    starvation is back regardless of what the poller does."""
    src = Path("news_discovery_engine.py").read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_news_discovery_cycles"
    )
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "fetch_nse_corporate_announcements":
            pytest.fail(
                "fetch_nse_corporate_announcements() is being called from the "
                "main cycle body again — section 1's LLM work will starve it"
            )


def test_poller_does_no_llm_or_pdf_work():
    """The poller must stay cheap: anything slow in it recreates the problem."""
    src = Path("news_discovery_engine.py").read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_nse_announcement_poller"
    )
    banned = {"process_ticker", "process_nse_announcement", "call_llm_chat", "call_mantle_chat"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in banned:
                pytest.fail(f"the poller calls {name}() — that reintroduces blocking")


def test_the_module_level_queue_bound_is_a_real_bound():
    """asyncio.Queue treats maxsize <= 0 as INFINITE.

    test_queue_is_bounded_... above substitutes its own small queue, so it
    proves the overflow *path* works but says nothing about the shipped
    constant. Without this, setting _NSE_QUEUE_MAX = 0 passes every other test
    in this file while making the queue unbounded in production.
    """
    assert isinstance(nde._NSE_QUEUE_MAX, int)
    assert nde._NSE_QUEUE_MAX > 0, (
        f"_NSE_QUEUE_MAX={nde._NSE_QUEUE_MAX} — asyncio.Queue reads any value "
        f"<= 0 as unbounded, which is exactly what the brief forbids"
    )


@pytest.mark.asyncio
async def test_startup_creates_a_bounded_queue():
    """The queue the loop actually installs must carry the bound."""
    async def fake_cycles():
        raise KeyboardInterrupt("stop")

    with patch.object(nde, "_news_discovery_cycles", fake_cycles), \
         patch.object(nde, "fetch_nse_corporate_announcements", AsyncMock(return_value=[])):
        with pytest.raises(KeyboardInterrupt):
            await nde.run_news_discovery_loop()

    assert nde._NSE_QUEUE is not None
    assert nde._NSE_QUEUE.maxsize == nde._NSE_QUEUE_MAX
    assert nde._NSE_QUEUE.maxsize > 0, "the installed queue is unbounded"
