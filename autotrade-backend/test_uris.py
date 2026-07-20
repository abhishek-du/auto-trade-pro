import httpx
import asyncio

async def test_uris():
    uris_to_test = [
        "https://localhost:8000/api/v1/upstox/callback",
        "http://localhost:8000/api/v1/upstox/callback",
        "https://127.0.0.1:8000/api/v1/upstox/callback",
        "http://127.0.0.1:8000/api/v1/upstox/callback",
        "https://localhost:8000/api/v1/upstox/callback/",
        "http://localhost:8000/",
        "https://localhost:8000/",
        "https://127.0.0.1:8000/",
        "http://127.0.0.1:8000/",
        "http://localhost:8000/callback",
    ]
    
    client_id = "d6e53090-ad32-4eef-b86d-187c7834340a"
    
    async with httpx.AsyncClient() as c:
        for uri in uris_to_test:
            # We just do the dialog step to see if it redirects or gives 401/error
            url = f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={client_id}&redirect_uri={uri}"
            r = await c.get(url, follow_redirects=False)
            
            print(f"Testing {uri} -> Status: {r.status_code}")
            if r.status_code == 302:
                print("FOUND MATCH!")
                print(r.headers.get("Location"))
                break

if __name__ == "__main__":
    asyncio.run(test_uris())
