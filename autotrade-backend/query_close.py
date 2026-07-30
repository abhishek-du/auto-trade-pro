import asyncio
from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import PaperTrade

async def run():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        today = date.today()
        stmt = select(PaperTrade).where(PaperTrade.opened_at >= today).where(PaperTrade.symbol.in_(["PPLPHARMA.NS", "RAINBOW.NS"]))
        result = await session.execute(stmt)
        trades = result.scalars().all()
        for t in trades:
            print(f"Trade {t.id} ({t.symbol}): Opened {t.opened_at}, Closed {t.closed_at}")
            print(f"Status: {t.status}, Exit Reason: {getattr(t, 'exit_reason', 'N/A')}")
            print(f"Ai Reason: {t.ai_reason[:100]}")
            print("-" * 50)

if __name__ == "__main__":
    asyncio.run(run())
