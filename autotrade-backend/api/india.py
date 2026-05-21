"""Indian Market API — signals, FII/DII, options, VIX, MF, fundamentals, seed.

Registered at /api/v1/india in main.py.
All endpoints are read-only except POST /seed and POST trigger endpoints.
"""

from __future__ import annotations

import asyncio
import datetime
import time as _time
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import (
    FIIDIIChartPoint,
    FIIDIIFlowOut,
    FIIDIISummaryOut,
    FIIDIITodayOut,
    FIIDIIAvgOut,
    FundamentalDataOut,
    FundComparisonOut,
    MarketIndexOut,
    MarketStatusOut,
    MutualFundBriefOut,
    MutualFundListOut,
    MutualFundNAVOut,
    MutualFundWithSignalOut,
    OptionsChainDetailOut,
    OptionsSnapshotOut,
    SectorPerfItem,
    SectorPerfOut,
    SectorRotationOut,
    SeedResultOut,
    SIPBriefOut,
    SIPProjectionIn,
    SIPProjectionOut,
    SIPSimulationOut,
    SignalOut,
    TriggerResult,
    VIXScoreOut,
)
from crawler.fii_dii_crawler import fetch_fii_dii_data, save_fii_dii_to_db
from crawler.india_price_feed import fetch_india_vix, is_nse_market_open
from crawler.options_chain import run_options_analysis
from db.database import get_db
from db.models import (
    Candle,
    FIIDIIFlow,
    FundamentalData,
    MutualFundNAV,
    OptionsChainSnapshot,
    Signal,
)
from engine.fundamental_analyzer import get_fundamentals_for_symbol, run_fundamental_update
from engine.india_signal_generator import analyze_all_india_symbols
from engine.india_specific import (
    SECTOR_INDEX,
    SECTOR_MAP,
    _NIFTY50,
    calculate_india_vix_score,
    calculate_sector_rotation_score,
)
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

_IST = ZoneInfo("Asia/Kolkata")

# NSE exchange holidays (2025–2026)
_NSE_HOLIDAYS: dict[str, str] = {
    "2025-01-26": "Republic Day",
    "2025-02-26": "Mahashivratri",
    "2025-03-14": "Holi",
    "2025-03-31": "Id-ul-Fitr",
    "2025-04-10": "Ram Navami",
    "2025-04-14": "Dr. Ambedkar Jayanti",
    "2025-04-18": "Good Friday",
    "2025-05-01": "Maharashtra Day",
    "2025-08-15": "Independence Day",
    "2025-09-02": "Ganesh Chaturthi",
    "2025-10-02": "Gandhi Jayanti",
    "2025-10-20": "Diwali Laxmi Puja",
    "2025-10-21": "Diwali Balipratipada",
    "2025-11-05": "Guru Nanak Jayanti",
    "2025-12-25": "Christmas",
    "2026-01-26": "Republic Day",
    "2026-02-18": "Mahashivratri",
    "2026-03-30": "Holi",
    "2026-04-02": "Ram Navami",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-08-15": "Independence Day",
    "2026-09-21": "Ganesh Chaturthi",
    "2026-10-02": "Gandhi Jayanti",
    "2026-10-21": "Dussehra",
    "2026-11-04": "Diwali Laxmi Puja",
    "2026-11-05": "Diwali Balipratipada",
    "2026-11-25": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}


# ── Sync helpers (run in executor) ────────────────────────────────────────────

