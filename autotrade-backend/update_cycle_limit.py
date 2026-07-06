import asyncio
from sqlalchemy import text
from db.database import get_db

async def main():
    async for db in get_db():
        await db.execute(text(
            "INSERT INTO runtime_settings (key, value) VALUES ('max_new_entries_per_cycle', '5') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ))
        await db.commit()
        print("Updated max_new_entries_per_cycle to 5 in DB.")
        break

asyncio.run(main())
