"""Full-NSE daily-candle backfill via Zerodha Kite historical API (paid, reliable).

Pulls 200 trading days of daily OHLCV for every NSE EQ instrument in
`kite_instruments` and upserts into the `candles` table. Replaces the flaky
yfinance backfill — Kite gives complete volume and no rate-limit-induced gaps.

Usage (from autotrade-backend/):
    .venv/bin/python3 scripts/backfill_all_candles_zerodha.py            # all symbols, skip fresh
    .venv/bin/python3 scripts/backfill_all_candles_zerodha.py --max 100  # quick test
    .venv/bin/python3 scripts/backfill_all_candles_zerodha.py --no-skip  # re-fetch everything
    .venv/bin/python3 scripts/backfill_all_candles_zerodha.py --days 5   # incremental refresh

Resumable: a symbol with a daily candle in the last `--fresh-days` (default 7)
is skipped, so you can Ctrl-C and re-run. Idempotent: ON CONFLICT DO NOTHING.

Rate limit: Kite historical API caps at 3 req/sec. Default 0.5s/req (~2 req/sec)
matches the proven-safe value in crawler/zerodha_historical.py. Retries with
exponential backoff on "Too many requests" / transient network errors.
"""
import os
import sys
import time
import asyncio
import argparse
import datetime as dt

# Make backend root importable when run from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from db.database import AsyncSessionLocal
from crawler.price_feed import save_candles_to_db
from crawler.zerodha_kite_lib import get_kite
from utils.logger import logger

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def _to_naive_utc(ts):
    if isinstance(ts, dt.datetime) and ts.tzinfo is not None:
        return ts.astimezone(dt.timezone.utc).replace(tzinfo=None)
    if isinstance(ts, dt.date) and not isinstance(ts, dt.datetime):
        return dt.datetime(ts.year, ts.month, ts.day)
    return ts


async def get_symbols(skip_fresh: bool, fresh_days: int):
    """Return [(tradingsymbol, instrument_token)] for NSE EQ, optionally skipping
    symbols that already have a recent daily candle."""
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT tradingsymbol, instrument_token
            FROM kite_instruments
            WHERE segment='NSE' AND instrument_type='EQ'
              AND name != '' AND instrument_token > 0
            ORDER BY tradingsymbol
        """))).all()
        all_syms = [(r.tradingsymbol, r.instrument_token) for r in rows]

        if not skip_fresh:
            return all_syms

        cutoff = dt.datetime.utcnow() - dt.timedelta(days=fresh_days)
        fresh = {r.symbol for r in (await s.execute(text(
            "SELECT DISTINCT symbol FROM candles WHERE timeframe='1d' AND timestamp >= :c"
        ), {"c": cutoff})).all()}

    todo = [(sym, tok) for sym, tok in all_syms if f"{sym}.NS" not in fresh]
    print(f"[backfill] NSE EQ instruments: {len(all_syms)} | already fresh: {len(fresh)} | to fetch: {len(todo)}")
    return todo


def fetch_one(kite, token, from_date, to_date, retries=3):
    """Sync Kite historical_data call with retry/backoff. Returns raw candle list."""
    for attempt in range(retries):
        try:
            return kite.historical_data(
                instrument_token=token,
                from_date=from_date,
                to_date=to_date,
                interval="day",
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "too many requests" in msg or "rate" in msg or "timed out" in msg or "connection" in msg:
                time.sleep(1.5 * (attempt + 1))  # backoff on rate/network
                continue
            # token-not-permitted / delisted / bad-token → don't retry
            return []
    return []


async def backfill(trading_days: int, delay: float, skip_fresh: bool,
                   fresh_days: int, max_symbols: int | None):
    kite = get_kite()
    # Sanity: confirm historical access works before the long run
    try:
        kite.profile()
    except Exception as exc:
        print(f"[backfill] ABORT — Zerodha not authenticated: {exc}")
        print("           Re-login at /zerodha/connect, then re-run.")
        return

    symbols = await get_symbols(skip_fresh, fresh_days)
    if max_symbols:
        symbols = symbols[:max_symbols]
        print(f"[backfill] capped to {max_symbols} for test run")

    # 200 trading days ≈ 290 calendar days (weekends + holidays)
    to_date   = dt.date.today()
    from_date = to_date - dt.timedelta(days=int(trading_days * 1.45) + 10)

    total = len(symbols)
    saved_total = fetched_total = ok = empty = errors = 0
    t0 = time.time()
    pending = []  # batch of candle dicts to flush

    iterator = tqdm(symbols, desc="Backfill", unit="sym") if tqdm else symbols

    async with AsyncSessionLocal() as session:
        for i, (sym, token) in enumerate(iterator, 1):
            raw = await asyncio.to_thread(fetch_one, kite, token, from_date, to_date)
            if raw:
                ok += 1
                fetched_total += len(raw)
                for c in raw:
                    ts = c.get("date")
                    if ts is None:
                        continue
                    pending.append({
                        "symbol":    f"{sym}.NS",
                        "timeframe": "1d",
                        "open":      float(c.get("open", 0.0)),
                        "high":      float(c.get("high", 0.0)),
                        "low":       float(c.get("low", 0.0)),
                        "close":     float(c.get("close", 0.0)),
                        "volume":    float(c.get("volume", 0) or 0),
                        "timestamp": _to_naive_utc(ts),
                    })
            else:
                empty += 1

            # Flush every 50 symbols to keep transactions small
            if len(pending) >= 5000 or i % 50 == 0:
                if pending:
                    try:
                        saved_total += await save_candles_to_db(pending, session)
                        await session.commit()
                    except Exception as exc:
                        errors += 1
                        await session.rollback()
                        logger.warning(f"[backfill] flush error: {exc}")
                    pending = []

            if i % 100 == 0:
                el = time.time() - t0
                rate = i / el if el else 0
                eta = (total - i) / rate / 60 if rate else 0
                msg = (f"[backfill] {i}/{total} | ok={ok} empty={empty} "
                       f"saved={saved_total:,} | {rate:.1f} sym/s | ETA {eta:.0f} min")
                if tqdm:
                    iterator.write(msg)
                else:
                    print(msg, flush=True)

            await asyncio.sleep(delay)

        # Final flush
        if pending:
            try:
                saved_total += await save_candles_to_db(pending, session)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.warning(f"[backfill] final flush error: {exc}")

    el = time.time() - t0
    print("\n[backfill] DONE")
    print(f"  symbols processed : {total:,}")
    print(f"  fetched ok        : {ok:,}   empty/skipped: {empty:,}")
    print(f"  candles fetched   : {fetched_total:,}")
    print(f"  candles saved new : {saved_total:,}")
    print(f"  flush errors      : {errors}")
    print(f"  elapsed           : {el/60:.1f} min")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Backfill NSE daily candles via Zerodha Kite")
    p.add_argument("--days",       type=int,   default=200,  help="Trading days of history (default 200)")
    p.add_argument("--delay",      type=float, default=0.5,  help="Seconds between requests (default 0.5 ≈ 2 req/s)")
    p.add_argument("--no-skip",    action="store_true",      help="Re-fetch even symbols with fresh candles")
    p.add_argument("--fresh-days", type=int,   default=7,    help="Skip symbols with a candle newer than N days")
    p.add_argument("--max",        type=int,   default=None, help="Cap symbol count (testing)")
    args = p.parse_args()

    asyncio.run(backfill(
        trading_days=args.days,
        delay=args.delay,
        skip_fresh=not args.no_skip,
        fresh_days=args.fresh_days,
        max_symbols=args.max,
    ))
