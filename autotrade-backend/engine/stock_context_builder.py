"""Stock context builder for the AI chat engine.

Aggregates live price, technicals, fundamentals, signals, news and FII/DII
flows into one structured dict that is fed to the LLM.
Calls EXISTING modules only — writes no new data-fetching logic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.live_prices import PRICE_CACHE, SYMBOLS_CONFIG
from db.models import FIIDIIFlow, NewsItem, Signal
from utils.logger import logger

# ── Symbol catalogue ──────────────────────────────────────────────────────────

SYMBOL_ALIASES: dict[str, str] = {
    "HDFC BANK":   "HDFCBANK.NS",
    "HDFCBANK":    "HDFCBANK.NS",
    "HDFC":        "HDFCBANK.NS",
    "RELIANCE":    "RELIANCE.NS",
    "TCS":         "TCS.NS",
    "INFOSYS":     "INFY.NS",
    "INFY":        "INFY.NS",
    "SBI":         "SBIN.NS",
    "ICICI":       "ICICIBANK.NS",
    "ICICI BANK":  "ICICIBANK.NS",
    "ICICIBANK":   "ICICIBANK.NS",
    "WIPRO":       "WIPRO.NS",
    "TATA":        "TCS.NS",
    "BAJAJ":       "BAJFINANCE.NS",
    "BAJFINANCE":  "BAJFINANCE.NS",
    "AIRTEL":      "BHARTIARTL.NS",
    "BHARTIARTL":  "BHARTIARTL.NS",
    "KOTAK":       "KOTAKBANK.NS",
    "KOTAKBANK":   "KOTAKBANK.NS",
    "AXIS":        "AXISBANK.NS",
    "AXIS BANK":   "AXISBANK.NS",
    "AXISBANK":    "AXISBANK.NS",
    "NIFTY":       "^NSEI",
    "NIFTY 50":    "^NSEI",
    "NIFTY50":     "^NSEI",
    "SENSEX":      "^BSESN",
    "BANK NIFTY":  "^NSEBANK",
    "BANKNIFTY":   "^NSEBANK",
    "LT":          "LT.NS",
    "HCL":         "HCLTECH.NS",
    "HCLTECH":     "HCLTECH.NS",
    "MARUTI":      "MARUTI.NS",
    "SUNPHARMA":   "SUNPHARMA.NS",
    "ITC":         "ITC.NS",
}

# Build reverse lookup: yfinance symbol → display name
_DISPLAY_NAMES: dict[str, str] = {cfg["symbol"]: cfg["name"] for cfg in SYMBOLS_CONFIG}


def resolve_symbol(query: str) -> str | None:
    """Convert user input to a yfinance-compatible symbol."""
    if not query:
        return None
    q = query.strip()

    # Already fully qualified (e.g. RELIANCE.NS, ^NSEI)
    if q.endswith(".NS") or q.endswith(".BO") or q.startswith("^"):
        return q

    upper = q.upper()

    # Exact alias match
    if upper in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[upper]

    # Direct PRICE_CACHE hit
    if upper in PRICE_CACHE:
        return upper
    if upper + ".NS" in PRICE_CACHE:
        return upper + ".NS"

    # Fuzzy: check if query is a substring of any cache key
    for cached_sym in PRICE_CACHE:
        if upper in cached_sym.upper():
            return cached_sym

    return None


# ── Data formatters ───────────────────────────────────────────────────────────

def format_indicators(ind: Any) -> dict | None:
    if ind is None:
        return None
    return {
        "rsi":                  round(ind.rsi, 1) if ind.rsi == ind.rsi else None,  # NaN check
        "rsi_signal":           ind.rsi_signal,
        "macd_cross":           ind.macd_cross,
        "bb_position":          ind.bb_position,
        "ema_trend":            ind.ema_trend,
        "composite_score":      round(ind.composite_score, 1),
        "atr":                  round(ind.atr, 2) if ind.atr == ind.atr else None,
        "supertrend_direction": getattr(ind, "supertrend_direction", None),
        "stoch_signal":         getattr(ind, "stoch_signal", None),
        "ichimoku_signal":      getattr(ind, "ichimoku_signal", None),
        "adx_trend_strength":   getattr(ind, "adx_trend_strength", None),
        "vwap_position":        getattr(ind, "vwap_position", None),
    }


def format_patterns(psum: dict | None) -> dict | None:
    if psum is None:
        return None
    return {
        "direction":   psum.get("direction", "NEUTRAL"),
        "total_score": psum.get("total_score", 0),
        "strongest":   psum.get("strongest_pattern"),
        "count":       psum.get("count", 0),
    }


def format_fundamentals(f: dict) -> dict:
    if not f:
        return {}
    raw = {
        "pe_ratio":       f.get("trailingPE"),
        "pb_ratio":       f.get("priceToBook"),
        "roe":            f.get("returnOnEquity"),
        "debt_equity":    f.get("debtToEquity"),
        "revenue_growth": f.get("revenueGrowth"),
        "dividend_yield": f.get("dividendYield"),
        "market_cap_cr":  round(f.get("marketCap", 0) / 1e7, 0) if f.get("marketCap") else None,
        "eps":            f.get("trailingEps"),
        "sector":         f.get("sector"),
        "industry":       f.get("industry"),
    }
    return {k: v for k, v in raw.items() if v is not None}


# ── Main context builder ──────────────────────────────────────────────────────

async def build_stock_context(
    symbol: str,
    session: AsyncSession,
    timeframe: str = "1h",
) -> dict:
    """Assemble all available data for a symbol into one context dict."""

    # ── Step 1: Price from PRICE_CACHE (instant) ──────────────────────────────
    price_data: dict = PRICE_CACHE.get(symbol, {})
    if not price_data.get("price"):
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            fi = ticker.fast_info
            last  = float(getattr(fi, "last_price", 0) or 0)
            prev  = float(getattr(fi, "previous_close", 0) or 0)
            price_data = {
                "price":      round(last, 2),
                "change":     round(last - prev, 2),
                "change_pct": round((last - prev) / prev * 100, 2) if prev else 0.0,
                "high":       float(getattr(fi, "day_high", 0) or 0),
                "low":        float(getattr(fi, "day_low", 0) or 0),
                "volume":     int(getattr(fi, "three_month_average_volume", 0) or 0),
                "52w_high":   float(getattr(fi, "fifty_two_week_high", 0) or 0),
                "52w_low":    float(getattr(fi, "fifty_two_week_low", 0) or 0),
            }
        except Exception as exc:
            logger.debug("build_stock_context: yfinance price fetch failed for %s: %s", symbol, exc)

    # ── Step 2: Candles from DB ───────────────────────────────────────────────
    from crawler.price_feed import get_latest_candles
    candles = []
    try:
        candles = await get_latest_candles(symbol, timeframe, 200, session)
    except Exception as exc:
        logger.debug("build_stock_context: candle fetch failed for %s: %s", symbol, exc)

    df: pd.DataFrame | None = None
    if len(candles) >= 20:
        df = pd.DataFrame([{
            "open":      c.open,
            "high":      c.high,
            "low":       c.low,
            "close":     c.close,
            "volume":    c.volume,
            "timestamp": c.timestamp,
        } for c in candles])
        df = df.sort_values("timestamp").reset_index(drop=True)

    # ── Steps 3 & 4: Indicators, patterns, fundamentals in parallel ───────────
    from engine.indicators import compute_indicators
    from engine.candlestick import detect_patterns, get_pattern_summary
    from engine.fundamental_analyzer import fetch_fundamentals_yfinance

    ind_task  = asyncio.to_thread(compute_indicators, df)    if df is not None else asyncio.sleep(0)
    pat_task  = asyncio.to_thread(detect_patterns, df)       if df is not None else asyncio.sleep(0)
    fund_task = asyncio.to_thread(fetch_fundamentals_yfinance, symbol)

    ind_raw, pat_raw, fund_raw = await asyncio.gather(
        ind_task, pat_task, fund_task,
        return_exceptions=True,
    )

    indicators = format_indicators(ind_raw)  if not isinstance(ind_raw, Exception) and df is not None else None
    patterns   = format_patterns(get_pattern_summary(pat_raw) if not isinstance(pat_raw, Exception) and df is not None else None)
    fundamentals = format_fundamentals(fund_raw if not isinstance(fund_raw, Exception) else {})

    # ── Step 5: Latest signal from DB ────────────────────────────────────────
    latest_signal: dict | None = None
    try:
        sig = (await session.execute(
            select(Signal)
            .where(Signal.symbol == symbol)
            .order_by(desc(Signal.created_at))
            .limit(1)
        )).scalar_one_or_none()
        if sig:
            latest_signal = {
                "action":      sig.signal_type.value if hasattr(sig.signal_type, "value") else str(sig.signal_type),
                "confidence":  sig.confidence,
                "final_score": sig.final_score,
                "created_at":  sig.created_at.isoformat(),
                "pattern":     sig.pattern_name,
            }
    except Exception as exc:
        logger.debug("build_stock_context: signal fetch failed: %s", exc)

    # ── Step 6: News + sentiment ──────────────────────────────────────────────
    from crawler.news_crawler import get_market_sentiment
    sentiment_score = 0.0
    recent_news: list[dict] = []
    try:
        sentiment_score = await get_market_sentiment(symbol, session)
        bare = symbol.replace(".NS", "").replace(".BO", "")
        news_rows = (await session.execute(
            select(NewsItem)
            .where(NewsItem.tickers_affected.contains([bare]))
            .order_by(desc(NewsItem.published_at))
            .limit(5)
        )).scalars().all()
        recent_news = [
            {
                "headline":     n.headline,
                "sentiment":    n.sentiment,
                "score":        n.score,
                "source":       n.source,
                "published_at": n.published_at.isoformat() if n.published_at else None,
            }
            for n in news_rows
        ]
    except Exception as exc:
        logger.debug("build_stock_context: news/sentiment failed: %s", exc)

    # ── Step 7: FII/DII context ───────────────────────────────────────────────
    fii_data: dict | None = None
    try:
        flows = (await session.execute(
            select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(3)
        )).scalars().all()
        if flows:
            fii_data = {
                "latest_date":      str(flows[0].date),
                "fii_net_3day":     sum(f.fii_net_buy for f in flows),
                "dii_net_3day":     sum(f.dii_net_buy for f in flows),
                "market_direction": flows[0].market_direction,
            }
    except Exception as exc:
        logger.debug("build_stock_context: FII/DII fetch failed: %s", exc)

    display_name = _DISPLAY_NAMES.get(symbol, symbol.replace(".NS", "").replace(".BO", ""))

    # ── Master Intelligence Hub score + macro context ────────────────────────
    hub_score: dict = {}
    macro_ctx: dict = {}
    try:
        from db.models import MasterIntelligenceScore
        from sqlalchemy import select, desc
        row = (await session.execute(
            select(MasterIntelligenceScore)
            .where(MasterIntelligenceScore.symbol == symbol)
            .order_by(desc(MasterIntelligenceScore.scored_at)).limit(1)
        )).scalar_one_or_none()
        if row:
            hub_score = {
                "master_score":   row.master_score,
                "signal":         row.signal,
                "rank":           row.rank,
                "is_blocked":     row.is_blocked,
                "blocked_reason": row.blocked_reason,
                "reasoning":      row.reasoning or {},
            }
        import engine.intelligence_hub as hub
        if hub.LAST_MACRO_CONTEXT is not None:
            m = hub.LAST_MACRO_CONTEXT
            macro_ctx = {
                "total_bias": m.total_macro_bias, "vix": m.india_vix,
                "market_mood": m.nse_market_mood, "fii_3d": m.fii_net_3d,
            }
    except Exception as exc:
        logger.debug("build_stock_context: hub score fetch failed for %s: %s", symbol, exc)

    return {
        "symbol":           symbol,
        "display_name":     display_name,
        "price":            price_data,
        "indicators":       indicators,
        "patterns":         patterns,
        "fundamentals":     fundamentals,
        "signal":           latest_signal,
        "sentiment":        {"score": sentiment_score, "news": recent_news},
        "fii_dii":          fii_data,
        "hub_score":        hub_score,
        "macro":            macro_ctx,
        "candle_count":     len(candles),
        "timeframe":        timeframe,
        "context_built_at": datetime.utcnow().isoformat(),
    }
