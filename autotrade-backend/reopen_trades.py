import asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import PaperTrade, TradeStatus

async def run():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        # Find trades 3698 (PPLPHARMA.NS) and 3701 (RAINBOW.NS)
        stmt = select(PaperTrade).where(PaperTrade.id.in_([3698, 3701]))
        result = await session.execute(stmt)
        trades = result.scalars().all()
        
        for t in trades:
            print(f"Re-opening Trade {t.id} ({t.symbol}). Previous status: {t.status}")
            t.status = TradeStatus.OPEN
            t.closed_at = None
            t.exit_price = None
            t.pnl = None
            t.exit_reason = None
            t.held_duration_sec = None
        
        await session.commit()
        print("Trades have been successfully reopened!")

if __name__ == "__main__":
    asyncio.run(run())
