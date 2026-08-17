"""Analyst-consensus provider for the expectation engine (P2-1).

Why this exists
---------------
`expectation.py` resolves its anchor CONSENSUS -> GUIDANCE -> HISTORICAL
BASELINE. Until 2026-08-17 the first two were unimplemented stubs returning
None, so every live trade silently fell through to the 3-year CAGR baseline —
which the code itself marks `is_market_expectation = False`. The strategy is
named "Pre-Event **Expectation** Gap" and its whole premise is trading the gap
between what we infer and what the MARKET expects, so with no market
expectation the premise was unevaluable. See
docs/2026-08-17_FORENSIC_POST_MORTEM.md §3.

What it can and cannot do
-------------------------
Coverage of Indian small/mid-caps is genuinely poor. Measured 2026-08-17 over
the 18 symbols from the forensic window:

    any analyst coverage      6/18  (33%)
    >= MIN_ANALYSTS coverage  3/18  (17%)
    the 11-name loss cluster  0/11  (GENESYS, MCLOUD, CPPLUS, LLOYDSENT,
                                     GODREJIND, SHREEJISPG, EPACKPEB,
                                     KRISHNADEF, CONFIPET, VIDYAWIRES, JNKINDIA)

So this does NOT retroactively fix the trades that prompted the investigation —
those names have no consensus to compare against at any price. It is paired
with REQUIRE_MARKET_EXPECTATION in decision.py, which restricts the strategy to
the universe where its premise is actually measurable, rather than letting it
keep trading a proxy and calling it an expectation gap.

Point-in-time semantics
-----------------------
The provider returns `known_at` = the moment WE observed the estimate, which is
the only defensible timestamp available (yfinance exposes no revision date on
`earnings_estimate`; `eps_trend` shows 7/30/60/90-day-ago values but not when
each was published). For LIVE prediction as_of == now, so `known_at <= as_of`
holds and the estimate is usable. For BACKTESTS as_of is in the past, so
expectation.py's existing point-in-time gate correctly REJECTS it rather than
leaking a look-ahead — that fail-closed behaviour is intentional and must not
be "fixed" by back-dating known_at.

Rate limiting
-------------
yfinance is aggressively rate-limited (see crawler/india_price_feed.py's 429
breaker). Consensus moves slowly — daily granularity is ample — so results are
cached in Redis for CACHE_TTL and negative results are cached too, otherwise
the ~70% of the universe with no coverage would re-hit the API on every scan.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from utils.config import settings
from utils.logger import logger

# A single analyst is not a "consensus". Below this the estimate is treated as
# unavailable rather than as a weak market expectation — the whole point of the
# anchor is that it represents what the MARKET expects.
MIN_ANALYSTS: int = int(getattr(settings, "CONSENSUS_MIN_ANALYSTS", 3))

# Consensus revisions are slow; a day-old figure is fine and keeps us far away
# from yfinance's rate limiter.
CACHE_TTL: int = int(getattr(settings, "CONSENSUS_CACHE_TTL", 86_400))
_NEG_CACHE_TTL: int = 6 * 3600      # shorter, so new coverage is picked up

_CACHE_PREFIX = "pre_event_consensus:"


def _to_yf_symbol(symbol: str) -> str:
    """Engine symbols already carry the .NS/.BO suffix yfinance expects."""
    s = symbol.upper()
    if s.endswith((".NS", ".BO")):
        return s
    return f"{s}.NS"


def _fetch_sync(symbol: str, want_annual: bool) -> dict | None:
    """Blocking yfinance read. Returns {'growth','n_analysts','period'} or None.

    `earnings_estimate` is indexed by period: '0q' (quarter being reported),
    '+1q', '0y' (current fiscal year), '+1y'. We pick the row matching the
    nowcast's own dimension so the gap is computed like-for-like — comparing a
    quarterly trend against an annual consensus would be meaningless.
    """
    import yfinance as yf

    period = "0y" if want_annual else "0q"
    try:
        est = yf.Ticker(_to_yf_symbol(symbol)).earnings_estimate
    except Exception as exc:
        logger.debug(f"[consensus] {symbol}: earnings_estimate fetch failed: {exc}")
        return None

    if est is None or getattr(est, "empty", True) or period not in est.index:
        return None

    try:
        growth = est.loc[period, "growth"]
        n = est.loc[period, "numberOfAnalysts"]
    except Exception:
        return None

    # yfinance yields NaN (not None) for absent values — NaN != NaN.
    if growth is None or growth != growth:
        return None
    if n is None or n != n:
        return None

    return {"growth": float(growth), "n_analysts": int(n), "period": period}


async def fetch_consensus_growth(symbol: str, want_annual: bool) -> tuple[float, datetime] | None:
    """Consensus expected PAT growth as a FRACTION (0.18 = +18%), with the
    timestamp at which we observed it — or None when unavailable/too thin.

    Shape matches what expectation.py::_fetch_consensus must return.
    """
    cache_key = f"{_CACHE_PREFIX}{symbol}:{'y' if want_annual else 'q'}"

    # ── cache read (best-effort; Redis being down must not block trading) ────
    try:
        from utils.cache import get_redis
        cached = await get_redis().get(cache_key)
        if cached is not None:
            payload = json.loads(cached)
            if payload.get("miss"):
                return None
            return float(payload["growth"]), datetime.fromisoformat(payload["known_at"])
    except Exception as exc:
        logger.debug(f"[consensus] cache read failed for {symbol}: {exc}")

    data = await asyncio.to_thread(_fetch_sync, symbol, want_annual)

    usable = bool(data) and data["n_analysts"] >= MIN_ANALYSTS
    if data and not usable:
        logger.debug(
            f"[consensus] {symbol}: only {data['n_analysts']} analyst(s) "
            f"(< {MIN_ANALYSTS}) — not a market consensus, treating as unavailable"
        )

    known_at = datetime.utcnow()
    try:
        from utils.cache import get_redis
        if usable:
            await get_redis().set(
                cache_key,
                json.dumps({"growth": data["growth"], "known_at": known_at.isoformat()}),
                ex=CACHE_TTL,
            )
        else:
            # Negative caching matters: ~70% of this universe has no coverage,
            # and without it every scan would re-hit a rate-limited API for
            # every uncovered symbol.
            await get_redis().set(cache_key, json.dumps({"miss": True}), ex=_NEG_CACHE_TTL)
    except Exception as exc:
        logger.debug(f"[consensus] cache write failed for {symbol}: {exc}")

    if not usable:
        return None

    logger.info(
        f"[consensus] {symbol}: {data['growth']:+.2%} expected PAT growth "
        f"({data['period']}, {data['n_analysts']} analysts)"
    )
    return data["growth"], known_at
