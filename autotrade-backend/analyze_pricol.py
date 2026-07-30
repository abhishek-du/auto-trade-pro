import asyncio
import json
import logging
logging.basicConfig(level=logging.WARNING)

from engine.agent.decision_engine import llm_tooluse_candidate
from utils.llm import get_last_reasoning

class MockCandidate:
    strategy = "EAGLE_EYE_SIGNAL"
    entry = 500
    stop = 460
    target = 600
    risk_reward = 2.5
    hub_subscores = {"technical": 70, "news": 80, "sector": 60, "macro": 50, "earnings": 60, "fundamental": 70, "options": 0}
    chart_brief = "Eagle Eyes setup: PRICOL demerger news. 1 share of Pricol Autotech Ltd for every 1 share of Pricol Ltd."
    reasons = []

class MockDecision:
    action = "BUY"
    regime = "NORMAL"
    master_score = 65
    confidence = 65
    confidence_factors = {}

async def test_run():
    symbol = "PRICOL.NS"
    cand = MockCandidate()
    dec = MockDecision()
    
    print(f"Running Deep AI Analysis for {symbol}...")
    result = await llm_tooluse_candidate(symbol, cand, dec)
    print("\n==== FINAL AI VERDICT ====")
    print(json.dumps(result, indent=2))
    
    print("\n==== REASONING LOG ====")
    print(get_last_reasoning())

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(test_run())
