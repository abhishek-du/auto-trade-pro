#!/usr/bin/env python3
"""Populate / refresh the spreadsheet trade journal on demand.

Idempotent — appends new trades, updates ones that have closed. First run
back-fills the full trade history into the configured backend (local .xlsx by
default; Google Sheets when SHEET_LOG_BACKEND="google").

Usage:
    python3 scripts/sync_trade_journal.py               # sync everything
    python3 scripts/sync_trade_journal.py --limit 50    # only the newest 50 trades
    python3 scripts/sync_trade_journal.py --clear       # wipe sheet, re-sync all
"""
import argparse
import asyncio
import os
import sys

# Add backend root to path so imports work when running from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _clear_sheet():
    """Clear data rows from the Google Sheet (keeps the header in row 1)."""
    from integrations.sheet_logger import GoogleSheetsSink, HEADERS
    sink = GoogleSheetsSink()
    ws, _ = sink._worksheet()
    total = len(ws.get_all_values())
    if total > 1:
        ws.delete_rows(2, total)   # delete row 2 through last row, keep header
    sink._row_count = 1            # header only
    print(f"Sheet cleared ({total - 1} data rows removed).")


async def main(limit: int, clear: bool):
    from integrations.sheet_logger import sync_journal
    from utils.config import settings

    print(f"Backend: {settings.SHEET_LOG_BACKEND}  "
          f"(LLM notes: {settings.SHEET_LOG_USE_LLM})")
    if settings.SHEET_LOG_BACKEND == "local":
        print(f"File:    {settings.SHEET_LOG_LOCAL_PATH}")
    else:
        print(f"Sheet:   {settings.GOOGLE_SHEETS_ID}")

    if clear:
        if settings.SHEET_LOG_BACKEND == "google":
            _clear_sheet()
        else:
            import os as _os
            path = settings.SHEET_LOG_LOCAL_PATH
            if _os.path.exists(path):
                _os.remove(path)
                print(f"Deleted local file: {path}")
        print("Cleared. Re-syncing all trades...")

    # Use Supabase session-mode pooler (port 5432) instead of transaction-mode
    # (port 6543). Transaction mode eats asyncpg's DEALLOCATE messages, leaving
    # stale prepared statements on backend connections — the next connection
    # collides on __asyncpg_stmt_1__. Session mode gives each client a dedicated
    # backend for the life of the connection, so prepared statements work correctly.
    from contextlib import asynccontextmanager
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    raw_url = settings.DATABASE_URL.replace(":6543/", ":5432/")

    @asynccontextmanager
    async def session_mode_session():
        engine = create_async_engine(
            make_url(raw_url),
            poolclass=NullPool,
            connect_args={"statement_cache_size": 0},
        )
        Session = async_sessionmaker(
            bind=engine, class_=AsyncSession,
            expire_on_commit=False, autocommit=False, autoflush=False,
        )
        try:
            async with Session() as session:
                yield session
        finally:
            await engine.dispose()

    async with session_mode_session() as session:
        result = await sync_journal(session, limit=limit)
    print(f"Synced — paper_trades: {result.get('paper_trades', 0)}, "
          f"agent_trades: {result.get('agent_trades', 0)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500, help="max newest trades to sync")
    ap.add_argument("--clear", action="store_true", help="wipe the sheet/file before syncing (use when schema changes)")
    args = ap.parse_args()
    asyncio.run(main(args.limit, args.clear))
