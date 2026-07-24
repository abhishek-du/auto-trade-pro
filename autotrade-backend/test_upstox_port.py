import asyncio
from crawler.upstox_data import _headers, ensure_upstox_token_fresh, _V2
import httpx

async def main():
    if not await ensure_upstox_token_fresh():
        print("No token")
        return
    async with httpx.AsyncClient() as c:
        rf = await c.get(f"{_V2}/user/get-funds-and-margin", headers=_headers(), params={"segment": "SEC"})
        print("Funds:", rf.status_code, rf.text[:200])
        rh = await c.get(f"{_V2}/portfolio/long-term-holdings", headers=_headers())
        print("Holdings:", rh.status_code, rh.text[:200])

asyncio.run(main())
