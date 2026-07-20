import asyncio
from db.database import AsyncSessionLocal
from engine.intelligence_hub import build_master_context, score_symbol
from crawler.price_feed import get_latest_candles
import pandas as pd
import json

class DummyPortfolio:
    equity = 1000000.0
    cash = 1000000.0
    open_positions = {}

async def dry_run():
    print("Initiating Dry Run for EMMVEE.NS to verify JSON structure...\n")
    symbol = "EMMVEE.NS"
    
    async with AsyncSessionLocal() as session:
        # Build live context
        ctx = await build_master_context(DummyPortfolio(), session, [symbol])
        
        # Get candles
        candles = await get_latest_candles(symbol, "1d", 300, session)
        if not candles:
            print("No candles found.")
            return
            
        data = [{
            "timestamp": c.timestamp, "open": c.open, "high": c.high,
            "low": c.low, "close": c.close, "volume": c.volume
        } for c in candles]
        
        df = pd.DataFrame(data)
        df.sort_values("timestamp", ascending=True, inplace=True)
        df.reset_index(drop=True, inplace=True)
        
        # Score
        res = await score_symbol(symbol, df, ctx, session, swing_mode=True)
        
        # Print only the requested keys to prove they exist
        output = {
            "symbol": res.symbol,
            "master_score": res.master_score,
            "availability_logs": res.reasoning.get("availability_logs"),
            "explainability_versioning": res.reasoning.get("explainability_versioning")
        }
        
        print(json.dumps(output, indent=2))

if __name__ == "__main__":
    asyncio.run(dry_run())
