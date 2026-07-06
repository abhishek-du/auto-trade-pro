import asyncio
from sqlalchemy import text
from db.database import get_db
import json

async def main():
    async for db in get_db():
        # Using PostgreSQL UPSERT (ON CONFLICT)
        await db.execute(text(
            "INSERT INTO runtime_settings (key, value) VALUES ('max_portfolio_risk', '0.6') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ))
        await db.commit()
        print("Updated max_portfolio_risk to 0.6 in DB")
        break

asyncio.run(main())
