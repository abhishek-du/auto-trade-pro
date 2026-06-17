"""Trade Attribution Analytics — Phase 1 reporting API.

All endpoints are read-only.  Data source: paper_trades + performance_snapshots
+ simulation_logs.  No compute service required.

Routes (all under /api/v1/analytics/ prefix set in main.py):
  GET /trades              — filterable trade list with full attribution
  GET /strategies          — per-strategy performance breakdown
  GET /regimes             — per-regime attribution + strategy × regime cross-tab
  GET /exit-effectiveness  — by exit_reason: capture %, count, avg R
  GET /portfolio           — equity curve + CAGR / MaxDD / Sharpe / Sortino / Calmar
  GET /risk                — portfolio heat + sector concentration
  GET /operational         — slippage stats + API failure counts from sim_logs
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import (
    OpenPosition,
    PaperTrade,
    PerformanceSnapshot,
    SimulationLog,
    TradeStatus,
)
from utils.config import settings

router = APIRouter(tags=["Attribution Analytics"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_div(a: float, b: float, default: float | None = None) -> float | None:
    return round(a / b, 4) if b and b != 0 else default


def _max_drawdown(equity_series: list[float]) -> float:
    """Peak-to-trough max drawdown as a fraction (negative)."""
    if len(equity_series) < 2:
        return 0.0
    peak = equity_series[0]
    max_dd = 0.0
    for v in equity_series:
        peak = max(peak, v)
        dd   = (v - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
    return round(max_dd, 6)


def _cumulative_r_drawdown(r_series: list[float]) -> float:
    """Max drawdown on a cumulative-R curve."""
    if not r_series:
        return 0.0
    cum: list[float] = []
    running = 0.0
    for r in r_series:
        running += r
        cum.append(running)
    return _max_drawdown(cum)


def _sharpe_like(daily_r: list[float]) -> float | None:
    if len(daily_r) < 5:
        return None
    try:
        mu  = statistics.mean(daily_r)
        std = statistics.stdev(daily_r)
        return round(mu / std * math.sqrt(252), 3) if std > 0 else None
    except Exception:
        return None


def _sortino(daily_r: list[float]) -> float | None:
    if len(daily_r) < 5:
        return None
    try:
        mu       = statistics.mean(daily_r)
        neg      = [r for r in daily_r if r < 0]
        std_neg  = statistics.stdev(neg) if len(neg) >= 2 else 0.0
        return round(mu / std_neg * math.sqrt(252), 3) if std_neg > 0 else None
    except Exception:
        return None


def _strategy_block(trades: list[PaperTrade]) -> dict:
    """Compute full performance block for a group of closed trades."""
    n = len(trades)
    if n == 0:
        return {}
    wins   = [t for t in trades if (t.pnl or 0) > 0]
    losses = [t for t in trades if (t.pnl or 0) <= 0]
    win_pnl   = sum(t.pnl or 0 for t in wins)
    loss_pnl  = abs(sum(t.pnl or 0 for t in losses))
    avg_win   = _safe_div(win_pnl, len(wins))
    avg_loss  = _safe_div(loss_pnl, len(losses))
    pf        = _safe_div(win_pnl, loss_pnl)
    r_vals    = [t.r_multiple for t in trades if t.r_multiple is not None]
    exp_r     = round(statistics.mean(r_vals), 3) if r_vals else None
    exp_inr   = None
    if wins and losses:
        wr    = len(wins) / n
        exp_inr = round(wr * (avg_win or 0) - (1 - wr) * (avg_loss or 0), 2)
    hold_vals = [t.holding_hours for t in trades if t.holding_hours is not None]
    avg_hold  = round(statistics.mean(hold_vals), 1) if hold_vals else None
    # Strategy-level cumulative-R drawdown
    r_by_close = sorted(
        [(t.closed_at or datetime.min, t.r_multiple) for t in trades if t.r_multiple is not None],
        key=lambda x: x[0],
    )
    max_dd_r = _cumulative_r_drawdown([r for _, r in r_by_close])

    return {
        "trades":          n,
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate":        round(len(wins) / n, 4),
        "avg_win_inr":     avg_win,
        "avg_loss_inr":    avg_loss,
        "profit_factor":   pf,
        "expectancy_r":    exp_r,
        "expectancy_inr":  exp_inr,
        "avg_hold_hours":  avg_hold,
        "max_dd_r":        round(max_dd_r, 3),
        "total_pnl":       round(sum(t.pnl or 0 for t in trades), 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1.  /trades — filterable trade list
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trades", summary="Filterable closed-trade list with full attribution")
async def get_trades(
    strategy:     Optional[str]  = Query(None),
    regime:       Optional[str]  = Query(None),
    conf_bucket:  Optional[str]  = Query(None),
    segment:      Optional[str]  = Query(None),
    exit_reason:  Optional[str]  = Query(None),
    date_from:    Optional[date] = Query(None),
    date_to:      Optional[date] = Query(None),
    limit:        int            = Query(200, le=1000),
    offset:       int            = Query(0),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(PaperTrade)
        .where(PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]))
    )
    if strategy:
        q = q.where(PaperTrade.strategy_name == strategy)
    if regime:
        q = q.where(PaperTrade.regime_at_entry == regime)
    if conf_bucket:
        q = q.where(PaperTrade.confidence_bucket == conf_bucket)
    if segment:
        q = q.where(PaperTrade.instrument_segment == segment)
    if exit_reason:
        q = q.where(PaperTrade.exit_reason == exit_reason)
    if date_from:
        q = q.where(PaperTrade.opened_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.where(PaperTrade.opened_at <= datetime.combine(date_to, datetime.max.time()))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows  = (await db.execute(q.order_by(desc(PaperTrade.opened_at)).limit(limit).offset(offset))).scalars().all()

    return {
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "trades": [
            {
                "id":                 t.id,
                "symbol":             t.symbol,
                "direction":          t.direction.value,
                "status":             t.status.value,
                "strategy_name":      t.strategy_name,
                "regime_at_entry":    t.regime_at_entry,
                "regime_at_exit":     t.regime_at_exit,
                "entry_reason":       t.entry_reason,
                "exit_reason":        t.exit_reason,
                "confidence_bucket":  t.confidence_bucket,
                "instrument_segment": t.instrument_segment,
                "entry_price":        t.entry_price,
                "exit_price":         t.exit_price,
                "pnl":                t.pnl,
                "pnl_pct":            t.pnl_percent,
                "r_multiple":         t.r_multiple,
                "initial_risk_inr":   t.initial_risk_inr,
                "mfe_abs":            t.mfe_abs,
                "mfe_r":              t.mfe_r,
                "mae_abs":            t.mae_abs,
                "mae_r":              t.mae_r,
                "max_open_profit":    t.max_open_profit,
                "holding_hours":      t.holding_hours,
                "signal_confidence":  t.signal_confidence,
                "opened_at":          t.opened_at.isoformat() if t.opened_at else None,
                "closed_at":          t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in rows
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2.  /strategies
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/strategies", summary="Per-strategy performance breakdown")
async def get_strategies(
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(PaperTrade).where(
        PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
        PaperTrade.pnl.isnot(None),
    )
    if date_from:
        q = q.where(PaperTrade.opened_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.where(PaperTrade.opened_at <= datetime.combine(date_to, datetime.max.time()))

    rows = (await db.execute(q)).scalars().all()

    by_strategy: dict[str, list] = {}
    for t in rows:
        key = t.strategy_name or "UNKNOWN"
        by_strategy.setdefault(key, []).append(t)

    result = {}
    for strat, trades in sorted(by_strategy.items()):
        result[strat] = _strategy_block(trades)

    return {
        "total_trades": len(rows),
        "strategies":   result,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3.  /regimes
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/regimes", summary="Per-regime attribution and strategy × regime cross-tab")
async def get_regimes(
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(PaperTrade).where(
        PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
        PaperTrade.pnl.isnot(None),
    )
    if date_from:
        q = q.where(PaperTrade.opened_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.where(PaperTrade.opened_at <= datetime.combine(date_to, datetime.max.time()))

    rows = (await db.execute(q)).scalars().all()

    # Per-regime summary
    by_regime: dict[str, list] = {}
    for t in rows:
        key = t.regime_at_entry or "NULL"
        by_regime.setdefault(key, []).append(t)

    # Strategy × regime cross-tab
    cross: dict[str, dict[str, dict]] = {}
    for t in rows:
        strat  = t.strategy_name  or "UNKNOWN"
        regime = t.regime_at_entry or "NULL"
        cross.setdefault(strat, {}).setdefault(regime, []).append(t)

    cross_result: dict[str, dict[str, dict]] = {}
    for strat, regimes in cross.items():
        cross_result[strat] = {}
        for regime, trades in regimes.items():
            b = _strategy_block(trades)
            cross_result[strat][regime] = {
                "trades":        b.get("trades"),
                "win_rate":      b.get("win_rate"),
                "expectancy_r":  b.get("expectancy_r"),
                "profit_factor": b.get("profit_factor"),
            }

    return {
        "by_regime": {r: _strategy_block(t) for r, t in by_regime.items()},
        "cross_tab": cross_result,
        "note": (
            "regime_at_entry from live agent uses BULL_TRENDING/BEAR_TRENDING/RANGE/UNKNOWN. "
            "morning_regime.py AGGRESSIVE/SELECTIVE/WAIT taxonomy is separate and not in this table."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4.  /exit-effectiveness
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/exit-effectiveness", summary="Profit capture % and R by exit reason")
async def get_exit_effectiveness(
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(PaperTrade).where(
        PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
        PaperTrade.pnl.isnot(None),
    )
    if date_from:
        q = q.where(PaperTrade.opened_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.where(PaperTrade.opened_at <= datetime.combine(date_to, datetime.max.time()))

    rows = (await db.execute(q)).scalars().all()

    by_exit: dict[str, list] = {}
    for t in rows:
        key = t.exit_reason or "UNKNOWN"
        by_exit.setdefault(key, []).append(t)

    result = {}
    for reason, trades in sorted(by_exit.items()):
        pnl_vals = [t.pnl or 0 for t in trades]
        r_vals   = [t.r_multiple for t in trades if t.r_multiple is not None]

        # profit_capture = realised_pnl / max_open_profit for ever-green trades
        cap_vals = []
        for t in trades:
            if t.max_open_profit and t.max_open_profit > 0 and t.pnl is not None:
                cap_vals.append((t.pnl / t.max_open_profit) * 100)

        # give_back = MFE - realised_pnl (profit surrendered to trail/stop)
        give_back_vals = []
        for t in trades:
            if t.mfe_abs is not None and t.pnl is not None:
                give_back_vals.append(t.mfe_abs - t.pnl)

        result[reason] = {
            "count":              len(trades),
            "avg_pnl":            round(statistics.mean(pnl_vals), 2) if pnl_vals else None,
            "total_pnl":          round(sum(pnl_vals), 2),
            "avg_r":              round(statistics.mean(r_vals), 3) if r_vals else None,
            "win_rate":           round(sum(1 for p in pnl_vals if p > 0) / len(pnl_vals), 3) if pnl_vals else None,
            "profit_capture_pct": round(statistics.mean(cap_vals), 1) if cap_vals else None,
            "avg_give_back":      round(statistics.mean(give_back_vals), 2) if give_back_vals else None,
        }

    # Summary: which exit mechanism captures the most profit
    return {
        "by_exit_reason": result,
        "interpretation": (
            "profit_capture_pct: how much of the peak unrealised profit was realised. "
            "avg_give_back: average ₹ surrendered between MFE and close. "
            "Lower give_back and higher capture = better exit timing."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5.  /portfolio
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/portfolio", summary="Equity curve + CAGR / MaxDD / Sharpe / Sortino / Calmar")
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    snaps = (await db.execute(
        select(PerformanceSnapshot).order_by(PerformanceSnapshot.date)
    )).scalars().all()

    if not snaps:
        return {"error": "No performance_snapshots found — run the daily snapshot task first"}

    equity_series = [float(s.equity) for s in snaps]
    dates         = [s.date for s in snaps]

    # Daily returns
    daily_ret: list[float] = []
    for i in range(1, len(equity_series)):
        prev = equity_series[i - 1]
        if prev and prev > 0:
            daily_ret.append((equity_series[i] - prev) / prev)

    # CAGR
    n_days  = max((dates[-1] - dates[0]).days, 1)
    start_e = equity_series[0]
    end_e   = equity_series[-1]
    cagr    = ((end_e / start_e) ** (252 / n_days) - 1) if start_e > 0 else None

    max_dd   = _max_drawdown(equity_series)
    sharpe   = _sharpe_like(daily_ret)
    sortino  = _sortino(daily_ret)
    calmar   = _safe_div(cagr or 0, abs(max_dd)) if max_dd < 0 else None

    # Rolling 60-day Sharpe (last 60 data points)
    rolling_sharpe = _sharpe_like(daily_ret[-60:]) if len(daily_ret) >= 10 else None

    # Monthly returns heatmap
    from collections import defaultdict
    monthly: dict[str, dict[str, float]] = defaultdict(dict)
    prev_monthly_eq: dict[str, float] = {}
    for snap in snaps:
        ym  = str(snap.date)[:7]   # "YYYY-MM"
        yr  = str(snap.date)[:4]
        mon = str(snap.date)[5:7]
        prev_monthly_eq[ym] = float(snap.equity)

    months_sorted = sorted(prev_monthly_eq.keys())
    for i in range(1, len(months_sorted)):
        cur_m  = months_sorted[i]
        prev_m = months_sorted[i - 1]
        cur_e  = prev_monthly_eq[cur_m]
        pre_e  = prev_monthly_eq[prev_m]
        yr     = cur_m[:4]
        mon    = cur_m[5:7]
        if pre_e and pre_e > 0:
            monthly[yr][mon] = round((cur_e - pre_e) / pre_e * 100, 2)

    # Recovery days (trough → prior-peak)
    peak_so_far = equity_series[0]
    in_dd = False
    dd_start_idx = 0
    recovery_days: list[int] = []
    for i, e in enumerate(equity_series):
        if e >= peak_so_far:
            if in_dd:
                recovery_days.append((dates[i] - dates[dd_start_idx]).days)
            peak_so_far = e
            in_dd = False
        elif e < peak_so_far and not in_dd:
            in_dd = True
            dd_start_idx = i

    return {
        "equity_curve": [
            {"date": str(d), "equity": round(e, 2)}
            for d, e in zip(dates, equity_series)
        ],
        "metrics": {
            "cagr":              round(cagr * 100, 2) if cagr is not None else None,
            "max_drawdown_pct":  round(max_dd * 100, 2),
            "sharpe":            sharpe,
            "sortino":           sortino,
            "calmar":            round(calmar, 3) if calmar else None,
            "rolling_60d_sharpe": rolling_sharpe,
            "total_return_pct":  round((end_e - start_e) / start_e * 100, 2) if start_e else None,
            "start_equity":      round(start_e, 2),
            "end_equity":        round(end_e, 2),
            "n_days":            n_days,
            "avg_recovery_days": round(statistics.mean(recovery_days), 0) if recovery_days else None,
        },
        "monthly_returns": monthly,
        "daily_pnl": [
            {"date": str(s.date), "pnl": s.daily_pnl}
            for s in snaps
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.  /risk
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/risk", summary="Portfolio heat, sector concentration, position sizing")
async def get_risk(db: AsyncSession = Depends(get_db)):
    # Current equity
    try:
        from paper_trading.virtual_wallet import VirtualWallet as VW
        wallet = await VW.get_summary(db)
        equity = float(wallet.get("equity") or settings.AGENT_EQUITY)
    except Exception:
        equity = float(settings.AGENT_EQUITY)

    # Open positions
    open_pos = (await db.execute(select(OpenPosition))).scalars().all()

    # Portfolio heat = sum of initial_risk_inr for all OPEN paper_trades
    open_trade_ids = [p.trade_id for p in open_pos if p.trade_id]
    heat_r = 0.0
    heat_trades = []
    if open_trade_ids:
        pt_rows = (await db.execute(
            select(PaperTrade).where(PaperTrade.id.in_(open_trade_ids))
        )).scalars().all()
        for t in pt_rows:
            r = t.initial_risk_inr or 0.0
            heat_r += r
            heat_trades.append({
                "symbol":           t.symbol,
                "strategy":         t.strategy_name,
                "size_usd":         t.size_usd,
                "initial_risk_inr": r,
                "risk_pct":         round(r / equity * 100, 2) if equity > 0 else None,
            })

    portfolio_heat_pct = round(heat_r / equity * 100, 2) if equity > 0 else None

    # Sector exposure via PortfolioHolding (if populated)
    sector_exposure: dict[str, float] = {}
    try:
        from db.models import PortfolioHolding
        holdings = (await db.execute(select(PortfolioHolding))).scalars().all()
        for h in holdings:
            sec = h.sector or "UNKNOWN"
            # current_value is the live notional
            cv = float(getattr(h, "current_value", 0) or 0)
            sector_exposure[sec] = sector_exposure.get(sec, 0.0) + cv
        if equity > 0:
            sector_exposure = {s: round(v / equity * 100, 2) for s, v in sector_exposure.items()}
    except Exception:
        sector_exposure = {}

    # Historical avg / peak heat from capital snapshots
    from db.models import AgentCapitalSnapshot
    cap_snaps = (await db.execute(
        select(AgentCapitalSnapshot).order_by(AgentCapitalSnapshot.snapshot_date.desc()).limit(30)
    )).scalars().all()

    return {
        "current": {
            "equity":             round(equity, 2),
            "open_positions":     len(open_pos),
            "portfolio_heat_inr": round(heat_r, 2),
            "portfolio_heat_pct": portfolio_heat_pct,
            "positions":          heat_trades,
        },
        "sector_exposure_pct": sector_exposure,
        "recent_snapshots": [
            {
                "date":          str(s.snapshot_date),
                "equity":        s.equity,
                "n_positions":   s.num_positions,
                "cash_pct":      s.cash_pct,
                "sector_weights": s.sector_weights,
            }
            for s in reversed(cap_snaps)
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7.  /operational
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/operational", summary="Slippage, API failures, and operational health")
async def get_operational(
    lookback_days: int = Query(7, le=90),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    # Simulation-log event counts
    log_rows = (await db.execute(
        select(SimulationLog)
        .where(SimulationLog.timestamp >= cutoff)
        .order_by(desc(SimulationLog.timestamp))
        .limit(2000)
    )).scalars().all()

    event_counts: dict[str, int] = {}
    for row in log_rows:
        event_counts[row.event_type] = event_counts.get(row.event_type, 0) + 1

    # Classify events by severity
    critical_events = [
        e for e in event_counts
        if any(k in e.upper() for k in ("ERROR", "FAIL", "TIMEOUT", "EXPIRE", "REJECT", "LOGIN"))
    ]
    warn_events = [
        e for e in event_counts
        if any(k in e.upper() for k in ("WARN", "SKIP", "BLOCK", "RETRY"))
    ]

    # Slippage from closed paper_trades
    slip_rows = (await db.execute(
        select(
            func.avg(PaperTrade.slippage_applied),
            func.max(PaperTrade.slippage_applied),
            func.count(PaperTrade.id),
        ).where(
            PaperTrade.opened_at >= cutoff,
            PaperTrade.slippage_applied.isnot(None),
        )
    )).one()

    avg_slip, max_slip, trade_count = slip_rows

    # Realised slippage as bps of entry price
    slip_detail = (await db.execute(
        select(
            PaperTrade.slippage_applied,
            PaperTrade.entry_price,
        ).where(
            PaperTrade.opened_at >= cutoff,
            PaperTrade.slippage_applied.isnot(None),
            PaperTrade.entry_price > 0,
        ).limit(500)
    )).all()

    slip_bps = [
        round(abs(row[0]) / row[1] * 10_000, 2)
        for row in slip_detail if row[1] and row[1] > 0
    ]
    avg_slip_bps = round(statistics.mean(slip_bps), 2) if slip_bps else None

    return {
        "lookback_days": lookback_days,
        "event_summary": {
            "total_log_entries": len(log_rows),
            "by_event_type":     dict(sorted(event_counts.items(), key=lambda x: -x[1])),
            "critical_types":    critical_events,
            "warn_types":        warn_events,
        },
        "slippage": {
            "trades_in_window":  int(trade_count or 0),
            "avg_slip_inr":      round(float(avg_slip or 0), 6),
            "max_slip_inr":      round(float(max_slip or 0), 6),
            "avg_slip_bps":      avg_slip_bps,
            "alert": (
                "HIGH" if (avg_slip_bps or 0) > 10
                else "OK"
            ),
        },
        "recent_critical_logs": [
            {
                "ts":      row.timestamp.isoformat(),
                "event":   row.event_type,
                "symbol":  row.symbol,
                "message": row.message[:200],
            }
            for row in log_rows
            if any(k in row.event_type.upper()
                   for k in ("ERROR", "FAIL", "TIMEOUT", "REJECT"))
        ][:20],
    }
