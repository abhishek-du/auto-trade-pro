import asyncio
from db.database import AsyncSessionLocal
from db.models import PaperTrade
from sqlalchemy import select, func as sqlfunc

async def main():
    async with AsyncSessionLocal() as db:
        # Query 1
        q1 = select(sqlfunc.coalesce(sqlfunc.sum(PaperTrade.pnl), 0.0)).where(PaperTrade.exit_price != None, PaperTrade.pnl != None)
        print("Query 1:", str(q1))
        res1 = (await db.execute(q1)).scalar()
        print("Result 1:", res1)

        # Query 2
        q2 = select(sqlfunc.coalesce(sqlfunc.sum(PaperTrade.pnl), 0.0)).where(PaperTrade.exit_price.isnot(None), PaperTrade.pnl.isnot(None))
        print("Query 2:", str(q2))
        res2 = (await db.execute(q2)).scalar()
        print("Result 2:", res2)

asyncio.run(main())
