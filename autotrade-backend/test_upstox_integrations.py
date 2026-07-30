import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def run_tests():
    from crawler.upstox_market import get_market_quote, get_ltp
    from crawler.upstox_historical import get_historical_candles, get_intraday_candles
    from crawler.upstox_data import get_market_intel
    from crawler.upstox_websocket import start_upstox_websocket, subscribe_symbol, get_live_tick
    from crawler.live_prices import get_price
    
    symbol = "TCS.NS"
    print(f"\n--- Testing Upstox Market API for {symbol} ---")
    quote = await get_market_quote(symbol)
    print(f"Market Quote: {bool(quote)} (Keys: {list(quote.keys())[:5]}...)")
    ltp = await get_ltp(symbol)
    print(f"LTP: {ltp}")
    
    print(f"\n--- Testing Upstox Historical API for {symbol} ---")
    hist = await get_historical_candles(symbol, interval="day")
    print(f"Historical (Day) Candles Count: {len(hist)}")
    if hist:
        print(f"Sample: {hist[0]}")
    
    intra = await get_intraday_candles(symbol, interval="1minute")
    print(f"Intraday (1min) Candles Count: {len(intra)}")
    
    print(f"\n--- Testing Upstox Market Intel with Greeks for {symbol} ---")
    intel = await get_market_intel(symbol, include_greeks=True)
    print(f"Market Intel Keys: {list(intel.keys())}")
    if "greeks" in intel:
        print("Greeks successfully fetched!")
        
    print(f"\n--- Testing Upstox Websocket Integration ---")
    from crawler.upstox_data import get_instrument_key
    ikey = await get_instrument_key(symbol)
    
    if ikey:
        print(f"Starting websocket for {symbol} ({ikey})")
        start_upstox_websocket({symbol: ikey})
        
        # Wait for a few seconds to let it connect and receive data
        print("Waiting 5 seconds for websocket tick...")
        await asyncio.sleep(5)
        
        tick = get_live_tick(symbol)
        print(f"Direct Websocket Tick: {tick}")
        
        price = get_price(symbol)
        print(f"Live_prices.py get_price(): {price}")
    else:
        print("Could not get instrument key for websocket test.")

if __name__ == "__main__":
    asyncio.run(run_tests())
