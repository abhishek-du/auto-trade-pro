import asyncio
import sys
from datetime import datetime, date
from sqlalchemy import select
sys.path.append(".")

from db.database import AsyncSessionLocal
from db.models import PaperTrade

async def dump_today():
    today = date(2026, 7, 16)
    async with AsyncSessionLocal() as session:
        stmt = select(PaperTrade).where(
            PaperTrade.opened_at >= datetime(today.year, today.month, today.day)
        ).order_by(PaperTrade.opened_at)
        res = await session.execute(stmt)
        trades = res.scalars().all()
        for t in trades:
            print(f"ID={t.id} Sym={t.symbol} Dir={t.direction.name if t.direction else ''} "
                  f"Entry={t.entry_price} Exit={t.exit_price} SL={t.stop_loss} TP={t.take_profit} "
                  f"PnL={t.pnl} Status={t.status.name if t.status else ''} Reason={t.exit_reason}")
            print(f"  Confidence: {t.signal_confidence}")
            print(f"  AI Reason: {t.ai_reason[:200]}..." if t.ai_reason else "  AI Reason: None")
            if t.indicator_snapshot:
                print(f"  Snapshot: {str(t.indicator_snapshot)[:200]}...")
            print("-" * 80)

if __name__ == "__main__":
    asyncio.run(dump_today())
