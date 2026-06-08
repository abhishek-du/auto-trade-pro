#!/usr/bin/env python3
"""Populate / refresh the spreadsheet trade journal on demand.

Idempotent — appends new trades, updates ones that have closed. First run
back-fills the full trade history into the configured backend (local .xlsx by
default; Google Sheets when SHEET_LOG_BACKEND="google").

Usage:
    python3 scripts/sync_trade_journal.py            # sync everything
    python3 scripts/sync_trade_journal.py --limit 50 # only the newest 50 trades
"""
import argparse
import asyncio
import os
import sys

# Add backend root to path so imports work when running from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(limit: int):
    from tasks._db import celery_session
    from integrations.sheet_logger import sync_journal
    from utils.config import settings

    print(f"Backend: {settings.SHEET_LOG_BACKEND}  "
          f"(LLM notes: {settings.SHEET_LOG_USE_LLM})")
    if settings.SHEET_LOG_BACKEND == "local":
        print(f"File:    {settings.SHEET_LOG_LOCAL_PATH}")
    else:
        print(f"Sheet:   {settings.GOOGLE_SHEETS_ID}")

    async with celery_session() as session:
        result = await sync_journal(session, limit=limit)
    print("Result:", result)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500, help="max newest trades to sync")
    args = ap.parse_args()
    asyncio.run(main(args.limit))
