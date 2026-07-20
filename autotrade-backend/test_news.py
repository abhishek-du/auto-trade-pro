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
        
        # Test get_news
        url = "https://api.upstox.com/v2/news/articles"
        params = {"instrument_key": "NSE_EQ|INE002A01018"}
        r = await c.get(url, headers=headers, params=params)
        print("Testing news:")
        print(r.status_code, r.text)

asyncio.run(test())
