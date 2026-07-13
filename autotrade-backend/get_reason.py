import asyncio
from sqlalchemy import select
from db.models import PaperTrade
from tasks._db import celery_session
from datetime import date

async def get_reason():
    async with celery_session() as session:
        today = date.today()
        res = await session.execute(select(PaperTrade).where(PaperTrade.symbol == 'BORORENEW.NS'))
        trades = res.scalars().all()
        for t in trades:
            if t.opened_at and t.opened_at.date() == today:
                print(f"[{t.symbol}] AI Reason:\n{t.ai_reason}\n")

if __name__ == "__main__":
    asyncio.run(get_reason())
