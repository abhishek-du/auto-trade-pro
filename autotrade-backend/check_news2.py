import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # First check actual columns
        cols = (await db.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name='news_items' ORDER BY ordinal_position
        """))).fetchall()
        print("Columns:", [c[0] for c in cols])
        
        total = (await db.execute(text("SELECT COUNT(*) FROM news_items"))).scalar()
        print(f"Total: {total}")
        
        # Get column names dynamically
        recent = (await db.execute(text("""
            SELECT * FROM news_items ORDER BY published_at DESC LIMIT 5
        """))).fetchall()
        for r in recent:
            print(dict(r._mapping))

asyncio.run(main())
