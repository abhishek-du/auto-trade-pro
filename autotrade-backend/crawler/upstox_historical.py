import asyncio
import httpx
from datetime import date, timedelta
from crawler.upstox_auth import ensure_upstox_token_fresh
from crawler.upstox_data import get_instrument_key, _headers, _V2
from utils.logger import logger

async def get_historical_candles(
    symbol: str,
    interval: str = "day",
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Fetch OHLCV candles from Upstox.

    interval: 1minute | 5minute | 30minute | day | week | month
    """
    if not from_date:
        from_date = (date.today() - timedelta(days=30 if "minute" in interval else 365)).isoformat()
    if not to_date:
        to_date = date.today().isoformat()

    if not await ensure_upstox_token_fresh():
        return []
    
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return []
        
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_V2}/historical-candle/{ikey}/{interval}/{to_date}/{from_date}",
                headers=_headers(),
            )
            if r.status_code == 200:
                candles = r.json().get("data", {}).get("candles", [])
                out = [
                    {
                        "timestamp": c[0], "open": c[1], "high": c[2],
                        "low": c[3], "close": c[4], "volume": c[5],
                    }
                    for c in candles
                ]
                return out
    except Exception as e:
        logger.error(f"[upstox/historical] Failed for {symbol}: {e}")
    return []

async def get_intraday_candles(symbol: str, interval: str = "1minute") -> list[dict]:
    """Fetch current trading day's intraday OHLCV candles from Upstox.
    interval: 1minute | 5minute | 30minute
    """
    if not await ensure_upstox_token_fresh():
        return []
    
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return []
        
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_V2}/historical-candle/intraday/{ikey}/{interval}",
                headers=_headers(),
            )
            if r.status_code == 200:
                candles = r.json().get("data", {}).get("candles", [])
                out = [
                    {
                        "timestamp": c[0], "open": c[1], "high": c[2],
                        "low": c[3], "close": c[4], "volume": c[5],
                    }
                    for c in candles
                ]
                return out
    except Exception as e:
        logger.error(f"[upstox/intraday] Failed for {symbol}: {e}")
    return []


# ── Long-tail intraday sync (2026-07-31) ──────────────────────────────────────
#
# Everything outside hub_universe (NSE names below the turnover cutoff, and
# ALL of BSE -- hub_universe is NSE-only) gets zero intraday coverage from
# the existing Kite-based crawlers, which are sized for the curated ~2,000-
# 3,000 symbol universe, not the full ~9,800-symbol NSE+BSE market. Rather
# than compete with Kite's already-tight 3 req/sec budget (used live by the
# Hub universe's 60s-3min trading cadence), this uses Upstox as a fully
# separate lane.
#
# Sequential loop + fixed delay, deliberately NOT an asyncio.Lock/Semaphore
# held across calls -- this codebase's Alpha Vantage rate-limiter
# (crawler/price_feed.py's _get_av_lock()) creates its lock lazily and reuses
# it forever, which breaks the moment it's touched from a second event loop
# (Celery tasks here run via asyncio.run() per invocation -- a lock bound to
# a prior, now-closed loop raises). A plain per-call asyncio.sleep() has no
# such loop affinity, and it's the exact pattern already proven safe at scale
# in sync_full_nse_universe()/sync_full_bse_universe() (crawler/
# zerodha_historical.py).
#
# Rate is intentionally conservative and NOT yet confirmed against Upstox's
# current published limits -- start here, watch the logs for 429s the first
# few runs, tune down (safety first) or up from there. Same empirical-tuning
# approach that got Kite's own rate from a documented-but-too-fast 3 req/sec
# down to an observed-safe 2 req/sec.
UPSTOX_LONG_TAIL_DELAY_SEC = 0.25   # ~4 req/sec, conservative starting point


async def sync_long_tail_intraday_upstox(
    session,
    *,
    interval: str = "1minute",
    delay_sec: float = UPSTOX_LONG_TAIL_DELAY_SEC,
) -> dict:
    """Refresh today's intraday candles, via Upstox, for every NSE+BSE EQ
    instrument NOT already covered by the Kite-based Hub universe crawl.

    Universe = kite_instruments (NSE+BSE EQ, GOI/SDL bonds excluded -- same
    filter as sync_full_nse_universe/sync_full_bse_universe) minus
    hub_universe.symbol, further restricted (2026-08-03) to symbols with a
    recent Kite daily candle -- proof Kite itself considers the instrument
    real and actively traded. Added after a live 40-symbol timing sample
    found the raw kite_instruments filter still let through ETFs/NAV-tracker
    reference symbols (BSE500IETF, HDFCPBINAV, ICISECINAV -- tagged EQ but
    never resolvable) that each cost 3-8s to fail Upstox ISIN resolution,
    dominating real per-symbol latency (0.98s/symbol average vs a ~0.3s
    estimate from testing only already-cached liquid names). Idempotent
    upsert into the same `candles` table via save_candles_to_db(), so
    re-running mid-day just refreshes each symbol's bars-so-far.
    """
    import time as _time
    from datetime import datetime as _dt, timezone as _tz
    from sqlalchemy import text as _text
    from crawler.price_feed import save_candles_to_db

    rows = (await session.execute(_text("""
        -- NSE ONLY (Step 2A, 2026-08-28). BSE is out of scope for the strategy. Restricting the QUERY rather than filtering afterwards means a .BO string is never constructed, so it cannot leak through a later branch.
        SELECT tradingsymbol, segment FROM kite_instruments
        WHERE segment = 'NSE' AND instrument_type = 'EQ'
          AND name != '' AND instrument_token > 0
          AND name NOT ILIKE 'GOI %' AND name NOT ILIKE 'SDL %'
        ORDER BY tradingsymbol
    """))).all()

    hub_symbols = set(
        (await session.execute(_text("SELECT symbol FROM hub_universe"))).scalars().all()
    )

    known_tradeable = set(
        (await session.execute(_text(
            "SELECT DISTINCT symbol FROM candles "
            "WHERE timeframe = '1d' AND timestamp >= now() - interval '10 days'"
        ))).scalars().all()
    )

    # NSE ONLY (Step 2A). DROPS any non-NSE row rather than relabelling it:
    # mapping a BSE row to ".NS" would make it look like an NSE instrument and
    # pass the exchange gate, which is strictly worse than the ".BO" it
    # replaced. The query above is already NSE-restricted; this is defence in
    # depth against a future widening of it.
    universe = [f"{sym}.NS" for sym, segment in rows if segment == "NSE"]
    long_tail = [s for s in universe if s not in hub_symbols and s in known_tradeable]

    # Time-boxed rotation (2026-08-03) -- walking the full ~7,672-symbol long
    # tail every 30-min tick took ~30 minutes itself, monopolizing one of only
    # 4 Celery worker slots on this 4-core box continuously and starving
    # _pre_event_gap_scan_loop (a live-trading task with its own pre-existing
    # 9-min timeout) of CPU -- confirmed via SoftTimeLimitExceeded errors
    # (22x on 2026-08-03, 0x the day before this task existed). Fix: process
    # one of 4 slices per call instead of the whole universe. Slice choice is
    # a pure function of wall-clock time -- deliberately NOT a process-memory
    # counter (see the DIRECT_NEWS recheck throttle bug this same class of
    # mistake caused earlier), so it self-corrects across the frequent
    # watchmedo restarts this codebase has, no coordination needed. Trade-off:
    # full long-tail coverage drops from every 30 min to every ~2 hours --
    # the right call, since this tier was always meant to be the slower,
    # best-effort one and should never cost the Hub universe / live trading
    # anything.
    _NUM_SLICES = 4
    slice_index = int(_time.time() // 1800) % _NUM_SLICES
    long_tail = long_tail[slice_index::_NUM_SLICES]

    # Wall-clock deadline (2026-08-03) -- the actual guarantee against
    # overrunning the task's own Celery time limit (900s/960s soft/hard,
    # tasks/india_tasks.py::sync_long_tail_intraday_task), regardless of how
    # accurate any per-symbol timing estimate turns out to be. A live test of
    # the symbol-count-only slicing above got force-killed at 960s having
    # saved zero rows, because the estimate it was sized on (already-cached
    # liquid names) didn't hold for this population. Stopping early here
    # falls through to the existing "if pending: save" below exactly as if
    # the slice had finished normally -- partial progress always gets saved,
    # the task always exits gracefully on its own, never via a kill.
    _DEADLINE_SEC = 700
    loop_start = _time.monotonic()
    stopped_early = False

    total_saved = ok = empty = 0
    pending: list[dict] = []

    for symbol in long_tail:
        if _time.monotonic() - loop_start >= _DEADLINE_SEC:
            stopped_early = True
            break
        try:
            raw = await get_intraday_candles(symbol, interval=interval)
        except Exception:
            raw = []
        if raw:
            ok += 1
            for c in raw:
                ts = c.get("timestamp")
                if ts is None:
                    continue
                if isinstance(ts, str):
                    ts = _dt.fromisoformat(ts)
                if ts.tzinfo is not None:
                    ts = ts.astimezone(_tz.utc).replace(tzinfo=None)
                pending.append({
                    "symbol": symbol, "timeframe": "1m",
                    "open": float(c.get("open", 0.0)), "high": float(c.get("high", 0.0)),
                    "low": float(c.get("low", 0.0)), "close": float(c.get("close", 0.0)),
                    "volume": float(c.get("volume", 0) or 0), "timestamp": ts,
                })
        else:
            empty += 1

        if len(pending) >= 3000:
            try:
                total_saved += await save_candles_to_db(pending, session)
                await session.commit()
            except Exception:
                await session.rollback()
            pending = []
        await asyncio.sleep(delay_sec)

    if pending:
        try:
            total_saved += await save_candles_to_db(pending, session)
            await session.commit()
        except Exception:
            await session.rollback()

    summary = {
        "symbols": len(long_tail), "fetched_ok": ok, "empty": empty, "saved": total_saved,
        "processed": ok + empty, "stopped_early": stopped_early,
    }
    logger.info(f"[sync_long_tail_intraday_upstox] → {summary}")
    return summary
