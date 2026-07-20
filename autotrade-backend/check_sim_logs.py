import asyncio
import sys
from sqlalchemy import select
sys.path.append(".")
from db.database import AsyncSessionLocal
from db.models import SimulationLog
async def dump():
    async with AsyncSessionLocal() as session:
        stmt = select(SimulationLog).where(SimulationLog.symbol.like('%HDFCLIFE.NS%')).order_by(SimulationLog.id.desc()).limit(50)
        res = await session.execute(stmt)
        for log in reversed(res.scalars().all()):
            print(f"[{log.timestamp}] {log.event_type} : {log.message}")
if __name__ == "__main__":
    asyncio.run(dump())
