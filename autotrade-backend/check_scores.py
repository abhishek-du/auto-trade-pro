import asyncio
from db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # Latest scores
        scores = (await db.execute(text("""
            SELECT symbol, master_score, signal, is_blocked, blocked_reason, regime
            FROM master_intelligence_scores
            ORDER BY scored_at DESC
            LIMIT 50
        """))).fetchall()
        
        print("=== LATEST SCORES ===")
        buys = 0
        sells = 0
        blocks = 0
        for r in scores:
            if r.signal in ('BUY', 'STRONG_BUY'): buys += 1
            if r.signal in ('SELL', 'STRONG_SELL'): sells += 1
            if r.is_blocked: blocks += 1
            print(f"{r.symbol:10} | {r.master_score:6.2f} | {r.signal:11} | Blocked: {r.is_blocked} ({r.blocked_reason}) | {r.regime}")
        
        print(f"\nSummary of last 50: Buys: {buys}, Sells: {sells}, Blocked: {blocks}")

asyncio.run(main())
