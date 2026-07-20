import asyncio
import sys
import logging
sys.path.append(".")
from db.database import AsyncSessionLocal
from engine.hub_universe import get_hub_universe
from engine.intelligence_hub import build_master_context, score_universe


logging.basicConfig(level=logging.DEBUG)

async def test_score():
    async with AsyncSessionLocal() as session:
        universe = await get_hub_universe(session)
        print(f"Total universe size: {len(universe)}")
        if "AVANTIFEED.NS" not in universe:
            print("AVANTIFEED.NS not in universe!")
            return
            
        print("AVANTIFEED.NS is in universe. Building ctx...")
        class MockPortfolio:
            open_positions = {}
            equity = 100000.0
            cash = 100000.0
        portfolio = MockPortfolio()
        ctx = await build_master_context(portfolio, session, hub_universe=["AVANTIFEED.NS"])
        print("Ctx built. Scoring AVANTIFEED.NS...")
        scored = await score_universe(["AVANTIFEED.NS"], ctx, session, timeframe="1d")
        print(f"Scored {len(scored)} symbols.")
        if scored:
            s = scored[0]
            print(f"Symbol: {s.symbol}, Master Score: {s.master_score}, Signal: {s.signal}")
        else:
            print("Symbol was skipped during scoring!")

if __name__ == "__main__":
    asyncio.run(test_score())
