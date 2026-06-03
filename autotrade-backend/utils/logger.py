# Loguru-based logger shared across the entire application.
# Structured JSON output in production; coloured console output in dev.

import logging as _logging
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

# File handler — rotates daily, JSON format for structured log ingestion
logger.add(
    "logs/autotrade_{time:YYYY-MM-DD}.log",
    rotation="00:00",        # new file each midnight
    retention="30 days",
    serialize=True,          # JSON lines
    level="INFO",
)

__all__ = ["logger"]
