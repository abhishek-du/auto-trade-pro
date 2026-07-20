import asyncio
import sys
from datetime import datetime, date
from sqlalchemy import select
sys.path.append(".")

from db.database import AsyncSessionLocal
from db.models import PaperTrade

async def debug_trade():
    async with AsyncSessionLocal() as session:
        stmt = select(PaperTrade).where(PaperTrade.symbol == "GOODLUCK.NS").order_by(PaperTrade.id.desc()).limit(1)
        res = await session.execute(stmt)
        trade = res.scalar_one_or_none()
        
        if trade:
            print(f"Trade ID: {trade.id}")
            print(f"Entry Price: {trade.entry_price}")
            print(f"Exit Price: {trade.exit_price}")
            print(f"Stop Loss: {trade.stop_loss}")
            print(f"Take Profit: {trade.take_profit}")
            print(f"Status: {trade.status.value}")
            print(f"Exit Reason: {trade.exit_reason}")
            print(f"Indicator Snapshot: {trade.indicator_snapshot}")
        else:
            print("Trade not found.")

if __name__ == "__main__":
    asyncio.run(debug_trade())
