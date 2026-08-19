"""D9 regression — every scheduled beat task must exist.

Three entries (india_options_analysis, india_equity_options_enrich,
fno_expiry_sweep) named tasks deleted with the F&O subsystem in 91457d7 but
were left in beat_schedule, so beat kept enqueueing them and the worker raised
NotRegistered on every tick.

The registry test below catches the NEXT orphan automatically rather than
pinning these three names forever.
"""
from __future__ import annotations

import pytest

from tasks.celery_app import celery_app

REMOVED = {
    "india-options-every-15min",
    "india-equity-options-enrich",
    "fno-expiry-sweep-daily",
}


class TestBeatSchedule:

    def test_removed_fno_entries_are_gone(self):
        present = REMOVED & set(celery_app.conf.beat_schedule)
        assert not present, f"orphaned F&O beat entries are back: {sorted(present)}"

    def test_every_scheduled_task_is_registered(self):
        """The general form of D9 — no beat entry may name a task that
        does not exist, whatever it is called."""
        import tasks.india_tasks  # noqa: F401  — registers the bulk of the tasks
        import tasks.market_scan, tasks.market_scanner, tasks.news_scan  # noqa: F401
        import tasks.narrative_scan, tasks.price_cache, tasks.pre_diagnose  # noqa: F401

        registered = set(celery_app.tasks)
        missing = {
            name: cfg["task"]
            for name, cfg in celery_app.conf.beat_schedule.items()
            if cfg["task"] not in registered
        }
        assert not missing, (
            "beat_schedule references unregistered tasks (they will raise "
            f"NotRegistered on every tick): {missing}"
        )

    def test_schedule_is_not_empty(self):
        """Guard against a bad edit deleting more than intended."""
        assert len(celery_app.conf.beat_schedule) > 40

    def test_every_entry_has_an_expires(self):
        """celery_app applies expires programmatically; a queue backlog once
        reached 63k tasks without it."""
        no_expiry = [
            name for name, cfg in celery_app.conf.beat_schedule.items()
            if "expires" not in cfg.get("options", {})
        ]
        assert not no_expiry, f"beat entries without expires: {no_expiry}"
