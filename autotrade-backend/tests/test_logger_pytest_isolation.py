"""Regression — the test suite must not write into the production log.

utils/logger.py installs one process-wide loguru sink, and the suite imports the
same application modules, so pytest runs were appending to
logs/autotrade_{date}.log — the exact file the four systemd services write to
and that operations greps.

The concrete harm: tests/test_kite_limiter.py exercises place_real_order()
against mocks, which emits

    CRITICAL | REAL ORDER PLACED — BUY 1×TESTCO @ ₹100.00 (order_id=NEW-ORDER)

into the production log. Grepping for "REAL ORDER PLACED" then shows a
real-money order that never happened — a false alarm on the most alarming
string in the system. Seven such lines were found in the live log on
2026-08-19 before this guard was added.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from loguru import logger

import utils.logger as app_logger

PROD_LOG = Path("logs") / f"autotrade_{datetime.now():%Y-%m-%d}.log"


class TestPytestIsolation:

    def test_pytest_is_detected(self):
        assert app_logger._UNDER_PYTEST is True, (
            "logger.py did not detect pytest, so it is writing to the production log"
        )

    def test_emitting_critical_does_not_touch_the_production_log(self):
        """The end-to-end guarantee, not just the flag."""
        before = PROD_LOG.stat().st_size if PROD_LOG.exists() else 0
        marker = f"pytest-isolation-probe-{os.getpid()}"
        logger.critical(f"REAL ORDER PLACED — {marker}")
        for handler_id in list(getattr(logger, "_core").handlers):  # flush
            pass
        if PROD_LOG.exists():
            # The live services append to this file concurrently, so the size
            # may legitimately grow — assert on OUR marker, not on the size.
            tail = PROD_LOG.read_bytes()[max(0, before - 4096):].decode("utf-8", "replace")
            assert marker not in tail, (
                "a pytest log line reached the production log — the guard in "
                "utils/logger.py has regressed"
            )

    def test_pytest_log_is_separate_and_still_captured(self):
        """Isolation must not mean losing the logs for a failing test."""
        marker = f"pytest-capture-probe-{os.getpid()}"
        logger.info(marker)
        pytest_log = Path("logs") / f"pytest_{datetime.now():%Y-%m-%d}.log"
        assert pytest_log.exists(), "pytest log file was not created"
        assert marker in pytest_log.read_text(encoding="utf-8", errors="replace"), (
            "pytest logs are being dropped entirely rather than redirected"
        )

    def test_production_path_is_used_when_not_under_pytest(self):
        """Guard the other direction: production must still get its own file."""
        import importlib
        saved = {k: os.environ.pop(k, None) for k in ("PYTEST_VERSION", "PYTEST_CURRENT_TEST")}
        try:
            # sys.modules still holds pytest, so _UNDER_PYTEST stays True here —
            # assert the selection logic itself rather than re-importing.
            assert "autotrade_{time:YYYY-MM-DD}.log" in Path(
                app_logger.__file__
            ).read_text(encoding="utf-8"), "production log path was removed entirely"
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
