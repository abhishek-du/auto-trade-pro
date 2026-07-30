import asyncio
import json
from crawler.upstox_data import get_key_ratios, get_shareholding, get_company_profile

async def test():
    symbol = "RELIANCE.NS"
    print("Testing Upstox APIs for", symbol)
    ratios = await get_key_ratios(symbol)
    share = await get_shareholding(symbol)
    prof = await get_company_profile(symbol)
    print("RATIOS:")
    print(json.dumps(ratios, indent=2))
    print("SHAREHOLDING:")
    print(json.dumps(share, indent=2))
    print("PROFILE:")
    print(json.dumps(prof, indent=2))

if __name__ == "__main__":
    asyncio.run(test())
