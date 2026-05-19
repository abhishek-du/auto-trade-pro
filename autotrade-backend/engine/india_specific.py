"""India-specific market analysis: VIX scoring and NSE sector rotation.

Provides two scoring inputs for the 15-algorithm confluence engine:
  - India VIX sentiment score (contrarian: extreme fear = buy)
  - Sector rotation score (relative strength of symbol's sector vs Nifty 50)

Public API
----------
calculate_india_vix_score(vix_value)                          -> dict
fetch_sector_returns(lookback_days)                           -> dict  (async)
get_symbol_sector(symbol)                                     -> str | None
calculate_sector_rotation_score(symbol, sector_returns)       -> dict
run_india_specific_analysis(vix_value, symbol)                -> dict  (async)
"""

from __future__ import annotations

import asyncio
import math
from typing import Optional

import pandas as pd

from utils.logger import logger

# ── Sector → index mapping (yfinance tickers) ────────────────────────────────

_SECTOR_INDICES: dict[str, str] = {
    "IT":       "^CNXIT",
    "BANKING":  "^NSEBANK",
    "PHARMA":   "^CNXPHARMA",
    "AUTO":     "^CNXAUTO",
    "FMCG":     "^CNXFMCG",
    "ENERGY":   "^CNXENERGY",
    "INFRA":    "^CNXINFRA",
    "METAL":    "^CNXMETAL",
}

# Nifty 50 benchmark for relative-strength calculation
_NIFTY50_TICKER = "^NSEI"

# ── Symbol → sector mapping (NSE .NS suffix) ─────────────────────────────────

_SYMBOL_SECTOR: dict[str, str] = {
    # IT
    "TCS.NS": "IT", "INFY.NS": "IT", "WIPRO.NS": "IT", "HCLTECH.NS": "IT",
    "TATAELXSI.NS": "IT", "PERSISTENT.NS": "IT", "COFORGE.NS": "IT", "LTTS.NS": "IT",
    # Banking & Finance
    "HDFCBANK.NS": "BANKING", "SBIN.NS": "BANKING", "ICICIBANK.NS": "BANKING",
    "AXISBANK.NS": "BANKING", "KOTAKBANK.NS": "BANKING", "BAJFINANCE.NS": "BANKING",
    "MUTHOOTFIN.NS": "BANKING",
    # FMCG
    "HINDUNILVR.NS": "FMCG", "ITC.NS": "FMCG", "NESTLEIND.NS": "FMCG",
    "PIDILITIND.NS": "FMCG", "ASTRAL.NS": "FMCG",
    # Pharma & Healthcare
    "SUNPHARMA.NS": "PHARMA", "DRREDDY.NS": "PHARMA",
    "METROPOLIS.NS": "PHARMA", "LALPATHLAB.NS": "PHARMA",
    # Auto
    "MARUTI.NS": "AUTO",
    # Energy
    "RELIANCE.NS": "ENERGY", "POWERGRID.NS": "ENERGY",
    # Infra & Industrials
    "LT.NS": "INFRA", "ULTRACEMCO.NS": "INFRA", "VOLTAS.NS": "INFRA",
    # Specialty / Paints
    "ASIANPAINT.NS": "SPECIALTY",
    # Telecom
    "BHARTIARTL.NS": "TELECOM",
}


# ── India VIX scoring ─────────────────────────────────────────────────────────

def calculate_india_vix_score(vix_value: float) -> dict:
    """Score India VIX on a contrarian basis.

    Extreme fear (high VIX) = buying opportunity; extreme complacency
    (low VIX) = caution.  Returns score in [-20, +30].

    Score table
    -----------
    VIX > 25   EXTREME_FEAR          +30  (strong contrarian buy)
    VIX 20-25  HIGH_FEAR             +20  (buy opportunity)
    VIX 15-20  NORMAL                  0
    VIX 12-15  LOW_VOLATILITY        -10  (mild caution)
    VIX < 12   EXTREME_COMPLACENCY   -20  (sell signal)
    """
    if math.isnan(vix_value) or vix_value <= 0:
        return {"vix_value": vix_value, "vix_signal": "NORMAL", "vix_score": 0.0}

    if vix_value > 25:
        signal, score = "EXTREME_FEAR",        30.0
    elif vix_value > 20:
        signal, score = "HIGH_FEAR",           20.0
    elif vix_value > 15:
        signal, score = "NORMAL",               0.0
    elif vix_value > 12:
        signal, score = "LOW_VOLATILITY",      -10.0
    else:
        signal, score = "EXTREME_COMPLACENCY", -20.0

    logger.info(f"India VIX: {vix_value:.2f}  │  {signal}  │  score={score:+.0f}")
    return {"vix_value": vix_value, "vix_signal": signal, "vix_score": score}


# ── Sector return fetcher ─────────────────────────────────────────────────────

def _download_sector_returns(lookback_days: int) -> dict[str, float]:
    """Blocking yfinance download — call via run_in_executor."""
    import yfinance as yf

    tickers = list(_SECTOR_INDICES.values()) + [_NIFTY50_TICKER]
    period  = f"{lookback_days + 5}d"   # buffer for weekends/holidays

    try:
        raw = yf.download(tickers, period=period, auto_adjust=True,
                          progress=False, threads=True)
    except Exception as exc:
        logger.warning(f"fetch_sector_returns: yfinance download failed: {exc}")
        return {}

    # yfinance returns MultiIndex when multiple tickers
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]] if "Close" in raw.columns else raw

    if close.empty:
        return {}

    # 5-day return for each ticker
    returns: dict[str, float] = {}
    window = min(lookback_days, len(close) - 1)
    if window < 1:
        return {}

    for ticker in tickers:
        if ticker not in close.columns:
            continue
        col = close[ticker].dropna()
        if len(col) < window + 1:
            continue
        ret = (float(col.iloc[-1]) - float(col.iloc[-1 - window])) / float(col.iloc[-1 - window]) * 100
        returns[ticker] = round(ret, 4)

    return returns


