import asyncio
import sys
from datetime import datetime, date
from sqlalchemy import select
sys.path.append(".")

from db.database import AsyncSessionLocal
from db.models import PaperTrade, OpenPosition

async def trace():
    async with AsyncSessionLocal() as session:
        # Check an open position
        stmt = select(OpenPosition).limit(1)
        res = await session.execute(stmt)
        pos = res.scalar_one_or_none()
        
        if pos:
            print(f"Open Position {pos.symbol}: Entry={pos.entry_price}, SL={pos.stop_loss}, TP={pos.take_profit}")
        else:
            print("No open positions.")

if __name__ == "__main__":
    asyncio.run(trace())
