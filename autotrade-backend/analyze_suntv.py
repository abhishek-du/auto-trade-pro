import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import PaperTrade
from datetime import date

async def analyze_suntv():
    today = date(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        query = select(PaperTrade).where(PaperTrade.symbol == 'SUNTV.NS')
        res = await session.execute(query)
        trades = res.scalars().all()
        todays_trades = [t for t in trades if t.opened_at.date() == today]
        
        todays_trades.sort(key=lambda x: x.opened_at)
        
        print(f"Total SUNTV trades today: {len(todays_trades)}")
        for t in todays_trades:
            print(f"- ID: {t.id} | Opened: {t.opened_at} | Closed: {t.closed_at} | Status: {t.status} | Price: {t.entry_price} | Exit: {t.exit_price}")
            print(f"  Reason: {t.ai_reason[:150]}")

if __name__ == "__main__":
    asyncio.run(analyze_suntv())
