import asyncio
import sys
from sqlalchemy import select
sys.path.append(".")
from db.database import AsyncSessionLocal
from db.models import HubUniverse
async def dump():
    async with AsyncSessionLocal() as session:
        count = await session.execute(select(HubUniverse.symbol))
        rows = count.scalars().all()
        print(f"Total symbols: {len(rows)}")
        for sym in ["SPMLINFRA.NS", "AVANTIFEED.NS", "ROLEXRINGS.NS"]:
            if sym in rows:
                print(f"{sym} is in HubUniverse")
            else:
                print(f"{sym} is MISSING from HubUniverse")
if __name__ == "__main__":
    asyncio.run(dump())
