"""Main Agent Loop — orchestrates the per-bar decision cycle.

Reference: trading_agent/main.py → evaluate_universe()
Runs on every 15-minute bar close via Celery beat.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from engine.agent.analyzer          import MarketAnalyzerAgent
from engine.agent.selector          import StrategySelectorAgent
from engine.agent.fundamentals      import FundamentalsAgent
from engine.agent.macro             import MacroSectorAgent
from engine.agent.risk_manager      import RiskManagerAgent
from engine.agent.decision_engine   import DecisionEngine, apply_reasoning_gate
from engine.agent.execution         import AgentExecutionManager
from engine.agent.portfolio_context import AgentPortfolioContext
from engine.agent.momentum_filter   import refresh_if_needed as _mom_refresh, is_eligible as _mom_eligible
from engine.agent.market_regime     import get_market_regime, WEAK_BEAR, STRONG_BEAR, RegimeResult, MODERATE_BULL
from utils.config import settings
from utils.logger import logger

# ── Module-level singletons ───────────────────────────────────────────────────

_analyzer   = MarketAnalyzerAgent()
_selector   = StrategySelectorAgent()
_fund_agent = FundamentalsAgent()
_macro      = MacroSectorAgent()
_decision   = DecisionEngine()
_executor   = AgentExecutionManager()

# Tracks symbols that already received a shortlist alert this session.
# Key = symbol (bare), value = datetime sent. Prevents repeat spam every 60s.
_shortlist_alerted: dict[str, datetime] = {}
_SHORTLIST_ALERT_COOLDOWN_HOURS = 4   # re-alert after this many hours
_MAX_SHORTLIST_ALERTS_PER_CYCLE = 5   # cap: top-5 per cycle to avoid LLM queue pile-up
_shortlist_alerts_this_cycle: int = 0  # reset at the start of each run_agent_cycle

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

    from db.models import OpenPosition as OpenPos, PaperTrade
    from sqlalchemy import select as _sel

    try:
        # Hydrate from open_positions table (source of truth — agent writes here via trade_simulator)
        open_pos_rows = (await session.execute(
            _sel(OpenPos).order_by(OpenPos.opened_at.asc())
        )).scalars().all()

        capital_locked = 0.0
        for pos in open_pos_rows:
            sym = pos.symbol.replace(".NS", "").replace(".BO", "") if pos.symbol else ""
            # Use the full yfinance symbol as key (e.g. RELIANCE.NS)
            sym_key = pos.symbol or sym
            if sym_key not in portfolio.open_positions:
                risk = abs(pos.entry_price - pos.stop_loss) if pos.stop_loss else pos.entry_price * 0.05
                portfolio.open_positions[sym_key] = {
                    "side":         pos.direction or "BUY",
                    "entry":        float(pos.entry_price),
                    "stop":         float(pos.stop_loss) if pos.stop_loss else 0.0,
                    "target":       float(pos.take_profit) if pos.take_profit else 0.0,
                    "qty":          float(pos.size_units),
                    "strategy":     "HUB_SIGNAL",
                    "target1":      round(float(pos.entry_price) + risk, 2),
                    "target2":      round(float(pos.entry_price) + 2 * risk, 2),
                    "partial_done": False,
                    "trailing_sl":  None,
                    "entry_ts":     pos.opened_at.isoformat() if pos.opened_at else None,
                    "product":      "CNC",
                }
                capital_locked += float(pos.size_usd)

        portfolio.cash = max(0.0, portfolio.equity - capital_locked)

        logger.info(
            f"[agent] portfolio hydrated from open_positions: {len(open_pos_rows)} positions, "
            f"capital locked ₹{capital_locked:,.0f}, cash remaining ₹{portfolio.cash:,.0f}"
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


async def _get_breadth_pct(session: AsyncSession) -> float | None:
    """Return the latest market breadth % (hub stocks above 50d proxy) or None."""
    try:
        from sqlalchemy import text as _text
        row = (await session.execute(_text("""
            SELECT breadth_pct FROM market_breadth
            ORDER BY ts DESC LIMIT 1
        """))).first()
        return float(row[0]) if row else None
    except Exception:
        return None


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

    global _shortlist_alerts_this_cycle
    _shortlist_alerts_this_cycle = 0  # reset per-cycle cap

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

    # ── 5-State Market Regime Gate ────────────────────────────────────────────
    # Replaced the old single EMA50 check with a composite regime engine that
    # uses EMA stack (4 levels) + 20-day ROC (fast correction detector) +
    # EMA50 slope + market breadth + VIX — the root cause of 2025 losses was
    # that the old EMA50 gate kept the door open for ~40 days while Nifty was
    # already in a correction (price still above EMA50 but rapidly falling).
    _breadth = await _get_breadth_pct(session)
    market_regime: RegimeResult = await get_market_regime(session, breadth_pct=_breadth)

    _short_enabled = getattr(settings, "EQUITY_SHORT_ENABLED", False)
    if not market_regime.can_buy:
        # In BEAR regime: block BUY entries. If SHORT is enabled, continue the
        # scan to find PULLBACK_SHORT opportunities; otherwise skip entirely.
        if not _short_enabled:
            logger.info(
                f"[agent] Market Regime = {market_regime.state} (score={market_regime.score}) — "
                f"blocking new BUY entries | SHORT disabled → full skip"
            )
            return {
                "status":          "regime_gate_blocked",
                "regime_state":    market_regime.state,
                "regime_score":    market_regime.score,
                "regime_signals":  market_regime.signals,
                "cycle_ts":        datetime.utcnow().isoformat(),
                "paper_mode":      settings.AGENT_PAPER_MODE,
                "symbols_scanned": 0,
                "decisions":       0,
                "fno_opened":      0,
                "skipped":         0,
                "portfolio": {
                    "equity":         portfolio.equity,
                    "cash":           round(portfolio.cash, 2),
                    "open_positions": len(portfolio.open_positions),
                    "daily_pnl_pct":  round(portfolio.daily_pnl_pct * 100, 2),
                    "weekly_pnl_pct": round(portfolio.weekly_pnl_pct * 100, 2),
                },
            }
        logger.info(
            f"[agent] Market Regime = {market_regime.state} (score={market_regime.score}) — "
            f"BUY blocked | scanning for PULLBACK_SHORT opportunities"
        )

    logger.info(
        f"[agent] Market Regime = {market_regime.state} | score={market_regime.score} | "
        f"min_conf={market_regime.min_conf} | size_mult={market_regime.size_mult}× | "
        f"signals={market_regime.signals}"
    )

    # Morning regime classification — one LLM call per trading day.
    # WAIT:      no new entries (market downtrend / high fear)
    # SELECTIVE: TREND_BREAKOUT candidates only (highest win-rate strategy)
    # AGGRESSIVE: all strategies (normal operation)
    from engine.agent.morning_regime import get_morning_regime
    regime_mode = await get_morning_regime(session)
    if regime_mode == "WAIT":
        logger.info("[agent] Morning regime = WAIT — skipping new entry scan")
        return {
            "status":          "morning_regime_wait",
            "cycle_ts":        datetime.utcnow().isoformat(),
            "paper_mode":      settings.AGENT_PAPER_MODE,
            "symbols_scanned": 0,
            "decisions":       0,
            "fno_opened":      0,
            "skipped":         0,
            "regime_mode":     regime_mode,
            "portfolio": {
                "equity":         portfolio.equity,
                "cash":           round(portfolio.cash, 2),
                "open_positions": len(portfolio.open_positions),
                "daily_pnl_pct":  round(portfolio.daily_pnl_pct * 100, 2),
                "weekly_pnl_pct": round(portfolio.weekly_pnl_pct * 100, 2),
            },
        }

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
                regime_mode=regime_mode,
                market_regime=market_regime,
            )
            if result:
                results.append(result)
            else:
                skipped += 1
        except Exception as exc:
            logger.warning(f"[agent] cycle error on {symbol}: {exc}")
            skipped += 1

    # ── F&O passes (additive; gated by ENABLE_OPTIONS / ENABLE_FUTURES) ───────
    fno_opened: list[dict] = []
    if getattr(settings, "ENABLE_OPTIONS", False):
        try:
            from engine.fno.selection import evaluate_index_options
            fno_opened += await evaluate_index_options(session, portfolio.equity)
        except Exception as exc:
            logger.warning(f"[agent] F&O option pass failed: {exc}")
    if getattr(settings, "ENABLE_FUTURES", False):
        try:
            from engine.fno.futures import evaluate_index_futures
            fno_opened += await evaluate_index_futures(session, portfolio.equity)
        except Exception as exc:
            logger.warning(f"[agent] F&O futures pass failed: {exc}")
    if getattr(settings, "FNO_HEDGE_ENABLED", False):
        try:
            from engine.fno.selection import evaluate_portfolio_hedge
            hedge = await evaluate_portfolio_hedge(session, portfolio.equity)
            if hedge:
                fno_opened.append(hedge)
        except Exception as exc:
            logger.warning(f"[agent] F&O hedge pass failed: {exc}")
    if getattr(settings, "FNO_VOL_ENABLED", False):
        try:
            from engine.fno.strategies_vol import evaluate_volatility
            fno_opened += await evaluate_volatility(session, portfolio.equity)
        except Exception as exc:
            logger.warning(f"[agent] F&O volatility pass failed: {exc}")
    if fno_opened:
        logger.info(f"[agent] F&O passes opened {len(fno_opened)} derivative position(s)")

    return {
        "status":           "ok",
        "cycle_ts":         datetime.utcnow().isoformat(),
        "paper_mode":       settings.AGENT_PAPER_MODE,
        "regime_mode":      regime_mode,
        "market_regime":    market_regime.state,
        "regime_score":     market_regime.score,
        "regime_signals":   market_regime.signals,
        "symbols_scanned":  len(universe),
        "decisions":        len(results),
        "fno_opened":       len(fno_opened),
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
    regime_mode: str = "AGGRESSIVE",
    market_regime: "RegimeResult | None" = None,
) -> dict | None:
    global _shortlist_alerts_this_cycle

    # SME/Emerge platform guard — symbols ending in -SM are NSE Emerge (illiquid,
    # delivery-only, not covered by any live price feed). Never trade them.
    _bare_sym = symbol.replace(".NS", "").replace(".BO", "").upper()
    if _bare_sym.endswith("-SM"):
        logger.debug(f"[agent] {symbol}: NSE SME stock — skipping (no live price feed)")
        return None

    # Duplicate position guard — block re-entry for any symbol already held.
    # Normalise both sides (.NS / bare) so "VIJAYA.NS" and "VIJAYA" match the
    # same in-memory key regardless of how the position was originally stored.
    _already = any(
        k == symbol or k.replace(".NS", "").replace(".BO", "").upper() == _bare_sym
        for k in portfolio.open_positions
    )
    if _already:
        return None

    # Momentum rotation gate — skip stocks ranked in the bottom (1 - AGENT_MOMENTUM_TOP_PCT)
    # of 63-day price momentum within the scan universe. Fail-open when cache is empty.
    if not _mom_eligible(symbol):
        logger.debug(f"[agent] {symbol}: below momentum rank threshold — skipping")
        return None

    # 1. Get candle data from DB — single, deterministic timeframe.
    # NO fallback cascade: a cascade let the agent's trading style (and the scale
    # of every ATR-based stop/target) be decided by whatever candle data happened
    # to exist per-symbol — so two stocks in the same scan could run on different
    # timeframes. The timeframe is now a deliberate config choice (AGENT_TIMEFRAME),
    # consistent for every symbol and matching the validated backtest basis.
    from crawler.price_feed import get_latest_candles

    _timeframe_used = settings.AGENT_TIMEFRAME
    candles = await get_latest_candles(symbol, settings.AGENT_TIMEFRAME, 300, session)
    if not candles or len(candles) < settings.AGENT_WARMUP_BARS:
        return None

    # Candle freshness guard — reject symbols whose DB data is too stale to trade.
    # 1d candles: latest candle must be from today or yesterday (≤36h).
    # Intraday: latest candle must be within the last 2 hours.
    # This catches the case where the crawler never ingested data for a symbol
    # (e.g. illiquid small-caps) so the agent would use week-old closes as entry.
    import datetime as _dt
    _latest_ts = max(c.timestamp for c in candles)
    if hasattr(_latest_ts, "tzinfo") and _latest_ts.tzinfo:
        _latest_ts = _latest_ts.replace(tzinfo=None)
    _candle_age_h = (_dt.datetime.utcnow() - _latest_ts).total_seconds() / 3600
    _max_age_h = 72 if settings.AGENT_TIMEFRAME == "1d" else 4
    if _candle_age_h > _max_age_h:
        logger.warning(
            f"[agent] {symbol}: latest candle is {_candle_age_h:.0f}h old "
            f"(max {_max_age_h}h for {settings.AGENT_TIMEFRAME}) — skipping stale data"
        )
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

    # Hub score gate — AI has formed a view but conviction is weak → skip.
    # Fail-open when no hub_info (no AI view) so technical strategies still run.
    if hub_info is not None:
        _hub_abs = abs(hub_info.get("composite_score") or hub_info.get("master_score") or 0)
        if _hub_abs < 50:
            return None

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
        # Stamp regime from features so Telegram alerts always have a real value
        if candidate is not None:
            candidate.regime = features.regime

    # ── VARSITY BACKTEST FIX ──────────────────────────────────────────────────
    # Re-enabling TREND_BREAKOUT_LONG and RANGE_REVERSAL_LONG as per expert 
    # trading rules.
    if candidate is not None:
        candidate.regime = features.regime

    # 4b-2. LIVE entry price — the candidate's entry comes from the latest candle
    # close (can be minutes old). Snap to live price if available.
    # If the live price diverges >5% from the candle price, the candle data is too
    # stale to trade safely — reject the candidate rather than fill at a phantom price.
    if candidate is not None:
        try:
            from crawler.live_prices import get_price
            lp = get_price(symbol)
            live_px = float(lp["price"]) if lp and lp.get("price") else None

            # WebSocket cache miss for mid-cap symbols — fall back to Kite REST LTP.
            # If we still can't confirm the price, reject: executing at an unverified
            # stale candle price is the root cause of the ₹438 vs ₹412 GNA-style bugs.
            if live_px is None:
                try:
                    from crawler.zerodha_market import get_live_prices
                    _sym_ns = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
                    _quotes = await get_live_prices([_sym_ns])
                    _q = _quotes.get(_sym_ns) or _quotes.get(symbol)
                    if _q:
                        _px = _q.get("price") or _q.get("last_price")
                        if _px and float(_px) > 0:
                            live_px = float(_px)
                except Exception as _exc:
                    logger.debug(f"[agent] REST LTP fallback failed for {symbol}: {_exc}")

            if live_px is None:
                logger.warning(
                    f"[agent] {symbol}: cannot confirm live price — "
                    f"no WebSocket tick or REST LTP available, skipping trade"
                )
                return None

            if candidate.entry:
                divergence = abs(live_px - candidate.entry) / candidate.entry
                if divergence < 0.05:
                    delta = live_px - candidate.entry
                    candidate.entry = round(live_px, 2)
                    if getattr(candidate, "stop", None):
                        candidate.stop = round(candidate.stop + delta, 2)
                    for attr in ("target", "target_1", "target_2"):
                        v = getattr(candidate, attr, None)
                        if v:
                            setattr(candidate, attr, round(v + delta, 2))
                else:
                    logger.warning(
                        f"[agent] {symbol}: candle price ₹{candidate.entry:.2f} vs "
                        f"live ₹{live_px:.2f} — {divergence*100:.1f}% divergence, "
                        f"candle data too stale — skipping trade"
                    )
                    return None
        except Exception as exc:
            logger.debug(f"[agent] live entry-price snap failed for {symbol}: {exc}")

    # SELECTIVE regime filter — only TREND_BREAKOUT_LONG proceeds (WR=58.8%).
    # Other strategies are suppressed until the morning LLM upgrades to AGGRESSIVE.
    if regime_mode == "SELECTIVE" and candidate is not None:
        if getattr(candidate, "strategy", "") != "TREND_BREAKOUT_LONG":
            return None

    # 4d. News Sentiment Circuit Breaker — BUY candidates only.
    #
    # Blocks TREND_BREAKOUT_LONG and RANGE_REVERSAL_LONG entries (and Hub BUY
    # signals that behave like breakouts/reversals) when FinBERT sentiment is
    # deeply negative OR a hard-keyword is detected in recent headlines.
    #
    # Rationale: breakout and reversal trades require *confirmation* from the
    # market narrative — buying a breakout into negative news is the most
    # reliably losing pattern in our backtest. Trend-following (HUB_7FACTOR)
    # already embeds the news subscore in master_score, but a separate hard
    # gate here prevents a high technical score from overriding a -0.3 FinBERT
    # read that the Hub's 7% news weight would otherwise dilute.
    #
    # Implementation: read Hub news subscore when available (no extra query);
    # fall back to a fresh get_market_sentiment() call for technical candidates.
    # Keyword check runs against DB headlines — no extra API call needed.

    _NEWS_BREAKER_STRATEGIES = {"TREND_BREAKOUT_LONG", "RANGE_REVERSAL_LONG", "HUB_7FACTOR"}
    # Words that indicate hard news risk — halt / fraud / insolvency / regulatory.
    _NEWS_HARD_KEYWORDS = frozenset({
        "fraud", "scam", "probe", "sebi", "cbi", "ed ", "enforcement",
        "halt", "suspend", "delist", "bankrupt", "insolvency", "default",
        "nclt", "nclt order", "promoter pledge", "pledg", "pledged",
        "fir", "arrest", "regulatory action", "show cause", "penalty",
        "circuit", "upper circuit breaker", "lower circuit",
        "earnings miss", "profit warning", "guidance cut",
    })
    _NEWS_SENTIMENT_BLOCK_THRESHOLD = -0.30   # FinBERT scale: −1..+1

    if candidate is not None and candidate.side == "BUY":
        _strategy = getattr(candidate, "strategy", "")
        if _strategy in _NEWS_BREAKER_STRATEGIES:
            # ── Get news score (Hub units −100..+100 → normalise to −1..+1) ──
            _hub_sub = getattr(candidate, "hub_subscores", None) or {}
            _raw_news_score: float | None = None
            if _hub_sub and "news" in _hub_sub:
                _raw_news_score = float(_hub_sub["news"]) / 100.0  # −100..+100 → −1..+1
            else:
                # Technical-only candidate — fetch from DB (no external call)
                try:
                    from crawler.news_crawler import get_market_sentiment
                    _raw_news_score = await get_market_sentiment(symbol, session)
                except Exception:
                    _raw_news_score = None

            _blocked_news = False
            _block_reason = ""

            # Gate 1: FinBERT sentiment below threshold
            if _raw_news_score is not None and _raw_news_score < _NEWS_SENTIMENT_BLOCK_THRESHOLD:
                _blocked_news = True
                _block_reason = (
                    f"news_sentiment_circuit_breaker: FinBERT={_raw_news_score:+.2f} "
                    f"< {_NEWS_SENTIMENT_BLOCK_THRESHOLD:+.2f} threshold"
                )

            # Gate 2: Hard-keyword scan against last 10 DB headlines (free — no Tavily call)
            if not _blocked_news:
                try:
                    from sqlalchemy import select as _sa_select, text as _sa_text
                    from db.models import NewsItem as _NewsItem
                    _headline_rows = (await session.execute(
                        _sa_select(_NewsItem.headline)
                        .where(_sa_text("tickers_affected::jsonb @> :p ::jsonb")
                               .bindparams(p=f'["{symbol}"]'))
                        .order_by(_NewsItem.crawled_at.desc())
                        .limit(10)
                    )).scalars().all()
                    _headlines_text = " ".join(h.lower() for h in _headline_rows if h)
                    _hit = next(
                        (kw for kw in _NEWS_HARD_KEYWORDS if kw in _headlines_text),
                        None,
                    )
                    if _hit:
                        _blocked_news = True
                        _block_reason = (
                            f"news_sentiment_circuit_breaker: hard keyword '{_hit}' "
                            f"in recent headlines"
                        )
                except Exception as _kw_exc:
                    logger.debug(f"[agent/news_cb] keyword scan failed for {symbol}: {_kw_exc}")

            if _blocked_news:
                logger.warning(
                    f"[agent] NEWS_CB BLOCKED {symbol} | strategy={_strategy} | {_block_reason}"
                )
                await _log_skipped_decision(
                    symbol=symbol,
                    candidate=candidate,
                    regime=features.regime,
                    macro_bias=macro_bias,
                    fund_score=fund_score,
                    drop_reason=_block_reason,
                    session=session,
                )
                return None

    # 5. Regime confidence gate — SIDEWAYS regime requires higher conviction;
    # STRONG_BULL allows slightly looser entry (market tailwind doing some work).
    # This is the extraordinary layer: during multi-month corrections (2025) the
    # composite regime score falls to SIDEWAYS before price crosses below EMA50,
    # so candidates must pass a harder confidence bar to survive.
    if candidate is not None and market_regime is not None:
        _regime_min = market_regime.min_conf
        if candidate.confidence < _regime_min:
            logger.debug(
                f"[agent] {symbol}: conf={candidate.confidence} < regime_min={_regime_min} "
                f"({market_regime.state}) — skipping"
            )
            return None
        # Stamp size_mult so executor can scale down position in SIDEWAYS
        candidate.regime_size_mult = market_regime.size_mult

    # 5. Decision fusion (regime factor + conflict detection + multiplicative confidence)
    # Tell the sizer how much capital is already deployed so it respects the
    # portfolio-wide cash buffer when sizing toward the deployment target.
    candidate.deployed_notional = max(0.0, portfolio.equity - portfolio.cash)
    decision, reject_reason = _decision.fuse(
        symbol=symbol,
        candidate=candidate,
        regime=features.regime,
        macro_bias=macro_bias,
        fund_score=fund_score,
        fund_grade=fund_grade,
        equity=portfolio.equity,
    )

    if decision is None:
        logger.debug(
            f"[agent] SKIP {symbol} | {reject_reason} | regime={features.regime}"
            + (" | hub_override" if hub_override else "")
        )
        if candidate is not None:
            # Log every fuse()-level rejection to agent_decisions for audit
            await _log_skipped_decision(
                symbol=symbol,
                candidate=candidate,
                regime=features.regime,
                macro_bias=macro_bias,
                fund_score=fund_score,
                drop_reason=reject_reason or "decision_filtered",
                session=session,
            )
        return None

    # 5a. Attach a technical/chart read (candlestick + indicators + ML) so the
    # reasoning gate weighs the chart, not just the numeric factors. Reuses the df
    # already in scope; fail-open.
    try:
        if getattr(settings, "AGENT_CHART_BRIEF_ENABLED", True):
            from engine.agent.chart_brief import build_chart_brief
            candidate.chart_brief = build_chart_brief(symbol, df)
    except Exception as _bx:
        logger.debug(f"[agent] chart_brief skipped {symbol}: {_bx}")

    # 5b. Level-1 LLM reasoning gate (opt-in: AGENT_LLM_REASONING_ENABLED).
    # Runs only on a candidate that already cleared the arithmetic threshold, so
    # LLM cost is bounded to qualified trades. Can veto (SKIP) or blend confidence.
    # Fail-open: disabled/LLM-down → arithmetic decision passes through unchanged.
    decision, _llm_reject = await apply_reasoning_gate(symbol, candidate, decision)
    if decision is None:
        logger.info(f"[agent] LLM-reason SKIP {symbol} | {_llm_reject}")
        await _log_skipped_decision(
            symbol=symbol, candidate=candidate, regime=features.regime,
            macro_bias=macro_bias, fund_score=fund_score,
            drop_reason=_llm_reject or "llm_reasoning_skip", session=session,
        )
        return None

    # 6. Risk Manager veto
    # Stamp the candidate's sector so can_take_trade() can check the exposure gate.
    # Resolution order: Hub hub_subscores → india_specific.SECTOR_MAP → None (unknown).
    if candidate is not None and not getattr(candidate, "sector", None):
        from engine.india_specific import SECTOR_MAP
        candidate.sector = SECTOR_MAP.get(symbol) or SECTOR_MAP.get(symbol.replace(".NS", "") + ".NS")

    risk_ok, risk_reason = RiskManagerAgent(portfolio.to_risk_ctx()).can_take_trade(
        candidate=candidate if candidate else decision,
        equity=portfolio.equity,
    )

    if not risk_ok:
        logger.info(f"[agent] BLOCKED {symbol} | {risk_reason} | {decision.strategy}")
        await _log_skipped_decision(
            symbol=symbol,
            candidate=candidate,
            regime=features.regime,
            macro_bias=macro_bias,
            fund_score=fund_score,
            drop_reason=risk_reason,
            session=session,
            decision=decision,
        )
        return None

    # 7. Pre-trade research gate (BUY only) — Screener + yfinance + Tavily + LLM veto
    #    Runs BEFORE execution so bad trades are blocked with real data, not just
    #    technical signals. Cached 20 min so repeated scans of the same symbol are free.
    if decision.action == "BUY":
        try:
            from engine.pre_trade_research import run_pre_trade_research
            research = await asyncio.wait_for(
                run_pre_trade_research(
                    symbol=symbol,
                    action=decision.action,
                    score=decision.master_score or decision.confidence,
                    regime=decision.regime,
                    entry=decision.entry,
                    stop=decision.stop,
                    t1=decision.target,
                    fund_grade=decision.fund_grade,
                ),
                timeout=12.0,
            )
            if research.get("veto"):
                veto_reason = research["veto_reason"]
                logger.warning(f"[agent] PRE-TRADE VETO {symbol}: {veto_reason}")
                await _log_skipped_decision(
                    symbol=symbol,
                    candidate=candidate,
                    regime=features.regime,
                    macro_bias=macro_bias,
                    fund_score=fund_score,
                    drop_reason=f"pre_trade_veto:{veto_reason}",
                    session=session,
                    decision=decision,
                )
                return None
            # Append research note to decision reasons for audit trail
            note = research.get("research_note", "")
            if note:
                decision.reasons.append(f"[web] {note[:200]}")
        except (asyncio.TimeoutError, Exception) as _exc:
            logger.debug(f"[agent] pre-trade research error for {symbol}: {_exc} → ALLOW")

    # 8. Execute
    order_id = await _executor.execute(decision, session)

    if order_id:
        portfolio.add_position(decision)
        try:
            from api.websocket import broadcast_agent_event
            import asyncio as _aio
            _aio.ensure_future(broadcast_agent_event("TRADE_OPENED", {
                "symbol":   decision.symbol,
                "side":     decision.action,
                "entry":    decision.entry,
                "qty":      getattr(decision, "qty", None),
                "strategy": decision.strategy,
                "score":    getattr(decision, "master_score", None),
            }))
        except Exception:
            pass
        _sym  = decision.symbol
        _risk = abs(decision.entry - decision.stop)
        portfolio.open_positions[_sym]["target1"]      = round(decision.entry + 1.0 * _risk, 2)
        portfolio.open_positions[_sym]["target2"]      = round(decision.entry + 2.0 * _risk, 2)
        portfolio.open_positions[_sym]["partial_done"] = False
        portfolio.open_positions[_sym]["trailing_sl"]  = None
        portfolio.open_positions[_sym]["entry_ts"]     = datetime.utcnow().isoformat()
        # Track product so MIS square-off sweep can identify intraday positions
        portfolio.open_positions[_sym]["product"]      = getattr(decision, "product", "CNC")
        # Track sector so sector_exposure() in portfolio_context can enforce the cap
        portfolio.open_positions[_sym]["sector"]       = getattr(candidate, "sector", None) or getattr(decision, "sector", None)

        # Telegram entry alert.
        # Hub-override trades already got the full shortlist alert → send a brief
        # "TRADE PLACED" follow-up. Technical-only trades get the full fmt_entry.
        if settings.telegram_available:
            from integrations.telegram_service import send, fmt_entry
            if hub_override:
                _sym_bare = _sym.replace(".NS", "")
                _t2 = portfolio.open_positions[_sym].get("target2", decision.target)
                placed_msg = (
                    f"✅ <b>TRADE PLACED</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 <b>{_sym_bare}</b>  "
                    f"· BUY {decision.qty} shares @ ₹{decision.entry:,.2f}\n"
                    f"🛑 Stop ₹{decision.stop:,.2f}  "
                    f"·  🎯 T2 ₹{_t2:,.2f}\n"
                    f"<i>Position opened — see analysis above</i>"
                )
                await send(placed_msg)
            else:
                await send(fmt_entry(decision))

    return decision.to_dict()


async def _send_shortlist_alert(
    candidate,
    df: "pd.DataFrame | None",
    executed: bool,
) -> None:
    """Build and send the STRONG_BUY shortlist alert to Telegram.

    Called for every high-score Hub candidate, regardless of whether the
    trade was actually executed (fund/risk constraints may have blocked it).
    Respects a 4-hour per-symbol cooldown so the channel isn't spammed.
    """
    if not settings.telegram_available:
        return

    bare = candidate.symbol.replace(".NS", "")
    now  = datetime.utcnow()

    # Cooldown: skip if we already alerted for this symbol recently AND the
    # trade wasn't just executed (executed = always send)
    if not executed:
        last_sent = _shortlist_alerted.get(bare)
        if last_sent and (now - last_sent).total_seconds() < _SHORTLIST_ALERT_COOLDOWN_HOURS * 3600:
            return

    # Build AI explanation — try Tavily research first (real web data), then LLM.
    ai_note = ""
    subs   = getattr(candidate, "hub_subscores", {}) or {}
    regime = getattr(candidate, "regime", "") or subs.get("regime", "")
    tech   = float(subs.get("technical",   0))
    news_s = float(subs.get("news",        0))
    earn   = float(subs.get("earnings",    0))
    fund   = float(subs.get("fundamental", 0))
    score  = candidate.master_score or 0.0
    entry  = candidate.entry
    stop   = candidate.stop
    risk   = abs(entry - stop)
    t1     = round(entry + risk, 2)
    t2     = round(entry + 2 * risk, 2)

    # 1. Try Tavily web research (factual, real-time, costs 2 credits)
    try:
        from engine.tavily_enricher import research_stock_for_alert
        from utils.config import settings as _cfg
        if getattr(_cfg, "tavily_available", False):
            ai_note = await research_stock_for_alert(
                symbol=candidate.symbol,
                score=score, tech_score=tech, news_score=news_s,
                regime=regime, entry=entry, stop=stop, t1=t1, t2=t2,
            ) or ""
    except Exception as exc:
        logger.debug(f"[agent/shortlist] Tavily research failed for {bare}: {exc}")

    # 2. Fallback to local LLM if Tavily returned nothing
    if not ai_note:
        try:
            llm_prompt = (
                f"Indian stock: {bare}\n"
                f"Hub 7-factor score: {score:+.1f} "
                f"(Technical={tech:+.0f}, News={news_s:+.0f}, "
                f"Earnings={earn:+.0f}, Fundamental={fund:+.0f})\n"
                f"Regime: {regime}\n"
                f"Entry ₹{entry:.0f}, Stop ₹{stop:.0f}, T1 ₹{t1:.0f}, T2 ₹{t2:.0f}\n\n"
                f"In 3 short sentences: WHY strong buy? Key risk? When to EXIT?"
            )
            from utils.llm import call_llm_chat
            ai_note = await call_llm_chat(
                [
                    {"role": "system", "content": "Concise Indian equity analyst. Max 3 sentences."},
                    {"role": "user",   "content": llm_prompt},
                ],
                max_tokens=200,
                temperature=0.3,
                timeout=50.0,
                groq_fallback=False,  # background task — protect Groq quota for user-facing requests
            ) or ""
        except Exception as exc:
            logger.debug(f"[agent/shortlist] LLM fallback failed for {bare}: {exc}")

    # Send
    try:
        from integrations.telegram_service import send, fmt_shortlist_alert
        msg = fmt_shortlist_alert(candidate, df=df, ai_note=ai_note, executed=executed)
        await send(msg)
        _shortlist_alerted[bare] = now
        logger.info(f"[agent/shortlist] ✓ Telegram alert sent for {bare} (executed={executed})")
    except Exception as exc:
        logger.warning(f"[agent/shortlist] Telegram send failed for {bare}: {exc}")


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

    # Refresh momentum rankings for the universe (cached for 6 hours).
    # Non-blocking: runs in background; first cycle is fail-open.
    try:
        top_pct = getattr(settings, "AGENT_MOMENTUM_TOP_PCT", 0.50)
        await _mom_refresh(universe[:150], session, top_pct)
    except Exception as _me:
        logger.debug(f"[agent] momentum_filter refresh error: {_me}")

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
    drop_reason: str,
    session: AsyncSession,
    candidate=None,
    regime: str = "",
    macro_bias: int = 0,
    fund_score: int = 0,
    decision=None,  # AgentDecisionOutput, populated when risk-manager blocked
) -> None:
    """Log every rejected trade to agent_decisions before dropping."""
    try:
        from db.models import AgentDecision

        # Use decision fields when available (risk-manager block), else candidate fields
        src = decision if decision is not None else candidate

        db_dec = AgentDecision(
            symbol=symbol,
            action=getattr(src, "action", None) or getattr(src, "side", "SKIP"),
            confidence=getattr(src, "confidence", 0),
            regime=getattr(decision, "regime", regime),
            strategy=getattr(src, "strategy", ""),
            entry=getattr(src, "entry", None),
            stop=getattr(src, "stop", None),
            target=getattr(src, "target", None),
            qty=0,
            risk_pct=getattr(src, "risk_pct", 0.0),
            reasons=getattr(src, "reasons", []),
            macro_bias=getattr(decision, "macro_bias", macro_bias),
            fund_score=getattr(decision, "fund_score", fund_score),
            skip_reason=drop_reason,
            master_score=getattr(src, "master_score", None),
            confidence_factors=getattr(decision, "confidence_factors", None),
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
