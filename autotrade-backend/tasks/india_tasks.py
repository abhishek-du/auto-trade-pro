from utils.logger import logger
# Indian market Celery tasks.
#
# Task schedule (all times UTC — Celery runs in UTC):
#   india_price_scan         — every 30 s  (NSE hours only)
#   india_fii_dii_fetch      — daily 13:00 (18:30 IST)
#   india_mutual_fund_nav    — daily 14:30 (20:00 IST, after AMFI publishes)
#   india_fundamental_update — Sunday 18:30 UTC (weekly)
#   india_trade_loop         — every 60 s  (NSE hours + 30 min)
#   train_ml_models_task     — Saturday 20:30 UTC (weekly)
#
# PAPER TRADING ONLY — virtual currency only; no real money is ever involved.

import asyncio
import datetime
from zoneinfo import ZoneInfo

from celery.signals import worker_ready
from tasks.celery_app import celery_app


_IST = ZoneInfo("Asia/Kolkata")


def _run_async(coro):
    return asyncio.run(coro)


def daily_coverage_is_stalled(
    newest: int, baseline: int, min_frac: float = 0.5
) -> bool:
    """Has the full-universe daily candle write stopped landing?

    `newest`   best single-day symbol count in the last few days
    `baseline` best single-day symbol count over the trailing month

    Split out from the watchdog so the decision can be tested directly: the
    interesting cases are a cold start (no history to compare against) and a
    small partial write arriving after a healthy full one, and neither is
    reachable from a test that can only add rows to a live table.

    `baseline > 100` keeps a fresh install quiet — with almost no history every
    comparison looks like a collapse, and an alert nobody can act on is noise.
    """
    if baseline <= 100:
        return False
    return newest < baseline * min_frac


def _is_india_trading_window() -> bool:
    """True during NSE market hours plus 30 minutes after close (9:15–16:00 IST)."""
    now = datetime.datetime.now(_IST)
    if now.weekday() >= 5:          # Saturday or Sunday
        return False
    from crawler.india_price_feed import NSE_HOLIDAYS
    if now.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
        return False
    h, m = now.hour, now.minute
    return ((h, m) >= (9, 15)) and ((h, m) <= (16, 0))


# ── 1. india_price_scan — every 5 min ────────────────────────────────────────

async def _india_price_scan():
    from crawler.india_price_feed import (
        fetch_india_vix,
        fetch_nifty_indices,
        is_nse_market_open,
        run_india_price_crawl,
    )
    from tasks._db import celery_session

    if not is_nse_market_open():
        return

    loop = asyncio.get_event_loop()

    # Launch sync fetches in executor while the candle crawl runs
    idx_fut = loop.run_in_executor(None, fetch_nifty_indices)
    vix_fut = loop.run_in_executor(None, fetch_india_vix)

    async with celery_session() as session:
        result = await run_india_price_crawl(session)
        await session.commit()

    indices, vix = await asyncio.gather(idx_fut, vix_fut)

    nifty      = indices.get("NIFTY50",   {})
    bank_nifty = indices.get("BANKNIFTY", {})
    sensex     = indices.get("SENSEX",    {})

    logger.info(
        f"[india_price_scan] "
        f"symbols={result.get('total_symbols', '?')}  "
        f"candles={result.get('total_candles_saved', '?')}  "
        f"errors={len(result.get('errors', []))}  "
        f"NIFTY={nifty.get('price', 0):,.0f} ({nifty.get('change_pct', 0):+.2f}%)  "
        f"BANKNIFTY={bank_nifty.get('price', 0):,.0f}  "
        f"SENSEX={sensex.get('price', 0):,.0f}  "
        f"VIX={vix:.2f}"
    )


@celery_app.task(
    name="tasks.india_price_scan",
    # 2026-08-04: previously had no override, so it inherited the celery-wide
    # default (300s/600s, tasks/celery_app.py) -- which exactly equals this
    # task's own 300s (5 min) beat interval. Measured real run durations
    # today: min 8s, max 1793s (~30 min), avg 657s, with 19/25 cycles
    # exceeding 300s -- same disease as kite_live_candles (see that task's
    # comment for the full failure chain: SoftTimeLimitExceeded landing
    # inside save_candles_to_db()'s chunk loop). The shared per-chunk-commit
    # fix there already contains the worst case here too, but the task was
    # still routinely overrunning its own kill limit. 2400/2460 gives real
    # headroom above the observed worst case; yfinance (external, rate-
    # limited) is more variable than Kite's REST API, so more headroom than
    # kite_live_candles's 1200/1260.
    soft_time_limit=2400,
    time_limit=2460,
)
def india_price_scan():
    """Fetch OHLCV candles + NIFTY/SENSEX/BANKNIFTY/VIX snapshots. Every 5 min."""
    from utils.config import settings
    import redis.asyncio as _aioredis

    # Overlap guard (2026-08-04) -- confirmed live: two separate Celery
    # worker child processes were both running full india_price_scan crawls
    # at the same time (10-min window, PIDs 3025992 and 3025995 both
    # independently calling fetch_nse_candles), tying up half the 4-worker
    # pool on duplicate work. Same SET NX EX pattern as kite_live_candles_task
    # and the DIRECT_NEWS recheck throttle (paper_trading/trade_simulator.py).
    async def _acquire_lock() -> bool:
        _r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            return bool(await _r.set("india_price_scan:running", "1", nx=True, ex=2520))
        finally:
            await _r.aclose()

    async def _release_lock():
        _r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await _r.delete("india_price_scan:running")
        finally:
            await _r.aclose()

    if not _run_async(_acquire_lock()):
        return {"skipped": "already_running"}

    try:
        _run_async(_india_price_scan())
    finally:
        _run_async(_release_lock())


# ── 2. india_fii_dii_fetch — daily 13:00 UTC (18:30 IST) ─────────────────────

async def _india_fii_dii_fetch():
    import datetime
    from zoneinfo import ZoneInfo
    from crawler.fii_dii_crawler import fetch_fii_dii_data, save_fii_dii_to_db
    from tasks._db import celery_session

    async with celery_session() as session:
        data = await fetch_fii_dii_data(session)
        await save_fii_dii_to_db(data, session)
        await session.commit()

    today_ist = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date()
    data_date = data.get("date")
    is_fresh = data_date == today_ist
    freshness = "FRESH" if is_fresh else f"STALE ({data_date} vs today {today_ist})"
    logger.info(
        f"[india_fii_dii] {freshness}  "
        f"fii_net={data.get('fii_net_buy', 0):+,.0f} Cr  "
        f"dii_net={data.get('dii_net_buy', 0):+,.0f} Cr  "
        f"direction={data.get('market_direction', '?')}"
    )

    # Alert on stale data — fetch_fii_dii_data() already DETECTS this (the
    # freshness check above) but previously only logged it, with no alert of
    # any kind, unlike its sibling _candle_staleness_watchdog. NSE's Akamai/
    # Cloudflare bot detection intermittently blocks both curl_cffi and the
    # httpx fallback on a given day — confirmed via a live audit of the DB
    # (2026-07-14 and 2026-07-06 both silently missing) — and because
    # save_fii_dii_to_db() upserts keyed on the FALLBACK data's own date (not
    # today's), a failed day leaves no row of its own at all, just a
    # redundant re-save of the previous day's row. 1-hour cooldown (shared
    # via Redis, see integrations/alerts/router.py's OPERATIONS/ERROR default)
    # so a multi-day outage doesn't spam.
    if not is_fresh:
        try:
            from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, RawTextPayload
            await publish(AlertEvent(
                category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.CRITICAL,
                dedup_key="fii_dii_stale",
                payload=RawTextPayload(text=(
                    f"⚠️ <b>FII/DII data stale</b> — latest available is {data_date}, "
                    f"today is {today_ist}. NSE fetch (curl_cffi + httpx fallback) likely "
                    f"blocked or today's data not yet published. The macro tool will keep "
                    f"serving this stale figure until the next successful crawl."
                )),
            ))
        except Exception as exc:
            logger.warning(f"[india_fii_dii] stale-data telegram alert failed: {exc}")


@celery_app.task(name="tasks.india_fii_dii_fetch")
def india_fii_dii_fetch():
    """Fetch and persist daily FII/DII flow data from NSE. Daily at 6:30 PM IST."""
    logger.info("[india_fii_dii] Starting")
    _run_async(_india_fii_dii_fetch())


# ── 3c. breakout_discovery — every 5 min during NSE hours ────────────────────

async def _run_breakout_discovery():
    """Scan ALL NSE candles for today's breakout stocks and inject them into the
    hub_universe + user_watchlist so the agent scores and trades them automatically.

    This is the fix for the ROTO problem: small/mid-cap stocks that suddenly
    move 5%+ on heavy volume are invisible to the Hub (which ranks by 30-day avg
    turnover). This engine catches them in real-time and promotes them into the
    agent's scoring universe so no breakout is ever missed again.
    """
    from crawler.india_price_feed import is_nse_market_open
    from engine.breakout_screener import run_breakout_discovery
    from tasks._db import celery_session

    if not is_nse_market_open():
        return {"status": "market_closed"}

    async with celery_session() as session:
        result = await run_breakout_discovery(session)
        await session.commit()

    logger.info(
        f"[breakout_discovery] scanned → {result.get('candidates', 0)} breakouts found | "
        f"injected hub={result.get('injected_hub', 0)} watchlist={result.get('injected_watchlist', 0)}"
    )
    if result.get("symbols"):
        for s in result["symbols"][:5]:
            logger.info(
                f"[breakout_discovery] 🚀 {s['symbol'].replace('.NS', '')}  "
                f"{s['change_pct']:+.1f}%  vol={s['volume_ratio']:.1f}×  {s['reason']}"
            )
    return result


@celery_app.task(name="tasks.breakout_discovery")
def breakout_discovery():
    """Every 5 min: scan ALL NSE symbols for price+volume breakouts and auto-inject
    them into hub_universe + user_watchlist so the agent never misses a ROTO-type move.
    """
    logger.info("[breakout_discovery] Starting breakout scan")
    return _run_async(_run_breakout_discovery())


# ── 3d. momentum_discovery — every 30 min ──────────────────────────────────────

async def _run_momentum_discovery():
    """Scan ALL NSE daily candles for stocks with sustained 30-day uptrends.

    Complements breakout_discovery (which catches single-day spikes). This catches
    the Eagle Eyes type of picks: stocks that have been gradually rising 10-100%
    over 30 days with no single explosive day — like SAKSOFT +55%, JTEKTINDIA +16%.

    Runs every 30 min so newly backfilled symbols get discovered quickly.
    Uses 1d candles so it works any time of day (not only market hours).
    """
    from engine.momentum_screener import run_momentum_discovery
    from tasks._db import celery_session

    async with celery_session() as session:
        result = await run_momentum_discovery(session)
        await session.commit()

    logger.info(
        f"[momentum_discovery] scanned → {result.get('candidates', 0)} candidates | "
        f"injected hub={result.get('injected_hub', 0)} watchlist={result.get('injected_watchlist', 0)}"
    )
    if result.get("symbols"):
        for s in result["symbols"][:5]:
            logger.info(
                f"[momentum_discovery] 📈 {s['symbol'].replace('.NS', '')}  "
                f"{s['return_30d']:+.1f}% 30d  vol_trend={s['volume_trend']:.1f}×  RSI={s['rsi']:.0f}"
            )
    return result


@celery_app.task(name="tasks.momentum_discovery", soft_time_limit=300, time_limit=420)
def momentum_discovery():
    """Every 30 min: scan ALL NSE symbols for sustained 30-day uptrends and
    auto-inject them into hub_universe + user_watchlist.

    This is the fix for the Eagle Eyes / slow-momentum problem:
    stocks rising 10-100% over 30 days that never trigger the single-day
    breakout screener because their daily moves are modest.
    """
    logger.info("[momentum_discovery] Starting slow-momentum scan")
    return _run_async(_run_momentum_discovery())


# ── 4. india_mutual_fund_nav — daily 14:30 UTC (20:00 IST) ───────────────────

async def _india_mutual_fund_nav():
    from crawler.india_price_feed import fetch_all_mutual_fund_navs
    from engine.mutual_fund_analyzer import fetch_and_save_nav
    from tasks._db import celery_session

    loop = asyncio.get_event_loop()

    # Bulk-fetch from AMFI — synchronous, runs in thread pool
    nav_list = await loop.run_in_executor(None, fetch_all_mutual_fund_navs)

    if not nav_list:
        logger.warning("[india_mf_nav] No NAV data from AMFI — skipping DB update")
        return

    async with celery_session() as session:
        saved = 0
        for entry in nav_list:
            try:
                await fetch_and_save_nav(entry["scheme_code"], session)
                saved += 1
            except Exception as exc:
                logger.warning(
                    f"[india_mf_nav] Failed to persist {entry['scheme_code']}: {exc}"
                )
        await session.commit()

    logger.info(f"[india_mf_nav] Persisted {saved}/{len(nav_list)} MF NAVs")


@celery_app.task(name="tasks.india_mutual_fund_nav")
def india_mutual_fund_nav():
    """Fetch and persist AMFI NAVs for all watchlist schemes. Daily at 8 PM IST."""
    logger.info("[india_mf_nav] Starting")
    _run_async(_india_mutual_fund_nav())


# ── 5. india_fundamental_update — Sunday 18:30 UTC ───────────────────────────

async def _india_fundamental_update():
    from engine.fundamental_analyzer import run_fundamental_update
    from tasks._db import celery_session

    async with celery_session() as session:
        await run_fundamental_update(session)
        await session.commit()


@celery_app.task(name="tasks.india_fundamental_update",
                 soft_time_limit=5400, time_limit=6000)   # 90 min / 100 min cap
def india_fundamental_update():
    """Weekly fundamental data refresh (PE, ROE, promoter holding…) for all NSE stocks."""
    logger.info("[india_fundamentals] Starting weekly refresh")
    _run_async(_india_fundamental_update())


# ── 6. india_trade_loop — every 60 s ─────────────────────────────────────────

_fast_sl_heartbeat_ts: float = 0.0          # throttle the fast-SL "alive" heartbeat log
_MAX_SHORTLIST_PER_CYCLE = 5


async def _send_loop_shortlist_alert(signal) -> None:
    """Send a full shortlist Telegram alert with 7-factor breakdown + web research.

    Fires regardless of whether a trade was actually opened (executed=False
    always, for this call path). Re-alerts a symbol ONLY when its content
    changes — content-delta dedup now lives in integrations/alerts/dedup.py
    (shared across worker processes via Redis; this was previously an
    in-process dict, duplicated with a slightly different policy in
    engine/agent/agent_loop.py's _send_shortlist_alert — both now go through
    the same shortlist_gate()). Non-blocking; swallows all errors.
    """
    from utils.config import settings as _s
    if not _s.telegram_available:
        return
    bare = signal.symbol.replace(".NS", "")
    score    = round(signal.final_score, 1)
    cur_news = round(float((getattr(signal, "hub_subscores", {}) or {}).get("news", 0) or 0), 1)

    # Cheap read-only peek so an alert that will just be suppressed doesn't
    # burn Tavily/LLM credits on research first. The authoritative,
    # state-mutating dedup decision happens once, inside publish() below.
    from integrations.alerts.dedup import shortlist_would_alert
    if not await shortlist_would_alert(bare, score, cur_news, executed=False):
        logger.debug(f"[trade_loop/shortlist] {bare} unchanged or too recent — skip")
        return

    # ── Tavily search + crawl (advanced depth = full article text) ────────────
    crawl_data: dict = {}
    ai_note: str = ""
    try:
        from engine.tavily_enricher import search_and_crawl, research_stock_for_alert
        if _s.tavily_available:
            crawl_data = await search_and_crawl(
                signal.symbol,
                query_suffix="NSE India stock news analysis 2026",
                crawl_top=2,
                extract_depth="basic",
            )
            # Also get the structured AI note from advanced search
            ai_note = await research_stock_for_alert(
                symbol=signal.symbol, score=float(score),
                tech_score=float(signal.hub_subscores.get("technical", 0)),
                news_score=float(signal.hub_subscores.get("news", 0)),
                regime=getattr(signal, "regime", ""),
                entry=signal.entry_price,
                stop=signal.stop_loss or 0.0,
                t1=signal.take_profit or 0.0,
                t2=getattr(signal, "target_2", 0.0) or 0.0,
            ) or ""
    except Exception as exc:
        logger.debug(f"[trade_loop/shortlist] research failed {bare}: {exc}")

    try:
        from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, ShortlistPayload
        await publish(AlertEvent(
            category=AlertCategory.SHORTLIST, action=AlertAction.ALERT, severity=Severity.INFO,
            symbol=bare,
            payload=ShortlistPayload(
                candidate=signal, score=score, news_subscore=cur_news, executed=False,
                df=None, ai_note=ai_note, crawl_data=crawl_data or None,
            ),
        ))
        logger.info(f"[trade_loop/shortlist] ✓ alert sent for {bare} score={score:+.0f} news={cur_news:+.0f}")
    except Exception as exc:
        logger.debug(f"[trade_loop/shortlist] send failed {bare}: {exc}")


async def _phase9_market_context(session) -> dict:
    """Compute once-per-cycle market-level Phase 9 inputs.

    Returns:
        nifty_ema200_ok  — True  if Nifty is ABOVE its 200-day EMA (long-term bull)
        nifty_roc20      — Nifty's 20-day rate-of-change (%), used for RS filter
        regime_allows_buy— True if 5-state regime engine is STRONG_BULL or MODERATE_BULL
        regime_allows_sell— True unless 5-state regime engine is STRONG_BULL (mirrors
                             RegimeResult.can_sell — don't short into a strong uptrend)
    """
    from sqlalchemy import text as _text
    import pandas as _pd
    from engine.agent.market_regime import classify_regime, build_regime_map_from_df

    result = {
        "nifty_ema200_ok": False, "nifty_roc20": 0.0,
        "regime_allows_buy": False, "regime_allows_sell": False,
    }
    try:
        rows = (await session.execute(_text("""
            SELECT close FROM candles
            WHERE symbol = 'NIFTYBEES.NS' AND timeframe = '1d'
            ORDER BY timestamp DESC LIMIT 220
        """))).scalars().all()

        if len(rows) >= 30:
            closes = _pd.Series(list(reversed(rows)), dtype=float)
            ema200 = closes.ewm(span=200, adjust=False).mean().iloc[-1]
            last   = closes.iloc[-1]
            result["nifty_ema200_ok"] = last >= ema200
            # ROC20: (today - 20 days ago) / 20 days ago × 100
            if len(closes) >= 21:
                result["nifty_roc20"] = float((last - closes.iloc[-21]) / closes.iloc[-21] * 100)

        # 5-state regime — use classify_regime_async which pulls live VIX from
        # PRICE_CACHE (kite_ws feed) so the score includes all 5 signals.
        try:
            from engine.agent.market_regime import get_market_regime as _creg
            # Breadth: advances/(advances+declines) from DB if available.
            _breadth: float | None = None
            try:
                from sqlalchemy import text as _bt
                async with session.begin_nested():   # savepoint — failure doesn't poison outer tx
                    _brow = (await session.execute(_bt("""
                        SELECT advances, declines FROM market_breadth_snapshots
                        ORDER BY timestamp DESC LIMIT 1
                    """))).one_or_none()
                    if _brow and (_brow[0] + _brow[1]) > 0:
                        _breadth = float(_brow[0]) / (_brow[0] + _brow[1]) * 100
            except Exception:
                pass  # table may not exist; VIX from PRICE_CACHE is still used
            regime_result = await _creg(session, breadth_pct=_breadth)
            state = regime_result.state
            result["regime_allows_buy"] = state in ("STRONG_BULL", "MODERATE_BULL")
            result["regime_allows_sell"] = regime_result.can_sell
            result["regime_state"] = state
            result["regime_score"] = regime_result.score
        except Exception as _re:
            logger.debug(f"[phase9] regime engine failed: {_re}")

    except Exception as exc:
        logger.warning(f"[phase9] market context failed: {exc}")
    return result


