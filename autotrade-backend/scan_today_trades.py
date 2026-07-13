import asyncio
import datetime
import logging
from db.database import AsyncSessionLocal
from db.models import OpenPosition
from engine.agent.decision_engine import llm_tooluse_candidate

logging.basicConfig(level=logging.WARNING)

class MockDecision:
    def __init__(self, action, conf):
        self.action = action
        self.confidence = conf
        self.regime = "BEAR_TRENDING"
        self.master_score = -60 if action == "SELL" else 60
        self.confidence_factors = {}

class MockCand:
    def __init__(self, side):
        self.strategy = "HUB_SIGNAL"
        self.side = side
        self.reasons = []
        self.entry = 0
        self.stop = 0
        self.target = 0
        self.risk_reward = 2.0
        self.hub_subscores = {"technical": 20, "news": 50, "sector": 40, "macro": 50, "earnings": 50, "fundamental": 50, "options": 0}
        self.chart_brief = "Testing mock candidate"

async def process_symbol(symbol, original_direction):
    print(f"\n{'='*50}\nANALYZING {symbol} (Original Trade: {original_direction})\n{'='*50}")
    dec = MockDecision(original_direction.name if hasattr(original_direction, 'name') else original_direction, 60)
    cand = MockCand(original_direction.name if hasattr(original_direction, 'name') else original_direction)
    try:
        result = await llm_tooluse_candidate(symbol, cand, dec)
        if result:
            print(f"VERDICT: {result.get('verdict')}")
            print(f"Confidence: {result.get('confidence')}%")
            print(f"Bull Case: {result.get('bull')}")
            print(f"Bear Case: {result.get('bear')}")
            print(f"Key Risk: {result.get('key_risk')}")
            if "thought" in result:
                print("\n--- DEBATE ---")
                print(result["thought"])
        else:
            print("Agent failed to reach a decision.")
    except Exception as e:
        print(f"Error scanning {symbol}: {e}")

async def main():
    symbols_to_test = ["SUZLON.NS", "KOTAKBANK.NS", "SJVN.NS", "DIXON.NS"]
    directions = ["SELL", "SELL", "SELL", "BUY"]
    
    for sym, dir in zip(symbols_to_test, directions):
        await process_symbol(sym, dir)

if __name__ == '__main__':
    import os
    os.environ['PYTHONPATH'] = '.'
    asyncio.run(main())
