#!/usr/bin/env python3
"""Populate / refresh the spreadsheet trade journal on demand.

Idempotent — appends new trades, updates ones that have closed. First run
back-fills the full trade history into the configured backend (local .xlsx by
default; Google Sheets when SHEET_LOG_BACKEND="google").

Usage:
    python3 scripts/sync_trade_journal.py               # sync everything
    python3 scripts/sync_trade_journal.py --limit 50    # only the newest 50 trades
    python3 scripts/sync_trade_journal.py --clear       # wipe sheet, re-sync all

--clear does two passes:
  Pass 1 (LLM OFF)  — clears the sheet, appends all trades as rows (no Groq calls,
                       fast). Closed trades land as OPEN status at this stage.
  Pass 2 (LLM ON)   — immediately re-syncs: detects the newly-closed rows, writes
                       Status/P&L/Duration, and generates AI postmortem notes via
                       Groq (paced at 24 RPM to respect the 30 RPM / 1K RPD limits).
"""
import argparse
import asyncio
import os
import sys
import time

# Add backend root to path so imports work when running from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _clear_sheet():
    """Clear data rows from the Google Sheet (keeps the header in row 1)."""
    from integrations.sheet_logger import GoogleSheetsSink
    sink = GoogleSheetsSink()
    ws, _ = sink._worksheet()
    total = len(ws.get_all_values())
    if total > 1:
        ws.delete_rows(2, total)   # delete row 2 through last row, keep header
    sink._row_count = 1            # header only
    print(f"  Sheet cleared ({total - 1} data rows removed).")


def _make_session_factory(raw_url: str):
    from contextlib import asynccontextmanager
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    @asynccontextmanager
    async def _session():
        # Session-mode pooler (port 5432) avoids DuplicatePreparedStatement errors
        # that transaction-mode pooler (port 6543) causes with asyncpg.
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

    return _session


async def _run_sync(session_factory, limit: int) -> dict:
    from integrations.sheet_logger import sync_journal
    async with session_factory() as session:
        return await sync_journal(session, limit=limit)


async def main(limit: int, clear: bool):
    from utils.config import settings

    # Use session-mode pooler (port 5432) instead of transaction-mode (port 6543)
    raw_url = settings.DATABASE_URL.replace(":6543/", ":5432/")
    session_factory = _make_session_factory(raw_url)

    print(f"Backend: {settings.SHEET_LOG_BACKEND}  "
          f"(LLM notes: {settings.SHEET_LOG_USE_LLM})")
    if settings.SHEET_LOG_BACKEND == "local":
        print(f"File:    {settings.SHEET_LOG_LOCAL_PATH}")
    else:
        print(f"Sheet:   {settings.GOOGLE_SHEETS_ID}")

    if clear:
        # ── Pass 1: clear + bulk-fill without LLM ────────────────────────────
        print("\n── Pass 1: clearing sheet and filling rows (LLM OFF) ──")
        settings.SHEET_LOG_USE_LLM = False

        if settings.SHEET_LOG_BACKEND == "google":
            _clear_sheet()
        else:
            import os as _os
            path = settings.SHEET_LOG_LOCAL_PATH
            if _os.path.exists(path):
                _os.remove(path)
                print(f"  Deleted local file: {path}")

        result1 = await _run_sync(session_factory, limit)
        paper1  = result1.get("paper_trades", 0)
        agent1  = result1.get("agent_trades", 0)
        appended1 = result1.get("appended", 0)
        print(f"  Done — {appended1} rows written  "
              f"({paper1} paper + {agent1} agent)")

        # ── Pass 2: close closed trades + AI postmortem notes via Groq ───────
        # Groq: 30 RPM / 1K RPD / 12K TPM.  _groq_sync paces at 24 RPM (2.5 s/call).
        # Expect ~1 call per closed trade → ~56 calls ≈ 140 s total.
        print("\n── Pass 2: updating closed trades with expert analysis (LLM ON) ──")
        print("  Groq pacing: 24 RPM (2.5 s/call) — Groq 30 RPM / 1K RPD / 12K TPM")
        settings.SHEET_LOG_USE_LLM = True

        t0 = time.monotonic()
        result2 = await _run_sync(session_factory, limit)
        elapsed = time.monotonic() - t0
        updated2 = result2.get("updated", 0)
        print(f"  Done — {updated2} rows closed/updated  ({elapsed:.0f}s)")

    else:
        # ── Normal incremental sync ───────────────────────────────────────────
        result = await _run_sync(session_factory, limit)
        paper  = result.get("paper_trades", 0)
        agent  = result.get("agent_trades", 0)
        appended = result.get("appended", 0)
        updated  = result.get("updated", 0)
        print(f"Synced — {appended} new, {updated} updated  "
              f"({paper} paper + {agent} agent)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500,
                    help="max newest trades to sync")
    ap.add_argument("--clear", action="store_true",
                    help="wipe the sheet/file before syncing (use when schema changes)")
    args = ap.parse_args()
    asyncio.run(main(args.limit, args.clear))
