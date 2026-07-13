import asyncio
from sqlalchemy import select
from db.models import PaperTrade, OpenPosition, AgentDecision
from tasks._db import celery_session
from datetime import datetime, date

async def analyze_today():
    async with celery_session() as session:
        today = date.today()
        print(f"--- Trades for {today} ---")
        res = await session.execute(select(PaperTrade))
        trades = res.scalars().all()
        today_trades = [t for t in trades if t.opened_at and t.opened_at.date() == today]
        print(f"Total trades taken today: {len(today_trades)}")
        for t in today_trades:
            print(f"[{t.symbol}] Side: {t.direction}, Asset: {t.instrument_type}, PnL: {t.pnl}, Status: {t.status}")
            
        print(f"\n--- Open Positions ---")
        res = await session.execute(select(OpenPosition))
        positions = res.scalars().all()
        for p in positions:
            print(f"[{p.symbol}] Side: {p.direction}, Asset: {p.trade.instrument_type if getattr(p, 'trade', None) else 'N/A'}")
            
        print(f"\n--- F&O Trades Analysis ---")
        fn_trades = [t for t in trades if t.instrument_type in ["CE", "PE", "FUTURE", "OPTION"]]
        print(f"Total F&O trades in history: {len(fn_trades)}")
        for t in fn_trades:
            print(f"[{t.symbol}] Opened: {t.opened_at}, PnL: {t.pnl}")
        
        # Let's check Agent Decisions today
        res = await session.execute(select(AgentDecision))
        decisions = res.scalars().all()
        today_decisions = [d for d in decisions if d.created_at and d.created_at.date() == today]
        print(f"\n--- Agent Decisions Today: {len(today_decisions)} ---")
        for d in today_decisions[:5]:
            print(f"[{d.symbol}] Action: {d.action}, Confidence: {d.confidence}")

if __name__ == "__main__":
    asyncio.run(analyze_today())

