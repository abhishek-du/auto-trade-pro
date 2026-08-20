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
        "tasks.price_cache",
        "tasks.narrative_scan",
        # B16: tasks.paper_trade_loop removed — deprecated duplicate trade loop
        # that caused oversized/duplicate trades; not scheduled, dead weight.
        "tasks.india_tasks",
        "tasks.market_scanner",
        "tasks.pre_diagnose",
        "tasks.tactical_tasks",
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

    # Live-price snapshot for every process (2026-08-17). Moved here out of the
    # API's lifespan loop, where its 4-23s fetch was stalling the event loop.
    # 30s matches the old in-process cadence closely enough; the Kite ticker
    # still supplies sub-second prices to the API during market hours.
    "refresh-price-cache-every-30s": {
        "task":     "tasks.price_cache.refresh_price_cache",
        "schedule": 30,
    },

    "scan-prices-every-30s": {
        "task":     "tasks.market_scan.scan_watchlist",
        "schedule": 30,
    },
    # 5 min, not 60s (2026-08-17). A crawl fetches 11 sources and then makes up
    # to ~45 classify_event() LLM round-trips, so it cannot finish inside 60s --
    # scheduling it that often just queued ticks the overlap guard now rejects.
    # The comment on purge-old-news-weekly below already describes this as "the
    # 5-minute crawl", so 60s was itself a drift from the documented intent.
    "crawl-news-every-5min": {
        "task":     "tasks.news_scan.scan_news",
        "schedule": 300,
    },
    # Sunday 02:30 IST (21:00 UTC Saturday). Keeps news_items bounded; the
    # 5-minute crawl saves ~150 rows/cycle → ~43k/day → ~2.6M/2 months without
    # a purge. 60-day default keeps history useful for backtests.
    "purge-old-news-weekly": {
        "task":     "tasks.purge_old_news",
        "schedule": crontab(day_of_week="sunday", hour=21, minute=0),
        "kwargs":   {"days": 60},
    },
    # ── Indian market tasks ───────────────────────────────────────────────────

    # Every 5 min during NSE hours: OHLCV candles + index snapshots + VIX
    "india-price-scan-every-5min": {
        "task":     "tasks.india_price_scan",
        "schedule": 300,
        "options":  {"countdown": 5},
    },

    # Every 5 min during NSE hours: refresh narrative intelligence cache
    # (RSS + Telegram → LLM decoder → sector boost scores for the Hub)
    "narrative-intelligence-every-5min": {
        "task":     "tasks.refresh_narrative_intelligence",
        "schedule": 300,
        "options":  {"countdown": 20},
    },

    # Daily 13:00 UTC = 6:30 PM IST: FII/DII flow from NSE
    "india-fii-dii-daily": {
        "task":     "tasks.india_fii_dii_fetch",
        "schedule": crontab(hour=13, minute=0),
    },

    # Saturday 05:30 UTC = 11:00 AM IST: Weekend LLM Self-Reflection Loop
    "weekend-reflection-loop": {
        "task":     "tasks.india_weekend_reflection",
        "schedule": crontab(day_of_week="saturday", hour=5, minute=30),
    },

    # ── Path F · Tactical pipeline (SHADOW MODE — opens no positions) ────────
    # hour="3-10" UTC == 09:15-15:30 IST. Celery runs on timezone=UTC, so an
    # IST-looking hour range here would fire in the evening and never during
    # the session.
    # F1 every minute on 1m candles; F4 every 5 min on 5m candles.
    # Explicit `expires` — the auto-loop at the bottom of this file gives every
    # CRONTAB entry 3600s, which is right for a daily job and badly wrong for a
    # 1-minute one: observed live 2026-08-20, a brief backlog replayed ~4 stale
    # F1 cycles inside one minute. A tactical scan is only meaningful for the
    # bar it was scheduled for, so drop it rather than run it late.
    "tactical-intraday-1min": {
        "task":     "tasks.tactical_tasks.run_tactical_intraday",
        "schedule": crontab(minute="*", hour="3-10", day_of_week="1-5"),
        "options":  {"expires": 55},      # < the 60s cadence
    },
    "tactical-meanrev-5min": {
        "task":     "tasks.tactical_tasks.run_tactical_mean_reversion",
        "schedule": crontab(minute="*/5", hour="3-10", day_of_week="1-5"),
        "options":  {"expires": 280},     # < the 300s cadence
    },

    # End-of-day Path F summary. 10:05 UTC = 15:35 IST, five minutes after the
    # NSE close so the 5s fast_sl_check loop has settled the day's exits before
    # wins are counted. Daily job, so the auto-loop's 3600s expires is correct
    # here and is deliberately not overridden.
    "tactical-daily-summary": {
        "task":     "tasks.tactical_tasks.tactical_daily_summary",
        "schedule": crontab(minute="5", hour="10", day_of_week="1-5"),
    },

    # NOTE (D9, audit 2026-08-19): three F&O beat entries were removed here —
    # india-options-every-15min, india-equity-options-enrich and
    # fno-expiry-sweep-daily. Their tasks were deleted with the F&O subsystem in
    # 91457d7 but the schedule was not updated, so beat kept enqueueing them and
    # the worker raised NotRegistered on every tick. tests/test_beat_schedule.py
    # now asserts every scheduled task name resolves in the Celery registry.

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

    # Weekly Sunday 19:00 UTC: rebuild yfinance sector mapping for all NSE EQ symbols
    "sector-cache-rebuild-weekly": {
        "task":     "tasks.rebuild_sector_cache",
        "schedule": crontab(day_of_week=0, hour=19, minute=0),
    },

    # Weekly Sunday 01:00 UTC (06:30 IST, before market open): refresh last week
    # of daily candles for the FULL NSE universe via Zerodha Kite. Keeps every
    # symbol's bars current so the scanner/agent cover the whole market.
    "full-nse-candles-weekly": {
        "task":     "tasks.refresh_full_nse_candles",
        "schedule": crontab(day_of_week="sunday", hour=1, minute=0),
    },

    # Daily 03:00 UTC (08:30 IST): refresh last week of daily candles for the
    # FULL BSE universe via Zerodha Kite — the BSE twin of the NSE job above,
    # but daily rather than weekly (2026-07-31; BSE had no full-universe
    # candle sync at all before this — see JUMBO.BO gap).
    # Originally scheduled 01:00 UTC — moved here 2026-08-03 after it failed
    # ("Zerodha not authenticated") every single day (1/2/3 Aug): 01:00 UTC is
    # BEFORE autotrade-zerodha-refresh.timer's 02:30 UTC daily token refresh,
    # so it was always running on the previous day's already-expired token.
    # 03:00 UTC matches sync-nse-eq-instruments-daily below, which has been
    # authenticating fine at this hour the whole time — same 30-min safety
    # margin after the token refresh.
    "full-bse-candles-daily": {
        "task":     "tasks.refresh_full_bse_candles",
        "schedule": crontab(hour=3, minute=0),
    },

    # Daily 03:00 UTC (08:30 IST): sync ALL NSE+BSE EQ instruments from Zerodha's
    # full instrument master. This populates ~9,600 NSE EQ stocks into kite_instruments
    # so EVERY stock gets automatic candle ingestion — not just the 30 hardcoded ones.
    # Root fix: small-caps (JTEKTINDIA, SAKSOFT, SIGNPOST etc.) are now auto-tracked.
    "sync-nse-eq-instruments-daily": {
        "task":     "tasks.sync_nse_eq_instruments",
        "schedule": crontab(hour=3, minute=0),
    },

    # Daily 03:30 UTC (09:00 IST): rebuild Hub universe by 30-day avg turnover.
    # Runs AFTER instrument sync (03:00) + candle backfill (03:10) so the universe
    # is rebuilt with fresh candles for ALL 9,600 NSE symbols.
    "rebuild-hub-universe-daily": {
        "task":     "tasks.rebuild_hub_universe",
        "schedule": crontab(hour=3, minute=30),
    },

    # Daily 03:40 UTC — after the universe rebuild above, so newly-added
    # symbols get an ISIN resolved the same night. Moves Upstox identity
    # resolution (yfinance / assets.upstox.com CSV) off the live
    # company_intelligence tool-call path and into a background job instead.
    "refresh-isin-map-daily": {
        "task":     "tasks.refresh_isin_map",
        "schedule": crontab(hour=3, minute=40),
    },

    # Daily 03:10 UTC (08:40 IST): backfill yesterday's 1d close for all Hub symbols.
    # Runs AFTER universe rebuild so the symbol list is fresh. Ensures Hub scoring
    # always has the latest daily candle even if intraday crawl missed symbols.
    "backfill-hub-1d-candles-daily": {
        "task":     "tasks.backfill_hub_1d_candles",
        "schedule": crontab(hour=3, minute=10),
    },

    # Evening 12:00 UTC (17:30 IST) Mon-Fri: refresh TODAY's 1d close for the
    # tradeable set (open positions + hub + shortlist). The 03:10 UTC run above
    # fires BEFORE Kite finalises the prior day's daily candle, so the daily view
    # for held/scored stocks ran ~2 days behind — which is what let entries fill
    # at a stale daily close (TBZ bought 8-Jul at the 6-Jul ₹198.71). This pass
    # runs after close + Kite finalisation on a small set, so it lands same-day.
    "refresh-priority-1d-candles-evening": {
        "task":     "tasks.refresh_priority_1d_candles",
        "schedule": crontab(hour=12, minute=0, day_of_week="1-5"),
    },

    # Every 15 min during NSE hours: score full NSE universe → market_shortlist
    # (runs 45 s before the hub cycle so the shortlist is always fresh)
    "market-scanner-every-15min": {
        "task":     "tasks.market_scanner.run_market_scanner",
        "schedule": 900,
        "options":  {"countdown": 30},
    },

    # Every 15 min during NSE hours: scan scheduled corporate events (1-15 days out)
    # for pre-event expectation gap setups. Parallel & independent of News Strategy.
    "pre-event-gap-scan-every-15min": {
        "task":     "tasks.india_pre_event_gap_scan",
        "schedule": 900,
        "options":  {"countdown": 45},
    },

    # Every 5 s during NSE hours: stop-loss / take-profit check on live PRICE_CACHE.
    # Pure exit-only path — reads WebSocket LTP, no scoring, no new entries.
    "fast-sl-check-every-5s": {
        "task":     "tasks.fast_sl_check",
        "schedule": 5,
    },

    # Every 30 s during NSE hours: fast market-shock guard. Tightens/flattens
    # open longs on a sudden index drop or high-severity news burst — reacts far
    # faster than the 15-min hub cycle. Gated OFF by default (ENABLE_SHOCK_GUARD).
    "market-shock-guard-every-30s": {
        "task":     "tasks.market_shock_guard",
        "schedule": 30,
        "options":  {"countdown": 8},
    },

    # Every 1 min (incl. after-hours): alert on high-impact market-shock news so
    # operators are aware of macro swings before the 15-min hub cycle logs them.
    "market-news-alert-every-1min": {
        "task":     "tasks.market_news_alert",
        "schedule": 60.0,
        "options":  {"expires": 45.0},
    },

    # Every 60 s during NSE hours + 30 min: full India paper-trading cycle
    "india-trade-loop-every-60s": {
        "task":     "tasks.india_trade_loop",
        "schedule": 60,
        "options":  {"countdown": 15},
    },

    # Every 30 min during NSE hours: intraday candles, via Upstox, for every
    # NSE+BSE EQ symbol NOT already covered by the Kite-based Hub universe
    # crawl (2026-07-31). Task self-gates on _is_india_trading_window(), same
    # pattern as india_trade_loop above. Separate broker (Upstox) from every
    # other price-crawl task here (Kite) by design — can't compete with or
    # slow down the Hub universe's live-trading price cadence.
    "long-tail-intraday-every-30min": {
        "task":     "tasks.sync_long_tail_intraday",
        "schedule": 1800,
        "options":  {"countdown": 45},
    },

    # Every 5 min: reconcile the spreadsheet trade journal (catches trades that
    # close after the 60 s trade loop stops running post-market).
    "trade-journal-sync-5min": {
        "task":     "tasks.india_tasks.sync_trade_journal",
        "schedule": 300,
        "options":  {"countdown": 30},
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

    # Daily 03:35 UTC (09:05 IST): detect stock splits/bonus issues for open positions.
    # Runs just after first 1m candle lands; adjusts units + entry/stop/target + fires news alert.
    "corporate-action-check-daily": {
        # D9-b (found 2026-08-19 by tests/test_beat_schedule.py): this named
        # "tasks.india_tasks.corporate_action_check", but the task registers as
        # "tasks.corporate_action_check" (india_tasks.py:1711). The name never
        # resolved, so beat raised NotRegistered daily and split/bonus detection
        # -- which adjusts position units and entry/stop/target -- never ran.
        "task":     "tasks.corporate_action_check",
        "schedule": crontab(hour=3, minute=35),
        "options":  {"expires": 300},
    },

    # Daily 03:05 UTC (08:35 IST): refresh NFO contracts (NIFTY/BANKNIFTY/FINNIFTY only).
    # Applies smart filters: nearest 2-3 expiries + 15% OTM strike window.
    # NSE/BSE equity instruments are NOT synced — Hub uses candles for its universe.
    "zerodha-nfo-instrument-refresh-daily": {
        "task":     "tasks.india_tasks.refresh_zerodha_instruments",
        "schedule": crontab(hour=3, minute=5),
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

    # Every 10 min: poll NSE Social Stock Exchange (NPO) announcements —
    # informational only, low filing volume, no need for the equities feed's
    # faster cadence.
    "sync-sse-announcements-10min": {
        "task":     "tasks.india_tasks.sync_sse_announcements",
        "schedule": 600,
    },

    # Daily 10:45 UTC = 4:15 PM IST: save capital snapshot with Sharpe/Treynor/Jensen
    "capital-snapshot-daily": {
        "task":     "tasks.india_tasks.save_capital_snapshot",
        "schedule": crontab(hour=10, minute=45),
    },

    # Weekly Sunday 17:00 UTC = 10:30 PM IST: rebalance signals + risk metrics +
    # AI commentary + PDF, one Telegram message (merged from the previously
    # separate weekly-portfolio-rebalance/weekly-ai-portfolio-report entries,
    # Phase 5 of the alert redesign).
    "weekly-report": {
        "task":     "tasks.india_tasks.weekly_report",
        "schedule": crontab(day_of_week="sunday", hour=17, minute=0),
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
    # Every minute during NSE session (03:45–10:00 UTC = 09:15–15:30 IST).
    # The task itself re-checks the clock and skips outside 09:15–15:30 IST.
    "kite-live-1m-candles": {
        "task":     "tasks.kite_live_candles",
        "schedule": crontab(minute="*/3", hour="3-10", day_of_week="1-5"),
    },
    # DISABLED: legacy kite_refresh_instruments task (replaced by zerodha-nfo above)
    # "kite-refresh-instruments-daily": {
    #     "task":     "tasks.kite_refresh_instruments",
    #     "schedule": crontab(hour=2, minute=45),
    # },
    "kite-check-token-daily": {
        "task":     "tasks.kite_check_token",
        "schedule": crontab(hour=0, minute=35),
    },
    # Daily 02:30 UTC = 08:00 IST: auto-refresh access token before market open.
    # Uses ZERODHA_USER_ID + ZERODHA_PASSWORD + ZERODHA_TOTP_SECRET from .env.
    # On success ZERODHA_ENABLED flips to True in-memory so the ticker can start.
    # Runs every day (not just weekdays) so the token is fresh for after-hours
    # data tasks too; the OAuth flow works regardless of market session.
    "kite-token-refresh-daily": {
        "task":     "tasks.zerodha_token_refresh",
        "schedule": crontab(hour=2, minute=30),
    },
    "kite-start-ticker-on-open": {
        "task":     "tasks.kite_start_ticker",
        "schedule": crontab(hour=3, minute=45),
    },

    # Daily 02:45 UTC = 08:15 IST: auto-refresh Upstox access token before
    # market open (15 min after Zerodha's 08:00 IST slot). Upstox tokens expire
    # daily; this is headless (TOTP), no OAuth browser hop required. Failure
    # after retries alerts via Telegram (see tasks.refresh_upstox_token).
    "upstox-token-refresh-daily": {
        "task":     "tasks.refresh_upstox_token",
        "schedule": crontab(hour=2, minute=45),
    },

    # Every 5 min: warn (Telegram) if the live price feed has gone stale during
    # NSE hours — early warning for a frozen feed (expired token / dead ticker /
    # wedged worker). The task self-gates on market hours.
    "candle-staleness-watchdog-5min": {
        "task":     "tasks.candle_staleness_watchdog",
        "schedule": 300,
    },
    "fetch-earnings-daily": {
        "task":     "tasks.fetch_earnings_transcripts",
        "schedule": crontab(hour=14, minute=30),  # 20:00 IST
    },
    # Master Intelligence Hub: every 15 min during NSE hours (Mon-Fri).
    # This cycle subsumes the agent — it builds the unified context, scores the
    # universe, and drives execution. Times are UTC: NSE 09:15-15:30 IST = 03:45-10:00 UTC.
    "master-intelligence-every-15min": {
        "task":     "tasks.run_master_intelligence_cycle",
        "schedule": crontab(hour="3-10", minute="14,29,44,59", day_of_week="1-5"),
        "options":  {"countdown": 45},  # 45s after bar close so candles are saved
    },
    # EOD reconcile at 15:25 IST = 09:55 UTC
    "agent-eod-reconcile": {
        "task":     "tasks.agent_eod_reconcile",
        "schedule": crontab(hour=9, minute=55, day_of_week="1-5"),
    },

    # ── Breakout Auto-Discovery ───────────────────────────────────────────────
    # Every 5 min during NSE hours: scan ALL 9,600+ NSE symbols for price+volume
    # breakouts (≥4% move + ≥2× volume) and inject them into hub_universe +
    # user_watchlist automatically. This is the fix for the "ROTO problem" —
    # small-cap breakouts that are invisible to the turnover-ranked Hub universe.
    # Injected symbols are scored in the next 15-min Hub cycle and traded normally.
    "breakout-discovery-every-5min": {
        "task":     "tasks.breakout_discovery",
        "schedule": 300,
        "options":  {"countdown": 60},   # 60s after each 5-min mark
    },

    # Every 30 min: scan ALL NSE symbols for SUSTAINED 30-day momentum (10-100%
    # gain over 30 days). Complements the 5-min breakout scan — catches the Eagle
    # Eyes type of slow-grind picks (SAKSOFT +55%, JTEKTINDIA +16%, SIGNPOST +26%)
    # that never trigger the single-day spike screener. Uses 1d candles so it
    # works outside market hours too (e.g. catches stocks that backfilled overnight).
    "momentum-discovery-every-30min": {
        "task":     "tasks.momentum_discovery",
        "schedule": 1800,
        "options":  {"countdown": 90},   # 90s after each 30-min mark
    },

    # ── Intraday MIS trading ──────────────────────────────────────────────────
    # 09:30 IST = 04:00 UTC: open 2-3 equity + optionally 1 NIFTY/BN option as MIS.
    # Uses top Hub BUY signals; separate budget from the positional CNC book.
    "intraday-morning-entry": {
        "task":     "tasks.intraday_entry",
        "schedule": crontab(hour=4, minute=0, day_of_week="1-5"),
    },
    # 15:10 IST = 09:40 UTC: close all MIS positions (10 min before Zerodha 15:20 auto-SO).
    "intraday-eod-squareoff": {
        "task":     "tasks.intraday_squareoff",
        "schedule": crontab(hour=9, minute=40, day_of_week="1-5"),
    },
}


# ── Auto-expiry: bound the queue so stale periodic tasks DROP, never pile up ──
# Root cause of a 63k-task Redis backlog: high-frequency periodic tasks (fast_sl
# every 5s, price scans every 30-60s) were enqueued faster than the worker could
# consume them, and with NO `expires` set they accumulated forever — burying the
# 15-min hub cycle and everything else for days. With an expiry, a task the worker
# can't reach within ~its own cadence is discarded by Celery instead of queued
# indefinitely (the next tick supersedes it), so the queue stays self-bounded.
# Applied programmatically so every current AND future entry is covered.
for _name, _cfg in celery_app.conf.beat_schedule.items():
    _opts = _cfg.setdefault("options", {})
    if "expires" in _opts:
        continue
    _sch = _cfg["schedule"]
    if isinstance(_sch, (int, float)):
        _opts["expires"] = max(int(_sch) * 2, 20)   # interval tasks: ~2 cycles of grace
    elif "master-intelligence" in _name:
        _opts["expires"] = 900                        # 15-min hub cycle: drop if a full cycle late
    else:
        _opts["expires"] = 3600                       # daily/weekly crons: 1h grace
