import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as session:
        print("Altering table agent_trades to add analytics_json...")
        try:
            await session.execute(text("ALTER TABLE agent_trades ADD COLUMN analytics_json JSONB DEFAULT '{}'::jsonb NOT NULL;"))
            await session.commit()
            print("Successfully added analytics_json column!")
        except Exception as e:
            print(f"Failed or already exists: {e}")

asyncio.run(main())
