import asyncio
from db.database import AsyncSessionLocal
from engine.intelligence_hub import score_symbol
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Let's test a mix of symbols, including ones that had news recently
        syms = ['IEX.NS', 'TCS.NS', 'WIPRO.NS', 'OBEROIRLTY.NS', 'AFCONS.NS', 'ITC.NS']
        for s in syms:
            res = await score_symbol(s, db, swing_mode=True)
            print(f"=== {s} ===")
            print(f"Master Score: {res.master_score:6.2f} | Signal: {res.signal:11} | Blocked: {res.is_blocked} ({res.blocked_reason})")
            strategy_scores = res.reasoning.get("strategy_scores", {})
            print(f"Strategy Vector:")
            print(f"  - Technical Swing: {strategy_scores.get('technical_swing', 0):6.2f}")
            print(f"  - Event Swing:     {strategy_scores.get('event_swing', 0):6.2f}")
            print(f"  - Intraday:        {strategy_scores.get('intraday', 0):6.2f}")
            print(f"  - Positional:      {strategy_scores.get('positional', 0):6.2f}")
            print("-" * 40)

asyncio.run(main())