async def _india_trade_loop():
    from sqlalchemy import select

    # Admin toggle (Path B). Gates ENTRIES only -- the exit/risk-management work
    # further down (auto-close, dynamic SL/TP, drawdown breakers) is what keeps
    # open positions safe and must run regardless, which is why this is checked
    # at the entry branch rather than returning here. See _entries_enabled below.

    from db.models import OpenPosition
    from engine.llm_explainer import (
        format_paper_trade_notification,
        generate_trade_explanation,
    )
    from engine.risk_manager import calculate_position_size, validate_signal
    from paper_trading.simulation_logger import SimLogger
    from paper_trading.trade_simulator import (
        open_paper_trade,
        update_positions_with_current_prices,
    )
    from paper_trading.virtual_wallet import VirtualWallet
    from tasks._db import celery_session

    from utils.config import settings as _cfg

    now_ist   = datetime.datetime.now(_IST)
    is_window = _is_india_trading_window()
    logger.info(
        f"[india_trade_loop] NSE market status: {'OPEN' if is_window else 'CLOSED'} "
        f"— IST time: {now_ist.strftime('%H:%M:%S')}"
    )
    if not is_window:
        return

    is_entry_window = now_ist.time() < datetime.time(15, 20)

    # ── Live snapshot: hot-patch PRICE_CACHE + SECTOR_CACHE from Kite ─────
    from crawler.live_snapshot import fetch_live_snapshot
    await fetch_live_snapshot()


    async with celery_session() as session:
        # Step 1: close SL/TP hits, refresh unrealised PnL
        # (runs BEFORE the halt gate — a halted book must still be de-risked,
        # and the breaker below needs the freshly marked equity)
        auto_closed = await update_positions_with_current_prices(session)
        if auto_closed:
            logger.info(
                f"[india_trade_loop] {len(auto_closed)} position(s) auto-closed"
            )
            from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, TradeExitPayload
            for c in auto_closed:
                await publish(AlertEvent(
                    category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
                    symbol=c["symbol"], trade_id=c.get("trade_id"),
                    payload=TradeExitPayload(
                        symbol=c["symbol"], side=c["direction"], entry=c["entry_price"],
                        exit_price=c["exit_price"], qty=c["size_units"], pnl=c["pnl"], reason=c["reason"],
                    ),
                ))

        # ── AI Dynamic Management: LLM manages SL/TP for open positions ────────
        #
        # Observability only (2026-08-26, phase 13). On 2026-08-26 this task ran
        # to its Celery time limits 16 times between 10:18 and 12:03 IST —
        # SoftTimeLimitExceeded at 300s, and 5 hard kills at 600s that SIGKILL
        # the pool worker with no cleanup. The hang is an I/O wait somewhere in
        # exit management, but three evidence sources cannot name it: the
        # soft-limit traceback lands in the event loop's epoll.poll() rather
        # than the awaiting coroutine, llm_dynamic_sl_tp logs nothing at all,
        # and py-spy needs ptrace privileges this host does not grant.
        #
        # These two log lines close that gap. They are additive: the try/except
        # below is byte-for-byte the original, the same exception is caught with
        # the same message, and nothing branches on the timing.
        #
        # A START with no END is itself the signal — that is what a SIGKILL at
        # the 600s hard limit looks like from the log.
        # Overall deadline (2026-08-26, phase 14). The per-position 60s bound in
        # dynamic_management is not sufficient on its own: :309 gathers every
        # position concurrently, but they contend for ONE shared Redis rate slot,
        # so a book of N positions can serialise into N x 60s. This second layer
        # bounds the whole block regardless of how many positions there are.
        #
        # 120s leaves 180s of the 300s Celery soft limit for the DB session to
        # unwind, the remaining exit-management work, exception handling and task
        # acknowledgement. Deliberately not near 299.
        #
        # A timeout here is caught by the SAME `except Exception` that was always
        # there — asyncio.TimeoutError subclasses OSError subclasses Exception —
        # so the loop continues to its existing behaviour: exits, breakers and
        # everything downstream still run. Dynamic management is simply
        # unavailable for this cycle.
        import asyncio as _aio14
        import time as _t13
        _DYN_MGMT_DEADLINE_SEC = 120.0

        # Close the position-update transaction before the LLM stage begins
        # (2026-08-26). update_positions_with_current_prices() above reaches
        # VirtualWallet, whose _fetch() does SELECT ... FOR UPDATE on the single
        # wallet row; without this commit that exclusive lock is held from :542
        # until the end-of-cycle commit at :1410 — across the Bedrock call
        # below. That is what produced the 44-backend convoy on 2026-08-26:
        # every other task and API handler wanting the wallet queued behind one
        # trade loop that was waiting on an external HTTP response.
        #
        # Committing at this phase boundary is the same reasoning the existing
        # commit at :1379 already documents ("so the position is persisted even
        # if the task hits SoftTimeLimitExceeded") — and it makes the exits and
        # PnL marks from the phase above durable BEFORE a stage that can time
        # out. Safe for the ORM objects still in use: celery_session() sets
        # expire_on_commit=False (tasks/_db.py).
        await session.commit()

        _dyn_t0 = _t13.monotonic()
        logger.info("[india_trade_loop] LLM_DYNAMIC_SL_TP_START")
        try:
            from engine.agent.dynamic_management import llm_dynamic_sl_tp
            await _aio14.wait_for(
                llm_dynamic_sl_tp(session), timeout=_DYN_MGMT_DEADLINE_SEC
            )
            logger.info(
                f"[india_trade_loop] LLM_DYNAMIC_SL_TP_END ok=True "
                f"elapsed_ms={int((_t13.monotonic() - _dyn_t0) * 1000)}"
            )
        except _aio14.TimeoutError:
            # Distinct classification. Would be caught by the handler below in any
            # case; splitting it makes the log searchable and states plainly that
            # no position was changed.
            logger.error(
                f"[india_trade_loop] LLM_DYNAMIC_SL_TP_TIMEOUT "
                f"elapsed_ms={int((_t13.monotonic() - _dyn_t0) * 1000)} "
                f"deadline_s={_DYN_MGMT_DEADLINE_SEC} — dynamic management "
                f"unavailable this cycle, positions unchanged"
            )
        except Exception as e:
            logger.error(f"[india_trade_loop] Dynamic management failed: {e}")
            logger.info(
                f"[india_trade_loop] LLM_DYNAMIC_SL_TP_ERROR exc={type(e).__name__} "
                f"elapsed_ms={int((_t13.monotonic() - _dyn_t0) * 1000)}"
            )

        # Circuit breaker + halt gate — AFTER exit management, BEFORE any entry.
        # check_drawdown_breakers trips the sticky trading_halted flag on a
        # >max_daily_loss mark-to-market day loss (audit P2.11).
        from paper_trading.virtual_wallet import VirtualWallet
        from utils.runtime_config import RuntimeConfig
        try:
            halted = await VirtualWallet.check_drawdown_breakers(session)
        except Exception as _brk_exc:
            logger.error(f"[india_trade_loop] breaker check failed: {_brk_exc}")
            halted = (await RuntimeConfig.load(session)).trading_halted
        if halted:
            logger.warning("[india_trade_loop] TRADING HALTED — exits done, no new entries")
            return

        # Transient market-shock cooldown — after a FLATTEN the shock guard blocks
        # new entries for a few minutes so this loop doesn't re-buy into the crash.
        if (await RuntimeConfig.load(session)).shock_cooldown_active:
            logger.warning("[india_trade_loop] SHOCK COOLDOWN active — exits done, no new entries")
            return

        # ── News-Only Target Architecture (Phase 1) — RE-BLOCKED 2026-08-19 ──
        # docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md §6: this is the "main
        # equity/short loop" — a technical-only BUY/SELL loop with no news
        # catalyst requirement.
        #
        # History: hard-blocked under the News-Only architecture, then set to
        # False on 2026-07-24 by explicit user instruction after the news-driven
        # path (news_discovery_engine.py) went ~2 days without a single trade
        # during the Bedrock LLM outage (empty content on ~80% of candidate
        # evaluations). That outage was resolved 2026-08-17, so the
        # justification no longer held — audit finding D1
        # (docs/DEEP_AUDIT_2026-08-19.md) flagged that the contract was
        # consequently unenforced on this loop, and that P&L attribution was
        # pooling news-driven and technical trades. Restored to True.
        #
        # Note this is belt-and-braces: engine/decision_router.py's
        # _TECHNICAL_TRADE_ORIGINATION_BLOCKED rejects any TECHNICAL-family
        # intent anyway. Blocking here as well avoids burning a full LLM
        # reasoning gate + pre-trade research + risk validation per candidate
        # every 60s on candidates that can never execute.
        #
        # Exit/risk management above (Step 1 auto-close, dynamic SL/TP, circuit
        # breaker) runs unconditionally either way and is unaffected.
        # Owner decision 2026-08-20 (contract SS10b): hub entries re-enabled.
        _NEWS_ONLY_BLOCKS_HUB_ENTRIES = bool(
            getattr(settings, "NEWS_ONLY_BLOCKS_HUB_ENTRIES", True)
        )
        # Admin toggle (Path B). Gates ENTRIES only: everything above this point
        # -- auto-close, dynamic SL/TP, the drawdown circuit breaker -- has
        # already run and keeps running when the strategy is switched off.
        # Disabling a strategy must never strand an open position.
        from utils.runtime_config import strategy_enabled

        _strategy_on = await strategy_enabled("india_trade_loop", session)
        if not _strategy_on:
            logger.info("[india_trade_loop] new entries disabled by strategy toggle — exits still ran")
        if _NEWS_ONLY_BLOCKS_HUB_ENTRIES or not is_entry_window or not _strategy_on:
            logger.info(
                "[india_trade_loop] new-entry origination disabled — News-Only architecture "
                "hard-block and/or past 15:20 IST — exits done, no new entries"
            )
            return

        # Step 2: read actionable signals from market_shortlist — the SINGLE source of
        # truth produced by the market scanner (compute_indicators → composite_score →
        # signal). This is exactly what the scanner UI and the /s/:symbol page show, so
        # the agent now trades precisely what the user sees. Fast (one DB read).
        from utils.config import settings
        from db.models import MasterIntelligenceScore
        from crawler.price_feed import get_latest_candles
        from crawler.live_prices import PRICE_CACHE
        from engine.signal_generator import TradingSignal as _TS
        from engine.india_specific import SECTOR_MAP

        conf_threshold = float(getattr(settings, "PAPER_CONFIDENCE_THRESHOLD", 40.0))
        _ACTIONABLE = ["BUY", "STRONG_BUY", "SELL", "STRONG_SELL"]

        # ── Candidate universe: Hub 7-factor scores ONLY. Every tradeable symbol
        # is deep-scored by the Master Intelligence Hub (technical + news +
        # fundamentals + earnings + sector + macro + options) over the ~500-name
        # turnover-ranked universe. Symbols without a Hub score are NOT eligible —
        # the technical-only fallback was removed so the agent never trades a name
        # it hasn't fully vetted. The Hub recomputes every 15 min → cheap DB read.

        # ── Get the LATEST score for each symbol, then filter by signal.
        # Doing DISTINCT ON + signal filter in one query is wrong: it returns the
        # latest BUY record which may be days old (e.g., HDFCGOLD STRONG_BUY June 10
        # while today's hub says NEUTRAL). Always trade the current assessment.
        from sqlalchemy import func as _func
        import datetime as _dt
        _now_utc = _dt.datetime.utcnow()
        _open_utc = _now_utc.replace(hour=3, minute=45, second=0, microsecond=0)
        _cutoff = max(_now_utc - _dt.timedelta(minutes=45), _open_utc)

        _latest_subq = (
            select(
                MasterIntelligenceScore.symbol.label("sym"),
                _func.max(MasterIntelligenceScore.scored_at).label("max_at"),
            )
            .where(
                MasterIntelligenceScore.symbol.like("%.NS"),
                MasterIntelligenceScore.scored_at >= _cutoff,
            )
            .group_by(MasterIntelligenceScore.symbol)
        ).subquery()

        hub_subq = (
            select(
                MasterIntelligenceScore.symbol,
                MasterIntelligenceScore.master_score,
                MasterIntelligenceScore.signal,
                MasterIntelligenceScore.regime,
                MasterIntelligenceScore.technical_score,
                MasterIntelligenceScore.news_score,
                MasterIntelligenceScore.sector_score,
                MasterIntelligenceScore.macro_score,
                MasterIntelligenceScore.earnings_score,
                MasterIntelligenceScore.fundamental_score,
                MasterIntelligenceScore.options_score,
                MasterIntelligenceScore.reasoning,
                MasterIntelligenceScore.scored_at,
            )
            .join(
                _latest_subq,
                (MasterIntelligenceScore.symbol == _latest_subq.c.sym)
                & (MasterIntelligenceScore.scored_at == _latest_subq.c.max_at),
            )
            .where(
                MasterIntelligenceScore.is_blocked == False,
                MasterIntelligenceScore.signal.in_(_ACTIONABLE),
            )
        ).subquery()
        hub_rows = (await session.execute(select(hub_subq))).all()

        candidates: list[dict] = [
            {
                "symbol": r.symbol, "score": float(r.master_score), "signal": r.signal,
                "source": "hub", "sector": SECTOR_MAP.get(r.symbol.replace(".NS", ""), ""),
                "rsi": None,
                "hub_subscores": {
                    "technical":   float(r.technical_score),
                    "news":        float(r.news_score),
                    "sector":      float(r.sector_score),
                    "macro":       float(r.macro_score),
                    "earnings":    float(r.earnings_score),
                    "fundamental": float(r.fundamental_score),
                    "options":     float(r.options_score),
                    "signal":      r.signal,
                    "regime":      r.regime,
                    "reasoning":   r.reasoning or {},
                    "scored_at":   r.scored_at.isoformat() if r.scored_at else "",
                },
            }
            for r in hub_rows
        ]

        if not candidates:
            logger.warning(
                "[india_trade_loop] no Hub-scored actionable symbols — "
                "agent idle this cycle (Hub may not have run yet)"
            )
        logger.info(
            f"[india_trade_loop] candidates: {len(candidates)} (Hub 7-factor only)"
        )

        # ── Portfolio-aware weight caps ──────────────────────────────────────
        from engine.portfolio_analytics import (
            get_position_weights,
            get_sector_weights,
            compute_adjusted_score,
        )
        from db.models import PortfolioPolicy
        _policy_row = (await session.execute(select(PortfolioPolicy).limit(1))).scalar_one_or_none()
        _max_stock_w  = float(_policy_row.max_single_stock_weight) if _policy_row else 10.0
        _max_sector_w = float(_policy_row.max_sector_weight) if _policy_row else 25.0

        _pos_weights    = await get_position_weights(session)
        _sector_weights = await get_sector_weights(session, _pos_weights)

        signals: list = []
        for c in candidates:
            conf = min(100.0, abs(c["score"]))
            # Strong-news override: a strongly-positive Hub news sub-score lets a
            # BUY survive even when the bear-dragged master score is below the
            # actionable threshold (it will bypass the regime gates downstream).
            _news_sub = float((c.get("hub_subscores") or {}).get("news", 0) or 0)
            _news_override = bool(
                getattr(settings, "NEWS_REGIME_OVERRIDE_ENABLED", False)
                and _news_sub >= getattr(settings, "NEWS_OVERRIDE_MIN_NEWS_SCORE", 75.0)
            )
            if conf < conf_threshold and not _news_override:
                continue
            if _news_override:
                action = "BUY"
                # Effective confidence scales with news strength so the candidate
                # ranks into the level pool instead of being crowded out.
                conf = max(conf, min(100.0, 50.0 + _news_sub * 0.5))
            else:
                action = "BUY" if "BUY" in c["signal"] else "SELL"
            # Shorts need higher conviction — only short on strong Hub signals
            if action == "SELL" and conf < 50:
                continue

            # Entry price resolution — must be LIVE, never a stale daily close.
            # Order: WebSocket cache → Kite REST LTP → freshest recent candle.
            # Phantom-fill fix: the old fallback used get_latest_candles('1d', 1),
            # which on a cold cache filled entries at the *last daily close* — days
            # old when the daily backfill lagged (observed 9-Jul: JINDRILL filled at
            # ₹601 = 7-Jul close while the live tape was ₹642). Now a candle is only
            # accepted if it is fresh (<=90 min old during the session); otherwise
            # the trade is SKIPPED rather than filled at a phantom price.
            sym_base = c["symbol"].replace(".NS", "")
            cached = PRICE_CACHE.get(c["symbol"]) or PRICE_CACHE.get(sym_base)
            entry_price = float(cached.get("price", 0) if isinstance(cached, dict) else getattr(cached, "price", 0) if cached else 0)
            if entry_price <= 0:
                # Kite REST LTP — real-time, authoritative.
                try:
                    from crawler.zerodha_market import get_live_prices as _glp
                    _ns = c["symbol"] if c["symbol"].endswith(".NS") else f"{c['symbol']}.NS"
                    _q = await _glp([_ns])
                    _qd = _q.get(_ns) or _q.get(c["symbol"]) or {}
                    _px = _qd.get("price") or _qd.get("last_price")
                    if _px and float(_px) > 0:
                        entry_price = float(_px)
                except Exception:
                    pass
            if entry_price <= 0:
                # Freshest candle across ALL timeframes, but only if recent.
                try:
                    from crawler.price_feed import get_freshest_candle
                    _cl, _cts = await get_freshest_candle(c["symbol"], session)
                    if _cl and _cts:
                        if getattr(_cts, "tzinfo", None):
                            _cts = _cts.replace(tzinfo=None)
                        _age_min = (datetime.datetime.utcnow() - _cts).total_seconds() / 60
                        if _age_min <= 90:
                            entry_price = _cl
                        else:
                            logger.warning(
                                f"[india_trade_loop] {c['symbol']}: no live price and "
                                f"freshest candle is {_age_min/60:.1f}h old — SKIP "
                                f"(refusing phantom fill at stale price ₹{_cl})"
                            )
                except Exception:
                    entry_price = 0.0
            if entry_price <= 0:
                continue   # can't size a trade without a fresh, real price

            # Provisional levels for ranking only — REAL dynamic SL/targets are
            # computed below (compute_indicators → compute_trade_levels) for just
            # the top candidates, so we don't recompute indicators for all ~70.
            if action == "BUY":
                stop_loss, take_profit = entry_price * 0.95, entry_price * 1.10
            else:
                # Tighter provisional levels for shorts (1×ATR proxy ≈ 3%)
                stop_loss, take_profit = entry_price * 1.03, entry_price * 0.94

            # ── Portfolio caps: skip if single-stock or sector limit exceeded ──
            if action == "BUY":
                sym_w    = _pos_weights.get(c["symbol"], 0.0)
                sec      = c.get("sector", "Other") or "Other"
                sector_w = _sector_weights.get(sec, 0.0)

                if sym_w >= _max_stock_w:
                    continue  # already at cap for this stock
                if sector_w >= _max_sector_w:
                    continue  # sector cap reached

                # Adjusted confidence: scales down as current weight approaches cap
                adj_conf = compute_adjusted_score(conf, sym_w, _max_stock_w)
                if adj_conf <= 0:
                    continue
                conf = adj_conf  # use the portfolio-aware score

            if c["source"] == "hub":
                why = (f"Hub 7-factor score {c['score']:+.0f} → {c['signal']} "
                       f"(technical+news+fundamentals+sector+macro+earnings+options)")
            else:
                why = f"Technical score {c['score']:+.0f} → {c['signal']}"
            if c["sector"]:
                why += f" · {c['sector']}"

            signals.append(_TS(
                symbol=c["symbol"],
                action=action,
                confidence=conf,
                final_score=float(c["score"]),
                pattern_score=0.0,
                indicator_score=float(c["score"]),
                sentiment_score=0.0,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_reward_ratio=2.0,
                patterns_detected=[],
                reasoning_points=[why],
                regime=c["hub_subscores"].get("regime", "") if c.get("hub_subscores") else "",
                timeframe="1d",
                hub_subscores=c.get("hub_subscores", {}),
                news_override=_news_override,
            ))

        actionable = [s for s in signals if s.action in ("BUY", "SELL")]
        logger.info(
            f"[india_trade_loop] candidates={len(candidates)}  "
            f"above_{conf_threshold:.0f}%={len(actionable)}  "
            f"(buy={sum(1 for s in actionable if s.action=='BUY')} "
            f"sell={sum(1 for s in actionable if s.action=='SELL')})"
        )

        if not actionable:
            await VirtualWallet.take_daily_snapshot(session)
            await session.commit()
            return

        # Step 4: rank by confidence. The agent no longer opens a fixed "top 5" —
        # it works down the ranked list and keeps opening while the portfolio risk
        # budget and cash buffer allow (enforced inside validate_signal), up to a
        # per-cycle new-entry cap. Capital is deployed by ANALYSIS, not a count.
        actionable.sort(key=lambda s: s.confidence, reverse=True)
        max_new = int(getattr(settings, "MAX_NEW_ENTRIES_PER_CYCLE", 8))
        # Compute dynamic levels for a pool large enough to fill the cap after
        # rejections (dup symbols, budget) — cap the indicator work at 24/cycle.
        level_pool = actionable[: min(len(actionable), max(max_new * 3, 12), 24)]

        # ── Phase 9 market context: EMA200 gate + regime + Nifty ROC20 ──────────
        # Computed once per cycle (one DB read) — attached to every signal in
        # the pool so the per-signal gate below needs no extra DB round-trip.
        _p9ctx = await _phase9_market_context(session)
        logger.info(
            f"[phase9] EMA200={'OK' if _p9ctx['nifty_ema200_ok'] else 'BELOW'} | "
            f"regime={_p9ctx.get('regime_state','?')} | "
            f"nifty_roc20={_p9ctx['nifty_roc20']:+.2f}% | "
            f"buy={'OK' if _p9ctx.get('regime_allows_buy', True) else 'BLOCKED'} | "
            f"sell={'OK' if _p9ctx.get('regime_allows_sell', True) else 'BLOCKED'}"
        )

        # Step 4b: compute REAL dynamic SL/targets + Phase 9 per-signal indicators.
        from engine.indicators import compute_indicators
        from engine.risk_manager import compute_trade_levels
        from engine.agent.analyzer import MarketAnalyzerAgent
        from engine.agent.strategies.pullback_trend import PullbackTrendLong
        import pandas as pd
        _pullback_strategy = PullbackTrendLong()
        _market_analyzer   = MarketAnalyzerAgent()
        for signal in level_pool:
            # Phase 9 per-signal defaults (safe fallback = gate passes)
            signal.phase9_roc20        = 0.0
            signal.phase9_ema20_slope_ok = True
            # Pullback pattern defaults to False — must be positively confirmed.
            # The Hub score identifies candidate stocks; this gate confirms the
            # entry timing matches the exact Phase 9 PULLBACK_LONG conditions.
            signal.phase9_pullback_ok  = False
            try:
                candles = await get_latest_candles(signal.symbol, "1d", 200, session)
                sig_ind = None
                if len(candles) >= 20:
                    df = pd.DataFrame([{"open": c.open, "high": c.high, "low": c.low,
                        "close": c.close, "volume": c.volume, "timestamp": c.timestamp}
                        for c in candles])
                    sig_ind = compute_indicators(df)
                    # Phase 9 RS filter: stock's 20-day ROC
                    closes = df["close"]
                    if len(closes) >= 21:
                        signal.phase9_roc20 = float(
                            (closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21] * 100
                        )
                    # Phase 9 EMA20 slope filter: EMA20 today vs 5 bars ago
                    if len(closes) >= 26:
                        ema20_series    = closes.ewm(span=20, adjust=False).mean()
                        ema20_today     = float(ema20_series.iloc[-1])
                        ema20_5ago      = float(ema20_series.iloc[-6])
                        signal.phase9_ema20_slope_ok = ema20_today > ema20_5ago
                    # Phase 9 PULLBACK pattern — mirrors PullbackTrendLong.evaluate()
                    # exactly: EMA20 touch on prev bar, bounce above EMA20, vol spike,
                    # RSI 50-70, ADX>20, EMA20>EMA50, EMA50>=EMA200×1.01, shallow touch,
                    # quiet prev bar, ADX not collapsing. Needs 30+ bars for MarketFeatures.
                    if len(candles) >= 30:
                        try:
                            _f = _market_analyzer.compute_features(df)
                            _pr = _pullback_strategy.evaluate(signal.symbol, df, _f, 0, "WATCHLIST")
                            signal.phase9_pullback_ok = (_pr is not None)
                        except Exception as _pe:
                            logger.debug(f"[phase9] {signal.symbol} pullback eval error: {_pe}")
                lv = compute_trade_levels(signal.action, signal.entry_price, sig=sig_ind)
                signal.stop_loss = lv["stop_loss"]
                signal.take_profit = lv["target_1"]   # T1 = first checkpoint / trailing trigger
                signal.target_2 = lv["target_2"]      # final target — position rides here
                signal.atr = lv["atr"]
                # Varsity checklist item 2: carry S&R levels for the 4% gate in
                # validate_signal().  Only present on the "dynamic" path (pivot S&R).
                signal.sr_support    = lv.get("support", 0.0) or 0.0
                signal.sr_resistance = lv.get("resistance", 0.0) or 0.0
                risk = abs(signal.entry_price - lv["stop_loss"])
                signal.risk_reward_ratio = round(abs(lv["target_2"] - signal.entry_price) / risk, 2) if risk > 0 else 0.0
                # Build rich expert note — replaces the simple one-liner
                try:
                    from integrations.trade_explainer import build_expert_note
                    hub_dict = getattr(signal, "hub_subscores", None) or {}
                    raw_reason = " · ".join(signal.reasoning_points)
                    expert = build_expert_note(
                        symbol=signal.symbol,
                        direction=signal.action,
                        entry=signal.entry_price,
                        stop=lv["stop_loss"],
                        target_1=lv["target_1"],
                        target_2=lv["target_2"],
                        confidence=signal.confidence,
                        hub=hub_dict or None,
                        reasoning=raw_reason,
                        strategy=getattr(signal, "strategy", "HUB_SIGNAL"),
                        regime=getattr(signal, "regime", "") or hub_dict.get("regime", ""),
                    )
                    signal.reasoning_points = [expert]
                except Exception:
                    signal.reasoning_points.append(
                        f"Trade levels [{lv['source']}]: SL ₹{lv['stop_loss']} · T1 ₹{lv['target_1']} · T2 ₹{lv['target_2']}"
                        + (f" · ATR ₹{lv['atr']}" if lv['atr'] else "")
                    )
            except Exception as exc:
                logger.debug(f"[india_trade_loop] {signal.symbol} level calc failed: {exc}")

        # Step 4d: pre-trade research gate — run Tavily web search + LLM verdict
        # concurrently for all BUY signals in the pool, then remove any vetoed ones.
        # 8-second hard timeout per symbol; failures default to ALLOW so research
        # never blocks trade execution.
        buy_signals = [s for s in level_pool if s.action == "BUY"]
        if buy_signals:
            from engine.pre_trade_research import run_pre_trade_research
            research_tasks = [
                asyncio.wait_for(
                    run_pre_trade_research(
                        symbol=sig.symbol,
                        action=sig.action,
                        score=sig.final_score,
                        regime=getattr(sig, "regime", "") or (getattr(sig, "hub_subscores", {}) or {}).get("regime", ""),
                        entry=sig.entry_price,
                        stop=sig.stop_loss or 0.0,
                        t1=sig.take_profit or 0.0,
                        fund_grade=str(getattr(sig, "fundamental_grade", "") or ""),
                    ),
                    timeout=8.0,
                )
                for sig in buy_signals
            ]
            research_results = await asyncio.gather(*research_tasks, return_exceptions=True)
            vetoed_syms: list[str] = []
            for sig, res in zip(buy_signals, research_results):
                if isinstance(res, Exception):
                    logger.debug(f"[india_trade_loop] pre_trade research error for {sig.symbol}: {res}")
                    continue
                if res.get("veto"):
                    vetoed_syms.append(sig.symbol)
                    logger.warning(
                        f"[india_trade_loop] PRE-TRADE VETO {sig.symbol}: {res['veto_reason']}"
                    )
                    await SimLogger.log_analysis_cycle(
                        session, sig.symbol, sig,
                        rejected=True,
                        reject_reason=f"[pre-trade research] {res['veto_reason']}",
                    )
                else:
                    note = res.get("research_note", "")
                    if note:
                        sig.reasoning_points.append(f"[web] {note[:300]}")
            if vetoed_syms:
                level_pool = [s for s in level_pool if s.symbol not in vetoed_syms]
                logger.info(
                    f"[india_trade_loop] {len(vetoed_syms)} signal(s) vetoed by research gate: "
                    + ", ".join(vetoed_syms)
                )

        # Step 5: current wallet state
        summary        = await VirtualWallet.get_summary(session)
        balance        = summary["balance"]
        # Exclude intraday MIS positions from the positional CNC count so that
        # open MIS trades don't consume slots from the delivery position budget.
        pos_result     = await session.execute(
            select(OpenPosition).where(OpenPosition.product != "MIS")
        )
        open_positions = list(pos_result.scalars().all())

        # Step 5b: Portfolio-level cognitive cycle — one top-down "veteran trader"
        # read of the whole book + market before any per-candidate decision. Logs
        # its stance every cycle; only gates trading when NOT in shadow mode.
        try:
            if getattr(settings, "AGENT_PORTFOLIO_BRAIN_ENABLED", False):
                from engine.agent.portfolio_brain import portfolio_cognitive_cycle, log_thesis
                _start    = summary.get("equity", 0) - summary.get("realised_pnl", 0) - summary.get("unrealised_pnl", 0)
                _deployed = max(0.0, summary["equity"] - balance)
                _vix = 0.0
                try:
                    _vix = float((PRICE_CACHE.get("^INDIAVIX", {}) or {}).get("price", 0) or 0)
                except Exception:
                    pass
                _actionable = [s for s in level_pool]
                _buys  = sum(1 for s in _actionable if s.action == "BUY")
                _sells = sum(1 for s in _actionable if s.action == "SELL")
                _top   = ", ".join(f"{s.symbol.replace('.NS','')}:{round(s.final_score)}"
                                   for s in _actionable[:5])
                _brain_ctx = {
                    "regime": (getattr(level_pool[0], "regime", "") if level_pool else "") or "UNKNOWN",
                    "vix": round(_vix, 1) or None, "macro_bias": None, "mood": None,
                    "nifty_5d_ret": None,
                    "equity": round(summary["equity"]), "cash": round(balance),
                    "deployed_pct": round(100 * _deployed / max(summary["equity"], 1)),
                    "open_positions": len(open_positions),
                    "max_positions": int(getattr(settings, "AGENT_MAX_POSITIONS", 15)),
                    "day_roi": round(summary.get("roi_percent", 0.0), 2),
                    "unrealised": round(summary.get("unrealised_pnl", 0.0)),
                    "worst_open": None, "best_open": None,
                    "n_candidates": len(_actionable), "n_buy": _buys, "n_sell": _sells,
                    "top_candidates": _top,
                }
                _stance = await portfolio_cognitive_cycle(_brain_ctx)
                if _stance:
                    _enforce = not getattr(settings, "AGENT_PORTFOLIO_BRAIN_SHADOW", True)
                    await log_thesis(_stance, _brain_ctx, enforced=_enforce)
                    logger.info(
                        f"[portfolio_brain] stance={_stance['stance']} halt={_stance['halt_new']} "
                        f"cap={_stance['max_new_entries']} mult={_stance['size_multiplier']} "
                        f"{'ENFORCED' if _enforce else 'shadow'} | {_stance['thesis'][:80]}"
                    )
                    if _enforce:
                        if _stance["halt_new"]:
                            max_new = 0
                        elif _stance["max_new_entries"] is not None:
                            max_new = min(max_new, int(_stance["max_new_entries"]))
        except Exception as _exc:
            logger.debug(f"[portfolio_brain] skipped: {_exc}")

        # Step 6: work down the ranked pool, opening until the risk budget / cash
        # buffer (inside validate_signal) or the per-cycle cap stops us.
        opened = 0
        news_opened = 0
        _news_cap = (
            int(getattr(settings, "NEWS_OVERRIDE_MAX_PER_CYCLE", 2))
            if getattr(settings, "NEWS_REGIME_OVERRIDE_ENABLED", False) else 0
        )
        for signal in level_pool:
            _override = bool(getattr(signal, "news_override", False)) and signal.action == "BUY"
            # Normal budget/halt cap reached (max_new can be 0 under a
            # portfolio-brain halt). A strong-news override may still open, up to
            # its own small per-cycle cap; everything else is done for this cycle.
            if opened >= max_new and not (_override and news_opened < _news_cap):
                continue

            # ── Phase 9 Quality Gate — mirrors the exact filters proven in backtest ──
            # All 4 checks must pass for a BUY to proceed. SELLs get their own
            # market-regime check below (Gate S1) — the 5-state engine was computed
            # per-cycle above but previously never consulted for shorts, which let
            # the short book fire regardless of the broader market's direction.
            if signal.action == "BUY":
                if _override:
                    logger.info(
                        f"[news-override] {signal.symbol} — strong news "
                        f"(news_score={float((getattr(signal,'hub_subscores',{}) or {}).get('news',0)):+.0f}) "
                        f"bypassing regime/technical entry gates "
                        f"[{news_opened + 1}/{_news_cap} this cycle]"
                    )
                # Gate 1: EMA200 absolute bear-market gate
                if not _p9ctx["nifty_ema200_ok"] and not _override:
                    logger.info(
                        f"[phase9] BLOCK {signal.symbol} — Nifty below EMA200 "
                        f"(structural bear market)"
                    )
                    await SimLogger.log_analysis_cycle(
                        session, signal.symbol, signal, rejected=True,
                        reject_reason="[phase9] Nifty below EMA200 — structural bear gate",
                    )
                    continue

                # Gate 2: 5-state regime engine — only STRONG_BULL / MODERATE_BULL
                if not _p9ctx["regime_allows_buy"] and not _override:
                    _rstate = _p9ctx.get("regime_state", "?")
                    logger.info(
                        f"[phase9] BLOCK {signal.symbol} — regime={_rstate} "
                        f"(requires STRONG_BULL or MODERATE_BULL)"
                    )
                    await SimLogger.log_analysis_cycle(
                        session, signal.symbol, signal, rejected=True,
                        reject_reason=f"[phase9] regime={_rstate} — not bull",
                    )
                    continue

                # Gate 3: Relative Strength filter — stock must not lag Nifty by >3%
                _stock_roc20 = getattr(signal, "phase9_roc20", 0.0)
                _nifty_roc20 = _p9ctx["nifty_roc20"]
                if _stock_roc20 < _nifty_roc20 - 3.0 and not _override:
                    logger.info(
                        f"[phase9] BLOCK {signal.symbol} — RS filter: "
                        f"stock_roc20={_stock_roc20:+.2f}% < nifty_roc20={_nifty_roc20:+.2f}% - 3%"
                    )
                    await SimLogger.log_analysis_cycle(
                        session, signal.symbol, signal, rejected=True,
                        reject_reason=(
                            f"[phase9] RS filter: stock {_stock_roc20:+.2f}% "
                            f"vs Nifty {_nifty_roc20:+.2f}% (lag >{_stock_roc20 - _nifty_roc20:.1f}%)"
                        ),
                    )
                    continue

                # Gate 4: EMA20 slope — must be rising (today > 5 bars ago)
                if not getattr(signal, "phase9_ema20_slope_ok", True) and not _override:
                    logger.info(
                        f"[phase9] BLOCK {signal.symbol} — EMA20 slope flat/declining "
                        f"(pullback may be reversal)"
                    )
                    await SimLogger.log_analysis_cycle(
                        session, signal.symbol, signal, rejected=True,
                        reject_reason="[phase9] EMA20 slope declining — trend not accelerating",
                    )
                    continue

                # Gate 5: PULLBACK_LONG pattern — prev bar touched EMA20, last bar
                # bounced above EMA20 with vol spike, RSI 50-70, ADX>20, EMA stack
                # (20>50, 50>=200×1.01), shallow touch (prev low within 3%), quiet
                # prev bar, ADX not collapsing. Mirrors PullbackTrendLong.evaluate().
                if not getattr(signal, "phase9_pullback_ok", False) and not _override:
                    logger.info(
                        f"[phase9] BLOCK {signal.symbol} — pullback pattern not confirmed "
                        f"(needs EMA20 touch + bounce + vol spike + RSI/ADX/EMA stack)"
                    )
                    await SimLogger.log_analysis_cycle(
                        session, signal.symbol, signal, rejected=True,
                        reject_reason="[phase9] PULLBACK_LONG pattern not confirmed — no valid setup",
                    )
                    continue

            elif signal.action == "SELL":
                # Gate S1: 5-state regime engine — block new shorts when the market
                # itself is STRONG_BULL. Per-stock BEAR_TRENDING/RANGE labels (from
                # analyzer._classify_regime) only look at that stock's own EMA/ADX —
                # they have no idea whether Nifty is rallying underneath them. This
                # is the gate that was missing.
                if not _p9ctx.get("regime_allows_sell", True):
                    _rstate = _p9ctx.get("regime_state", "?")
                    logger.info(
                        f"[phase9] BLOCK {signal.symbol} — regime={_rstate} "
                        f"(STRONG_BULL blocks new shorts)"
                    )
                    await SimLogger.log_analysis_cycle(
                        session, signal.symbol, signal, rejected=True,
                        reject_reason=f"[phase9] regime={_rstate} — STRONG_BULL blocks shorts",
                    )
                    continue

            validated, reason = await validate_signal(
                signal, balance, open_positions, session
            )
            await SimLogger.log_analysis_cycle(
                session, signal.symbol, signal,
                rejected=not validated,
                reject_reason=reason if not validated else None,
            )
            # Budget-full / ceiling rejections mean no further candidate will fit
            # either — stop the cycle early rather than logging 20 identical fails.
            if not validated:
                if "budget" in reason.lower() or "ceiling" in reason.lower() or "cash buffer" in reason.lower():
                    logger.info(f"[india_trade_loop] capital fully deployed — {reason}")
                    break
                continue

            # ── Level-1/2/3 LLM reasoning gate on the LIVE trade path ──────────
            # Runs only on validated signals about to be opened (≤ max_new/cycle, so
            # LLM cost is bounded). Builds lightweight candidate/decision shims from
            # the signal so the gate + its 7-factor verdict logging work here too.
            # SHADOW mode only logs (trade proceeds); otherwise it can SKIP or blend.
            try:
                from types import SimpleNamespace as _NS
                from engine.agent.decision_engine import apply_reasoning_gate
                _hub  = getattr(signal, "hub_subscores", None) or {}
                _strat = getattr(signal, "strategy", "HUB_SIGNAL")
                _rgm   = getattr(signal, "regime", "") or _hub.get("regime", "")
                _tgt   = getattr(signal, "target_2", None) or signal.take_profit or 0.0
                _rr    = getattr(signal, "risk_reward_ratio", 0.0)
                # Technical/chart brief — built from recent candles (reused from the
                # level-calc step if available), so the LLM reasons over the chart + ML.
                _brief = ""
                try:
                    if getattr(settings, "AGENT_CHART_BRIEF_ENABLED", True):
                        from engine.agent.chart_brief import build_chart_brief
                        _bc = await get_latest_candles(signal.symbol, "1d", 120, session)
                        if _bc and len(_bc) >= 20:
                            _bc = sorted(_bc, key=lambda c: c.timestamp)
                            _bdf = pd.DataFrame([{"open": c.open, "high": c.high, "low": c.low,
                                "close": c.close, "volume": c.volume} for c in _bc])
                            _brief = build_chart_brief(signal.symbol, _bdf)
                except Exception as _bx:
                    logger.debug(f"[india_trade_loop] chart_brief skipped {signal.symbol}: {_bx}")
                _cand = _NS(symbol=signal.symbol, side=signal.action, strategy=_strat,
                            entry=signal.entry_price, stop=signal.stop_loss or 0.0,
                            target=_tgt, risk_reward=_rr, hub_subscores=_hub, reasons=[],
                            chart_brief=_brief)
                _dec = _NS(action=signal.action, regime=_rgm, strategy=_strat,
                           master_score=getattr(signal, "final_score", None) or signal.confidence,
                           confidence=int(signal.confidence or 0),
                           entry=signal.entry_price, stop=signal.stop_loss or 0.0,
                           target=_tgt, risk_reward=_rr, reasons=[],
                           confidence_factors={k: _hub.get(k) for k in
                               ("technical", "news", "sector", "macro",
                                "earnings", "fundamental", "options")})
                _kept, _llm_reject = await apply_reasoning_gate(signal.symbol, _cand, _dec)
                if _kept is None:
                    logger.info(f"[india_trade_loop] LLM-reason SKIP {signal.symbol}: {_llm_reject}")
                    await SimLogger.log_analysis_cycle(
                        session, signal.symbol, signal,
                        rejected=True, reject_reason=f"[llm_reason] {_llm_reject}",
                    )
                    continue
                signal.confidence = _kept.confidence  # propagate blend (no-op in shadow)
            except Exception as _exc:
                # Fail-CLOSED: a broken/crashed reasoning gate must reject the
                # candidate, not silently let it trade unreviewed. This is the
                # same class of bug that let 8 unreviewed SUNTV entries through
                # on 2026-07-14 -- an AttributeError inside apply_reasoning_gate
                # was swallowed here and execution fell through regardless.
                logger.warning(f"[india_trade_loop] reasoning gate FAILED (fail-closed) {signal.symbol}: {_exc}")
                await SimLogger.log_analysis_cycle(
                    session, signal.symbol, signal,
                    rejected=True, reject_reason=f"[llm_reason_error] {_exc}",
                )
                continue

            pos_size = calculate_position_size(signal, balance)
            # SELL = equity short → must be intraday MIS (NSE rule); BUY = CNC delivery
            _product = "MIS" if signal.action == "SELL" else "CNC"
            try:
                import datetime as _dt
                _score_age = (_dt.datetime.utcnow() - _dt.datetime.fromisoformat(signal.hub_subscores["scored_at"])).total_seconds() if signal.hub_subscores and signal.hub_subscores.get("scored_at") else 0.0
                from crawler.live_prices import PRICE_CACHE
                _lp = PRICE_CACHE.get(signal.symbol)
                _price_age = _lp.get("age_seconds", 0.0) if _lp else 0.0
                logger.info(f"[hub_instrumentation] PATH: B_live_loop | SYMBOL: {signal.symbol} | SCORE_AGE: {_score_age}s | PRICE_AGE: {_price_age}s")
            except Exception: pass

            # Central execution gate — confidence_source=CALCULATED since this
            # signal already passed validate_signal + apply_reasoning_gate above;
            # event_directness=NOT_APPLICABLE (technical/hub scan, not a news
            # cascade). position_size_hint preserves the sizing already computed.
            from engine.decision_router import TradeIntent, ConfidenceSource, EventDirectness, StrategyFamily, execute_trade_intent, RoutingOutcome
            _intent = TradeIntent(
                strategy=_strat, symbol=signal.symbol, action=signal.action, instrument_type="EQUITY",
                entry_price=signal.entry_price, stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                confidence=signal.confidence, confidence_source=ConfidenceSource.CALCULATED,
                strategy_family=StrategyFamily.TECHNICAL,
                event_directness=EventDirectness.NOT_APPLICABLE, position_size_hint=pos_size, product=_product,
            )
            _gate_result = await execute_trade_intent(_intent, session)
            if _gate_result.outcome not in (RoutingOutcome.EXECUTED_PAPER, RoutingOutcome.EXECUTED_LIVE):
                logger.info(f"[india_trade_loop] gate blocked {signal.symbol}: {_gate_result.outcome.value} — {_gate_result.reason}")
                continue
            balance -= pos_size["usd_value"]
            opened  += 1
            # Commit immediately after each trade so the position is persisted
            # even if the task hits SoftTimeLimitExceeded while processing
            # subsequent signals (with 19+ signals + Ollama fallback each cycle
            # can easily exceed the 300s SoftTimeLimit).
            await session.commit()
            pos_result     = await session.execute(select(OpenPosition))
            open_positions = list(pos_result.scalars().all())

            from db.models import PaperTrade as _PaperTrade
            _trade_id = (_gate_result.metadata or {}).get("trade_id")
            trade = await session.get(_PaperTrade, _trade_id) if _trade_id else None
            if trade is not None:
                explanation  = await generate_trade_explanation(signal)
                notification = format_paper_trade_notification(trade, explanation)
                logger.info(notification)

            from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, TradeEntryPayload
            await publish(AlertEvent(
                category=AlertCategory.TRADE, action=AlertAction.ENTRY, severity=Severity.SUCCESS,
                symbol=signal.symbol, trade_id=_trade_id,
                payload=TradeEntryPayload(decision=signal, qty=pos_size.get("units", 0)),
            ))

        logger.info(f"[india_trade_loop] opened {opened} new position(s) this cycle")

        # Step 9: persist daily performance snapshot
        await VirtualWallet.take_daily_snapshot(session)
        final = await VirtualWallet.get_summary(session)
        logger.info(
            f"[india_trade_loop] cycle done — "
            f"balance=₹{final['balance']:.0f}  "
            f"equity=₹{final['equity']:.0f}  "
            f"roi={final['roi_percent']:+.2f}%  "
            f"open={len(open_positions)}"
        )
        await session.commit()

        # ── Mirror the trade ledger to the spreadsheet journal (best-effort) ──
        # Idempotent: appends new trades, updates ones that just closed. Never
        # raises into the trade loop — journal failures must not affect trading.
        try:
            from integrations.sheet_logger import sync_journal
            await sync_journal(session)
        except Exception as exc:
            logger.warning(f"[india_trade_loop] journal sync skipped: {exc}")


