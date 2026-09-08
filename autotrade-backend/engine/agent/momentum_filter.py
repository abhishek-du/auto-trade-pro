"""Monthly Momentum Rotation Filter.

Ranks stocks in the scan universe by 63-day price momentum (holding-period return)
and keeps only the top-N% as eligible for new entry signals. Updated at most once
per day (cached), so the per-symbol overhead is a single dict lookup.

Rationale (from QSTrader Momentum Top-N research):
  - Stocks in the top half of 63-day momentum outperform across all tested regimes.
  - Low-momentum stocks in choppy/bear markets are the primary source of 2024-26 losses.
  - This filter is the simplest lever to push PULLBACK_LONG PF from 1.27 → above 1.3.
"""
from __future__ import annotations

import asyncio
import time
from typing import Set

from utils.logger import logger

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache_ts:   float           = 0.0     # unix timestamp of last refresh
_eligible:   Set[str]        = set()   # bare symbols (no .NS) that passed momentum gate
_CACHE_TTL   = 6 * 3600               # refresh every 6 hours (covers market day)
_LOCK        = asyncio.Lock()
_MOM_LOOKBACK = 63                    # ~3 months of trading days


def _bare(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".BO", "").upper()


def is_eligible(symbol: str) -> bool:
    """Return True if the symbol passed the most recent momentum rank filter.

    Returns True (fail-open) when the cache is empty — first cycle before the
    async refresh runs, or when candle data is unavailable for the universe.
    """
    if not _eligible:
        return True
    return _bare(symbol) in _eligible


async def refresh_if_needed(universe: list[str], session, top_pct: float = 0.50) -> None:
    """Recompute momentum rankings for the universe if cache is stale.

    Called once per agent cycle from _build_scan_universe. Only one coroutine
    runs the refresh at a time (async lock). Others return immediately.
    """
    global _cache_ts, _eligible

    now = time.monotonic()
    if now - _cache_ts < _CACHE_TTL and _eligible:
        return

    async with _LOCK:
        # Re-check inside the lock (another coroutine may have refreshed already)
        if time.monotonic() - _cache_ts < _CACHE_TTL and _eligible:
            return

        try:
            await _compute_ranks(universe, session, top_pct)
            _cache_ts = time.monotonic()
        except Exception as exc:
            logger.warning(f"[momentum_filter] refresh failed — fail-open: {exc}")


async def _compute_ranks(universe: list[str], session, top_pct: float) -> None:
    """Fetch 63-day close prices from DB and rank symbols by holding-period return."""
    global _eligible

    from sqlalchemy import text

    bare_list = list({_bare(s) for s in universe})
    if not bare_list:
        return

    # SESSIONS, not rows (Step 2K, H-1).
    #
    # This used one `SELECT ... WHERE timeframe='1d' ORDER BY timestamp DESC`
    # and then took the newest 64 ROWS per symbol. While 18:30 coexists with
    # canonical that is not 64 sessions — measured over a 90-day window, 110,246
    # (symbol, session) pairs across 3,520 symbols carry two conventions, so the
    # "63-day" return was computed over roughly half that span, and inconsistently
    # between symbols depending on how much legacy each carries.
    #
    # session_closes_bulk keeps this a SINGLE query — a per-symbol round trip
    # over the whole universe would be hundreds of queries — while resolving
    # sessions with the same resolver daily_series uses. Completed sessions only.
    from engine.daily_series import session_closes_bulk

    ns_list = [f"{b}.NS" for b in bare_list]
    series  = await session_closes_bulk(ns_list, session, sessions=_MOM_LOOKBACK + 1)

    sym_closes: dict[str, list[float]] = {}
    for ns_sym, rows in series.items():
        # newest-first, matching the indexing the ranking below already uses
        sym_closes[_bare(ns_sym)] = [float(c) for _d, c, _cv in reversed(rows)]

    if not sym_closes:
        logger.warning("[momentum_filter] no candle rows — fail-open")
        return

    # Compute 63-day HPR for each symbol that has enough history
    returns: dict[str, float] = {}
    for sym, closes in sym_closes.items():
        if len(closes) >= _MOM_LOOKBACK:
            # closes[0] = latest, closes[63] = 63 bars ago
            latest = closes[0]
            past   = closes[_MOM_LOOKBACK - 1]
            if past > 0:
                returns[sym] = (latest - past) / past

    if not returns:
        logger.warning("[momentum_filter] no symbols with sufficient history — fail-open")
        return

    # Rank and take top-N%
    sorted_syms = sorted(returns, key=lambda s: returns[s], reverse=True)
    cutoff      = max(1, int(len(sorted_syms) * top_pct))
    new_eligible = set(sorted_syms[:cutoff])

    old_count = len(_eligible)
    _eligible = new_eligible
    logger.info(
        f"[momentum_filter] refreshed: {len(returns)} ranked, "
        f"top-{top_pct*100:.0f}% → {len(_eligible)} eligible "
        f"(was {old_count})"
    )
