import asyncio
import datetime
from zoneinfo import ZoneInfo
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def check_age():
    async with AsyncSessionLocal() as db:
        res = await db.execute(text("SELECT MAX(timestamp) FROM candles WHERE timeframe = '5m'"))
        last_ts = res.scalar()
        if not last_ts:
            print("No 5m candles")
            return
            
        now = datetime.datetime.now(datetime.timezone.utc)
        age = (now - last_ts.replace(tzinfo=datetime.timezone.utc)).total_seconds() / 60
        print(f"Last candle: {last_ts}")
        print(f"Age: {age:.1f} minutes")

asyncio.run(check_age())
