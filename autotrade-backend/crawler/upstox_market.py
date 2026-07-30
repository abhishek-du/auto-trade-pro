import httpx
from crawler.upstox_auth import ensure_upstox_token_fresh
from crawler.upstox_data import get_instrument_key, _headers, _V2
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
