import asyncio
from db.database import engine, AsyncSessionLocal
from sqlalchemy import text
from db.models import Base

async def init_db():
    print("Creating MasterEvent table...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    print("Altering agent_trades table...")
    async with AsyncSessionLocal() as session:
        try:
            await session.execute(text("ALTER TABLE agent_trades ADD COLUMN event_id VARCHAR(36) REFERENCES master_events(event_id) ON DELETE SET NULL;"))
            await session.commit()
            print("Successfully added event_id column!")
        except Exception as e:
            print(f"Failed or already exists: {e}")

if __name__ == "__main__":
    asyncio.run(init_db())
    print("Database updated.")
