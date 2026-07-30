import asyncio
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import PaperTrade

async def run():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        stmt = select(PaperTrade).where(PaperTrade.id == 3649)
        result = await session.execute(stmt)
        t = result.scalars().first()
        
        if t:
            print("Indicator snapshot:")
            print(json.dumps(t.indicator_snapshot, indent=2))

if __name__ == "__main__":
    asyncio.run(run())
