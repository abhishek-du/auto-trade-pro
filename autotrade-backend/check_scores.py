import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import MasterIntelligenceScore

async def check_scores():
    async with AsyncSessionLocal() as session:
        print("=== Today's Master Scores (Multi-Strategy) ===")
        res = await session.execute(
            select(MasterIntelligenceScore)
            .where(MasterIntelligenceScore.symbol.in_(['ITC.NS', 'TECHM.NS', 'WIPRO.NS', 'IEX.NS']))
            .order_by(MasterIntelligenceScore.bar_time.desc())
            .limit(10)
        )
        scores = res.scalars().all()
        
        seen = set()
        for s in scores:
            if s.symbol in seen:
                continue
            seen.add(s.symbol)
            print(f"[{s.bar_time}] {s.symbol} | Master Health: {s.master_score} | Blocked: {s.is_blocked}")
            reasoning = s.reasoning or {}
            strat = reasoning.get("strategy_scores", {})
            print(f"   Event Swing:     {strat.get('event_swing')}")
            print(f"   Technical Swing: {strat.get('technical_swing')}")
            print(f"   Positional:      {strat.get('positional')}")
            print(f"   Intraday:        {strat.get('intraday')}")
            print("-" * 50)

if __name__ == "__main__":
    asyncio.run(check_scores())
