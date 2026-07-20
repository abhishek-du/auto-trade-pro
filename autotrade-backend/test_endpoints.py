import httpx
import asyncio

async def test_backend():
    base_url = "http://127.0.0.1:8000/api/v1"
    endpoints = [
        "/upstox/status",
        "/upstox/ltp/RELIANCE",
        "/upstox/overview/RELIANCE",
        "/upstox/historical/RELIANCE",
    ]
    
    async with httpx.AsyncClient() as client:
        for ep in endpoints:
            try:
                print(f"Testing {ep} ...")
                resp = await client.get(base_url + ep)
                print(f"[{resp.status_code}] {resp.text[:200]}")
                if resp.status_code != 200:
                    print(f"ERROR on {ep}: Status {resp.status_code}")
            except Exception as e:
                print(f"EXCEPTION on {ep}: {e}")

if __name__ == "__main__":
    asyncio.run(test_backend())
