"""Refuse a tactical LONG into a sector that is broadly falling.

THE INCIDENT (2026-08-21)
-------------------------
The government allowed duty-free import of 10 lakh tonnes of raw sugar. The
whole sugar complex fell 3-7%. At 09:14 IST the tactical pipeline BOUGHT
DHAMPURSUG on a GAP_AND_GO pattern. Measured at that minute, all 13 sugar peers
were down and DHAMPURSUG itself was already -1.16%. It was squared off at a loss.

WHY THE OBVIOUS FIX DOES NOT WORK
---------------------------------
"Block entries when a bearish event names this symbol" fails here, because the
classifier read the news as BULLISH: the 05:06 and 07:00 REGULATORY_CHANGE rows
(importance 78) and the 06:43 SECTOR_MOMENTUM row all carry the sugar names in
`bullish_stocks`, with `bearish_stocks` empty. The system did not miss the news
— it read the direction backwards. Any veto keyed on the event's own direction
would have passed the trade through.

So this veto ignores what the news CLAIMS and measures what the sector is
DOING. Price is not subject to classification error.

PEER DISCOVERY
--------------
Peers are harvested from `causal_events` rather than a hand-maintained symbol ->
sector map, because no such map covers these names: NSE_SECTOR_MAP has 59
entries, india_specific.SECTOR_MAP has 18, and neither contains a single sugar
stock. Events already carry both the sector and the tickers, and they stay
current for free.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger


def _cfg(name: str, default):
    return getattr(settings, name, default)


async def sector_peers(symbol: str, session: AsyncSession) -> tuple[str | None, list[str]]:
    """(sector, peer symbols) for `symbol`, derived from today's events.

    Returns (None, []) when the symbol is not named in any sectored event —
    which is the common case and correctly means "no opinion".
    """
    bare = symbol.replace(".NS", "").replace(".BO", "").upper()
    lookback = int(_cfg("TACTICAL_SECTOR_VETO_LOOKBACK_H", 24))
    since = datetime.utcnow() - timedelta(hours=lookback)

    rows = (await session.execute(text("""
        SELECT affected_sectors::text, bullish_stocks::text, bearish_stocks::text
        FROM causal_events
        WHERE created_at >= :since
          AND affected_sectors::text NOT IN ('[]', 'null', '')
    """), {"since": since})).fetchall()

    sectors_for_symbol: set[str] = set()
    by_sector: dict[str, set[str]] = {}
    import json

    for sec_raw, bull_raw, bear_raw in rows:
        try:
            secs = json.loads(sec_raw) or []
            names = (json.loads(bull_raw) or []) + (json.loads(bear_raw) or [])
        except Exception:
            continue
        # Events carry a mix of tickers and company names; keep only things that
        # look like tickers, and normalise. A company name would never match a
        # candle symbol anyway.
        tickers = {str(n).replace(".NS", "").replace(".BO", "").upper()
                   for n in names if n and " " not in str(n)}
        for sec in secs:
            if not sec:
                continue
            by_sector.setdefault(sec, set()).update(tickers)
            if bare in tickers:
                sectors_for_symbol.add(sec)

    if not sectors_for_symbol:
        return None, []
    # If the symbol sits in several sectors, use the one with the most members —
    # the broadest read is the most reliable breadth measurement.
    sector = max(sectors_for_symbol, key=lambda s: len(by_sector.get(s, ())))
    peers = sorted(by_sector.get(sector, set()) - {bare})
    return sector, peers


async def sector_breadth_veto(symbol: str, side: str,
                              session: AsyncSession) -> tuple[bool, str | None]:
    """(veto, reason). True means: do not take this trade.

    Only vetoes LONGS. A short into a falling sector is aligned with breadth,
    not against it.

    FAILS OPEN on every uncertainty — too few peers, missing candles, any error.
    A veto that fires on thin data would block ordinary trades for no reason,
    and this is a filter on top of the existing gates, not one of them.
    """
    if not bool(_cfg("TACTICAL_SECTOR_BREADTH_VETO", True)):
        return False, None
    if str(side).upper() != "BUY":
        return False, None

    try:
        sector, peers = await sector_peers(symbol, session)
        min_peers = int(_cfg("TACTICAL_SECTOR_VETO_MIN_PEERS", 5))
        if not sector or len(peers) < min_peers:
            return False, None

        rows = (await session.execute(text("""
            WITH d AS (
              SELECT symbol,
                     (array_agg(open  ORDER BY timestamp))[1]      AS o,
                     (array_agg(close ORDER BY timestamp DESC))[1] AS c
              FROM candles
              WHERE timeframe = '1m' AND timestamp::date = CURRENT_DATE
                AND replace(replace(symbol,'.NS',''),'.BO','') = ANY(:peers)
              GROUP BY symbol)
            SELECT symbol, (c - o) / NULLIF(o,0) * 100 FROM d WHERE o > 0
        """), {"peers": peers})).fetchall()

        moves = [float(r[1]) for r in rows if r[1] is not None]
        if len(moves) < min_peers:
            return False, None

        down = sum(1 for m in moves if m < 0)
        frac_down = down / len(moves)
        threshold = float(_cfg("TACTICAL_SECTOR_VETO_DOWN_FRAC", 0.70))

        if frac_down >= threshold:
            avg = sum(moves) / len(moves)
            return True, (f"{sector} sector breadth {down}/{len(moves)} down "
                          f"(avg {avg:+.2f}%) — refusing a long into a falling sector")
        return False, None
    except Exception as exc:
        logger.debug(f"[sector_veto] {symbol}: check failed ({exc}) — allowing")
        return False, None
