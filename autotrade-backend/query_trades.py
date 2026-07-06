import asyncio
from sqlalchemy import text
from db.database import get_db

async def main():
    async for db in get_db():
        result = await db.execute(text("SELECT symbol, side, price, timestamp, confidence_score FROM paper_trades WHERE timestamp >= '2026-07-02' ORDER BY timestamp DESC LIMIT 10"))
        trades = result.fetchall()
        print("Today's trades:")
        for t in trades:
            print(t)
        break

asyncio.run(main())
