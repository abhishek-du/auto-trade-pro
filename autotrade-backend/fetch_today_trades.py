import asyncio
from datetime import datetime
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import AgentTrade, AgentDecision, OpenPosition

async def fetch_real_today_trades():
    today = datetime.now().date()
    
    async with AsyncSessionLocal() as session:
        print(f"=== TRADES EXECUTED TODAY ({today}) ===")
        trades_res = await session.execute(select(AgentTrade))
        all_trades = trades_res.scalars().all()
        today_trades = [t for t in all_trades if t.created_at.date() == today]
        
        if not today_trades:
            print("No trades found in AgentTrade for today.")
        else:
            for t in today_trades:
                print(f"[{t.side}] {t.symbol} | Qty: {t.qty} | Price: {t.entry_price}")
                print(f"   Strategy: {t.strategy}")
                print("-" * 50)
                
        print("\n=== AI DECISIONS TODAY ===")
        dec_res = await session.execute(select(AgentDecision).order_by(AgentDecision.id.desc()))
        all_dec = dec_res.scalars().all()
        today_dec = [d for d in all_dec if d.ts.date() == today]
        
        if not today_dec:
            print("No AgentDecisions found for today.")
        else:
            for d in today_dec[:15]:
                print(f"[{d.action}] {d.symbol} (Score: {d.confidence})")
                print(f"   Reasoning: {d.reasoning}")
                print("-" * 50)

if __name__ == "__main__":
    asyncio.run(fetch_real_today_trades())
