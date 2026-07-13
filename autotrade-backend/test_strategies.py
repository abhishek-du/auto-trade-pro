import asyncio
import json
from datetime import datetime
from utils.logger import logger

# Mock classes for testing
class MockSession:
    async def commit(self): pass
    async def flush(self): pass
    async def execute(self, *args, **kwargs):
        class MockResult:
            def first(self): return ("NEUTRAL", "No particular reason.")
            def scalars(self): 
                class MockScalars:
                    def all(self): return []
                return MockScalars()
            def fetchall(self): return []
        return MockResult()
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

class MockCandidate:
    strategy = "PULLBACK_LONG"
    entry = 100.0
    stop = 95.0
    target = 110.0
    risk_reward = 2.0
    hub_subscores = {"technical": 10, "news": 5}
    chart_brief = "Price bouncing off EMA50."

class MockDecision:
    action = "BUY"
    regime = "MODERATE_BULL"
    master_score = 85
    confidence = 80
    confidence_factors = {}

async def test_all():
    session = MockSession()
    
    print("\n--- Testing Strategy 1: Multi-Agent Debate ---")
    try:
        from engine.agent.decision_engine import llm_debate_candidate
        res1 = await llm_debate_candidate("RELIANCE", MockCandidate(), MockDecision())
        print(f"Debate Result: {json.dumps(res1, indent=2)}")
    except Exception as e:
        print(f"Error in Strategy 1: {e}")

    print("\n--- Testing Strategy 2a: Earnings Tone Comparison ---")
    try:
        from engine.earnings_summarizer import summarize_transcript
        res2a = await summarize_transcript(
            transcript_text="We see massive uncertainty and strong headwinds going into Q3 due to supply constraints.",
            symbol="TCS.NS", company_name="TCS", quarter="Q2 FY25", call_date="2025-10-15",
            pdf_url="", source="Test"
        )
        print(f"Tone vs Last Qtr: {getattr(res2a, 'tone_reason', 'N/A')}")
    except Exception as e:
        print(f"Error in Strategy 2a: {e}")

    print("\n--- Testing Strategy 2b: Supply Chain Shock ---")
    try:
        from engine.agent.unstructured_alpha import analyze_supply_chain_shock
        res2b = await analyze_supply_chain_shock("Apple", "Apple to slash iPhone 16 production by 30%", "Suppliers worldwide to be impacted.")
        print(f"Supply Chain Impact: {json.dumps(res2b, indent=2)}")
    except Exception as e:
        print(f"Error in Strategy 2b: {e}")

    print("\n--- Testing Strategy 4: Continuous Self-Reflection ---")
    try:
        from tasks.india_tasks import _india_weekend_reflection
        # This queries the DB for actual trades. If DB is empty, it returns safely.
        await _india_weekend_reflection()
        print("Self-Reflection loop completed (check logs for generated rules).")
    except Exception as e:
        print(f"Error in Strategy 4: {e}")

    print("\n--- Testing Strategy 5: Event Arbitrage ---")
    try:
        from engine.agent.event_arbitrage import evaluate_news_flash
        print("Sending massive shock event to Event Arbitrage engine...")
        await evaluate_news_flash(
            "HDFC Bank CEO suddenly resigns amid massive accounting fraud allegations",
            "Trading halted as massive discrepancies found in Q1 reports.",
            "Bloomberg", session
        )
        print("Event Arbitrage evaluation finished (check logs for HIGH SURPRISE EVENT).")
    except Exception as e:
        print(f"Error in Strategy 5: {e}")

if __name__ == "__main__":
    asyncio.run(test_all())
