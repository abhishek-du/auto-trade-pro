"""Price data fetcher for AutoTrade Pro.

Primary  : yfinance  (no API key required)
Fallback : Alpha Vantage (free key — 25 req/day, 5 req/min on the free tier)

All data is OHLCV only — no real trading signals are generated here.
"""

import asyncio
import contextlib as _contextlib
import datetime as _dt
import io as _io
import logging as _logging
import time
from datetime import datetime

import httpx
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Candle
from utils.config import settings
from utils.logger import logger

# yfinance writes its own "$SYMBOL: possibly delisted" diagnostics directly
# via print() AND through a "yfinance" / "peewee" stdlib logger. Both bypass
# our loguru handler. Silence the loggers once at import time; the print()
# path is handled per-call below via redirect_stdout/stderr.
for _name in ("yfinance", "peewee", "urllib3", "yfinance.utils"):
    _logging.getLogger(_name).setLevel(_logging.CRITICAL)

# ── Symbol helpers ────────────────────────────────────────────────────────────

_FOREX_YF: dict[str, str] = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X", "USD/CHF": "USDCHF=X", "USD/CAD": "USDCAD=X",
    "NZD/USD": "NZDUSD=X", "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X",
}


def _to_yf_symbol(symbol: str) -> str:
    """'EUR/USD' → 'EURUSD=X', stock tickers unchanged."""
    if symbol in _FOREX_YF:
        return _FOREX_YF[symbol]
    if "/" in symbol:
        base, quote = symbol.replace(" ", "").split("/", 1)
        return f"{base}{quote}=X"
    return symbol


def _is_forex(symbol: str) -> bool:
    return "/" in symbol or symbol.endswith("=X")


# ── Interval / period tables ──────────────────────────────────────────────────

_YF_INTERVAL: dict[str, str] = {
    "1m": "1m",  "5m": "5m",  "15m": "15m", "30m": "30m",
    "1h": "1h",  "4h": "1h",  "1d": "1d",   "1wk": "1wk",
}

# Max look-back period supported by yfinance for each interval
_YF_PERIOD: dict[str, str] = {
    "1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
    "1h": "730d", "4h": "730d", "1d": "5y", "1wk": "10y",
}

# Intraday interval → Alpha Vantage string (daily uses a different endpoint)
_AV_INTERVAL: dict[str, str] = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min",
}

_AV_BASE      = "https://www.alphavantage.co/query"
_AV_MIN_GAP   = 15.0   # seconds between calls (free tier: max 5/min)
_AV_MAX_RETRY = 3

# Module-level rate-limiter state for Alpha Vantage
_av_lock: asyncio.Lock | None = None   # created lazily inside an event loop
_av_last_call: float = 0.0


def _get_av_lock() -> asyncio.Lock:
    global _av_lock
    if _av_lock is None:
        _av_lock = asyncio.Lock()
    return _av_lock


# ── 1. yfinance ───────────────────────────────────────────────────────────────

