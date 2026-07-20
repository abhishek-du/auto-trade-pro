import asyncio
from datetime import datetime, date
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import HubCycleLog

async def get_cycle_logs():
    today = date(2026, 7, 17)
    
    async with AsyncSessionLocal() as session:
        print(f"=== Hub Cycle Logs ({today}) ===")
        res = await session.execute(select(HubCycleLog).order_by(HubCycleLog.cycle_start.desc()).limit(10))
        logs = res.scalars().all()
        for log in logs:
            print(f"[{log.cycle_start}] Status: {log.status}")

if __name__ == "__main__":
    asyncio.run(get_cycle_logs())
