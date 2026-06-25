"""Restart the Zerodha ticker with current open positions subscribed.

Run from autotrade-backend/ with .venv/bin/python scripts/restart_ticker.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    from sqlalchemy import text
    from db.database import AsyncSessionLocal

    print("Fetching open positions from DB...")
    async with AsyncSessionLocal() as sess:
        r = await sess.execute(text("SELECT DISTINCT symbol FROM open_positions"))
        syms = {row[0] for row in r.fetchall()}
    print(f"Open positions: {syms}")

    from crawler.zerodha_ticker import set_open_position_symbols
    set_open_position_symbols(syms)

asyncio.run(main())

print("Pre-loading instruments...")
from crawler.zerodha_market import hydrate_tokens_from_db
import asyncio
asyncio.run(hydrate_tokens_from_db())

print("Stopping existing ticker...")
from crawler.zerodha_ticker import stop_kite_ticker, start_kite_ticker
stop_kite_ticker()

import time
time.sleep(2)

print("Starting ticker with open positions...")
ok = start_kite_ticker()
print(f"Ticker started: {ok}")

time.sleep(5)
from crawler.zerodha_ticker import CONNECTED, _OPEN_POSITION_SYMBOLS
print(f"Connected: {CONNECTED}")
print(f"Open position symbols queued: {_OPEN_POSITION_SYMBOLS}")
print("Done — ticker is running in background thread. This process can exit.")
