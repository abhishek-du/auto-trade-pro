import asyncio
import httpx
from crawler.upstox_auth import ensure_upstox_token_fresh
from crawler.upstox_data import get_instrument_key, prime_isin_cache, _headers, _V2
from utils.logger import logger

async def get_market_quote(symbol: str) -> dict:
    """Fetch live Market Quote including LTP, OHLC, and Depth for a symbol."""
    if not await ensure_upstox_token_fresh():
        return {}
    
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return {}
        
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_V2}/market-quote/quotes",
                headers=_headers(),
                params={"instrument_key": ikey},
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                for v in data.values():
                    # Return the first matching quote
                    return v
    except Exception as e:
        logger.error(f"[upstox/market_quote] Failed for {symbol}: {e}")
    return {}

# Symbols Upstox returned no instrument for. Process-local and never expired:
# an absent instrument is a property of the symbol, not a transient failure, and
# a stale entry only costs one missed quote tier (Kite and yfinance still run).
_UNRESOLVABLE: set[str] = set()


def _upstox_can_resolve(symbol: str) -> bool:
    """False for symbols Upstox structurally has no equity instrument for.

    yfinance-style index tickers ('^NSEI', '^BSESN'), futures ('GC=F') and FX
    pairs ('USDINR=X') are not NSE/BSE cash instruments, so get_instrument_key
    can only ever fail for them — but it fails EXPENSIVELY, doing an ISIN
    lookup per call. 15 of the 31 symbols on the API's live-price watchlist are
    exactly these, and they were retried on every refresh cycle forever.
    """
    s = symbol.upper()
    return not (s.startswith("^") or s.endswith("=F") or s.endswith("=X"))


async def get_market_quote_batch(symbols: list[str]) -> dict[str, dict]:
    """Fetch live quotes for multiple symbols at once."""
    if not symbols or not await ensure_upstox_token_fresh():
        return {}

    # Resolve instrument keys CONCURRENTLY, and only for symbols Upstox could
    # possibly have (2026-08-17). This loop used to `await get_instrument_key`
    # once per symbol, sequentially: measured on the live watchlist it took
    # 36.5s to resolve 0 of 31 symbols. Since it runs inside the API process's
    # 60s live-price loop, that single call degraded the whole event loop for a
    # large share of every cycle — /health (a static dict, no I/O) was taking
    # 1.5-3.8s because of it.
    candidates = [
        s for s in symbols
        if _upstox_can_resolve(s) and s not in _UNRESOLVABLE
    ]
    if not candidates:
        return {}

    # One query for every symbol's ISIN instead of one session per symbol —
    # see prime_isin_cache's docstring for the connection leak this removes.
    await prime_isin_cache(candidates)

    # Bounded concurrency. An unbounded gather here is NOT safe: this is called
    # from the API's live-price loop with every symbol in PRICE_CACHE, which
    # grows as pages are viewed (observed live at ~2,970). Firing thousands of
    # concurrent ISIN lookups saturated the event loop far worse than the
    # sequential version it replaced — the API stopped answering entirely.
    _sem = asyncio.Semaphore(8)

    async def _one(sym: str):
        async with _sem:
            try:
                return await asyncio.wait_for(get_instrument_key(sym), timeout=5)
            except Exception:
                return None

    resolved = await asyncio.gather(*(_one(s) for s in candidates))

    ikeys = []
    key_to_sym = {}
    for sym, ik in zip(candidates, resolved):
        if not ik:
            # Negative-cache: Upstox has no instrument for this symbol, and that
            # does not change between cycles. Without this every failing symbol
            # was re-looked-up on EVERY refresh, forever — the dominant cost of
            # the whole loop, and pure waste since the answer is always None.
            _UNRESOLVABLE.add(sym)
            continue
        ikeys.append(ik)
        key_to_sym[ik] = sym

    if not ikeys:
        return {}
        
    results = {}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            # Upstox recommends max 500 instrument keys per request
            for i in range(0, len(ikeys), 500):
                batch_keys = ikeys[i:i+500]
                r = await c.get(
                    f"{_V2}/market-quote/quotes",
                    headers=_headers(),
                    params={"instrument_key": ",".join(batch_keys)},
                )
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    for ik, quote in data.items():
                        sym = key_to_sym.get(ik)
                        if sym:
                            results[sym] = quote
    except Exception as e:
        logger.error(f"[upstox/market_quote_batch] Failed: {e}")
        
    return results

async def get_ltp(symbol: str) -> float | None:
    """Fetch live LTP for a symbol."""
    if not await ensure_upstox_token_fresh():
        return None
    
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return None
        
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{_V2}/market-quote/ltp",
                headers=_headers(),
                params={"instrument_key": ikey},
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                for v in data.values():
                    return v.get("last_price") or v.get("ltp")
    except Exception as e:
        logger.error(f"[upstox/ltp] Failed for {symbol}: {e}")
    return None
