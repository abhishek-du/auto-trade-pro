import asyncio
import httpx

async def main():
    async with httpx.AsyncClient() as c:
        try:
            res = await c.post("http://127.0.0.1:8000/api/v1/portfolios/sync-upstox")
            print("Status Code:", res.status_code)
            print("Response:", res.text)
        except Exception as e:
            print("Error:", e)

asyncio.run(main())
