import asyncio
import json
from sqlalchemy import select
from db.database import AsyncSessionLocal
from engine.hub_universe import rebuild_hub_universe, get_hub_universe
from engine.intelligence_hub import score_universe, build_master_context
from db.models import MasterIntelligenceScore

async def test_full_pipeline_flow():
    print("=== FULL PIPELINE END-TO-END TEST ===\n")
    
    async with AsyncSessionLocal() as session:
        # Step 1: Universe Selection
        print("1. Rebuilding Hub Universe (Top 5 only)...")
        # We use a very high turnover filter just to get the absolute most liquid (e.g. Reliance, HDFC)
        universe_summary = await rebuild_hub_universe(session, top_n=5, min_turnover_cr=100.0)
        symbols = universe_summary["top"]
        print(f"   Universe Selected: {symbols}")
        
        # We need the full '.NS' names for scoring
        full_symbols = await get_hub_universe(session)
        print(f"   Resolved Symbols: {full_symbols}")
        
        # Create a mock agent portfolio for the test
        from collections import namedtuple
        MockAgent = namedtuple('MockAgent', ['id', 'equity', 'cash_available', 'open_positions', 'open_positions_value', 'margin_used', 'cash'])
        mock_agent = MockAgent(id=1, equity=1000000.0, cash_available=1000000.0, open_positions={}, open_positions_value=0.0, margin_used=0.0, cash=1000000.0)

        # Step 2: Build Master Context (Offline)
        print("\n2. Building Master Context...")
        ctx = await build_master_context(mock_agent, session, full_symbols)
        print(f"   Macro Nifty Regime: {ctx.macro.nifty_regime}")
        print(f"   VIX: {ctx.macro.india_vix}")
        
        # Step 3: Run the Multi-Strategy Engine
        print("\n3. Scoring Universe (Offline Technical Engine)...")
        results = await score_universe(full_symbols, ctx, session, timeframe="1d")
        
        print(f"   Successfully scored {len(results)} symbols.")
        
        # Wait a moment to ensure DB commits are processed if done asynchronously, 
        # but score_universe usually persists them.
        await asyncio.sleep(2)
        
        # Step 4: Verify Database Persistence and JSON Structure
        print("\n4. Verifying DB Persistence (Immutable Decision Snapshot)...")
        stmt = (
            select(MasterIntelligenceScore)
            .where(MasterIntelligenceScore.symbol.in_(full_symbols))
            .order_by(MasterIntelligenceScore.scored_at.desc())
            .limit(len(full_symbols))
        )
        db_results = (await session.execute(stmt)).scalars().all()
        
        print(f"   Found {len(db_results)} records in DB.")
        
        if not db_results:
            print("   ❌ FAILED: No scores were saved to the database.")
            return

        for row in db_results:
            print(f"\n--- Output for {row.symbol} ---")
            print(f"   Master Score: {row.master_score}")
            print(f"   Signal: {row.signal}")
            
            reasoning = row.reasoning
            if isinstance(reasoning, str):
                reasoning = json.loads(reasoning)
                
            avail_logs = reasoning.get("availability_logs", {})
            explain = reasoning.get("explainability_versioning", {})
            weights = reasoning.get("weighted_contribution", {})
            
            print("   Availability Logs:")
            for k, v in avail_logs.items():
                print(f"      - {k}: {v}")
                
            print("   Weighted Contribution Decomposition:")
            for k, v in weights.items():
                print(f"      - {k}: {v}")
                
            print("   Explainability Versioning:")
            for k, v in explain.items():
                print(f"      - {k}: {v}")

            # Verification assertions
            if "technical" not in avail_logs:
                print("   ❌ FAILED: Missing technical availability log.")
            elif "strategy_version" not in explain:
                print("   ❌ FAILED: Missing explainability versioning.")
            else:
                print("   ✅ PASSED: Immutable Snapshot perfectly formatted.")

if __name__ == "__main__":
    asyncio.run(test_full_pipeline_flow())
