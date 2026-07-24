# Trades API — paper-trade history, open positions, manual entry/close.

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from api.schemas import PaperTradeOut, TradeSummaryOut
from db.database import get_db
from db.models import PaperTrade, TradeDirection, TradeStatus
from paper_trading.position_tracker import PositionTracker
from paper_trading.trade_simulator import TradeSimulator

router = APIRouter(tags=["Trades"])


def _trade_out(t: PaperTrade) -> PaperTradeOut:
    snap = t.indicator_snapshot or {}
    return PaperTradeOut(
        id=t.id,
        symbol=t.symbol,
        direction=t.direction.value,
        status=t.status.value,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        stop_loss=t.stop_loss,
        take_profit=t.take_profit,
        size_units=t.size_units,
        size_usd=t.size_usd,
        pnl=t.pnl,
        pnl_percent=t.pnl_percent,
        ai_reason=t.ai_reason,
        signal_confidence=t.signal_confidence,
        pattern_name=t.pattern_name,
        news_sentiment_score=t.news_sentiment_score,
        slippage_applied=t.slippage_applied,
        opened_at=t.opened_at,
        closed_at=t.closed_at,
        confidence_factors=snap.get("confidence_factors") or {},
    )


# ── NOTE: literal routes must come before /{trade_id} ────────────────────────

@router.get(
    "/open",
    response_model=list[PaperTradeOut],
    summary="All currently open trades",
)
async def get_open_trades(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PaperTrade)
        .where(PaperTrade.status == TradeStatus.OPEN)
        .order_by(desc(PaperTrade.opened_at))
    )
    return [_trade_out(t) for t in result.scalars().all()]


@router.get(
    "/summary",
    response_model=TradeSummaryOut,
    summary="Aggregate trade counts and P&L",
)
async def get_trade_summary(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PaperTrade.status, PaperTrade.pnl)
    )
    rows = result.all()

    total   = len(rows)
    open_   = sum(1 for r in rows if r.status == TradeStatus.OPEN)
    closed  = sum(1 for r in rows if r.status == TradeStatus.CLOSED)
    stopped = sum(1 for r in rows if r.status == TradeStatus.STOPPED)
    wins    = sum(1 for r in rows if (r.pnl or 0) > 0 and r.status != TradeStatus.OPEN)
    losses  = sum(1 for r in rows if (r.pnl or 0) <= 0 and r.status != TradeStatus.OPEN)
    finished = closed + stopped
    win_rate = round(wins / finished * 100, 2) if finished else 0.0
    total_pnl = round(sum((r.pnl or 0) for r in rows if r.status != TradeStatus.OPEN), 4)

    return TradeSummaryOut(
        total=total,
        open=open_,
        closed=closed,
        stopped=stopped,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_pnl=total_pnl,
    )


@router.get(
    "/",
    response_model=list[PaperTradeOut],
    summary="Paper trade history with optional filters",
)
async def list_trades(
    limit:     int            = Query(100, le=500),
    symbol:    Optional[str]  = Query(None),
    status:    Optional[str]  = Query(None, description="OPEN | CLOSED | STOPPED"),
    direction: Optional[str]  = Query(None, description="BUY | SELL"),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if symbol:
        filters.append(PaperTrade.symbol == symbol.upper())
    if status:
        try:
            filters.append(PaperTrade.status == TradeStatus[status.upper()])
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    if direction:
        try:
            filters.append(PaperTrade.direction == TradeDirection[direction.upper()])
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid direction: {direction}")

    query = select(PaperTrade).order_by(desc(PaperTrade.opened_at)).limit(limit)
    if filters:
        query = query.where(and_(*filters))

    result = await db.execute(query)
    return [_trade_out(t) for t in result.scalars().all()]


@router.get(
    "/{trade_id}",
    response_model=PaperTradeOut,
    summary="Single trade by ID",
)
async def get_trade(trade_id: int, db: AsyncSession = Depends(get_db)):
    trade = await PositionTracker.get_trade(db, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return _trade_out(trade)


@router.post(
    "/{trade_id}/close",
    response_model=PaperTradeOut,
    summary="Manually close an open trade",
)
async def close_trade(
    trade_id: int,
    price:    float = Query(..., gt=0, description="Exit price"),
    db: AsyncSession = Depends(get_db),
):
    """Close a virtual position at the specified price. No real money is affected."""
    trade = await PositionTracker.get_trade(db, trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    if trade.status != TradeStatus.OPEN:
        raise HTTPException(status_code=400, detail="Trade is not open")

    fill   = TradeSimulator.execute_sell(trade.symbol, price, trade.size_units)
    closed = await PositionTracker.close_position(db, trade_id, fill, reason="MANUAL")
    if closed is None:
        raise HTTPException(status_code=500, detail="Failed to close trade")

    await db.commit()
    return _trade_out(closed)
