import asyncio
import datetime
from crawler.zerodha_client import get_kite
from crawler.zerodha_historical import fetch_historical_data

async def test_kite():
    kite = await get_kite()
    if not kite:
        print("Kite not connected")
        return
        
    print(f"Kite profile: {kite.profile()}")
    
    # RELIANCE token is usually 738561
    now = datetime.datetime.now()
    from_date = now - datetime.timedelta(days=1)
    
    try:
        data = kite.historical_data(738561, from_date, now, "5minute")
        print(f"Got {len(data)} candles.")
        if data:
            print(f"Latest candle: {data[-1]}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test_kite())