def _fetch_index_prices() -> dict[str, dict]:
    """Fetch NIFTY / BANKNIFTY / SENSEX prices via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        neutral = {"price": None, "change": None, "change_pct": None}
        return {"nifty": neutral, "bank_nifty": neutral, "sensex": neutral}

    result: dict[str, dict] = {}
    for key, ticker in [("nifty", "^NSEI"), ("bank_nifty", "^NSEBANK"), ("sensex", "^BSESN")]:
        try:
            fi     = yf.Ticker(ticker).fast_info
            price  = getattr(fi, "last_price", None)
            prev   = getattr(fi, "previous_close", None)
            if price and prev and prev > 0:
                result[key] = {
                    "price":      round(float(price), 2),
                    "change":     round(float(price - prev), 2),
                    "change_pct": round(float((price - prev) / prev * 100), 2),
                }
            else:
                result[key] = {
                    "price":      round(float(price), 2) if price else None,
                    "change":     None,
                    "change_pct": None,
                }
        except Exception as exc:
            logger.debug(f"_fetch_index_prices {ticker}: {exc}")
            result[key] = {"price": None, "change": None, "change_pct": None}
    return result


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_candle_return_30d(
    symbol: str, session: AsyncSession
) -> float | None:
    """Return approximate 30-day % return from the Candle table."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=40)
    rows = (await session.execute(
        select(Candle.close, Candle.timestamp)
        .where(
            Candle.symbol    == symbol,
            Candle.timeframe == "1d",
            Candle.timestamp >= cutoff,
        )
        .order_by(Candle.timestamp)
        .limit(35)
    )).all()
    if len(rows) < 5:
        return None
    start = float(rows[0].close)
    end   = float(rows[-1].close)
    if start <= 0:
        return None
    return round((end - start) / start * 100, 2)


def _options_score_from_pcr(pcr: float | None) -> float | None:
    if pcr is None:
        return None
    if pcr > 1.5: return  8.0
    if pcr > 1.2: return  5.0
    if pcr > 0.8: return  0.0
    if pcr > 0.5: return -5.0
    return -8.0


# ── Serialisers ───────────────────────────────────────────────────────────────

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


def _fund_out(row: FundamentalData) -> FundamentalDataOut:
    return FundamentalDataOut(
        symbol=row.symbol,
        company_name=row.company_name,
        pe_ratio=row.pe_ratio,
        pb_ratio=row.pb_ratio,
        roe=row.roe,
        roce=row.roce,
        debt_to_equity=row.debt_to_equity,
        current_ratio=row.current_ratio,
        revenue_growth_3yr=row.revenue_growth_3yr,
        profit_growth_3yr=row.profit_growth_3yr,
        promoter_holding=row.promoter_holding,
        fii_holding=row.fii_holding,
        pledged_pct=row.pledged_pct,
        market_cap_cr=row.market_cap_cr,
        dividend_yield=row.dividend_yield,
        fundamental_score=row.fundamental_score,
        last_updated=row.last_updated,
    )


def _vix_label(vix: float | None) -> str:
    if vix is None:    return "UNAVAILABLE"
    if vix > 40:       return "CRASH_ZONE"
    if vix > 30:       return "EXTREME_FEAR"
    if vix > 25:       return "HIGH_FEAR"
    if vix > 20:       return "ELEVATED"
    if vix > 15:       return "NORMAL"
    if vix >= 12:      return "BULL_RUN"
    return "COMPLACENCY"


