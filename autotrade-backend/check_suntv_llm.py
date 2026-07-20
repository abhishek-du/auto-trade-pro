import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import LLMReasoningLog
from datetime import date

async def run():
    today = date(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        query = select(LLMReasoningLog).where(LLMReasoningLog.symbol == 'SUNTV.NS')
        res = await session.execute(query)
        logs = res.scalars().all()
        todays = [l for l in logs if l.created_at.date() == today]
        
        for l in todays:
            print("====================================")
            print(f"VERDICT: {l.content}")
            print(f"REASONING: {l.reasoning[:1000]}") # first 1000 chars

if __name__ == "__main__":
    asyncio.run(run())
