import asyncio
from crawler.upstox_data import get_instrument_key, get_ltp

async def main():
    ikey = await get_instrument_key("RELIANCE")
    print(f"Instrument Key: {ikey}")
    ltp = await get_ltp("RELIANCE")
    print(f"LTP: {ltp}")

if __name__ == "__main__":
    asyncio.run(main())
