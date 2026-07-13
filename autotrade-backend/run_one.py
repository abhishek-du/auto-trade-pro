import asyncio
import json
import logging
from engine.agent.decision_engine import llm_tooluse_candidate
from utils.llm import get_last_reasoning

logging.basicConfig(level=logging.DEBUG)

class MockCandidate:
    strategy = "EAGLE_EYE_SIGNAL"
    entry = 355
    stop = 330
    target = 400
    risk_reward = 2.5
    hub_subscores = {"technical": 70, "news": 60, "sector": 50, "macro": 50, "earnings": 60, "fundamental": 60, "options": 0}
    chart_brief = "HPL ELECTRIC strong bounce back from support level cmp 355"
    reasons = []

class MockDecision:
    action = "BUY"
    regime = "NORMAL"
    master_score = 65
    confidence = 65
    confidence_factors = {}

async def test_run():
    symbol = "HPL.NS"
    cand = MockCandidate()
    dec = MockDecision()
    result = await llm_tooluse_candidate(symbol, cand, dec)
    print("\n==== FINAL RESULT ====")
    print(json.dumps(result, indent=2) if result else "null")

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(test_run())
