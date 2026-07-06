import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Get all columns first
        cols = (await db.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name='paper_trades' ORDER BY ordinal_position
        """))).fetchall()
        print("paper_trades columns:", [c[0] for c in cols])

asyncio.run(main())
