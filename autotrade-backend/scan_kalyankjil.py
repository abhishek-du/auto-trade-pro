import asyncio
import json
import logging
from db.database import AsyncSessionLocal
from engine.agent.decision_engine import llm_tooluse_candidate

logging.basicConfig(level=logging.DEBUG)

class MockDecision:
    def __init__(self, action, regime, score, conf):
        self.action = action
        self.regime = regime
        self.master_score = score
        self.confidence = conf
        self.confidence_factors = {}

async def main():
    symbol = "KALYANKJIL.NS"
    print(f"=== ANALYZING {symbol} ===")
    
    dec = MockDecision("HOLD", "BULL_TRENDING", 75.0, 50)
    
    class MockCand:
        strategy = "SWING_BREAKOUT"
        entry = 0 
        stop = 0
        target = 0
        risk_reward = 2.5
        hub_subscores = {"technical": 80, "news": 50, "sector": 60, "macro": 50, "earnings": 50, "fundamental": 50, "options": 0}
        reasons = []
        chart_brief = f"{symbol} Strong volume breakout candidate."
    
    cand = MockCand()
    result = await llm_tooluse_candidate(symbol, cand, dec)
    
    if result:
        print(f"VERDICT: {result.get('verdict')}")
        print(f"Confidence: {result.get('confidence')}%")
        print(f"Bull Case: {result.get('bull')}")
        print(f"Bear Case: {result.get('bear')}")
        print(f"Key Risk: {result.get('key_risk')}")
        print(f"Tools Used: {len(result.get('tools_used', []))} {result.get('tools_used')}")
        if "thought" in result:
            print("\n--- DEBATE / THOUGHT ---")
            print(result["thought"])
    else:
        print("Agent failed to reach a decision or timed out.")

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(main())
