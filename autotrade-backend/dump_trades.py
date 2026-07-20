import asyncio
import sys
from sqlalchemy import select
sys.path.append(".")

from db.database import AsyncSessionLocal
from db.models import PaperTrade

async def dump():
    async with AsyncSessionLocal() as session:
        stmt = select(PaperTrade).where(PaperTrade.exit_reason == 'STOP_LOSS').order_by(PaperTrade.id.desc()).limit(10)
        res = await session.execute(stmt)
        trades = res.scalars().all()
        for t in trades:
            print(f"ID={t.id} Sym={t.symbol} Dir={t.direction} Entry={t.entry_price} Exit={t.exit_price} SL={t.stop_loss} PnL={t.pnl}")

if __name__ == "__main__":
    asyncio.run(dump())
