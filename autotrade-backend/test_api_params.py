import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv(".env")
token = os.environ.get("UPSTOX_ACCESS_TOKEN")

async def test():
    async with httpx.AsyncClient() as c:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        # Test with instrument_key
        url = "https://api.upstox.com/v2/fundamentals/company-profile"
        params = {"instrument_key": "NSE_EQ|INE002A01018"}
        r = await c.get(url, headers=headers, params=params)
        print("Testing instrument_key:")
        print(r.status_code, r.text)

        # Test with symbol?
        params = {"symbol": "RELIANCE"}
        r = await c.get(url, headers=headers, params=params)
        print("Testing symbol:")
        print(r.status_code, r.text)

        # Test options chain just to see
        url2 = "https://api.upstox.com/v2/option/chain"
        params2 = {"instrument_key": "NSE_EQ|INE002A01018"}
        r2 = await c.get(url2, headers=headers, params=params2)
        print("Testing option chain:")
        print(r2.status_code, str(r2.text)[:200])

asyncio.run(test())
