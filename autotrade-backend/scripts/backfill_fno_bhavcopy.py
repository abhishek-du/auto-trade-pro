"""One-time/continuable backfill: historical NSE F&O bhavcopy → OptionContractSnapshot.

Walks every weekday in a date range, downloads the F&O bhavcopy (UDiFF or legacy
format, auto-selected by date), computes IV + Greeks *as of that day*, and persists
index option snapshots + ATM-IV history.

Usage (from autotrade-backend/):
    .venv/bin/python scripts/backfill_fno_bhavcopy.py --from 2022-01-01 --to 2026-06-14
    .venv/bin/python scripts/backfill_fno_bhavcopy.py --from 2024-07-01 --to 2024-07-31 \
        --symbols NIFTY,BANKNIFTY --sleep 2.0

Behaviour
---------
* Weekends are skipped locally; market holidays return HTTP 404 and are skipped.
* Resumable & idempotent: a date whose canonical snapshot already exists in the DB
  is skipped (``--resume``, on by default), and the persist itself uses
  INSERT ... ON CONFLICT DO NOTHING, so re-running never duplicates.
* Each date is committed independently, so Ctrl-C is safe — just re-run to continue.
* A failed date is logged and skipped; the run continues.
* A polite delay between dates keeps NSE's bot-detection happy (each fetch also
  warms up the homepage first).

Volume / time
-------------
~3 index underlyings × hundreds of strikes × ~250 trading days/year → low-millions
of rows for a multi-year range. Network round-trips dominate; expect a few seconds
per date.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys

# Add backend root to path so imports work when running from scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from crawler.bhavcopy_fno import fetch_fno_bhavcopy, _DEFAULT_SYMBOLS  # noqa: E402
from db.database import AsyncSessionLocal  # noqa: E402
from db.models import OptionContractSnapshot  # noqa: E402
from engine.fno.historical_ingest import persist_bhavcopy, _snapshot_at  # noqa: E402
from utils.logger import logger  # noqa: E402


def _iter_weekdays(start: datetime.date, end: datetime.date):
    """Yield each weekday (Mon–Fri) from start to end inclusive."""
    d = start
    one = datetime.timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:        # 0=Mon .. 4=Fri
            yield d
        d += one


async def _already_done(session, trade_date: datetime.date) -> bool:
    """True if a snapshot for this date's canonical instant already exists."""
    hit = (await session.execute(
        select(OptionContractSnapshot.id)
        .where(OptionContractSnapshot.snapshot_at == _snapshot_at(trade_date))
        .limit(1)
    )).first()
    return hit is not None


async def run(args: argparse.Namespace) -> None:
    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end)
    if start > end:
        raise SystemExit(f"--from {start} is after --to {end}")

    symbols = (frozenset(s.strip().upper() for s in args.symbols.split(",") if s.strip())
               if args.symbols else _DEFAULT_SYMBOLS)
    days = list(_iter_weekdays(start, end))
    if args.limit:
        days = days[: args.limit]

    logger.info(f"[backfill-fno] {start}..{end} | {len(days)} weekdays | symbols={sorted(symbols)} "
                f"| resume={not args.no_resume} | sleep={args.sleep}s")

    n_done = n_skip_resume = n_holiday = n_err = total_rows = 0

    async with AsyncSessionLocal() as session:
        for i, d in enumerate(days, 1):
            try:
                if not args.no_resume and await _already_done(session, d):
                    n_skip_resume += 1
                    continue

                rows = await fetch_fno_bhavcopy(d, symbols)
                if rows is None:
                    n_holiday += 1
                    continue

                summ = await persist_bhavcopy(session, rows, d, commit=True)
                total_rows += summ.rows_written
                n_done += 1
            except KeyboardInterrupt:
                logger.warning("[backfill-fno] interrupted — committed dates are safe; re-run to resume")
                break
            except Exception as exc:
                n_err += 1
                logger.warning(f"[backfill-fno] {d} failed: {type(exc).__name__}: {exc}")
                await session.rollback()

            if i % args.progress_every == 0 or i == len(days):
                logger.info(f"[backfill-fno] {i}/{len(days)} | done={n_done} "
                            f"resume-skip={n_skip_resume} holiday={n_holiday} err={n_err} "
                            f"rows={total_rows:,}")

            if args.sleep:
                await asyncio.sleep(args.sleep)

    logger.info(f"[backfill-fno] FINISHED | persisted {n_done} dates ({total_rows:,} rows) | "
                f"resume-skipped {n_skip_resume} | holidays {n_holiday} | errors {n_err}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill NSE F&O bhavcopy into OptionContractSnapshot")
    p.add_argument("--from", dest="start", required=True, help="start date YYYY-MM-DD (inclusive)")
    p.add_argument("--to", dest="end", required=True, help="end date YYYY-MM-DD (inclusive)")
    p.add_argument("--symbols", default="", help="comma list (default NIFTY,BANKNIFTY,FINNIFTY)")
    p.add_argument("--sleep", type=float, default=1.5, help="seconds between dates (default 1.5)")
    p.add_argument("--no-resume", action="store_true", help="re-process dates already in the DB")
    p.add_argument("--limit", type=int, default=0, help="cap number of weekdays (testing)")
    p.add_argument("--progress-every", type=int, default=10, help="log progress every N dates")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(run(_parse_args()))
