import asyncio
import sys
from sqlalchemy import text
sys.path.append(".")
from db.database import AsyncSessionLocal

async def dump():
    async with AsyncSessionLocal() as session:
        for sym in ["SPMLINFRA.NS", "AVANTIFEED.NS", "ROLEXRINGS.NS"]:
            res = await session.execute(text("SELECT timeframe, COUNT(*) FROM candles WHERE symbol = :sym GROUP BY timeframe"), {"sym": sym})
            print(f"{sym}:")
            for row in res.fetchall():
                print(f"  {row[0]}: {row[1]}")
if __name__ == "__main__":
    asyncio.run(dump())
