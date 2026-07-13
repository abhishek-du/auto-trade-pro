import asyncio
from db.database import engine, Base
from db.models import PreMarketNewsQueue

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

if __name__ == "__main__":
    asyncio.run(init_db())
    print("Table created.")