@celery_app.task(name="tasks.india_trade_loop")
def india_trade_loop():
    """Full Indian paper-trading cycle: update positions → signals → risk → open trades.

    Runs every 60 s during NSE hours plus 30 min after close.
    PAPER TRADING ONLY — virtual currency, no real money involved.
    """
    logger.info("[india_trade_loop] Starting cycle")
    _run_async(_india_trade_loop())


# ── 6b. Fast stop-loss check — every 5 s ─────────────────────────────────────
#
# Fetches current LTP directly from Kite REST API (fresh every 5 s) and closes
# any open position whose stop-loss or take-profit is hit WITHOUT waiting for
# the 60 s trade loop.  Does NOT score, does NOT open new trades — pure exit.
# Uses the same close_paper_trade() as the main loop so P&L, wallet, and logs
# are all updated identically.  Skips the tick entirely if Kite is unavailable.

async def _fast_sl_check() -> None:
    # ── Dead-man's-switch heartbeat (2026-08-21) ─────────────────────────────
    # On 21 Aug this loop ran once in a full session and NOTHING surfaced it:
    # the dispatches expired in a saturated queue, and an expired Celery task
    # logs nothing, raises nothing and never reaches a worker. All four services
    # showed healthy. Only counting rows in the DB exposed it.
    #
    # A key with a short TTL inverts that: absence is the alarm. If this loop
    # stops, the key is simply gone within 20s, which a monitor can see.
    try:
        import time as _hb_time

        from utils.cache import get_redis
        await get_redis().set("exit_worker:heartbeat", str(_hb_time.time()), ex=20)
    except Exception as _hb_exc:
        # Logged, not silent: a heartbeat that fails quietly is exactly the
        # blind spot it exists to remove.
        logger.warning(f"[fast_sl] heartbeat write failed: {type(_hb_exc).__name__}: {_hb_exc}")

    from db.models import OpenPosition, TradeDirection
    from paper_trading.trade_simulator import close_paper_trade
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from tasks._db import celery_session

    if not _is_india_trading_window():
        return

    async with celery_session() as session:
        result = await session.execute(
            select(OpenPosition).options(selectinload(OpenPosition.trade))
        )
        positions = list(result.scalars().all())
        if not positions:
            return

        # ── Fetch fresh LTP directly from Kite REST API ───────────────────────
        # PRICE_CACHE is an in-memory dict in the main/FastAPI process — Celery
        # worker processes have a stale copy.  Kite's LTP endpoint returns the
        # current market price in <100 ms for any number of symbols.
        symbols = [p.symbol for p in positions]
        live_px: dict[str, float] = {}
        if symbols:
            try:
                from crawler.zerodha_market import get_live_prices
                quotes = await get_live_prices(symbols, exit_bucket=True)   # D6: reserved exit quota
                for sym, q in (quotes or {}).items():
                    px = q.get("price") or q.get("last_price")
                    if px and px > 0:
                        live_px[sym] = float(px)
            except Exception as exc:
                logger.debug(f"[fast_sl] Kite LTP fetch failed: {exc}")

            # yfinance backstop — Kite LTP can return {} (disconnect / 403 / token
            # expiry) EXACTLY when volatility spikes (e.g. a geopolitical shock).
            # Without a fallback the fast SL/TP loop goes blind mid-crash and
            # leaves stops unenforced. Recover any symbol Kite couldn't price via
            # a direct yfinance quote so live protection survives a broker hiccup.
            missing = [s for s in symbols if s not in live_px]
            if missing:
                try:
                    from crawler.live_prices import yfinance_ltp_batch
                    yf_px = await yfinance_ltp_batch(missing)
                    live_px.update(yf_px)
                    if yf_px:
                        logger.info(
                            f"[fast_sl] Kite LTP missing {len(missing)} symbol(s) — "
                            f"recovered {len(yf_px)} via yfinance backstop"
                        )
                except Exception as exc:
                    logger.debug(f"[fast_sl] yfinance backstop failed: {exc}")

            if not live_px:
                logger.warning(
                    "[fast_sl] no live price from Kite OR yfinance for "
                    f"{len(symbols)} open position(s) — stops UNENFORCED this tick"
                )
                return

        # Heartbeat (throttled to ~1/min) so operators can confirm the 5 s loop
        # is actually alive and watching positions — it is otherwise silent
        # unless it closes a trade, which hid whether it was running at all.
        global _fast_sl_heartbeat_ts
        import time as _time
        if _time.time() - _fast_sl_heartbeat_ts >= 60:
            _fast_sl_heartbeat_ts = _time.time()
            logger.info(
                f"[fast_sl] alive — watching {len(positions)} position(s), "
                f"{len(live_px)} priced live"
            )

        closed: list[dict] = []
        for pos in positions:
            price = live_px.get(pos.symbol, 0.0)
            if price <= 0 or not pos.stop_loss:
                continue

            # Sanity check: if price dropped >40% from entry, it's almost
            # certainly a corporate action (split/bonus/demerger) or a bad tick.
            # Trigger the corporate action handler immediately instead of a false stop.
            entry_ref = float(pos.entry_price or 0)
            if entry_ref > 0 and (entry_ref - price) / entry_ref > 0.40:
                logger.warning(
                    f"[fast_sl] {pos.symbol} price ₹{price:.2f} dropped "
                    f">40% from entry ₹{entry_ref:.2f} — triggering corp action check"
                )
                try:
                    from crawler.corporate_actions import check_and_handle_corporate_actions
                    await check_and_handle_corporate_actions(session)
                    await session.commit()
                except Exception as _ca_exc:
                    logger.warning(f"[fast_sl] corp action handler failed: {_ca_exc}")
                continue  # Never fire a stop on a suspected corp-action price

            # Sanity check: if price spiked >3× above entry, the data feed is
            # returning a pre-split (unadjusted) price for a recently split stock.
            # A genuine 3× intraday move is essentially impossible; skip this tick.
            if entry_ref > 0 and price > entry_ref * 3.0:
                logger.warning(
                    f"[fast_sl] {pos.symbol} price ₹{price:.2f} is >3× entry "
                    f"₹{entry_ref:.2f} — likely stale pre-split feed, skipping"
                )
                continue

            is_buy = pos.direction == TradeDirection.BUY

            # ── Advanced exit management (2026-08-21) ────────────────────────
            # Wrapped whole. This is the 5s loop that protects every open
            # position; a fault in trailing or exhaustion must NEVER stop the
            # fixed stop-loss below from firing. On any error we log and fall
            # through to the original logic untouched.
            #
            # Placed BEFORE sl_hit is computed so a tightened trailing stop is
            # evaluated on this same tick rather than the next one.
            _adv_reason = None

            # Is this position even ALLOWED to exit right now? A SWING trade
            # inside its minimum-hold window has fast exits deliberately
            # suppressed (see the bypass below), so an exit signal computed for
            # it can never be acted on.
            #
            # Measured 2026-08-24: RUBICON.NS is SWING with swing_min_hold two
            # days out. Exhaustion fired on it 358 times in one session — every
            # 5 seconds — and closed nothing, because the bypass reset sl_hit
            # each time. That is not just log noise: each detection cost a 5m
            # candle query plus an ATR computation on the exit worker, on the
            # 5-second loop, for a decision that was structurally impossible.
            _exit_blocked = False
            if pos.trade_style == "SWING" and pos.swing_min_hold:
                from datetime import datetime as _dt2
                from zoneinfo import ZoneInfo as _ZI2
                if _dt2.now(_ZI2("Asia/Kolkata")).replace(tzinfo=None) < pos.swing_min_hold:
                    _exit_blocked = True

            # V2 profit-management gate (Phase 25). CONTROL leaves this True for
            # every position at every age, so the branches below are byte-for-byte
            # the old behaviour. Under V2 it is False until the position has had
            # V2_MIN_HOLD_MINUTES to work, which defers EXHAUSTION, the T1/T2
            # partials, TAKE_PROFIT and the trailing ratchet -- and NOTHING else.
            # The fixed stop-loss below is untouched in both modes.
            from engine.exit_policy import profit_management_allowed as _pm_allowed
            _pm_ok = _pm_allowed(getattr(pos, "opened_at", None))

            try:
                from paper_trading.position_tracker import update_trailing_stop
                from utils.config import settings as _s

                _c5 = None
                _atr = 0.0
                if bool(getattr(_s, "ENABLE_TRAILING_STOP", True)) or \
                   bool(getattr(_s, "ENABLE_EXHAUSTION_EXIT", True)):
                    from crawler.price_feed import get_latest_candles
                    _rows = await get_latest_candles(pos.symbol, "5m", 20, session)
                    if _rows and len(_rows) >= 5:
                        import pandas as _pd
                        _c5 = _pd.DataFrame([{ "open": r.open, "high": r.high, "low": r.low,
                                               "close": r.close, "volume": r.volume}
                                             for r in reversed(_rows)]).astype(float)
                        _tr = (_c5["high"] - _c5["low"]).tail(14)
                        _atr = float(_tr.mean()) if len(_tr) else 0.0

                # 1. Ratchet the stop. Mutates pos.stop_loss; sl_hit below sees it.
                if bool(getattr(_s, "ENABLE_TRAILING_STOP", True)):
                    # ratchet=_pm_ok: under V2, before the minimum hold, the peak
                    # is still tracked but the stop is not moved. The ratchet and
                    # the hard stop share pos.stop_loss, so trailing to breakeven
                    # at +2% is a profit-management exit that would report itself
                    # as STOP_LOSS. Deferring the layer means deferring the move.
                    _moved, _note = update_trailing_stop(pos, price, _atr, ratchet=_pm_ok)
                    if _moved:
                        await session.commit()
                        logger.info(f"[fast_sl] TRAILING {pos.symbol}: {_note}")

                # 2. Sell into weakness before the stop is reached.
                # Skipped entirely while the position cannot exit — see
                # _exit_blocked above. Trailing still runs: tightening a stop is
                # not an exit, and the ratcheted level applies the moment the
                # hold window ends.
                if _adv_reason is None and _c5 is not None and not _exit_blocked and _pm_ok and \
                   bool(getattr(_s, "ENABLE_EXHAUSTION_EXIT", True)) and is_buy:
                    from engine.indicators import detect_exhaustion
                    _ex, _why = detect_exhaustion(_c5, _atr)
                    if _ex:
                        _adv_reason = "EXHAUSTION"
                        # Instrumentation only (2026-08-25, Phase 1B task D).
                        # Two BHEL positions were opened and exhaustion-exited
                        # five seconds apart with MFE = MAE = 0.000, which is
                        # consistent with entry and exit reading the same closed
                        # 5m bar — but only consistent, not proven: the stored
                        # 5m series is rebuilt from 1m by the resampler, so a
                        # replay cannot recover the frame the live check held.
                        # detect_exhaustion drops the forming bar, so the bar it
                        # actually consumed is _c5[-2]. Log it, and the entry, so
                        # the next occurrence is decidable from the record rather
                        # than inferred. No behaviour changes here.
                        try:
                            _bars = list(getattr(_c5, "index", []) or [])
                            _consumed = _c5["timestamp"].iloc[-2] if "timestamp" in getattr(_c5, "columns", []) and len(_c5) >= 2 else None
                            _forming = _c5["timestamp"].iloc[-1] if "timestamp" in getattr(_c5, "columns", []) and len(_c5) >= 1 else None
                            logger.info(
                                f"[fast_sl] EXHAUSTION_AUDIT {pos.symbol} "
                                f"entry_at={pos.opened_at} entry_px={pos.entry_price} "
                                f"check_at={datetime.datetime.utcnow().isoformat()} "
                                f"consumed_bar={_consumed} forming_bar={_forming} "
                                f"bars={len(_c5) if _c5 is not None else 0} atr={_atr} "
                                f"reason={_why}"
                            )
                        except Exception as _audit_exc:
                            logger.debug(f"[fast_sl] EXHAUSTION_AUDIT logging failed: {_audit_exc}")
                        logger.info(f"[fast_sl] EXHAUSTION {pos.symbol}: {_why}")

                # 3. T2 — book another 30% at +3%, then run the rest.
                if _adv_reason is None and _pm_ok and bool(getattr(_s, "ENABLE_PARTIAL_BOOKING_T2", True)) \
                   and int(getattr(pos, "exit_tier", 1) or 1) == 2 and pos.entry_price > 0:
                    _t2_pct = float(getattr(_s, "PARTIAL_BOOKING_T2_PCT", 0.030))
                    _t2 = pos.entry_price * (1 + _t2_pct) if is_buy else pos.entry_price * (1 - _t2_pct)
                    if (is_buy and price >= _t2) or ((not is_buy) and price <= _t2):
                        from paper_trading.trade_simulator import scale_out_paper_trade
                        _p2 = await scale_out_paper_trade(pos, 0.3, price, "T2_HIT", session)
                        pos.exit_tier = 3
                        pos.stop_loss = (max(pos.stop_loss, pos.entry_price) if is_buy
                                         else min(pos.stop_loss, pos.entry_price))
                        await session.commit()
                        logger.info(
                            f"[fast_sl] PARTIAL_T2 {pos.symbol} booked 30% @ ₹{price:.2f} "
                            f"pnl=₹{_p2:,.2f} — runner now trailing"
                        )
                        continue
            except Exception as _adv_exc:
                # Deliberately swallowed: the fixed stop-loss below is the
                # guarantee and must run regardless.
                logger.warning(f"[fast_sl] advanced exit checks failed for {pos.symbol}: {_adv_exc}")
                _adv_reason = None

            sl_hit = (is_buy and price <= pos.stop_loss) or (
                not is_buy and price >= pos.stop_loss
            )
            if _adv_reason:
                sl_hit = True          # route through the normal close path
            
            # Bypass fast SL for swing trades during their minimum hold period
            if sl_hit and pos.trade_style == "SWING" and pos.swing_min_hold:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                _IST = ZoneInfo("Asia/Kolkata")
                now_ist = datetime.now(_IST).replace(tzinfo=None)
                if now_ist < pos.swing_min_hold:
                    sl_hit = False
            
            if not sl_hit:
                # Also check take_profit for fast wins.
                # Under V2, before the minimum hold, neither the T1 partial nor a
                # full TAKE_PROFIT may fire -- Phase 24 measured the signalled
                # subset still net-negative at 60 minutes and positive by 120.
                if pos.take_profit and _pm_ok:
                    tp_hit = (is_buy and price >= pos.take_profit) or (
                        not is_buy and price <= pos.take_profit
                    )
                    if not tp_hit:
                        continue
                        
                    # ── Smart Trailing & Post-Entry Management ──
                    trade_obj = getattr(pos, "trade", None)
                    if not trade_obj:
                        from db.models import PaperTrade
                        trade_obj = await session.get(PaperTrade, pos.trade_id)
                        
                    if trade_obj and abs(pos.size_units - trade_obj.size_units) < 1e-4:
                        try:
                            from paper_trading.trade_simulator import scale_out_paper_trade
                            partial_pnl = await scale_out_paper_trade(pos, 0.5, price, "T1_HIT", session)
                            # Move SL to cost-to-cost
                            pos.stop_loss = max(pos.stop_loss, pos.entry_price) if is_buy else min(pos.stop_loss, pos.entry_price)
                            pos.exit_tier = 2      # T2 (30% at +3%) is next
                            if pos.highest_high is None and is_buy:
                                pos.highest_high = max(price, pos.entry_price)
                            # Let trailing logic in update_positions_with_current_prices handle the rest
                            pos.take_profit = 0.0  
                            await session.commit()
                            
                            # Alert
                            
                            logger.info(f"[fast_sl] {pos.symbol} hit T1. Booked 50% (PnL: ₹{partial_pnl:.2f}). SL moved to entry ₹{pos.entry_price:.2f}.")
                            
                        except Exception as exc:
                            
                            logger.warning(f"[fast_sl] scale_out failed for {pos.symbol}: {exc}")
                            await session.rollback()
                        continue
                    else:
                        reason = "TAKE_PROFIT"
                else:
                    continue
            else:
                reason = _adv_reason or "STOP_LOSS"

            try:
                trade = await close_paper_trade(pos, price, reason, session)
                await session.commit()
                closed.append({
                    "trade_id":    trade.id,
                    "symbol":      trade.symbol,
                    "direction":   pos.direction.value,
                    "entry_price": trade.entry_price,
                    "exit_price":  price,
                    "qty":         trade.size_units,
                    "pnl":         trade.pnl,
                    "reason":      reason,
                })
                logger.info(
                    f"[fast_sl] {trade.symbol} @ ₹{price:.2f} → {reason} "
                    f"pnl=₹{trade.pnl:,.2f}"
                )
            except Exception as exc:
                logger.warning(f"[fast_sl] close failed for {pos.symbol}: {exc}")
                await session.rollback()

        # Telegram alerts for live exits — deduplicated by trade_id (now via
        # integrations/alerts/router.py's shared Redis dedup, replacing the
        # in-process _exit_alerted_trade_ids set).
        if closed:
            try:
                from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, TradeExitPayload
                for c in closed:
                    await publish(AlertEvent(
                        category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
                        symbol=c["symbol"], trade_id=c.get("trade_id"),
                        payload=TradeExitPayload(
                            symbol=c["symbol"], side=c["direction"], entry=c["entry_price"],
                            exit_price=c["exit_price"], qty=c["qty"], pnl=c["pnl"], reason=c["reason"],
                        ),
                    ))
            except Exception as exc:
                logger.debug(f"[fast_sl] Telegram notify failed: {exc}")