async def fetch_candles_yfinance(
    symbol: str,
    period: str = "60d",
    interval: str = "1h",
) -> list[dict]:
    """Fetch OHLCV bars from yfinance, normalised to standard dict format.

    Runs the synchronous yfinance call in a thread-pool executor.
    Returns [] on any error — never raises.

    Output format per candle:
        {symbol, timeframe, open, high, low, close, volume, timestamp (UTC-naive datetime)}
    """
    yf_sym      = _to_yf_symbol(symbol)
    yf_interval = _YF_INTERVAL.get(interval, interval)

    def _sync_fetch() -> list[dict]:
        try:
            # Capture yfinance's chatty stdout/stderr ($SYMBOL: possibly
            # delisted spam on transient Yahoo errors). The data return is
            # what we actually care about — empty df below already signals
            # "nothing usable" without yfinance writing to the process log.
            _buf_out, _buf_err = _io.StringIO(), _io.StringIO()
            with _contextlib.redirect_stdout(_buf_out), _contextlib.redirect_stderr(_buf_err):
                df = yf.Ticker(yf_sym).history(period=period, interval=yf_interval)
            if df.empty:
                logger.warning(f"yfinance: empty response for {yf_sym} ({interval})")
                return []

            df.columns = [c.lower() for c in df.columns]
            df = df.reset_index()
            date_col = df.columns[0]   # 'Datetime' for intraday, 'Date' for daily

            # Convert timezone-aware timestamps → UTC-naive
            ts_col = df[date_col]
            try:
                if ts_col.dt.tz is not None:
                    ts_col = ts_col.dt.tz_convert("UTC").dt.tz_localize(None)
            except Exception:
                pass  # already naive

            rows = []
            for i in range(len(df)):
                ts = ts_col.iloc[i]
                rows.append({
                    "symbol":    symbol,
                    "timeframe": interval,
                    "open":      float(df["open"].iloc[i]),
                    "high":      float(df["high"].iloc[i]),
                    "low":       float(df["low"].iloc[i]),
                    "close":     float(df["close"].iloc[i]),
                    "volume":    float(df["volume"].iloc[i]) if "volume" in df.columns else 0.0,
                    "timestamp": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                })
            return rows

        except Exception as exc:
            logger.error(f"yfinance fetch failed [{yf_sym} {interval}]: {exc}")
            return []

    candles = await asyncio.get_event_loop().run_in_executor(None, _sync_fetch)
    if candles:
        logger.info(
            f"yfinance  ✓  {symbol:<12}  {len(candles):4d} candles  "
            f"interval={interval}  latest={candles[-1]['timestamp'].strftime('%Y-%m-%d %H:%M')}"
        )
    return candles


# ── 2. Alpha Vantage fallback ─────────────────────────────────────────────────

async def fetch_candles_alphavantage(
    symbol: str,
    interval: str,
    api_key: str,
) -> list[dict]:
    """Fetch OHLCV from Alpha Vantage with per-call rate-limiting and exponential back-off.

    Supports intraday (1m–1h) for stocks and forex; daily for stocks.
    Returns [] on failure — never raises.
    """
    global _av_last_call

    is_daily  = interval in ("1d", "1wk")
    av_int    = _AV_INTERVAL.get(interval)
    forex     = _is_forex(symbol)

    if av_int is None and not is_daily:
        logger.warning(f"Alpha Vantage: interval '{interval}' not supported — skipping {symbol}")
        return []

    # Determine endpoint and response key
    if is_daily:
        func_name = "FX_DAILY"              if forex else "TIME_SERIES_DAILY"
        ts_key    = "Time Series FX (Daily)" if forex else "Time Series (Daily)"
    else:
        func_name = "FX_INTRADAY"                  if forex else "TIME_SERIES_INTRADAY"
        ts_key    = f"Time Series FX ({av_int})"   if forex else f"Time Series ({av_int})"

    params: dict = {"function": func_name, "apikey": api_key, "outputsize": "full"}
    if forex and "/" in symbol:
        base, quote = symbol.split("/")
        params["from_symbol"] = base
        params["to_symbol"]   = quote
    elif not forex:
        params["symbol"] = symbol
    if not is_daily:
        params["interval"] = av_int

    lock = _get_av_lock()

    for attempt in range(1, _AV_MAX_RETRY + 1):
        # Global rate-limit: enforce minimum gap between AV calls
        async with lock:
            elapsed = time.monotonic() - _av_last_call
            gap = _AV_MIN_GAP - elapsed
            if gap > 0:
                logger.debug(f"Alpha Vantage rate-limit pause: {gap:.1f}s")
                await asyncio.sleep(gap)
            _av_last_call = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(_AV_BASE, params=params)
                resp.raise_for_status()
                data = resp.json()

            # Free-tier notice / daily limit message
            if "Note" in data or "Information" in data:
                notice = data.get("Note") or data.get("Information", "")
                logger.warning(f"Alpha Vantage notice ({symbol}): {notice[:120]}")
                backoff = _AV_MIN_GAP * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)
                continue

            if ts_key not in data:
                available = list(data.keys())[:6]
                logger.warning(
                    f"Alpha Vantage: ts_key '{ts_key}' missing for {symbol} — "
                    f"got keys: {available}"
                )
                return []

            rows: list[dict] = []
            for ts_str, bar in data[ts_key].items():
                fmt = "%Y-%m-%d %H:%M:%S" if " " in ts_str else "%Y-%m-%d"
                ts  = datetime.strptime(ts_str, fmt)
                rows.append({
                    "symbol":    symbol,
                    "timeframe": interval,
                    "open":      float(bar["1. open"]),
                    "high":      float(bar["2. high"]),
                    "low":       float(bar["3. low"]),
                    "close":     float(bar["4. close"]),
                    "volume":    float(bar.get("5. volume") or 0),
                    "timestamp": ts,
                })

            rows.sort(key=lambda r: r["timestamp"])
            logger.info(
                f"Alpha Vantage ✓  {symbol:<12}  {len(rows):4d} candles  "
                f"interval={interval}  latest={rows[-1]['timestamp'].strftime('%Y-%m-%d %H:%M')}"
            )
            return rows

        except httpx.HTTPStatusError as exc:
            logger.error(
                f"Alpha Vantage HTTP {exc.response.status_code} for {symbol} "
                f"(attempt {attempt}/{_AV_MAX_RETRY})"
            )
        except Exception as exc:
            logger.error(
                f"Alpha Vantage failed for {symbol} attempt {attempt}/{_AV_MAX_RETRY}: {exc}"
            )

        if attempt < _AV_MAX_RETRY:
            backoff = _AV_MIN_GAP * (2 ** (attempt - 1))
            logger.info(f"Alpha Vantage: retry {symbol} in {backoff:.0f}s")
            await asyncio.sleep(backoff)

    logger.error(f"Alpha Vantage: all {_AV_MAX_RETRY} attempts exhausted for {symbol}")
    return []


