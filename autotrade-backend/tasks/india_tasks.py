# Indian market Celery tasks.
#
# Task schedule (all times UTC — Celery runs in UTC):
#   india_price_scan         — every 30 s  (NSE hours only)
#   india_fii_dii_fetch      — daily 13:00 (18:30 IST)
#   india_options_analysis   — every 15 min (NSE hours only)
#   india_mutual_fund_nav    — daily 14:30 (20:00 IST, after AMFI publishes)
#   india_fundamental_update — Sunday 18:30 UTC (weekly)
#   india_trade_loop         — every 60 s  (NSE hours + 30 min)
#   train_ml_models_task     — Saturday 20:30 UTC (weekly)
#
# PAPER TRADING ONLY — virtual currency only; no real money is ever involved.

import asyncio
import datetime
from zoneinfo import ZoneInfo

from tasks.celery_app import celery_app
from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")


def _run_async(coro):
    return asyncio.run(coro)


def _is_india_trading_window() -> bool:
    """True during NSE market hours plus 30 minutes after close (9:15–16:00 IST)."""
    now = datetime.datetime.now(_IST)
    if now.weekday() >= 5:          # Saturday or Sunday
        return False
    h, m = now.hour, now.minute
    return ((h, m) >= (9, 15)) and ((h, m) <= (16, 0))


# ── 1. india_price_scan — every 30 s ─────────────────────────────────────────

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


@celery_app.task(name="tasks.india_price_scan")
def india_price_scan():
    """Fetch OHLCV candles + NIFTY/SENSEX/BANKNIFTY/VIX snapshots. Every 30 s."""
    _run_async(_india_price_scan())


# ── 2. india_fii_dii_fetch — daily 13:00 UTC (18:30 IST) ─────────────────────

async def _india_fii_dii_fetch():
    from crawler.fii_dii_crawler import fetch_fii_dii_data, save_fii_dii_to_db
    from tasks._db import celery_session

    async with celery_session() as session:
        data = await fetch_fii_dii_data(session)
        await save_fii_dii_to_db(data, session)
        await session.commit()

    logger.info(
        f"[india_fii_dii] "
        f"fii_net={data.get('fii_net_buy', 0):+,.0f} Cr  "
        f"dii_net={data.get('dii_net_buy', 0):+,.0f} Cr  "
        f"direction={data.get('market_direction', '?')}"
    )


@celery_app.task(name="tasks.india_fii_dii_fetch")
def india_fii_dii_fetch():
    """Fetch and persist daily FII/DII flow data from NSE. Daily at 6:30 PM IST."""
    logger.info("[india_fii_dii] Starting")
    _run_async(_india_fii_dii_fetch())


# ── 3. india_options_analysis — every 15 min ─────────────────────────────────

async def _india_options_analysis():
    from crawler.india_price_feed import is_nse_market_open
    from crawler.options_chain import run_options_analysis
    from tasks._db import celery_session

    if not is_nse_market_open():
        logger.info("[india_options] NSE closed — skipping")
        return

    async with celery_session() as session:
        results = await run_options_analysis(session)
        await session.commit()

    for sym, res in results.items():
        if "error" in res:
            logger.warning(f"[india_options] {sym}: {res['error']}")
        else:
            logger.info(
                f"[india_options] {sym}  "
                f"pcr={res.get('pcr', '?')}  "
                f"max_pain={res.get('max_pain', '?')}  "
                f"score={res.get('options_score', '?')}"
            )


@celery_app.task(name="tasks.india_options_analysis")
def india_options_analysis():
    """Fetch NIFTY + BANKNIFTY options chains and persist snapshots. Every 15 min."""
    logger.info("[india_options] Starting")
    _run_async(_india_options_analysis())


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


@celery_app.task(name="tasks.india_fundamental_update")
def india_fundamental_update():
    """Weekly fundamental data refresh (PE, ROE, promoter holding…) for all NSE stocks."""
    logger.info("[india_fundamentals] Starting weekly refresh")
    _run_async(_india_fundamental_update())


# ── 6. india_trade_loop — every 60 s ─────────────────────────────────────────

