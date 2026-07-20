import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import LLMReasoningLog
from datetime import date

async def check_llm_logs():
    today = date(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        query = select(LLMReasoningLog)
        res = await session.execute(query)
        logs = res.scalars().all()
        todays_logs = [l for l in logs if l.created_at.date() == today]
        
        print(f"Total LLMReasoningLogs today: {len(todays_logs)}")
        for l in todays_logs:
            print(f"- {l.symbol} [{l.source}]: {l.content[:100]}")

if __name__ == "__main__":
    asyncio.run(check_llm_logs())
