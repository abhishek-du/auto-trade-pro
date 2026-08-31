"""Upstox instrument master — the replacement for Kite's /instruments download.

WHY THIS EXISTS
---------------
Kite Connect's token expired on 2026-08-31 and Upstox is now the sole broker
backend. Kite addressed instruments by a numeric `instrument_token`; Upstox uses
a STRING `instrument_key` shaped "NSE_EQ|INE002A01018". Nothing in this codebase
can call Upstox without that key, so populating it is the first migration step.

THE ENVIRONMENT CONSTRAINT THAT SHAPED THIS MODULE
--------------------------------------------------
Upstox publishes a bulk instrument dump at
assets.upstox.com/market-quote/instruments/exchange/*.json.gz, which would be
the obvious source. It is NOT reachable from this host:

    api.upstox.com     -> HTTP 200
    assets.upstox.com  -> SSL: CERTIFICATE_VERIFY_FAILED (self-signed
                          certificate in certificate chain)

That is TLS interception on the asset host, not an Upstox outage, and it is an
infrastructure condition this module cannot fix. So the master is built from
`GET /v2/instruments/search` instead — the same authoritative data, one symbol
at a time. Measured 0.09 s per call, so ~10k symbols is ~15 minutes sequential
and a few minutes with modest concurrency. That is acceptable for a job that
runs once a day, before the open.

If assets.upstox.com ever becomes reachable, `load_from_bulk_dump()` below is
the faster path and should be preferred; it is written and tested but currently
raises on the TLS error, and the caller falls back to search.

WHAT IT WRITES
--------------
It ENRICHES the existing `kite_instruments` rows in place rather than creating a
parallel table. The table name is now a misnomer, kept deliberately: ~50 call
sites, the hub-universe rebuild and the candle pipeline all query it by name,
and renaming it would be a large, risky change for zero functional gain. Two
additive columns carry the Upstox identity:

    instrument_key   "NSE_EQ|INE002A01018"
    isin             "INE002A01018"

NSE ONLY. Step 2A established that no BSE instrument may enter an active path;
this loader never requests or stores a BSE key.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy import text

from utils.config import settings
from utils.logger import logger

_API = "https://api.upstox.com/v2"
_BULK = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# Upstox segment for cash-market NSE equities. Every instrument_key this module
# writes carries this prefix; anything else is out of scope (Step 2A).
_NSE_EQ = "NSE_EQ"

# Concurrency for the search fallback. Upstox's standard-API limit is 50/sec,
# 500/min. Eight in flight against a ~0.09 s call is roughly 90/sec at the
# socket but well under the per-minute cap once DB writes are included; the
# semaphore exists to stay polite rather than to hit the ceiling.
_SEARCH_CONCURRENCY = 6


@dataclass(frozen=True)
class UpstoxInstrument:
    instrument_key: str
    tradingsymbol: str
    name: str
    isin: str
    segment: str
    exchange: str
    instrument_type: str
    lot_size: int
    tick_size: float


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}",
        "Accept": "application/json",
    }


def instrument_key_for(isin: str, segment: str = _NSE_EQ) -> str | None:
    """Deterministic key construction. Returns None rather than a wrong key."""
    isin = (isin or "").strip().upper()
    if not isin or len(isin) != 12 or not isin.startswith("INE") and not isin.startswith("IN"):
        return None
    return f"{segment}|{isin}"


async def search_instrument(client: httpx.AsyncClient, query: str,
                            *, attempts: int = 3) -> tuple[UpstoxInstrument | None, str]:
    """Resolve ONE tradingsymbol to its NSE_EQ instrument.

    Returns (instrument, outcome) where outcome is one of:
        "ok"          resolved
        "not_listed"  the API answered and no NSE_EQ row matched exactly
        "error"       transport/HTTP/rate-limit failure -- UNKNOWN, retry later

    The three-way return matters. An earlier version returned None for both
    "not listed" and "rate limited", so a burst of 429s looked exactly like a
    batch of delisted symbols: 10 of 60 real NSE stocks (AJANTPHARM, AIAENG,
    AJMERA...) were recorded as unresolvable when the search endpoint in fact
    returns them correctly. An incremental sync would then retry them forever
    without ever reporting why.

    Exact tradingsymbol match only. The endpoint is a fuzzy company search --
    "RELIANCE" also returns RPOWER (Reliance Power) -- so accepting anything
    looser would silently bind the wrong instrument. That is the identity
    failure class this project already paid for; see utils/identity.py.
    """
    from crawler.upstox_limiter import acquire

    backoff = 0.5
    for attempt in range(attempts):
        try:
            # Pace against Upstox's 500/min standard-API budget. Without this
            # a 3,040-symbol pass finishes faster than the per-MINUTE cap
            # allows and 518 of them come back rate-limited -- measured on the
            # first migration run.
            await acquire()
            r = await client.get(f"{_API}/instruments/search",
                                 params={"query": query}, headers=_headers(), timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                # Retryable. 429 is the documented rate-limit response.
                if attempt < attempts - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                return None, "error"
            if r.status_code != 200:
                return None, "error"
            for row in (r.json().get("data") or []):
                if (row.get("segment") == _NSE_EQ
                        and (row.get("trading_symbol") or "").upper() == query.upper()):
                    return UpstoxInstrument(
                        instrument_key=row.get("instrument_key") or "",
                        tradingsymbol=row.get("trading_symbol") or "",
                        name=row.get("name") or "",
                        isin=row.get("isin") or "",
                        segment=row.get("segment") or "",
                        exchange=row.get("exchange") or "",
                        instrument_type=row.get("instrument_type") or "",
                        lot_size=int(row.get("lot_size") or 1),
                        tick_size=float(row.get("tick_size") or 0.05),
                    ), "ok"
            return None, "not_listed"
        except Exception as exc:
            if attempt < attempts - 1:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            logger.debug(f"[upstox_instruments] search failed for {query}: {type(exc).__name__}")
            return None, "error"
    return None, "error"


async def load_from_bulk_dump() -> list[UpstoxInstrument]:
    """The fast path. Raises on this host -- see the module docstring."""
    import gzip
    import json

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(_BULK)
        r.raise_for_status()
        rows = json.loads(gzip.decompress(r.content))
    out = []
    for row in rows:
        if row.get("segment") != _NSE_EQ:
            continue
        out.append(UpstoxInstrument(
            instrument_key=row.get("instrument_key", ""),
            tradingsymbol=row.get("trading_symbol", ""),
            name=row.get("name", ""),
            isin=row.get("isin", ""),
            segment=row.get("segment", ""),
            exchange=row.get("exchange", ""),
            instrument_type=row.get("instrument_type", ""),
            lot_size=int(row.get("lot_size") or 1),
            tick_size=float(row.get("tick_size") or 0.05),
        ))
    return out


async def sync_upstox_instrument_keys(session, *, limit: int | None = None,
                                      only_missing: bool = True) -> dict:
    """Populate instrument_key/isin on NSE rows in kite_instruments.

    `only_missing=True` makes the job incremental and therefore restartable:
    an interrupted run resumes where it stopped instead of re-resolving
    everything. A full refresh passes only_missing=False.
    """
    # PLAIN-EQ SERIES ONLY, and hub members first.
    #
    # Step 2's audit found that only 3,002 of the 10,134 "NSE EQ" rows are
    # actual equities: 4,301 are State Development Loans, 1,300 NCDs, 132 govt
    # securities, 84 treasury bills, 45 sovereign gold bonds. Kite encodes the
    # series in the tradingsymbol suffix ("656KA30-SG"), and none of those
    # resolve to an NSE_EQ instrument_key -- an alphabetical pass spends its
    # entire budget on "0ABCL31-N0" and friends before reaching a real stock.
    #
    # The ordering puts hub_universe members first so that even a truncated or
    # interrupted run has covered everything the scanner actually trades.
    where = ("exchange = 'NSE' AND instrument_type = 'EQ' "
             "AND tradingsymbol !~ '-[A-Z0-9]{2}$' "     # drop -SG/-N0/-GS/-SM/-BE/...
             "AND tradingsymbol !~ '^[0-9]'")            # drop numeric-coded debt
    if only_missing:
        where += " AND (instrument_key IS NULL OR instrument_key = '')"
    sql = f"""SELECT k.tradingsymbol FROM kite_instruments k
              WHERE {where}
              ORDER BY (SELECT 1 FROM hub_universe h
                        WHERE h.symbol = k.tradingsymbol || '.NS') NULLS LAST,
                       k.tradingsymbol"""
    if limit:
        sql += f" LIMIT {int(limit)}"

    symbols = [r.tradingsymbol for r in (await session.execute(text(sql))).all()]
    if not symbols:
        return {"requested": 0, "resolved": 0, "unresolved": 0, "source": "none"}

    logger.info(f"[upstox_instruments] resolving {len(symbols):,} NSE symbols via search")

    sem = asyncio.Semaphore(_SEARCH_CONCURRENCY)
    resolved: list[UpstoxInstrument] = []
    not_listed: list[str] = []
    errored: list[str] = []

    async with httpx.AsyncClient(timeout=25) as client:
        async def one(sym: str):
            async with sem:
                inst, outcome = await search_instrument(client, sym)
                if outcome == "ok":
                    resolved.append(inst)
                elif outcome == "not_listed":
                    not_listed.append(sym)
                else:
                    errored.append(sym)

        # Chunked so a very large universe cannot build one enormous task list.
        for i in range(0, len(symbols), 500):
            await asyncio.gather(*(one(s) for s in symbols[i:i + 500]))
            logger.info(f"[upstox_instruments] {min(i + 500, len(symbols)):,}/{len(symbols):,} "
                        f"resolved={len(resolved):,} not_listed={len(not_listed):,} "
                        f"errored={len(errored):,}")

    # Write in one transaction per chunk so a mid-run failure keeps progress.
    written = 0
    for i in range(0, len(resolved), 500):
        for inst in resolved[i:i + 500]:
            await session.execute(text("""
                UPDATE kite_instruments
                   SET instrument_key = :k, isin = :i
                 WHERE exchange = 'NSE' AND tradingsymbol = :s
            """), {"k": inst.instrument_key, "i": inst.isin, "s": inst.tradingsymbol})
            written += 1
        await session.commit()

    summary = {"requested": len(symbols), "resolved": written,
               "not_listed": len(not_listed), "errored": len(errored),
               "source": "search",
               "not_listed_sample": not_listed[:10],
               "errored_sample": errored[:10]}
    if errored:
        # Errors are UNKNOWN, not absent. Saying so keeps the next run honest
        # about whether the universe is actually covered.
        logger.warning(
            f"[upstox_instruments] {len(errored)} symbol(s) failed with transport/"
            f"rate-limit errors and remain UNRESOLVED — rerun to cover them")
    logger.info(f"[upstox_instruments] {summary}")
    return summary


async def get_instrument_key(session, symbol: str) -> str | None:
    """symbol ('RELIANCE.NS' or 'RELIANCE') -> 'NSE_EQ|INE...'. NSE only.

    Reads the stored column; it does NOT hit the network. A missing key means
    the daily sync has not covered that symbol yet, and the caller should treat
    it as unavailable rather than guessing.
    """
    from utils.symbols import strip_suffix

    bare = strip_suffix(symbol)
    if not bare:
        return None
    row = (await session.execute(text(
        "SELECT instrument_key FROM kite_instruments "
        "WHERE exchange = 'NSE' AND tradingsymbol = :s AND instrument_key IS NOT NULL LIMIT 1"),
        {"s": bare})).first()
    return row[0] if row else None


async def build_key_maps(session) -> tuple[dict, dict]:
    """(symbol->instrument_key, instrument_key->symbol) for the whole NSE book.

    Both directions are needed: outbound requests map symbol->key, and the
    WebSocket feed returns keys that must be mapped back to the '.NS' symbols
    the rest of the system uses.
    """
    rows = (await session.execute(text(
        "SELECT tradingsymbol, instrument_key FROM kite_instruments "
        "WHERE exchange = 'NSE' AND instrument_key IS NOT NULL AND instrument_key <> ''"
    ))).all()
    fwd = {f"{r.tradingsymbol}.NS": r.instrument_key for r in rows}
    rev = {r.instrument_key: f"{r.tradingsymbol}.NS" for r in rows}
    return fwd, rev
