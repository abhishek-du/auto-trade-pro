import asyncio
from db.database import get_db
from sqlalchemy import text
import pandas as pd

async def main():
    async for db in get_db():
        result = await db.execute(text("SELECT symbol, direction, entry_price, stop_loss, current_price, pnl_percent FROM paper_trades WHERE status='OPEN'"))
        rows = result.fetchall()
        print(f"Total open trades: {len(rows)}")
        for r in rows:
            print(f"{r.symbol} ({r.direction}) | Entry: {r.entry_price:.2f} | CMP: {r.current_price:.2f} | PnL: {r.pnl_percent:.2f}% | SL: {r.stop_loss:.2f}")
        break

asyncio.run(main())