# ── 3. Unified fetcher ────────────────────────────────────────────────────────

async def fetch_candles(symbol: str, timeframe: str = "1h") -> list[dict]:
    """Try yfinance first; fall back to Alpha Vantage if yfinance returns nothing.

    Logs which source was used.  Returns [] on complete failure — never raises.
    """
    period  = _YF_PERIOD.get(timeframe, "60d")
    candles = await fetch_candles_yfinance(symbol, period=period, interval=timeframe)

    if candles:
        return candles

    logger.info(f"yfinance returned 0 bars for {symbol}/{timeframe} — trying Alpha Vantage")

    if not settings.alpha_vantage_available:
        logger.warning("ALPHA_VANTAGE_KEY not configured — no fallback available")
        return []

    candles = await fetch_candles_alphavantage(symbol, timeframe, settings.ALPHA_VANTAGE_KEY)
    if candles:
        return candles

    logger.error(f"All price sources failed for {symbol}/{timeframe}")
    return []


# ── 4. DB upsert ──────────────────────────────────────────────────────────────

def _to_naive_utc(ts) -> datetime:
    """Convert any datetime to a UTC-naive datetime (TIMESTAMP WITHOUT TIME ZONE).

    asyncpg requires naive datetimes for TIMESTAMP WITHOUT TIME ZONE columns.
    yfinance can return timezone-aware timestamps in IST, US/Eastern, or UTC
    depending on the ticker and interval — always normalise before DB insert.
    """
    if ts is None:
        return ts
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        ts = ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return ts


async def save_candles_to_db(candles: list[dict], session: AsyncSession) -> int:
    """Batch-insert candles, skipping any that already exist.

    Uses INSERT … ON CONFLICT DO NOTHING against the (symbol, timeframe, timestamp)
    unique constraint.  Returns the count of genuinely new rows inserted.

    Rows are sent in chunks of 3 000 to stay under asyncpg's 32 767 parameter
    limit (3 000 rows × 8 columns = 24 000 params per statement).

    Timestamps are normalised to UTC-naive before insert to avoid asyncpg
    DataError when callers pass timezone-aware datetimes.
    """
    if not candles:
        return 0

    rows = [
        {
            "symbol":    c["symbol"],
            "timeframe": c["timeframe"],
            "open":      c["open"],
            "high":      c["high"],
            "low":       c["low"],
            "close":     c["close"],
            "volume":    c["volume"],
            "timestamp": _to_naive_utc(c["timestamp"]),
        }
        for c in candles
    ]

    _CHUNK = 3_000
    total  = 0
    for i in range(0, len(rows), _CHUNK):
        chunk = rows[i : i + _CHUNK]
        try:
            stmt   = pg_insert(Candle).values(chunk).on_conflict_do_nothing(constraint="uq_candle_bar")
            result = await session.execute(stmt)
            total += result.rowcount
        except Exception as exc:
            logger.error(f"save_candles_to_db error (chunk {i}–{i+len(chunk)}): {exc}")
            await session.rollback()
            return total

    await session.flush()
    return total