@celery_app.task(name="tasks.fast_sl_check")
def fast_sl_check():
    """Stop-loss / take-profit check on live PRICE_CACHE ticks every 5 s."""
    _run_async(_fast_sl_check())


# ── 6a. Fast market-shock guard — every 30 s ─────────────────────────────────

async def _market_shock_guard() -> None:
    """Tighten/flatten open longs on a sudden index or high-severity news shock.

    Reacts in ~30 s instead of waiting for the 15-min hub cycle. Gated OFF by
    default (settings.ENABLE_SHOCK_GUARD).
    """
    from utils.config import settings
    if not _is_india_trading_window() or not settings.ENABLE_SHOCK_GUARD:
        return

    from engine.agent.shock_guard import run_shock_guard
    from tasks._db import celery_session

    async with celery_session() as session:
        summary = await run_shock_guard(session)

    if not summary or not (summary.get("closed") or summary.get("tightened")):
        return

    try:
        lines = [f"⚠️ MARKET SHOCK — {summary['level']}"]
        if summary.get("reason"):
            lines.append(summary["reason"])
        for c in summary.get("closed", []):
            lines.append(f"FLATTEN {c['symbol']} @ ₹{c['price']:.2f} (pnl ₹{c['pnl']:,.0f})")
        if summary.get("tightened"):
            lines.append(f"Tightened stops on {len(summary['tightened'])} long(s)")
        from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, RawTextPayload
        await publish(AlertEvent(
            category=AlertCategory.NEWS_EVENT, action=AlertAction.ALERT, severity=Severity.WARNING,
            payload=RawTextPayload(text="\n".join(lines)),
        ))
    except Exception as exc:
        logger.debug(f"[shock] telegram notify failed: {exc}")


@celery_app.task(name="tasks.market_shock_guard")
def market_shock_guard():
    """Fast market-shock guard — tighten/flatten longs on an index/news shock."""
    _run_async(_market_shock_guard())


# ── 6b. High-impact news alert — every 5 min (incl. after-hours) ─────────────

async def _market_news_alert() -> None:
    """Push a Telegram alert when a crash-capable headline lands.

    Fixes the gap where a market-moving event (e.g. the Iran ceasefire news) was
    captured but silently buried in the chronological /news feed with no alert.
    Runs regardless of market hours — such news often breaks after close. A DB
    watermark (last_news_alert_at) dedupes so each headline alerts at most once.
    """
    from utils.config import settings
    if not settings.ENABLE_NEWS_ALERTS:
        return

    from datetime import datetime, timedelta
    from sqlalchemy import select
    from db.models import NewsItem
    from utils.runtime_config import RuntimeConfig
    from engine.news_impact import is_high_impact_news
    from tasks._db import celery_session

    async with celery_session() as session:
        rc  = await RuntimeConfig.load(session)
        raw = rc._get("last_news_alert_at", "")
        try:
            since = datetime.fromisoformat(raw) if raw else datetime.utcnow() - timedelta(minutes=10)
        except (ValueError, TypeError):
            since = datetime.utcnow() - timedelta(minutes=10)

        rows = (await session.execute(
            select(
                NewsItem.headline, NewsItem.source, NewsItem.sentiment,
                NewsItem.score, NewsItem.crawled_at,
            )
            .where(NewsItem.crawled_at > since)
            .order_by(NewsItem.crawled_at.desc())
            .limit(200)
        )).all()
        if not rows:
            return

        # Advance the watermark past everything scanned this cycle (even non-hits)
        # so the same news is never re-evaluated / re-alerted.
        newest = max(r.crawled_at for r in rows)
        await RuntimeConfig.set(session, "last_news_alert_at", newest.isoformat())
        await session.commit()

        hits = [
            r for r in rows
            if is_high_impact_news(r.headline, r.sentiment, r.score,
                                   settings.NEWS_ALERT_MIN_ABS_SCORE)
        ]
        
        # Deep LLM analysis on ALL new headlines to detect hidden trends and stock catalysts
        from engine.agent.event_arbitrage import evaluate_news_flash
        for r in rows:
            # We don't have a summary column readily here, just pass the headline
            await evaluate_news_flash(r.headline, "Full analysis via LLM reasoning", r.source, session)
            
        if not hits:
            return

        logger.warning(f"[news_alert] {len(hits)} high-impact headline(s) detected")
        try:
            cap = int(settings.NEWS_ALERT_MAX_PER_CYCLE)
            lines = ["⚡ Breaking Market News ⚡\n"]
            for r in hits[:cap]:
                lines.append(f"📌 {r.headline[:150]}\n")
            if len(hits) > cap:
                lines.append(f"…and {len(hits) - cap} more.\n")
            lines.append("👀 Keep eyes on these.")
            from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, RawTextPayload
            await publish(AlertEvent(
                category=AlertCategory.NEWS_EVENT, action=AlertAction.ALERT, severity=Severity.WARNING,
                payload=RawTextPayload(text="\n".join(lines)),
            ))
        except Exception as exc:
            logger.debug(f"[news_alert] telegram notify failed: {exc}")


@celery_app.task(name="tasks.market_news_alert")
def market_news_alert():
    """High-impact news alert — Telegram push on crash-capable headlines."""
    _run_async(_market_news_alert())


# ── 6b. Corporate action check — 09:05 IST daily ─────────────────────────────

async def _corporate_action_check() -> None:
    """Detect stock splits/bonus issues for open positions and auto-adjust them.

    Runs once at 09:05 IST each trading day — after the first 1m candle lands
    but before the fast-SL check has a chance to fire a false stop.
    Compares yesterday's 1d close vs today's first 1m open; if price dropped
    >30%, adjusts units, entry, stop and target proportionally.
    """
    if not _is_india_trading_window():
        return

    from tasks._db import celery_session
    from crawler.corporate_actions import check_and_handle_corporate_actions

    async with celery_session() as session:
        events = await check_and_handle_corporate_actions(session)
        if events:
            logger.info(
                f"[corp_action] {len(events)} corporate action(s) handled: "
                + ", ".join(f"{e.symbol}(×{e.ratio:.2f})" for e in events)
            )
        else:
            logger.debug("[corp_action] no corporate actions detected today")


@celery_app.task(name="tasks.corporate_action_check")
def corporate_action_check():
    """Detect stock splits/bonus issues for open positions and auto-adjust. 09:05 IST."""
    _run_async(_corporate_action_check())


# ── 7. Intraday MIS burst: morning entry + EOD squareoff ─────────────────────
#
# Goals:
#   ① Generate 3-5 trades/day (equity MIS)
#   ② Test agent decision quality on intraday timeframe
#   ③ Keep positions separate from the positional CNC book (own budget + own limit)
#
# Schedule (UTC): entry 04:00 (09:30 IST), squareoff 09:40 (15:10 IST Mon-Fri)
# Positions tagged product='MIS'; excluded from CNC position count in trade_loop.

async def _intraday_entry_task():
    """Full-pipeline intraday entry at ~09:30 IST.

    Pipeline (mirrors _india_trade_loop exactly):
      1. Read latest Hub 7-factor scores from DB (recomputed at 09:29 IST)
      2. Fetch live price from PRICE_CACHE → fallback to last 1m candle
      3. Compute REAL dynamic SL/TP from 1m + 1d candles via compute_indicators
      4. Concurrent Tavily web research + LLM veto for all candidates
      5. Veto failed symbols; log rejection reason
      6. Place surviving signals as MIS trades
      8. Telegram summary with all 7-factor subscores + entry/SL/TP
    """
    # ── HARD BLOCK — News-Only Target Architecture (Phase 1) ─────────────────
    # docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md §6: this entire function is
    # new-entry origination from MasterIntelligenceScore alone, no news event
    # (its own docstring: "this task only opens NEW positions" — there is no
    # exit/risk-management component to preserve, unlike _india_trade_loop or
    # run_master_intelligence_cycle). FORBIDDEN in full under the News-Only
    # architecture. Hardcoded, not a settings flag, so it can't be silently
    # re-enabled by flipping an unrelated config value.
    _NEWS_ONLY_BLOCKS_HUB_ENTRIES = bool(
        getattr(settings, "NEWS_ONLY_BLOCKS_HUB_ENTRIES", True)
    )
    if _NEWS_ONLY_BLOCKS_HUB_ENTRIES:
        logger.info(
            "[intraday_entry] disabled — News-Only architecture hard-block "
            "(docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md)"
        )
        return

    import pandas as pd
    from sqlalchemy import select, func as _func, and_

    from db.models import MasterIntelligenceScore, OpenPosition
    from paper_trading.trade_simulator import open_paper_trade
    from paper_trading.virtual_wallet import VirtualWallet
    from paper_trading.simulation_logger import SimLogger
    from tasks._db import celery_session
    from crawler.live_prices import PRICE_CACHE
    from crawler.price_feed import get_latest_candles
    from engine.signal_generator import TradingSignal as _TS
    from engine.indicators import compute_indicators
    from engine.risk_manager import compute_trade_levels
    from utils.config import settings as _cfg

    if not getattr(_cfg, "INTRADAY_ENABLED", True):
        return

    now_ist = datetime.datetime.now(_IST)
    if not _is_india_trading_window():
        return
    # Only run in the first 90 minutes of the session
    if not (9 <= now_ist.hour < 11):
        logger.info(f"[intraday_entry] Outside entry window ({now_ist.strftime('%H:%M')} IST)")
        return

    from crawler.live_snapshot import fetch_live_snapshot
    await fetch_live_snapshot()


    async with celery_session() as session:
        # Circuit breaker + halt gate — this task only opens NEW positions,
        # so it is safe to bail out entirely when halted.
        from paper_trading.virtual_wallet import VirtualWallet
        from utils.runtime_config import RuntimeConfig
        try:
            halted = await VirtualWallet.check_drawdown_breakers(session)
        except Exception as _brk_exc:
            logger.error(f"[intraday_entry] breaker check failed: {_brk_exc}")
            halted = (await RuntimeConfig.load(session)).trading_halted
        if halted:
            logger.warning("[intraday_entry] TRADING HALTED — skipping")
            return

        # ── Guard: count MIS positions already opened today ────────────────────
        today_utc = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        mis_today = (await session.execute(
            select(OpenPosition)
            .where(OpenPosition.product == "MIS")
            .where(OpenPosition.opened_at >= today_utc)
        )).scalars().all()

        max_intraday = int(getattr(_cfg, "INTRADAY_MAX_TRADES_PER_DAY", 3))
        if len(mis_today) >= max_intraday:
            logger.info(f"[intraday_entry] Already {len(mis_today)}/{max_intraday} MIS trades today — skip")
            return

        slots    = max_intraday - len(mis_today)
        conf_min = float(getattr(_cfg, "INTRADAY_CONFIDENCE_MIN", 40.0))
        size_inr = float(getattr(_cfg, "INTRADAY_POSITION_SIZE_INR", 150_000.0))

        # ── Step 1: top Hub BUY signals (latest batch only) ───────────────────
        _latest_subq = (
            select(
                MasterIntelligenceScore.symbol.label("sym"),
                _func.max(MasterIntelligenceScore.scored_at).label("max_at"),
            )
            .where(MasterIntelligenceScore.symbol.like("%.NS"))
            .group_by(MasterIntelligenceScore.symbol)
        ).subquery()

        # Fetch extras (×3) to cover price-cache misses + post-veto drops
        hub_rows = (await session.execute(
            select(MasterIntelligenceScore)
            .join(
                _latest_subq,
                and_(
                    MasterIntelligenceScore.symbol == _latest_subq.c.sym,
                    MasterIntelligenceScore.scored_at == _latest_subq.c.max_at,
                ),
            )
            .where(MasterIntelligenceScore.is_blocked == False)
            .where(MasterIntelligenceScore.signal.in_(["BUY", "STRONG_BUY"]))
            .where(MasterIntelligenceScore.master_score >= conf_min)
            .order_by(MasterIntelligenceScore.master_score.desc())
            .limit(max(slots * 3, 12))
        )).scalars().all()

        logger.info(f"[intraday_entry] Hub BUY candidates: {len(hub_rows)} (threshold={conf_min:.0f})")

        if not hub_rows:
            logger.warning("[intraday_entry] No Hub BUY signals above threshold — skip")
            return

        # ── Step 2: build TradingSignal objects with live price ───────────────
        signals: list[_TS] = []
        for row in hub_rows:
            sym_base = row.symbol.replace(".NS", "")
            cached = PRICE_CACHE.get(row.symbol) or PRICE_CACHE.get(sym_base)
            if isinstance(cached, dict):
                price = float(cached.get("price", 0) or 0)
            else:
                price = float(getattr(cached, "price", 0) or 0) if cached else 0.0
            if price <= 0:
                # Kite REST LTP (real-time) before any candle fallback.
                try:
                    from crawler.zerodha_market import get_live_prices as _glp
                    _ns = row.symbol if row.symbol.endswith(".NS") else f"{row.symbol}.NS"
                    _q = await _glp([_ns]); _qd = _q.get(_ns) or _q.get(row.symbol) or {}
                    _px = _qd.get("price") or _qd.get("last_price")
                    if _px and float(_px) > 0:
                        price = float(_px)
                except Exception:
                    pass
            if price <= 0:
                # Freshest candle across ALL timeframes, only if <=30 min old
                # (intraday MIS entries need a genuinely live price — no phantom fills).
                try:
                    from crawler.price_feed import get_freshest_candle
                    _cl, _cts = await get_freshest_candle(row.symbol, session)
                    if _cl and _cts:
                        if getattr(_cts, "tzinfo", None):
                            _cts = _cts.replace(tzinfo=None)
                        if (datetime.datetime.utcnow() - _cts).total_seconds() <= 30 * 60:
                            price = _cl
                except Exception:
                    price = 0.0
            if price <= 0:
                logger.debug(f"[intraday_entry] {row.symbol}: no fresh live price, skip")
                continue

            hub_sub = {
                "technical":   float(row.technical_score),
                "news":        float(row.news_score),
                "sector":      float(row.sector_score),
                "macro":       float(row.macro_score),
                "earnings":    float(row.earnings_score),
                "fundamental": float(row.fundamental_score),
                "options":     float(row.options_score),
                "signal":      row.signal,
                "regime":      row.regime or "",
                "scored_at":   row.scored_at.isoformat() if row.scored_at else "",
            }
            signals.append(_TS(
                symbol=row.symbol,
                action="BUY",
                confidence=float(row.master_score),
                final_score=float(row.master_score),
                pattern_score=0.0,
                indicator_score=float(row.master_score),
                sentiment_score=0.0,
                entry_price=price,
                stop_loss=round(price * 0.995, 2),      # placeholder; overwritten below
                take_profit=round(price * 1.010, 2),    # placeholder; overwritten below
                risk_reward_ratio=2.0,
                patterns_detected=[],
                reasoning_points=[
                    f"Hub {row.signal} score={row.master_score:+.0f} "
                    f"[T={row.technical_score:+.0f} N={row.news_score:+.0f} "
                    f"F={row.fundamental_score:+.0f} E={row.earnings_score:+.0f} "
                    f"S={row.sector_score:+.0f} M={row.macro_score:+.0f} "
                    f"O={row.options_score:+.0f}]"
                ],
                regime=row.regime or "",
                timeframe="1m",
                hub_subscores=hub_sub,
            ))

        if not signals:
            logger.warning("[intraday_entry] No signals with valid price — abort")
            return

        # ── Step 3: compute REAL dynamic SL/TP from 1m + 1d candles ──────────
        # Uses same compute_indicators → compute_trade_levels path as trade_loop.
        # Tries 1m first (intraday ATR), falls back to 1d if insufficient bars.
        for sig in signals:
            try:
                # Try 1m candles first (at least 20 bars needed for ATR)
                candles_1m = await get_latest_candles(sig.symbol, "1m", 60, session)
                df = None
                if len(candles_1m) >= 20:
                    df = pd.DataFrame([{
                        "open": c.open, "high": c.high, "low": c.low,
                        "close": c.close, "volume": c.volume, "timestamp": c.timestamp,
                    } for c in candles_1m])
                if df is None or df.empty:
                    # Fall back to daily candles
                    candles_1d = await get_latest_candles(sig.symbol, "1d", 60, session)
                    if len(candles_1d) >= 20:
                        df = pd.DataFrame([{
                            "open": c.open, "high": c.high, "low": c.low,
                            "close": c.close, "volume": c.volume, "timestamp": c.timestamp,
                        } for c in candles_1d])

                sig_ind = compute_indicators(df) if df is not None and not df.empty else None
                lv = compute_trade_levels("BUY", sig.entry_price, sig=sig_ind)
                sig.stop_loss    = lv["stop_loss"]
                sig.take_profit  = lv["target_1"]
                sig.target_2     = lv["target_2"]
                sig.atr          = lv["atr"]
                risk = abs(sig.entry_price - lv["stop_loss"])
                sig.risk_reward_ratio = round(
                    abs(lv["target_2"] - sig.entry_price) / risk, 2
                ) if risk > 0 else 2.0

                # Build expert note (same as trade_loop)
                try:
                    from integrations.trade_explainer import build_expert_note
                    expert = build_expert_note(
                        symbol=sig.symbol,
                        direction="BUY",
                        entry=sig.entry_price,
                        stop=lv["stop_loss"],
                        target_1=lv["target_1"],
                        target_2=lv["target_2"],
                        confidence=sig.confidence,
                        hub=sig.hub_subscores or None,
                        reasoning=sig.reasoning_points[0] if sig.reasoning_points else "",
                        strategy="INTRADAY_MIS",
                        regime=sig.regime or "",
                    )
                    sig.reasoning_points = [expert]
                except Exception:
                    sig.reasoning_points.append(
                        f"Trade levels [{lv['source']}]: SL ₹{lv['stop_loss']} "
                        f"· T1 ₹{lv['target_1']} · T2 ₹{lv['target_2']}"
                    )
            except Exception as exc:
                logger.debug(f"[intraday_entry] {sig.symbol} level calc failed: {exc}")

        # ── Step 4: concurrent Tavily web research + LLM veto ─────────────────
        # Same 8-second timeout per symbol as the main trade loop.
        # Failures default to ALLOW so research never blocks execution.
        from engine.pre_trade_research import run_pre_trade_research
        research_tasks = [
            asyncio.wait_for(
                run_pre_trade_research(
                    symbol=sig.symbol,
                    action=sig.action,
                    score=sig.final_score,
                    regime=getattr(sig, "regime", "") or "",
                    entry=sig.entry_price,
                    stop=sig.stop_loss or 0.0,
                    t1=sig.take_profit or 0.0,
                    fund_grade=str(getattr(sig, "fundamental_grade", "") or ""),
                ),
                timeout=8.0,
            )
            for sig in signals
        ]
        research_results = await asyncio.gather(*research_tasks, return_exceptions=True)

        vetoed: set[str] = set()
        for sig, res in zip(signals, research_results):
            if isinstance(res, Exception):
                logger.debug(f"[intraday_entry] research error {sig.symbol}: {res}")
                continue
            if res.get("veto"):
                vetoed.add(sig.symbol)
                logger.warning(
                    f"[intraday_entry] VETO {sig.symbol}: {res['veto_reason']}"
                )
                await SimLogger.log_analysis_cycle(
                    session, sig.symbol, sig,
                    rejected=True,
                    reject_reason=f"[intraday/web-veto] {res['veto_reason']}",
                )
            else:
                note = res.get("research_note", "")
                if note:
                    sig.reasoning_points.append(f"[web] {note[:300]}")

        surviving = [s for s in signals if s.symbol not in vetoed]
        logger.info(
            f"[intraday_entry] {len(signals)} candidates → "
            f"{len(vetoed)} vetoed → {len(surviving)} approved"
        )

        if not surviving:
            logger.warning("[intraday_entry] All candidates vetoed — no trades placed")
            await session.commit()
            return

        # ── Step 5: place MIS trades for approved signals ─────────────────────
        wallet  = await VirtualWallet.get_summary(session)
        balance = wallet["balance"]
        opened  = 0
        opened_details: list[dict] = []

        for sig in surviving:
            if opened >= slots:
                break
            units = max(1, int(size_inr / sig.entry_price))
            cost  = sig.entry_price * units
            if cost > balance * 0.95:
                logger.debug(f"[intraday_entry] {sig.symbol}: insufficient cash (₹{balance:.0f})")
                continue

            from engine.decision_router import TradeIntent, ConfidenceSource, EventDirectness, StrategyFamily, execute_trade_intent, RoutingOutcome
            _intent = TradeIntent(
                strategy="INTRADAY_MIS", symbol=sig.symbol, action="BUY", instrument_type="EQUITY",
                entry_price=sig.entry_price, stop_loss=sig.stop_loss, take_profit=sig.take_profit,
                confidence=sig.confidence, confidence_source=ConfidenceSource.CALCULATED,
                strategy_family=StrategyFamily.TECHNICAL,
                event_directness=EventDirectness.NOT_APPLICABLE,
                position_size_hint={"units": units, "usd_value": cost}, product="MIS",
            )
            try:
                _gate_result = await execute_trade_intent(_intent, session)
                if _gate_result.outcome not in (RoutingOutcome.EXECUTED_PAPER, RoutingOutcome.EXECUTED_LIVE):
                    logger.info(f"[intraday_entry] gate blocked {sig.symbol}: {_gate_result.outcome.value} — {_gate_result.reason}")
                    continue
                balance -= cost
                opened  += 1
                opened_details.append({
                    "symbol": sig.symbol.replace(".NS", ""),
                    "price":  sig.entry_price,
                    "sl":     sig.stop_loss,
                    "tp":     sig.take_profit,
                    "score":  sig.final_score,
                    "units":  units,
                })
                logger.info(
                    f"[intraday_entry] ✓ MIS BUY {sig.symbol} ×{units} "
                    f"@₹{sig.entry_price:.2f} SL=₹{sig.stop_loss:.2f} TP=₹{sig.take_profit:.2f} "
                    f"score={sig.final_score:+.1f}"
                )
            except Exception as exc:
                logger.warning(f"[intraday_entry] {sig.symbol} open failed: {exc}")

        await VirtualWallet.take_daily_snapshot(session)
        await session.commit()

        logger.info(
            f"[intraday_entry] done — {opened} MIS trade(s) placed, "
            f"{len(vetoed)} vetoed by web research"
        )

        # ── Step 7: Telegram summary with full breakdown ───────────────────────
        if opened:
            lines = [
                f"🌅 <b>Intraday MIS Entry — {now_ist.strftime('%d %b %H:%M')} IST</b>",
                f"Placed: {opened} trade(s)  |  Vetoed: {len(vetoed)}",
                "",
            ]
            for d in opened_details:
                lines.append(
                    f"• <b>{d['symbol']}</b>  score={d['score']:+.0f}  "
                    f"×{d['units']} @₹{d['price']:.2f}  "
                    f"SL ₹{d['sl']:.2f} → TP ₹{d['tp']:.2f}"
                )
            if vetoed:
                lines.append(f"\n<i>Vetoed: {', '.join(s.replace('.NS','') for s in vetoed)}</i>")
            from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, RawTextPayload
            await publish(AlertEvent(
                category=AlertCategory.TRADE, action=AlertAction.ENTRY, severity=Severity.SUCCESS,
                payload=RawTextPayload(text="\n".join(lines)),
            ))


