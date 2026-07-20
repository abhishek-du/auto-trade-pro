import asyncio
from sqlalchemy import select, or_
from db.database import AsyncSessionLocal
from db.models import NewsItem

async def check_news():
    async with AsyncSessionLocal() as session:
        query = select(NewsItem).where(
            or_(
                NewsItem.headline.ilike("%aditya birla%"),
                NewsItem.headline.ilike("%sprng%"),
                NewsItem.headline.ilike("%shell%"),
            )
        ).order_by(NewsItem.published_at.desc()).limit(10)
        
        res = await session.execute(query)
        items = res.scalars().all()
        
        if items:
            for item in items:
                print(f"[{item.published_at}] {item.headline}")
                print(f"Tickers: {item.tickers_affected}")
                print("---")
        else:
            print("No matching news found.")

if __name__ == "__main__":
    asyncio.run(check_news())
