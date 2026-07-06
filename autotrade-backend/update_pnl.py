import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text
from tasks.india_tasks import _sync_all_prices

async def main():
    async with AsyncSessionLocal() as db:
        await _sync_all_prices(db)
        await db.commit()
        print("Prices and PnL synced.")

asyncio.run(main())
