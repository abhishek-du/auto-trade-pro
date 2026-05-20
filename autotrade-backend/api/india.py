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
    FundComparisonOut,
    MutualFundNAVOut,
    MutualFundWithSignalOut,
    OptionsSnapshotOut,
    SectorRotationOut,
    SIPProjectionIn,
    SIPProjectionOut,
    SIPSimulationOut,
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
from db.models import MutualFundNAV
from engine.mutual_fund_analyzer import (
    compare_funds,
    fetch_and_save_nav,
    get_mf_buy_signal,
    project_sip,
    simulate_sip,
)
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


def _mf_nav_out(r: MutualFundNAV) -> MutualFundNAVOut:
    return MutualFundNAVOut(
        id=r.id,
        scheme_code=r.scheme_code,
        scheme_name=r.scheme_name,
        nav=r.nav,
        prev_nav=r.prev_nav,
        change=r.change,
        change_pct=r.change_pct,
        category=r.category,
        one_month_return=r.one_month_return,
        three_month_return=r.three_month_return,
        one_year_return=r.one_year_return,
        three_year_return=r.three_year_return,
        recorded_at=r.recorded_at,
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
    response_model=list[MutualFundWithSignalOut],
    summary="All tracked fund NAVs with performance metrics and buy signal",
)
async def list_mutual_funds(
    scheme_codes: Optional[str] = Query(
        default=None,
        description="Comma-separated scheme codes; defaults to WATCHLIST_MUTUAL_FUND_SCHEMES",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Returns the latest NAV snapshot for each scheme from the DB.
    If a scheme has no DB record yet, a fresh NAV fetch is triggered automatically.
    """
    codes = (
        [c.strip() for c in scheme_codes.split(",")]
        if scheme_codes
        else settings.WATCHLIST_MUTUAL_FUND_SCHEMES
    )

    results: list[MutualFundWithSignalOut] = []
    for code in codes:
        # Check DB for latest record
        latest = (await db.execute(
            select(MutualFundNAV)
            .where(MutualFundNAV.scheme_code == code)
            .order_by(desc(MutualFundNAV.recorded_at))
            .limit(1)
        )).scalar_one_or_none()

        # Auto-fetch if no record
        if latest is None:
            await fetch_and_save_nav(code, db)
            await db.commit()
            latest = (await db.execute(
                select(MutualFundNAV)
                .where(MutualFundNAV.scheme_code == code)
                .order_by(desc(MutualFundNAV.recorded_at))
                .limit(1)
            )).scalar_one_or_none()

        if latest is None:
            logger.warning(f"list_mutual_funds: no data for scheme {code}")
            continue

        signal_data = await get_mf_buy_signal(code, db)

        results.append(MutualFundWithSignalOut(
            scheme_code=latest.scheme_code,
            scheme_name=latest.scheme_name,
            current_nav=latest.nav,
            one_month_return=latest.one_month_return,
            three_month_return=latest.three_month_return,
            one_year_return=latest.one_year_return,
            three_year_return=latest.three_year_return,
            change_pct=latest.change_pct,
            category=latest.category,
            recorded_at=latest.recorded_at,
            signal=signal_data.get("signal", "HOLD"),
            reason=signal_data.get("reason", ""),
            high_52w=signal_data.get("high_52w"),
            dip_from_high_pct=signal_data.get("dip_from_high_pct"),
            vix=signal_data.get("vix"),
        ))

    return results


@router.get(
    "/mutual-funds/{scheme_code}/nav",
    response_model=list[MutualFundNAVOut],
    summary="Historical NAV snapshots for a scheme (from DB)",
)
async def get_mf_nav_history(
    scheme_code: str,
    limit: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(MutualFundNAV)
        .where(MutualFundNAV.scheme_code == scheme_code)
        .order_by(desc(MutualFundNAV.recorded_at))
        .limit(limit)
    )).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No NAV data for scheme {scheme_code}")
    return [_mf_nav_out(r) for r in rows]


@router.post(
    "/mutual-funds/{scheme_code}/refresh",
    response_model=MutualFundNAVOut,
    summary="Fetch fresh NAV from AMFI and persist to DB",
)
async def refresh_mf_nav(scheme_code: str, db: AsyncSession = Depends(get_db)):
    summary = await fetch_and_save_nav(scheme_code, db)
    if not summary:
        raise HTTPException(status_code=502, detail=f"Failed to fetch NAV for scheme {scheme_code}")
    await db.commit()
    latest = (await db.execute(
        select(MutualFundNAV)
        .where(MutualFundNAV.scheme_code == scheme_code)
        .order_by(desc(MutualFundNAV.recorded_at))
        .limit(1)
    )).scalar_one()
    return _mf_nav_out(latest)


@router.get(
    "/mutual-funds/{scheme_code}/signal",
    response_model=MutualFundWithSignalOut,
    summary="BUY / HOLD signal for a single fund scheme",
)
async def get_fund_signal(scheme_code: str, db: AsyncSession = Depends(get_db)):
    latest = (await db.execute(
        select(MutualFundNAV)
        .where(MutualFundNAV.scheme_code == scheme_code)
        .order_by(desc(MutualFundNAV.recorded_at))
        .limit(1)
    )).scalar_one_or_none()

    if latest is None:
        raise HTTPException(
            status_code=404,
            detail=f"No NAV data for scheme {scheme_code}. Call POST /refresh first.",
        )

    signal_data = await get_mf_buy_signal(scheme_code, db)
    return MutualFundWithSignalOut(
        scheme_code=latest.scheme_code,
        scheme_name=latest.scheme_name,
        current_nav=latest.nav,
        one_month_return=latest.one_month_return,
        three_month_return=latest.three_month_return,
        one_year_return=latest.one_year_return,
        three_year_return=latest.three_year_return,
        change_pct=latest.change_pct,
        category=latest.category,
        recorded_at=latest.recorded_at,
        signal=signal_data.get("signal", "HOLD"),
        reason=signal_data.get("reason", ""),
        high_52w=signal_data.get("high_52w"),
        dip_from_high_pct=signal_data.get("dip_from_high_pct"),
        vix=signal_data.get("vix"),
    )


@router.get(
    "/mutual-funds/compare",
    response_model=list[FundComparisonOut],
    summary="Compare funds by 1Y/3Y return and consistency; top fund highlighted",
)
async def compare_mutual_funds(
    scheme_codes: Optional[str] = Query(
        default=None,
        description="Comma-separated scheme codes; defaults to WATCHLIST_MUTUAL_FUND_SCHEMES",
    ),
    db: AsyncSession = Depends(get_db),
):
    codes = (
        [c.strip() for c in scheme_codes.split(",")]
        if scheme_codes
        else settings.WATCHLIST_MUTUAL_FUND_SCHEMES
    )
    entries = await compare_funds(codes, db)
    await db.commit()
    return [FundComparisonOut(**e) for e in entries]


@router.get(
    "/mutual-funds/{scheme_code}/sip",
    response_model=SIPSimulationOut,
    summary="Simulate a monthly SIP using actual historical NAV data from DB",
)
async def simulate_sip_endpoint(
    scheme_code: str,
    monthly_amount: float = Query(default=5000.0, gt=0, description="Monthly SIP amount in INR"),
    months: int = Query(default=36, ge=1, le=360, description="Number of months to simulate"),
    db: AsyncSession = Depends(get_db),
):
    result = await simulate_sip(scheme_code, monthly_amount, months, db)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No historical NAV data for scheme {scheme_code}",
        )
    return SIPSimulationOut(**result)


@router.post(
    "/sip/project",
    response_model=SIPProjectionOut,
    summary="Project SIP corpus using an assumed constant CAGR (planning tool)",
)
async def project_sip_returns(body: SIPProjectionIn):
    """Does NOT use real NAV history. Use /mutual-funds/{code}/sip for real data."""
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
