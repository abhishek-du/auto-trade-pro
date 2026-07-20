import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import AgentDecision
from datetime import date

async def check_decisions():
    today = date(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        query = select(AgentDecision).where(AgentDecision.symbol == 'SUNTV.NS')
        res = await session.execute(query)
        logs = res.scalars().all()
        todays = [l for l in logs if l.timestamp.date() == today]
        
        print(f"Total AgentDecisions for SUNTV today: {len(todays)}")
        for l in todays:
            print(f"- Time: {l.timestamp.time()} | Taken: {l.taken} | Action: {l.action} | Reason: {l.drop_reason}")

if __name__ == "__main__":
    asyncio.run(check_decisions())
