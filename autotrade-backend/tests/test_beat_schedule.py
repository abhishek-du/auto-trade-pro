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
        # Import whatever celery_app declares in `include`, rather than a
        # hardcoded list — otherwise adding a task module (e.g. Path F's
        # tactical tasks) makes this test fail for the wrong reason.
        import importlib

        for module in celery_app.conf.include or ():
            importlib.import_module(module)

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
