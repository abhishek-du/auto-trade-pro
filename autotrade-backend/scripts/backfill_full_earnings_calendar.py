"""One-time overnight catch-up: scan EVERY real NSE+BSE equity symbol
(~10,197, from kite_instruments) for its next yfinance earnings date, and
persist each one as it's found.

Added 2026-07-28 (user request: "subah tk sb ho" -- full coverage by
morning). The daily celery task (tasks.seed_calendar_events) only scans a
~450-symbol rotating slice per day (~23-day full cycle) because that's what
safely fits inside a bounded daily time budget. A full pass at the
documented safe sequential rate (~0.25 req/sec) is ~11-12 hours, which has
no business inside a bounded celery task -- so this runs as a plain
background OS process instead, with no overall time limit, and commits
incrementally (per symbol, not once at the end) so an interruption partway
through doesn't lose all overnight progress.

Rate-limit backoff (added after a live run confirmed a fixed 4s delay is
NOT actually safe over a long sustained scan -- after ~3 hours / ~1,600
requests, yfinance started rejecting 100% of requests with "Too Many
Requests", and it did NOT self-clear while the script kept hammering it
every 4s. This version tracks consecutive rate-limit hits and, past a small
threshold, pauses for a real cooldown before resuming, instead of treating
a rate-limit exactly like "this ticker has no calendar data" and plowing
straight through.

Resumable: skips any symbol that already has a future-dated EARNINGS row
from a prior run of this script (or the daily task), so re-running after an
interruption picks up roughly where it left off instead of re-scanning
already-covered ground.

Usage:
    .venv/bin/python scripts/backfill_full_earnings_calendar.py
    (run detached / in background -- see the launching Bash call for how)
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import date

sys.path.insert(0, ".")

from utils.logger import logger  # noqa: E402

# After this many CONSECUTIVE rate-limit hits, pause for a real cooldown
# instead of continuing to hit yfinance every _YFINANCE_SAFE_DELAY_SEC --
# confirmed live that keeps making it worse, not better.
#
# Bumped 2026-07-29 ~05:47 IST: at threshold=5/cooldown=600s, two cooldown
# episodes back-to-back (05:20:33 and 05:30:51) both failed to clear the
# block -- resuming after the first 10-min cooldown got rate-limited again
# within seconds, on the very first symbols retried. Backing off sooner
# (lower threshold) and cooling down longer gives Yahoo's throttle more
# room to actually reset instead of getting re-triggered immediately.
_RATE_LIMIT_STREAK_THRESHOLD = 3
_RATE_LIMIT_COOLDOWN_SEC = 1200  # 20 min


async def main() -> None:
    from sqlalchemy import select
    from db.database import AsyncSessionLocal
    from db.models import MarketEvent
    from engine.calendar_engine import (
        _get_full_nse_bse_universe, _fetch_one_earnings_event, _YFINANCE_SAFE_DELAY_SEC,
    )

    start = time.monotonic()
    today = date.today()

    async with AsyncSessionLocal() as session:
        full_universe = await _get_full_nse_bse_universe(session)
        already_covered = set((await session.execute(
            select(MarketEvent.symbol).where(
                MarketEvent.event_type == "EARNINGS",
                MarketEvent.source == "YFINANCE",
                MarketEvent.event_date >= today,
            )
        )).scalars().all())

    symbols = [s for s in full_universe if s not in already_covered]
    logger.info(f"[full_backfill] {len(full_universe)} total symbols, {len(already_covered)} already "
                f"covered from a prior run, {len(symbols)} left to scan -- "
                f"~{len(symbols) * _YFINANCE_SAFE_DELAY_SEC / 3600:.1f}h estimated (excludes any cooldowns)")

    found = 0
    skipped_no_date = 0
    rate_limit_streak = 0
    total_rate_limit_hits = 0

    for i, sym in enumerate(symbols):
        try:
            ev = await _fetch_one_earnings_event(sym, raise_on_error=True)
            rate_limit_streak = 0  # any clean result (found or genuinely no data) resets the streak
        except Exception as exc:
            is_rate_limit = "Too Many Requests" in str(exc) or "Rate limited" in str(exc)
            if is_rate_limit:
                rate_limit_streak += 1
                total_rate_limit_hits += 1
            else:
                rate_limit_streak = 0
            logger.debug(f"[full_backfill] {sym}: {exc}")
            ev = None

        if ev is not None:
            async with AsyncSessionLocal() as session:
                from sqlalchemy import delete
                # Replace any existing future-dated row for this exact symbol
                # (idempotent/resumable).
                await session.execute(
                    delete(MarketEvent).where(
                        MarketEvent.event_type == "EARNINGS",
                        MarketEvent.symbol == sym,
                        MarketEvent.event_date >= today,
                    )
                )
                session.add(MarketEvent(**ev))
                await session.commit()
            found += 1
        elif rate_limit_streak == 0:
            skipped_no_date += 1

        if (i + 1) % 100 == 0:
            elapsed_h = (time.monotonic() - start) / 3600
            logger.info(f"[full_backfill] {i + 1}/{len(symbols)} scanned ({found} found, "
                        f"{skipped_no_date} no-date, {total_rate_limit_hits} rate-limit hits) -- "
                        f"{elapsed_h:.2f}h elapsed")

        if rate_limit_streak >= _RATE_LIMIT_STREAK_THRESHOLD:
            logger.warning(f"[full_backfill] {rate_limit_streak} consecutive rate-limit hits -- "
                            f"cooling down {_RATE_LIMIT_COOLDOWN_SEC}s before resuming")
            await asyncio.sleep(_RATE_LIMIT_COOLDOWN_SEC)
            rate_limit_streak = 0
        elif i < len(symbols) - 1:
            await asyncio.sleep(_YFINANCE_SAFE_DELAY_SEC)

    elapsed_h = (time.monotonic() - start) / 3600
    logger.info(f"[full_backfill] DONE — {found} earnings events found across "
                f"{len(symbols)} symbols scanned ({total_rate_limit_hits} total rate-limit hits "
                f"encountered) in {elapsed_h:.2f}h")


if __name__ == "__main__":
    asyncio.run(main())
