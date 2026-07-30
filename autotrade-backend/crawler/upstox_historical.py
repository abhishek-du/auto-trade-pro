import httpx
from datetime import date, timedelta
from crawler.upstox_auth import ensure_upstox_token_fresh
from crawler.upstox_data import get_instrument_key, _headers, _V2
from utils.logger import logger

async def get_historical_candles(
    symbol: str,
    interval: str = "day",
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Fetch OHLCV candles from Upstox.

    interval: 1minute | 5minute | 30minute | day | week | month
    """
    if not from_date:
        from_date = (date.today() - timedelta(days=30 if "minute" in interval else 365)).isoformat()
    if not to_date:
        to_date = date.today().isoformat()

    if not await ensure_upstox_token_fresh():
        return []
    
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return []
        
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_V2}/historical-candle/{ikey}/{interval}/{to_date}/{from_date}",
                headers=_headers(),
            )
            if r.status_code == 200:
                candles = r.json().get("data", {}).get("candles", [])
                out = [
                    {
                        "timestamp": c[0], "open": c[1], "high": c[2],
                        "low": c[3], "close": c[4], "volume": c[5],
                    }
                    for c in candles
                ]
                return out
    except Exception as e:
        logger.error(f"[upstox/historical] Failed for {symbol}: {e}")
    return []

async def get_intraday_candles(symbol: str, interval: str = "1minute") -> list[dict]:
    """Fetch current trading day's intraday OHLCV candles from Upstox.
    interval: 1minute | 5minute | 30minute
    """
    if not await ensure_upstox_token_fresh():
        return []
    
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return []
        
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_V2}/historical-candle/intraday/{ikey}/{interval}",
                headers=_headers(),
            )
            if r.status_code == 200:
                candles = r.json().get("data", {}).get("candles", [])
                out = [
                    {
                        "timestamp": c[0], "open": c[1], "high": c[2],
                        "low": c[3], "close": c[4], "volume": c[5],
                    }
                    for c in candles
                ]
                return out
    except Exception as e:
        logger.error(f"[upstox/intraday] Failed for {symbol}: {e}")
    return []
