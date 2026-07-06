import asyncio
from sqlalchemy import text
from db.database import get_db

symbols = ['EPACK.NS', 'IGARASHI.NS', 'ATULAUTO.NS', 'RICOAUTO.NS', 'MOSCHIP.NS', 'INDOFARM.NS', 'EPACKPEB.NS']

async def main():
    async for db in get_db():
        for sym in symbols:
            res = await db.execute(text("SELECT symbol, signal, master_score, scored_at FROM master_intelligence_scores WHERE symbol = :sym ORDER BY scored_at DESC LIMIT 1"), {"sym": sym})
            print(f"{sym}: {res.fetchall()}")
        break

asyncio.run(main())
