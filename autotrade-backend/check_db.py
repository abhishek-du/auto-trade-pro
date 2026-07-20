import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def check_db():
    async with AsyncSessionLocal() as db:
        res = await db.execute(text("SELECT MAX(timestamp) FROM candles"))
        print(f"Max candle timestamp (any timeframe): {res.scalar()}")
        
        res = await db.execute(text("SELECT DISTINCT timeframe FROM candles"))
        print(f"Timeframes: {[r[0] for r in res.all()]}")

asyncio.run(check_db())
