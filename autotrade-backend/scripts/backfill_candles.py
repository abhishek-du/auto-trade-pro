"""One-time backfill: fetch 200 days of daily candles for every NSE EQ symbol.

Usage (from autotrade-backend/):
    .venv/bin/python3 scripts/backfill_candles.py [--workers N] [--batch B]

Strategy
--------
* yf.download() is used in batches — it can fetch ~50 symbols in a single
  HTTP round-trip, making it ~50× faster than one-by-one Ticker calls.
* Symbols already having a candle within the last 7 days are skipped (fresh).
* Progress is printed every batch so you can Ctrl-C and resume safely.
  Re-running is idempotent (ON CONFLICT DO NOTHING).

Expected run time (8,000 symbols, batch=50, 200 days daily):
  ~20-30 min on a decent connection.  Actual network speed is the bottleneck.
"""

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone

import yfinance as yf

# Add backend root to path so imports work when running from scripts/
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Configuration ──────────────────────────────────────────────────────────────
PERIOD       = "200d"      # yfinance lookback
INTERVAL     = "1d"        # daily candles (smaller = faster, more granular = 1h)
BATCH_SIZE   = 50          # symbols per yf.download() call
SKIP_FRESH   = True        # skip symbols with candles in the last 7 days
MAX_SYMBOLS  = None        # set to e.g. 200 to do a quick test run; None = all
# ──────────────────────────────────────────────────────────────────────────────


def _to_naive_utc(ts) -> datetime:
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


async def get_symbols_to_backfill(session, skip_fresh: bool) -> list[str]:
    from sqlalchemy import select, text
    from db.models import KiteInstrument

    # All NSE EQ symbols
    result = await session.execute(
        select(KiteInstrument.tradingsymbol)
        .where(
            KiteInstrument.instrument_type == "EQ",
            KiteInstrument.segment == "NSE",
            KiteInstrument.name != "",
        )
        .order_by(KiteInstrument.tradingsymbol)
    )
    all_syms = [f"{r.tradingsymbol}.NS" for r in result.all()]
    print(f"[backfill] Total NSE EQ symbols: {len(all_syms)}")

    if not skip_fresh:
        return all_syms

    # Find symbols with recent daily candles (skip them)
    cutoff = datetime.utcnow() - timedelta(days=7)
    fresh_result = await session.execute(
        text("""
            SELECT DISTINCT symbol FROM candles
            WHERE timeframe = '1d'
              AND timestamp >= :cutoff
        """),
        {"cutoff": cutoff},
    )
    fresh = {r.symbol for r in fresh_result.all()}
    todo = [s for s in all_syms if s not in fresh]
    print(f"[backfill] Already fresh: {len(fresh)}  To fetch: {len(todo)}")
    return todo


async def backfill(batch_size: int = BATCH_SIZE, max_symbols: int | None = MAX_SYMBOLS, skip_fresh: bool = True):
    from tasks._db import celery_session
    from crawler.price_feed import save_candles_to_db

    t0 = time.time()

    async with celery_session() as session:
        symbols = await get_symbols_to_backfill(session, skip_fresh=skip_fresh)

    if max_symbols:
        symbols = symbols[:max_symbols]
        print(f"[backfill] Capped to {max_symbols} symbols for test run")

    total_syms    = len(symbols)
    total_candles = 0
    total_saved   = 0
    errors        = 0

    print(f"[backfill] Starting — {total_syms} symbols  batch={batch_size}  period={PERIOD}  interval={INTERVAL}")
    print(f"[backfill] Estimated time: ~{int(total_syms / batch_size * 2 / 60)} min")

    for batch_start in range(0, total_syms, batch_size):
        batch = symbols[batch_start : batch_start + batch_size]
        elapsed = time.time() - t0
        pct = batch_start / total_syms * 100
        print(
            f"[backfill] {batch_start:5d}/{total_syms}  ({pct:4.1f}%)  "
            f"saved={total_saved:,}  errors={errors}  elapsed={elapsed:.0f}s",
            end="\r",
            flush=True,
        )

        # Rate-limit guard: sleep between batches to avoid "Too Many Requests"
        # especially when the live price crawl (every 30s) is also hitting yfinance.
        time.sleep(3)

        try:
            # yf.download fetches all symbols in the batch in parallel
            df = yf.download(
                tickers=batch,
                period=PERIOD,
                interval=INTERVAL,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )

            candles_batch: list[dict] = []

            # yfinance returns:
            #   single symbol → flat columns (Open/High/Low/Close/Volume)
            #   multi symbol  → MultiIndex columns with level-0 = symbol
            available_syms = (
                df.columns.get_level_values(0).unique().tolist()
                if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1
                else [batch[0]] if len(batch) == 1 and not df.empty
                else []
            )

            for sym in batch:
                try:
                    if sym not in available_syms:
                        continue
                    sym_df = df[sym] if df.columns.nlevels > 1 else df
                    if sym_df is None or sym_df.empty:
                        continue
                    # Drop rows where Close is NaN (delisted/no-data bars)
                    sym_df = sym_df.dropna(subset=["Close"])
                    for row in sym_df.itertuples():
                        try:
                            candles_batch.append({
                                "symbol":    sym,
                                "timeframe": INTERVAL,
                                "open":      float(row.Open),
                                "high":      float(row.High),
                                "low":       float(row.Low),
                                "close":     float(row.Close),
                                "volume":    float(getattr(row, "Volume", 0.0) or 0.0),
                                "timestamp": _to_naive_utc(row.Index.to_pydatetime()),
                            })
                        except Exception:
                            pass
                except Exception:
                    errors += 1

            total_candles += len(candles_batch)

            if candles_batch:
                async with celery_session() as session:
                    saved = await save_candles_to_db(candles_batch, session)
                    await session.commit()
                    total_saved += saved

        except Exception as exc:
            errors += 1
            print(f"\n[backfill] Batch error at {batch_start}: {exc}")

    elapsed = time.time() - t0
    print(f"\n[backfill] Done!")
    print(f"  Symbols processed : {total_syms:,}")
    print(f"  Candles fetched   : {total_candles:,}")
    print(f"  Candles saved (new): {total_saved:,}")
    print(f"  Errors            : {errors}")
    print(f"  Time              : {elapsed:.0f}s ({elapsed/60:.1f} min)")
    return total_saved


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill daily candles for all NSE EQ symbols")
    parser.add_argument("--batch",   type=int, default=BATCH_SIZE,  help=f"Symbols per yf.download batch (default {BATCH_SIZE})")
    parser.add_argument("--max",     type=int, default=None,        help="Cap symbol count (for testing, e.g. --max 200)")
    parser.add_argument("--no-skip", action="store_true",           help="Don't skip symbols with recent candles")
    args = parser.parse_args()

    asyncio.run(backfill(batch_size=args.batch, max_symbols=args.max, skip_fresh=not args.no_skip))