async def _intraday_squareoff_task():
    """Close all open MIS positions at 15:10 IST before Zerodha auto-squareoff at 15:20.

    Always runs regardless of INTRADAY_ENABLED — SELL trades from the main
    trade loop are tagged MIS and must be squared off daily even when the
    intraday burst is disabled.
    """
    from sqlalchemy import select

    from db.models import OpenPosition
    from paper_trading.trade_simulator import close_paper_trade
    from paper_trading.virtual_wallet import VirtualWallet
    from tasks._db import celery_session
    from crawler.live_prices import PRICE_CACHE

    from crawler.live_snapshot import fetch_live_snapshot
    await fetch_live_snapshot()

    async with celery_session() as session:
        mis_positions = (await session.execute(
            select(OpenPosition).where(OpenPosition.product == "MIS")
        )).scalars().all()

        if not mis_positions:
            logger.info("[intraday_squareoff] No MIS positions to close")
            return

        closed = 0
        total_pnl = 0.0
        details: list[str] = []

        for pos in mis_positions:
            sym_base = pos.symbol.replace(".NS", "")
            cached = PRICE_CACHE.get(pos.symbol) or PRICE_CACHE.get(sym_base)
            if isinstance(cached, dict):
                close_price = float(cached.get("price", 0) or pos.current_price)
            else:
                close_price = float(getattr(cached, "price", 0) or pos.current_price) if cached else pos.current_price
            if close_price <= 0:
                close_price = pos.current_price

            try:
                # Per-position SAVEPOINT: a deadlock or any other DB error on one
                # position poisons the whole shared session's transaction — every
                # later statement fails with "current transaction is aborted"
                # until a rollback happens. Observed 2026-07-03: a deadlock on the
                # 8th close cascaded into 29 more silent failures, leaving those
                # MIS positions open and unmonitored for 3 days. begin_nested()
                # gives each close its own SAVEPOINT that rolls back on exception
                # without poisoning the outer session, so one failure can't take
                # down the rest of the sweep.
                async with session.begin_nested():
                    trade = await close_paper_trade(pos, close_price, "MIS_SQUAREOFF", session)
                pnl = float(trade.pnl or 0)
                total_pnl += pnl
                closed += 1
                sign = "+" if pnl >= 0 else ""
                details.append(f"{sym_base} {sign}₹{pnl:,.0f}")
                logger.info(f"[intraday_squareoff] ✓ Closed {pos.symbol} @₹{close_price:.2f} pnl={sign}₹{pnl:,.0f}")
            except Exception as exc:
                logger.warning(f"[intraday_squareoff] {pos.symbol} close failed: {exc}")

        await VirtualWallet.take_daily_snapshot(session)
        await session.commit()

        sign = "+" if total_pnl >= 0 else ""
        logger.info(f"[intraday_squareoff] closed {closed} MIS position(s), P&L ₹{sign}{total_pnl:,.0f}")

        if closed:
            detail_str = " · ".join(details) if details else ""
            msg = (
                f"📊 <b>Intraday Squareoff Complete</b>\n"
                f"Closed: {closed} MIS position(s)\n"
                f"Total P&L: ₹{sign}{total_pnl:,.0f}\n"
            )
            if detail_str:
                msg += f"Detail: {detail_str}"
            from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, RawTextPayload
            await publish(AlertEvent(
                category=AlertCategory.TRADE, action=AlertAction.EXIT, severity=Severity.SUCCESS,
                # Batch summary (many trades, no single trade_id) -- key it by
                # day so the (TRADE, EXIT) default 7-day cooldown doesn't
                # swallow tomorrow's squareoff summary too.
                dedup_key=f"intraday_squareoff:{datetime.datetime.utcnow().date()}",
                payload=RawTextPayload(text=msg),
            ))


@celery_app.task(name="tasks.intraday_entry")
def intraday_entry():
    """09:30 IST: open intraday MIS trades from top Hub signals."""
    logger.info("[intraday_entry] Starting intraday morning burst")
    _run_async(_intraday_entry_task())


@celery_app.task(name="tasks.intraday_squareoff")
def intraday_squareoff():
    """15:10 IST: squareoff all MIS positions before Zerodha 15:20 auto-SO."""
    logger.info("[intraday_squareoff] Starting MIS squareoff sweep")
    _run_async(_intraday_squareoff_task())


# ── Trade journal sync — keeps the spreadsheet up to date out-of-band ─────────

async def _sync_trade_journal():
    from integrations.sheet_logger import sync_journal
    from tasks._db import celery_session

    async with celery_session() as session:
        return await sync_journal(session)


@celery_app.task(name="tasks.india_tasks.sync_trade_journal")
def sync_trade_journal_task():
    """Reconcile the spreadsheet trade journal with the trades table.

    Idempotent and safe to run on a schedule — picks up trades that closed
    after market hours (when the 60 s trade loop isn't running).
    """
    return _run_async(_sync_trade_journal())


# ── 7. ML model training — kept for beat schedule compatibility ───────────────

async def _train_ml_models():
    from engine.ml_predictor import train_all_models
    from tasks._db import celery_session

    async with celery_session() as session:
        await train_all_models(session)


@celery_app.task(name="tasks.india_tasks.train_ml_models_task")
def train_ml_models_task():
    """Weekly LSTM + RF training for all NSE large + mid cap symbols."""
    logger.info("[ml_training] Starting weekly model training")
    _run_async(_train_ml_models())


# ── 8. Kite portfolio sync — every 15 min during NSE hours ───────────────────

async def _sync_kite_holdings():
    from services.kite_service import KiteService
    from tasks._db import celery_session

    async with celery_session() as session:
        token = await KiteService.get_access_token(session)
        if not token:
            return  # not connected — skip silently
        try:
            raw = await KiteService.sync_holdings(session)
            await KiteService.update_xirr_for_all(session)
            await session.commit()
            logger.info(f"[kite_sync] Synced {len(raw)} holdings")
        except Exception as exc:
            logger.warning(f"[kite_sync] Sync failed: {exc}")


@celery_app.task(name="tasks.india_tasks.sync_kite_holdings")
def sync_kite_holdings():
    """Sync Zerodha Kite portfolio holdings every 15 min during NSE hours.

    Read-only — no orders are placed.
    """
    if not _is_india_trading_window():
        return
    _run_async(_sync_kite_holdings())


# ── 9. Zerodha instrument token refresh — daily 08:00 IST ────────────────────

async def _refresh_zerodha_instruments():
    from crawler.zerodha_client import get_kite_client
    from crawler.zerodha_market import refresh_instrument_tokens
    from tasks._db import celery_session

    kite = get_kite_client()
    if not kite.access_token:
        logger.info("[zerodha] Instrument refresh skipped — no access token")
        return

    async with celery_session() as session:
        count = await refresh_instrument_tokens(session)
        await session.commit()
        logger.info(f"[zerodha] Instrument tokens refreshed: {count} rows")

    # Also refresh the in-memory INSTRUMENT_CACHE used by zerodha_historical
    try:
        from crawler.zerodha_instruments import refresh_instrument_cache
        cached = await refresh_instrument_cache()
        logger.info(f"[zerodha] INSTRUMENT_CACHE refreshed: {cached} symbols")
    except Exception as exc:
        logger.debug(f"[zerodha] INSTRUMENT_CACHE refresh skipped: {exc}")


@celery_app.task(name="tasks.india_tasks.refresh_zerodha_instruments")
def refresh_zerodha_instruments():
    """Download fresh NSE instrument master from Kite daily before market open."""
    _run_async(_refresh_zerodha_instruments())


# ── 10. Zerodha token expiry check — daily 06:05 IST ─────────────────────────

async def _check_zerodha_token():
    from crawler.zerodha_client import clear_kite_token, get_kite_client
    from utils.config import settings

    # 1. If we have a token and it still works, nothing to do.
    if settings.ZERODHA_ACCESS_TOKEN:
        try:
            await get_kite_client().get_profile()
            logger.info("[zerodha] Token still valid")
            return
        except Exception:
            clear_kite_token()
            logger.warning("[zerodha] Token expired at 6 AM — attempting headless auto re-login")

    # 2. Self-heal: re-login headlessly (OAuth via scripts.refresh_zerodha_token).
    #    The refresh hits the backend callback, which now also rebuilds the ticker.
    try:
        result = zerodha_token_refresh_task()
    except Exception as exc:
        result = {"status": "error", "error": repr(exc)}
    if isinstance(result, dict) and result.get("status") == "ok":
        logger.info("[zerodha] 6 AM self-heal re-login succeeded — live feed restored")
        return

    # 3. Auto-login failed → the feed stays frozen until a manual login. ALERT.
    msg = (
        "⚠️ <b>Zerodha auto re-login FAILED</b> (6 AM token refresh).\n"
        "Live price feed will stay frozen until manual login at "
        "<code>/api/v1/zerodha/login-url</code>.\n"
        f"Detail: <code>{str(result)[:300]}</code>"
    )
    logger.error(f"[zerodha] {msg}")
    try:
        from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, RawTextPayload
        await publish(AlertEvent(
            category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.CRITICAL,
            dedup_key="zerodha_token_dead", payload=RawTextPayload(text=msg),
        ))
    except Exception as exc:
        logger.warning(f"[zerodha] telegram alert failed: {exc}")


@celery_app.task(name="tasks.india_tasks.check_zerodha_token")
def check_zerodha_token():
    """Check token validity at 6:05 AM IST (right after daily expiry)."""
    _run_async(_check_zerodha_token())


# ── 11. Live price cache refresh — every 15 s ─────────────────────────────────

@celery_app.task(name="tasks.refresh_live_prices")
def refresh_live_prices_task():
    """Refreshes the in-process PRICE_CACHE. Note: cannot broadcast WebSocket
    from Celery (different process). Broadcasting is handled by the FastAPI
    background task in main.py. This task keeps the cache warm for REST callers."""
    async def _run():
        from crawler.live_prices import refresh_all_prices
        await refresh_all_prices()

    _run_async(_run())


# ── 11b. Sector mapping cache rebuild — Sunday 19:00 UTC (weekly) ─────────────

@celery_app.task(name="tasks.rebuild_sector_cache")
def rebuild_sector_cache_task():
    """Rebuild yfinance-based sector mapping for all 9,600+ NSE stocks. Weekly."""
    async def _run():
        from tasks._db import celery_session
        from utils.sector_cache import rebuild_sector_cache
        async with celery_session() as session:
            count = await rebuild_sector_cache(session)
        logger.info(f"[sector_cache] rebuild complete: {count} mappings")
    _run_async(_run())


# ── 12. Sector data refresh — every 60 s ─────────────────────────────────────

@celery_app.task(name="tasks.refresh_sector_data")
def refresh_sector_data_task():
    """Refresh sector performance data from PRICE_CACHE. Every 60 s."""
    async def _run():
        from crawler.sector_data import refresh_sector_data
        result = await refresh_sector_data()
        logger.info(f"[sector_data] {len(result)} sectors updated")
    _run_async(_run())


# ── 13. Market breadth refresh — every 2 minutes ────────────────────────────

@celery_app.task(name="tasks.refresh_market_breadth")
def refresh_market_breadth_task():
    """Refreshes advances/declines, gainers/losers, 52W movers. Every 2 minutes."""
    async def _run():
        from crawler.market_breadth import refresh_breadth_data
        result = await refresh_breadth_data()
        logger.info(
            f"[breadth] NSE adv={result.get('nse', {}).get('advances', 0)} "
            f"dec={result.get('nse', {}).get('declines', 0)} "
            f"source={result.get('source', '?')}"
        )
    _run_async(_run())


# ── 14. Calendar seed — daily 7 AM IST ───────────────────────────────────────

@celery_app.task(name="tasks.seed_calendar_events", time_limit=300, soft_time_limit=240)
def seed_calendar_events_task():
    """Seeds market calendar with F&O expiries, RBI, holidays, IPOs, earnings.
    Runs daily at 7 AM IST = 1:30 AM UTC.

    full_universe=True, tried 2026-07-28/29: scanning every real NSE+BSE
    symbol (~10,000) via yfinance to cover large names missing from the old
    ~100-symbol curated watchlist (Varun Beverages, Ambuja Cements, Tata
    Capital, Cholamandalam Finance). Confirmed working for coverage, but
    proved fundamentally fragile in practice -- 10-way concurrency got 81%
    of requests rate-limited outright, and even a tuned sequential/backoff
    version run overnight hit a sustained yfinance block after ~3h that
    repeated cooldowns couldn't clear.

    Replaced 2026-07-29 with fetch_nse_board_meetings_calendar(): NSE's own
    /api/event-calendar returns every listed company's CONFIRMED upcoming
    board-meeting date (the legal earnings date under SEBI LODR) for
    "Financial Results" in one bulk call -- no per-symbol looping, no
    rate-limit exposure, and it's the company's own filing rather than an
    analyst guess. seed_calendar_events() always calls it internally
    (unconditional on full_universe), so this task no longer needs
    full_universe=True to get broad coverage -- back to the fast
    curated-~100-symbol default, same as the manual "Refresh Data" button.
    Time budget shrunk accordingly (was 40min soft / 45min hard for the
    full-universe scan).
    """
    async def _run():
        from engine.calendar_engine import seed_calendar_events
        from tasks._db import celery_session
        async with celery_session() as session:
            result = await seed_calendar_events(session, months_ahead=3)
            logger.info(f"[calendar_seed] {result}")
    logger.info("[calendar_seed] Starting")
    _run_async(_run())


# ── 13. Stock info cache refresh — daily 8 AM IST ────────────────────────────

@celery_app.task(name="tasks.refresh_stock_info_cache")
def refresh_stock_info_cache():
    """Refreshes INFO_CACHE (PE, market cap, beta…) for all NSE stocks once daily.
    Runs at 8 AM IST = 2:30 AM UTC.  Separate from the 15-second price refresh.
    """
    async def _run():
        from crawler.live_prices import refresh_info_cache
        from utils.config import settings
        await refresh_info_cache(settings.nse_symbols + settings.nse_mid_symbols)

    logger.info("[stock_info_cache] Starting daily refresh")
    _run_async(_run())
    logger.info("[stock_info_cache] Done")


# ── 15. IPO data refresh — every 30 minutes ──────────────────────────────────

@celery_app.task(name="tasks.india_tasks.refresh_ipo_data")
def refresh_ipo_data():
    """Refresh IPO cache from ipoalerts.in API. Runs every 30 minutes."""
    async def _run():
        from crawler.ipo_crawler import refresh_ipo_cache
        await refresh_ipo_cache()

    logger.info("[ipo_refresh] Starting")
    _run_async(_run())
    logger.info("[ipo_refresh] Done")


# ── 15b. NSE Social Stock Exchange announcements — every 10 minutes ─────────

async def _sync_sse_announcements():
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from crawler.news_crawler import fetch_sse_announcements, SentimentAnalyser
    from db.models import SSEAnnouncement
    from tasks._db import celery_session

    items = await fetch_sse_announcements()
    if not items:
        return {"fetched": 0, "saved": 0}

    analyser = SentimentAnalyser()
    try:
        sentiments = analyser.analyse_batch(
            [f"{i.get('an_desc') or ''} {i.get('text') or ''}" for i in items]
        )
    except Exception as exc:
        logger.error(f"[sse_announcements] sentiment scoring failed: {exc}")
        sentiments = [{"sentiment": "neutral", "score": 0.0}] * len(items)

    saved = 0
    async with celery_session() as session:
        for item, sent in zip(items, sentiments):
            stmt = (
                pg_insert(SSEAnnouncement)
                .values(
                    seq_id=item["seq_id"], comp_name=item["comp_name"],
                    symbol=item["symbol"], an_desc=item["an_desc"], text=item["text"],
                    an_attach=item["an_attach"], att_file_size=item["att_file_size"],
                    has_xbrl=item["has_xbrl"], ann_date=item["ann_date"],
                    ann_tstamp=item["ann_tstamp"], diff_time=item["diff_time"],
                    sentiment=sent.get("sentiment", "neutral"), score=sent.get("score", 0.0),
                )
                .on_conflict_do_nothing(index_elements=["seq_id"])
            )
            result = await session.execute(stmt)
            saved += result.rowcount or 0
        await session.commit()

    return {"fetched": len(items), "saved": saved}


@celery_app.task(name="tasks.india_tasks.sync_sse_announcements")
def sync_sse_announcements():
    """Poll NSE's Social Stock Exchange (NPO) announcement feed. DB-backed
    dedup via seq_id's unique constraint (ON CONFLICT DO NOTHING) rather than
    an in-memory set, so a worker restart can't reprocess/duplicate rows the
    way the News-First Discovery Engine's in-memory dedup can."""
    logger.info("[sse_announcements] Starting")
    result = _run_async(_sync_sse_announcements())
    logger.info(f"[sse_announcements] Done — {result}")


# ── 16. Daily capital snapshot (Sharpe/Treynor/Jensen) ───────────────────────

async def _save_capital_snapshot():
    from engine.portfolio_analytics import save_capital_snapshot
    from tasks._db import celery_session

    async with celery_session() as session:
        snap = await save_capital_snapshot(session)
        await session.commit()
    return snap


@celery_app.task(name="tasks.india_tasks.save_capital_snapshot")
def save_capital_snapshot_task():
    """Compute and save today's portfolio capital model snapshot (Sharpe/Treynor/Jensen).
    Runs daily at 4:15 PM IST = 10:45 UTC, right after market close.
    """
    logger.info("[capital_snapshot] Computing daily portfolio metrics")
    _run_async(_save_capital_snapshot())
    logger.info("[capital_snapshot] Done")


# ── 17/18. Weekly portfolio report (merged, Phase 5) ─────────────────────────
# Was two separate, uncoordinated Sunday tasks 30 minutes apart
# (weekly_portfolio_rebalance + weekly_ai_portfolio_report), each
# independently computing similar Sharpe/Treynor/Alpha risk metrics and
# sending its own Telegram message. Merged into one task, one publish()
# call: structured metrics + rebalance signals render directly in the
# message, the LLM narrative becomes a smaller "AI commentary" addendum,
# and a companion PDF (full metrics table + equity curve) goes out as a
# reply document.

async def _weekly_report_task():
    from engine.portfolio_analytics import (
        compute_performance_metrics,
        compute_rebalance_trades,
        get_position_weights,
        get_sector_weights,
        save_capital_snapshot,
    )
    from tasks._db import celery_session
    from utils.config import settings

    if not settings.telegram_available:
        return

    async with celery_session() as session:
        await save_capital_snapshot(session)
        rebalance_trades = await compute_rebalance_trades(session)
        metrics = await compute_performance_metrics(session, days=30)
        pos_weights = await get_position_weights(session)
        sector_weights = await get_sector_weights(session, pos_weights)
        await session.commit()

    # ── AI commentary (best-effort; a metrics-only fallback if the LLM fails) ──
    ai_text = ""
    if metrics.get("sharpe_ratio") is not None:
        top_sectors = sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)[:5]
        sectors_text = "\n".join(f"  {s}: {w:.1f}%" for s, w in top_sectors)
        top_positions = sorted(pos_weights.items(), key=lambda x: x[1], reverse=True)[:8]
        positions_text = "\n".join(f"  {s.replace('.NS','')}: {w:.1f}%" for s, w in top_positions)
        metrics_text = (
            f"Portfolio Return (annualized): {metrics.get('portfolio_return', 'N/A')}%\n"
            f"NIFTY Benchmark Return: {metrics.get('benchmark_return', 'N/A')}%\n"
            f"Portfolio Beta: {metrics.get('portfolio_beta', 'N/A')}\n"
            f"Sharpe Ratio: {metrics.get('sharpe_ratio', 'N/A')}\n"
            f"Treynor Ratio: {metrics.get('treynor_ratio', 'N/A')}\n"
            f"Jensen's Alpha: {metrics.get('jensens_alpha', 'N/A')}%"
        )
        prompt = f"""You are AutoTrade Pro's AI portfolio manager. Write a brief (100-150 word) weekly commentary for a paper-trading agent focused on NSE Indian equities. The structured metrics are already shown separately -- do not repeat them verbatim, interpret them.

Performance Metrics (last 30 days):
{metrics_text}

Top Sector Exposure:
{sectors_text}

Top Position Weights:
{positions_text}

Write 2 short paragraphs: (1) is the risk-adjusted performance (Sharpe/Treynor/Jensen) good or bad, and why; (2) one actionable recommendation for next week. Plain text, no HTML tags."""
        try:
            from utils.llm import call_llm_chat
            ai_text = await call_llm_chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.3,
                model="nvidia.nemotron-super-3-120b"
            )
            if ai_text:
                ai_text = ai_text.strip()
            else:
                ai_text = ""
        except Exception as exc:
            logger.warning(f"[weekly_report] LLM commentary failed: {exc}")

    # ── Companion PDF (best-effort; the Telegram message is the primary
    #    deliverable and must still go out if PDF building fails) ──────────────
    pdf_bytes = None
    try:
        from integrations.alerts.charts import build_equity_curve_chart, fetch_equity_snapshots
        from integrations.alerts.reports import build_weekly_pdf
        snapshots = await fetch_equity_snapshots()
        equity_png = build_equity_curve_chart(snapshots) if snapshots else None
        pdf_bytes = build_weekly_pdf(
            metrics=metrics, rebalance_trades=rebalance_trades,
            sector_weights=sector_weights, position_weights=pos_weights,
            ai_commentary=ai_text, equity_curve_png=equity_png,
            report_date=str(datetime.date.today()),
        )
    except Exception as exc:
        logger.warning(f"[weekly_report] PDF build failed: {exc}")

    from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, ReportPayload
    await publish(AlertEvent(
        category=AlertCategory.WEEKLY_REPORT, action=AlertAction.REPORT, severity=Severity.INFO,
        payload=ReportPayload(
            metrics=metrics, rebalance_trades=rebalance_trades,
            sector_weights=sector_weights, position_weights=pos_weights,
            ai_commentary=ai_text, pdf_bytes=pdf_bytes,
        ),
    ))
    logger.info(f"[weekly_report] sent — {len(rebalance_trades)} rebalance signal(s), pdf={'yes' if pdf_bytes else 'no'}")


@celery_app.task(name="tasks.india_tasks.weekly_report")
def weekly_report():
    """Weekly portfolio report: rebalance signals + risk metrics + AI
    commentary + companion PDF, one Telegram message. Runs Sunday 17:00
    UTC (10:30 PM IST). Replaces the previously-separate
    weekly_portfolio_rebalance/weekly_ai_portfolio_report tasks.
    """
    logger.info("[weekly_report] Starting weekly portfolio report")
    _run_async(_weekly_report_task())


# ─────────────────────────────────────────────────────────────────────────────
# Kite-library tasks (Step 12)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="tasks.kite_sync_holdings")
def kite_sync_holdings_task():
    """Sync Demat holdings from Kite into portfolio_holdings (daily 15:35 IST)."""
    from utils.config import settings
    if not settings.ZERODHA_ENABLED:
        return {"skipped": True}

    async def _run():
        from db.database import get_db
        from engine.zerodha_portfolio import sync_real_holdings
        async for session in get_db():
            return await sync_real_holdings(session)

    result = _run_async(_run())
    logger.info(f"[kite_sync_holdings] Result: {result}")
    return result


