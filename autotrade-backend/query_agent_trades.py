import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import AgentTrade
from datetime import datetime, date

async def get_todays_trades():
    today = date(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        # Check columns
        # created_at or executed_at
        query = select(AgentTrade)
        res = await session.execute(query)
        trades = res.scalars().all()
        
        todays_trades = [t for t in trades if t.created_at.date() == today]
        print(f"Total trades today: {len(todays_trades)}")
        for t in todays_trades:
            print(f"- {t.symbol}: {t.side} {t.quantity} shares @ {t.execution_price if hasattr(t, 'execution_price') else t.price if hasattr(t, 'price') else 'N/A'}")
            print(f"  Reason: {t.narrative_reason if hasattr(t, 'narrative_reason') else t.reason if hasattr(t, 'reason') else 'N/A'}")
            print()

if __name__ == "__main__":
    asyncio.run(get_todays_trades())
