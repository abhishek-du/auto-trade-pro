import asyncio
from crawler.zerodha_market import get_live_prices

async def check():
    prices = await get_live_prices(["PPLPHARMA.NS"])
    print("PPLPHARMA Live Price:", prices.get("PPLPHARMA.NS"))

if __name__ == "__main__":
    asyncio.run(check())
