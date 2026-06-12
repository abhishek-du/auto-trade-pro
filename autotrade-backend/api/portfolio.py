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
    "/trades",
    summary="All paper trades (open + closed history)",
)
async def get_paper_trades(
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    from db.models import PaperTrade, OpenPosition

    rows = (await db.execute(
        select(PaperTrade).order_by(desc(PaperTrade.opened_at)).limit(limit)
    )).scalars().all()

    # Fetch live unrealised PnL from open_positions for all open trades in one query
    open_trade_ids = [r.id for r in rows if r.exit_price is None]
    live_pnl: dict[int, tuple] = {}
    if open_trade_ids:
        op_rows = (await db.execute(
            select(
                OpenPosition.trade_id,
                OpenPosition.unrealised_pnl,
                OpenPosition.unrealised_pct,
                OpenPosition.current_price,
            ).where(OpenPosition.trade_id.in_(open_trade_ids))
        )).all()
        for trade_id, upnl, upct, cur in op_rows:
            live_pnl[trade_id] = (upnl, upct, cur)

    out = []
    for r in rows:
        is_open = r.exit_price is None
        if is_open and r.id in live_pnl:
            pnl, pnl_pct, cur_price = live_pnl[r.id]
        else:
            pnl      = r.pnl
            pnl_pct  = r.pnl_percent
            cur_price = None

        out.append({
            "id":               f"paper_{r.id}",
            "symbol":           r.symbol.replace(".NS", "") if r.symbol else r.symbol,
            "direction":        r.direction.value if hasattr(r.direction, "value") else str(r.direction),
            "status":           r.status.value   if hasattr(r.status,    "value") else str(r.status),
            "entry_price":      r.entry_price,
            "exit_price":       r.exit_price,
            "stop_loss":        r.stop_loss,
            "take_profit":      r.take_profit,
            "size_units":       r.size_units,
            "size_usd":         r.size_usd,
            "pnl":              pnl,
            "pnl_percent":      pnl_pct,
            "current_price":    cur_price,
            "signal_confidence":r.signal_confidence,
            "pattern_name":     r.pattern_name,
            "ai_reason":        r.ai_reason,
            "opened_at":        r.opened_at.isoformat() if r.opened_at else None,
            "closed_at":        r.closed_at.isoformat() if r.closed_at else None,
            "exit_reason":      None,
            "source":           "paper",
        })
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


@router.get(
    "/capital-model",
    summary="Portfolio capital model: weights, Sharpe/Treynor/Jensen, policy",
)
async def get_capital_model(
    days: int = Query(90, ge=10, le=365, description="Look-back window for metrics"),
    db: AsyncSession = Depends(get_db),
):
    """Returns the full portfolio capital model including:
    - Current position & sector weights
    - Sharpe ratio, Treynor ratio, Jensen's alpha
    - Portfolio policy (caps)
    - Rebalance signals

    Formulas from Investment Analysis and Portfolio Management (TYBMS 2016-17):
      Sharpe  = (Rp - Rf) / σp      (reward-to-variability, p.108)
      Treynor = (Rp - Rf) / β       (reward-to-systematic-risk, p.108)
      Jensen  = Rp - [Rf + β(Rm-Rf)] (CAPM differential, p.109)
    """
    from db.models import AgentCapitalSnapshot, PortfolioPolicy
    from engine.portfolio_analytics import (
        compute_performance_metrics,
        compute_rebalance_trades,
        get_position_weights,
        get_sector_weights,
    )

    # ── Policy ────────────────────────────────────────────────────────────────
    policy = (await db.execute(select(PortfolioPolicy).limit(1))).scalar_one_or_none()
    policy_data = {
        "risk_tolerance":           policy.risk_tolerance        if policy else "MODERATE",
        "target_annual_return":     policy.target_annual_return  if policy else 15.0,
        "max_single_stock_weight":  policy.max_single_stock_weight if policy else 10.0,
        "max_sector_weight":        policy.max_sector_weight     if policy else 25.0,
        "min_cash_reserve":         policy.min_cash_reserve      if policy else 10.0,
        "rebalance_threshold":      policy.rebalance_threshold   if policy else 5.0,
        "risk_free_rate":           policy.risk_free_rate        if policy else 7.1,
    }

    # ── Current weights ───────────────────────────────────────────────────────
    pos_weights    = await get_position_weights(db)
    sector_weights = await get_sector_weights(db, pos_weights)

    # ── Performance metrics ───────────────────────────────────────────────────
    metrics = await compute_performance_metrics(db, days=days, risk_free_rate_pct=policy_data["risk_free_rate"])
    metrics.pop("daily_returns", None)  # strip raw data; not needed by UI

    # ── Rebalance signals ─────────────────────────────────────────────────────
    rebalance = await compute_rebalance_trades(db)

    # ── Latest snapshot ───────────────────────────────────────────────────────
    latest_snap = (await db.execute(
        select(AgentCapitalSnapshot)
        .order_by(AgentCapitalSnapshot.snapshot_date.desc())
        .limit(1)
    )).scalar_one_or_none()

    historical_snapshots = (await db.execute(
        select(
            AgentCapitalSnapshot.snapshot_date,
            AgentCapitalSnapshot.sharpe_ratio,
            AgentCapitalSnapshot.treynor_ratio,
            AgentCapitalSnapshot.jensens_alpha,
            AgentCapitalSnapshot.portfolio_return,
            AgentCapitalSnapshot.benchmark_return,
            AgentCapitalSnapshot.equity,
        )
        .order_by(AgentCapitalSnapshot.snapshot_date.desc())
        .limit(30)
    )).all()

    return {
        "policy":           policy_data,
        "position_weights": pos_weights,
        "sector_weights":   sector_weights,
        "metrics":          metrics,
        "rebalance_signals": rebalance,
        "latest_snapshot":  {
            "date":             str(latest_snap.snapshot_date) if latest_snap else None,
            "equity":           latest_snap.equity            if latest_snap else None,
            "sharpe_ratio":     latest_snap.sharpe_ratio      if latest_snap else None,
            "treynor_ratio":    latest_snap.treynor_ratio     if latest_snap else None,
            "jensens_alpha":    latest_snap.jensens_alpha     if latest_snap else None,
            "rebalance_needed": latest_snap.rebalance_needed  if latest_snap else False,
        },
        "history": [
            {
                "date":             str(r.snapshot_date),
                "sharpe_ratio":     r.sharpe_ratio,
                "treynor_ratio":    r.treynor_ratio,
                "jensens_alpha":    r.jensens_alpha,
                "portfolio_return": r.portfolio_return,
                "benchmark_return": r.benchmark_return,
                "equity":           r.equity,
            }
            for r in reversed(historical_snapshots)
        ],
    }


@router.put(
    "/capital-model/policy",
    summary="Update portfolio policy (position caps, risk tolerance)",
)
async def update_portfolio_policy(
    risk_tolerance:           str   = Query("MODERATE"),
    target_annual_return:     float = Query(15.0),
    max_single_stock_weight:  float = Query(10.0, ge=1.0, le=50.0),
    max_sector_weight:        float = Query(25.0, ge=5.0, le=100.0),
    min_cash_reserve:         float = Query(10.0, ge=0.0, le=50.0),
    rebalance_threshold:      float = Query(5.0,  ge=1.0, le=20.0),
    risk_free_rate:           float = Query(7.1,  ge=0.0, le=20.0),
    db: AsyncSession = Depends(get_db),
):
    """Update the portfolio risk policy that governs the agent's capital allocation."""
    from db.models import PortfolioPolicy

    policy = (await db.execute(select(PortfolioPolicy).limit(1))).scalar_one_or_none()
    if not policy:
        policy = PortfolioPolicy()
        db.add(policy)

    policy.risk_tolerance           = risk_tolerance
    policy.target_annual_return     = target_annual_return
    policy.max_single_stock_weight  = max_single_stock_weight
    policy.max_sector_weight        = max_sector_weight
    policy.min_cash_reserve         = min_cash_reserve
    policy.rebalance_threshold      = rebalance_threshold
    policy.risk_free_rate           = risk_free_rate

    await db.commit()
    return {"ok": True, "message": "Portfolio policy updated"}


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
