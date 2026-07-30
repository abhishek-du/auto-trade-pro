import asyncio
from datetime import datetime, date, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import AgentDecision

async def run():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        since = datetime.now() - timedelta(days=1)
        stmt = select(AgentDecision).where(AgentDecision.created_at >= since)
        result = await session.execute(stmt)
        decisions = result.scalars().all()
        
        for d in decisions:
            reason = str(getattr(d, 'reasons', getattr(d, 'reason', '')))
            if "Did not meet criteria" in reason:
                print("Found 'Did not meet criteria':")
                print("Verdict:", getattr(d, "verdict", "N/A"))
                print("Confidence_factors:", d.confidence_factors)
                break

if __name__ == "__main__":
    asyncio.run(run())