# ═════════════════════════════════════════════════════════════════════════════
# 1. MARKET STATUS
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/market-status",
    response_model=MarketStatusOut,
    summary="Live NSE market status with index prices and VIX",
)
async def get_market_status():
    """Returns NSE open/closed status, IST time, NIFTY/BANKNIFTY/SENSEX
    last traded prices, India VIX, and today's holiday flag."""
    loop    = asyncio.get_event_loop()
    now_ist = datetime.datetime.now(_IST)
    date_str = now_ist.strftime("%Y-%m-%d")

    today_holiday = date_str in _NSE_HOLIDAYS
    holiday_name  = _NSE_HOLIDAYS.get(date_str, "")

    nse_open = is_nse_market_open() and not today_holiday

    idx = await loop.run_in_executor(None, _fetch_index_prices)

    vix: float | None = None
    try:
        vix = await loop.run_in_executor(None, fetch_india_vix)
    except Exception:
        pass

    return MarketStatusOut(
        nse_open=nse_open,
        ist_time=now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        nifty=MarketIndexOut(**idx.get("nifty",      {})),
        bank_nifty=MarketIndexOut(**idx.get("bank_nifty", {})),
        sensex=MarketIndexOut(**idx.get("sensex",    {})),
        india_vix=round(vix, 2) if vix else None,
        today_holiday=today_holiday,
        holiday_name=holiday_name,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. FII / DII
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/fii-dii",
    response_model=FIIDIISummaryOut,
    summary="FII/DII flow summary with trend analysis and 30-day chart data",
)
async def get_fii_dii_summary(db: AsyncSession = Depends(get_db)):
    """Returns today's FII/DII net flows, 5-day average, trend classification,
    a normalised sentiment score (−10 to +10), and 30-day chart data."""
    rows = (await db.execute(
        select(FIIDIIFlow)
        .order_by(desc(FIIDIIFlow.date))
        .limit(30)
    )).scalars().all()

    if not rows:
        return FIIDIISummaryOut(
            today=None, five_day_avg=None,
            trend="MIXED", score=0.0, chart_data=[],
        )

    # Today
    latest = rows[0]
    today_out = FIIDIITodayOut(
        fii_net=latest.fii_net_buy,
        dii_net=latest.dii_net_buy,
        market_direction=latest.market_direction,
    )

    # 5-day average
    last5   = rows[:5]
    fii_avg = sum(r.fii_net_buy for r in last5) / len(last5)
    dii_avg = sum(r.dii_net_buy for r in last5) / len(last5)
    avg_out = FIIDIIAvgOut(
        fii_avg=round(fii_avg, 2),
        dii_avg=round(dii_avg, 2),
    )

    # Trend
    if fii_avg > 500:
        trend = "ACCUMULATION"
    elif fii_avg < -500:
        trend = "DISTRIBUTION"
    else:
        trend = "MIXED"

    # Normalised score: combined 5d avg / 1000 Cr, clamped ±10
    combined = fii_avg + dii_avg
    score    = max(-10.0, min(10.0, round(combined / 1000.0, 2)))

    # Chart data (oldest first)
    chart = [
        FIIDIIChartPoint(date=r.date, fii_net=r.fii_net_buy, dii_net=r.dii_net_buy)
        for r in reversed(rows)
    ]

    return FIIDIISummaryOut(
        today=today_out,
        five_day_avg=avg_out,
        trend=trend,
        score=score,
        chart_data=chart,
    )


