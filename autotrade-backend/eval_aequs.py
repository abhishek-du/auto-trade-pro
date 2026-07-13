import asyncio
import json
import traceback
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from db.database import AsyncSessionLocal
from utils.logger import logger

async def test_aequs_eval():
    symbol = "AEQUS.NS"
    print(f"--- Initiating Evaluation for {symbol} ---")
    
    try:
        from crawler.live_prices import get_price
        # 1. Fetch Price
        tick = get_price(symbol)
        print(f"LIVE PRICE TICK: {tick}")
        
        # 2. Get Historical Candles via Database
        import pandas as pd
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                text("SELECT open, high, low, close, volume, timestamp FROM candles WHERE symbol = :sym ORDER BY timestamp DESC LIMIT 200"),
                {"sym": symbol}
            )).fetchall()
            if not rows:
                print("No candles found in DB for AEQUS.NS.")
                return
            df = pd.DataFrame([dict(r._mapping) for r in reversed(rows)])
        print(f"\nFetched {len(df)} daily candles via database.")
        
        if len(df) < 30:
            print("Not enough candles for technical analysis!")
            return

        from engine.indicators import compute_indicators
        signal = compute_indicators(df)
        print(f"TECHNICAL SIGNAL: Score={signal.composite_score}, RSI={signal.rsi}, MACD={signal.macd_cross}")
        
        # 3. Intelligence Hub Aggregation
        from engine.intelligence_hub import build_master_context, score_symbol
        from engine.agent.market_regime import get_market_regime
        async with AsyncSessionLocal() as session:
            regime = await get_market_regime(session)
            print(f"\nMARKET REGIME: {regime.state} (Score: {regime.score})")
            
            # This calls the hub
            class MockPortfolio:
                equity = 100000.0
                cash = 100000.0
                open_positions = {}
            
            ctx = await build_master_context(MockPortfolio(), session)
            hub_res = await score_symbol(symbol, df, ctx, session=session)
            print(f"\nINTELLIGENCE HUB SCORE: {hub_res.master_score} / 100")
            
            # 4. Agent Decision
            from engine.agent.decision_engine import llm_reason_candidate, llm_debate_candidate
            
            from engine.agent.setup_builder import build_breakout_setup, build_pullback_setup
            price = df.iloc[-1]['close']
            setup = build_breakout_setup(signal, price)
            if not setup:
                setup = build_pullback_setup(signal, price)
                
            if not setup:
                print("No technical setup could be built. Aborting LLM.")
                return
                
            class Candidate:
                strategy = setup['strategy']
                entry = setup['entry']
                stop = setup['stop']
                target = setup['target']
                risk_reward = setup['risk_reward']
                hub_subscores = {}
                chart_brief = f"RSI: {signal.rsi}, Trend: {signal.ema_trend}"
                
            candidate = Candidate()
            
            # Reason
            reasoning = await llm_reason_candidate(symbol, candidate, regime.state, session)
            print(f"\nLLM Reasoning:\n{json.dumps(reasoning, indent=2)}")
            
            # Debate
            class Decision:
                action = "BUY"
                regime = regime.state
                master_score = hub_res['master_score']
                confidence = 80
                confidence_factors = {}
            decision = Decision()
            
            debate = await llm_debate_candidate(symbol, candidate, decision)
            print(f"\nLLM Debate Output:\n{json.dumps(debate, indent=2)}")
            
            print("\n--- ALL DONE ---")
            
    except Exception as e:
        print(f"FAILED: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_aequs_eval())
