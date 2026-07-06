import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        await db.execute(text("UPDATE open_positions SET instrument_type='CE' WHERE instrument_type='OPTION'"))
        await db.execute(text("UPDATE paper_trades SET instrument_type='CE' WHERE instrument_type='OPTION'"))
        await db.commit()
        print("Fixed instrument types")

asyncio.run(main())