@celery_app.task(
    name="tasks.kite_live_candles",
    # 2026-08-04: previously had no override, so it inherited the celery-wide
    # default (300s/600s, tasks/celery_app.py). hub_universe has grown to
    # 2,569 symbols since this task's original "~500 in ~90s" design point,
    # so real runs now regularly exceed 300s. The soft-limit hit was landing
    # mid-chunk inside save_candles_to_db(), rolling back the whole run's
    # uncommitted inserts while the task still logged a false "saved: N"
    # success — see crawler/price_feed.py::save_candles_to_db for the
    # matching per-chunk-commit fix. 1200/1260 gives real headroom for the
    # current universe size.
    soft_time_limit=1200,
    time_limit=1260,
)
def kite_live_candles_task(closing_sweep: bool = False):
    """Fetch 1-minute candles from Kite every 3 min while NSE market is open.

    Covers the full hub universe (2,569+ symbols) via concurrent fetching
    (semaphore=3). Runs 09:15–15:30 IST Mon–Fri via beat. Upsert-safe.

    `closing_sweep=True` runs the same pass just after the close, which is the
    only way the final minutes of the session ever land. On a 3-minute beat the
    last in-session tick fires at 15:30 exactly, and a full pass over the
    universe does not finish before the task's own window closes: measured on
    24 Aug, only 582 symbols carried a 15:29 bar while 1,463 stopped at 15:26.
    Those missing minutes are not idle ones — the sugar pack did most of its
    move in them, which is why DWARKESH and BAJAJHIND showed a 3% gap against
    the Kite close. The sweep re-fetches with the whole session available, and
    the upsert makes re-covering settled bars free.
    """
    from utils.config import settings
    if not settings.ZERODHA_ENABLED:
        return {"skipped": "zerodha_disabled"}

    now_ist = datetime.datetime.now(_IST)
    h, m = now_ist.hour, now_ist.minute
    if closing_sweep:
        # After the close, but still the same trading day.
        in_session = ((h, m) >= (15, 30)) and (h < 18) and now_ist.weekday() < 5
    else:
        in_session = ((h, m) >= (9, 15)) and ((h, m) <= (15, 30)) and now_ist.weekday() < 5
    if not in_session:
        return {"skipped": "outside_market_hours"}

    # Overlap guard (2026-08-04) -- runtime now regularly exceeds the 3-min
    # beat interval, so without this, successive beat ticks were stacking
    # multiple instances on the same 4-worker/4-core box, worsening both the
    # timeout above and the CPU contention already fixed for
    # _pre_event_gap_scan_loop. Same atomic SET NX EX pattern as the
    # DIRECT_NEWS recheck throttle (paper_trading/trade_simulator.py) --
    # single Redis round-trip, self-clearing TTL, no manual cleanup needed.
    import redis.asyncio as _aioredis

    async def _acquire_lock() -> bool:
        _r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            return bool(await _r.set("kite_live_candles:running", "1", nx=True, ex=1320))
        finally:
            await _r.aclose()

    async def _release_lock():
        _r = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await _r.delete("kite_live_candles:running")
        finally:
            await _r.aclose()

    if not _run_async(_acquire_lock()):
        # Log the skip (2026-08-26, phase 18). The lock itself is correct and
        # stays: a 3-minute beat against a run that took 10-18 minutes would
        # otherwise stack concurrent full-universe fetches until the worker
        # pool is exhausted. But this path returned WITHOUT logging, so the
        # loss was invisible — ~125 dispatches on 2026-08-26 produced 37
        # completions and no record of the ~88 that were dropped. One line
        # makes the effective cadence measurable instead of inferred.
        logger.info("[kite_live_candles] SKIPPED already_running")
        return {"skipped": "already_running"}

    async def _run():
        from tasks._db import celery_session
        from crawler.zerodha_historical import sync_live_1m_candles
        from crawler.zerodha_instruments import refresh_instrument_cache
        from engine.hub_universe import get_hub_universe

        await refresh_instrument_cache()
        async with celery_session() as session:
            hub_syms = await get_hub_universe(session)
            # Strip .NS/.BO — get_kite_candles_for_range handles both forms
            symbols = [s.replace(".NS", "").replace(".BO", "") for s in hub_syms]
            return await sync_live_1m_candles(session, symbols=symbols)

    try:
        result = _run_async(_run())
    finally:
        _run_async(_release_lock())
    logger.info(f"[kite_live_candles] {result}")
    return result


@celery_app.task(name="tasks.kite_sync_candles")
def kite_sync_candles_task():
    """Fetch daily candles for all NSE watchlist symbols via Kite (10:00 UTC)."""
    from utils.config import settings
    if not settings.ZERODHA_ENABLED:
        return {"skipped": True}

    async def _run():
        from db.database import get_db
        from crawler.zerodha_historical import sync_all_nse_candles
        async for session in get_db():
            return await sync_all_nse_candles(session)

    result = _run_async(_run())
    logger.info(f"[kite_sync_candles] Result: {result}")
    return result


@celery_app.task(name="tasks.kite_refresh_instruments")
def kite_refresh_instruments_task():
    """Refresh the NSE instrument cache (02:30 UTC / 08:00 IST)."""
    from utils.config import settings
    if not settings.ZERODHA_ENABLED:
        return {"skipped": True}

    async def _run():
        from crawler.zerodha_instruments import refresh_instrument_cache
        return await refresh_instrument_cache()

    count = _run_async(_run())
    logger.info(f"[kite_refresh_instruments] Cache refreshed: {count} symbols")
    return {"refreshed": count}


@celery_app.task(name="tasks.kite_check_token")
def kite_check_token_task():
    """Verify token validity after the 6:00 AM IST expiry (00:35 UTC)."""
    from utils.config import settings
    from crawler.zerodha_kite_lib import verify_token, _write_env, reset_kite
    valid = False
    try:
        valid = verify_token()
    except Exception as exc:
        logger.warning(f"[kite_check_token] verify failed: {exc}")
    if not valid:
        settings.ZERODHA_ENABLED = False
        _write_env("ZERODHA_ENABLED", "false")
        reset_kite()
        logger.warning("[kite_check_token] Kite access_token expired — user must re-login")
    return {"valid": valid}


@celery_app.task(
    name="tasks.run_master_intelligence_cycle",
    soft_time_limit=1080,  # ~758 symbols × candle-load + indicators; raise from the 300s default
    time_limit=1200,
)
def run_master_intelligence_cycle():
    """Master brain cycle: build unified context, score the NSE universe,
    drive the agent on top opportunities, score MFs, log the cycle."""
    import pandas as pd

    from utils.runtime_config import strategy_enabled

    # Admin toggle (Path A).
    #
    # NOTE what this switch actually controls. Path A no longer ORIGINATES
    # trades -- that inline loop was removed 2026-07-21 (Phase 3C Phase B).
    # What it still does is score the universe into market_shortlist and run
    # two exit paths: executor.check_and_close_positions() and the
    # sector-STRONGLY_BEARISH sweep. Turning it off therefore stops SCORING
    # (which starves Path B of candidates) and stops those two discretionary
    # exits.
    #
    # Stop-losses are NOT affected: fast_sl_check runs every 5s on its own beat
    # entry and is deliberately not gated by any strategy toggle, so an open
    # position still exits on SL/TP with every strategy switched off.
    if not _run_async(strategy_enabled("master_intelligence")):
        logger.info("[hub] master intelligence cycle skipped — disabled by strategy toggle")
        return {"status": "disabled_by_toggle"}

    async def _run():
        from datetime import datetime
        from db.database import get_db
        from engine.intelligence_hub import (
            build_master_context, score_universe, persist_scores,
            persist_daily_history, run_research_gate_for_history,
            _get_sector_for_symbol,
        )
        from engine.agent.agent_loop import (
            _get_portfolio, _is_market_hours, _is_trading_day,
        )
        from engine.agent.execution import AgentExecutionManager
        from engine.mf_signal_engine import (
            get_portfolio_mf_holdings, score_mf_universe, persist_mf_scores,
        )
        from crawler.price_feed import get_latest_candles
        from crawler.live_prices import PRICE_CACHE
        from db.models import HubCycleLog
        from utils.config import settings

        if not settings.AGENT_ENABLED:
            logger.info("[hub] agent disabled — skipping master cycle")
            return
        if not _is_trading_day():
            logger.info("[hub] not a trading day — skipping")
            return

        portfolio   = _get_portfolio()
        cycle_start = datetime.utcnow()

        # ── Overlap guard ──────────────────────────────────────────────────────
        # This cycle scores ~1,700 symbols and can run close to its own 15-min
        # schedule interval under CPU contention. Without this guard, a slow
        # cycle plus beat's next tick stack multiple heavy scoring passes on the
        # same 4 worker slots, each one making the others slower — a
        # self-inflicted pile-up (observed: 5 rows stuck "running" back to back
        # after a cold restart). Skip this tick if the previous one hasn't
        # finished (or errored/timed-out without updating its row) yet.
        from datetime import timedelta as _timedelta
        from sqlalchemy import select as _sel_guard
        _guard_cutoff = cycle_start - _timedelta(seconds=1200)
        async for _gsession in get_db():
            _running = (await _gsession.execute(
                _sel_guard(HubCycleLog.id).where(
                    HubCycleLog.status == "running",
                    HubCycleLog.cycle_start >= _guard_cutoff,
                ).limit(1)
            )).scalar_one_or_none()
            break
        if _running is not None:
            logger.warning(
                "[hub] previous cycle still running — skipping this tick to avoid "
                "stacking concurrent scoring passes"
            )
            return

        # ── Live snapshot: hot-patch PRICE_CACHE + SECTOR_CACHE from Kite ─────
        # Celery workers never receive WebSocket ticks — without this every
        # downstream PRICE_CACHE read (macro, VIX, sector mood, entry price)
        # would use stale data.
        from crawler.live_snapshot import fetch_live_snapshot
        _open_syms = list(portfolio.open_positions.keys())
        await fetch_live_snapshot(extra_symbols=_open_syms)

        async for session in get_db():
            cycle_log = HubCycleLog(cycle_start=cycle_start, bar_time=cycle_start, status="running")
            session.add(cycle_log)
            await session.commit()

            try:
                # Build universe first so Tavily can enrich missing news in build_master_context
                from engine.hub_universe import get_hub_universe
                universe = await get_hub_universe(session)

                ctx = await build_master_context(portfolio, session, hub_universe=universe)
                logger.info(
                    f"[hub] context: macro_bias={ctx.macro.total_macro_bias:+d} "
                    f"vix={ctx.macro.india_vix:.1f} mood={ctx.macro.nse_market_mood} "
                    f"news={len(ctx.news.scores_by_symbol)} earnings={len(ctx.earnings.tones_by_symbol)}"
                )
                logger.info(f"[hub] scoring universe of {len(universe)} symbols")
                # Daily candles: the 500-name universe is backfilled at 1d (only
                # the ~22 legacy large-caps have live 1h bars). Score on '1d' so
                # the whole universe is covered, not just the hourly-fed names.
                scored = await score_universe(universe, ctx, session, timeframe="1d")
                await persist_scores(scored, cycle_start, session)

                # ── Flight recorder: store full Hub output + web research gate ──
                # Runs pre-trade research for top 15 BUY signals (Tavily budget-safe:
                # ~15 calls/cycle × 1 cycle/day = 330 calls/month, within free tier).
                # The hub_daily_history table becomes the historical replay source
                # for backtest — grows one row per symbol per trading day.
                try:
                    research_results = await run_research_gate_for_history(scored, max_symbols=15)
                    await persist_daily_history(scored, ctx, session, research_results)
                except Exception as _hist_exc:
                    logger.warning(f"[hub] daily history persist failed: {_hist_exc}")

                top_buys = [
                    {"symbol": s.symbol, "score": s.master_score}
                    for s in scored if s.signal in ("STRONG_BUY", "BUY") and not s.is_blocked
                ][:5]
                top_sells = [
                    {"symbol": s.symbol, "score": s.master_score}
                    for s in scored if s.signal in ("STRONG_SELL", "SELL") and not s.is_blocked
                ][:5]
                logger.info(f"[hub] scored {len(scored)} | top_buys={[b['symbol'] for b in top_buys]}")

                decisions_made = 0
                if _is_market_hours():
                    # `selector`/`de`/`rm` (StrategySelectorAgent/DecisionEngine/
                    # RiskManagerAgent) previously instantiated here were used
                    # exclusively by the hub-inline new-entry loop removed below
                    # (Phase 3C Phase B, docs/PHASE_3C_PHASE_B_DEPENDENCY_AUDIT.md
                    # §6 — that loop called executor.execute() directly with no
                    # TradeIntent/authorize_trade_intent() in between, a genuine
                    # central-gate bypass, latent only because the loop had been
                    # hard-blocked to iterate an empty list since Phase 1). Removed
                    # along with the loop, not kept as unused scaffolding. `executor`
                    # itself stays — it's still needed immediately below for exit
                    # management (check_and_close_positions), which is unrelated to
                    # trade origination and was never part of the bypass.
                    executor = AgentExecutionManager()

                    await executor.check_and_close_positions(portfolio, PRICE_CACHE, session)

                    # Exit positions whose sector turned strongly bearish
                    for sym, pos in list(portfolio.open_positions.items()):
                        sec = _get_sector_for_symbol(sym)
                        if ctx.sectors.sector_moods.get(sec) == "STRONGLY_BEARISH":
                            price = (PRICE_CACHE.get(sym, {}) or {}).get("price", 0) or 0
                            if price > 0:
                                portfolio.close_position(sym, price)
                                logger.warning(f"[hub] exited {sym}: sector {sec} STRONGLY_BEARISH")

                    # ── REMOVED (Phase 3C Phase B, 2026-07-21) ───────────────────────
                    # docs/PHASE_3C_PHASE_B_DEPENDENCY_AUDIT.md §6/§8: this used to be a
                    # ~200-line inline loop (the "Master Intelligence strategy") that
                    # independently originated NEW equity/short entries straight from
                    # MasterIntelligenceScore, with no news event involved. It had been
                    # hard-blocked since Phase 1 by swapping its iterable for an empty
                    # list (`_NEWS_ONLY_BLOCKS_HUB_ENTRIES = True`), but the loop BODY —
                    # left in place — called `executor.execute(decision, session)`
                    # directly, with no `TradeIntent` construction and no call to
                    # `authorize_trade_intent()`/`execute_trade_intent()` anywhere in
                    # between. That is a genuine central-gate bypass: if the empty-list
                    # guard above it were ever reverted without also wiring this loop
                    # through the gate, trades would originate with zero
                    # confidence-provenance check, zero event check, and zero of the
                    # gate's risk validation beyond the loop's own `rm.can_take_trade()`.
                    # The dependency audit confirmed this loop body was the ONLY thing
                    # `selector`/`de`/`rm`/`flow`/`_hub_short_strat`/`_intraday_on`/
                    # `_short_enabled` existed for — none of them are used anywhere else
                    # in this function or file — so removing the loop meant removing all
                    # of that scaffolding with it, not leaving it as dead weight.
                    # Scoring (score_universe/persist_scores), exit management
                    # (check_and_close_positions, the sector-bearish exit loop above),
                    # and MF scoring below are untouched — none of them depended on this
                    # loop or its variables.
                    logger.info(
                        "[hub] new-entry origination via this inline loop was permanently "
                        "removed under the News-Only architecture (Phase 3C Phase B) — "
                        "scoring + exit management ran normally above this line"
                    )

                # Score MF portfolio
                try:
                    mfs = await get_portfolio_mf_holdings(session)
                    if mfs:
                        mf_scores = await score_mf_universe(mfs, ctx, session)
                        await persist_mf_scores(mf_scores, session)
                        logger.info(f"[hub] MF scored: {len(mf_scores)}")
                except Exception as exc:
                    logger.warning(f"[hub] MF scoring skipped: {exc}")

                cycle_log.cycle_end        = datetime.utcnow()
                cycle_log.symbols_scored   = len(scored)
                cycle_log.top_buys         = top_buys
                cycle_log.top_sells        = top_sells
                cycle_log.macro_context    = {
                    "total_macro_bias": ctx.macro.total_macro_bias,
                    "india_vix":        ctx.macro.india_vix,
                    "nse_market_mood":  ctx.macro.nse_market_mood,
                    "fii_net_3d":       ctx.macro.fii_net_3d,
                    "dii_net_3d":       ctx.macro.dii_net_3d,
                }
                cycle_log.decisions_made   = decisions_made
                cycle_log.skipped_count    = sum(1 for s in scored if s.is_blocked)
                cycle_log.status           = "complete"
                cycle_log.duration_seconds = (datetime.utcnow() - cycle_start).total_seconds()
                await session.commit()

                # Broadcast to WS clients via Redis pub/sub (non-fatal)
                try:
                    import json, redis as _redis
                    r = _redis.from_url(
                        settings.REDIS_URL,
                        ssl_cert_reqs=None if settings.redis_uses_tls else None,
                    )
                    r.publish("hub_events", json.dumps({
                        "type": "hub_cycle_complete",
                        "bar_time": cycle_start.isoformat(),
                        "top_buys": [b["symbol"] for b in top_buys],
                        "top_sells": [s["symbol"] for s in top_sells],
                        "macro_bias": ctx.macro.total_macro_bias,
                        "vix": ctx.macro.india_vix,
                        "mood": ctx.macro.nse_market_mood,
                        "decisions": decisions_made,
                        "scores_updated": len(scored),
                    }))
                except Exception:
                    pass

                logger.info(
                    f"[hub] cycle complete in {cycle_log.duration_seconds:.1f}s | "
                    f"scored={len(scored)} trades={decisions_made} macro={ctx.macro.total_macro_bias:+d}"
                )
            except Exception as exc:
                logger.exception(f"[hub] cycle error: {exc}")
                cycle_log.status = "error"
                cycle_log.error_msg = str(exc)[:500]
                try:
                    await session.commit()
                except Exception:
                    pass
            break

    asyncio.run(_run())
    return {"status": "done"}


@celery_app.task(name="tasks.agent_eod_reconcile")
def agent_eod_reconcile_task():
    """End-of-day: close remaining open positions, reset daily counters."""
    async def _run():
        from db.database import get_db
        from crawler.live_prices import PRICE_CACHE
        from crawler.live_snapshot import fetch_live_snapshot
        from engine.agent.agent_loop import _get_portfolio, _executor, eod_reconcile

        portfolio = _get_portfolio()
        await fetch_live_snapshot(extra_symbols=list(portfolio.open_positions.keys()))
        async for session in get_db():
            await _executor.check_and_close_positions(portfolio, PRICE_CACHE, session)
            break
        eod_reconcile()

    asyncio.run(_run())
    return {"status": "done"}


@celery_app.task(name="tasks.fetch_earnings_transcripts")
def fetch_earnings_transcripts_task():
    """Auto-fetch and AI-summarize new earnings for top NSE stocks.
    Runs daily at 14:30 UTC (20:00 IST) during results season.
    """
    from utils.config import settings

    async def _run():
        from db.database import get_db
        from engine.earnings_summarizer import get_earnings_summary
        async for session in get_db():
            for symbol in settings.nse_symbols[:10]:
                try:
                    summary = await get_earnings_summary(symbol, session=session)
                    if summary:
                        logger.info(
                            f"[earnings_task] {symbol} {summary.quarter} "
                            f"tone={summary.management_tone} words={summary.word_count}"
                        )
                except Exception as exc:
                    logger.warning(f"[earnings_task] Failed for {symbol}: {exc}")
                await asyncio.sleep(5)
            break

    asyncio.run(_run())
    return {"status": "done"}


@celery_app.task(name="tasks.zerodha_token_refresh")
def zerodha_token_refresh_task():
    """Auto-refresh Zerodha access token daily at 08:00 IST (02:30 UTC).

    Drives the full OAuth flow headlessly: password login → TOTP → request_token
    → access_token exchange. On success, ZERODHA_ENABLED flips to True in
    memory so the ticker start task that fires at 09:15 IST will start live feeds.
    """
    try:
        # Ensure the backend root is importable regardless of the worker's cwd —
        # a worker started from a different directory previously failed with
        # "No module named 'scripts'", silently skipping the daily auto-login.
        import sys as _sys
        from pathlib import Path as _Path
        _root = str(_Path(__file__).resolve().parent.parent)
        if _root not in _sys.path:
            _sys.path.insert(0, _root)

        from scripts.refresh_zerodha_token import main as _refresh
        _refresh(backend="http://127.0.0.1:8000")
        logger.info("[zerodha_token_refresh] Token refreshed successfully")

        # The OAuth exchange happens in the BACKEND process (via the callback), so
        # only the backend's Kite singleton + .env get the new token. This task
        # runs in the CELERY process, whose singleton is still stale. Re-read the
        # fresh token from .env and apply it locally so Celery's mark-to-market /
        # journal / live-price tasks use the new token too.
        try:
            from dotenv import dotenv_values
            from pathlib import Path
            env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
            new_token = (env or {}).get("ZERODHA_ACCESS_TOKEN", "")
            if new_token:
                from crawler.zerodha_client import update_kite_token
                update_kite_token(new_token)
                logger.info("[zerodha_token_refresh] Celery singleton updated with fresh token")
        except Exception as exc:
            logger.warning(f"[zerodha_token_refresh] local token reload failed: {exc}")

        # If market is already open (e.g. task ran late), kick the ticker now
        # rather than waiting for the 09:15 beat slot.
        from crawler.india_price_feed import is_nse_market_open
        from crawler.zerodha_ticker import is_ticker_running, start_kite_ticker
        if is_nse_market_open() and not is_ticker_running():
            start_kite_ticker()
            logger.info("[zerodha_token_refresh] Ticker started immediately after token refresh")
        return {"status": "ok"}
    except SystemExit as exc:
        logger.error(f"[zerodha_token_refresh] Token refresh failed (exit {exc.code})")
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        logger.error(f"[zerodha_token_refresh] Unexpected error: {exc}")
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="tasks.zerodha_ensure_token")
def zerodha_ensure_token_task():
    """Refresh the Zerodha token if it's missing/expired — a catch-up for when
    the daily 08:00 IST schedule was missed because the machine was powered off
    overnight. Runs on worker startup (see worker_ready below) so the token is
    restored whenever the app comes online, not only at the fixed cron slot.
    """
    from utils.config import settings
    if not (settings.ZERODHA_USER_ID and settings.ZERODHA_PASSWORD and settings.ZERODHA_TOTP_SECRET):
        return {"skipped": "no_credentials"}
    valid = False
    try:
        from crawler.zerodha_kite_lib import verify_token
        valid = verify_token()
    except Exception as exc:
        logger.warning(f"[zerodha_ensure_token] verify failed (treating as expired): {exc}")
    if valid:
        logger.info("[zerodha_ensure_token] Token still valid — no refresh needed")
        return {"valid": True, "refreshed": False}
    logger.info("[zerodha_ensure_token] Token missing/expired — running auto-login now")
    return zerodha_token_refresh_task()


@celery_app.task(name="tasks.refresh_upstox_token")
def refresh_upstox_token_task():
    """Auto-refresh the Upstox access token daily at 08:15 IST (02:45 UTC) —
    Upstox tokens expire daily. Unlike Zerodha's flow this is fully headless
    (TOTP, no OAuth browser hop), so it can run standalone in the Celery
    process. On repeated failure it alerts via Telegram, so the company-
    intelligence enrichment layer going dark doesn't fail silently."""
    from crawler.upstox_auth import refresh_upstox_token_with_retry
    from utils.config import settings
    if not (settings.UPSTOX_API_KEY and settings.UPSTOX_API_SECRET):
        return {"skipped": "no_credentials"}
    ok = _run_async(refresh_upstox_token_with_retry(retries=3))
    if not ok:
        logger.error("[upstox_token_refresh] Token refresh failed after 3 attempts")
        try:
            from integrations.alerts import publish_sync, AlertEvent, AlertCategory, AlertAction, Severity, RawTextPayload
            publish_sync(AlertEvent(
                category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.CRITICAL,
                dedup_key="upstox_token_refresh_failed",
                payload=RawTextPayload(text=(
                    "⚠️ <b>Upstox token refresh failed</b> (3 attempts).\n"
                    "Company-intelligence enrichment (fundamentals/shareholding/"
                    "corporate actions) will be unavailable until this is fixed — "
                    "check UPSTOX_TOTP_SECRET/UPSTOX_PIN in .env, or visit "
                    "<code>/api/v1/upstox/login</code> to re-authenticate manually."
                )),
            ))
        except Exception as exc:
            logger.warning(f"[upstox_token_refresh] telegram alert failed: {exc}")
        return {"status": "failed"}
    logger.info("[upstox_token_refresh] Token refreshed successfully")
    return {"status": "ok"}


@celery_app.task(name="tasks.upstox_ensure_token")
def upstox_ensure_token_task():
    """Catch-up refresh on worker boot — mirrors zerodha_ensure_token_task so a
    missed 08:15 IST slot (e.g. machine was off overnight) doesn't leave the
    Upstox enrichment layer dark until the next day's cron slot."""
    from utils.config import settings
    if not (settings.UPSTOX_API_KEY and settings.UPSTOX_API_SECRET):
        return {"skipped": "no_credentials"}
    from crawler.upstox_auth import verify_upstox_token
    valid = _run_async(verify_upstox_token())
    if valid:
        logger.info("[upstox_ensure_token] Token still valid — no refresh needed")
        return {"valid": True, "refreshed": False}
    logger.info("[upstox_ensure_token] Token missing/expired — running auto-login now")
    return refresh_upstox_token_task()