@router.get(
    "/fii-dii/history",
    response_model=list[FIIDIIFlowOut],
    summary="FII/DII raw daily records (last N days)",
)
async def list_fii_dii_history(
    days: int = Query(default=10, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(days)
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


# ═════════════════════════════════════════════════════════════════════════════
# 3. OPTIONS CHAIN
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/options-chain/{symbol}",
    response_model=OptionsChainDetailOut,
    summary="Options chain snapshot for NIFTY or BANKNIFTY",
)
async def get_options_chain_detail(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """Returns PCR, max pain, support/resistance levels, and options score.
    Triggers a fresh fetch automatically if no DB snapshot exists.
    chain_data is populated when per-strike data is available.
    """
    sym = symbol.upper()
    if sym not in ("NIFTY", "BANKNIFTY"):
        raise HTTPException(status_code=400, detail="symbol must be NIFTY or BANKNIFTY")

    snap = (await db.execute(
        select(OptionsChainSnapshot)
        .where(OptionsChainSnapshot.symbol == sym)
        .order_by(desc(OptionsChainSnapshot.snapshot_at))
        .limit(1)
    )).scalar_one_or_none()

    if snap is None:
        try:
            await run_options_analysis(db)
            await db.commit()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Options fetch failed: {exc}")
        snap = (await db.execute(
            select(OptionsChainSnapshot)
            .where(OptionsChainSnapshot.symbol == sym)
            .order_by(desc(OptionsChainSnapshot.snapshot_at))
            .limit(1)
        )).scalar_one_or_none()

    if snap is None:
        raise HTTPException(status_code=404, detail=f"No options data for {sym}")

    return OptionsChainDetailOut(
        spot_price=snap.atm_strike,         # closest available proxy for spot
        expiry_date=snap.expiry_date,
        pcr=snap.pcr,
        max_pain=snap.max_pain,
        support_levels=snap.support_levels or [],
        resistance_levels=snap.resistance_levels or [],
        options_score=_options_score_from_pcr(snap.pcr),
        chain_data=[],                       # per-strike rows not stored in DB
    )


@router.get(
    "/options/{symbol}",
    response_model=list[OptionsSnapshotOut],
    summary="Latest options chain snapshots for NIFTY or BANKNIFTY (DB list)",
)
async def get_options_snapshot(
    symbol: str,
    limit: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    sym    = symbol.upper()
    result = await db.execute(
        select(OptionsChainSnapshot)
        .where(OptionsChainSnapshot.symbol == sym)
        .order_by(desc(OptionsChainSnapshot.snapshot_at))
        .limit(limit)
    )
    rows = result.scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No options snapshots for {sym}")
    return [_options_out(r) for r in rows]


@router.post(
    "/options/{symbol}/trigger",
    response_model=OptionsSnapshotOut,
    summary="Fetch a fresh NSE options chain snapshot and persist",
)
async def trigger_options_fetch(symbol: str, db: AsyncSession = Depends(get_db)):
    sym = symbol.upper()
    if sym not in ("NIFTY", "BANKNIFTY"):
        raise HTTPException(status_code=400, detail="symbol must be NIFTY or BANKNIFTY")
    try:
        await run_options_analysis(db)
        await db.commit()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Options fetch failed: {exc}")
    row = (await db.execute(
        select(OptionsChainSnapshot)
        .where(OptionsChainSnapshot.symbol == sym)
        .order_by(desc(OptionsChainSnapshot.snapshot_at))
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=502, detail=f"No snapshot found for {sym} after fetch")
    return _options_out(row)


# ═════════════════════════════════════════════════════════════════════════════
# 4. INDIA VIX
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# 5. MUTUAL FUNDS
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/mutual-funds",
    response_model=MutualFundListOut,
    summary="All tracked fund NAVs with buy signal — simplified list",
)
async def list_mutual_funds(
    scheme_codes: Optional[str] = Query(
        default=None,
        description="Comma-separated scheme codes; defaults to WATCHLIST_MUTUAL_FUND_SCHEMES",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Returns {funds: [...]} with one entry per scheme. Auto-fetches when no DB record exists."""
    codes = (
        [c.strip() for c in scheme_codes.split(",")]
        if scheme_codes
        else settings.WATCHLIST_MUTUAL_FUND_SCHEMES
    )

    funds: list[MutualFundBriefOut] = []
    for code in codes:
        latest = (await db.execute(
            select(MutualFundNAV)
            .where(MutualFundNAV.scheme_code == code)
            .order_by(desc(MutualFundNAV.recorded_at))
            .limit(1)
        )).scalar_one_or_none()

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

        sig = await get_mf_buy_signal(code, db)
        funds.append(MutualFundBriefOut(
            scheme_code=latest.scheme_code,
            name=latest.scheme_name,
            nav=latest.nav,
            change_pct=latest.change_pct,
            one_month_return=latest.one_month_return,
            one_yr_return=latest.one_year_return,
            three_year_return=latest.three_year_return,
            signal=sig.get("signal", "HOLD"),
            category=latest.category,
        ))

    return MutualFundListOut(funds=funds)


@router.get(
    "/mutual-funds/compare",
    response_model=list[FundComparisonOut],
    summary="Compare funds by 1Y/3Y return and consistency",
)
async def compare_mutual_funds(
    scheme_codes: Optional[str] = Query(default=None),
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
        raise HTTPException(status_code=502, detail=f"Failed to fetch NAV for {scheme_code}")
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
            detail=f"No NAV data for {scheme_code}. Call POST /refresh first.",
        )
    sig = await get_mf_buy_signal(scheme_code, db)
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
        signal=sig.get("signal", "HOLD"),
        reason=sig.get("reason", ""),
        high_52w=sig.get("high_52w"),
        dip_from_high_pct=sig.get("dip_from_high_pct"),
        vix=sig.get("vix"),
    )


@router.get(
    "/mutual-funds/{scheme_code}/sip",
    response_model=SIPBriefOut,
    summary="SIP simulation — total invested, current value, CAGR, absolute return",
)
async def simulate_sip_endpoint(
    scheme_code: str,
    monthly_amount: float = Query(default=5000.0, gt=0),
    months: int = Query(default=12, ge=1, le=360),
    db: AsyncSession = Depends(get_db),
):
    result = await simulate_sip(scheme_code, monthly_amount, months, db)
    if not result:
        raise HTTPException(status_code=404, detail=f"No NAV history for scheme {scheme_code}")
    return SIPBriefOut(
        total_invested=result["total_invested"],
        current_value=result["current_value"],
        cagr=result.get("cagr_percent", 0.0),
        absolute_return=result.get("absolute_return", 0.0),
    )


@router.post(
    "/sip/project",
    response_model=SIPProjectionOut,
    summary="Project SIP corpus using an assumed constant CAGR (planning tool)",
)
async def project_sip_returns(body: SIPProjectionIn):
    if body.months <= 0 or body.monthly_amount <= 0:
        raise HTTPException(status_code=400, detail="monthly_amount and months must be positive")
    result = project_sip(body.monthly_amount, body.expected_annual_return_pct, body.months)
    return SIPProjectionOut(**result)


# ═════════════════════════════════════════════════════════════════════════════
# 6. FUNDAMENTALS
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/fundamentals",
    response_model=list[FundamentalDataOut],
    summary="All fundamental data rows — used by the screener UI",
)
async def list_fundamentals(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(FundamentalData)
        .order_by(FundamentalData.fundamental_score.desc().nullslast())
    )).scalars().all()
    return [_fund_out(r) for r in rows]


@router.get(
    "/fundamentals/{symbol}",
    response_model=FundamentalDataOut,
    summary="Fundamental data for an NSE-listed stock (from weekly DB snapshot)",
)
async def get_fundamentals(symbol: str, db: AsyncSession = Depends(get_db)):
    sym = symbol.upper()
    if not sym.endswith(".NS"):
        sym = sym + ".NS"
    row = await get_fundamentals_for_symbol(sym, db)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No fundamental data for {sym}. Run POST /fundamentals/refresh to populate.",
        )
    return _fund_out(row)


@router.post(
    "/fundamentals/refresh",
    summary="Trigger a full fundamental data refresh for all NSE symbols",
)
async def refresh_fundamentals(db: AsyncSession = Depends(get_db)):
    await run_fundamental_update(db)
    await db.commit()
    return {"status": "ok", "message": "Fundamental update complete"}


# ═════════════════════════════════════════════════════════════════════════════
# 7. SECTOR PERFORMANCE
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/sector-performance",
    response_model=SectorPerfOut,
    summary="30-day relative performance of NSE sectors vs NIFTY 50",
)
async def get_sector_performance(db: AsyncSession = Depends(get_db)):
    """Computes 30-day return for each sector (using SECTOR_INDEX ETFs when
    available, falling back to constituent stock averages).
    vs_nifty_pct = sector_return − nifty_return.
    Signal: OUTPERFORM (>+2%), UNDERPERFORM (<−2%), NEUTRAL otherwise.
    """
    nifty_30d = await _get_candle_return_30d(_NIFTY50, db)

    # Unique sectors from SECTOR_MAP
    sectors = sorted({s for s in SECTOR_MAP.values()})
    results: list[SectorPerfItem] = []

    for sector in sectors:
        # Prefer sector index ETF
        idx_ticker = SECTOR_INDEX.get(sector)
        ret: float | None = None

        if idx_ticker:
            ret = await _get_candle_return_30d(idx_ticker, db)

        # Fallback: average of constituent stocks
        if ret is None:
            constituent_syms = [sym for sym, sec in SECTOR_MAP.items() if sec == sector]
            returns = []
            for sym in constituent_syms[:4]:  # cap to avoid N+1 slowness
                r = await _get_candle_return_30d(sym, db)
                if r is not None:
                    returns.append(r)
            ret = round(sum(returns) / len(returns), 2) if returns else None

        if ret is not None and nifty_30d is not None:
            vs_nifty = round(ret - nifty_30d, 2)
            signal = (
                "OUTPERFORM" if vs_nifty > 2
                else "UNDERPERFORM" if vs_nifty < -2
                else "NEUTRAL"
            )
        else:
            vs_nifty = None
            signal   = "NEUTRAL"

        results.append(SectorPerfItem(
            name=sector,
            return_30d=ret,
            vs_nifty_pct=vs_nifty,
            signal=signal,
        ))

    # Sort by vs_nifty descending (best performers first)
    results.sort(key=lambda x: x.vs_nifty_pct or 0, reverse=True)
    return SectorPerfOut(sectors=results)


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
            detail=f"{sym} is not in the sector map",
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


# ═════════════════════════════════════════════════════════════════════════════
# 8. SIGNALS
# ═════════════════════════════════════════════════════════════════════════════

@router.get(
    "/signals",
    response_model=list[SignalOut],
    summary="Latest signals for Indian symbols",
)
async def list_india_signals(
    limit: int = Query(default=30, ge=1, le=200),
    category: Optional[str] = Query(
        default=None,
        description="Filter by: stocks | indices | forex | mf",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Optional *category* filter:
    - **stocks** — NSE large + mid cap equities
    - **indices** — NIFTY, BANKNIFTY, SENSEX indices
    - **forex** — USDINR, EURINR, GBPINR
    - **mf** — mutual fund scheme signals
    """
    cat = (category or "").lower()
    if cat == "stocks":
        pool = settings.nse_symbols + settings.nse_mid_symbols
    elif cat == "indices":
        pool = settings.WATCHLIST_NIFTY_INDICES
    elif cat == "forex":
        pool = settings.WATCHLIST_INDIAN_FOREX
    elif cat == "mf":
        pool = list(getattr(settings, "WATCHLIST_MUTUAL_FUND_SCHEMES", []))
    else:
        pool = settings.all_indian_symbols

    result = await db.execute(
        select(Signal)
        .where(Signal.symbol.in_(pool))
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
    signals = await analyze_all_india_symbols(db)
    for sig in signals:
        await save_signal(sig, db)
    await db.commit()
    return TriggerResult(
        signals_generated=len(signals),
        actionable=sum(1 for s in signals if s.action in ("BUY", "SELL")),
        symbols=[s.symbol for s in signals],
    )


# ═════════════════════════════════════════════════════════════════════════════
# 9. SEED
# ═════════════════════════════════════════════════════════════════════════════

@router.post(
    "/seed",
    response_model=SeedResultOut,
    summary="Seed all Indian market data: candles → FII/DII → options → signals",
)
async def seed_india_data(db: AsyncSession = Depends(get_db)):
    """Runs a full data refresh in sequence:
    1. Fetch OHLCV candles for all NSE symbols via yfinance.
    2. Fetch latest FII/DII flow data from NSE.
    3. Fetch NIFTY + BANKNIFTY options chain snapshots.
    4. Run the full India confluence signal scan.
    Returns a summary of everything that was fetched/generated.
    """
    from crawler.india_price_feed import run_india_price_crawl

    t0 = _time.monotonic()

    # ── 1. Candles ────────────────────────────────────────────────────────────
    symbols_fetched = 0
    candles_saved   = 0
    try:
        price_result  = await run_india_price_crawl(db)
        await db.commit()
        symbols_fetched = price_result.get("total_symbols", 0)
        candles_saved   = price_result.get("total_candles_saved", 0)
    except Exception as exc:
        logger.warning(f"[seed] price crawl error: {exc}")

    # ── 2. FII/DII ────────────────────────────────────────────────────────────
    try:
        data = await fetch_fii_dii_data(db)
        await save_fii_dii_to_db(data, db)
        await db.commit()
    except Exception as exc:
        logger.warning(f"[seed] FII/DII error: {exc}")

    # ── 3. Options chain ──────────────────────────────────────────────────────
    try:
        await run_options_analysis(db)
        await db.commit()
    except Exception as exc:
        logger.warning(f"[seed] options error: {exc}")

    # ── 4. Signals ────────────────────────────────────────────────────────────
    signals: list = []
    try:
        signals = await analyze_all_india_symbols(db)
        for sig in signals:
            await save_signal(sig, db)
        await db.commit()
    except Exception as exc:
        logger.warning(f"[seed] signal scan error: {exc}")

    actionable = [s for s in signals if s.action in ("BUY", "SELL")]
    duration   = round(_time.monotonic() - t0, 2)

    logger.info(
        f"[seed] symbols={symbols_fetched}  candles={candles_saved}  "
        f"signals={len(signals)}  actionable={len(actionable)}  "
        f"duration={duration}s"
    )
    return SeedResultOut(
        status="ok",
        symbols_fetched=symbols_fetched,
        candles_saved=candles_saved,
        signals_generated=len(signals),
        actionable_signals=len(actionable),
        duration_seconds=duration,
    )
