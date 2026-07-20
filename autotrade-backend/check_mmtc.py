import asyncio
from utils.config import settings
from kiteconnect import KiteConnect
from crawler.zerodha_market import get_kite_client
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import OpenPosition
import traceback

async def check_mmtc():
    # 1. Fetch DB Position
    async with AsyncSessionLocal() as session:
        query = select(OpenPosition).where(OpenPosition.symbol == "MMTC.NS")
        res = await session.execute(query)
        pos = res.scalar_one_or_none()
        if pos:
            print(f"--- DB POSITION FOR MMTC.NS ---")
            print(f"Entry Price: ₹{pos.entry_price}")
            print(f"Qty: {pos.size_units}")
            print(f"Stop Loss: ₹{pos.stop_loss}")
            print(f"Take Profit: ₹{pos.take_profit}")
        else:
            print("No open position found for MMTC.NS in DB.")

    # 2. Fetch Zerodha Live Price
    print("\n--- ZERODHA LIVE QUOTE ---")
    if settings.ZERODHA_ACCESS_TOKEN:
        try:
            kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
            kite.set_access_token(settings.ZERODHA_ACCESS_TOKEN)
            quotes = kite.quote(["NSE:MMTC"])
            data = quotes.get("NSE:MMTC", {})
            if data:
                last_price = data.get('last_price')
                ohlc = data.get('ohlc', {})
                print(f"Live Price: ₹{last_price}")
                print(f"Day Open: ₹{ohlc.get('open')} | High: ₹{ohlc.get('high')} | Low: ₹{ohlc.get('low')}")
                print(f"Volume: {data.get('volume')}")
                print(f"Net Change: {data.get('net_change')}%")
                
                # Compare
                if pos:
                    pnl = (last_price - pos.entry_price) * pos.size_units
                    print(f"Current Unrealized P&L (Live): ₹{pnl:.2f}")
            else:
                print("No data received for MMTC from Zerodha.")
        except Exception as e:
            print(f"Zerodha API Error: {e}")
            traceback.print_exc()
    else:
        print("Zerodha Access Token missing.")

if __name__ == "__main__":
    asyncio.run(check_mmtc())
