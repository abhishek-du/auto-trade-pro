import asyncio
import json
import logging
logging.basicConfig(level=logging.DEBUG)

from engine.agent.decision_engine import llm_tooluse_candidate
from utils.llm import get_last_reasoning

class MockCandidate:
    strategy = "EAGLE_EYE_SIGNAL"
    entry = 2035
    stop = 1700
    target = 2900
    risk_reward = 2.58
    hub_subscores = {"technical": 70, "news": 60, "sector": 50, "macro": 50, "earnings": 60, "fundamental": 60, "options": 0}
    chart_brief = "Eagle Eyes setup: Sasken tech CMP 2035 Accumulation for 2500-2900 Long term Work in Cybersecurity Downside risk 1700"
    reasons = []

class MockDecision:
    action = "BUY"
    regime = "NORMAL"
    master_score = 65
    confidence = 65
    confidence_factors = {}

async def test_run():
    symbol = "SASKEN.NS"
    cand = MockCandidate()
    dec = MockDecision()
    
    print("Testing Eagle Eye Signal for", symbol)
    result = await llm_tooluse_candidate(symbol, cand, dec)
    print("\n==== FINAL RESULT ====")
    print(json.dumps(result, indent=2))
    
    print("\n==== LLM LAST REASONING LOG ====")
    print(get_last_reasoning())

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(test_run())
