import asyncio
import json
import logging
from db.database import AsyncSessionLocal
from db.models import MarketShortlist
from sqlalchemy import select
from engine.agent.decision_engine import llm_tooluse_candidate

logging.basicConfig(level=logging.WARNING)

class MockDecision:
    def __init__(self, action, regime, score, conf):
        self.action = action
        self.regime = regime
        self.master_score = score
        self.confidence = conf
        self.confidence_factors = {}

async def main():
    async with AsyncSessionLocal() as session:
        # Get top 3 stocks from market shortlist
        rows = await session.execute(
            select(MarketShortlist).order_by(MarketShortlist.rank).limit(3)
        )
        stocks = rows.scalars().all()

    if not stocks:
        print("No stocks found in shortlist.")
        return

    print("=== TOMORROW'S TRADE ANALYSIS ===")
    for s in stocks:
        print(f"\nEvaluating: {s.symbol} (Score: {s.master_score})")
        dec = MockDecision(s.signal, "BULL_TRENDING", s.master_score, 65)
        
        class MockCand:
            strategy = "SWING_BREAKOUT"
            entry = 1000  # mock value since it's not in MarketShortlist
            stop = 950
            target = 1150
            risk_reward = 3.0
            hub_subscores = {"technical": 80, "news": 50, "sector": 60, "macro": 50, "earnings": 50, "fundamental": 50, "options": 0}
            reasons = []
            chart_brief = f"{s.symbol} Strong technical breakout with high master score."
        
        cand = MockCand()
        result = await llm_tooluse_candidate(s.symbol, cand, dec)
        
        if result:
            print(f"VERDICT: {result.get('verdict')}")
            print(f"Confidence: {result.get('confidence')}%")
            print(f"Bull Case: {result.get('bull')}")
            print(f"Bear Case: {result.get('bear')}")
            print(f"Key Risk: {result.get('key_risk')}")
            print(f"Tools Used: {len(result.get('tools_used', []))} {result.get('tools_used')}")
        else:
            print("Agent failed to reach a decision or timed out.")

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(main())
