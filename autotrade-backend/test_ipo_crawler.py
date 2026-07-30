import asyncio
import logging
from crawler.ipo_crawler import refresh_ipo_cache, get_ipo_cache, fetch_single_ipo

logging.basicConfig(level=logging.INFO)

async def main():
    await refresh_ipo_cache()
    data = await get_ipo_cache()
    print("IPOs length:", len(data))
    if data:
        print("First IPO:", data[0])
        print("Single fetch for first:", await fetch_single_ipo(data[0]["id"]))

if __name__ == "__main__":
    asyncio.run(main())
