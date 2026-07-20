import httpx
import asyncio

async def check_zerodha():
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get("http://127.0.0.1:8000/api/v1/zerodha/status")
            print(f"[{r.status_code}] {r.text}")
    except Exception as e:
        print(f"Error checking zerodha status: {e}")

asyncio.run(check_zerodha())
