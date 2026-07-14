import asyncio
from sqlalchemy import select
from db.database import AsyncSessionLocal
from db.models import OpenPosition, KiteSession, NewsItem
from datetime import date, datetime
from kiteconnect import KiteConnect
from utils.config import settings
import logging

logging.basicConfig(level=logging.INFO)

async def check_live_situation():
    today = date(2026, 7, 14)
    today_dt = datetime(2026, 7, 14)
    async with AsyncSessionLocal() as session:
        # Get symbols
        query = select(OpenPosition).where(OpenPosition.opened_at >= today_dt)
        res = await session.execute(query)
        opos = res.scalars().all()
        symbols = list(set([t.symbol for t in opos]))
        
        print(f"Symbols to check: {symbols}\n")
        
        prices = {}
        if settings.ZERODHA_ACCESS_TOKEN:
            kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
            kite.set_access_token(settings.ZERODHA_ACCESS_TOKEN)
            instruments = [f"NSE:{sym.replace('.NS', '')}" for sym in symbols if "SILVER" not in sym]
            try:
                quotes = kite.quote(instruments)
                for ins, data in quotes.items():
                    sym = ins.replace('NSE:', '') + '.NS'
                    prices[sym] = data.get('last_price')
            except Exception as e:
                import traceback
                print(f"Kite fetch failed: {e}")
                traceback.print_exc()
        else:
            print("No active Zerodha session. Using current_price from DB.\n")
            for t in opos:
                prices[t.symbol] = t.current_price
                
        print("--- LIVE PRICES & SITUATION ---")
        for sym in symbols:
            lp = prices.get(sym, 'N/A')
            print(f"{sym}: Live Price = ₹{lp}")
            
        print("\n--- RECENT NEWS ---")
        for sym in symbols:
            base_sym = sym.split('.')[0].lower()
            query = select(NewsItem).limit(100)
            res = await session.execute(query)
            news = res.scalars().all()
            found = False
            for n in news:
                if base_sym in n.headline.lower():
                    print(f"[{sym}] {n.headline} ({n.source})")
                    found = True
            if not found:
                print(f"[{sym}] No recent news found.")

if __name__ == "__main__":
    asyncio.run(check_live_situation())
