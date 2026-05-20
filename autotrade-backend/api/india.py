"""Indian Market API — signals, FII/DII flows, options, VIX, MF, fundamentals.

All endpoints are read-only by default. POST /trigger endpoints kick off
fresh data fetches and persist results to the database.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import (
    FIIDIIFlowOut,
    FundamentalAnalysisOut,
    FundamentalDataOut,
    MutualFundOut,
    OptionsSnapshotOut,
    SectorRotationOut,
    SIPProjectionIn,
    SIPProjectionOut,
    SIPResultOut,
    SignalOut,
    TriggerResult,
    VIXScoreOut,
)
from crawler.fii_dii_crawler import fetch_fii_dii_data, save_fii_dii_to_db
from crawler.india_price_feed import fetch_india_vix
from crawler.options_chain import run_options_analysis
from db.database import get_db
from db.models import FIIDIIFlow, OptionsChainSnapshot, Signal
from engine.fundamental_analyzer import analyze_fundamentals
from engine.india_signal_generator import analyze_all_india_symbols
from engine.india_specific import (
    SECTOR_MAP,
    calculate_india_vix_score,
    calculate_sector_rotation_score,
)
from engine.mutual_fund_analyzer import analyze_all_schemes, analyze_scheme, project_sip
from engine.signal_generator import save_signal
from utils.config import settings
from utils.logger import logger

router = APIRouter(tags=["India"])

# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _fii_out(f: FIIDIIFlow) -> FIIDIIFlowOut:
    return FIIDIIFlowOut(
        id=f.id,
        date=f.date,
        fii_net_buy=f.fii_net_buy,
        dii_net_buy=f.dii_net_buy,
        fii_gross_buy=f.fii_gross_buy,
        fii_gross_sell=f.fii_gross_sell,
        dii_gross_buy=f.dii_gross_buy,
        dii_gross_sell=f.dii_gross_sell,
        market_direction=f.market_direction,
        created_at=f.created_at,
    )


def _options_out(o: OptionsChainSnapshot) -> OptionsSnapshotOut:
    return OptionsSnapshotOut(
        id=o.id,
        symbol=o.symbol,
        expiry_date=o.expiry_date,
        atm_strike=o.atm_strike,
        pcr=o.pcr,
        max_pain=o.max_pain,
        total_call_oi=o.total_call_oi,
        total_put_oi=o.total_put_oi,
        support_levels=o.support_levels,
        resistance_levels=o.resistance_levels,
        snapshot_at=o.snapshot_at,
    )


def _sip_result_out(s) -> Optional[SIPResultOut]:
    if s is None:
        return None
    return SIPResultOut(
        scheme_code=s.scheme_code,
        scheme_name=s.scheme_name,
        monthly_amount=s.monthly_amount,
        months_invested=s.months_invested,
        total_invested=s.total_invested,
        current_value=s.current_value,
        absolute_return_pct=s.absolute_return_pct,
        cagr=s.cagr,
        units_held=s.units_held,
    )


def _mf_out(a) -> MutualFundOut:
    return MutualFundOut(
        scheme_code=a.scheme_code,
        scheme_name=a.scheme_name,
        fund_house=a.fund_house,
        category=a.category,
        current_nav=a.current_nav,
        nav_date=a.nav_date,
        return_1y=a.return_1y,
        return_3y=a.return_3y,
        return_5y=a.return_5y,
        sip_1y=_sip_result_out(a.sip_1y),
        sip_3y=_sip_result_out(a.sip_3y),
        volatility=a.volatility,
        sharpe_ratio=a.sharpe_ratio,
        analyzed_at=a.analyzed_at,
    )


def _fund_out(a) -> FundamentalAnalysisOut:
    d = a.data
    return FundamentalAnalysisOut(
        symbol=a.symbol,
        data=FundamentalDataOut(
            symbol=d.symbol,
            market_cap_cr=d.market_cap_cr,
            current_price=d.current_price,
            high_52w=d.high_52w,
            low_52w=d.low_52w,
            pe_ratio=d.pe_ratio,
            pb_ratio=d.pb_ratio,
            dividend_yield_pct=d.dividend_yield_pct,
            roce_pct=d.roce_pct,
            roe_pct=d.roe_pct,
            debt_to_equity=d.debt_to_equity,
            eps=d.eps,
            book_value=d.book_value,
            face_value=d.face_value,
            fetched_at=d.fetched_at,
        ),
        pe_score=a.pe_score,
        roe_score=a.roe_score,
        debt_score=a.debt_score,
        roce_score=a.roce_score,
        composite_score=a.composite_score,
        valuation_label=a.valuation_label,
        analyzed_at=a.analyzed_at,
    )


def _vix_label(vix: float | None) -> str:
    if vix is None:
        return "UNAVAILABLE"
    if vix > 40:    return "CRASH_ZONE"
    if vix > 30:    return "EXTREME_FEAR"
    if vix > 25:    return "HIGH_FEAR"
    if vix > 20:    return "ELEVATED"
    if vix > 15:    return "NORMAL"
    if vix >= 12:   return "BULL_RUN"
    return "COMPLACENCY"


# ── Signals ───────────────────────────────────────────────────────────────────

@router.get(
    "/signals",
    response_model=list[SignalOut],
    summary="Last 30 Indian market signals",
)
async def list_india_signals(
    limit: int = Query(default=30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent signals for NSE/India symbols, newest first."""
    india_prefixes = tuple(settings.all_indian_symbols)
    result = await db.execute(
        select(Signal)
        .where(Signal.symbol.in_(india_prefixes))
        .order_by(desc(Signal.created_at))
        .limit(limit)
    )
    return [_signal_out(s) for s in result.scalars().all()]


