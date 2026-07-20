import asyncio
import sys
from datetime import datetime, date
from sqlalchemy import select
sys.path.append(".")

from db.database import AsyncSessionLocal
from db.models import PaperTrade

async def analyze_today_trades():
    today = date(2026, 7, 15) # Today's date from metadata
    
    async with AsyncSessionLocal() as session:
        stmt = select(PaperTrade).where(
            PaperTrade.closed_at >= datetime(2026, 7, 15, 0, 0, 0)
        )
        res = await session.execute(stmt)
        trades = res.scalars().all()
        
        print(f"Total closed trades today: {len(trades)}")
        
        wins = 0
        losses = 0
        
        for t in trades:
            pnl = t.pnl if t.pnl else 0
            if pnl > 0:
                wins += 1
            else:
                losses += 1
                
            print(f"[{t.status.value}] {t.direction.value} {t.symbol} | PNL: {pnl:.2f} ({t.pnl_percent}%) | Entry: {t.entry_price} Exit: {t.exit_price}")
            print(f"   Strategy: {t.strategy_name} | Exit Reason: {t.exit_reason}")
            print(f"   AI Reason: {t.ai_reason[:100]}...")
            print(f"   Regime: {t.regime_at_entry} -> {t.regime_at_exit}")
            print("-" * 40)
            
        print(f"\nWins: {wins}, Losses: {losses}")
        if (wins + losses) > 0:
            print(f"Win Rate: {wins/(wins+losses)*100:.2f}%")
            
        # Let's also check currently open trades
        stmt = select(PaperTrade).where(PaperTrade.status == "OPEN")
        res = await session.execute(stmt)
        open_trades = res.scalars().all()
        
        print(f"\nCurrently Open Trades: {len(open_trades)}")
        for t in open_trades:
             print(f"[OPEN] {t.direction.value} {t.symbol} | Entry: {t.entry_price}")
             print(f"   Strategy: {t.strategy_name}")
             print("-" * 40)

if __name__ == "__main__":
    asyncio.run(analyze_today_trades())
