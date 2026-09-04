"""LEGACY / DISABLED — one-shot yfinance daily backfill.

DISABLED 2026-09-04 (Step 2C.2). DO NOT RE-ENABLE WITHOUT READING THIS.

This script writes daily candles from yfinance, whose daily timestamps are
tz-naive MIDNIGHT — i.e. the 00:00 UTC series. That is one of the two legacy
conventions Step 2C.1 identified, and it is the DEAD, pre-split series: for
JLHL.NS its last close is 1348.0 against the live series' 315.45.

It also bypasses the canonical guard entirely: it INSERTs straight into
`candles` rather than going through price_feed.save_candles_to_db, so the
contract check in utils/candle_contract cannot see it.

Running it would silently reintroduce exactly the divergence Step 2C.2 exists
to eliminate, across hub_universe.

If a daily backfill is needed, use the canonical pipeline instead:

    crawler.upstox_candles.get_upstox_candles_for_range(symbol, frm, to, "1d")
    crawler.price_feed.save_candles_to_db(rows, session, source="manual-backfill")

which produces 03:45 UTC session-open timestamps and is validated on write.

The historical rows this script already created are NOT touched — historical
reconciliation is Step 2D.
"""

# Hard stop. Placed at import time so neither `python scripts/backfill_1d_candles.py`
# nor an accidental `import` can reach the yfinance write path below.
_DISABLED_REASON = (
    "scripts/backfill_1d_candles.py is DISABLED (Step 2C.2): it writes the dead "
    "00:00 UTC yfinance daily series and bypasses the canonical candle guard. "
    "Use crawler.upstox_candles.get_upstox_candles_for_range + "
    "price_feed.save_candles_to_db(source=...) instead. "
    "Set ALLOW_LEGACY_1D_BACKFILL=1 only if you have read Step 2C.2 and intend "
    "to write legacy-convention rows deliberately."
)

import os as _os

if _os.environ.get("ALLOW_LEGACY_1D_BACKFILL") != "1":
    raise SystemExit(_DISABLED_REASON)

import asyncio
import os
import sys
from datetime import datetime

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from utils.config import settings

DATABASE_URL = os.environ.get("DATABASE_URL") or settings.DATABASE_URL

BATCH_SIZE = 50   # symbols per yfinance download call
PERIOD     = "2y"  # 2 years gives 500+ trading days — enough for all indicators


# ── Fetch symbols to backfill ─────────────────────────────────────────────────

async def get_symbols(engine) -> list[str]:
    async with engine.connect() as conn:
        r1 = await conn.execute(text("SELECT symbol FROM hub_universe ORDER BY rank"))
        hub = [row[0] for row in r1.all()]

        r2 = await conn.execute(text(
            "SELECT DISTINCT symbol FROM candles WHERE timeframe='1h' AND symbol LIKE '%.NS'"
        ))
        h1 = [row[0] for row in r2.all()]

    # hub_universe first (ranked by importance), then any 1h-only symbols not in hub
    seen = set(hub)
    extra = [s for s in h1 if s not in seen]
    return hub + extra


# ── yfinance download ─────────────────────────────────────────────────────────

def _download_batch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Return {symbol: OHLCV DataFrame} for symbols that have data."""
    try:
        raw = yf.download(
            symbols,
            period=PERIOD,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        print(f"  [yfinance] batch error: {exc}")
        return {}

    if raw.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}

    if isinstance(raw.columns, pd.MultiIndex):
        # Multi-symbol download: columns = (PriceType, Symbol)
        for sym in symbols:
            try:
                df = raw.xs(sym, axis=1, level=1).dropna(how="all")
                if len(df) >= 10:
                    result[sym] = df
            except KeyError:
                pass
    else:
        # Single-symbol download (shouldn't reach here but defensive)
        sym = symbols[0]
        df = raw.dropna(how="all")
        if len(df) >= 10:
            result[sym] = df

    return result


# ── DB insert ─────────────────────────────────────────────────────────────────

async def insert_rows(engine, rows: list[dict]) -> int:
    if not rows:
        return 0
    async with engine.begin() as conn:
        r = await conn.execute(
            text("""
                INSERT INTO candles
                    (symbol, timeframe, open, high, low, close, volume, timestamp)
                VALUES
                    (:symbol, :timeframe, :open, :high, :low, :close, :volume, :timestamp)
                ON CONFLICT ON CONSTRAINT uq_candle_bar DO NOTHING
            """),
            rows,
        )
        return r.rowcount


def _to_rows(sym: str, df: pd.DataFrame) -> list[dict]:
    rows = []
    for ts, row in df.iterrows():
        # yfinance returns tz-naive timestamps for NSE 1d data
        ts_dt = pd.Timestamp(ts).to_pydatetime().replace(tzinfo=None)
        o = float(row.get("Open", 0) or 0)
        h = float(row.get("High", 0) or 0)
        l = float(row.get("Low",  0) or 0)
        c = float(row.get("Close", 0) or 0)
        v = float(row.get("Volume", 0) or 0)
        # Skip bad rows
        if c <= 0 or o <= 0:
            continue
        rows.append({
            "symbol":    sym,
            "timeframe": "1d",
            "open":      o,
            "high":      h,
            "low":       l,
            "close":     c,
            "volume":    v,
            "timestamp": ts_dt,
        })
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    engine = create_async_engine(DATABASE_URL, pool_size=2)

    symbols = await get_symbols(engine)
    total   = len(symbols)
    print(f"Backfilling {total} symbols  (period={PERIOD}, batch={BATCH_SIZE})")
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}\n")

    total_rows     = 0
    total_inserted = 0
    total_symbols  = 0
    failed         = []

    num_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    for batch_i, start in enumerate(range(0, total, BATCH_SIZE), 1):
        batch = symbols[start : start + BATCH_SIZE]
        print(
            f"[{batch_i:3d}/{num_batches}]  "
            f"symbols {start+1}–{min(start+BATCH_SIZE, total)}  "
            f"({batch[0].replace('.NS','')} … {batch[-1].replace('.NS','')})",
            end="  ",
            flush=True,
        )

        dfs = await asyncio.to_thread(_download_batch, batch)

        rows = []
        for sym, df in dfs.items():
            rows.extend(_to_rows(sym, df))

        missing = [s for s in batch if s not in dfs]
        if missing:
            failed.extend(missing)

        inserted = await insert_rows(engine, rows)
        total_rows     += len(rows)
        total_inserted += inserted
        total_symbols  += len(dfs)

        print(
            f"got={len(dfs):3d}  rows={len(rows):5d}  new={inserted:5d}"
            + (f"  miss={len(missing)}" if missing else "")
        )

    await engine.dispose()

    print(f"\n{'='*60}")
    print(f"Finished: {datetime.now().strftime('%H:%M:%S')}")
    print(f"Symbols with data : {total_symbols} / {total}")
    print(f"Total rows parsed : {total_rows:,}")
    print(f"New rows inserted : {total_inserted:,}")
    if failed:
        print(f"No data for       : {len(failed)} symbols")
        for s in failed[:20]:
            print(f"  {s}")
        if len(failed) > 20:
            print(f"  … and {len(failed)-20} more")


if __name__ == "__main__":
    asyncio.run(main())
