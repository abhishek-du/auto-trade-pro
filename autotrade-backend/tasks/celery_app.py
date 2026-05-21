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
        "broker_use_ssl":        {"ssl_cert_reqs": ssl.CERT_NONE},
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
    # Do not fire missed tasks on startup — prevents queue flood on restart
    beat_max_loop_interval=5,
    worker_prefetch_multiplier=1,
    # Ensure tasks are not lost if the worker disconnects from Upstash
    task_acks_late=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    **_ssl_kwargs,
)

# ── Beat schedule ─────────────────────────────────────────────────────────────
celery_app.conf.beat_schedule = {

    # ── US / global market tasks ──────────────────────────────────────────────

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

    # Every 30 s during NSE hours: OHLCV candles + index snapshots + VIX
    "india-price-scan-every-30s": {
        "task":     "tasks.india_price_scan",
        "schedule": 30,
        "options":  {"countdown": 5},
    },

    # Daily 13:00 UTC = 6:30 PM IST: FII/DII flow from NSE
    "india-fii-dii-daily": {
        "task":     "tasks.india_fii_dii_fetch",
        "schedule": crontab(hour=13, minute=0),
    },

    # Every 15 min during NSE hours: NIFTY + BANKNIFTY options chain
    "india-options-every-15min": {
        "task":     "tasks.india_options_analysis",
        "schedule": 900,
        "options":  {"countdown": 10},
    },

    # Daily 14:30 UTC = 8:00 PM IST: AMFI NAV bulk fetch (publishes after 7 PM IST)
    "india-mf-nav-daily": {
        "task":     "tasks.india_mutual_fund_nav",
        "schedule": crontab(hour=14, minute=30),
    },

    # Weekly Sunday 18:30 UTC: fundamental data refresh (PE, ROE, promoter holding…)
    "india-fundamentals-weekly": {
        "task":     "tasks.india_fundamental_update",
        "schedule": crontab(day_of_week=0, hour=18, minute=30),
    },

    # Every 60 s during NSE hours + 30 min: full India paper-trading cycle
    "india-trade-loop-every-60s": {
        "task":     "tasks.india_trade_loop",
        "schedule": 60,
        "options":  {"countdown": 15},
    },

    # Weekly Saturday 20:30 UTC = Sunday 02:00 IST: LSTM + RF model training
    "ml-model-training-weekly": {
        "task":     "tasks.india_tasks.train_ml_models_task",
        "schedule": crontab(hour=20, minute=30, day_of_week="saturday"),
    },

    # Every 15 min: Zerodha Kite portfolio holdings sync (NSE hours only)
    "kite-portfolio-sync-15min": {
        "task":     "tasks.india_tasks.sync_kite_holdings",
        "schedule": 900,
        "options":  {"countdown": 20},
    },
}
