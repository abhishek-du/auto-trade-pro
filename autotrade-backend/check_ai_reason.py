import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import PaperTrade
from datetime import date

async def check_ai_reason():
    today = date(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        query = select(PaperTrade)
        res = await session.execute(query)
        ptrades = res.scalars().all()
        todays_ptrades = [t for t in ptrades if t.opened_at.date() == today]
        
        for t in todays_ptrades:
            print(f"- {t.symbol}: AI Reason = {t.ai_reason[:150]}...")
            print(f"  Pattern = {t.pattern_name}")
            print()

if __name__ == "__main__":
    asyncio.run(check_ai_reason())
