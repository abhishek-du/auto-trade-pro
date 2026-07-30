import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import Candle
from datetime import datetime, date

async def check():
    engine = create_async_engine("postgresql+asyncpg://autotrade:autotrade@localhost:5432/autotrade_pro")
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    async with async_session() as session:
        today = date.today()
        stmt = select(Candle).where(Candle.symbol == "RAINBOW.NS", Candle.timeframe == "5minute").order_by(Candle.timestamp.desc()).limit(15)
        result = await session.execute(stmt)
        candles = result.scalars().all()
        if not candles:
            print("No 5-min candles found in DB. Trying 15minute.")
            stmt = select(Candle).where(Candle.symbol == "RAINBOW.NS", Candle.timeframe == "15minute").order_by(Candle.timestamp.desc()).limit(15)
            result = await session.execute(stmt)
            candles = result.scalars().all()
        for c in reversed(candles):
            print(f"{c.timestamp}: O={c.open:.2f} H={c.high:.2f} L={c.low:.2f} C={c.close:.2f} V={c.volume}")

if __name__ == "__main__":
    asyncio.run(check())
