# Portfolio API — virtual wallet, open positions, snapshots, and reset.

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import (
    OpenPositionOut,
    PerformanceSnapshotOut,
    PortfolioStatsOut,
    WalletSummary,
)
from crawler.price_feed import get_latest_price
from db.database import get_db
from db.models import PerformanceSnapshot
from paper_trading.pnl_calculator import PnLCalculator
from paper_trading.position_tracker import PositionTracker
from paper_trading.simulation_logger import SimLogger
from paper_trading.virtual_wallet import VirtualWallet

router = APIRouter(tags=["Portfolio"])


@router.get(
    "/",
    response_model=WalletSummary,
    summary="Virtual wallet summary",
)
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    """Current balance, equity, P&L, win-rate, and ROI for the paper-trading account."""
    return await VirtualWallet.get_summary(db)


@router.get(
    "/positions",
    response_model=list[OpenPositionOut],
    summary="All currently open virtual positions",
)
async def get_open_positions(db: AsyncSession = Depends(get_db)):
    positions = await PositionTracker.get_open_positions(db)

    # Pull trade-management state (targets, ATR, trailing flag) from the linked
    # PaperTrade.indicator_snapshot so the UI can show trailing-stop status.
    from db.models import PaperTrade
    trade_ids = [p.trade_id for p in positions]
    mgmt_by_trade: dict[int, dict] = {}
    if trade_ids:
        rows = (await db.execute(
            select(PaperTrade.id, PaperTrade.indicator_snapshot)
            .where(PaperTrade.id.in_(trade_ids))
        )).all()
        for tid, snap in rows:
            tm = (snap or {}).get("trade_mgmt") if isinstance(snap, dict) else None
            if tm:
                mgmt_by_trade[tid] = tm

    out = []
    for p in positions:
        tm = mgmt_by_trade.get(p.trade_id, {})
        out.append(OpenPositionOut(
            id=p.id,
            symbol=p.symbol,
            direction=p.direction.value,
            entry_price=p.entry_price,
            current_price=p.current_price,
            stop_loss=p.stop_loss,
            take_profit=p.take_profit,
            size_units=p.size_units,
            size_usd=p.size_usd,
            unrealised_pnl=p.unrealised_pnl,
            unrealised_pct=p.unrealised_pct,
            trade_id=p.trade_id,
            opened_at=p.opened_at,
            last_updated=p.last_updated,
            target_1=tm.get("target_1"),
            target_2=tm.get("target_2"),
            atr=tm.get("atr"),
            trailing=bool(tm.get("trailing", False)),
            level_source=tm.get("level_source"),
        ))
    return out


@router.get(
    "/snapshots",
    response_model=list[PerformanceSnapshotOut],
    summary="Last 30 daily equity snapshots (equity curve data)",
)
async def get_snapshots(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PerformanceSnapshot)
        .order_by(desc(PerformanceSnapshot.date))
        .limit(30)
    )
    rows = list(reversed(result.scalars().all()))
    return [
        PerformanceSnapshotOut(
            id=r.id,
            date=r.date,
            balance=r.balance,
            equity=r.equity,
            daily_pnl=r.daily_pnl,
            trades_today=r.trades_today,
            win_rate_today=r.win_rate_today,
            snapshot_at=r.snapshot_at,
        )
        for r in rows
    ]


@router.get(
    "/stats",
    response_model=PortfolioStatsOut,
    summary="Full performance evaluation from the simulation logger",
)
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Aggregated stats used to judge whether the strategy is ready to go live."""
    return await SimLogger.get_performance_summary(db)


@router.post(
    "/reset",
    response_model=WalletSummary,
    summary="Reset virtual wallet to starting balance",
)
async def reset_portfolio(
    confirm: bool = Query(False, description="Must be true to proceed"),
    db: AsyncSession = Depends(get_db),
):
    """Wipe all open positions and reset the virtual balance.  No real money is affected."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=true to confirm the reset. All positions will be closed.",
        )
    result = await VirtualWallet.reset(db)
    await db.commit()
    return result


@router.post(
    "/reconcile",
    summary="Close stale agent trades and re-sync wallet to ₹5L unified capital pool",
)
async def reconcile_capital(
    confirm: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """One-time maintenance: close all over-deployed stale agent trades, reset wallet
    to the unified ₹5L capital pool.  Safe to run multiple times (idempotent).
    """
    if not confirm:
        raise HTTPException(400, "Pass ?confirm=true to proceed")

    from datetime import datetime as _dt
    from db.models import AgentTrade as AT
    from sqlalchemy import select, update

    now = _dt.utcnow()

    # 1. Close all open agent trades (stale over-deployment artefacts)
    stale = (await db.execute(
        select(AT).where(AT.exit_ts == None, AT.is_paper == True)
    )).scalars().all()

    agent_closed = 0
    for t in stale:
        t.exit_price  = t.entry_price      # 0 PnL — we can't recover real prices
        t.exit_ts     = now
        t.exit_reason = "RECONCILE_CLEANUP"
        t.pnl         = 0.0
        t.pnl_pct     = 0.0
        agent_closed += 1

    # 2. Also reset the in-memory agent portfolio so it doesn't re-open them
    try:
        from engine.agent.agent_loop import _get_portfolio
        port = _get_portfolio()
        port.open_positions.clear()
        port.cash = port.equity
    except Exception:
        pass

    # 3. Reset the VirtualWallet (closes open paper positions, restores ₹5L)
    await VirtualWallet.reset(db)

    await db.commit()
    logger.info(
        f"[reconcile] closed {agent_closed} stale agent trades, "
        f"reset wallet to ₹5L unified pool"
    )
    return {
        "agent_trades_closed": agent_closed,
        "wallet": await VirtualWallet.get_summary(db),
    }
