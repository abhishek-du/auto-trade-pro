import asyncio
from httpx import AsyncClient

async def main():
    async with AsyncClient(timeout=60.0) as client:
        req = {
            "portfolio_id": "5ebbd324-4e0a-40f6-bd96-41678d99e3ac",
            "risk_profile": "moderate",
            "annual_income": 1000000
        }
        res = await client.post("http://127.0.0.1:8000/api/v1/doctor/diagnose", json=req)
        print(res.status_code)
        print(res.text)

asyncio.run(main())