@router.post(
    "/signals/trigger",
    response_model=TriggerResult,
    summary="Trigger a full India signal generation pass",
)
async def trigger_india_signals(db: AsyncSession = Depends(get_db)):
    """Run analyze_all_india_symbols() right now and persist results."""
    signals = await analyze_all_india_symbols(db)
    for sig in signals:
        await save_signal(sig, db)
    await db.commit()
    return TriggerResult(
        signals_generated=len(signals),
        actionable=sum(1 for s in signals if s.action in ("BUY", "SELL")),
        symbols=[s.symbol for s in signals],
    )


# ── FII / DII ─────────────────────────────────────────────────────────────────

@router.get(
    "/fii-dii",
    response_model=list[FIIDIIFlowOut],
    summary="FII/DII daily flow data (last N days)",
)
async def list_fii_dii(
    days: int = Query(default=10, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FIIDIIFlow)
        .order_by(desc(FIIDIIFlow.date))
        .limit(days)
    )
    return [_fii_out(r) for r in result.scalars().all()]


@router.post(
    "/fii-dii/trigger",
    response_model=FIIDIIFlowOut,
    summary="Fetch fresh FII/DII data from NSE and persist",
)
async def trigger_fii_dii(db: AsyncSession = Depends(get_db)):
    try:
        data = await fetch_fii_dii_data(db)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"FII/DII fetch failed: {exc}")

    row = await save_fii_dii_to_db(data, db)
    await db.commit()
    return _fii_out(row)


# ── Options chain ─────────────────────────────────────────────────────────────

