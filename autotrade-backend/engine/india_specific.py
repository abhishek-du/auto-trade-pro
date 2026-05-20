"""India-specific market analysis: VIX scoring, sector rotation, RBI proximity.

Provides three scoring inputs consumed by the 15-algorithm confluence engine.

Public API
----------
calculate_india_vix_score(session)                -> float  (async)
calculate_sector_rotation_score(symbol, session)  -> float  (async)
get_rbi_event_proximity_score()                   -> float
"""

from __future__ import annotations

import asyncio
import datetime
import math

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Candle
from utils.logger import logger

# ── Sector maps ───────────────────────────────────────────────────────────────

SECTOR_MAP: dict[str, str] = {
    "RELIANCE.NS":   "ENERGY",
    "TCS.NS":        "IT",
    "INFY.NS":       "IT",
    "HCLTECH.NS":    "IT",
    "WIPRO.NS":      "IT",
    "PERSISTENT.NS": "IT",
    "HDFCBANK.NS":   "BANKING",
    "ICICIBANK.NS":  "BANKING",
    "SBIN.NS":       "BANKING",
    "KOTAKBANK.NS":  "BANKING",
    "AXISBANK.NS":   "BANKING",
    "SUNPHARMA.NS":  "PHARMA",
    "DRREDDY.NS":    "PHARMA",
    "MARUTI.NS":     "AUTO",
    "HINDUNILVR.NS": "FMCG",
    "ITC.NS":        "FMCG",
    "NESTLEIND.NS":  "FMCG",
    "LT.NS":         "INFRA",
}

SECTOR_INDEX: dict[str, str] = {
    "IT":      "NIFTYIT.NS",
    "BANKING": "^NSEBANK",
    "PHARMA":  "NIFTYPHARMA.NS",
    "FMCG":    "NIFTYFMCG.NS",
}

_NIFTY50 = "^NSEI"

# ── RBI MPC meeting dates (2026) ──────────────────────────────────────────────

_RBI_MPC_DATES_2026: list[datetime.date] = [
    datetime.date(2026, 2,  5),
    datetime.date(2026, 4,  7),
    datetime.date(2026, 6,  4),
    datetime.date(2026, 8,  5),
    datetime.date(2026, 10, 7),
    datetime.date(2026, 12, 3),
]


# ── Helper: fetch latest daily close from DB ──────────────────────────────────

async def _fetch_candle_prices(
    ticker: str,
    session: AsyncSession,
    limit: int = 32,
) -> tuple[float, float] | tuple[None, None]:
    """Return (price_now, price_30d_ago) from the last `limit` daily candles.

    Returns (None, None) when insufficient data is available.
    """
    rows = (await session.execute(
        select(Candle)
        .where(Candle.symbol == ticker, Candle.timeframe == "1d")
        .order_by(desc(Candle.timestamp))
        .limit(limit)
    )).scalars().all()

    if len(rows) < 2:
        return None, None

    price_now  = float(rows[0].close)
    price_30d  = float(rows[-1].close)
    return price_now, price_30d


# ── 1. India VIX score ────────────────────────────────────────────────────────

async def calculate_india_vix_score(session: AsyncSession) -> float:
    """Fetch India VIX and return a contrarian sentiment score.

    Scoring table
    -------------
    VIX > 40    +25  crash territory — scale in carefully
    VIX 30-40   +35  extreme fear — historically strong buy zone
    VIX 25-30   +20  high fear — contrarian buy zone
    VIX 20-25   -5   elevated uncertainty
    VIX 15-20    0   normal conditions
    VIX 12-15  +15   low fear — trending bull market
    VIX < 12   -10   complacency — market may be overheated
    """
    from crawler.india_price_feed import fetch_india_vix  # deferred — optional dep

    try:
        loop = asyncio.get_event_loop()
        vix  = await loop.run_in_executor(None, fetch_india_vix)
    except Exception as exc:
        logger.warning(f"calculate_india_vix_score: VIX fetch failed ({exc}); score=0")
        return 0.0

    if not vix or math.isnan(vix) or vix <= 0:
        logger.warning(f"calculate_india_vix_score: invalid VIX={vix}; score=0")
        return 0.0

    if vix > 40:
        score = 25.0
    elif vix > 30:
        score = 35.0
    elif vix > 25:
        score = 20.0
    elif vix > 20:
        score = -5.0
    elif vix > 15:
        score = 0.0
    elif vix >= 12:
        score = 15.0
    else:
        score = -10.0

    logger.info(f"India VIX: {vix:.2f}  │  score={score:+.0f}")
    return score


# ── 2. Sector rotation score ──────────────────────────────────────────────────

async def calculate_sector_rotation_score(
    symbol: str,
    session: AsyncSession,
) -> float:
    """Score symbol based on its sector's 30-day relative strength vs Nifty 50.

    Queries daily candles already stored in the DB (no live network call).

    Score table (relative_strength = sector_return - nifty_return)
    --------------------------------------------------------------
    RS > +5 %    +25  (strong inflow into sector)
    RS +2 to +5  +15
    RS -2 to +2    0  (in line with market)
    RS -5 to -2  -15
    RS < -5 %    -25  (significant outflow)
    """
    sector      = SECTOR_MAP.get(symbol)
    index_ticker = SECTOR_INDEX.get(sector or "")

    if not sector or not index_ticker:
        logger.debug(f"calculate_sector_rotation_score: no sector index for {symbol} ({sector})")
        return 0.0

    # Fetch sector index prices
    sect_now, sect_30d = await _fetch_candle_prices(index_ticker, session)
    if sect_now is None or sect_30d is None or sect_30d == 0:
        logger.warning(
            f"calculate_sector_rotation_score: insufficient data for {index_ticker}"
        )
        return 0.0

    # Fetch Nifty 50 benchmark prices
    nifty_now, nifty_30d = await _fetch_candle_prices(_NIFTY50, session)
    if nifty_now is None or nifty_30d is None or nifty_30d == 0:
        logger.warning("calculate_sector_rotation_score: insufficient Nifty 50 data")
        return 0.0

    sector_return = (sect_now  - sect_30d)  / sect_30d  * 100
    nifty_return  = (nifty_now - nifty_30d) / nifty_30d * 100
    rs            = sector_return - nifty_return

    if rs > 5:
        score = 25.0
    elif rs > 2:
        score = 15.0
    elif rs >= -2:
        score = 0.0
    elif rs >= -5:
        score = -15.0
    else:
        score = -25.0

    logger.info(
        f"Sector rotation: {symbol} ({sector})  "
        f"sector={sector_return:+.2f}%  nifty={nifty_return:+.2f}%  "
        f"RS={rs:+.2f}%  score={score:+.0f}"
    )
    return score


# ── 3. RBI event proximity score ──────────────────────────────────────────────

def get_rbi_event_proximity_score() -> float:
    """Return a score based on proximity to an RBI MPC meeting.

    Markets often price in uncertainty ahead of rate decisions.

    Score table
    -----------
    Within 3 days BEFORE meeting   -10  (pre-meeting uncertainty)
    Within 3 days AFTER  meeting   +5   (post-decision clarity)
    Otherwise                        0
    """
    today = datetime.date.today()

    for meeting in _RBI_MPC_DATES_2026:
        days_diff = (today - meeting).days   # negative = before meeting

        if -3 <= days_diff < 0:
            logger.info(f"RBI MPC on {meeting} in {-days_diff}d  │  score=-10")
            return -10.0

        if 0 <= days_diff <= 3:
            logger.info(f"RBI MPC was {meeting} ({days_diff}d ago)  │  score=+5")
            return 5.0

    return 0.0
