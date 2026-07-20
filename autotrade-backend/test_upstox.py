import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "crawler"))
load_dotenv(".env")
from utils.config import settings

# force upstox auth token to load from env
settings.UPSTOX_ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN")

from crawler.upstox_data import get_historical, get_ltp, get_company_profile

async def main():
    print(f"Authenticated: {settings.upstox_authenticated}")
    print("Testing LTP for RELIANCE...")
    ltp = await get_ltp("RELIANCE")
    print(f"LTP: {ltp}")
    
    print("Testing Profile for RELIANCE...")
    prof = await get_company_profile("RELIANCE")
    print(f"Profile: {prof.get('company_name', 'Not Found')}")

if __name__ == "__main__":
    asyncio.run(main())
