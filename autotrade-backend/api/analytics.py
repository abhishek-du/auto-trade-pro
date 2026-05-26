# Analytics API — comprehensive performance dashboard data.

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import (
    AnalyticsOut,
    DailyPnlPoint,
    EquityPoint,
    PnlBySymbolOut,
)
from db.database import get_db
from db.models import (
    PaperTrade,
    PerformanceSnapshot,
    TradeDirection,
    TradeStatus,
)
from paper_trading.virtual_wallet import VirtualWallet
from utils.config import settings

router = APIRouter(tags=["Analytics"])


@router.get(
    "/",
    response_model=AnalyticsOut,
    summary="Full analytics dashboard — win-rate, R:R, equity curve, daily P&L",
)
async def get_analytics(db: AsyncSession = Depends(get_db)):

    # ── Closed trades ─────────────────────────────────────────────────────────
    closed_result = await db.execute(
        select(PaperTrade).where(
            PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
            PaperTrade.pnl.isnot(None),
        )
    )
    closed = closed_result.scalars().all()
    n = len(closed)

    total_pnl = round(sum(t.pnl for t in closed), 4) if closed else 0.0
    wins      = [t for t in closed if (t.pnl or 0) > 0]
    win_rate  = round(len(wins) / n * 100, 2) if n else 0.0

    # ── Average R:R (planned reward / planned risk per trade) ─────────────────
    rr_values = []
    for t in closed:
        risk = abs(t.entry_price - t.stop_loss)
        if risk > 0:
            reward = abs(t.take_profit - t.entry_price)
            rr_values.append(reward / risk)
    avg_rr = round(sum(rr_values) / len(rr_values), 3) if rr_values else None

    # ── Average trade duration ────────────────────────────────────────────────
    durations = [
        (t.closed_at - t.opened_at).total_seconds() / 3600
        for t in closed
        if t.closed_at and t.opened_at
    ]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else None

    # ── Best / worst trade ────────────────────────────────────────────────────
    def _trade_stub(t: PaperTrade) -> dict:
        return {
            "id":          t.id,
            "symbol":      t.symbol,
            "direction":   t.direction.value,
            "pnl":         t.pnl,
            "pnl_percent": t.pnl_percent,
            "entry_price": t.entry_price,
            "exit_price":  t.exit_price,
            "opened_at":   t.opened_at.isoformat() if t.opened_at else None,
            "closed_at":   t.closed_at.isoformat() if t.closed_at else None,
        }

    best_trade  = _trade_stub(max(closed, key=lambda t: t.pnl or 0)) if closed else None
    worst_trade = _trade_stub(min(closed, key=lambda t: t.pnl or 0)) if closed else None

    # ── P&L by symbol ─────────────────────────────────────────────────────────
    symbol_map: dict[str, dict] = {}
    for t in closed:
        s = t.symbol
        if s not in symbol_map:
            symbol_map[s] = {"pnl": 0.0, "trades": 0, "wins": 0}
        symbol_map[s]["pnl"]    += t.pnl or 0
        symbol_map[s]["trades"] += 1
        if (t.pnl or 0) > 0:
            symbol_map[s]["wins"] += 1

    pnl_by_symbol = [
        PnlBySymbolOut(
            symbol=sym,
            trades=v["trades"],
            total_pnl=round(v["pnl"], 4),
            win_rate=round(v["wins"] / v["trades"] * 100, 2) if v["trades"] else 0.0,
        )
        for sym, v in sorted(symbol_map.items(), key=lambda x: x[1]["pnl"], reverse=True)
    ]

    # ── Trades by direction ───────────────────────────────────────────────────
    buy_count  = sum(1 for t in closed if t.direction == TradeDirection.BUY)
    sell_count = sum(1 for t in closed if t.direction == TradeDirection.SELL)
    trades_by_direction = {"BUY": buy_count, "SELL": sell_count}

    # ── Equity curve from PerformanceSnapshot ─────────────────────────────────
    snap_result = await db.execute(
        select(PerformanceSnapshot)
        .order_by(PerformanceSnapshot.date)
    )
    snapshots = snap_result.scalars().all()

    if snapshots:
        equity_curve = [
            EquityPoint(date=s.date, equity=s.equity)
            for s in snapshots
        ]
    else:
        # Fall back to reconstructing curve from closed trades
        running = settings.PAPER_TRADING_BALANCE
        equity_curve = []
        for t in sorted(closed, key=lambda x: x.closed_at or datetime.min):
            running += t.pnl or 0
            equity_curve.append(EquityPoint(
                date=t.closed_at.date() if t.closed_at else None,
                equity=round(running, 2),
            ))

    # ── Daily P&L chart (last 30 days from snapshots) ─────────────────────────
    daily_result = await db.execute(
        select(PerformanceSnapshot)
        .order_by(desc(PerformanceSnapshot.date))
        .limit(30)
    )
    daily_rows = list(reversed(daily_result.scalars().all()))
    daily_pnl_chart = [
        DailyPnlPoint(
            date=r.date,
            daily_pnl=r.daily_pnl,
            balance=r.balance,
        )
        for r in daily_rows
    ]

    # ── Total trades (all statuses) ───────────────────────────────────────────
    total_result = await db.execute(select(func.count(PaperTrade.id)))
    total_trades = int(total_result.scalar_one() or 0)

    wallet_status = await VirtualWallet.get_summary(db)
    roi_pct       = wallet_status.get("roi_percent")

    return AnalyticsOut(
        win_rate=win_rate,
        avg_rr=avg_rr,
        total_trades=total_trades,
        total_pnl=total_pnl,
        roi_pct=roi_pct,
        equity_curve=equity_curve,
        pnl_by_symbol=pnl_by_symbol,
        trades_by_direction=trades_by_direction,
        daily_pnl_chart=daily_pnl_chart,
        best_trade=best_trade,
        worst_trade=worst_trade,
        avg_trade_duration_hours=avg_duration,
    )
