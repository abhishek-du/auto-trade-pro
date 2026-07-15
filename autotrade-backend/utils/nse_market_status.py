import asyncio
import httpx
from loguru import logger
from datetime import datetime
from crawler.fii_dii_crawler import BROWSER_HEADERS

_holiday_cache = {}
_last_fetch_time = None
_CACHE_DURATION_SECONDS = 86400  # Cache for 24 hours

async def fetch_nse_holidays_dynamic() -> dict[str, str]:
    """Fetch holidays dynamically from NSE /api/holiday-master?type=trading"""
    global _holiday_cache, _last_fetch_time
    
    now = datetime.now()
    if _holiday_cache and _last_fetch_time:
        if (now - _last_fetch_time).total_seconds() < _CACHE_DURATION_SECONDS:
            return _holiday_cache

    url = "https://www.nseindia.com/api/holiday-master?type=trading"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            await client.get("https://www.nseindia.com", headers=BROWSER_HEADERS)
            await asyncio.sleep(1.0)
            
            r = await client.get(url, headers={
                **BROWSER_HEADERS,
                "Referer": "https://www.nseindia.com/resources/exchange-communication-holidays",
            })
            if r.status_code == 200:
                data = r.json()
                # Parse CM (Capital Market) holidays
                holidays_map = {}
                for item in data.get("CM", []):
                    # tradingDate is usually like "01-May-2026" or "15-Aug-2026"
                    try:
                        dt = datetime.strptime(item["tradingDate"], "%d-%b-%Y")
                        holidays_map[dt.strftime("%Y-%m-%d")] = item.get("description", "Holiday")
                    except Exception as e:
                        logger.warning(f"Failed to parse holiday date {item.get('tradingDate')}: {e}")
                
                _holiday_cache = holidays_map
                _last_fetch_time = now
                logger.info(f"[nse_holidays] Successfully fetched {len(_holiday_cache)} dynamic holidays from NSE API.")
                return _holiday_cache
            else:
                logger.error(f"[nse_holidays] API returned {r.status_code}. Using stale cache if available.")
    except Exception as exc:
        logger.error(f"[nse_holidays] Fetch failed: {exc}")

    return _holiday_cache

def fetch_nse_holidays_sync() -> dict[str, str]:
    """Synchronous version for module load times."""
    import requests
    
    url = "https://www.nseindia.com/api/holiday-master?type=trading"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10.0)
        
        r = session.get(url, headers={
            **headers,
            "Referer": "https://www.nseindia.com/resources/exchange-communication-holidays",
        }, timeout=10.0)
        
        if r.status_code == 200:
            data = r.json()
            holidays_map = {}
            for item in data.get("CM", []):
                try:
                    dt = datetime.strptime(item["tradingDate"], "%d-%b-%Y")
                    holidays_map[dt.strftime("%Y-%m-%d")] = item.get("description", "Holiday")
                except Exception:
                    pass
            return holidays_map
    except Exception as exc:
        logger.error(f"[nse_holidays_sync] Fetch failed: {exc}")
        
    return {}

async def check_market_status_dynamic() -> dict:
    """Check live market status from NSE /api/marketStatus"""
    url = "https://www.nseindia.com/api/marketStatus"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            await client.get("https://www.nseindia.com", headers=BROWSER_HEADERS)
            await asyncio.sleep(0.5)
            r = await client.get(url, headers=BROWSER_HEADERS)
            if r.status_code == 200:
                data = r.json()
                for market in data.get("marketState", []):
                    if market.get("market") == "Capital Market":
                        return {
                            "status": market.get("marketStatus", ""),
                            "tradeDate": market.get("tradeDate", "")
                        }
    except Exception as exc:
        logger.error(f"[nse_market_status] Fetch failed: {exc}")
    
    return {}
