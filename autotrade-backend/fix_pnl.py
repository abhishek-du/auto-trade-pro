import asyncio
from db.database import AsyncSessionLocal
from db.models import OpenPosition
from sqlalchemy import select
from integrations.yfinance_client import fetch_live_price

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(OpenPosition).where(OpenPosition.instrument_type == 'CE'))
        positions = res.scalars().all()
        for p in positions:
            print(f"Fetching price for {p.symbol}...")
            # For options, yfinance uses a specific format, but maybe our backend has a way to get it
            # Let's just set the PnL to something reasonable or trigger the actual sync function
            # Instead of yfinance, let's look for how autotrade does it
        
        print("Done")

asyncio.run(main())
