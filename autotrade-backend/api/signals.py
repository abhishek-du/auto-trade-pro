# Signals API — signal history and on-demand generation.

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import SignalOut, TriggerResult
from crawler.price_feed import fetch_candles
from db.database import get_db
from db.models import Signal
from engine.signal_generator import analyze_all_symbols, generate_signal, save_signal

router = APIRouter(tags=["Signals"])


def _signal_out(s: Signal) -> SignalOut:
    return SignalOut(
        id=s.id,
        symbol=s.symbol,
        timeframe=s.timeframe,
        signal_type=s.signal_type.value if hasattr(s.signal_type, "value") else str(s.signal_type),
        confidence=s.confidence,
        pattern_name=s.pattern_name,
        news_sentiment=s.news_sentiment,
        final_score=s.final_score,
        created_at=s.created_at,
    )


@router.get(
    "/",
    response_model=list[SignalOut],
    summary="Last 20 signals across all symbols",
)
async def list_signals(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Signal).order_by(desc(Signal.created_at)).limit(20)
    )
    return [_signal_out(s) for s in result.scalars().all()]


@router.post(
    "/trigger",
    response_model=TriggerResult,
    summary="Manually trigger analyze_all_symbols() — for testing",
)
async def trigger_analysis(db: AsyncSession = Depends(get_db)):
    """Run a full signal-generation pass right now and persist results."""
    signals = await analyze_all_symbols(db)
    for sig in signals:
        await save_signal(sig, db)
    await db.commit()
    return TriggerResult(
        signals_generated=len(signals),
        actionable=sum(1 for s in signals if s.action in ("BUY", "SELL")),
        symbols=[s.symbol for s in signals],
    )


@router.post(
    "/seed",
    summary="Fetch price data then run signal analysis — use this to bootstrap before Celery starts",
)
async def seed_and_analyse(db: AsyncSession = Depends(get_db)):
    """
    1. Crawls OHLCV candles for all watchlist symbols via yfinance (no API key needed).
    2. Immediately runs signal analysis on the freshly fetched data.
    Use this once after starting the backend to populate the DB without waiting
    for the Celery beat schedule.
    """
    from crawler.price_feed import run_price_crawl

    crawl = await run_price_crawl(db)
    await db.commit()

    signals = await analyze_all_symbols(db)
    for sig in signals:
        await save_signal(sig, db)
    await db.commit()

    return {
        "step_1_crawl": {
            "symbols_fetched": crawl.get("total_symbols", 0),
            "candles_saved":   crawl.get("total_candles_saved", 0),
            "errors":          crawl.get("errors", []),
        },
        "step_2_signals": {
            "signals_generated": len(signals),
            "actionable":        sum(1 for s in signals if s.action in ("BUY", "SELL")),
            "symbols":           [s.symbol for s in signals],
        },
    }


@router.get(
    "/{symbol:path}",
    response_model=list[SignalOut],
    summary="Last 10 signals for a specific symbol",
)
async def get_signals_for_symbol(symbol: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Signal)
        .where(Signal.symbol == symbol.upper())
        .order_by(desc(Signal.created_at))
        .limit(10)
    )
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No signals found for {symbol}")
    return [_signal_out(s) for s in rows]
