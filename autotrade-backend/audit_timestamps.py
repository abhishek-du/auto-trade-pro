import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Get latest run
        res = await db.execute(text("SELECT symbol, bar_time, master_score, technical_score, news_score FROM master_scores ORDER BY bar_time DESC LIMIT 1;"))
        row = res.fetchone()
        print("Latest score:", row)
        
        # Check DB timezone
        res = await db.execute(text("SHOW timezone;"))
        print("DB Timezone:", res.fetchone())

        # Check current time
        res = await db.execute(text("SELECT NOW();"))
        print("DB NOW:", res.fetchone())

asyncio.run(main())
