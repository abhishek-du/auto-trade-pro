# Loguru-based logger shared across the entire application.
# Structured JSON output in production; coloured console output in dev.

import sys
from loguru import logger

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