async def fetch_sector_returns(lookback_days: int = 5) -> dict[str, float]:
    """Return {sector_name: pct_return} for each NSE sector index.

    Also includes "NIFTY50" key for the benchmark return.
    Runs blocking yfinance download in a thread executor.
    """
    loop = asyncio.get_event_loop()
    ticker_returns: dict[str, float] = await loop.run_in_executor(
        None, _download_sector_returns, lookback_days
    )

    if not ticker_returns:
        logger.warning("fetch_sector_returns: no data returned — using empty dict")
        return {}

    # Map ticker → sector name
    result: dict[str, float] = {}
    for sector, ticker in _SECTOR_INDICES.items():
        if ticker in ticker_returns:
            result[sector] = ticker_returns[ticker]

    nifty_ret = ticker_returns.get(_NIFTY50_TICKER)
    if nifty_ret is not None:
        result["NIFTY50"] = nifty_ret

    # Compute relative strength: sector return minus Nifty 50 return
    if "NIFTY50" in result:
        benchmark = result["NIFTY50"]
        for sector in list(_SECTOR_INDICES.keys()):
            if sector in result:
                result[f"{sector}_RS"] = round(result[sector] - benchmark, 4)

    logger.info(
        "Sector returns: "
        + "  ".join(f"{k}={v:+.2f}%" for k, v in result.items() if not k.endswith("_RS"))
    )
    return result


# ── Symbol sector lookup ──────────────────────────────────────────────────────

def get_symbol_sector(symbol: str) -> Optional[str]:
    """Return the sector name for an NSE symbol, or None if unknown."""
    return _SYMBOL_SECTOR.get(symbol)


# ── Sector rotation scoring ───────────────────────────────────────────────────

def calculate_sector_rotation_score(
    symbol: str,
    sector_returns: dict[str, float],
) -> dict:
    """Score a symbol based on its sector's relative strength vs Nifty 50.

    Uses relative-strength keys (sector_RS) when available; falls back to
    absolute returns sorted against other sectors.

    Score table
    -----------
    Top-3 sector (strong inflow)     +15
    Mid-range sector                   0
    Bottom-3 sector (outflow)        -15
    Symbol sector unknown              0
    """
    _empty = {"sector": None, "sector_rs": None,
              "sector_rank": None, "rotation_score": 0.0}

    sector = get_symbol_sector(symbol)
    if not sector:
        logger.debug(f"calculate_sector_rotation_score: {symbol} sector unknown")
        return _empty

    if not sector_returns:
        return {**_empty, "sector": sector}

    # Prefer relative-strength values; fall back to absolute returns
    rs_key    = f"{sector}_RS"
    rs_value  = sector_returns.get(rs_key) or sector_returns.get(sector)
    if rs_value is None:
        return {**_empty, "sector": sector}

    # Rank all sectors by relative strength
    ranked = sorted(
        [s for s in _SECTOR_INDICES if f"{s}_RS" in sector_returns or s in sector_returns],
        key=lambda s: sector_returns.get(f"{s}_RS", sector_returns.get(s, 0)),
        reverse=True,
    )
    n_sectors = len(ranked)
    rank      = ranked.index(sector) + 1 if sector in ranked else None

    if rank is None:
        score = 0.0
    elif rank <= 3:
        score = 15.0
    elif rank > n_sectors - 3:
        score = -15.0
    else:
        score = 0.0

    logger.info(
        f"Sector rotation: {symbol} → {sector}  "
        f"RS={rs_value:+.2f}%  rank={rank}/{n_sectors}  score={score:+.0f}"
    )
    return {
        "sector": sector,
        "sector_rs": rs_value,
        "sector_rank": rank,
        "rotation_score": score,
    }


# ── Async orchestrator ────────────────────────────────────────────────────────

async def run_india_specific_analysis(
    vix_value: float,
    symbol: Optional[str] = None,
) -> dict:
    """Combine VIX score and sector rotation score for a symbol.

    Parameters
    ----------
    vix_value : current India VIX (from fetch_india_vix or DB)
    symbol    : NSE symbol with .NS suffix, e.g. 'RELIANCE.NS'

    Returns
    -------
    {
        'vix_value': float,  'vix_signal': str,   'vix_score': float,
        'sector': str|None,  'sector_rs': float|None, 'sector_rank': int|None,
        'rotation_score': float,
        'combined_score': float,   # vix_score + rotation_score
    }
    """
    vix_result = calculate_india_vix_score(vix_value)

    sector_returns = await fetch_sector_returns(lookback_days=5)
    rotation_result = calculate_sector_rotation_score(symbol or "", sector_returns)

    combined = vix_result["vix_score"] + rotation_result["rotation_score"]
    combined = max(-100.0, min(100.0, combined))

    return {
        **vix_result,
        "sector":         rotation_result["sector"],
        "sector_rs":      rotation_result["sector_rs"],
        "sector_rank":    rotation_result["sector_rank"],
        "rotation_score": rotation_result["rotation_score"],
        "combined_score": combined,
    }
