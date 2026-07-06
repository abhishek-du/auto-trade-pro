import asyncio
from engine.agent.decision_engine import apply_reasoning_gate, AgentDecisionOutput
from types import SimpleNamespace
from utils.config import settings

async def main():
    settings.AGENT_LLM_REASONING_ENABLED = True
    settings.AGENT_LLM_SHADOW_MODE = False
    
    cand = SimpleNamespace(
        strategy="MOMENTUM", 
        entry=100.0, 
        stop=90.0, 
        target=120.0, 
        risk_reward=2.0,
        hub_subscores={"technical": 80, "volume": 100, "sector": 50, "macro": 50},
        chart_brief="Bullish engulfing with RSI=65"
    )
    dec = AgentDecisionOutput(
        symbol="TCS.NS",
        action="BUY",
        confidence=80,
        regime="BULL",
        strategy="MOMENTUM",
        entry=100.0,
        stop=90.0,
        target=120.0,
        qty=10,
        risk_pct=1.0,
        risk_reward=2.0,
        master_score=75.0,
    )
    
    kept, reject_reason = await apply_reasoning_gate("TCS.NS", cand, dec)
    print("Kept:", kept is not None)
    print("Reject Reason:", reject_reason)
    
asyncio.run(main())
