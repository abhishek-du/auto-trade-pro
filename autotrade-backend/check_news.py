import asyncio
from db.database import AsyncSessionLocal
from db.models import NewsItem
from sqlalchemy import select, desc

async def check_news():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(NewsItem).order_by(desc(NewsItem.id)).limit(50))
        for item in res.scalars().all():
            if "birla" in item.headline.lower() or "shell" in item.headline.lower() or "sprng" in item.headline.lower():
                print(f"FOUND: {item.headline} | {item.source}")
                return
        print("NOT FOUND IN DB")

if __name__ == "__main__":
    asyncio.run(check_news())
