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

    # Daily 02:35 UTC = 08:05 IST: download fresh NSE instrument master before open
    "zerodha-instrument-refresh-daily": {
        "task":     "tasks.india_tasks.refresh_zerodha_instruments",
        "schedule": crontab(hour=2, minute=35),
    },

    # Daily 00:35 UTC = 06:05 IST: check if Kite token expired at 6 AM
    "zerodha-token-expiry-check": {
        "task":     "tasks.india_tasks.check_zerodha_token",
        "schedule": crontab(hour=0, minute=35),
    },

    # Live price cache — every 15 s (supplements FastAPI background task)
    "refresh-live-prices-15s": {
        "task":     "tasks.refresh_live_prices",
        "schedule": 15,
        "options":  {"countdown": 3},
    },

    # Daily 02:30 UTC = 08:00 IST: refresh PE/market-cap/beta fundamentals
    "refresh-stock-info-daily": {
        "task":     "tasks.refresh_stock_info_cache",
        "schedule": crontab(hour=2, minute=30),
    },

    # Every 60 s: sector performance from PRICE_CACHE
    "refresh-sector-data-60s": {
        "task":    "tasks.refresh_sector_data",
        "schedule": 60,
        "options": {"countdown": 12},
    },

    # Every 2 minutes: market breadth advances/declines + gainers/losers
    "refresh-market-breadth-2min": {
        "task":    "tasks.refresh_market_breadth",
        "schedule": 120,
        "options": {"countdown": 8},
    },

    # Daily 1:30 AM UTC = 7:00 AM IST: seed market calendar (expiries, RBI, IPOs, earnings)
    "seed-calendar-daily": {
        "task":     "tasks.seed_calendar_events",
        "schedule": crontab(hour=1, minute=30),
    },

    # Every 30 min: refresh IPO data from ipoalerts.in
    "refresh-ipo-data-30min": {
        "task":     "tasks.india_tasks.refresh_ipo_data",
        "schedule": 1800,
        "options":  {"countdown": 20},
    },

    # ── Kite library tasks (post market-close holdings, daily candles, etc.) ──
    "kite-sync-holdings-daily": {
        "task":     "tasks.kite_sync_holdings",
        "schedule": crontab(hour=15, minute=35),
    },
    "kite-sync-candles-daily": {
        "task":     "tasks.kite_sync_candles",
        "schedule": crontab(hour=10, minute=0),
    },
    "kite-refresh-instruments-daily": {
        "task":     "tasks.kite_refresh_instruments",
        "schedule": crontab(hour=2, minute=30),
    },
    "kite-check-token-daily": {
        "task":     "tasks.kite_check_token",
        "schedule": crontab(hour=0, minute=35),
    },
    "kite-start-ticker-on-open": {
        "task":     "tasks.kite_start_ticker",
        "schedule": crontab(hour=3, minute=45),
    },
}
