import asyncio
from db.database import AsyncSessionLocal
from engine.intelligence_hub import score_symbol
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        syms = ['INFY.NS', 'TCS.NS', 'HAPPSTMNDS.NS', 'RPOWER.NS']
        for s in syms:
            res = await score_symbol(s, db, swing_mode=True)
            print(f"{s:15} | Score: {res.master_score:6.2f} | Signal: {res.signal:11} | Blocked: {res.is_blocked} ({res.blocked_reason})")

asyncio.run(main())
