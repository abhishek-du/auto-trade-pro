import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import PaperTrade, OpenPosition, AgentTrade
from datetime import datetime, date

async def get_todays_trades():
    today = date(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        print("--- PaperTrades ---")
        query = select(PaperTrade)
        res = await session.execute(query)
        ptrades = res.scalars().all()
        todays_ptrades = [t for t in ptrades if t.opened_at.date() == today]
        print(f"Total PaperTrades today: {len(todays_ptrades)}")
        for t in todays_ptrades:
            print(f"- {t.symbol}: {t.direction} {t.size_units} shares @ {t.entry_price}")
        
        print("\n--- OpenPositions ---")
        query = select(OpenPosition)
        res = await session.execute(query)
        opos = res.scalars().all()
        todays_opos = [t for t in opos if t.opened_at.date() == today]
        print(f"Total OpenPositions today: {len(todays_opos)}")
        for t in todays_opos:
            print(f"- {t.symbol}: {t.direction} {t.size_units} shares @ {t.entry_price}")

if __name__ == "__main__":
    asyncio.run(get_todays_trades())
