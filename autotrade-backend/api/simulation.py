# Simulation API — logs, analysis history, performance, and live-readiness check.

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from api.schemas import (
    AnalysisEntryOut,
    PortfolioStatsOut,
    ShouldGoLiveOut,
    SimulationLogOut,
)
from db.database import get_db
from db.models import SimulationLog
from paper_trading.simulation_logger import SimLogger
from paper_trading.virtual_wallet import VirtualWallet
from utils.config import settings

router = APIRouter(tags=["Simulation"])

_paused = False

# ── Thresholds for "should go live" gate ─────────────────────────────────────
_MIN_WIN_RATE    = 55.0   # %
_MIN_ROI         = 10.0   # %
_MIN_TRADES      = 30
_MAX_DRAWDOWN    = 20.0   # %


@router.get(
    "/logs",
    response_model=list[SimulationLogOut],
    summary="Last 100 simulation log entries with optional filters",
)
async def get_logs(
    event_type: Optional[str] = Query(None, description="e.g. TRADE_OPENED, ANALYSIS_CYCLE"),
    symbol:     Optional[str] = Query(None),
    limit:      int           = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if event_type:
        filters.append(SimulationLog.event_type == event_type.upper())
    if symbol:
        filters.append(SimulationLog.symbol == symbol.upper())

    query = (
        select(SimulationLog)
        .order_by(desc(SimulationLog.timestamp))
        .limit(limit)
    )
    if filters:
        query = query.where(and_(*filters))

    result = await db.execute(query)
    return [
        SimulationLogOut(
            id=log.id,
            event_type=log.event_type,
            symbol=log.symbol,
            message=log.message,
            data=log.data,
            timestamp=log.timestamp,
        )
        for log in result.scalars().all()
    ]


@router.get(
    "/analysis",
    response_model=list[AnalysisEntryOut],
    summary="AI analysis history — every signal the engine considered",
)
async def get_analysis_history(
    symbol: Optional[str] = Query(None),
    limit:  int           = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Shows how the AI has been thinking about each symbol, including rejected signals."""
    rows = await SimLogger.get_analysis_history(db, symbol=symbol, limit=limit)
    return [
        AnalysisEntryOut(
            id=r.get("id"),
            timestamp=r.get("timestamp"),
            symbol=r.get("symbol", ""),
            message=r.get("message", ""),
            action=r.get("action"),
            confidence=r.get("confidence"),
            final_score=r.get("final_score"),
            trade_taken=r.get("trade_taken"),
            reject_reason=r.get("reject_reason"),
        )
        for r in rows
    ]


@router.get(
    "/performance",
    response_model=PortfolioStatsOut,
    summary="Full performance evaluation report",
)
async def get_performance(db: AsyncSession = Depends(get_db)):
    """Complete strategy performance statistics for evaluating live-trade readiness."""
    return await SimLogger.get_performance_summary(db)


@router.get(
    "/should-go-live",
    response_model=ShouldGoLiveOut,
    summary="Evaluate whether the strategy is profitable enough to trade with real money",
)
async def should_go_live(db: AsyncSession = Depends(get_db)):
    """
    Runs four checks against the paper-trading record:
      - Win rate > 55 %
      - ROI > 10 %
      - At least 30 closed trades
      - Maximum drawdown < 20 %

    Returns ready=True only when **all** checks pass.
    """
    wallet  = await VirtualWallet.get_summary(db)
    perf    = await SimLogger.get_performance_summary(db)

    win_rate     = perf["win_rate"]
    roi          = perf["roi_percent"]
    total_trades = perf["trades_taken"]
    max_drawdown = wallet["max_drawdown"]

    checks = {
        "win_rate_ok":    win_rate    >= _MIN_WIN_RATE,
        "roi_ok":         roi         >= _MIN_ROI,
        "trades_ok":      total_trades >= _MIN_TRADES,
        "drawdown_ok":    max_drawdown <= _MAX_DRAWDOWN,
    }

    metrics = {
        "win_rate":         win_rate,
        "min_win_rate":     _MIN_WIN_RATE,
        "roi_percent":      roi,
        "min_roi":          _MIN_ROI,
        "total_trades":     total_trades,
        "min_trades":       _MIN_TRADES,
        "max_drawdown_pct": max_drawdown,
        "max_drawdown_limit": _MAX_DRAWDOWN,
        "checks":           checks,
    }

    if all(checks.values()):
        reason = (
            f"All checks passed: win_rate={win_rate:.1f}%, "
            f"roi={roi:+.1f}%, trades={total_trades}, "
            f"drawdown={max_drawdown:.1f}%"
        )
        ready = True
    else:
        failures = []
        if not checks["win_rate_ok"]:
            failures.append(f"win_rate {win_rate:.1f}% < {_MIN_WIN_RATE}%")
        if not checks["roi_ok"]:
            failures.append(f"roi {roi:+.1f}% < {_MIN_ROI}%")
        if not checks["trades_ok"]:
            failures.append(f"only {total_trades} trades (need {_MIN_TRADES})")
        if not checks["drawdown_ok"]:
            failures.append(f"drawdown {max_drawdown:.1f}% > {_MAX_DRAWDOWN}%")
        reason = "Not ready: " + "; ".join(failures)
        ready  = False

    return ShouldGoLiveOut(ready=ready, reason=reason, metrics=metrics)


@router.post(
    "/pause",
    summary="Pause the automated paper-trading loop",
)
async def pause_simulation():
    """Sets an in-memory flag that prevents new Celery beat tasks from opening trades."""
    global _paused
    _paused = True
    return {"paused": True, "message": "Auto-trading loop paused"}


@router.post(
    "/resume",
    summary="Resume the automated paper-trading loop",
)
async def resume_simulation():
    global _paused
    _paused = False
    return {"paused": False, "message": "Auto-trading loop resumed"}


@router.get("/status", summary="Current simulation state")
async def get_status(db: AsyncSession = Depends(get_db)):
    from paper_trading.position_tracker import PositionTracker
    wallet    = await VirtualWallet.get_summary(db)
    open_count = await PositionTracker.count_open(db)
    return {
        "paused":          _paused,
        "mode":            "PAPER_TRADING — VIRTUAL CURRENCY ONLY",
        "real_money":      False,
        "virtual_balance": wallet["balance"],
        "equity":          wallet["equity"],
        "open_positions":  open_count,
        "roi_percent":     wallet["roi_percent"],
    }
