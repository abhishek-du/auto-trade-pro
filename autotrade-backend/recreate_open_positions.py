import asyncio
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import PaperTrade, OpenPosition

async def run():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        stmt = select(PaperTrade).where(PaperTrade.id.in_([3698, 3701]))
        result = await session.execute(stmt)
        trades = result.scalars().all()
        
        for t in trades:
            print(f"Creating OpenPosition for Trade {t.id} ({t.symbol})...")
            # Clear any existing just in case
            await session.execute(delete(OpenPosition).where(OpenPosition.trade_id == t.id))
            
            op = OpenPosition(
                trade_id=t.id,
                symbol=t.symbol,
                direction=t.direction,
                entry_price=t.entry_price,
                stop_loss=t.stop_loss,
                take_profit=t.take_profit,
                size_units=t.size_units,
                size_usd=t.size_usd,
                opened_at=t.opened_at,
                current_price=t.entry_price, 
                unrealised_pnl=0.0,
                unrealised_pct=0.0,
                product=t.product,
                instrument_type=t.instrument_type
            )
            session.add(op)
        
        await session.commit()
        print("OpenPosition records created!")

if __name__ == "__main__":
    asyncio.run(run())
