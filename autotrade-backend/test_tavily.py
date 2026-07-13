import asyncio
from utils.config import settings

async def test_tavily():
    print(f"Settings Tavily Available: {settings.tavily_available}")
    if not settings.tavily_available:
        print("Tavily is disabled in settings.")
        return

    from engine.tavily_enricher import _client
    client = _client()
    if not client:
        print("Failed to initialize Tavily client.")
        return

    print("Tavily client initialized. Running test search...")
    try:
        res = client.search(query="TCS NSE India stock news today", search_depth="basic", max_results=3)
        print(f"Search successful! Found {len(res.get('results', []))} results.")
        for i, r in enumerate(res.get("results", [])):
            print(f"{i+1}. {r.get('title')}: {r.get('url')}")
    except Exception as e:
        print(f"Tavily search failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_tavily())
