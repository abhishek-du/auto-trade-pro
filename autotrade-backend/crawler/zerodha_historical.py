"""Historical candle fetcher backed by Kite Connect.

Uses the official kiteconnect library to pull OHLCV candles for an
instrument token, normalises to the project's `Candle` schema, and
persists via the existing `save_candles_to_db` helper.

Supported timeframes are mapped to Kite's interval strings via INTERVAL_MAP.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from crawler.price_feed import save_candles_to_db
from crawler.zerodha_instruments import get_token
from utils.config import settings
from utils.logger import logger

# ── Interval map ─────────────────────────────────────────────────────────────

INTERVAL_MAP: dict[str, str] = {
    "1m":  "minute",
    "3m":  "3minute",
    "5m":  "5minute",
    "10m": "10minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h":  "60minute",
    "1d":  "day",
}


def _to_kite_interval(tf: str) -> str:
    return INTERVAL_MAP.get(tf, tf)


# ── Raw fetch ────────────────────────────────────────────────────────────────

async def get_kite_candles_for_range(
    symbol: str,
    from_date: _dt.date | _dt.datetime | str,
    to_date: _dt.date | _dt.datetime | str,
    interval: str = "1d",
    oi: bool = False,
) -> list[dict]:
    """Fetch raw candles for a symbol over [from_date, to_date].

    Returns a list of dicts in save_candles_to_db format.
    """
    token = get_token(symbol)
    if token is None:
        logger.warning(f"[zerodha_historical] No instrument token for {symbol}")
        return []

    from crawler.zerodha_kite_lib import get_historical_data

    kite_interval = _to_kite_interval(interval)
    try:
        raw = await asyncio.to_thread(
            get_historical_data,
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=kite_interval,
            oi=oi,
        )
    except Exception as exc:
        logger.warning(f"[zerodha_historical] Fetch failed for {symbol}: {exc}")
        return []

    # Normalise into Candle DB row format
    tf_reverse = {v: k for k, v in INTERVAL_MAP.items()}
    tf = tf_reverse.get(kite_interval, interval)
    sym_save = symbol if symbol.endswith(".NS") or symbol.startswith("^") else f"{symbol}.NS"

    candles: list[dict] = []
    for c in raw:
        ts = c.get("date") or c.get("timestamp")
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = _dt.datetime.fromisoformat(ts)
            except ValueError:
                continue
        if isinstance(ts, _dt.datetime) and ts.tzinfo is not None:
            ts = ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        candles.append({
            "symbol":    sym_save,
            "timeframe": tf,
            "open":      float(c.get("open", 0.0)),
            "high":      float(c.get("high", 0.0)),
            "low":       float(c.get("low", 0.0)),
            "close":     float(c.get("close", 0.0)),
            "volume":    float(c.get("volume", 0)),
            "timestamp": ts,
        })
    return candles


# ── DB sync — single symbol ──────────────────────────────────────────────────

async def sync_kite_candles(
    symbol: str,
    timeframe: str,
    days_back: int,
    session: AsyncSession,
) -> dict:
    """Fetch [days_back] days of candles for symbol/timeframe and save to DB."""
    to_date = _dt.date.today()
    from_date = to_date - _dt.timedelta(days=days_back)
    candles = await get_kite_candles_for_range(symbol, from_date, to_date, interval=timeframe)
    if not candles:
        return {"symbol": symbol, "timeframe": timeframe, "saved": 0, "fetched": 0}
    saved = await save_candles_to_db(candles, session)
    return {"symbol": symbol, "timeframe": timeframe, "saved": saved, "fetched": len(candles)}


# ── DB sync — all NSE symbols ────────────────────────────────────────────────

async def sync_all_nse_candles(
    session: AsyncSession,
    *,
    timeframe: str = "1d",
    days_back: int = 120,
    delay_sec: float = 0.5,
) -> dict:
    """Iterate settings.nse_symbols (+ mid caps) and persist daily candles.

    ``delay_sec`` defaults to 0.5 to stay under Kite's 3 req/sec historical
    rate limit with headroom — 0.35 (≈2.85 req/sec) hit 429s in practice.
    """
    symbols: Iterable[str] = settings.nse_symbols + settings.nse_mid_symbols
    total_saved = 0
    total_fetched = 0
    errors: list[str] = []

    for sym in symbols:
        try:
            result = await sync_kite_candles(sym, timeframe, days_back, session)
            total_saved += result.get("saved", 0)
            total_fetched += result.get("fetched", 0)
        except Exception as exc:
            errors.append(f"{sym}: {exc}")
            logger.warning(f"[zerodha_historical] {sym} sync error: {exc}")
        await asyncio.sleep(delay_sec)

    try:
        await session.commit()
    except Exception:
        await session.rollback()

    summary = {
        "symbols": len(list(symbols)),
        "fetched": total_fetched,
        "saved":   total_saved,
        "errors":  errors,
    }
    logger.info(f"[zerodha_historical] sync_all_nse_candles → {summary}")
    return summary


# ── DB sync — FULL NSE universe from kite_instruments ────────────────────────

async def sync_full_nse_universe(
    session: AsyncSession,
    *,
    days_back: int = 7,
    delay_sec: float = 0.5,
) -> dict:
    """Incrementally refresh daily candles for EVERY NSE EQ instrument.

    Reads instrument tokens straight from `kite_instruments` (not the curated
    watchlist) so the agent's full-market universe stays current. Designed for
    the weekly beat task: ``days_back=7`` keeps the latest week of bars fresh.

    Idempotent (ON CONFLICT DO NOTHING). Skips symbols whose token is missing.
    """
    import datetime as _d
    from sqlalchemy import text as _text

    rows = (await session.execute(_text("""
        SELECT tradingsymbol, instrument_token
        FROM kite_instruments
        WHERE segment='NSE' AND instrument_type='EQ'
          AND name != '' AND instrument_token > 0
        ORDER BY tradingsymbol
    """))).all()

    kite = None
    try:
        from crawler.zerodha_kite_lib import get_kite
        kite = get_kite()
        kite.profile()  # verify token before the long loop
    except Exception as exc:
        logger.warning(f"[sync_full_nse_universe] Zerodha not authenticated: {exc}")
        return {"symbols": 0, "saved": 0, "error": "not_authenticated"}

    to_date   = _d.date.today()
    from_date = to_date - _d.timedelta(days=int(days_back * 1.6) + 3)

    total_saved = ok = empty = 0
    pending: list[dict] = []

    for sym, token in rows:
        try:
            raw = await asyncio.to_thread(
                kite.historical_data,
                instrument_token=token, from_date=from_date,
                to_date=to_date, interval="day",
            )
        except Exception:
            raw = []
        if raw:
            ok += 1
            for c in raw:
                ts = c.get("date")
                if ts is None:
                    continue
                if isinstance(ts, _d.datetime) and ts.tzinfo is not None:
                    ts = ts.astimezone(_d.timezone.utc).replace(tzinfo=None)
                pending.append({
                    "symbol": f"{sym}.NS", "timeframe": "1d",
                    "open": float(c.get("open", 0.0)), "high": float(c.get("high", 0.0)),
                    "low": float(c.get("low", 0.0)), "close": float(c.get("close", 0.0)),
                    "volume": float(c.get("volume", 0) or 0), "timestamp": ts,
                })
        else:
            empty += 1

        if len(pending) >= 5000:
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

    summary = {"symbols": len(rows), "fetched_ok": ok, "empty": empty, "saved": total_saved}
    logger.info(f"[sync_full_nse_universe] → {summary}")
    return summary


# ── Live 1-minute candle sync (runs every 60 s during market hours) ───────────

async def sync_live_1m_candles(
    session: AsyncSession,
    symbols: list[str] | None = None,
    *,
    concurrency: int = 3,
    delay_sec: float = 0.1,
) -> dict:
    """Fetch today's 1-minute candles from Kite for every watched symbol.

    Designed to be called every 3 min while NSE is open. Uses upsert so
    repeated runs for the same bar are idempotent. Fetches concurrently
    (semaphore=3) to cover 500+ hub symbols in ~90 seconds.
    Symbols default to the hub universe from DB; falls back to nse_symbols.
    """
    from zoneinfo import ZoneInfo

    _IST = ZoneInfo("Asia/Kolkata")
    now_ist = _dt.datetime.now(_IST).replace(tzinfo=None)
    from_dt = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    to_dt   = now_ist

    if symbols is None:
        symbols = list(settings.nse_symbols) + list(getattr(settings, "nse_mid_symbols", []))

    sem = asyncio.Semaphore(concurrency)
    errors: list[str] = []

    async def _fetch(sym: str) -> list[dict]:
        async with sem:
            result = await get_kite_candles_for_range(sym, from_dt, to_dt, interval="1m")
            await asyncio.sleep(delay_sec)
            return result or []

    results = await asyncio.gather(*[_fetch(s) for s in symbols], return_exceptions=True)

    all_candles: list[dict] = []
    for sym, res in zip(symbols, results):
        if isinstance(res, Exception):
            errors.append(f"{sym}:{res}")
        else:
            all_candles.extend(res)

    saved = 0
    if all_candles:
        saved = await save_candles_to_db(all_candles, session)
        try:
            await session.commit()
        except Exception:
            await session.rollback()

    summary = {
        "symbols": len(symbols),
        "candles": len(all_candles),
        "saved":   saved,
        "errors":  len(errors),
    }
    logger.info(f"[live_1m] → {summary}")
    return summary