async def _india_trade_loop():
    from sqlalchemy import select

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
    # Kill-switch: when scanner_enabled=false in runtime_settings, the agent
    # runs solo and the SCAN paper trader stays silent.
    if not getattr(_cfg, "SCANNER_ENABLED", True):
        logger.info("[india_trade_loop] scanner disabled (SCANNER_ENABLED=false) — agent runs solo")
        return

    now_ist   = datetime.datetime.now(_IST)
    is_window = _is_india_trading_window()
    is_paper  = getattr(_cfg, "PAPER_MODE", True)
    logger.info(
        f"[india_trade_loop] NSE market status: {'OPEN' if is_window else 'CLOSED'} "
        f"— IST time: {now_ist.strftime('%H:%M:%S')}"
        + (" [paper mode — running outside hours]" if not is_window and is_paper else "")
    )
    if not is_window and not is_paper:
        return

    async with celery_session() as session:

        # Step 1: close SL/TP hits, refresh unrealised PnL
        auto_closed = await update_positions_with_current_prices(session)
        if auto_closed:
            logger.info(
                f"[india_trade_loop] {len(auto_closed)} position(s) auto-closed"
            )

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

        hub_subq = (
            select(
                MasterIntelligenceScore.symbol,
                MasterIntelligenceScore.master_score,
                MasterIntelligenceScore.signal,
            )
            .distinct(MasterIntelligenceScore.symbol)
            .where(
                MasterIntelligenceScore.is_blocked == False,
                MasterIntelligenceScore.signal.in_(_ACTIONABLE),
                MasterIntelligenceScore.symbol.like("%.NS"),
            )
            .order_by(
                MasterIntelligenceScore.symbol,
                MasterIntelligenceScore.scored_at.desc(),
            )
        ).subquery()
        hub_rows = (await session.execute(select(hub_subq))).all()

        candidates: list[dict] = [
            {
                "symbol": r.symbol, "score": float(r.master_score), "signal": r.signal,
                "source": "hub", "sector": SECTOR_MAP.get(r.symbol.replace(".NS", ""), ""),
                "rsi": None,
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

        signals: list = []
        for c in candidates:
            conf = min(100.0, abs(c["score"]))
            if conf < conf_threshold:
                continue
            action = "BUY" if "BUY" in c["signal"] else "SELL"

            # Current price: live cache → last candle close
            sym_base = c["symbol"].replace(".NS", "")
            cached = PRICE_CACHE.get(c["symbol"]) or PRICE_CACHE.get(sym_base)
            entry_price = float(cached.get("price", 0) if isinstance(cached, dict) else getattr(cached, "price", 0) if cached else 0)
            if entry_price <= 0:
                try:
                    last_candles = await get_latest_candles(c["symbol"], "1d", 1, session)
                    entry_price = float(last_candles[-1].close) if last_candles else 0.0
                except Exception:
                    entry_price = 0.0
            if entry_price <= 0:
                continue   # can't size a trade without a price

            # Provisional levels for ranking only — REAL dynamic SL/targets are
            # computed below (compute_indicators → compute_trade_levels) for just
            # the top candidates, so we don't recompute indicators for all ~70.
            if action == "BUY":
                stop_loss, take_profit = entry_price * 0.95, entry_price * 1.10
            else:
                stop_loss, take_profit = entry_price * 1.05, entry_price * 0.90

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
                regime="UNKNOWN",
                timeframe="1d",
            ))

        actionable = [s for s in signals if s.action in ("BUY", "SELL")]
        logger.info(
            f"[india_trade_loop] candidates={len(candidates)}  "
            f"above_{conf_threshold:.0f}%={len(actionable)}"
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

        # Step 4b: compute REAL dynamic SL/targets for the pool.
        from engine.indicators import compute_indicators
        from engine.risk_manager import compute_trade_levels
        import pandas as pd
        for signal in level_pool:
            try:
                candles = await get_latest_candles(signal.symbol, "1d", 200, session)
                sig_ind = None
                if len(candles) >= 20:
                    df = pd.DataFrame([{"open": c.open, "high": c.high, "low": c.low,
                        "close": c.close, "volume": c.volume, "timestamp": c.timestamp}
                        for c in candles])
                    sig_ind = compute_indicators(df)
                lv = compute_trade_levels(signal.action, signal.entry_price, sig=sig_ind)
                signal.stop_loss = lv["stop_loss"]
                signal.take_profit = lv["target_1"]   # T1 = first checkpoint / trailing trigger
                signal.target_2 = lv["target_2"]      # final target — position rides here
                signal.atr = lv["atr"]
                risk = abs(signal.entry_price - lv["stop_loss"])
                signal.risk_reward_ratio = round(abs(lv["target_2"] - signal.entry_price) / risk, 2) if risk > 0 else 0.0
                # Build rich expert note — replaces the simple one-liner
                try:
                    from integrations.trade_explainer import build_expert_note
                    hub_dict = getattr(signal, "hub_scores", None) or {}
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
                        regime=getattr(signal, "regime", "UNKNOWN"),
                    )
                    signal.reasoning_points = [expert]
                except Exception:
                    signal.reasoning_points.append(
                        f"Trade levels [{lv['source']}]: SL ₹{lv['stop_loss']} · T1 ₹{lv['target_1']} · T2 ₹{lv['target_2']}"
                        + (f" · ATR ₹{lv['atr']}" if lv['atr'] else "")
                    )
            except Exception as exc:
                logger.debug(f"[india_trade_loop] {signal.symbol} level calc failed: {exc}")

        # Step 5: current wallet state
        summary        = await VirtualWallet.get_summary(session)
        balance        = summary["balance"]
        pos_result     = await session.execute(select(OpenPosition))
        open_positions = list(pos_result.scalars().all())

        # Step 6: work down the ranked pool, opening until the risk budget / cash
        # buffer (inside validate_signal) or the per-cycle cap stops us.
        opened = 0
        for signal in level_pool:
            if opened >= max_new:
                break
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

            pos_size       = calculate_position_size(signal, balance)
            trade          = await open_paper_trade(signal, pos_size, session)
            balance       -= pos_size["usd_value"] * 0.1
            opened        += 1
            pos_result     = await session.execute(select(OpenPosition))
            open_positions = list(pos_result.scalars().all())

            explanation  = await generate_trade_explanation(signal)
            notification = format_paper_trade_notification(trade, explanation)
            logger.info(notification)

        logger.info(f"[india_trade_loop] opened {opened} new position(s) this cycle")

        # Step 8: persist daily performance snapshot
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


