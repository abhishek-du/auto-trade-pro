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
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import (
    BacktestRequestIn,
    BacktestResultOut,
    BacktestSymbolResultOut,
    SignalDetail,
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
from crawler.india_price_feed import fetch_india_vix, fetch_nse_candles, is_nse_market_open
from crawler.price_feed import save_candles_to_db
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


# ── Candle timeframe configuration ───────────────────────────────────────────

TIMEFRAME_CONFIG: dict[str, dict] = {
    "1m":  {"db_tf": "1m",  "yf_interval": "1m",  "yf_period": "1d",   "label": "1 Min",  "seconds": 60},
    "5m":  {"db_tf": "5m",  "yf_interval": "5m",  "yf_period": "5d",   "label": "5 Min",  "seconds": 300},
    "15m": {"db_tf": "15m", "yf_interval": "15m", "yf_period": "5d",   "label": "15 Min", "seconds": 900},
    "1h":  {"db_tf": "1h",  "yf_interval": "1h",  "yf_period": "60d",  "label": "1 Hour", "seconds": 3600},
    "1d":  {"db_tf": "1d",  "yf_interval": "1d",  "yf_period": "2y",   "label": "1 Day",  "seconds": 86400},
}

_IST_OFFSET_SEC = 19_800  # 5h30m in seconds


def _normalize_symbol(symbol: str) -> str:
    """RELIANCE → RELIANCE.NS; ^NSEI / GC=F / TCS.NS unchanged."""
    if symbol.startswith("^") or "=" in symbol or "." in symbol:
        return symbol
    return symbol + ".NS"


def _ts_to_unix(ts) -> int:
    """UTC-naive datetime → unix seconds (for lightweight-charts)."""
    import calendar
    return calendar.timegm(ts.timetuple())


def _compute_indicator_series(candles: list[dict]) -> dict:
    """Return EMA/RSI/MACD/BB/Supertrend/VWAP time series from candle list."""
    import math as _m
    import numpy as np
    import pandas as pd

    if len(candles) < 20:
        return {}

    times  = np.array([c["time"]   for c in candles], dtype=np.int64)
    close  = np.array([c["close"]  for c in candles], dtype=np.float64)
    high   = np.array([c["high"]   for c in candles], dtype=np.float64)
    low    = np.array([c["low"]    for c in candles], dtype=np.float64)
    volume = np.array([c["volume"] for c in candles], dtype=np.float64)

    def to_s(vals):
        return [
            {"time": int(t), "value": round(float(v), 4)}
            for t, v in zip(times, vals)
            if not _m.isnan(float(v))
        ]

    result: dict = {}

    # EMAs
    cs = pd.Series(close)
    for p, key in [(20, "ema20"), (50, "ema50"), (200, "ema200")]:
        if len(close) >= p:
            result[key] = to_s(cs.ewm(span=p, adjust=False).mean().values)

    # RSI-14
    if len(close) >= 15:
        delta     = cs.diff()
        gain      = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss      = (-delta).clip(lower=0).ewm(com=13, adjust=False).mean()
        rs        = gain / loss.replace(0, np.nan)
        rsi       = 100 - 100 / (1 + rs)
        result["rsi"] = to_s(rsi.values)

    # MACD (12/26/9)
    if len(close) >= 27:
        ema12  = cs.ewm(span=12, adjust=False).mean()
        ema26  = cs.ewm(span=26, adjust=False).mean()
        macd_l = ema12 - ema26
        sig_l  = macd_l.ewm(span=9, adjust=False).mean()
        hist   = macd_l - sig_l
        result["macd"] = {
            "macd":   to_s(macd_l.values),
            "signal": to_s(sig_l.values),
            "histogram": [
                {
                    "time":  int(t),
                    "value": round(float(v), 4),
                    "color": "rgba(16,185,129,0.7)" if v >= 0 else "rgba(239,68,68,0.7)",
                }
                for t, v in zip(times, hist.values)
                if not _m.isnan(float(v))
            ],
        }

    # Bollinger Bands (20, 2σ)
    if len(close) >= 20:
        mid   = cs.rolling(20).mean()
        std   = cs.rolling(20).std()
        result["bollinger"] = {
            "upper":  to_s((mid + 2 * std).values),
            "middle": to_s(mid.values),
            "lower":  to_s((mid - 2 * std).values),
        }

    # Supertrend (period=7, mult=3)
    if len(close) >= 14:
        prev_c = cs.shift(1)
        tr = pd.concat([
            pd.Series(high)  - pd.Series(low),
            (pd.Series(high) - prev_c).abs(),
            (pd.Series(low)  - prev_c).abs(),
        ], axis=1).max(axis=1)
        atr_s = tr.rolling(7).mean().values
        hl2   = (high + low) / 2
        ub    = hl2 + 3.0 * atr_s
        lb    = hl2 - 3.0 * atr_s

        fu = np.full(len(close), np.nan)
        fl = np.full(len(close), np.nan)
        st = np.full(len(close), np.nan)
        dirs: list[str] = [""] * len(close)

        valids = np.where(~np.isnan(atr_s))[0]
        if len(valids):
            s = int(valids[0])
            fu[s], fl[s] = ub[s], lb[s]
            st[s]   = fu[s] if close[s] <= fu[s] else fl[s]
            dirs[s] = "down" if close[s] <= fu[s] else "up"
            for i in range(s + 1, len(close)):
                fu[i] = ub[i] if ub[i] < fu[i-1] or close[i-1] > fu[i-1] else fu[i-1]
                fl[i] = lb[i] if lb[i] > fl[i-1] or close[i-1] < fl[i-1] else fl[i-1]
                if st[i-1] == fu[i-1]:
                    st[i] = fl[i] if close[i] > fu[i] else fu[i]
                else:
                    st[i] = fu[i] if close[i] < fl[i] else fl[i]
                dirs[i] = "up" if close[i] > st[i] else "down"

        result["supertrend"] = [
            {"time": int(t), "value": round(float(v), 4), "direction": d}
            for t, v, d in zip(times, st, dirs)
            if not _m.isnan(float(v)) and d
        ]

    # VWAP with daily IST reset (meaningful only for intraday)
    try:
        from datetime import datetime, timedelta, timezone as _tz
        _ist_off = timedelta(hours=5, minutes=30)
        cumtv = 0.0; cumv = 0.0; last_date = None
        vwap_pts: list[dict] = []
        for i in range(len(times)):
            dt_ist  = datetime.utcfromtimestamp(int(times[i])) + _ist_off
            cur_d   = dt_ist.date()
            if cur_d != last_date:
                cumtv = 0.0; cumv = 0.0; last_date = cur_d
            tp     = (high[i] + low[i] + close[i]) / 3
            cumtv += tp * volume[i]
            cumv  += volume[i]
            if cumv > 0:
                vwap_pts.append({"time": int(times[i]), "value": round(cumtv / cumv, 4)})
        result["vwap"] = vwap_pts
    except Exception:
        pass

    # Support / resistance (local pivot method)
    if len(close) >= 30:
        w = min(15, len(close) // 4)
        loc_hi, loc_lo = [], []
        for i in range(w, len(close) - w):
            if high[i]  == max(high[i-w : i+w+1]):  loc_hi.append(float(high[i]))
            if low[i]   == min(low[i-w  : i+w+1]):  loc_lo.append(float(low[i]))

        def _cluster(levels: list[float], tol: float = 0.01) -> list[float]:
            if not levels: return []
            levels = sorted(set(levels))
            out, grp = [], [levels[0]]
            for lv in levels[1:]:
                if grp and lv <= grp[-1] * (1 + tol):
                    grp.append(lv)
                else:
                    out.append(sum(grp) / len(grp)); grp = [lv]
            out.append(sum(grp) / len(grp))
            return out

        cur = float(close[-1])
        result["resistance_levels"] = [round(r, 2) for r in sorted(_cluster(loc_hi)) if r > cur][:3]
        result["support_levels"]    = [round(s, 2) for s in sorted(_cluster(loc_lo), reverse=True) if s < cur][:3]

    return result


# ── Candle REST endpoints ─────────────────────────────────────────────────────

@router.get("/candles/{symbol}", summary="OHLCV candles for TradingView chart")
async def get_candles(
    symbol: str,
    timeframe: str = Query("1h", description="1m | 5m | 15m | 1h | 1d"),
    limit: int     = Query(300, ge=1, le=1000),
    from_ts: Optional[int] = Query(None, description="Oldest unix timestamp to return"),
    db: AsyncSession = Depends(get_db),
):
    if timeframe not in TIMEFRAME_CONFIG:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of: {list(TIMEFRAME_CONFIG)}")

    sym = _normalize_symbol(symbol)
    cfg = TIMEFRAME_CONFIG[timeframe]

    # ── 1. Try DB first ────────────────────────────────────────────────────────
    async def _query_db() -> list:
        q = (
            select(Candle)
            .where(and_(Candle.symbol == sym, Candle.timeframe == timeframe))
            .order_by(desc(Candle.timestamp))
            .limit(limit)
        )
        if from_ts:
            from datetime import datetime, timezone as _tz
            dt = datetime.fromtimestamp(from_ts, tz=_tz.utc).replace(tzinfo=None)
            q  = q.where(Candle.timestamp >= dt)
        return (await db.execute(q)).scalars().all()

    rows = await _query_db()

    # ── 2. Fetch from yfinance if DB is sparse ─────────────────────────────────
    if len(rows) < 50:
        logger.info(f"[candles] DB sparse ({len(rows)}) for {sym}/{timeframe} — fetching from yfinance")
        try:
            fresh = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: fetch_nse_candles(sym, interval=cfg["yf_interval"], period=cfg["yf_period"]),
            )
            if fresh:
                await save_candles_to_db(fresh, db)
                await db.commit()
                rows = await _query_db()
        except Exception as exc:
            logger.warning(f"[candles] yfinance fetch failed for {sym}: {exc}")

    if not rows:
        raise HTTPException(status_code=404, detail=f"No candle data for {sym}/{timeframe}")

    # ── 3. Sort ASC, build response ────────────────────────────────────────────
    rows_asc = sorted(rows, key=lambda r: r.timestamp)
    candles  = [
        {
            "time":   _ts_to_unix(r.timestamp),
            "open":   round(float(r.open),   4),
            "high":   round(float(r.high),   4),
            "low":    round(float(r.low),    4),
            "close":  round(float(r.close),  4),
            "volume": round(float(r.volume), 2),
        }
        for r in rows_asc
    ]

    # ── 4. Current price from PRICE_CACHE if available ─────────────────────────
    current_price: float | None = None
    try:
        from crawler.live_prices import PRICE_CACHE
        entry = PRICE_CACHE.get(sym) or PRICE_CACHE.get(symbol)
        if entry:
            current_price = entry.get("price")
    except Exception:
        pass
    if current_price is None and candles:
        current_price = candles[-1]["close"]

    return {
        "symbol":        sym,
        "timeframe":     timeframe,
        "candles":       candles,
        "count":         len(candles),
        "from_time":     candles[0]["time"]  if candles else None,
        "to_time":       candles[-1]["time"] if candles else None,
        "current_price": current_price,
    }


@router.get("/candles/{symbol}/latest", summary="Most recent candle bar")
async def get_latest_candle(
    symbol: str,
    timeframe: str = Query("1h"),
    db: AsyncSession = Depends(get_db),
):
    if timeframe not in TIMEFRAME_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid timeframe")

    sym = _normalize_symbol(symbol)
    row = (await db.execute(
        select(Candle)
        .where(and_(Candle.symbol == sym, Candle.timeframe == timeframe))
        .order_by(desc(Candle.timestamp))
        .limit(1)
    )).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail=f"No candles for {sym}/{timeframe}")

    return {
        "time":   _ts_to_unix(row.timestamp),
        "open":   round(float(row.open),   4),
        "high":   round(float(row.high),   4),
        "low":    round(float(row.low),    4),
        "close":  round(float(row.close),  4),
        "volume": round(float(row.volume), 2),
    }


