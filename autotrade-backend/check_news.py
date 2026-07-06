import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Count total news
        total = (await db.execute(text("SELECT COUNT(*) FROM news_items"))).scalar()
        print(f"Total news items in DB: {total}")
        
        # Recent news in last 24h
        recent = (await db.execute(text("""
            SELECT title, source, sentiment_label, published_at, tickers_affected
            FROM news_items 
            WHERE published_at >= NOW() - INTERVAL '24 hours'
            ORDER BY published_at DESC 
            LIMIT 10
        """))).fetchall()
        
        print(f"\nRecent news (last 24h): {len(recent)} items")
        for r in recent:
            print(f"  [{r.source}] {r.title[:70]}... | Sentiment: {r.sentiment_label} | {r.published_at}")

asyncio.run(main())
