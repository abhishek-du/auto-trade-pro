import asyncio
import json
import logging
from engine.agent.decision_engine import llm_tooluse_candidate

# Suppress debug logs for cleaner output
logging.basicConfig(level=logging.WARNING)

class MockCandidate:
    strategy = "EAGLE_EYE_SIGNAL"
    risk_reward = 2.5
    hub_subscores = {"technical": 70, "news": 60, "sector": 50, "macro": 50, "earnings": 60, "fundamental": 60, "options": 0}
    reasons = []
    
    def __init__(self, entry, brief):
        self.entry = entry
        self.stop = entry * 0.95
        self.target = entry * 1.15
        self.chart_brief = brief

class MockDecision:
    action = "BUY"
    regime = "NORMAL"
    master_score = 65
    confidence = 65
    confidence_factors = {}

async def run_stock(symbol, entry, brief):
    cand = MockCandidate(entry, brief)
    dec = MockDecision()
    print(f"\n--- Testing: {symbol} ---")
    try:
        result = await llm_tooluse_candidate(symbol, cand, dec)
        print(f"VERDICT for {symbol}:", result.get("verdict") if result else "null")
        if result:
            print("Reasoning Bull:", result.get("bull"))
            print("Reasoning Bear:", result.get("bear"))
            print("Tools used count:", len(result.get("tools_used", [])))
    except Exception as e:
        print(f"Error testing {symbol}: {e}")

async def main():
    stocks = [
        ("HPL.NS", 355, "HPL ELECTRIC strong bounce back from support level cmp 355"),
        ("WALCHANNAG.NS", 245, "WALCHAND nagar industries is looking good cmp 245 Strong reversal on daily chart Breakout on hourly chart"),
        ("MARINE.NS", 256, "Marine electrical showing strong reversal CMP 256 Downside risk 246"),
        ("PREMEXPLN.NS", 698, "Apollo micro promoter bought Premier explosive at 698 Don't worry, keep holding for swing")
    ]
    for sym, entry, brief in stocks:
        await run_stock(sym, entry, brief)

if __name__ == "__main__":
    import os
    os.environ["PYTHONPATH"] = "."
    asyncio.run(main())
