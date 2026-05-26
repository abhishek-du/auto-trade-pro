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
    delay_sec: float = 0.3,
) -> dict:
    """Iterate settings.nse_symbols (+ mid caps) and persist daily candles."""
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