@router.get("/candles/{symbol}/indicators", summary="Indicator time series for chart")
async def get_candle_indicators(
    symbol: str,
    timeframe: str = Query("1h"),
    limit: int     = Query(300, ge=20, le=1000),
    db: AsyncSession = Depends(get_db),
):
    if timeframe not in TIMEFRAME_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid timeframe")

    sym  = _normalize_symbol(symbol)
    rows = (await db.execute(
        select(Candle)
        .where(and_(Candle.symbol == sym, Candle.timeframe == timeframe))
        .order_by(desc(Candle.timestamp))
        .limit(limit)
    )).scalars().all()

    if not rows:
        return {}

    rows_asc = sorted(rows, key=lambda r: r.timestamp)
    candles  = [
        {
            "time":   _ts_to_unix(r.timestamp),
            "open":   float(r.open),
            "high":   float(r.high),
            "low":    float(r.low),
            "close":  float(r.close),
            "volume": float(r.volume),
        }
        for r in rows_asc
    ]

    return _compute_indicator_series(candles)


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
    """Return approximate 30-day % return from the Candle table.

    Uses 1h candles (the timeframe saved by the India price crawl).
    30 trading days × ~6.5 h/day ≈ 195 bars — fetch up to 210 to be safe.
    """
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=40)
    rows = (await session.execute(
        select(Candle.close, Candle.timestamp)
        .where(
            Candle.symbol    == symbol,
            Candle.timeframe == "1h",
            Candle.timestamp >= cutoff,
        )
        .order_by(Candle.timestamp)
        .limit(210)
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
    "/fii-dii/fetch",
    response_model=FIIDIIFlowOut,
    summary="Manually fetch fresh FII/DII data from NSE and persist immediately",
)
async def fetch_fii_dii_now(db: AsyncSession = Depends(get_db)):
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

    # Prefer the most recent snapshot that has real data (pcr > 0 and atm_strike > 0).
    # NSE returns {} after market hours; the crawler now skips saving those, but
    # any zero-snapshots already in the DB need to be skipped here too.
    snap = (await db.execute(
        select(OptionsChainSnapshot)
        .where(
            OptionsChainSnapshot.symbol == sym,
            OptionsChainSnapshot.pcr > 0,
            OptionsChainSnapshot.atm_strike > 0,
        )
        .order_by(desc(OptionsChainSnapshot.snapshot_at))
        .limit(1)
    )).scalar_one_or_none()

    if snap is None:
        raise HTTPException(
            status_code=404,
            detail=f"No options data for {sym} yet — Celery refreshes every 15 min during NSE hours (09:15–15:30 IST)",
        )

    return OptionsChainDetailOut(
        spot_price=snap.atm_strike,         # ATM strike is the closest proxy for spot
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
    """Always runs the full India signal scan regardless of market hours.

    Use this endpoint to verify the signal pipeline works outside NSE trading hours.
    Returns all actionable (BUY/SELL) signals with per-signal detail.
    """
    signals = await analyze_all_india_symbols(db, ignore_market_hours=True)
    for sig in signals:
        await save_signal(sig, db)
    await db.commit()
    return TriggerResult(
        signals_generated=len(signals),
        actionable=sum(1 for s in signals if s.action in ("BUY", "SELL")),
        symbols=[s.symbol for s in signals],
        signal_details=[
            SignalDetail(
                symbol=s.symbol,
                action=s.action,
                confidence=s.confidence,
                final_score=s.final_score,
                reasoning_points=s.reasoning_points,
            )
            for s in signals
        ],
    )


# ═════════════════════════════════════════════════════════════════════════════
# 9. SEED
# ═════════════════════════════════════════════════════════════════════════════

@router.post(
    "/seed",
    response_model=SeedResultOut,
    summary="Seed all Indian market data: candles → FII/DII → options → signals",
)
async def seed_india_data(
    db:    AsyncSession = Depends(get_db),
    force: bool        = Query(False, description="Bypass market-hours check for signal scan"),
):
    """Runs a full data refresh in sequence:
    1. Fetch OHLCV candles for all NSE symbols via yfinance.
    2. Fetch latest FII/DII flow data from NSE.
    3. Fetch NIFTY + BANKNIFTY options chain snapshots.
    4. Run the full India confluence signal scan.

    Pass ?force=true to run the signal scan even when NSE is closed (useful for testing).
    """
    from crawler.india_price_feed import run_india_price_crawl
    from sqlalchemy import func as sa_func

    t0 = _time.monotonic()

    logger.info(
        f"[seed] Starting — force={force}  "
        f"symbols={len(settings.all_indian_symbols)}  "
        f"sample={settings.all_indian_symbols[:3]}"
    )

    # ── 1. Candles — always fetch regardless of market hours ──────────────────
    symbols_fetched = 0
    candles_saved   = 0
    try:
        price_result = await run_india_price_crawl(db, ignore_market_hours=True)
        await db.commit()
        symbols_fetched = price_result.get("total_symbols", 0)
        candles_saved   = price_result.get("total_candles_saved", 0)
        logger.info(f"[seed] Price crawl result: {price_result}")
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

    # ── 4. Signals — always run when force=True; uses whatever candles are in DB ─
    signals: list = []
    symbols_analysed: int | None = None
    market_open = is_nse_market_open()
    if force or market_open:
        logger.info(
            f"[seed] Running signal scan — force={force}  market_open={market_open}  "
            f"(uses existing DB candles regardless of symbols_fetched)"
        )
        try:
            signals = await analyze_all_india_symbols(db, ignore_market_hours=force)
            symbols_analysed = len(signals)
            for sig in signals:
                await save_signal(sig, db)
            await db.commit()
            logger.info(
                f"[seed] Signal scan done — "
                f"actionable={len([s for s in signals if s.action in ('BUY','SELL')])}  "
                f"total={len(signals)}"
            )
        except Exception as exc:
            logger.error(f"[seed] signal scan error: {exc}", exc_info=True)
    else:
        logger.info("[seed] NSE closed and force=False — skipping signal scan")

    # ── Candles count for response ────────────────────────────────────────────
    from db.models import Candle as CandleModel
    candles_available: int | None = None
    try:
        result = await db.execute(
            select(sa_func.count()).select_from(CandleModel)
        )
        candles_available = result.scalar_one()
    except Exception:
        pass

    actionable = [s for s in signals if s.action in ("BUY", "SELL")]
    duration   = round(_time.monotonic() - t0, 2)

    logger.info(
        f"[seed] symbols={symbols_fetched}  candles={candles_saved}  "
        f"signals={len(signals)}  actionable={len(actionable)}  "
        f"duration={duration}s  force={force}"
    )
    return SeedResultOut(
        status="ok",
        symbols_fetched=symbols_fetched,
        candles_saved=candles_saved,
        signals_generated=len(signals),
        actionable_signals=len(actionable),
        duration_seconds=duration,
        symbols_analysed=symbols_analysed,
        candles_available=candles_available,
    )


# ── Backtest ──────────────────────────────────────────────────────────────────

@router.post(
    "/backtest",
    response_model=BacktestResultOut,
    summary="Run walk-forward backtest on India watchlist symbols",
)
async def run_india_backtest(
    body: BacktestRequestIn,
    db: AsyncSession = Depends(get_db),
):
    """Replay the India signal engine over historical OHLCV data.

    Signal at bar *i* → execute at bar *i+1* open.  SL/TP checked intra-bar.
    If both SL and TP are hit in the same bar, SL takes precedence (conservative).

    PAPER TRADING ONLY — all results are simulated; no real money involved.
    """
    import time as _time2
    from engine.backtester import BacktestConfig, run_backtest_all

    t0 = _time2.monotonic()

    cfg = BacktestConfig(
        atr_multiplier=body.atr_multiplier,
        risk_reward=body.risk_reward,
        commission_pct=body.commission_pct,
        slippage_pct=body.slippage_pct,
        initial_capital=body.initial_capital,
        lookback_candles=body.lookback_candles,
    )

    results = await run_backtest_all(
        symbols=body.symbols,
        timeframe=body.timeframe,
        config=cfg,
        session=db,
    )

    if not results:
        raise HTTPException(status_code=404, detail="No backtest results — insufficient data")

    def _to_out(r) -> BacktestSymbolResultOut:
        return BacktestSymbolResultOut(
            symbol=r.symbol,
            timeframe=r.timeframe,
            total_trades=r.total_trades,
            winning_trades=r.winning_trades,
            losing_trades=r.losing_trades,
            win_rate=r.win_rate,
            total_return_pct=r.total_return_pct,
            max_drawdown_pct=r.max_drawdown_pct,
            sharpe_ratio=r.sharpe_ratio,
            avg_win_pct=r.avg_win_pct,
            avg_loss_pct=r.avg_loss_pct,
            profit_factor=r.profit_factor,
            equity_curve=r.equity_curve[:500],  # cap payload size
        )

    all_out = [_to_out(r) for r in results]
    sharpe_vals = [r.sharpe_ratio for r in results if r.sharpe_ratio is not None]

    return BacktestResultOut(
        symbols_tested=len(results),
        timeframe=body.timeframe,
        total_trades=sum(r.total_trades for r in results),
        avg_win_rate=round(
            sum(r.win_rate for r in results) / len(results), 2
        ) if results else 0.0,
        avg_return_pct=round(
            sum(r.total_return_pct for r in results) / len(results), 2
        ) if results else 0.0,
        avg_sharpe=round(sum(sharpe_vals) / len(sharpe_vals), 2) if sharpe_vals else 0.0,
        best_symbols=all_out[:5],
        worst_symbols=list(reversed(all_out[-5:])),
        all_results=all_out,
        duration_seconds=round(_time2.monotonic() - t0, 2),
    )


# ── Live price cache endpoints ────────────────────────────────────────────────

@router.get("/live-prices", summary="Full live price cache")
async def get_live_prices():
    """Returns the full in-memory price cache (all symbols)."""
    from crawler.live_prices import get_all_cached_prices
    return get_all_cached_prices()


@router.get("/live-prices/{symbol:path}", summary="Single symbol live price")
async def get_live_price_symbol(symbol: str):
    """Returns cached price for one symbol; fetches live if not cached."""
    from crawler.live_prices import (
        fetch_prices_batch,
        get_cached_price,
        PRICE_CACHE,
        _SYMBOL_META,
    )
    cached = get_cached_price(symbol)
    if cached:
        return cached
    # Not in cache — fetch on demand
    result = await fetch_prices_batch([symbol])
    if symbol in result:
        PRICE_CACHE[symbol] = result[symbol]
        return result[symbol]
    raise HTTPException(status_code=404, detail=f"Symbol {symbol!r} not found")


@router.get("/market-summary", summary="NIFTY / SENSEX / VIX + breadth")
async def get_market_summary_endpoint():
    """Market summary: top indices, VIX, advances/declines, IST time."""
    from crawler.live_prices import get_market_summary
    return get_market_summary()


@router.get("/indices", summary="All index prices from cache")
async def get_indices():
    """Returns only index-type symbols from the live price cache."""
    from crawler.live_prices import get_all_cached_prices
    return {k: v for k, v in get_all_cached_prices().items() if v.get("type") == "index"}


@router.get("/top-movers", summary="Top gainers, losers, most active")
async def get_top_movers():
    """Returns top 5 gainers, losers, and most active stocks."""
    from crawler.live_prices import get_all_cached_prices
    stocks = [v for v in get_all_cached_prices().values() if v.get("type") == "stock"]
    top_gainers  = sorted(stocks, key=lambda x: x.get("change_pct", 0), reverse=True)[:5]
    top_losers   = sorted(stocks, key=lambda x: x.get("change_pct", 0))[:5]
    most_active  = sorted(stocks, key=lambda x: x.get("volume", 0), reverse=True)[:5]
    return {
        "top_gainers": top_gainers,
        "top_losers":  top_losers,
        "most_active": most_active,
    }


@router.post("/live-prices/refresh", summary="Force immediate price cache refresh")
async def force_refresh_live_prices():
    """Forces an immediate refresh of the price cache. Returns updated prices."""
    from crawler.live_prices import refresh_all_prices
    updated = await refresh_all_prices()
    return {"refreshed": len(updated), "prices": updated}


# ═════════════════════════════════════════════════════════════════════════════
# WATCHLIST — enriched NSE stock data
# IMPORTANT: static sub-paths (/alerts, /sector/…) MUST be registered
# before the /{symbol:path} catch-all route.
# ═════════════════════════════════════════════════════════════════════════════

async def _compute_technical_summary(symbol: str, session: AsyncSession) -> dict:
    """Run indicator engine on stored 1h candles and return a compact summary."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    rows = (await session.execute(
        select(Candle)
        .where(Candle.symbol == symbol, Candle.timeframe == "1h", Candle.timestamp >= cutoff)
        .order_by(Candle.timestamp)
        .limit(200)
    )).scalars().all()

    _neutral = {
        "overall": "NEUTRAL", "rsi": None, "rsi_signal": "NEUTRAL",
        "macd_signal": "NONE", "supertrend": "NEUTRAL",
        "vwap_position": "NEAR_VWAP", "ema_trend": "NEUTRAL",
    }

    if len(rows) < 30:
        return _neutral

    try:
        import math as _math
        import pandas as pd
        from engine.indicators import compute_indicators

        df = pd.DataFrame([{
            "open": float(r.open), "high": float(r.high), "low": float(r.low),
            "close": float(r.close), "volume": float(r.volume),
            "timestamp": r.timestamp,
        } for r in rows])

        ind = compute_indicators(df)

        bullish = sum([
            ind.rsi_signal == "OVERSOLD",
            ind.macd_cross == "BULLISH_CROSS",
            ind.supertrend_direction == "BULLISH",
            ind.vwap_position == "ABOVE_VWAP",
        ])
        bearish = sum([
            ind.rsi_signal == "OVERBOUGHT",
            ind.macd_cross == "BEARISH_CROSS",
            ind.supertrend_direction == "BEARISH",
            ind.vwap_position == "BELOW_VWAP",
        ])

        overall = "BULLISH" if bullish >= 3 else "BEARISH" if bearish >= 3 else "NEUTRAL"
        return {
            "rsi":          None if _math.isnan(ind.rsi) else round(ind.rsi, 1),
            "rsi_signal":   ind.rsi_signal,
            "macd_signal":  ind.macd_cross,
            "supertrend":   ind.supertrend_direction,
            "vwap_position": ind.vwap_position,
            "ema_trend":    ind.ema_trend,
            "overall":      overall,
        }
    except Exception as exc:
        logger.debug(f"[watchlist] technical summary failed for {symbol}: {exc}")
        return _neutral


@router.get("/watchlist", summary="Full NSE watchlist with enriched data")
async def get_watchlist():
    """Returns all stock-type symbols from PRICE_CACHE with fundamental enrichment."""
    from crawler.live_prices import get_all_cached_prices, get_market_summary

    all_prices = get_all_cached_prices()
    stocks = [v for v in all_prices.values() if v.get("type") == "stock"]

    summary   = get_market_summary()
    advances  = sum(1 for s in stocks if (s.get("change") or 0) > 0)
    declines  = sum(1 for s in stocks if (s.get("change") or 0) < 0)

    return {
        "stocks":          stocks,
        "last_refreshed":  summary.get("last_refreshed"),
        "market_status":   summary.get("market_status"),
        "total_advances":  advances,
        "total_declines":  declines,
    }


@router.get("/watchlist/alerts", summary="Watchlist alert conditions")
async def get_watchlist_alerts():
    """Returns stocks grouped by alert condition: near 52W high/low, high volume, strong signals."""
    from crawler.live_prices import get_all_cached_prices

    stocks = [v for v in get_all_cached_prices().values() if v.get("type") == "stock"]

    def _near_high(s):
        v = s.get("from_52w_high")
        return v is not None and v <= 2.0

    def _near_low(s):
        v = s.get("from_52w_low")
        return v is not None and v <= 2.0

    def _high_vol(s):
        v = s.get("volume_ratio")
        return v is not None and v > 2.0

    def _strong_sig(s):
        return s.get("signal") in ("BUY", "SELL") and (s.get("signal_confidence") or 0) > 65

    def _oversold(s):
        v = s.get("from_52w_high")
        return v is not None and v >= 20.0

    return {
        "near_52w_high":  [s for s in stocks if _near_high(s)],
        "near_52w_low":   [s for s in stocks if _near_low(s)],
        "high_volume":    [s for s in stocks if _high_vol(s)],
        "strong_signals": [s for s in stocks if _strong_sig(s)],
        "overbought":     [s for s in stocks if _near_high(s) and (s.get("change_pct") or 0) > 2],
        "oversold":       [s for s in stocks if _oversold(s)],
    }


@router.get("/watchlist/sector/{sector_name}", summary="Watchlist filtered by sector")
async def get_watchlist_sector(sector_name: str):
    """Returns watchlist stocks belonging to the named sector (case-insensitive)."""
    from crawler.live_prices import get_all_cached_prices

    sector_upper = sector_name.upper()
    stocks = [
        v for v in get_all_cached_prices().values()
        if v.get("type") == "stock"
        and (v.get("sector") or "").upper() == sector_upper
    ]
    return {"sector": sector_name, "stocks": stocks, "count": len(stocks)}


@router.post("/watchlist/refresh", summary="Force refresh prices + signal enrichment")
async def refresh_watchlist(db: AsyncSession = Depends(get_db)):
    """Refreshes PRICE_CACHE and re-injects latest signal data. Returns timing stats."""
    import time as _t
    from crawler.live_prices import enrich_cache_with_signals, refresh_all_prices

    t0      = _t.monotonic()
    updated = await refresh_all_prices()
    await enrich_cache_with_signals(db)
    duration_ms = int((_t.monotonic() - t0) * 1000)

    stocks = [v for v in updated.values() if v.get("type") == "stock"]
    return {"refreshed_count": len(stocks), "duration_ms": duration_ms}


@router.get("/watchlist/{symbol:path}", summary="Single stock deep data for detail panel")
async def get_watchlist_symbol(symbol: str, db: AsyncSession = Depends(get_db)):
    """Returns enriched data + technical summary + recent signals/news for one stock."""
    from crawler.live_prices import get_cached_price

    sym = symbol.upper()
    if not sym.endswith(".NS"):
        sym = sym + ".NS"

    cached = get_cached_price(sym)
    if not cached:
        raise HTTPException(status_code=404, detail=f"Symbol {sym!r} not found in cache")

    # ── Recent signals ────────────────────────────────────────────────────────
    recent_sigs = (await db.execute(
        select(Signal)
        .where(Signal.symbol == sym)
        .order_by(desc(Signal.created_at))
        .limit(5)
    )).scalars().all()

    # ── Recent news mentioning this ticker ────────────────────────────────────
    from db.models import NewsItem
    short = sym.replace(".NS", "")
    recent_news_rows = (await db.execute(
        select(NewsItem)
        .where(NewsItem.headline.ilike(f"%{short}%"))
        .order_by(desc(NewsItem.published_at))
        .limit(3)
    )).scalars().all()

    recent_news = [
        {
            "headline":     n.headline,
            "source":       n.source,
            "sentiment":    n.sentiment,
            "score":        n.score,
            "published_at": n.published_at,
        }
        for n in recent_news_rows
    ]

    # ── Technical summary ─────────────────────────────────────────────────────
    tech = await _compute_technical_summary(sym, db)

    # ── AI analysis from most recent signal ───────────────────────────────────
    ai_analysis = recent_sigs[0].indicators_data if recent_sigs else None

    return {
        **cached,
        "recent_signals":    [_signal_out(s) for s in recent_sigs],
        "recent_news":       recent_news,
        "technical_summary": tech,
        "ai_analysis":       ai_analysis,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Market Breadth endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/breadth")
async def get_market_breadth():
    """Return full BREADTH_CACHE — advances/declines, gainers/losers, 52W movers."""
    from crawler.market_breadth import get_breadth_cache
    return get_breadth_cache()


@router.get("/breadth/summary")
async def get_breadth_summary():
    """Compact breadth summary for dashboard widgets."""
    from crawler.market_breadth import get_breadth_cache
    data = get_breadth_cache()
    nse  = data.get("nse", {})
    bse  = data.get("bse", {})
    wl   = data.get("watchlist", {})
    tg   = data.get("top_gainers", [])
    tl   = data.get("top_losers", [])
    return {
        "nse_advances":         nse.get("advances", 0),
        "nse_declines":         nse.get("declines", 0),
        "nse_unchanged":        nse.get("unchanged", 0),
        "nse_ad_ratio":         nse.get("ad_ratio", 1.0),
        "nse_market_mood":      nse.get("market_mood", "NEUTRAL"),
        "bse_advances":         bse.get("advances", 0),
        "bse_declines":         bse.get("declines", 0),
        "watchlist_advances":   wl.get("advances", 0),
        "watchlist_declines":   wl.get("declines", 0),
        "week52_high_count":    len(data.get("week52_high", [])),
        "week52_low_count":     len(data.get("week52_low", [])),
        "top_gainer": (
            {"symbol": tg[0].get("symbol"), "name": tg[0].get("name"), "change_pct": tg[0].get("change_pct")}
            if tg else None
        ),
        "top_loser": (
            {"symbol": tl[0].get("symbol"), "name": tl[0].get("name"), "change_pct": tl[0].get("change_pct")}
            if tl else None
        ),
        "last_updated": data.get("last_updated"),
        "source":       data.get("source", "COMPUTED"),
    }


@router.get("/breadth/gainers")
async def get_breadth_gainers(limit: int = Query(default=10, ge=1, le=50)):
    """Top gainers list."""
    from crawler.market_breadth import get_breadth_cache
    data = get_breadth_cache()
    return data.get("top_gainers", [])[:limit]


@router.get("/breadth/losers")
async def get_breadth_losers(limit: int = Query(default=10, ge=1, le=50)):
    """Top losers list."""
    from crawler.market_breadth import get_breadth_cache
    data = get_breadth_cache()
    return data.get("top_losers", [])[:limit]


@router.get("/breadth/active")
async def get_breadth_active(limit: int = Query(default=10, ge=1, le=50)):
    """Most active stocks by volume."""
    from crawler.market_breadth import get_breadth_cache
    data = get_breadth_cache()
    return data.get("most_active", [])[:limit]


@router.get("/breadth/52week")
async def get_breadth_52week():
    """Stocks at/near 52-week highs and lows."""
    from crawler.market_breadth import get_breadth_cache
    data = get_breadth_cache()
    high = data.get("week52_high", [])
    low  = data.get("week52_low",  [])
    return {"high": high, "low": low, "high_count": len(high), "low_count": len(low)}


@router.get("/breadth/history")
async def get_breadth_history():
    """Intraday breadth timeline (last 50 readings, ~100 minutes)."""
    from crawler.market_breadth import BREADTH_HISTORY
    return list(BREADTH_HISTORY)


@router.post("/breadth/refresh")
async def refresh_breadth():
    """Force immediate breadth data refresh."""
    from crawler.market_breadth import refresh_breadth_data
    result = await refresh_breadth_data()
    nse = result.get("nse", {})
    wl  = result.get("watchlist", {})
    return {
        "nse_advances":       nse.get("advances", 0),
        "nse_declines":       nse.get("declines", 0),
        "nse_market_mood":    nse.get("market_mood", "NEUTRAL"),
        "watchlist_advances": wl.get("advances", 0),
        "watchlist_declines": wl.get("declines", 0),
        "source":             result.get("source", "COMPUTED"),
        "last_updated":       result.get("last_updated"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Sector Heatmap endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/sectors")
async def get_sectors_full():
    """Return full SECTOR_CACHE dict keyed by sector name."""
    from crawler.sector_data import get_sector_cache
    return get_sector_cache()


@router.get("/sectors/summary")
async def get_sectors_summary():
    """Compact sorted list for heatmap rendering."""
    from crawler.sector_data import get_sector_summary
    return get_sector_summary()


@router.get("/sectors/rotation")
async def get_sector_rotation():
    """Sector rotation signal — outperforming/underperforming vs NIFTY 50."""
    from crawler.sector_data import get_sector_rotation_signal
    return get_sector_rotation_signal()


@router.get("/sectors/{sector_key}")
async def get_sector_detail(sector_key: str):
    """Full data for one sector including all stocks."""
    from crawler.sector_data import get_sector_cache
    cache = get_sector_cache()
    data  = cache.get(sector_key)
    if not data:
        raise HTTPException(status_code=404, detail=f"Sector '{sector_key}' not found")
    return data


@router.post("/sectors/refresh")
async def refresh_sectors():
    """Force immediate sector data refresh."""
    from crawler.sector_data import refresh_sector_data, get_sector_summary
    await refresh_sector_data()
    return get_sector_summary()


# ═══════════════════════════════════════════════════════════════════════════════
# Market Calendar endpoints
# ═══════════════════════════════════════════════════════════════════════════════

def _ev_dict(ev) -> dict:
    from engine.calendar_engine import _event_to_dict
    return _event_to_dict(ev)


@router.get("/calendar")
async def get_calendar(
    from_date: Optional[str] = None,
    to_date:   Optional[str] = None,
    types:     Optional[str] = None,
    symbol:    Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    from engine.calendar_engine import get_events_for_range, get_events_by_date, _event_to_dict
    import datetime as _dt

    today = datetime.date.today()
    fd = datetime.date.fromisoformat(from_date) if from_date else today
    td = datetime.date.fromisoformat(to_date)   if to_date   else today + datetime.timedelta(days=30)
    ev_types = [t.strip() for t in types.split(",")] if types else None

    events = await get_events_for_range(db, fd, td, ev_types, symbol)
    by_date = {k: [_event_to_dict(e) for e in v] for k, v in get_events_by_date(events).items()}
    return {
        "events_by_date": by_date,
        "total_events":   len(events),
        "date_range":     {"from": str(fd), "to": str(td)},
    }


@router.get("/calendar/upcoming")
async def get_calendar_upcoming(
    days:  int = 14,
    db: AsyncSession = Depends(get_db),
):
    from engine.calendar_engine import get_upcoming_events, _event_to_dict
    import datetime as _dt

    events = await get_upcoming_events(db, days=days)
    today  = datetime.date.today()

    def _days_away(ev_date_str: str) -> int:
        return (datetime.date.fromisoformat(ev_date_str) - today).days

    by_type: dict[str, int] = {}
    for ev in events:
        by_type[ev.event_type] = by_type.get(ev.event_type, 0) + 1

    next_expiry = next(
        ({"date": str(e.event_date), "days_away": _days_away(str(e.event_date)), "title": e.title}
         for e in events if e.event_type == "FNO_EXPIRY"),
        None
    )
    next_rbi = next(
        ({"date": str(e.event_date), "days_away": _days_away(str(e.event_date)), "title": e.title}
         for e in events if e.event_type == "RBI_MPC"),
        None
    )
    next_ipo = next(
        ({"date": str(e.event_date), "days_away": _days_away(str(e.event_date)), "title": e.title}
         for e in events if e.event_type == "IPO"),
        None
    )
    next_earnings = next(
        ({"date": str(e.event_date), "days_away": _days_away(str(e.event_date)), "title": e.title}
         for e in events if e.event_type == "EARNINGS"),
        None
    )

    return {
        "events":        [_event_to_dict(e) for e in events],
        "by_type":       by_type,
        "next_expiry":   next_expiry,
        "next_rbi":      next_rbi,
        "next_ipo":      next_ipo,
        "next_earnings": next_earnings,
    }


@router.get("/calendar/today")
async def get_calendar_today(db: AsyncSession = Depends(get_db)):
    from engine.calendar_engine import get_events_for_range, _event_to_dict
    today  = datetime.date.today()
    events = await get_events_for_range(db, today, today)
    return {"date": str(today), "events": [_event_to_dict(e) for e in events]}


@router.get("/calendar/month/{year}/{month}")
async def get_calendar_month(
    year: int,
    month: int,
    db: AsyncSession = Depends(get_db),
):
    from engine.calendar_engine import get_events_for_range, get_events_by_date, _event_to_dict
    import calendar as _cal
    _, last_day = _cal.monthrange(year, month)
    fd = datetime.date(year, month, 1)
    td = datetime.date(year, month, last_day)
    events  = await get_events_for_range(db, fd, td)
    by_date = {k: [_event_to_dict(e) for e in v] for k, v in get_events_by_date(events).items()}
    return {
        "year": year,
        "month": month,
        "events_by_date": by_date,
        "total_events": len(events),
    }


@router.get("/calendar/expiry")
async def get_calendar_expiry(db: AsyncSession = Depends(get_db)):
    from engine.calendar_engine import get_events_for_range, _event_to_dict
    today  = datetime.date.today()
    ahead  = today + datetime.timedelta(days=60)
    events = await get_events_for_range(db, today, ahead, ["FNO_EXPIRY"])

    def _pick(exchange: str, monthly: bool | None = None):
        for e in events:
            meta = e.event_metadata or {}
            if exchange == "NSE" and meta.get("exchange", "NSE") == "NSE":
                if monthly is None or meta.get("is_monthly") == monthly:
                    return {"date": str(e.event_date), "days_away": (e.event_date - today).days, "title": e.title}
            if exchange == "BSE" and meta.get("exchange") == "BSE":
                if monthly is None or meta.get("is_monthly") == monthly:
                    return {"date": str(e.event_date), "days_away": (e.event_date - today).days, "title": e.title}
        return None

    return {
        "next_weekly_nifty":   _pick("NSE", False),
        "next_monthly_nifty":  _pick("NSE", True),
        "next_weekly_sensex":  _pick("BSE", False),
        "next_monthly_sensex": _pick("BSE", True),
        "upcoming_expiries":   [_event_to_dict(e) for e in events[:8]],
    }


@router.get("/calendar/rbi")
async def get_calendar_rbi(db: AsyncSession = Depends(get_db)):
    from engine.calendar_engine import get_events_for_range, _event_to_dict
    today  = datetime.date.today()
    ahead  = today + datetime.timedelta(days=365)
    events = await get_events_for_range(db, today, ahead, ["RBI_MPC"])

    decisions = [e for e in events if "Decision" in e.title]
    next_mtg  = decisions[0] if decisions else None

    return {
        "next_meeting":     {
            "start_date":    str(next_mtg.start_date) if next_mtg else None,
            "decision_date": str(next_mtg.event_date) if next_mtg else None,
            "days_away":     (next_mtg.event_date - today).days if next_mtg else None,
        } if next_mtg else None,
        "current_repo_rate": 5.25,
        "all_meetings":      [_event_to_dict(e) for e in events],
    }


@router.get("/calendar/ipos")
async def get_calendar_ipos(db: AsyncSession = Depends(get_db)):
    from engine.calendar_engine import get_events_for_range, _event_to_dict
    today    = datetime.date.today()
    past_30  = today - datetime.timedelta(days=30)
    ahead_90 = today + datetime.timedelta(days=90)

    all_ipo  = await get_events_for_range(db, past_30, ahead_90, ["IPO"])
    upcoming = [_event_to_dict(e) for e in all_ipo if e.event_date >= today]
    recent   = [_event_to_dict(e) for e in all_ipo if e.event_date < today]
    return {"upcoming": upcoming, "recently_listed": recent}


@router.post("/calendar/seed")
async def seed_calendar(db: AsyncSession = Depends(get_db)):
    from engine.calendar_engine import seed_calendar_events
    result = await seed_calendar_events(db, months_ahead=3)
    return result
