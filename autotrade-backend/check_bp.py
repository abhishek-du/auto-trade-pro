import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import PaperTrade
from datetime import datetime

async def run():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        stmt = select(PaperTrade).where(PaperTrade.symbol == "BERGEPAINT.NS").order_by(PaperTrade.opened_at.desc()).limit(10)
        result = await session.execute(stmt)
        trades = result.scalars().all()
        
        for t in trades:
            print(f"Trade {t.id} ({t.symbol}): Opened {t.opened_at}, Closed {t.closed_at}")
            print(f"Status: {t.status}, Exit Reason: {getattr(t, 'exit_reason', 'N/A')}")
            print(f"Entry: {t.entry_price}, SL: {t.stop_loss}, TP: {t.take_profit}, Exit: {t.exit_price}")
            print("-" * 40)

if __name__ == "__main__":
    asyncio.run(run())
