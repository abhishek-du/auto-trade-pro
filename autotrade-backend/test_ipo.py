import asyncio
from crawler.ipo_crawler import refresh_ipo_cache, get_ipo_cache
async def run():
    await refresh_ipo_cache()
    data = await get_ipo_cache()
    print(f"Total IPOs in cache: {len(data)}")
    if data:
        print(f"First IPO: {data[0]}")
asyncio.run(run())
