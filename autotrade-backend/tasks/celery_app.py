# Celery application — broker: Upstash Redis (TLS), backend: same.
# Upstash requires rediss:// (TLS). The ssl_cert_reqs=CERT_NONE config
# is needed because Upstash uses SNI-based TLS without client certs.

import os
import ssl
import sys

# Ensure the project root is on sys.path for all fork-pool workers
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from celery import Celery
from celery.schedules import crontab
from utils.config import settings

# Build Celery broker/backend kwargs for Upstash TLS compatibility
_ssl_kwargs: dict = {}
if settings.redis_uses_tls:
    _ssl_kwargs = {
        "broker_use_ssl":  {"ssl_cert_reqs": ssl.CERT_NONE},
        "redis_backend_use_ssl": {"ssl_cert_reqs": ssl.CERT_NONE},
    }

celery_app = Celery(
    "autotrade_pro",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "tasks.market_scan",
        "tasks.news_scan",
        "tasks.paper_trade_loop",
        "tasks.india_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Upstash has a 1 MB command-size limit — keep task payloads small
    task_soft_time_limit=300,
    task_time_limit=600,
    **_ssl_kwargs,
)

# ── Beat schedule ─────────────────────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    "scan-prices-every-30s": {
        "task":     "tasks.market_scan.scan_watchlist",
        "schedule": 30,
    },
    "crawl-news-every-5min": {
        "task":     "tasks.news_scan.scan_news",
        "schedule": 300,
    },
    "paper-trade-loop-every-minute": {
        "task":     "tasks.paper_trade_loop.run_paper_trade_loop",
        "schedule": 60,
    },
    # ── Indian market tasks ───────────────────────────────────────────────────
    "india-price-crawl-every-5min": {
        "task":     "tasks.india_tasks.crawl_india_prices",
        "schedule": 300,
    },
    "india-fii-dii-every-15min": {
        "task":     "tasks.india_tasks.crawl_fii_dii",
        "schedule": 900,
    },
    "india-options-every-10min": {
        "task":     "tasks.india_tasks.crawl_options_chain",
        "schedule": 600,
    },
    "india-signals-every-5min": {
        "task":     "tasks.india_tasks.run_india_signal_scan",
        "schedule": 300,
    },
    # Saturday 18:30 UTC = Sunday 00:00 IST
    "fundamentals-update-weekly": {
        "task":     "tasks.india_tasks.run_fundamental_update_task",
        "schedule": crontab(hour=18, minute=30, day_of_week="saturday"),
    },
}
