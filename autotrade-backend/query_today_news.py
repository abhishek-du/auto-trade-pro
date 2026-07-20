import asyncio
from datetime import datetime, time
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import CausalEvent, NewsItem

async def query_today():
    today_start = datetime.combine(datetime.today(), time.min)
    async with AsyncSessionLocal() as session:
        # Get Causal Events
        stmt_causal = select(CausalEvent).where(CausalEvent.created_at >= today_start).order_by(CausalEvent.created_at.desc())
        res_causal = await session.execute(stmt_causal)
        events = res_causal.scalars().all()
        
        # Get News Items
        stmt_news = select(NewsItem).where(NewsItem.published_at >= today_start).order_by(NewsItem.published_at.desc())
        res_news = await session.execute(stmt_news)
        news = res_news.scalars().all()

        print(f"Total NewsItems fetched today: {len(news)}")
        print(f"Total CausalEvents processed today: {len(events)}\n")
        
        print("--- TOP LATEST CAUSAL EVENTS ---")
        for e in events[:15]:
            print(f"[{e.created_at.strftime('%H:%M:%S')}] Impact: {e.importance}/10 | {e.event_title} ({e.country})")
            
        print("\n--- TOP LATEST RAW NEWS ITEMS ---")
        for n in news[:15]:
            print(f"[{n.published_at.strftime('%H:%M:%S')}] {n.source}: {n.headline[:100]}...")

asyncio.run(query_today())