@worker_ready.connect
def _on_worker_ready(**_kwargs):
    """When the worker boots, schedule a token catch-up. Delayed so the backend
    (which serves the OAuth callback the refresh depends on) has time to come up.
    """
    try:
        zerodha_ensure_token_task.apply_async(countdown=45)
        logger.info("[worker_ready] scheduled Zerodha token catch-up (+45s)")
    except Exception as exc:
        logger.warning(f"[worker_ready] could not schedule token catch-up: {exc}")
    try:
        upstox_ensure_token_task.apply_async(countdown=50)
        logger.info("[worker_ready] scheduled Upstox token catch-up (+50s)")
    except Exception as exc:
        logger.warning(f"[worker_ready] could not schedule Upstox token catch-up: {exc}")


@celery_app.task(name="tasks.kite_start_ticker")
def kite_start_ticker_task():
    """(Re)start the KiteTicker just before market open (03:45 UTC / 09:15 IST).

    ALWAYS rebuilds — never skips on is_ticker_running(). A KiteTicker from a prior
    session bakes the (now 6 AM-expired) token in at construction and auto-reconnects
    on it, 403-looping while intermittently reporting 'connected'. Treating that as
    'already running' is exactly what kept the feed frozen, so we tear any existing
    ticker down and rebuild it on the current token.
    """
    from utils.config import settings
    if not settings.ZERODHA_ENABLED:
        return {"skipped": "zerodha_disabled"}
    from crawler.zerodha_ticker import start_kite_ticker, stop_kite_ticker
    from crawler.india_price_feed import is_nse_market_open
    if not is_nse_market_open():
        return {"skipped": "market_closed"}
    stop_kite_ticker()             # kill any stale-token ticker (no-op if none)
    started = start_kite_ticker()  # rebuild on the current token
    logger.info(f"[kite_start_ticker] (re)built ticker on current token: {started}")
    return {"started": bool(started)}


# ── Price-feed watchdog: alert if candles stop being written during market hours ──

async def _candle_staleness_watchdog():
    """During NSE hours, alert (≤1×/hour) if no intraday (5m) candle has been
    written in CANDLE_STALENESS_ALERT_MIN minutes — the early-warning the system
    lacked when the feed silently froze for days. 5m is the broadest signal (whole
    universe via yfinance), so it catches a wedged worker, a dead ticker, or an
    expired Kite token alike. Cooldown now lives in integrations/alerts/router.py's
    shared Redis dedup (OPERATIONS/ERROR default 1hr), not an in-process global."""
    from crawler.india_price_feed import is_nse_market_open
    # ── Daily-coverage check (2026-08-21) ────────────────────────────────────
    # Runs BEFORE the market-hours guard, because a daily-bar gap is only
    # visible after the close and the 5m check below returns early when the
    # market is shut.
    #
    # This watchdog only ever looked at `timeframe='5m'`. It therefore had no
    # view of the daily feed at all, and a coverage collapse went unnoticed for
    # two sessions: 7,066 symbols with a 1d bar on 19 Aug, 2,500 on the 20th,
    # 4 on the 21st. Nothing alerted. It surfaced only because a trading rule
    # read a two-day-old close and reported HSCL down 12.78% against a real
    # -7.07%.
    #
    # Daily bars feed the 20-day RVOL baseline, the 20-day high, pivot levels
    # and every day-move gate — a silent gap there quietly corrupts entry
    # decisions rather than stopping them, which is the worse failure.
    try:
        from tasks._db import celery_session as _cs
        from sqlalchemy import text as _txt

        # Ask "when did the daily feed last write the WHOLE universe", not
        # "what is the newest 1d row".
        #
        # MAX(timestamp) is not the freshness signal it looks like. Several
        # tasks write 1d rows for tiny symbol sets — kite_sync_candles covers a
        # 33-name watchlist at 10:00 UTC, and the index symbols get written
        # separately — while the full ~8,000-symbol write lands in a single
        # batch the following morning. So MAX(timestamp) routinely names a date
        # holding 4 or 33 symbols, and comparing that against an 8,000-symbol
        # baseline reports a 99% "collapse" every single afternoon. An alert
        # that fires daily on healthy data trains everyone to ignore it, which
        # is worse than no alert.
        #
        # Note also that a 1d bar is stamped at IST midnight stored as naive
        # UTC (D 00:00 IST -> D-1 18:30), so `timestamp::date` reads one day
        # BEFORE the session it describes. Hence the generous day window here:
        # this checks liveness, not exact dating.
        async with _cs() as _s2:
            _cov = (await _s2.execute(_txt("""
                WITH per_day AS (
                    SELECT timestamp::date AS d, COUNT(DISTINCT symbol) AS n
                    FROM candles
                    WHERE timeframe = '1d'
                      AND timestamp >= CURRENT_DATE - 30
                    GROUP BY 1
                )
                SELECT
                  (SELECT COALESCE(MAX(n), 0) FROM per_day
                     WHERE d >= CURRENT_DATE - 4),
                  (SELECT COALESCE(MAX(n), 0) FROM per_day)
            """))).fetchone()
        _newest, _baseline = int(_cov[0] or 0), int(_cov[1] or 0)
        from utils.config import settings as _cfg_s
        _min_frac = float(getattr(_cfg_s, "DAILY_COVERAGE_MIN_FRAC", 0.5))
        if daily_coverage_is_stalled(_newest, _baseline, _min_frac):
            logger.error(
                f"[watchdog] DAILY CANDLE FEED STALLED — the best daily coverage "
                f"in the last 4 days is {_newest} symbols against a 30-day best "
                f"of {_baseline} ({_newest / _baseline:.0%}). The full-universe "
                f"daily write has not landed. RVOL baselines, 20-day highs, "
                f"day-move gates and the hub universe all read from this."
            )
    except Exception as _cov_exc:
        logger.debug(f"[watchdog] daily-coverage check failed: {_cov_exc}")

    if not is_nse_market_open():
        return {"skipped": "market_closed"}

    from utils.config import settings
    threshold = int(getattr(settings, "CANDLE_STALENESS_ALERT_MIN", 20))
    from tasks._db import celery_session
    from sqlalchemy import text
    async with celery_session() as s:
        age = (await s.execute(text(
            "SELECT extract(epoch FROM ((now() AT TIME ZONE 'utc') - max(timestamp)))/60 "
            "FROM candles WHERE timeframe='5m'"
        ))).scalar()
    if age is None:
        return {"status": "no_candles"}
    age = float(age)
    if age <= threshold:
        return {"status": "fresh", "age_min": round(age, 1)}

    # Stale — alert, with a 1-hour cooldown (shared via Redis) so a multi-hour
    # outage doesn't spam.
    msg = (
        f"⚠️ <b>Price feed stale</b> — newest 5m candle is {age:.0f} min old "
        f"(threshold {threshold} min) during market hours.\n"
        f"Likely cause: expired Kite token, dead WebSocket ticker, or a wedged "
        f"Celery worker. Check <code>/api/v1/zerodha/status</code>."
    )
    logger.error(f"[watchdog] {msg}")
    try:
        from integrations.alerts import publish, AlertEvent, AlertCategory, AlertAction, Severity, RawTextPayload
        await publish(AlertEvent(
            category=AlertCategory.OPERATIONS, action=AlertAction.ERROR, severity=Severity.CRITICAL,
            dedup_key="candle_staleness", payload=RawTextPayload(text=msg),
        ))
    except Exception as exc:
        logger.warning(f"[watchdog] telegram alert failed: {exc}")
    return {"status": "stale_alerted", "age_min": round(age, 1)}


@celery_app.task(
    name="tasks.resample_intraday_candles",
    soft_time_limit=110,
    time_limit=140,
)
def resample_intraday_candles_task(lookback_minutes: int = 180):
    """Derive 5m / 15m / 1h bars from the 1m bars already in Postgres.

    Replaces the per-symbol yfinance fetch that used to produce these
    timeframes and could not finish a pass inside its own beat interval — on
    24 Aug the newest 5m bar was 14:50 IST and the newest 1h 14:15 IST, so F4
    was scoring a session it could not see the end of. This is one SQL
    statement per timeframe over data we already hold: no network, no rate
    limit, and the coarse bars can never lag the 1m feed they are built from.

    Runs on scan_queue so it can never compete for a default-queue slot with
    the trading path. PAPER TRADING — read-only aggregation, no orders.
    """
    async def _run():
        from crawler.candle_resampler import resample_intraday
        from tasks._db import celery_session

        async with celery_session() as session:
            return await resample_intraday(
                session, lookback_minutes=lookback_minutes
            )

    result = _run_async(_run())
    logger.info(f"[resample_intraday] {result}")
    return result


@celery_app.task(name="tasks.candle_staleness_watchdog")
def candle_staleness_watchdog_task():
    """Every 5 min: warn if the live price feed has gone stale (see above)."""
    return _run_async(_candle_staleness_watchdog())


# ── Retroactive news re-tagging (one-shot, manual trigger) ───────────────────
#
# After the news ticker map expanded from 59 large-caps to the full ~9.6k NSE
# universe, every historical news_items row tagged BEFORE the upgrade still
# carries [] (or whatever the old small-map produced). This task walks the
# table in 500-row batches and re-runs `extract_tickers_from_headline` against
# the new map so the sentiment signal gets immediate historical depth.
#
# Manual trigger only — NOT in the beat schedule:
#     from tasks.india_tasks import retag_historical_news
#     retag_historical_news.delay()

async def _retag_historical_news():
    from sqlalchemy import select, or_, update, func, text as sql_text
    from tasks._db import celery_session
    from crawler.news_crawler import (
        _build_india_name_map, extract_tickers_from_headline,
    )
    from db.models import NewsItem

    _BATCH = 500
    processed = updated = skipped = errors = 0
    last_id = 0

    async with celery_session() as session:
        # Pre-warm the India name map once (TTL-cached inside the module).
        await _build_india_name_map(session)

        # "Untagged" covers three on-disk shapes we've seen for this column:
        #   - SQL NULL                  (older rows before the JSON column existed)
        #   - JSON null literal         (asyncpg sends Python None as 'null'::jsonb)
        #   - empty JSON array '[]'     (extractor returned no matches)
        # We compare with direct jsonb equality only — using jsonb_array_length
        # in a WHERE clause crashes on scalar rows because PostgreSQL evaluates
        # both sides of AND per-row regardless of jsonb_typeof.
        empty_clause = sql_text(
            "(tickers_affected IS NULL "
            " OR tickers_affected = 'null'::jsonb "
            " OR tickers_affected = '[]'::jsonb)"
        )

        total = (await session.execute(
            select(func.count(NewsItem.id)).where(empty_clause)
        )).scalar() or 0
        logger.info(f"[retag] candidates with empty tickers: {total}")

        while True:
            rows = (await session.execute(
                select(NewsItem.id, NewsItem.headline)
                .where(empty_clause, NewsItem.id > last_id)
                .order_by(NewsItem.id.asc())
                .limit(_BATCH)
            )).all()
            if not rows:
                break

            for row_id, headline in rows:
                processed += 1
                last_id = row_id
                try:
                    tickers = extract_tickers_from_headline(headline or "")
                    if not tickers:
                        skipped += 1
                        continue
                    await session.execute(
                        update(NewsItem)
                        .where(NewsItem.id == row_id)
                        .values(tickers_affected=tickers)
                    )
                    updated += 1
                except Exception as exc:
                    errors += 1
                    logger.warning(f"[retag] row {row_id} failed: {exc}")

            await session.commit()
            if processed % 1000 == 0 or not rows:
                logger.info(
                    f"[retag] progress  processed={processed}  "
                    f"updated={updated}  skipped={skipped}  errors={errors}"
                )

    logger.info(
        f"[retag] DONE  processed={processed}  updated={updated}  "
        f"skipped={skipped}  errors={errors}"
    )
    return {
        "processed": processed,
        "updated":   updated,
        "skipped":   skipped,
        "errors":    errors,
    }


@celery_app.task(name="tasks.retag_historical_news")
def retag_historical_news():
    """Re-run ticker extraction over all historical news_items rows with empty
    or NULL ``tickers_affected``. One-shot, manual trigger only."""
    return _run_async(_retag_historical_news())


# ── Weekly news retention purge — keeps news_items from growing unbounded ────
#
# Scheduled in tasks.celery_app beat as ``purge-old-news-weekly``. Retention
# default is 60 days; older rows are deleted in a single statement. Bigger
# tables can use a chunked delete loop, but this DB is small enough that the
# simple form is fine for several years.

async def _purge_old_news(days: int = 60) -> dict:
    from sqlalchemy import text as _text
    from tasks._db import celery_session
    async with celery_session() as session:
        result = await session.execute(
            _text(
                "DELETE FROM news_items "
                "WHERE crawled_at < (NOW() - (:days || ' days')::interval)"
            ).bindparams(days=str(days))
        )
        await session.commit()
        deleted = result.rowcount or 0
    logger.info(f"[purge_old_news] deleted {deleted} rows older than {days}d")
    return {"deleted": deleted, "older_than_days": days}


@celery_app.task(name="tasks.purge_old_news")
def purge_old_news_task(days: int = 60):
    """Weekly cleanup: delete news_items older than ``days`` (default 60)."""
    return _run_async(_purge_old_news(days))


# ── Daily NSE+BSE EQ instrument sync (Zerodha full dump) ────────────────────

async def _sync_nse_eq_instruments():
    """Download ALL NSE+BSE equity instruments from Zerodha and upsert into kite_instruments.

    This populates the full ~9,600 NSE EQ universe so that every stock
    automatically gets an instrument_token and daily candle ingestion.
    Without this, only the 30 hardcoded symbols in NSE_TOKENS are tracked.
    """
    from crawler.zerodha_market import sync_nse_eq_instruments
    from crawler.zerodha_market import hydrate_tokens_from_db
    from tasks._db import celery_session

    async with celery_session() as session:
        result = await sync_nse_eq_instruments(session)
        # Re-hydrate the in-memory NSE_TOKENS map so this worker
        # immediately benefits from the new symbols without a restart.
        await hydrate_tokens_from_db(session)

    logger.info(
        f"[sync_nse_eq_instruments] done — "
        f"NSE={result.get('nse_eq', 0):,}  BSE={result.get('bse_eq', 0):,}  "
        f"total={result.get('total', 0):,}"
    )
    return result


@celery_app.task(
    name="tasks.sync_nse_eq_instruments",
    soft_time_limit=600,
    time_limit=900,
)
def sync_nse_eq_instruments_task():
    """Daily 03:00 UTC (08:30 IST): sync ALL NSE+BSE EQ instruments from Zerodha's
    full instrument master into kite_instruments. Runs before hub rebuild (02:50 UTC)
    is rescheduled after this task so the universe has fresh tokens.

    This is the root fix for small-cap stocks being invisible to the system:
    once JTEKTINDIA, SAKSOFT, SIGNPOST etc. are in kite_instruments, their
    candles are automatically fetched every day and they enter hub_universe
    when their turnover qualifies.
    """
    logger.info("[sync_nse_eq_instruments] starting full NSE+BSE EQ instrument sync")
    return _run_async(_sync_nse_eq_instruments())


# ── Weekly full-NSE candle refresh (Zerodha) ─────────────────────────────────

async def _refresh_full_nse_candles(days_back: int = 7):
    from crawler.zerodha_historical import sync_full_nse_universe
    from tasks._db import celery_session
    async with celery_session() as session:
        return await sync_full_nse_universe(session, days_back=days_back, delay_sec=0.5)


@celery_app.task(
    name="tasks.refresh_full_nse_candles",
    soft_time_limit=7200,   # ~8000 syms × 0.5s ≈ 70 min; allow headroom
    time_limit=7800,
)
def refresh_full_nse_candles_task(days_back: int = 7):
    """Weekly: refresh the last ~week of daily candles for EVERY NSE EQ symbol
    via Zerodha Kite, keeping the agent's full-market universe fresh.
    PAPER TRADING — read-only market data, no orders."""
    logger.info("[refresh_full_nse_candles] starting weekly full-universe refresh")
    return _run_async(_refresh_full_nse_candles(days_back))


# ── Daily full-BSE candle refresh (Zerodha) — 2026-07-31 ─────────────────────
# Twin of _refresh_full_nse_candles above; BSE had no full-universe sync of
# any kind before this (see crawler/zerodha_historical.py::sync_full_bse_universe
# docstring — JUMBO.BO, a real BSE micro-cap DIRECT_NEWS traded on, had zero
# candle history because of this exact gap). Daily rather than weekly, per
# explicit choice over the weekly-like-NSE default.

async def _refresh_full_bse_candles(days_back: int = 7):
    from crawler.zerodha_historical import sync_full_bse_universe
    from tasks._db import celery_session
    async with celery_session() as session:
        return await sync_full_bse_universe(session, days_back=days_back, delay_sec=0.5)


@celery_app.task(
    name="tasks.refresh_full_bse_candles",
    soft_time_limit=4800,   # ~6,150 syms × 0.5s ≈ 51 min; allow headroom
    time_limit=5100,
)
def refresh_full_bse_candles_task(days_back: int = 7):
    """Daily: refresh the last ~week of daily candles for EVERY BSE EQ symbol
    via Zerodha Kite. PAPER TRADING — read-only market data, no orders."""
    logger.info("[refresh_full_bse_candles] starting daily full-BSE-universe refresh")
    return _run_async(_refresh_full_bse_candles(days_back))


# ── Long-tail intraday candle refresh (Upstox) — 2026-07-31 ──────────────────
# Everything outside hub_universe (NSE below the turnover cutoff, all of BSE)
# gets intraday coverage here instead — separate lane, separate broker (Upstox,
# not Kite), so it can't compete with or slow down the Hub universe's existing
# live-trading price cadence. See crawler/upstox_historical.py::
# sync_long_tail_intraday_upstox for the full design writeup.

async def _sync_long_tail_intraday():
    # ── Off-hours only (2026-08-21) ──────────────────────────────────────────
    # This processes ~1,918 symbols over ~8 minutes and holds one of only two
    # default-queue slots for that entire time. It is the documented
    # 2026-08-03 CPU-contention culprit, and on 21 Aug it was one of the two
    # tasks occupying both slots while fast_sl_check — the 5s stop-loss loop —
    # was being starved out entirely.
    #
    # The symbols it syncs are the LONG TAIL: outside the F1 universe and not
    # tradeable, so nothing in the live path needs them refreshed mid-session.
    # Runs on the same 30-minute cadence outside market hours, which is ample.
    # 2026-08-24: the off-hours guard above was added on 21 Aug, but the task
    # wrapper still carried the ORIGINAL `_is_india_trading_window()` gate
    # (09:15-16:00 IST). The two are very nearly complements, so between them
    # the task could only execute in the 15:30-16:00 sliver -- once a day at
    # best, against a 30-minute beat. Long-tail intraday coverage silently
    # stopped being refreshed. The wrapper gate is gone now; this function owns
    # the whole decision.
    from crawler.india_price_feed import is_nse_market_open

    now_ist = datetime.datetime.now(_IST)

    if is_nse_market_open():
        logger.info("[long_tail] skipped — market open, leaving the slot to the trading path")
        return {"skipped": "market_open"}

    # Weekdays only, and only once the session has produced something to sync.
    # Without this the 30-minute beat would fire through the small hours
    # fetching a session that has not happened yet.
    if now_ist.weekday() >= 5:
        return {"skipped": "weekend"}
    if (now_ist.hour, now_ist.minute) < (15, 30):
        return {"skipped": "pre_session_close"}

    from crawler.upstox_historical import sync_long_tail_intraday_upstox
    from tasks._db import celery_session
    async with celery_session() as session:
        return await sync_long_tail_intraday_upstox(session)


@celery_app.task(
    name="tasks.sync_long_tail_intraday",
    # 2026-08-03: was 1800/1900 (sized to the old full-universe-per-call
    # design). Now processes 1 of 4 time-boxed slices per call (~1,918 syms,
    # ~8 min) -- see sync_long_tail_intraday_upstox()'s docstring for why.
    # 900/960 is a safety net for a slow slice, not the expected runtime.
    soft_time_limit=900,
    time_limit=960,
)
def sync_long_tail_intraday_task():
    """Every ~30 min during NSE hours: intraday candles, via Upstox, for every
    NSE+BSE EQ symbol NOT already covered by the Kite-based Hub universe crawl.
    PAPER TRADING — read-only market data, no orders."""
    # NOTE: deliberately NO `_is_india_trading_window()` gate here. This task
    # is off-hours by design (see _sync_long_tail_intraday), and that window
    # (09:15-16:00 IST) is the complement of the one this task wants, so the
    # pair cancelled out and the task effectively stopped running on 21 Aug.
    logger.info("[sync_long_tail_intraday] starting long-tail intraday refresh")
    return _run_async(_sync_long_tail_intraday())


async def _rebuild_hub_universe(top_n: int | None = None, min_turnover_cr: float | None = None):
    from engine.hub_universe import rebuild_hub_universe
    from tasks._db import celery_session
    from utils.config import settings
    top_n = top_n or int(getattr(settings, "HUB_UNIVERSE_SIZE", 500))
    min_turnover_cr = min_turnover_cr if min_turnover_cr is not None else float(getattr(settings, "HUB_UNIVERSE_MIN_TURNOVER_CR", 5.0))
    async with celery_session() as session:
        return await rebuild_hub_universe(session, top_n=top_n, min_turnover_cr=min_turnover_cr)


@celery_app.task(name="tasks.rebuild_hub_universe")
def rebuild_hub_universe_task(top_n: int | None = None, min_turnover_cr: float | None = None):
    """Daily: rebuild the Hub's deep-score universe (top-N NSE equities by
    30-day avg turnover). Size from HUB_UNIVERSE_SIZE env (default 500)."""
    logger.info("[rebuild_hub_universe] starting daily universe rebuild")
    return _run_async(_rebuild_hub_universe(top_n, min_turnover_cr))


async def _refresh_isin_map():
    """Background ISIN resolution (2026-07-21 fix) — populates SymbolISINMap
    for every HubUniverse symbol not yet cached, so
    crawler/upstox_data.py::get_isin() can serve from the DB during a live
    trading decision instead of resolving (via yfinance / an
    assets.upstox.com CSV download) on that hot path. Identity resolution is
    a data-ingestion concern, not a trading-decision concern.

    Only resolves symbols missing from the table — ISINs don't change for a
    listed company, so there's nothing to refresh once a symbol is cached."""
    from tasks._db import celery_session
    from sqlalchemy import select
    from db.models import HubUniverse, SymbolISINMap
    from crawler.upstox_data import _resolve_isin_live

    async with celery_session() as session:
        universe = (await session.execute(select(HubUniverse.symbol))).scalars().all()
        existing = {
            s.upper().replace(".NS", "").replace(".BO", "")
            for s in (await session.execute(select(SymbolISINMap.symbol))).scalars().all()
        }
        missing = [s for s in universe if s.upper().replace(".NS", "").replace(".BO", "") not in existing]

        resolved = failed = 0
        for symbol in missing:
            bare = symbol.upper().replace(".NS", "").replace(".BO", "")
            result = await _resolve_isin_live(bare)
            if result:
                isin, source = result
                session.add(SymbolISINMap(symbol=bare, isin=isin, source=source))
                resolved += 1
            else:
                failed += 1
            await asyncio.sleep(0.3)  # polite to yfinance/Upstox — runs unattended overnight
        await session.commit()

    logger.info(f"[refresh_isin_map] resolved={resolved} failed={failed} (of {len(missing)} missing)")
    return {"resolved": resolved, "failed": failed, "total_missing": len(missing)}


@celery_app.task(name="tasks.refresh_isin_map")
def refresh_isin_map_task():
    """Daily: resolve + persist symbol->ISIN for any HubUniverse symbol not
    yet in SymbolISINMap. Scheduled after rebuild_hub_universe so newly-added
    universe symbols get picked up the same night."""
    logger.info("[refresh_isin_map] starting background ISIN resolution")
    return _run_async(_refresh_isin_map())


