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

# Live India VIX is one slow-moving number, but calculate_india_vix_score() is
# called once PER SYMBOL during a scan. Uncached, a 443-symbol scan fired ~443 live
# yfinance VIX fetches → yfinance 429 rate-limiting that also starved the candle
# crawl. Cache the live fetch for a short TTL so a whole scan reuses one fetch.
_LIVE_VIX_CACHE: dict = {"value": None, "mono": 0.0}
_LIVE_VIX_TTL: float = 60.0  # seconds

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
    "IT":      "^CNXIT",
    "BANKING": "^NSEBANK",
    "PHARMA":  "^CNXPHARMA",
    "FMCG":    "^CNXFMCG",
    "AUTO":    "^CNXAUTO",
    "INFRA":   "^CNXINFRA",
    "ENERGY":  "^CNXENERGY",
    "METAL":   "^CNXMETAL",
    "REALTY":  "^CNXREALTY",
    "MEDIA":   "^CNXMEDIA",
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
    """Return (price_now, price_30d_ago) from the last ``limit`` daily candles.

    Resolution order:
      1. ``candles`` table at ``timeframe='1d'`` (fast path, hot during the
         day once kite_sync_candles has populated it).
      2. ``candles`` table at ``timeframe='1h'`` (we have these for ^NSEI
         and ^NSEBANK already; rebuilt into a daily-close approximation).
      3. yfinance on-demand fetch for sector indices (^CNXIT / ^CNXFMCG …)
         that no scheduled task currently crawls. 30-second cache in
         ``_YF_SECTOR_CACHE`` so the per-symbol cycle doesn't hammer Yahoo.

    Returns (None, None) only when all three paths fail.
    """
    rows = (await session.execute(
        select(Candle)
        .where(Candle.symbol == ticker, Candle.timeframe == "1d")
        .order_by(desc(Candle.timestamp))
        .limit(limit)
    )).scalars().all()

    if len(rows) >= 2:
        return float(rows[0].close), float(rows[-1].close)

    # Fallback 1: 1h candles for the same ticker — sufficient for a
    # 30-day comparison even with ~6 bars/day intraday density.
    rows_1h = (await session.execute(
        select(Candle)
        .where(Candle.symbol == ticker, Candle.timeframe == "1h")
        .order_by(desc(Candle.timestamp))
        .limit(limit * 7)
    )).scalars().all()
    if len(rows_1h) >= 2:
        return float(rows_1h[0].close), float(rows_1h[-1].close)

    # Fallback 2: yfinance on-demand for sector indices we don't crawl.
    return await _fetch_yfinance_close_pair(ticker)


_YF_SECTOR_CACHE: dict[str, tuple[tuple[float | None, float | None], float]] = {}
_YF_SECTOR_TTL = 30 * 60   # 30 minutes — sector indices change slowly intraday


async def _fetch_yfinance_close_pair(ticker: str) -> tuple[float, float] | tuple[None, None]:
    """Fetch (latest_close, ~30d-ago_close) from yfinance for ``ticker``.

    Used only as a last resort by _fetch_candle_prices when the DB has no
    cached candles for a sector index. Cached in-process for 30 minutes.
    """
    import time as _t
    hit = _YF_SECTOR_CACHE.get(ticker)
    if hit and (_t.monotonic() - hit[1]) < _YF_SECTOR_TTL:
        return hit[0]

    try:
        import contextlib as _ctx
        import io as _io
        import yfinance as _yf

        def _blocking():
            with _ctx.redirect_stdout(_io.StringIO()), _ctx.redirect_stderr(_io.StringIO()):
                return _yf.Ticker(ticker).history(period="35d", interval="1d", auto_adjust=False)

        df = await asyncio.to_thread(_blocking)
        if df is None or df.empty or len(df) < 2:
            _YF_SECTOR_CACHE[ticker] = ((None, None), _t.monotonic())
            return None, None
        closes = df["Close"].dropna()
        if len(closes) < 2:
            _YF_SECTOR_CACHE[ticker] = ((None, None), _t.monotonic())
            return None, None
        result = (float(closes.iloc[-1]), float(closes.iloc[0]))
        _YF_SECTOR_CACHE[ticker] = (result, _t.monotonic())
        return result
    except Exception as exc:
        logger.debug(f"_fetch_yfinance_close_pair {ticker} failed: {exc}")
        _YF_SECTOR_CACHE[ticker] = ((None, None), _t.monotonic())
        return None, None


# ── 1. India VIX score ────────────────────────────────────────────────────────

async def calculate_india_vix_score(
    session: AsyncSession,
    bar_date: datetime.datetime | None = None,
) -> float:
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

    When bar_date is provided (backtest mode) the function queries the candles
    table for the most-recent ``^INDIAVIX`` daily bar on or before that date
    instead of doing a live yfinance fetch, eliminating look-ahead bias.
    """
    vix: float = 0.0

    if bar_date is not None and session is not None:
        # Backtest path: read historical VIX from the candles table.
        try:
            from db.models import Candle
            row = (await session.execute(
                select(Candle)
                .where(
                    Candle.symbol    == "^INDIAVIX",
                    Candle.timeframe == "1d",
                    Candle.timestamp <= bar_date,
                )
                .order_by(Candle.timestamp.desc())
                .limit(1)
            )).scalar_one_or_none()
            vix = float(row.close) if row else 15.0  # default neutral if no data
        except Exception as exc:
            logger.warning(f"calculate_india_vix_score: historical VIX query failed ({exc}); using neutral")
            return 0.0
    else:
        # Live path: fetch current VIX from yfinance, cached for _LIVE_VIX_TTL so a
        # full per-symbol scan triggers ONE fetch instead of one per symbol (which
        # caused yfinance 429 storms that starved the candle crawl).
        import time as _time
        _now = _time.monotonic()
        _cached = _LIVE_VIX_CACHE["value"]
        if _cached is not None and (_now - _LIVE_VIX_CACHE["mono"]) < _LIVE_VIX_TTL:
            vix = _cached
        else:
            from crawler.india_price_feed import fetch_india_vix  # deferred — optional dep
            try:
                loop = asyncio.get_event_loop()
                vix  = await loop.run_in_executor(None, fetch_india_vix)
                if vix and vix > 0:
                    _LIVE_VIX_CACHE["value"] = vix
                    _LIVE_VIX_CACHE["mono"]  = _now
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
        # Demoted from WARNING — this fires legitimately on a fresh DB until
        # daily 1d candles are crawled. The DB + 1h fallback + yfinance
        # fallback chain already covers the common cases; reaching here means
        # all three failed, which is uncommon enough to leave at debug.
        logger.debug(
            f"calculate_sector_rotation_score: insufficient data for {index_ticker} "
            f"(DB 1d + 1h + yfinance all empty)"
        )
        return 0.0

    # Fetch Nifty 50 benchmark prices
    nifty_now, nifty_30d = await _fetch_candle_prices(_NIFTY50, session)
    if nifty_now is None or nifty_30d is None or nifty_30d == 0:
        logger.debug("calculate_sector_rotation_score: insufficient Nifty 50 data")
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
