import asyncio
from crawler.news_crawler import fetch_nse_corporate_announcements

async def test():
    results = await fetch_nse_corporate_announcements(limit=100)
    for res in results[:20]:  # print first 20
        print(f"[{res['published_at']}] {res['symbol']} - {res['category']}")
        print(f"Summary: {res['summary']}")
        print("---")

if __name__ == "__main__":
    asyncio.run(test())
