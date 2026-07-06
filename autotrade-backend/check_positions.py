import asyncio
from db.database import get_db
from sqlalchemy import text

async def main():
    async for db in get_db():
        result = await db.execute(text("SELECT id, symbol, instrument_type, trade_id FROM open_positions"))
        rows = result.fetchall()
        for r in rows:
            print(dict(r._mapping))
        break

asyncio.run(main())