async def _backfill_hub_1d_candles():
    """Fetch yesterday's 1d candle for EVERY NSE EQ symbol in kite_instruments.

    Expanded scope (was: hub_universe only → now: ALL kite_instruments NSE EQ).
    This ensures EVERY NSE stock — including small-caps outside hub_universe —
    has fresh daily candles so:
      • hub_universe rebuild has complete 30-day turnover for ALL symbols
      • breakout_screener can scan the full NSE universe (currently 9,600 stocks)
      • small-caps like JTEKTINDIA, SAKSOFT, SIGNPOST get picked up automatically

    Runs at 3:10 AM daily — after sync_nse_eq_instruments (3:00 AM) and
    before hub rebuild (3:30 AM, rescheduled). Skips symbols whose last
    candle is already today (idempotent).

    Uses the Kite historical API, not yfinance. Measured in production:
    yfinance throughput (even after excluding bond/T-bill dead weight below)
    sustained only ~0.2-0.25 symbols/sec, and pushing concurrency higher just
    triggered Yahoo's opaque per-IP throttling (20s stalls per request,
    net throughput *dropped*). Kite's historical endpoint has a documented,
    predictable rate limit (~3 req/sec) instead of an unknown one, and it's
    the same authenticated broker connection already used elsewhere in this
    codebase for exactly this purpose (see sync_all_nse_candles). Sequential
    with a 0.35s delay stays safely under 3 req/sec — ~3,500 symbols in one
    run fits comfortably inside the time budget below.
    """
    import asyncio as _asyncio
    from sqlalchemy import text as _text
    from crawler.price_feed import save_candles_to_db
    from crawler.zerodha_historical import get_kite_candles_for_range
    from tasks._db import celery_session
    import datetime as _dt

    from crawler.zerodha_kite_lib import get_kite
    kite = get_kite()
    if not kite.access_token:
        logger.warning("[backfill_hub_1d] Zerodha not authenticated — skipping")
        return {"skipped": True, "reason": "not_authenticated"}

    # get_kite_candles_for_range() resolves each symbol's instrument_token via
    # the in-memory INSTRUMENT_CACHE, which is only populated by an explicit
    # refresh call elsewhere (e.g. the daily 08:00 IST instrument-token task).
    # Don't assume some other task already warmed it before this one runs —
    # that ordering isn't guaranteed after a restart. Idempotent, so safe to
    # call unconditionally; skip if already populated to avoid a redundant
    # full-universe download every single run.
    from crawler.zerodha_instruments import INSTRUMENT_CACHE, refresh_instrument_cache
    if not INSTRUMENT_CACHE:
        n = await refresh_instrument_cache()
        logger.info(f"[backfill_hub_1d] INSTRUMENT_CACHE was empty — refreshed {n} symbols")

    # Fetch ALL NSE + BSE EQ symbols from kite_instruments.
    #
    # Zerodha's instrument master tags government bonds/T-bills/state loans
    # (GOI TBILL, GOI LOAN, GOI STRIPS, SDL — State Development Loans) with
    # instrument_type='EQ' just like real equities, and there is no dedicated
    # segment/instrument_type to distinguish them. Measured in production: they
    # are 4,474 of the 8,203 rows this query returned (~55%) — and because their
    # tradingsymbols are numeric-coded (e.g. "182D100926-TB", "723MZ38-SG") they
    # sort alphabetically ahead of nearly every real ticker. yfinance can never
    # return data for them (they don't exist under an NSE equity ticker), so the
    # backfill spent almost its entire per-run time budget on ~4,474 guaranteed
    # failures before ever reaching most real stocks — the actual reason 87% of
    # the tradeable universe was still frozen on a June-30 candle days later.
    # Their `name` field reliably identifies them (e.g. "GOI TBILL 182D-...",
    # "SDL MZ 7.23% 2038") — no real NSE company name starts with GOI/SDL.
    #
    # BSE included as of 2026-07-28: this query, refresh_instrument_cache()
    # (NSE-only before), get_token() (didn't strip ".BO"), and
    # get_kite_candles_for_range()'s save path (forced ".NS" onto every
    # symbol) formed a 4-layer, mutually-reinforcing gap where NO BSE symbol
    # could ever get a candle -- confirmed live via ASIIL.BO/MOLDTKPAC.NS,
    # two Direct News trades taken with literally zero price history on
    # either side (chart-blind entries). All four layers fixed together;
    # this is the query-level piece.
    async with celery_session() as session:
        rows = (await session.execute(_text("""
            SELECT tradingsymbol, segment
            FROM kite_instruments
            WHERE segment IN ('NSE', 'BSE') AND instrument_type = 'EQ'
              AND name != '' AND instrument_token > 0
              AND name NOT ILIKE 'GOI %' AND name NOT ILIKE 'SDL %'
              -- Bonds/SDLs also appear with numeric-coded tradingsymbols that
              -- the name filters above miss entirely: 708GJ31.BO, 765TN30.BO
              -- and 786 others. Kite returns no daily candles for any of them,
              -- so every one is a guaranteed empty fetch. Observed live
              -- 2026-08-21: a 300-symbol chunk landed in this block and saved
              -- 36 rows. No real NSE/BSE equity ticker starts with a digit.
              AND tradingsymbol !~ '^[0-9]'
            ORDER BY tradingsymbol
        """))).all()
        _suffix = {"NSE": ".NS", "BSE": ".BO"}
        all_symbols = [f"{sym}{_suffix[segment]}" for sym, segment in rows]

        # Also include hub_universe symbols (extras not yet in kite_instruments)
        from engine.hub_universe import get_hub_universe
        hub_syms = await get_hub_universe(session)

    # Union: kite_instruments NSE + hub_universe
    symbol_set = list(dict.fromkeys(all_symbols + list(hub_syms)))  # preserve order, dedup

    # Filter: skip symbols that already have a candle from today or yesterday
    today_utc = _dt.datetime.utcnow().date()
    stale_cutoff = str(today_utc - _dt.timedelta(days=1))  # skip if last candle ≥ yesterday

    async with celery_session() as session:
        fresh = (await session.execute(_text(f"""
            SELECT DISTINCT symbol FROM candles
            WHERE timeframe = '1d' AND timestamp >= '{stale_cutoff}'
        """))).scalars().all()
    fresh_set = set(fresh)

    # Only backfill symbols that are stale (no candle since yesterday)
    stale_symbols = [s for s in symbol_set if s not in fresh_set]

    # ── Chunked + resumable (2026-08-21) ─────────────────────────────────────
    # This task used to attempt the whole stale list in one run. Measured on
    # 21 Aug: all_syms=10139, fresh=4, need_backfill=10138 -- and at the 0.35s
    # spacing below that is ~59 minutes of pure sleep before any request
    # overhead, against a 3600s soft limit. It died with SoftTimeLimitExceeded
    # at exactly 60:00 having finished nothing, every day. Daily coverage
    # decayed 7,066 -> 2,500 -> 4 symbols over three sessions while every
    # service reported healthy, because a task that always dies at the limit
    # looks identical to one that is merely slow.
    #
    # Two changes, together:
    #   1. PRIORITISE the tradeable universe. Daily bars matter for symbols the
    #      system can actually trade -- they feed the 20-day RVOL baseline, the
    #      20-day high and the day-move gates. The long tail still gets done,
    #      just after the names that matter.
    #   2. Process a bounded CHUNK per run and remember the position in Redis,
    #      so consecutive runs sweep the backlog instead of restarting it.
    #
    # The cursor is a symbol, not an index: the stale list is recomputed every
    # run (symbols become fresh as they are filled), so an index would point at
    # a different symbol each time and skip whole blocks.
    from utils.config import settings as _bf_cfg

    _chunk = int(getattr(_bf_cfg, "BACKFILL_1D_CHUNK", 300))
    _progress_key = "backfill_hub_1d:cursor"

    try:
        from engine.tactical_data_fetcher import get_f1_universe
        async with celery_session() as _s3:
            _priority = {x for x in await get_f1_universe(_s3)}
    except Exception as _pri_exc:
        logger.debug(f"[backfill_hub_1d] priority universe unavailable: {_pri_exc}")
        _priority = set()

    # Tradeable names first, everything else after, each half in stable order.
    stale_symbols = ([s for s in stale_symbols if s in _priority]
                     + [s for s in stale_symbols if s not in _priority])

    _cursor = None
    try:
        from utils.cache import get_redis
        _cursor = await get_redis().get(_progress_key)
    except Exception as _cur_exc:
        logger.debug(f"[backfill_hub_1d] cursor read failed: {_cur_exc}")

    _start = 0
    if _cursor and _cursor in stale_symbols:
        # Resume just AFTER the last symbol completed.
        _start = stale_symbols.index(_cursor) + 1
    _batch = stale_symbols[_start:_start + _chunk]
    if not _batch and stale_symbols:
        # Cursor ran off the end (or the list shrank) -- wrap to the front so a
        # stale cursor can never wedge the sweep permanently.
        _start, _batch = 0, stale_symbols[:_chunk]

    logger.info(
        f"[backfill_hub_1d] all_syms={len(symbol_set)}  fresh={len(fresh_set)}  "
        f"need_backfill={len(stale_symbols)}  "
        f"priority={len([s for s in stale_symbols if s in _priority])}  "
        f"this_run={len(_batch)} (from index {_start})"
    )
    stale_symbols = _batch

    saved_total = 0
    failed = 0
    # Kite historical rate limit is ~3 req/sec, documented and enforced by the
    # broker itself (unlike yfinance's opaque per-IP throttling). 0.35s spacing
    # stays safely under that. Sequential, not concurrent — this is the same
    # broker connection live trading uses; getting it rate-limited or flagged
    # would risk more than a slow backfill.
    _DELAY_SEC = 0.35
    to_date = _dt.date.today()
    from_date = to_date - _dt.timedelta(days=5)  # covers weekends/holidays

    async def _fetch_and_save(sym: str) -> int:
        """Fetch 1d candles for one symbol via Kite and persist. Returns count saved."""
        try:
            candles = await get_kite_candles_for_range(sym, from_date, to_date, interval="1d")
            if not candles:
                return 0
            async with celery_session() as s2:
                saved = await save_candles_to_db(candles, s2)
                await s2.commit()
                return saved
        except Exception:
            return -1  # sentinel for failure

    for sym in stale_symbols:
        r = await _fetch_and_save(sym)
        if r < 0:
            failed += 1
        else:
            saved_total += r
        await _asyncio.sleep(_DELAY_SEC)

    # Remember where this run stopped so the next one continues the sweep.
    if stale_symbols:
        try:
            from utils.cache import get_redis
            await get_redis().set(_progress_key, stale_symbols[-1], ex=86_400)
        except Exception as _w_exc:
            logger.debug(f"[backfill_hub_1d] cursor write failed: {_w_exc}")

    logger.info(
        f"[backfill_hub_1d] done — total_syms={len(symbol_set)}  this_run={len(stale_symbols)}  "
        f"saved={saved_total}  failed={failed}"
    )
    return {
        "total_symbols": len(symbol_set),
        "stale_backfilled": len(stale_symbols),
        "saved": saved_total,
        "failed": failed,
    }


@celery_app.task(name="tasks.backfill_hub_1d_candles", time_limit=3900, soft_time_limit=3600)
def backfill_hub_1d_candles_task():
    """Daily 3:10 AM: backfill 1d candles for all Hub universe symbols.

    Time budget bumped 2026-07-28 (was 2400s/2700s soft/hard) when BSE was
    added alongside NSE -- universe grew from ~3,900 to ~10,200 symbols
    (measured: 3,872 NSE + 6,325 BSE post bond-exclude). At the existing
    0.35s/symbol pacing that's ~3,570s worst-case for a cold, fully-stale
    run; steady-state runs are far faster since the stale-skip only re-fetches
    symbols without a fresh candle. A run that still hits the time limit on a
    cold start is not a regression -- next day's run picks up wherever this
    one left off (idempotent, stale-skip based) and converges within a few
    days rather than requiring one run to cover the whole expanded universe.
    """
    logger.info("[backfill_hub_1d] starting daily 1d candle backfill for Hub universe")
    return _run_async(_backfill_hub_1d_candles())


async def _refresh_priority_1d_candles():
    """Evening (17:30 IST) refresh of TODAY's 1d candle for the symbols that
    actually matter: OPEN POSITIONS + hub universe + market shortlist.

    Why this exists: the full-universe backfill runs at 03:10 UTC (08:40 IST),
    BEFORE Kite finalizes the previous day's daily candle (verified 2026-07-09:
    at 03:41 UTC Kite still only had through 7-Jul; by 14:05 IST it had 8-Jul).
    Its stale-skip (`candle >= yesterday`) also treats a symbol as fresh once it
    has *yesterday's* bar, so it never fetches *today's*. Net effect: the daily
    view for tradeable stocks ran ~2 days behind, which is what filled entries at
    stale daily closes (e.g. TBZ bought 8-Jul at the 6-Jul close of ₹198.71).

    This pass runs AFTER market close + Kite finalisation, on a SMALL priority set
    (~hundreds, not ~9,600), so it finishes fast and gets the *current* day's close
    same-day. Idempotent (save_candles_to_db upserts); no stale-skip — the set is
    small enough to always refresh.
    """
    import asyncio as _asyncio
    from sqlalchemy import text as _text
    from crawler.price_feed import save_candles_to_db
    from crawler.zerodha_historical import get_kite_candles_for_range
    from crawler.zerodha_kite_lib import get_kite
    from crawler.zerodha_instruments import INSTRUMENT_CACHE, refresh_instrument_cache
    from engine.hub_universe import get_hub_universe
    from tasks._db import celery_session
    import datetime as _dt

    kite = get_kite()
    if not kite.access_token:
        logger.warning("[refresh_1d_priority] Zerodha not authenticated — skipping")
        return {"skipped": True, "reason": "not_authenticated"}
    if not INSTRUMENT_CACHE:
        await refresh_instrument_cache()

    # Priority symbols: open positions FIRST (must never be stale), then hub
    # universe, then today's shortlist. Dedup preserving that priority order.
    async with celery_session() as session:
        open_syms = (await session.execute(_text(
            "SELECT DISTINCT symbol FROM open_positions"
        ))).scalars().all()
        shortlist = (await session.execute(_text(
            "SELECT DISTINCT symbol FROM market_shortlist "
            "WHERE created_at > now() - interval '2 days'"
        ))).scalars().all()
        hub_syms = list(await get_hub_universe(session))

    def _ns(s: str) -> str:
        return s if (s.endswith(".NS") or s.endswith(".BO")) else f"{s}.NS"

    priority: list[str] = []
    seen: set[str] = set()
    for group in (open_syms, hub_syms, shortlist):
        for s in group:
            k = _ns(s)
            if k not in seen:
                seen.add(k); priority.append(k)

    to_date = _dt.date.today()
    from_date = to_date - _dt.timedelta(days=6)   # covers a long weekend
    saved = failed = 0
    for sym in priority:
        try:
            candles = await get_kite_candles_for_range(sym, from_date, to_date, interval="1d")
            if candles:
                async with celery_session() as s2:
                    saved += await save_candles_to_db(candles, s2)
                    await s2.commit()
        except Exception:
            failed += 1
        await _asyncio.sleep(0.35)   # stay under Kite's ~3 req/sec limit

    logger.info(
        f"[refresh_1d_priority] refreshed today's 1d for {len(priority)} priority "
        f"symbols (open={len(open_syms)} hub={len(hub_syms)} shortlist={len(shortlist)}) "
        f"— saved={saved} failed={failed}"
    )
    return {"priority_symbols": len(priority), "saved": saved, "failed": failed}


@celery_app.task(name="tasks.refresh_priority_1d_candles", time_limit=900, soft_time_limit=780)
def refresh_priority_1d_candles_task():
    """Evening 17:30 IST: fetch TODAY's 1d candle for open positions + hub + shortlist."""
    logger.info("[refresh_1d_priority] starting evening priority 1d refresh")
    return _run_async(_refresh_priority_1d_candles())

@celery_app.task(name="tasks.india_weekend_reflection")
def india_weekend_reflection():
    """Runs the weekend self-reflection loop to analyze past trades."""
    _run_async(_india_weekend_reflection())

async def _india_weekend_reflection():
    from db.database import AsyncSessionLocal
    from sqlalchemy import text as _t
    import json
    import os
    
    from utils.llm import call_llm_chat

    logger.info("[weekend_reflection] Starting weekend self-reflection loop...")
    async with AsyncSessionLocal() as session:
        # Fetch the last 20 closed trades
        rows = (await session.execute(_t("""
            SELECT id, symbol, direction, entry_price, status, strategy_name, regime_at_entry, entry_reason, exit_reason, r_multiple
            FROM paper_trades
            WHERE status IN ('CLOSED_WIN', 'CLOSED_LOSS')
            ORDER BY closed_at DESC
            LIMIT 20
        """))).fetchall()

    if not rows:
        logger.info("[weekend_reflection] No closed trades found for reflection.")
        return

    trade_data = []
    wins = 0
    for r in rows:
        if "WIN" in r.status:
            wins += 1
        trade_data.append(
            f"Trade #{r.id} | {r.symbol} | {r.direction} | Entry: {r.entry_price} | Strategy: {r.strategy_name} | "
            f"Regime: {r.regime_at_entry} | R-Mult: {r.r_multiple} | Exit: {r.exit_reason} | "
            f"Reasoning: {r.entry_reason}"
        )

    win_rate = (wins / len(rows)) * 100

    prompt = (
        f"Analyze these recent {len(rows)} closed trades. Our win rate was {win_rate:.1f}%.\n"
        "Identify common patterns in our LOSING trades and what setup features led to WINS.\n"
        "Then, formulate exactly ONE concrete, actionable trading rule (max 2 sentences) to add to our global rulebook "
        "to prevent future losses based on these patterns. Format the output as JSON:\n"
        '{"analysis": "...", "new_rule": "..."}\n\n'
        "Trades:\n" + "\n".join(trade_data)
    )

    sys_prompt = "You are an elite quantitative trading coach reviewing a portfolio's recent performance."

    try:
        resp = await call_llm_chat(
            [{"role": "system", "content": sys_prompt},
             {"role": "user", "content": prompt}],
            max_tokens=500, temperature=0.3
        )

        from engine.agent.decision_engine import _parse_first_json
        data = _parse_first_json(resp)
        if data and data.get("new_rule"):
            rule = data["new_rule"]
            logger.info(f"[weekend_reflection] New Rule Generated: {rule}")
            
            rules_file = os.path.join(os.path.dirname(__file__), "..", "engine", "agent", "agent_rules.json")
            existing_rules = []
            if os.path.exists(rules_file):
                with open(rules_file, "r") as f:
                    try:
                        existing_rules = json.load(f)
                    except: pass
            
            existing_rules.append({"rule": rule, "from_trades": [r.id for r in rows]})
            existing_rules = existing_rules[-10:]
            
            with open(rules_file, "w") as f:
                json.dump(existing_rules, f, indent=4)
    except Exception as exc:
        logger.error(f"[weekend_reflection] Error: {exc}")


# ── Pre-Event Expectation Gap Strategy Task (Phase 6) ────────────────────────
# Dedicated live production wiring for the parallel Pre-Event Expectation Gap
# strategy. Operates independently of the News Strategy without interference or
# shared state. Evaluates scheduled corporate events and routes actionable
# candidates through the central execution gate with source="AI Predict" attribution.

async def _compute_pre_event_trade_levels(symbol: str, action: str, entry_price: float, session) -> dict:
    import pandas as pd
    from crawler.price_feed import get_latest_candles
    from engine.indicators import compute_indicators
    from engine.risk_manager import compute_trade_levels

    sig_ind = None
    try:
        candles_1d = await get_latest_candles(symbol, "1d", 60, session)
        if len(candles_1d) >= 20:
            df = pd.DataFrame([{
                "open": c.open, "high": c.high, "low": c.low,
                "close": c.close, "volume": c.volume, "timestamp": c.timestamp,
            } for c in candles_1d])
            sig_ind = compute_indicators(df)
    except Exception as exc:
        logger.debug(f"[pre_event_gap] {symbol}: candle fetch for SL/TP levels failed: {exc}")

    lv = compute_trade_levels(action, entry_price, sig=sig_ind)
    take_profit = lv["target_1"]

    return {
        "stop_loss": round(lv["stop_loss"], 2),
        "take_profit": round(take_profit, 2),
        "target_2": round(lv.get("target_2", take_profit), 2),
        "atr": lv.get("atr", 0.0),
    }


async def _pre_event_gap_scan_loop(min_days_until: int = 1, max_days_until: int = 15):
    from utils.config import settings
    if not settings.PRE_EVENT_GAP_ENABLED:
        return

    is_window = _is_india_trading_window()
    now_ist = datetime.datetime.now(_IST)
    is_entry_window = is_window and (now_ist.time() < datetime.time(15, 20))

    if is_window:
        try:
            from crawler.live_snapshot import fetch_live_snapshot
            await fetch_live_snapshot()
        except Exception as exc:
            logger.warning(f"[pre_event_gap] live snapshot fetch failed: {exc}")

    from tasks._db import celery_session
    from engine.pre_event_expectation_gap import scan as _pre_event_scan, PreEventDecision, STRATEGY_ID, TRADE_SOURCE
    from engine.decision_router import (
        TradeIntent, ConfidenceSource, EventDirectness, StrategyFamily, execute_trade_intent, RoutingOutcome,
    )
    from crawler.market_snapshot import get_market_snapshot

    async with celery_session() as session:
        logger.info(f"[pre_event_gap] starting event scan (window={min_days_until}..{max_days_until}d)")
        predictions = await _pre_event_scan(session, min_days_until=min_days_until, max_days_until=max_days_until)
        logger.info(f"[pre_event_gap] evaluated {len(predictions)} scheduled events")

        if not is_entry_window:
            logger.info("[pre_event_gap] outside market entry window — scan recorded, no trade execution attempted")
            return

        opened = 0
        for pred in predictions:
            if pred.decision not in (PreEventDecision.LONG, PreEventDecision.SHORT):
                continue

            # Symbol-specific lockout (2026-07-31) — ported from the 45-min
            # cooldown + two-strikes lockout in engine/agent/agent_loop.py's
            # decide(). This scan loop calls execute_trade_intent() directly
            # every ~15-20min cycle and never went through that gate, which is
            # what let AVALON/PROTEAN/MINDTECK/RPTECH/PACEDIGITK each get
            # bought and SECTOR_REVERSAL-stopped 8-10x in a single session on
            # 2026-07-31 (₹3,375 net loss, 49 trades) — nothing checked
            # "was this symbol just closed" before re-opening it.
            from db.models import PaperTrade
            from sqlalchemy import select
            today_start = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            closed_today = (await session.execute(
                select(PaperTrade.closed_at, PaperTrade.pnl).where(
                    PaperTrade.symbol == pred.symbol,
                    PaperTrade.closed_at >= today_start,
                )
            )).all()
            if closed_today:
                last_closed = max(row[0] for row in closed_today if row[0] is not None)
                if last_closed and (datetime.datetime.utcnow() - last_closed) < datetime.timedelta(minutes=45):
                    logger.info(f"[pre_event_gap] BLOCKED {pred.symbol} | COOLDOWN_LOCK: mandatory 45 min rest since last close")
                    continue
                losses_today = sum(1 for _, pnl in closed_today if pnl is not None and pnl < 0)
                if losses_today >= 2:
                    logger.info(f"[pre_event_gap] BLOCKED {pred.symbol} | TWO_STRIKES_LOCK: {losses_today} losses already today")
                    continue

            action = "BUY" if pred.decision == PreEventDecision.LONG else "SELL"
            product = "MIS" if action == "SELL" else "CNC"

            snap = await get_market_snapshot(pred.symbol)
            entry_price = snap.ltp if snap else None
            if not entry_price or entry_price <= 0:
                logger.warning(f"[pre_event_gap] {pred.symbol}: no live quote available — skipping trade execution")
                continue

            levels = await _compute_pre_event_trade_levels(pred.symbol, action, entry_price, session)

            extra_meta = {
                "source": TRADE_SOURCE,
                "reasoning_points": [
                    f"Event: {pred.event.event_type.value} on {pred.event.event_date.isoformat()}",
                    f"Nowcast profit: {pred.nowcast.profit_direction.value} (conf: {pred.nowcast.confidence:.2f})",
                    f"Price discount status: {pred.price_discount.status.value}",
                    pred.decision_reason,
                ],
                "audit_record": pred.to_audit_dict(),
            }

            intent = TradeIntent(
                strategy=STRATEGY_ID,
                symbol=pred.symbol,
                action=action,
                instrument_type="EQUITY",
                entry_price=entry_price,
                stop_loss=levels["stop_loss"],
                take_profit=levels["take_profit"],
                confidence=pred.pre_event_score,
                confidence_source=ConfidenceSource.CALCULATED,
                strategy_family=StrategyFamily.PRE_EVENT,
                event_directness=EventDirectness.NOT_APPLICABLE,
                evidence_ids=[],
                event_id=None,
                product=product,
                extra=extra_meta,
                target_2=levels["target_2"],
                atr=levels["atr"],
                confidence_factors={
                    "score_breakdown": pred.score_breakdown,
                    "source": TRADE_SOURCE,
                    # Post-event reversal check (paper_trading/trade_simulator.py's
                    # update_positions_with_current_prices) needs these to know
                    # WHEN the event resolves and WHICH direction the thesis bet
                    # on -- without this, a pre-event trade just rides the
                    # generic SL/TP for however long, even after the event it
                    # was betting on has already resolved the opposite way
                    # (found 2026-08-06, EPACKPEB.NS: result reaction gapped
                    # down against a POSITIVE nowcast, position held 6+ days
                    # on the original stop before being asked about).
                    "event_date": pred.event.event_date.isoformat(),
                    "event_type": pred.event.event_type.value,
                    "nowcast_direction": pred.nowcast.profit_direction.value,
                    "nowcast_confidence": pred.nowcast.confidence,
                },
            )

            result = await execute_trade_intent(intent, session)
            if result.outcome in (RoutingOutcome.EXECUTED_PAPER, RoutingOutcome.EXECUTED_LIVE):
                opened += 1
                logger.warning(f"✅ PRE-EVENT TRADE OPENED: {pred.symbol} {action} @ ₹{entry_price} ({result.outcome.value} via {TRADE_SOURCE})")
            else:
                logger.info(f"[pre_event_gap] trade not executed for {pred.symbol}: {result.outcome.value} — {result.reason}")

        if opened > 0:
            logger.info(f"[pre_event_gap] scan cycle opened {opened} trade(s)")


@celery_app.task(
    name="tasks.india_pre_event_gap_scan",
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    time_limit=600,
    soft_time_limit=540,
)
def run_pre_event_gap_scan(self, min_days_until: int = 1, max_days_until: int = 15):
    """Phase 6: Live production wiring for Pre-Event Expectation Gap strategy.

    Runs completely in parallel to and isolated from the News Strategy.
    Scans upcoming scheduled corporate events in the specified window, generates
    auditable PreEventPredictions, and routes actionable (LONG) predictions through
    the central execution gate with source='AI Predict' attribution.
    """
    from utils.config import settings
    from utils.runtime_config import strategy_enabled

    # Admin toggle, checked alongside the .env flag. Sync task body, so the
    # async check is driven through the same _run_async helper the task uses.
    if not _run_async(strategy_enabled("pre_event_gap")):
        logger.info("[pre_event_gap] task skipped — disabled by strategy toggle")
        return
    if not settings.PRE_EVENT_GAP_ENABLED:
        logger.debug("[pre_event_gap] task skipped — PRE_EVENT_GAP_ENABLED is False")
        return

    try:
        _run_async(_pre_event_gap_scan_loop(min_days_until, max_days_until))
    except Exception as exc:
        logger.error(f"[pre_event_gap] scan loop failed: {exc}", exc_info=True)
        try:
            self.retry(exc=exc)
        except Exception:
            pass

