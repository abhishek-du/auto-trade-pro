import asyncio
from db.database import AsyncSessionLocal
from crawler.india_price_feed import run_india_price_crawl
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    async with AsyncSessionLocal() as session:
        result = await run_india_price_crawl(session, ignore_market_hours=True)
        await session.commit()
        print(f"Update Result: {result}")

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(main())
