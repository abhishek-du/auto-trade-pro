import asyncio
from sqlalchemy import update
from db.database import AsyncSessionLocal
from db.models import HubCycleLog

async def clear_lock():
    async with AsyncSessionLocal() as session:
        print("Clearing stuck running cycle locks...")
        await session.execute(
            update(HubCycleLog)
            .where(HubCycleLog.status == "running")
            .values(status="error")
        )
        await session.commit()
        print("Lock cleared.")

if __name__ == "__main__":
    asyncio.run(clear_lock())
