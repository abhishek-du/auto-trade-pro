import asyncio
from datetime import datetime, date
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import PaperTrade, OpenPosition

async def run():
    today = date(2026, 7, 15)
    
    async with AsyncSessionLocal() as session:
        # Check closed trades for today
        query_closed = select(PaperTrade).where(
            PaperTrade.opened_at >= datetime(today.year, today.month, today.day)
        ).order_by(PaperTrade.opened_at)
        
        res_closed = await session.execute(query_closed)
        trades = res_closed.scalars().all()
        
        total_pnl = 0.0
        print(f"--- CLOSED TRADES TODAY ({today}) ---")
        if not trades:
            print("No closed trades today.")
        else:
            for t in trades:
                pnl = t.pnl if t.pnl is not None else 0.0
                total_pnl += pnl
                print(f"[{t.opened_at.time()}] {t.symbol} | {t.direction} | Entry: {t.entry_price} | Exit: {t.exit_price} | P&L: ₹{pnl:.2f} | Status: {t.status}")
                
        print(f"\nTotal Realized P&L today: ₹{total_pnl:.2f}\n")
        
        # Check open positions
        query_open = select(OpenPosition)
        res_open = await session.execute(query_open)
        open_pos = res_open.scalars().all()
        
        print("--- CURRENT OPEN POSITIONS ---")
        if not open_pos:
            print("No open positions.")
        else:
            for p in open_pos:
                unrealized = p.unrealised_pnl if p.unrealised_pnl is not None else 0.0
                print(f"{p.symbol} | {p.direction} | Qty: {p.size_units} | Entry: {p.entry_price} | Unrealized P&L: ₹{unrealized:.2f}")

if __name__ == "__main__":
    asyncio.run(run())