# ── 5. DB query ───────────────────────────────────────────────────────────────

async def get_latest_candles(
    symbol: str,
    timeframe: str,
    limit: int,
    session: AsyncSession,
) -> list[Candle]:
    """Return the last N candles for a symbol/timeframe, newest first.

    Returns [] if no data exists yet.
    """
    result = await session.execute(
        select(Candle)
        .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
        .order_by(Candle.timestamp.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_latest_price(symbol: str) -> float | None:
    """Return the most recent close price using 1-minute yfinance data."""
    candles = await fetch_candles_yfinance(symbol, period="1d", interval="1m")
    if candles:
        return candles[-1]["close"]
    logger.error(f"Could not fetch latest price for {symbol}")
    return None


async def get_quote(symbol: str) -> dict:
    """Quick summary: price, prev_close, volume, market_cap."""
    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(
            None, lambda: yf.Ticker(_to_yf_symbol(symbol)).fast_info
        )
        return {
            "symbol":     symbol,
            "price":      getattr(info, "last_price",                None),
            "prev_close": getattr(info, "previous_close",            None),
            "volume":     getattr(info, "three_month_average_volume", None),
            "market_cap": getattr(info, "market_cap",                None),
        }
    except Exception as exc:
        logger.error(f"Quote fetch failed for {symbol}: {exc}")
        return {"symbol": symbol, "price": None}


# ── 6. Batch price crawl ──────────────────────────────────────────────────────

async def run_price_crawl(session: AsyncSession) -> dict:
    """Fetch 1h candles for every watchlist symbol and persist to the DB.

    Symbols are processed sequentially so we never flood yfinance.
    A single symbol failure is caught, logged, and the loop continues.

    Returns:
        total_symbols         : number of symbols attempted
        total_candles_fetched : raw candle count across all symbols
        total_candles_saved   : genuinely new rows inserted into DB
        errors                : list of "<symbol>: <reason>" strings
    """
    forex_syms = [(s, "forex") for s in settings.forex_symbols]
    stock_syms = [(s, "stock") for s in settings.stock_symbols]
    all_symbols = forex_syms + stock_syms

    total_fetched = 0
    total_saved   = 0
    errors: list[str] = []

    logger.info(
        f"━━ Price crawl START ━━  {len(all_symbols)} symbols "
        f"({len(forex_syms)} forex  {len(stock_syms)} stocks)"
    )

    for sym, kind in all_symbols:
        try:
            candles = await fetch_candles(sym, timeframe="1h")

            if not candles:
                msg = f"No data returned for {sym}"
                logger.warning(f"  ✗  {sym}: {msg}")
                errors.append(f"{sym}: {msg}")
                continue

            saved = await save_candles_to_db(candles, session)
            total_fetched += len(candles)
            total_saved   += saved

            logger.info(
                f"  ✓  [{kind:5}] {sym:<12}  "
                f"fetched={len(candles):4d}  new={saved:4d}  "
                f"latest={candles[-1]['timestamp'].strftime('%Y-%m-%d %H:%M')}"
            )

        except Exception as exc:
            msg = str(exc)
            logger.error(f"  ✗  {sym}: {msg}")
            errors.append(f"{sym}: {msg}")

    summary = {
        "total_symbols":         len(all_symbols),
        "total_candles_fetched": total_fetched,
        "total_candles_saved":   total_saved,
        "errors":                errors,
    }
    logger.info(
        f"━━ Price crawl DONE  ━━  "
        f"symbols={len(all_symbols)}  fetched={total_fetched}  "
        f"saved={total_saved}  errors={len(errors)}"
    )
    return summary
