"""AI Trading Agent API — /api/v1/agent endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import AgentDecision, AgentTrade, AgentPerformance, Candle
from utils.config import settings

router = APIRouter(tags=["Trading Agent"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    symbol:     str
    timeframe:  str   = "1h"
    fund_grade: str   = "WATCHLIST"
    macro_bias: int   = 0
    days_back:  int   = 365

class ConfigUpdate(BaseModel):
    enabled:              Optional[bool]  = None
    paper_mode:           Optional[bool]  = None
    confidence_threshold: Optional[int]   = None
    max_risk_per_trade:   Optional[float] = None


# ── GET /status ───────────────────────────────────────────────────────────────

@router.get("/status")
async def agent_status(db: AsyncSession = Depends(get_db)):
    from engine.agent.agent_loop import _get_portfolio, _is_market_hours, _is_trading_day
    from sqlalchemy import func as sqlfunc

    portfolio = _get_portfolio()
    now       = datetime.utcnow()

    # Count decisions today
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    decisions_today = (await db.execute(
        select(AgentDecision).where(
            AgentDecision.created_at >= today_start,
            AgentDecision.order_id != None,
            AgentDecision.is_paper == settings.AGENT_PAPER_MODE,
        )
    )).scalars().all()

    # ── DB-authoritative equity/cash ─────────────────────────────────────────────
    # Trades are written to paper_trades + open_positions (not agent_trades).
    # Read from those tables so equity/cash always reflects reality.
    START_CAPITAL = settings.AGENT_EQUITY

    from db.models import PaperTrade, OpenPosition as OpenPos
    from sqlalchemy import func as sqlfunc

    # Realised P&L: closed paper_trades (exit_price set, pnl recorded)
    realised_row = (await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(PaperTrade.pnl), 0.0))
        .where(PaperTrade.exit_price != None, PaperTrade.pnl != None)
    )).scalar()
    realised_pnl = float(realised_row or 0.0)

    # Open positions: read from open_positions, but compute LIVE unrealised P&L
    # on read (not the stored value) so the equity/unrealised numbers are always
    # real-time, including brand-new positions.
    open_pos_rows = (await db.execute(select(OpenPos))).scalars().all()
    # Capital deployed = equity notional + F&O MARGIN (not F&O notional). Futures
    # block only ~18% margin, so counting their full notional would wrongly zero
    # out cash. Options block the premium (= margin_blocked).
    capital_deployed = sum(
        float(p.margin_blocked or 0.0)
        if getattr(p, "instrument_type", "EQUITY") in ("CE", "PE", "FUTURE")
        else float(p.size_usd)
        for p in open_pos_rows
    )
    from paper_trading.trade_simulator import compute_live_pnl
    _live = await compute_live_pnl(open_pos_rows, db)
    unrealised_pnl = sum(_live.get(p.id, (0, p.unrealised_pnl, 0))[1] for p in open_pos_rows)

    db_equity = START_CAPITAL + realised_pnl + unrealised_pnl
    db_cash   = max(0.0, START_CAPITAL + realised_pnl - capital_deployed)
    daily_pnl_pct = round(portfolio.daily_pnl_pct * 100, 2)
    open_risk_pct = round(portfolio.open_risk_pct * 100, 2)

    return {
        "enabled":              settings.AGENT_ENABLED,
        "paper_mode":           settings.AGENT_PAPER_MODE,
        "session_active":       _is_market_hours() and _is_trading_day(),
        "confidence_threshold": settings.AGENT_CONFIDENCE_THRESHOLD,
        "max_risk_per_trade":   settings.AGENT_MAX_RISK_PER_TRADE,
        "portfolio": {
            "start_capital":        round(START_CAPITAL, 2),
            "equity":               round(db_equity, 2),
            "cash":                 round(db_cash, 2),
            "unrealised_pnl":       round(unrealised_pnl, 2),
            "realised_pnl":         round(realised_pnl, 2),
            "open_positions_count": len(open_pos_rows),
            "open_positions":       portfolio.open_positions,
            "daily_pnl_pct":        daily_pnl_pct,
            "weekly_pnl_pct":       round(portfolio.weekly_pnl_pct * 100, 2),
            "open_risk_pct":        open_risk_pct,
        },
        "decisions_today": len(decisions_today),
    }


# ── POST /cycle/trigger ───────────────────────────────────────────────────────

@router.post("/cycle/trigger")
async def trigger_cycle(db: AsyncSession = Depends(get_db)):
    """Manually trigger one agent evaluation cycle.

    Always runs regardless of market hours or the AGENT_ENABLED flag —
    user explicitly requested it.
    """
    from engine.agent.agent_loop import run_agent_cycle
    result = await run_agent_cycle(db, force=True)
    return result


# ── POST /backtest ────────────────────────────────────────────────────────────

@router.post("/backtest")
async def run_backtest(req: BacktestRequest, db: AsyncSession = Depends(get_db)):
    """Run backtest on historical candle data."""
    from engine.agent.backtester import AgentBacktester
    from crawler.price_feed import get_latest_candles

    limit = req.days_back * 7  # generous estimate for intraday bars
    candles = await get_latest_candles(req.symbol, req.timeframe, limit, db)

    if not candles or len(candles) < settings.AGENT_WARMUP_BARS + 10:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough data for {req.symbol} on {req.timeframe}. "
                   f"Need {settings.AGENT_WARMUP_BARS + 10} bars, got {len(candles)}.",
        )

    candles_sorted = sorted(candles, key=lambda c: c.timestamp)
    df = pd.DataFrame([{
        "open":   float(c.open), "high":  float(c.high),
        "low":    float(c.low),  "close": float(c.close),
        "volume": float(c.volume), "timestamp": c.timestamp,
    } for c in candles_sorted])
    df.set_index("timestamp", inplace=True)

    bt = AgentBacktester()
    try:
        result = bt.run(
            df,
            symbol=req.symbol,
            equity=settings.AGENT_EQUITY,
            fund_grade=req.fund_grade,
            macro_bias=req.macro_bias,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Remove per-trade list from response to keep it small
    result_clean = {k: v for k, v in result.items() if k != "trades"}
    result_clean["trade_count_detail"] = len(result.get("trades", []))
    return result_clean


# ── GET /decisions ────────────────────────────────────────────────────────────

@router.get("/decisions")
async def get_decisions(
    limit:  int = 20,
    symbol: Optional[str] = None,
    action: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(AgentDecision).order_by(desc(AgentDecision.created_at)).limit(limit)
    if symbol: q = q.where(AgentDecision.symbol == symbol)
    if action: q = q.where(AgentDecision.action == action.upper())
    rows = (await db.execute(q)).scalars().all()

    return [
        {
            "id":          r.id,
            "ts":          r.ts.isoformat() if r.ts else None,
            "symbol":      r.symbol,
            "action":      r.action,
            "confidence":  r.confidence,
            "regime":      r.regime,
            "strategy":    r.strategy,
            "entry":       r.entry,
            "stop":        r.stop,
            "target":      r.target,
            "qty":         r.qty,
            "risk_pct":    r.risk_pct,
            "reasons":     r.reasons or [],
            "macro_bias":  r.macro_bias,
            "fund_score":  r.fund_score,
            "skip_reason": r.skip_reason,
            "is_paper":    r.is_paper,
            "order_id":    r.order_id,
        }
        for r in rows
    ]


# ── GET /trades ───────────────────────────────────────────────────────────────

@router.get("/trades")
async def get_trades(
    limit:     int  = 500,
    open_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    q = select(AgentTrade).order_by(desc(AgentTrade.entry_ts)).limit(limit)
    if open_only:
        q = q.where(AgentTrade.exit_ts == None)
    rows = (await db.execute(q)).scalars().all()

    # Use PRICE_CACHE (refreshed every 15 s by live price loop) for unrealised P&L.
    # Candle DB is stale between bars — PRICE_CACHE always has the latest tick.
    from crawler.live_prices import PRICE_CACHE, get_price

    open_syms = {r.symbol for r in rows if r.exit_price is None}
    price_map: dict[str, float] = {}
    for sym in open_syms:
        cached = PRICE_CACHE.get(sym, {})
        lp = cached.get("price") or cached.get("ltp")
        if lp:
            price_map[sym] = float(lp)
        else:
            # Fallback: get_price is SYNC and returns a dict|None (live ticks →
            # cache). Extract the price; never await it.
            live = get_price(sym)
            px = (live or {}).get("price") or (live or {}).get("last_price")
            if px:
                price_map[sym] = float(px)

    out = []
    for r in rows:
        cur_price: float | None = None
        unrealised_pnl: float | None = None
        unrealised_pct: float | None = None
        if r.exit_price is None and r.symbol in price_map:
            cur_price = price_map[r.symbol]
            notional  = float(r.qty) * float(r.entry_price)
            if r.side == "BUY":
                unrealised_pnl = (cur_price - float(r.entry_price)) * float(r.qty)
            else:
                unrealised_pnl = (float(r.entry_price) - cur_price) * float(r.qty)
            unrealised_pct = (unrealised_pnl / notional * 100) if notional else 0.0
        out.append({
            "id":            r.id,
            "symbol":        r.symbol,
            "side":          r.side,
            "qty":           r.qty,
            "entry_price":   r.entry_price,
            "exit_price":    r.exit_price,
            "stop_price":    r.stop_price,
            "target_price":  r.target_price,
            "entry_ts":      r.entry_ts.isoformat(),
            "exit_ts":       r.exit_ts.isoformat() if r.exit_ts else None,
            "exit_reason":   r.exit_reason,
            "pnl":           r.pnl,
            "strategy":      r.strategy,
            "regime":        r.regime,
            "is_paper":      r.is_paper,
            "current_price": cur_price,
            "unrealised_pnl": unrealised_pnl,
            "unrealised_pct": unrealised_pct,
        })
    return out


# ── GET /performance ──────────────────────────────────────────────────────────

@router.get("/performance")
async def get_performance(db: AsyncSession = Depends(get_db)):
    """Compute live performance from agent_trades table."""
    rows = (await db.execute(
        select(AgentTrade).where(
            AgentTrade.exit_ts != None,
            AgentTrade.is_paper == settings.AGENT_PAPER_MODE,
        )
    )).scalars().all()

    if not rows:
        return {"total_trades": 0, "message": "No closed trades yet"}

    trades = list(rows)
    pnls   = [t.pnl or 0.0 for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate   = len(wins) / len(pnls)
    avg_win    = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss   = abs(sum(losses) / len(losses)) if losses else 0.0
    profit_fac = sum(wins) / max(abs(sum(losses)), 1e-9)

    # Equity curve from DB
    from db.models import AgentPerformance as AP
    perf_rows = (await db.execute(
        select(AP).where(AP.is_paper == settings.AGENT_PAPER_MODE).order_by(AP.date)
    )).scalars().all()

    equity_curve = [{"date": str(r.date), "equity": r.equity_end} for r in perf_rows]

    return {
        "total_trades":          len(trades),
        "win_rate_pct":          round(win_rate * 100, 2),
        "avg_win_inr":           round(avg_win,  2),
        "avg_loss_inr":          round(avg_loss, 2),
        "profit_factor":         round(profit_fac, 2),
        "expectancy_per_trade":  round(win_rate * avg_win - (1 - win_rate) * avg_loss, 2),
        "total_pnl":             round(sum(pnls), 2),
        "equity_curve":          equity_curve,
    }


# ── GET /positions ────────────────────────────────────────────────────────────

@router.get("/positions")
async def get_positions(db: AsyncSession = Depends(get_db)):
    """All open agent positions with live candle prices — DB-authoritative."""
    from sqlalchemy import func as sqlfunc

    # Read open trades directly from DB (source of truth — survives restarts)
    open_trades = (await db.execute(
        select(AgentTrade).where(AgentTrade.exit_price == None)
        .order_by(AgentTrade.entry_ts.asc())
    )).scalars().all()

    # Deduplicate to latest per symbol (same as hydration logic)
    latest: dict[str, AgentTrade] = {}
    for t in open_trades:
        latest[t.symbol] = t

    if not latest:
        return []

    # Batch-fetch latest candle close (same approach as /trades endpoint)
    syms = list(latest.keys())
    latest_q = (
        select(Candle.symbol, sqlfunc.max(Candle.timestamp).label("max_ts"))
        .where(Candle.symbol.in_(syms))
        .group_by(Candle.symbol)
        .subquery()
    )
    candle_q = select(Candle.symbol, Candle.close).join(
        latest_q,
        (Candle.symbol == latest_q.c.symbol) & (Candle.timestamp == latest_q.c.max_ts),
    )
    price_map: dict[str, float] = {}
    for sym, close in (await db.execute(candle_q)).fetchall():
        price_map[sym] = close

    result = []
    for symbol, t in latest.items():
        cur = price_map.get(symbol, 0.0)
        pnl = 0.0
        if cur > 0:
            pnl = (cur - t.entry_price) * t.qty if t.side == "BUY" else (t.entry_price - cur) * t.qty

        # Also pull in-memory trading metadata (trailing stop etc.) if available
        from engine.agent.agent_loop import _get_portfolio
        mem_pos = _get_portfolio().open_positions.get(symbol, {})

        result.append({
            "symbol":         symbol,
            "side":           t.side,
            "qty":            t.qty,
            "entry":          t.entry_price,
            "stop":           t.stop_price,
            "target":         t.target_price,
            "current_price":  cur,
            "unrealized_pnl": round(pnl, 2),
            "strategy":       t.strategy or "",
            "product":        t.product if hasattr(t, "product") else mem_pos.get("product", "CNC"),
            "trailing_sl":    mem_pos.get("trailing_sl"),
            "partial_done":   mem_pos.get("partial_done", False),
            "entry_ts":       t.entry_ts.isoformat() if t.entry_ts else None,
        })

    return result


# ── POST /positions/{symbol}/close ────────────────────────────────────────────

@router.post("/positions/{symbol}/close")
async def close_position(symbol: str, db: AsyncSession = Depends(get_db)):
    """Manually close an open position — DB-authoritative.

    The agent's in-memory portfolio lives in the Celery worker, not this API
    process, so the previous in-memory lookup (_get_portfolio) always 404'd when
    served by uvicorn. This reads the OpenPosition/PaperTrade — the documented
    source of truth — and closes it via the canonical close_paper_trade path,
    which is a SINGLE wallet return. It then marks the matching AgentTrade exited
    (without returning margin again) so the agent's own exit path cannot
    double-return the margin. Celery re-hydrates its portfolio from the DB.
    """
    from db.models import OpenPosition
    from paper_trading.trade_simulator import close_paper_trade
    from crawler.live_prices import PRICE_CACHE

    pos = (await db.execute(
        select(OpenPosition).where(OpenPosition.symbol == symbol)
    )).scalars().first()
    if pos is None:
        raise HTTPException(404, f"{symbol} not in open positions")

    # Price: live cache → newest candle close (so it works without the live feed).
    price = float(PRICE_CACHE.get(symbol, {}).get("price", 0) or 0)
    if price <= 0:
        price = float((await db.execute(
            select(Candle.close).where(Candle.symbol == symbol)
            .order_by(Candle.timestamp.desc()).limit(1)
        )).scalar_one_or_none() or 0.0)
    if price <= 0:
        raise HTTPException(422, f"No price available for {symbol}")

    # Canonical close: updates PaperTrade, deletes OpenPosition, returns margin ONCE.
    trade = await close_paper_trade(pos, price, "MANUAL", db)

    # Keep the agent ledger consistent — but do NOT return margin a second time.
    agent_trade = (await db.execute(
        select(AgentTrade).where(
            AgentTrade.symbol == symbol, AgentTrade.exit_ts == None,
        ).order_by(AgentTrade.entry_ts.desc()).limit(1)
    )).scalars().first()
    if agent_trade is not None:
        agent_trade.exit_price  = price
        agent_trade.exit_ts     = datetime.utcnow()
        agent_trade.exit_reason = "MANUAL"
        agent_trade.pnl         = trade.pnl

    await db.commit()
    return {
        "symbol":     symbol,
        "exit_price": round(price, 4),
        "pnl":        round(trade.pnl or 0.0, 2),
        "status":     trade.status.value,
    }


# ── POST /signal/{symbol} ─────────────────────────────────────────────────────

@router.post("/signal/{symbol}")
async def on_demand_signal(symbol: str, db: AsyncSession = Depends(get_db)):
    """On-demand signal without execution."""
    from crawler.price_feed import get_latest_candles
    from engine.agent.analyzer    import MarketAnalyzerAgent
    from engine.agent.selector    import StrategySelectorAgent
    from engine.agent.fundamentals import FundamentalsAgent
    from engine.agent.macro       import MacroSectorAgent

    candles = await get_latest_candles(symbol, settings.AGENT_TIMEFRAME, 300, db)
    if not candles or len(candles) < 30:
        raise HTTPException(422, f"Not enough data for {symbol}")

    candles_sorted = sorted(candles, key=lambda c: c.timestamp)
    df = pd.DataFrame([{
        "open": float(c.open), "high": float(c.high), "low": float(c.low),
        "close": float(c.close), "volume": float(c.volume),
    } for c in candles_sorted])

    analyzer   = MarketAnalyzerAgent()
    selector   = StrategySelectorAgent()
    fund_agent = FundamentalsAgent()
    macro      = MacroSectorAgent()

    features   = analyzer.compute_features(df)
    macro_bias = macro.bias(symbol)
    fund_score, fund_grade = await fund_agent.get_cached_grade(symbol)
    candidate  = selector.propose(symbol, df, features, macro_bias, fund_grade)

    if not candidate:
        return {
            "action": "HOLD",
            "regime": features.regime,
            "reasons": ["no_qualifying_setup"],
            "composite_score": features.composite_score,
            "macro_bias": macro_bias,
            "fund_grade": fund_grade,
        }

    return {
        **candidate.to_dict(),
        "regime":          features.regime,
        "composite_score": features.composite_score,
        "macro_bias":      macro_bias,
        "fund_grade":      fund_grade,
        "fund_score":      fund_score,
    }


# ── PUT /config ───────────────────────────────────────────────────────────────

@router.put("/config")
async def update_config(
    body: ConfigUpdate,
    x_agent_config_update: Optional[str] = Header(None),
):
    if x_agent_config_update != "yes":
        raise HTTPException(403, "Requires header: X-Agent-Config-Update: yes")

    changes = {}
    if body.enabled is not None:
        settings.AGENT_ENABLED = body.enabled
        changes["enabled"] = body.enabled
    if body.paper_mode is not None:
        settings.AGENT_PAPER_MODE = body.paper_mode
        changes["paper_mode"] = body.paper_mode
    if body.confidence_threshold is not None:
        settings.AGENT_CONFIDENCE_THRESHOLD = body.confidence_threshold
        changes["confidence_threshold"] = body.confidence_threshold
    if body.max_risk_per_trade is not None:
        settings.AGENT_MAX_RISK_PER_TRADE = body.max_risk_per_trade
        changes["max_risk_per_trade"] = body.max_risk_per_trade

    return {"updated": changes}


# ── GET /rulebook ─────────────────────────────────────────────────────────────

@router.get("/rulebook")
async def get_rulebook():
    """All Varsity-derived trading rules as structured JSON."""
    return {
        "modules": [
            {"id": "M1.1", "module": "Introduction to Stock Markets",
             "rule": "Min ₹5 Cr avg daily turnover gate",
             "condition": "avg_daily_turnover_cr >= 5.0",
             "action": "include_in_universe"},
            {"id": "M2.1", "module": "Technical Analysis",
             "rule": "Trend breakout: price > 20-bar swing high + volume spike + bull regime",
             "condition": "regime=BULL_TRENDING AND close>swing_high_20 AND vol_spike AND 55<=rsi14<=75",
             "action": "BUY with 2R target"},
            {"id": "M2.2", "module": "Technical Analysis",
             "rule": "Pullback to 20EMA in bull trend",
             "condition": "regime=BULL_TRENDING AND prev_low<=ema20 AND close>ema20 AND rsi>=40",
             "action": "BUY with 2R target"},
            {"id": "M2.3", "module": "Technical Analysis",
             "rule": "Mean reversion short at BB upper in range",
             "condition": "regime=RANGE AND close>=bb_upper AND rsi>=70 AND bearish_rejection_candle",
             "action": "SELL to BB midline"},
            {"id": "M7.1", "module": "Markets and Taxation",
             "rule": "Always net costs from P&L: brokerage + STT + GST + exchange + stamp",
             "condition": "always",
             "action": "deduct_realistic_costs"},
            {"id": "M9.1", "module": "Risk Management",
             "rule": "Max 1% equity at risk per trade",
             "condition": "trade_risk_pct > max_risk_per_trade",
             "action": "BLOCK_TRADE"},
            {"id": "M9.2", "module": "Risk Management",
             "rule": "Daily/Weekly/Monthly drawdown stops",
             "condition": "daily_dd>3% OR weekly_dd>5% OR monthly_dd>10%",
             "action": "HALT_ALL_ENTRIES"},
            {"id": "M9.3", "module": "Risk Management",
             "rule": "Consecutive loss lockout",
             "condition": "consec_losses>=2",
             "action": "BLOCK_NEW_ENTRIES_TODAY"},
            {"id": "M9.4", "module": "Risk Management",
             "rule": "Minimum 1.5:1 risk-reward",
             "condition": "risk_reward < 1.5",
             "action": "DISCARD_CANDIDATE"},
            {"id": "M11.1", "module": "Personal Finance",
             "rule": "Keep 20% cash buffer at all times",
             "condition": "cash_after_trade < 0.20 * equity",
             "action": "BLOCK_TRADE"},
            {"id": "M12.1", "module": "Innerworth",
             "rule": "Write the bear case before every entry",
             "condition": "always",
             "action": "reduce_confidence_if_strong_bear_case"},
            {"id": "M12.2", "module": "Innerworth",
             "rule": "Minimum confidence threshold",
             "condition": "confidence < 70",
             "action": "HOLD"},
            {"id": "M16.1", "module": "Quantitative Concepts",
             "rule": "Block correlated cluster (>0.7 correlation)",
             "condition": "max_corr_with_open > 0.70",
             "action": "BLOCK_TRADE"},
        ]
    }


@router.get("/performance-metrics", summary="Sharpe/Treynor/Jensen + beta + drawdown")
async def get_performance_metrics(db: AsyncSession = Depends(get_db)):
    """Risk-adjusted performance scorecard for the agent — grounded in CAPM/
    portfolio theory. Sharpe, Treynor, Jensen's alpha, regression beta, max
    drawdown, win rate, profit factor, expectancy + a plain verdict.
    """
    from engine.agent.performance_engine import compute_metrics
    return await compute_metrics(db)
