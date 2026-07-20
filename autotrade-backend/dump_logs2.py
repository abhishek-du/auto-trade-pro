import asyncio
import sys
from sqlalchemy import select
sys.path.append(".")

from db.database import AsyncSessionLocal
from db.models import SimulationLog

async def dump():
    async with AsyncSessionLocal() as session:
        stmt = select(SimulationLog).order_by(SimulationLog.id.desc()).limit(20)
        res = await session.execute(stmt)
        logs = res.scalars().all()
        for log in logs:
            print(f"[{log.timestamp}] {log.event_type} - {log.symbol} : {log.message}")

if __name__ == "__main__":
    asyncio.run(dump())
