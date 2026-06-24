import sys
import os
sys.path.append(os.path.abspath(os.getcwd()))

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from engine.hub_universe import rebuild_hub_universe, get_hub_universe
from db.database import AsyncSessionLocal
import pandas as pd

async def test_all():
    async with AsyncSessionLocal() as session:
        # 1. Test Rebuild Universe
        print("Testing rebuild_hub_universe...")
        summary = await rebuild_hub_universe(session, top_n=10, min_turnover_cr=2.0)
        print("Universe Rebuilt:", summary)
        
        # 2. Get Universe
        universe = await get_hub_universe(session)
        print(f"Got {len(universe)} symbols from universe")
        
        if not universe:
            print("Universe empty!")
            return
            
        # 3. Test compute_indicators
        print("Testing compute_indicators...")
        from engine.indicators import compute_indicators
        from engine.candlestick_patterns import detect_candlestick_patterns
        
        # Create a mock df with 30 rows
        dates = pd.date_range("2026-06-01", periods=30)
        df = pd.DataFrame({
            "open": range(100, 130),
            "high": list(range(105, 134)) + [140], # last candle is a big bullish engulfing
            "low": range(95, 125),
            "close": list(range(102, 131)) + [138],
            "volume": [1000] * 30,
        }, index=dates)
        
        signals = compute_indicators(df)
        print("Indicators Computed!")
        print(f"Pivot: {signals.pivot}, Support 1: {signals.support_1}")
        print(f"Patterns: {signals.patterns}")
        
        # 4. Test deep_analysis build_trade_setup
        from engine.deep_analysis import build_trade_setup
        setup = build_trade_setup(signals, ltp=138.0, signal="BUY")
        print("Trade Setup Built!")
        print(setup.get('bullish', ''))

asyncio.run(test_all())
