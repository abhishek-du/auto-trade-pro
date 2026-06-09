"""Main Agent Loop — orchestrates the per-bar decision cycle.

Reference: trading_agent/main.py → evaluate_universe()
Runs on every 15-minute bar close via Celery beat.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from engine.agent.analyzer        import MarketAnalyzerAgent
from engine.agent.selector        import StrategySelectorAgent
from engine.agent.fundamentals    import FundamentalsAgent
from engine.agent.macro           import MacroSectorAgent
from engine.agent.risk_manager    import RiskManagerAgent
from engine.agent.decision_engine import DecisionEngine
from engine.agent.execution       import AgentExecutionManager
from engine.agent.portfolio_context import AgentPortfolioContext
from utils.config import settings
from utils.logger import logger

# ── Module-level singletons ───────────────────────────────────────────────────

_analyzer   = MarketAnalyzerAgent()
_selector   = StrategySelectorAgent()
_fund_agent = FundamentalsAgent()
_macro      = MacroSectorAgent()
_decision   = DecisionEngine()
_executor   = AgentExecutionManager()

# Shared in-memory portfolio (paper mode uses this; live mode syncs from Kite)
_portfolio: AgentPortfolioContext | None = None
_portfolio_hydrated: bool = False


def _get_portfolio() -> AgentPortfolioContext:
    global _portfolio
    if _portfolio is None:
        _portfolio = AgentPortfolioContext(
            equity=settings.AGENT_EQUITY,
            cash=settings.AGENT_EQUITY,
        )
    return _portfolio


async def _hydrate_portfolio_from_db(
    portfolio: AgentPortfolioContext,
    session: AsyncSession,
) -> None:
    """On first cycle after a restart, reload open positions from DB.

    Without this, the in-memory portfolio is empty after every restart, so:
    - ALREADY_IN_POSITION check never fires → same stock bought N times
    - check_and_close_positions finds nothing → stops/targets never trigger
    Also deduplicates: if the same symbol has multiple open DB rows (caused by
    a previous restart bug), the most recent row is kept and older ones are
    closed at entry price (zero P&L, exit_reason=DUPLICATE_CLEANUP).
    """
    global _portfolio_hydrated
    if _portfolio_hydrated:
        return

    from db.models import AgentTrade
    from sqlalchemy import select as _sel

    try:
        open_trades = (await session.execute(
            _sel(AgentTrade)
            .where(AgentTrade.exit_price == None)
            .order_by(AgentTrade.entry_ts.asc())
        )).scalars().all()

        # Keep only the most recent open trade per symbol
        latest: dict[str, AgentTrade] = {}
        for trade in open_trades:
            latest[trade.symbol] = trade  # ascending order → last write = most recent

        # Close older duplicates in DB
        for trade in open_trades:
            if latest[trade.symbol].id != trade.id:
                trade.exit_price  = trade.entry_price
                trade.exit_ts     = datetime.utcnow()
                trade.exit_reason = "DUPLICATE_CLEANUP"
                trade.pnl         = 0.0
                trade.pnl_pct     = 0.0
                session.add(trade)

        if open_trades:
            await session.commit()

        # Hydrate in-memory portfolio with the unique open positions
        capital_locked = 0.0
        for sym, trade in latest.items():
            if sym not in portfolio.open_positions:
                risk = abs(trade.entry_price - trade.stop_price)
                portfolio.open_positions[sym] = {
                    "side":          trade.side,
                    "entry":         trade.entry_price,
                    "stop":          trade.stop_price,
                    "target":        trade.target_price,
                    "qty":           trade.qty,
                    "strategy":      trade.strategy,
                    "target1":       round(trade.entry_price + risk, 2),
                    "target2":       round(trade.entry_price + 2 * risk, 2),
                    "partial_done":  False,
                    "trailing_sl":   None,
                    "entry_ts":      trade.entry_ts.isoformat() if trade.entry_ts else None,
                    "product":       trade.product,
                }
                capital_locked += trade.qty * trade.entry_price

        # Set cash to reflect what's actually been deployed from the virtual account.
        # If positions already over-used the equity (old bug), cap cash at 0.
        portfolio.cash = max(0.0, portfolio.equity - capital_locked)

        dupes_closed = len(open_trades) - len(latest)
        logger.info(
            f"[agent] portfolio hydrated from DB: {len(latest)} open positions, "
            f"capital locked ₹{capital_locked:,.0f}, cash remaining ₹{portfolio.cash:,.0f}"
            + (f", {dupes_closed} duplicate(s) cleaned up" if dupes_closed else "")
        )
    except Exception as exc:
        logger.warning(f"[agent] portfolio hydration failed: {exc}")

    _portfolio_hydrated = True


def _is_market_hours() -> bool:
    now = datetime.now().time()
    start_h, start_m = map(int, settings.AGENT_SESSION_START.split(":"))
    end_h,   end_m   = map(int, settings.AGENT_SESSION_END.split(":"))
    return dtime(start_h, start_m) <= now <= dtime(end_h, end_m)


def _is_trading_day() -> bool:
    return datetime.now().weekday() < 5  # Mon-Fri


def _is_mis_squareoff_window() -> bool:
    """True from MIS_SQUAREOFF_TIME until session end (3:15–3:30 PM IST by default).

    NSE/BSE rule: MIS (intraday) positions MUST be closed before 3:20 PM IST.
    Zerodha auto-squares at 3:20 PM — we initiate at 3:15 to avoid market-order
    slippage from the broker's forced square-off.
    """
    now = datetime.now().time()
    sq_h, sq_m = map(int, settings.AGENT_MIS_SQUAREOFF_TIME.split(":"))
    end_h, end_m = map(int, settings.AGENT_SESSION_END.split(":"))
    return dtime(sq_h, sq_m) <= now <= dtime(end_h, end_m)


async def run_agent_cycle(session: AsyncSession, force: bool = False) -> dict:
    """Top-level entry point called by the Celery task.

    force=True bypasses the enabled flag and market-hours check — used by the
    manual trigger button and always allowed in paper trading mode.
    """
    is_paper = getattr(settings, "PAPER_MODE", True)

    if not force and not settings.AGENT_ENABLED:
        return {"status": "disabled"}

    if not force and not _is_trading_day():
        return {"status": "non_trading_day"}

    if not force and not _is_market_hours():
        return {"status": "outside_market_hours"}

    portfolio = _get_portfolio()

    # Reload open positions from DB on first cycle after a restart.
    # This prevents the same stock from being bought multiple times and ensures
    # stop/target exits fire correctly even after a backend restart.
    await _hydrate_portfolio_from_db(portfolio, session)

    # Check stop/target on all open positions first
    from crawler.live_prices import PRICE_CACHE
    await _executor.check_and_close_positions(portfolio, PRICE_CACHE, session)

    # NSE/BSE Rule: MIS (intraday) positions must be squared off before 3:20 PM IST.
    # Zerodha auto-squares at 3:20 PM with market orders (bad fill). We close at
    # AGENT_MIS_SQUAREOFF_TIME (default 3:15 PM) with limit orders for better pricing.
    if _is_mis_squareoff_window():
        mis_symbols = [
            sym for sym, pos in portfolio.open_positions.items()
            if pos.get("product", "CNC") == "MIS"
        ]
        if mis_symbols:
            logger.info(
                f"[agent] MIS square-off window — closing {len(mis_symbols)} "
                f"intraday position(s): {mis_symbols}"
            )
            for sym in mis_symbols:
                pos = portfolio.open_positions.get(sym, {})
                price_data = PRICE_CACHE.get(sym, {})
                price = float(price_data.get("price", 0) or pos.get("entry", 0))
                if price > 0:
                    pnl = portfolio.close_position(sym, price)
                    await _executor._record_exit(sym, price, "MIS_SQUAREOFF", pnl, session)
                    logger.info(
                        f"[agent] MIS squared off {sym} @ ₹{price:.2f} | pnl=₹{pnl:,.2f}"
                    )

    # Build scan universe from market shortlist (BUY-signaled stocks from the
    # full 9,600-symbol scanner) + the hardcoded large-cap fallback.
    # The shortlist is the right source because it already did the heavy work of
    # filtering by volume, score, and signal — so the agent scans quality stocks,
    # not just 22 large caps that may all be in RANGE regime simultaneously.
    universe = await _build_scan_universe(session)
    results   = []
    skipped   = 0

    # Pre-fetch hub scores for the whole universe in one call (avoids N+1 queries)
    hub_scores: dict[str, dict] = await _fetch_hub_scores(universe, session)

    max_pos = getattr(settings, "AGENT_MAX_POSITIONS", 15)
    for symbol in universe:
        if len(portfolio.open_positions) >= max_pos:
            logger.info(f"[agent] MAX_POSITIONS cap ({max_pos}) reached — stopping new entries")
            break
        try:
            result = await _process_symbol(
                symbol, portfolio, session,
                hub_info=hub_scores.get(symbol) or hub_scores.get(symbol.replace(".NS", "")),
            )
            if result:
                results.append(result)
            else:
                skipped += 1
        except Exception as exc:
            logger.warning(f"[agent] cycle error on {symbol}: {exc}")
            skipped += 1

    return {
        "status":           "ok",
        "cycle_ts":         datetime.utcnow().isoformat(),
        "paper_mode":       settings.AGENT_PAPER_MODE,
        "symbols_scanned":  len(universe),
        "decisions":        len(results),
        "skipped":          skipped,
        "portfolio": {
            "equity":             portfolio.equity,
            "cash":               round(portfolio.cash, 2),
            "open_positions":     len(portfolio.open_positions),
            "daily_pnl_pct":      round(portfolio.daily_pnl_pct * 100, 2),
            "weekly_pnl_pct":     round(portfolio.weekly_pnl_pct * 100, 2),
        },
        "decisions_data": results,
    }


async def _process_symbol(
    symbol: str,
    portfolio: AgentPortfolioContext,
    session: AsyncSession,
    hub_info: dict | None = None,
) -> dict | None:

    # 1. Get candle data from DB
    from crawler.price_feed import get_latest_candles

    candles = await get_latest_candles(symbol, settings.AGENT_TIMEFRAME, 300, session)
    if not candles or len(candles) < settings.AGENT_WARMUP_BARS:
        return None

    candles_sorted = sorted(candles, key=lambda c: c.timestamp)
    df = pd.DataFrame([{
        "open":      float(c.open),
        "high":      float(c.high),
        "low":       float(c.low),
        "close":     float(c.close),
        "volume":    float(c.volume),
        "timestamp": c.timestamp,
    } for c in candles_sorted])
    df.set_index("timestamp", inplace=True)

    # 2. Compute market features
    try:
        features = _analyzer.compute_features(df)
    except Exception as exc:
        logger.debug(f"[agent] features failed for {symbol}: {exc}")
        return None

    # Inject hub composite score + signal so HubSignalStrategy can use them
    if hub_info:
        features.hub_composite_score = hub_info.get("composite_score") or hub_info.get("master_score")
        features.hub_signal          = hub_info.get("signal", "HOLD")
    else:
        features.hub_composite_score = None
        features.hub_signal          = "HOLD"

    # 3. Macro and fundamentals (cached)
    macro_bias          = _macro.bias(symbol)
    fund_score, fund_grade = await _fund_agent.get_cached_grade(symbol)

    # 4a. Hub 7-Factor override — primary signal source.
    #     Fetch the latest master_intelligence_score (within 2 hours).
    #     If a fresh, above-threshold score exists, use it directly and skip
    #     the strategy selector. This gives every trade the holistic 7-factor
    #     view (technical + news + sector + macro + earnings + fundamentals +
    #     options) while retaining all Varsity risk-management downstream.
    from engine.agent.decision_engine import fetch_hub_candidate
    candidate = await fetch_hub_candidate(symbol, features, session)
    hub_override = candidate is not None

    # 4b. Fallback — technical-only strategy selector when no fresh Hub score.
    if not hub_override:
        candidate = _selector.propose(symbol, df, features, macro_bias, fund_grade)

    # 5. Decision fusion (risk + position sizing + bear-case check)
    decision = _decision.fuse(
        symbol=symbol,
        candidate=candidate,
        regime=features.regime,
        macro_bias=macro_bias,
        fund_score=fund_score,
        fund_grade=fund_grade,
        equity=portfolio.equity,
    )

    if decision is None:
        skip_reason = (
            f"no_qualifying_setup" if candidate is None
            else f"decision_filtered:conf={candidate.confidence}"
        )
        logger.debug(
            f"[agent] SKIP {symbol} | {skip_reason} | regime={features.regime}"
            + (" | hub_override" if hub_override else "")
        )
        return None

    # 6. Risk Manager veto
    risk_ok, risk_reason = RiskManagerAgent(portfolio.to_risk_ctx()).can_take_trade(
        candidate=candidate if candidate else decision,
        equity=portfolio.equity,
    )

    if not risk_ok:
        logger.info(f"[agent] BLOCKED {symbol} | {risk_reason} | {decision.strategy}")
        await _log_skipped_decision(symbol, decision, risk_reason, session)
        return None

    # 7. Execute
    order_id = await _executor.execute(decision, session)

    if order_id:
        portfolio.add_position(decision)
        _sym  = decision.symbol
        _risk = abs(decision.entry - decision.stop)
        portfolio.open_positions[_sym]["target1"]      = round(decision.entry + 1.0 * _risk, 2)
        portfolio.open_positions[_sym]["target2"]      = round(decision.entry + 2.0 * _risk, 2)
        portfolio.open_positions[_sym]["partial_done"] = False
        portfolio.open_positions[_sym]["trailing_sl"]  = None
        portfolio.open_positions[_sym]["entry_ts"]     = datetime.utcnow().isoformat()
        # Track product so MIS square-off sweep can identify intraday positions
        portfolio.open_positions[_sym]["product"]      = getattr(decision, "product", "CNC")

    return decision.to_dict()


async def _build_scan_universe(session: AsyncSession) -> list[str]:
    """Return the agent's scan universe.

    Priority:
      1. Market shortlist BUY/STRONG_BUY rows (scanner already ranked them)
      2. User watchlist additions
      3. Hard-coded NSE large-cap fallback (ensures we always scan something)

    Deduplication is applied; result is capped at 150 symbols to avoid
    very long cycles when the shortlist is large.
    """
    from db.models import MarketShortlist, UserWatchlist
    from sqlalchemy import select as _sel

    seen: set[str] = set()
    universe: list[str] = []

    # 1. BUY-signaled stocks from the latest market shortlist
    try:
        rows = (await session.execute(
            _sel(MarketShortlist.symbol, MarketShortlist.signal, MarketShortlist.master_score)
            .where(MarketShortlist.signal.in_(["BUY", "STRONG_BUY", "HOLD"]))
            .order_by(MarketShortlist.master_score.desc())
            .limit(120)
        )).all()
        for row in rows:
            sym = row.symbol if row.symbol.endswith(".NS") else row.symbol + ".NS"
            if sym not in seen:
                seen.add(sym)
                universe.append(sym)
    except Exception as exc:
        logger.warning(f"[agent] shortlist fetch failed: {exc}")

    # 2. User priority watchlist
    try:
        wl_rows = (await session.execute(
            _sel(UserWatchlist.symbol)
        )).scalars().all()
        for sym in wl_rows:
            s = sym if sym.endswith(".NS") else sym + ".NS"
            if s not in seen:
                seen.add(s)
                universe.append(s)
    except Exception:
        pass

    # 3. Fallback — large-cap hardcoded list (in case DB is empty / first run)
    for sym in settings.nse_symbols:
        if sym not in seen:
            seen.add(sym)
            universe.append(sym)

    logger.info(f"[agent] scan universe: {len(universe)} symbols "
                f"({min(len(universe), 120)} from shortlist)")
    return universe[:150]


async def _fetch_hub_scores(universe: list[str], session: AsyncSession) -> dict[str, dict]:
    """Fetch hub composite scores + signals for all universe symbols in one query."""
    from db.models import MarketShortlist
    from sqlalchemy import select as _sel

    bare_symbols = [s.replace(".NS", "") for s in universe]
    ns_symbols   = [s if s.endswith(".NS") else s + ".NS" for s in universe]

    try:
        rows = (await session.execute(
            _sel(
                MarketShortlist.symbol,
                MarketShortlist.master_score,
                MarketShortlist.signal,
            ).where(MarketShortlist.symbol.in_(bare_symbols + ns_symbols))
            .order_by(MarketShortlist.created_at.desc())
        )).all()

        result: dict[str, dict] = {}
        for row in rows:
            bare = row.symbol.replace(".NS", "")
            if bare not in result:
                result[bare] = {
                    "composite_score": row.master_score,
                    "signal":          row.signal,
                }
        return result
    except Exception as exc:
        logger.warning(f"[agent] hub score prefetch failed: {exc}")
        return {}


async def _log_skipped_decision(
    symbol: str,
    decision,
    risk_reason: str,
    session: AsyncSession,
) -> None:
    try:
        from db.models import AgentDecision
        db_dec = AgentDecision(
            symbol=symbol,
            action=decision.action,
            confidence=decision.confidence,
            regime=decision.regime,
            strategy=decision.strategy,
            entry=decision.entry,
            stop=decision.stop,
            target=decision.target,
            qty=0,
            risk_pct=decision.risk_pct,
            reasons=decision.reasons,
            macro_bias=decision.macro_bias,
            fund_score=decision.fund_score,
            skip_reason=risk_reason,
            is_paper=settings.AGENT_PAPER_MODE,
            order_id=None,
        )
        session.add(db_dec)
        await session.commit()
    except Exception as exc:
        logger.debug(f"[agent] skip log failed: {exc}")


def eod_reconcile() -> None:
    """Reset daily counters at EOD."""
    global _portfolio_hydrated
    portfolio = _get_portfolio()
    portfolio.reset_day()
    _portfolio_hydrated = False  # force re-hydration next cycle (picks up any DB changes)
    logger.info("[agent] EOD reset complete")
