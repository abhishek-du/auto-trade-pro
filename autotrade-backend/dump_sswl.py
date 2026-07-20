import asyncio
import sys
from sqlalchemy import select
sys.path.append(".")

from db.database import AsyncSessionLocal
from db.models import PaperTrade

async def dump():
    async with AsyncSessionLocal() as session:
        stmt = select(PaperTrade).where(PaperTrade.id == 3527)
        res = await session.execute(stmt)
        trade = res.scalar_one_or_none()
        if trade:
            print(f"ID={trade.id} Sym={trade.symbol} Entry={trade.entry_price} Exit={trade.exit_price} SL={trade.stop_loss} TP={trade.take_profit} Reason={trade.exit_reason}")
            print(f"Snapshot: {trade.indicator_snapshot}")

if __name__ == "__main__":
    asyncio.run(dump())
