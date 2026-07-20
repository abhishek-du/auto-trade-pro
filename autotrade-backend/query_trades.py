import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import Trade
from datetime import datetime, date

async def get_todays_trades():
    today = date(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        query = select(Trade).where(Trade.timestamp >= datetime(today.year, today.month, today.day))
        res = await session.execute(query)
        trades = res.scalars().all()
        
        print(f"Total trades today: {len(trades)}")
        for t in trades:
            print(f"- {t.symbol}: {t.side} {t.quantity} shares @ {t.price} (Reason: {t.reason})")

if __name__ == "__main__":
    asyncio.run(get_todays_trades())
