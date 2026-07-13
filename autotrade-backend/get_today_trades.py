import asyncio
import datetime
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import AgentTrade, AgentDecision, OpenPosition

async def main():
    today = datetime.date(2026, 7, 13)
    
    async with AsyncSessionLocal() as session:
        # Get trades from AgentTrade
        print("=== TRADES EXECUTED TODAY (AgentTrade) ===")
        trades_res = await session.execute(select(AgentTrade))
        all_trades = trades_res.scalars().all()
        today_trades = [t for t in all_trades if t.executed_at.date() == today]
        
        if not today_trades:
            print("No trades found in AgentTrade for today.")
        else:
            for t in today_trades:
                print(f"[{t.trade_type}] {t.symbol} | Qty: {t.quantity} | Price: {t.price}")
                print(f"   Reason: {t.ai_reason}")
                print("-" * 50)
                
        # Get open positions
        print("\n=== CURRENT OPEN POSITIONS (Opened Today) ===")
        open_res = await session.execute(select(OpenPosition))
        open_positions = open_res.scalars().all()
        today_open = [p for p in open_positions if p.opened_at.date() == today]
        
        if not today_open:
            print("No open positions taken today.")
        else:
            for p in today_open:
                print(f"[{p.direction}] {p.symbol} @ {p.entry_price} (Size: {p.size_units})")
                print(f"   Reason: {p.ai_reason}")
                print("-" * 50)
                
        # Get decisions
        print("\n=== AI DECISIONS TODAY ===")
        dec_res = await session.execute(select(AgentDecision).order_by(AgentDecision.id.desc()).limit(20))
        all_dec = dec_res.scalars().all()
        today_dec = [d for d in all_dec if d.decision_time.date() == today]
        
        if not today_dec:
            print("No AgentDecisions found for today.")
        else:
            for d in today_dec:
                print(f"[{d.action}] {d.symbol} (Score: {d.score})")
                print(f"   Thought: {d.thought_process}")
                print("-" * 50)

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(main())
