# Loguru-based logger shared across the entire application.
# Structured JSON output in production; coloured console output in dev.

import logging as _logging
import os
import sys
from loguru import logger

# Silence noisy third-party stdlib loggers that flood the console under
# Celery prefork and otherwise bypass our loguru handler:
#   yfinance — "$SYMBOL: possibly delisted" on Yahoo transient errors
#   peewee   — yfinance's optional SQLite cache layer chatter
#   urllib3  — connection-pool reset warnings during yfinance retries
# Imported once here because utils.logger is imported by virtually every
# module in the project.
for _name in ("yfinance", "yfinance.utils", "peewee", "urllib3", "urllib3.connectionpool"):
    _logging.getLogger(_name).setLevel(_logging.CRITICAL)

# Remove the default handler so we control formatting ourselves
logger.remove()

# Console handler — human-readable with colours
logger.add(
    sys.stdout,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
        "<level>{message}</level>"
    ),
    level="DEBUG",
    colorize=True,
)

# ── Keep the test suite out of the production log ─────────────────────────────
# Added 2026-08-19. This module installs ONE process-wide loguru sink, and the
# test suite imports the same application modules — so pytest runs were writing
# into logs/autotrade_{date}.log, the exact file the four systemd services write
# to and that operations greps.
#
# That is not cosmetic. tests/test_kite_limiter.py exercises place_real_order()
# against mocks, which emits
#     CRITICAL | REAL ORDER PLACED — BUY 1×TESTCO @ ₹100.00
# into the production log. Anyone (or any alert rule) grepping for
# "REAL ORDER PLACED" sees a real-money order that never happened. It is a
# false alarm on the single most alarming string in the system.
#
# Same guard idiom as integrations/telegram_service.py, which hard-disables
# itself under PYTEST_CURRENT_TEST so a test cannot post to the real chat.
#
# PYTEST_VERSION is set by pytest >= 8 from startup and survives into
# subprocesses; PYTEST_CURRENT_TEST only exists once a test is running (too late
# for import time); the sys.modules check covers a runner that sets neither.
_UNDER_PYTEST = bool(
    os.getenv("PYTEST_VERSION")
    or os.getenv("PYTEST_CURRENT_TEST")
    or "pytest" in sys.modules
)

# Escape hatch for anything that needs the file sink gone entirely.
_FILE_LOG_DISABLED = os.getenv("DISABLE_FILE_LOG", "").lower() in {"1", "true", "yes"}

# File handler — rotates daily, JSON format for structured log ingestion.
# Under pytest this goes to a separate file (kept, so a failing test's logs are
# still inspectable) with short retention.
if not _FILE_LOG_DISABLED:
    logger.add(
        "logs/pytest_{time:YYYY-MM-DD}.log" if _UNDER_PYTEST
        else "logs/autotrade_{time:YYYY-MM-DD}.log",
        rotation="00:00",        # new file each midnight
        retention="2 days" if _UNDER_PYTEST else "30 days",
        serialize=True,          # JSON lines
        level="INFO",
    )

__all__ = ["logger"]