@celery_app.task(name="tasks.india_tasks.refresh_zerodha_instruments")
def refresh_zerodha_instruments():
    """Download fresh NSE instrument master from Kite daily before market open."""
    _run_async(_refresh_zerodha_instruments())


# ── 10. Zerodha token expiry check — daily 06:05 IST ─────────────────────────

async def _check_zerodha_token():
    from crawler.zerodha_client import clear_kite_token, get_kite_client
    from utils.config import settings

    if not settings.ZERODHA_ACCESS_TOKEN:
        return

    kite = get_kite_client()
    try:
        await kite.get_profile()
        logger.info("[zerodha] Token still valid")
    except Exception:
        clear_kite_token()
        logger.warning(
            "[zerodha] Token expired at 6 AM — user must re-login via "
            "/api/v1/zerodha/login-url"
        )


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

@celery_app.task(name="tasks.seed_calendar_events")
def seed_calendar_events_task():
    """Seeds market calendar with F&O expiries, RBI, holidays, IPOs, earnings.
    Runs daily at 7 AM IST = 1:30 AM UTC.
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


@celery_app.task(name="tasks.run_agent_cycle")
def run_agent_cycle_task():
    """Run one AI agent evaluation cycle on each 15-min bar close."""
    async def _run():
        from db.database import get_db
        from engine.agent.agent_loop import run_agent_cycle
        async for session in get_db():
            result = await run_agent_cycle(session)
            logger.info(
                f"[agent_cycle] scanned={result.get('symbols_scanned',0)} "
                f"decisions={result.get('decisions',0)} "
                f"mode={'PAPER' if result.get('paper_mode') else 'LIVE'} "
                f"status={result.get('status')}"
            )
            break

    asyncio.run(_run())
    return {"status": "done"}


@celery_app.task(
    name="tasks.run_master_intelligence_cycle",
    soft_time_limit=1080,  # ~758 symbols × candle-load + indicators; raise from the 300s default
    time_limit=1200,
)
def run_master_intelligence_cycle():
    """Master brain cycle: build unified context, score the NSE universe,
    drive the agent on top opportunities, score MFs, log the cycle."""
    import pandas as pd

    async def _run():
        from datetime import datetime
        from db.database import get_db
        from engine.intelligence_hub import (
            build_master_context, score_universe, persist_scores,
            _get_sector_for_symbol,
        )
        from engine.agent.agent_loop import (
            _get_portfolio, _is_market_hours, _is_trading_day,
        )
        from engine.agent.execution import AgentExecutionManager
        from engine.agent.selector import StrategySelectorAgent
        from engine.agent.decision_engine import DecisionEngine
        from engine.agent.risk_manager import RiskManagerAgent
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

        async for session in get_db():
            cycle_log = HubCycleLog(cycle_start=cycle_start, bar_time=cycle_start, status="running")
            session.add(cycle_log)
            await session.commit()

            try:
                ctx = await build_master_context(portfolio, session)
                logger.info(
                    f"[hub] context: macro_bias={ctx.macro.total_macro_bias:+d} "
                    f"vix={ctx.macro.india_vix:.1f} mood={ctx.macro.nse_market_mood} "
                    f"news={len(ctx.news.scores_by_symbol)} earnings={len(ctx.earnings.tones_by_symbol)}"
                )

                from engine.hub_universe import get_hub_universe
                universe = await get_hub_universe(session)
                logger.info(f"[hub] scoring universe of {len(universe)} symbols")
                # Daily candles: the 500-name universe is backfilled at 1d (only
                # the ~22 legacy large-caps have live 1h bars). Score on '1d' so
                # the whole universe is covered, not just the hourly-fed names.
                scored = await score_universe(universe, ctx, session, timeframe="1d")
                await persist_scores(scored, cycle_start, session)

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
                    executor = AgentExecutionManager()
                    selector = StrategySelectorAgent()
                    de       = DecisionEngine()
                    rm       = RiskManagerAgent(portfolio.to_risk_ctx())

                    await executor.check_and_close_positions(portfolio, PRICE_CACHE, session)

                    # Exit positions whose sector turned strongly bearish
                    for sym, pos in list(portfolio.open_positions.items()):
                        sec = _get_sector_for_symbol(sym)
                        if ctx.sectors.sector_moods.get(sec) == "STRONGLY_BEARISH":
                            price = (PRICE_CACHE.get(sym, {}) or {}).get("price", 0) or 0
                            if price > 0:
                                portfolio.close_position(sym, price)
                                logger.warning(f"[hub] exited {sym}: sector {sec} STRONGLY_BEARISH")

                    tried = 0
                    for stock in scored:
                        if stock.is_blocked:
                            continue
                        if stock.signal not in ("STRONG_BUY", "BUY"):
                            break
                        if tried >= 10 or decisions_made >= settings.AGENT_MAX_NEW_ENTRIES_DAY:
                            break
                        tried += 1
                        try:
                            candles = await get_latest_candles(stock.symbol, "15m", 300, session)
                            if not candles or len(candles) < 50:
                                continue
                            cs = sorted(candles, key=lambda c: c.timestamp)
                            df = pd.DataFrame([{
                                "open": float(c.open), "high": float(c.high), "low": float(c.low),
                                "close": float(c.close), "volume": float(c.volume),
                                "timestamp": c.timestamp,
                            } for c in cs])
                            df.set_index("timestamp", inplace=True)

                            candidate = selector.propose(
                                stock.symbol, df, stock.features,
                                macro_bias=ctx.macro.total_macro_bias,
                                fund_grade=stock.fund_grade,
                            )
                            if candidate is None:
                                continue
                            decision, _reject = de.fuse(
                                symbol=stock.symbol, candidate=candidate, regime=stock.regime,
                                macro_bias=ctx.macro.total_macro_bias, fund_score=0,
                                fund_grade=stock.fund_grade, equity=portfolio.equity,
                            )
                            if decision is None:
                                if _reject:
                                    logger.debug(f"[hub] {stock.symbol} fuse-filtered: {_reject}")
                                continue
                            ok, why = rm.can_take_trade(candidate, portfolio.equity)
                            if not ok:
                                logger.info(f"[hub] blocked {stock.symbol}: {why}")
                                continue
                            order_id = await executor.execute(decision, session)
                            if order_id:
                                portfolio.add_position(decision)
                                # Multi-target exit keys (mirror agent_loop._process_symbol)
                                _sym  = decision.symbol
                                _risk = abs(decision.entry - decision.stop)
                                portfolio.open_positions[_sym]["target1"]      = round(decision.entry + 1.0 * _risk, 2)
                                portfolio.open_positions[_sym]["target2"]      = round(decision.entry + 2.0 * _risk, 2)
                                portfolio.open_positions[_sym]["partial_done"] = False
                                portfolio.open_positions[_sym]["trailing_sl"]  = None
                                portfolio.open_positions[_sym]["entry_ts"]     = datetime.utcnow().isoformat()
                                decisions_made += 1
                                logger.info(
                                    f"[hub] TRADE {decision.action} {decision.qty} {stock.symbol} "
                                    f"score={stock.master_score:.1f} conf={decision.confidence}%"
                                )
                        except Exception as exc:
                            logger.error(f"[hub] exec error {stock.symbol}: {exc}")

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
        from engine.agent.agent_loop import _get_portfolio, _executor, eod_reconcile

        portfolio = _get_portfolio()
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


@celery_app.task(name="tasks.kite_start_ticker")
def kite_start_ticker_task():
    """Start the KiteTicker just before market open (03:45 UTC / 09:15 IST)."""
    from utils.config import settings
    if not settings.ZERODHA_ENABLED:
        return {"skipped": True}
    from crawler.zerodha_ticker import start_kite_ticker, is_ticker_running
    from crawler.india_price_feed import is_nse_market_open
    if not is_nse_market_open():
        return {"skipped": "market_closed"}
    if is_ticker_running():
        return {"skipped": "already_running"}
    started = start_kite_ticker()
    logger.info(f"[kite_start_ticker] Started: {started}")
    return {"started": bool(started)}


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