@router.get(
    "/options/{symbol}",
    response_model=list[OptionsSnapshotOut],
    summary="Latest options chain snapshots for NIFTY or BANKNIFTY",
)
async def get_options_snapshot(
    symbol: str,
    limit: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """*symbol* should be NIFTY or BANKNIFTY."""
    sym = symbol.upper()
    result = await db.execute(
        select(OptionsChainSnapshot)
        .where(OptionsChainSnapshot.symbol == sym)
        .order_by(desc(OptionsChainSnapshot.snapshot_at))
        .limit(limit)
    )
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No options snapshots found for {sym}")
    return [_options_out(r) for r in rows]


@router.post(
    "/options/{symbol}/trigger",
    response_model=OptionsSnapshotOut,
    summary="Fetch a fresh NSE options chain snapshot and persist (runs for all symbols)",
)
async def trigger_options_fetch(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """Triggers a full options-chain fetch for NIFTY + BANKNIFTY; returns the
    latest snapshot for the requested symbol after the run completes.
    """
    sym = symbol.upper()
    if sym not in ("NIFTY", "BANKNIFTY"):
        raise HTTPException(status_code=400, detail="symbol must be NIFTY or BANKNIFTY")

    try:
        await run_options_analysis(db)
        await db.commit()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Options fetch failed: {exc}")

    row = await db.execute(
        select(OptionsChainSnapshot)
        .where(OptionsChainSnapshot.symbol == sym)
        .order_by(desc(OptionsChainSnapshot.snapshot_at))
        .limit(1)
    )
    snap = row.scalar_one_or_none()
    if snap is None:
        raise HTTPException(status_code=502, detail=f"No snapshot found for {sym} after fetch")
    return _options_out(snap)


# ── India VIX ─────────────────────────────────────────────────────────────────

@router.get(
    "/vix",
    response_model=VIXScoreOut,
    summary="Current India VIX and contrarian sentiment score",
)
async def get_india_vix(db: AsyncSession = Depends(get_db)):
    loop = asyncio.get_event_loop()
    vix: float | None = None
    try:
        vix = await loop.run_in_executor(None, fetch_india_vix)
    except Exception as exc:
        logger.warning(f"VIX fetch failed: {exc}")

    score = await calculate_india_vix_score(db)
    return VIXScoreOut(vix=vix, score=score, label=_vix_label(vix))


# ── Mutual funds ──────────────────────────────────────────────────────────────

@router.get(
    "/mutual-funds",
    response_model=list[MutualFundOut],
    summary="Analyze all configured mutual fund schemes",
)
async def list_mutual_funds(
    scheme_codes: Optional[str] = Query(
        default=None,
        description="Comma-separated scheme codes; defaults to WATCHLIST_MUTUAL_FUND_SCHEMES",
    ),
):
    codes = [c.strip() for c in scheme_codes.split(",")] if scheme_codes else None
    analyses = await analyze_all_schemes(codes)
    return [_mf_out(a) for a in analyses]


@router.get(
    "/mutual-funds/{scheme_code}",
    response_model=MutualFundOut,
    summary="Analyze a single mutual fund scheme",
)
async def get_mutual_fund(scheme_code: str):
    analysis = await analyze_scheme(scheme_code)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Scheme {scheme_code} not found or no NAV data")
    return _mf_out(analysis)


@router.post(
    "/sip/project",
    response_model=SIPProjectionOut,
    summary="Project SIP corpus using an assumed constant CAGR",
)
async def project_sip_returns(body: SIPProjectionIn):
    """Planning tool — does not use real NAV history."""
    if body.months <= 0 or body.monthly_amount <= 0:
        raise HTTPException(status_code=400, detail="monthly_amount and months must be positive")
    result = project_sip(body.monthly_amount, body.expected_annual_return_pct, body.months)
    return SIPProjectionOut(**result)


# ── Fundamentals ──────────────────────────────────────────────────────────────

@router.get(
    "/fundamentals/{symbol}",
    response_model=FundamentalAnalysisOut,
    summary="Fundamental analysis for an NSE-listed stock via Screener.in",
)
async def get_fundamentals(symbol: str):
    """`symbol` may include or omit the `.NS` suffix."""
    analysis = await analyze_fundamentals(symbol)
    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch fundamentals for {symbol} — symbol may be unlisted on Screener.in",
        )
    return _fund_out(analysis)


# ── Sector rotation ───────────────────────────────────────────────────────────

@router.get(
    "/sector/{symbol}",
    response_model=SectorRotationOut,
    summary="30-day relative strength score for a symbol's sector vs Nifty 50",
)
async def get_sector_rotation(symbol: str, db: AsyncSession = Depends(get_db)):
    sym = symbol.upper()
    if not sym.endswith(".NS"):
        sym = sym + ".NS"

    sector = SECTOR_MAP.get(sym)
    if sector is None:
        raise HTTPException(
            status_code=404,
            detail=f"{sym} is not in the sector map — only mapped NSE large/mid-cap symbols are supported",
        )

    score = await calculate_sector_rotation_score(sym, db)
    return SectorRotationOut(symbol=sym, sector=sector, score=score)


@router.get(
    "/sector",
    response_model=list[SectorRotationOut],
    summary="Sector rotation scores for all mapped symbols",
)
async def list_sector_rotation(db: AsyncSession = Depends(get_db)):
    results = []
    for sym, sector in SECTOR_MAP.items():
        score = await calculate_sector_rotation_score(sym, db)
        results.append(SectorRotationOut(symbol=sym, sector=sector, score=score))
    results.sort(key=lambda r: r.score, reverse=True)
    return results
