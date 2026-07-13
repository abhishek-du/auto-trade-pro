import asyncio
import json
from engine.agent.decision_engine import llm_tooluse_candidate

class MockCandidate:
    strategy = "MOCK_TEST"
    entry = 3000
    stop = 2950
    target = 3100
    risk_reward = 2.0
    hub_subscores = {"technical": 90, "news": 80, "sector": 60, "macro": 50, "earnings": 40, "fundamental": 70, "options": 0}
    chart_brief = "Testing mock candidate"
    reasons = []

class MockDecision:
    action = "BUY"
    regime = "NORMAL"
    master_score = 85
    confidence = 85
    confidence_factors = {}

async def test_run():
    symbol = "RELIANCE.NS"
    cand = MockCandidate()
    dec = MockDecision()
    
    print("Starting LLM Tool Use Test for", symbol)
    result = await llm_tooluse_candidate(symbol, cand, dec)
    print("\n==== FINAL RESULT ====")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(test_run())
