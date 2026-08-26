"""BUG-2 — india_trade_loop must not share a lane with the Hub cycle.

Measured 2026-08-25: the loop ran 11 times inside 09:15-15:30 IST against ~375
expected at its 60s cadence, with a 329-minute gap spanned by continuous Master
Intelligence Hub scoring on both default-queue slots. Expired Celery tasks log
nothing, raise nothing and never reach a worker, so the starvation was silent.

This is the third task to need its own queue (fast_sl_check -> exit_queue,
tactical scans -> scan_queue). These tests pin the routing so a future edit
cannot quietly put it back on `default`.
"""
from __future__ import annotations

import pytest

from tasks.celery_app import celery_app

TASK = "tasks.india_trade_loop"
BEAT = "india-trade-loop-every-60s"


def _queue_names():
    return {q.name for q in celery_app.conf.task_queues}


def test_trade_queue_is_declared():
    assert "trade_queue" in _queue_names(), (
        "trade_queue is not declared — routing to an undeclared queue means the "
        "task is never consumed by anything"
    )


def test_loop_is_routed_off_the_default_queue():
    route = celery_app.conf.task_routes.get(TASK)
    assert route is not None, f"{TASK} has no route — it falls back to `default`"
    assert route["queue"] == "trade_queue"
    assert route["routing_key"] == "trade_queue"


def test_loop_does_not_share_a_queue_with_the_hub_cycle():
    """The Hub cycle is what starved it; they must not land in the same lane."""
    loop_q = celery_app.conf.task_routes.get(TASK, {}).get("queue")
    hub_q = celery_app.conf.task_routes.get(
        "tasks.run_master_intelligence_cycle", {}
    ).get("queue", celery_app.conf.task_default_queue)
    assert loop_q != hub_q, (
        f"india_trade_loop and the Hub cycle are both on '{loop_q}' — this is "
        f"exactly the contention that produced the 329-minute gap"
    )


def test_beat_entry_carries_the_queue_and_an_expiry():
    entry = celery_app.conf.beat_schedule[BEAT]
    opts = entry.get("options", {})
    assert opts.get("queue") == "trade_queue"
    exp = opts.get("expires")
    assert exp is not None, (
        "no expires — a dedicated queue without one moves the pile-up instead "
        "of removing it"
    )
    assert exp < entry["schedule"], (
        f"expires={exp} is not shorter than the {entry['schedule']}s cadence, so "
        f"a backlog can still accumulate"
    )


def test_the_other_dedicated_lanes_are_untouched():
    """This change must not disturb the two queues that already work."""
    r = celery_app.conf.task_routes
    assert r["tasks.fast_sl_check"]["queue"] == "exit_queue"
    assert r["tasks.tactical_tasks.run_tactical_intraday"]["queue"] == "scan_queue"
    assert celery_app.conf.task_default_queue == "default"


def test_bug1_is_still_present_so_this_change_cannot_enable_origination():
    """Phase 5 fixes scheduling ONLY. If BUG-1 is ever repaired, that is a
    separate, deliberate decision — and this test must be updated with it."""
    from tasks.india_tasks import _india_trade_loop
    code = _india_trade_loop.__code__
    inner = [c for c in code.co_consts if hasattr(c, "co_varnames")]
    names = set(code.co_varnames) | {n for c in inner for n in c.co_varnames}
    assert "settings" in names, (
        "`settings` is no longer a function-local, so BUG-1 may have been fixed. "
        "Phase 5 deliberately left the Hub origination path unreachable; if that "
        "changed, confirm it was intended and that NEWS_ONLY_BLOCKS_HUB_ENTRIES "
        "gates it."
    )
