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

    def test_frequent_crontab_entries_do_not_inherit_the_1h_expiry(self):
        """The auto-expires loop gives EVERY crontab entry 3600s, which is right
        for a daily job and wrong for a sub-hourly one — a backlog then replays
        stale cycles long after they mean anything. Observed live 2026-08-20:
        ~4 stale 1-minute tactical scans ran inside one minute.

        Any crontab entry that fires more often than hourly must declare its own
        `expires` shorter than its cadence.
        """
        from celery.schedules import crontab

        # Exempt: verified to self-gate and be idempotent, so a late run is
        # harmless. kite_live_candles returns {"skipped": "outside_market_hours"}
        # and its candle writes are upserts, unlike a tactical scan whose output
        # is timestamped signals that would be attributed to the wrong minute.
        EXEMPT = {"kite-live-1m-candles"}

        offenders = []
        for name, cfg in celery_app.conf.beat_schedule.items():
            sch = cfg["schedule"]
            if not isinstance(sch, crontab):
                continue
            minute = str(getattr(sch, "_orig_minute", "*"))
            # "*" or any "*/n" step means it fires multiple times per hour.
            fires_sub_hourly = minute == "*" or "/" in minute or "," in minute
            if not fires_sub_hourly:
                continue
            expires = cfg.get("options", {}).get("expires")
            if name in EXEMPT:
                continue
            if expires is None or expires >= 3600:
                offenders.append(f"{name} (minute={minute!r}, expires={expires})")

        assert not offenders, (
            "sub-hourly crontab entries inheriting the 1h default expiry:\n  "
            + "\n  ".join(offenders)
        )

    def test_every_entry_has_an_expires(self):
        """celery_app applies expires programmatically; a queue backlog once
        reached 63k tasks without it."""
        no_expiry = [
            name for name, cfg in celery_app.conf.beat_schedule.items()
            if "expires" not in cfg.get("options", {})
        ]
        assert not no_expiry, f"beat entries without expires: {no_expiry}"
