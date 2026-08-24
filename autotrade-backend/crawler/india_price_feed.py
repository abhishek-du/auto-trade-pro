"""Indian market price and NAV crawler for AutoTrade Pro.

Strategy
--------
  yfinance     — all price data: NSE/BSE stocks, indices, India VIX,
                 INR forex pairs, and commodities.
  mftool       — mutual fund NAV parsing only (AMFI flat-file format is
                 tedious to maintain; delegate to the library).
  nselib       — FPI investment data as fallback (fills gaps yfinance
                 does not cover).
  Custom httpx — FII/DII flows and options chain (simple NSE JSON
                 endpoints; own the code completely).

Do NOT import or install NSEpy (dead since 2018) or jugaad-trader
(inactive; 156 weekly downloads as of May 2026).
"""

from __future__ import annotations

import asyncio
import contextlib as _contextlib
import datetime
import io as _io
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


def _silently(fn):
    """Run a sync yfinance callable with stdout/stderr captured.

    yfinance prints "$SYMBOL: possibly delisted" to stdout when Yahoo
    transiently fails; those lines bypass Python logging and flood the
    process log. Empty-DataFrame return below is sufficient to detect
    the failure programmatically — we don't need the chatter.
    """
    with _contextlib.redirect_stdout(_io.StringIO()), _contextlib.redirect_stderr(_io.StringIO()):
        return fn()
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.price_feed import save_candles_to_db
from utils.config import settings
from utils.logger import logger

# ── NSE market calendar ───────────────────────────────────────────────────────

import os
_HOLIDAY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nse_holidays.txt")
def _load_holidays() -> set[str]:
    from utils.nse_market_status import fetch_nse_holidays_sync
    try:
        holidays_map = fetch_nse_holidays_sync()
        if holidays_map:
            return set(holidays_map.keys())
        
        # Fallback to local file if API fails
        with open(_HOLIDAY_FILE, "r") as f:
            days = {line.strip() for line in f if line.strip() and not line.startswith("#")}
        import datetime as _dt
        _yr = str(_dt.date.today().year)
        if days and not any(d.startswith(_yr) for d in days):
            logger.warning(
                f"[nse_calendar] API failed and nse_holidays.txt is STALE for {_yr}."
            )
        return days
    except Exception:
        return set()

NSE_HOLIDAYS = _load_holidays()

# Index symbols → human-readable names
NIFTY_INDEX_SYMBOLS: dict[str, str] = {
    "^NSEI":   "NIFTY50",
    "^BSESN":  "SENSEX",
    "^NSEBANK": "BANKNIFTY",
}

# Module-level mftool singleton — expensive to create, reuse across calls.
_MF_TOOL = None


# ── 1. Market hours check ─────────────────────────────────────────────────────

def is_nse_market_open() -> bool:
    """Return True when NSE is currently open for trading (IST).

    Excludes weekends and all 2026 exchange holidays.
    Uses settings.IST_TIMEZONE so the timezone string is configurable.
    """
    ist = ZoneInfo(settings.IST_TIMEZONE)
    now = datetime.datetime.now(ist)

    if now.weekday() >= 5:          # Saturday or Sunday
        return False

    if now.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
        return False

    market_open = now.replace(
        hour=settings.NSE_OPEN_HOUR,
        minute=settings.NSE_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )
    market_close = now.replace(
        hour=settings.NSE_CLOSE_HOUR,
        minute=settings.NSE_CLOSE_MINUTE,
        second=0,
        microsecond=0,
    )
    return market_open <= now <= market_close


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_utc_naive(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with a UTC-naive DatetimeIndex.

    yfinance may return a tz-aware index in IST, US/Eastern, or UTC.
    Standardise everything to UTC-naive before building candle dicts.
    """
    df = df.copy()
    if df.index.tz is None:
        # Assume IST for NSE tickers that lack explicit tz info
        df.index = df.index.tz_localize(settings.IST_TIMEZONE)
    df.index = df.index.tz_convert("UTC").tz_localize(None)
    return df


def _fast_info_float(info, key: str, default: float = 0.0) -> float:
    """Safe float extraction from yfinance fast_info (dict-like or attribute)."""
    try:
        val = info.get(key, default)
    except AttributeError:
        val = getattr(info, key, default)
    if val is None:
        return default
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


# ── 2. NSE candle fetcher ─────────────────────────────────────────────────────

import time
_YF_RATE_LIMIT_UNTIL = 0.0

def fetch_nse_candles(
    symbol: str,
    interval: str = "1h",
    period: str = "60d",
) -> list[dict]:
    """Fetch OHLCV candles from yfinance for any NSE/BSE/index symbol.

    Parameters
    ----------
    symbol   : yfinance ticker, e.g. ``RELIANCE.NS``, ``^NSEI``, ``GC=F``.
    interval : Candle size — ``'1m'``, ``'5m'``, ``'15m'``, ``'1h'``, ``'1d'``.
    period   : Look-back window accepted by yfinance, e.g. ``'60d'``, ``'1y'``.

    Returns
    -------
    list of dicts compatible with ``save_candles_to_db()``.
    Returns ``[]`` on any error — never raises.
    """
    global _YF_RATE_LIMIT_UNTIL
    if time.time() < _YF_RATE_LIMIT_UNTIL:
        return []
        
    from utils.config import settings as _s
    if getattr(_s, "ZERODHA_ENABLED", False) and getattr(_s, "ZERODHA_ACCESS_TOKEN", ""):
        try:
            import datetime as _dt
            from crawler.zerodha_historical import get_token
            from crawler.zerodha_kite_lib import get_historical_data

            yf_to_kite = {
                "1m": "minute", "3m": "3minute", "5m": "5minute",
                "10m": "10minute", "15m": "15minute", "30m": "30minute",
                "1h": "60minute", "1d": "day", "1wk": "day", "1mo": "day"
            }
            kite_interval = yf_to_kite.get(interval)
            
            days_back = 60
            if period.endswith("d"): days_back = int(period[:-1])
            elif period.endswith("mo"): days_back = int(period[:-2]) * 30
            elif period.endswith("y"): days_back = int(period[:-1]) * 365
            elif period == "max": days_back = 3650
            
            pure_sym = symbol
            if pure_sym.endswith(".NS"): pure_sym = pure_sym[:-3]
            elif pure_sym.endswith(".BO"): pure_sym = pure_sym[:-3]
            elif pure_sym == "^NSEI": pure_sym = "NIFTY 50"
            elif pure_sym == "^NSEBANK": pure_sym = "NIFTY BANK"
            
            token = get_token(pure_sym)
            if token and kite_interval:
                to_date = _dt.datetime.now()
                from_date = to_date - _dt.timedelta(days=days_back)
                
                raw = []
                for attempt in range(4):
                    try:
                        raw = get_historical_data(
                            instrument_token=token,
                            from_date=from_date,
                            to_date=to_date,
                            interval=kite_interval,
                            oi=False
                        )
                        break
                    except Exception as k_exc:
                        if "Too many requests" in str(k_exc) or "429" in str(k_exc):
                            if attempt < 3:
                                time.sleep(1.0 * (attempt + 1))
                                continue
                        break
                
                if raw:
                    rows = []
                    for c in raw:
                        ts = c.get("date") or c.get("timestamp")
                        if not ts: continue
                        if isinstance(ts, str):
                            try: ts = _dt.datetime.fromisoformat(ts)
                            except: pass
                        if isinstance(ts, _dt.datetime):
                            if ts.tzinfo is not None:
                                ts = ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
                            if interval in ("1d", "1wk", "1mo"):
                                ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)
                        
                        rows.append({
                            "symbol": symbol,
                            "timeframe": interval,
                            "open": float(c.get("open", 0)),
                            "high": float(c.get("high", 0)),
                            "low": float(c.get("low", 0)),
                            "close": float(c.get("close", 0)),
                            "volume": float(c.get("volume", 0)),
                            "timestamp": ts,
                        })
                    if rows:
                        logger.info(f"Kite NSE  ✓  {symbol:<15}  {len(rows):4d} candles  interval={interval}  latest={rows[-1]['timestamp'].strftime('%Y-%m-%d %H:%M')}")
                        return rows
        except Exception as e:
            pass

    # Upstox Fallback
    if getattr(_s, "UPSTOX_ENABLED", False) and getattr(_s, "UPSTOX_ACCESS_TOKEN", ""):
        try:
            import datetime as _dt
            import asyncio
            from crawler.upstox_historical import get_historical_candles

            yf_to_upstox = {
                "1m": "1minute", "5m": "5minute", "30m": "30minute",
                "1d": "day", "1wk": "week", "1mo": "month"
            }
            # Upstox supports fewer intervals. If not matched, fallback to yf.
            upstox_interval = yf_to_upstox.get(interval)

            if upstox_interval:
                days_back = 60
                if period.endswith("d"): days_back = int(period[:-1])
                elif period.endswith("mo"): days_back = int(period[:-2]) * 30
                elif period.endswith("y"): days_back = int(period[:-1]) * 365
                elif period == "max": days_back = 3650
                
                pure_sym = symbol
                if pure_sym.endswith(".NS"): pure_sym = pure_sym[:-3]
                elif pure_sym.endswith(".BO"): pure_sym = pure_sym[:-3]
                elif pure_sym == "^NSEI": pure_sym = "NIFTY 50"
                elif pure_sym == "^NSEBANK": pure_sym = "NIFTY BANK"

                to_date = _dt.date.today()
                from_date = to_date - _dt.timedelta(days=days_back)
                
                # Try to use asyncio.run safely
                raw = []
                try:
                    loop = asyncio.get_running_loop()
                    # Cannot use asyncio.run in a running loop, but fetch_nse_candles
                    # is usually run in a ThreadPoolExecutor. So this will raise RuntimeError.
                except RuntimeError:
                    # Good, no running loop. We can use asyncio.run
                    raw = asyncio.run(get_historical_candles(
                        symbol=pure_sym,
                        interval=upstox_interval,
                        from_date=from_date.isoformat(),
                        to_date=to_date.isoformat()
                    ))

                if raw:
                    rows = []
                    for c in raw:
                        ts = c.get("timestamp")
                        if not ts: continue
                        if isinstance(ts, str):
                            try: ts = _dt.datetime.fromisoformat(ts)
                            except: pass
                        if isinstance(ts, _dt.datetime):
                            if ts.tzinfo is not None:
                                ts = ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
                            if interval in ("1d", "1wk", "1mo"):
                                ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)

                        rows.append({
                            "symbol": symbol,
                            "timeframe": interval,
                            "open": float(c.get("open", 0)),
                            "high": float(c.get("high", 0)),
                            "low": float(c.get("low", 0)),
                            "close": float(c.get("close", 0)),
                            "volume": float(c.get("volume", 0)),
                            "timestamp": ts,
                        })
                    if rows:
                        logger.info(f"Upstox NSE✓  {symbol:<15}  {len(rows):4d} candles  interval={interval}  latest={rows[-1]['timestamp'].strftime('%Y-%m-%d %H:%M')}")
                        return rows
        except Exception as e:
            pass # Fallback to yfinance

    try:
        time.sleep(0.1) # Small throttle to avoid triggering 429
        df = _silently(lambda: yf.Ticker(symbol).history(period=period, interval=interval))

        if df.empty:
            logger.warning(f"yfinance NSE: empty response for {symbol} ({interval})")
            return []

        if interval in ("1d", "1wk", "1mo"):
            # A daily/weekly bar is a calendar date, not a moment in time. Applying
            # an intraday IST→UTC shift pushes midnight-IST bars onto the previous
            # day (Fridays land on the weekend), corrupting date alignment. Strip
            # tz and floor to the date so every daily bar keeps its true date.
            df = df.copy()
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            df.index = df.index.normalize()
        else:
            df = _to_utc_naive(df)

        rows: list[dict] = []
        for row in df.itertuples():
            rows.append({
                "symbol":    symbol,
                "timeframe": interval,
                "open":      float(row.Open),
                "high":      float(row.High),
                "low":       float(row.Low),
                "close":     float(row.Close),
                "volume":    float(getattr(row, "Volume", 0.0) or 0.0),
                "timestamp": row.Index.to_pydatetime(),
            })

        logger.info(
            f"yfinance NSE  ✓  {symbol:<15}  {len(rows):4d} candles  "
            f"interval={interval}  latest={rows[-1]['timestamp'].strftime('%Y-%m-%d %H:%M')}"
        )
        return rows

    except Exception as exc:
        if "Rate limit" in str(exc) or "429" in str(exc) or "RateLimitError" in type(exc).__name__:
            if time.time() > _YF_RATE_LIMIT_UNTIL:
                logger.warning(f"[fetch_nse_candles] Yahoo Finance rate limit hit on {symbol}. Muting yfinance candle fetches for 15 mins.")
                _YF_RATE_LIMIT_UNTIL = time.time() + 900
            return []
        logger.warning(f"fetch_nse_candles: failed {symbol}: {exc}")
        return []



# ── 3. NIFTY / SENSEX / BANKNIFTY snapshots ──────────────────────────────────

def fetch_nifty_indices() -> dict:
    """Fetch live price snapshots for NIFTY50, SENSEX, and BANKNIFTY.

    Returns
    -------
    dict keyed by human name (``'NIFTY50'``, ``'SENSEX'``, ``'BANKNIFTY'``)::

        {
            'NIFTY50': {
                'price': 24500.0,
                'change': 120.5,
                'change_pct': 0.49,
                'high_52w': 26277.35,
                'low_52w': 21964.0,
            },
            ...
        }
    """
    snapshots: dict = {}

    for sym, name in NIFTY_INDEX_SYMBOLS.items():
        try:
            info = _silently(lambda s=sym: yf.Ticker(s).fast_info)
            price      = _fast_info_float(info, "last_price")
            prev_close = _fast_info_float(info, "previous_close")
            change     = price - prev_close if prev_close else 0.0
            change_pct = (change / prev_close * 100.0) if prev_close else 0.0

            snapshots[name] = {
                "price":      round(price, 2),
                "change":     round(change, 2),
                "change_pct": round(change_pct, 4),
                "high_52w":   _fast_info_float(info, "year_high"),
                "low_52w":    _fast_info_float(info, "year_low"),
            }
            logger.info(
                f"Index  {name:<12}  price={price:,.2f}  "
                f"change={change:+,.2f} ({change_pct:+.2f}%)"
            )
        except Exception as exc:
            logger.warning(f"fetch_nifty_indices: failed {sym} ({name}): {exc}")
            snapshots[name] = {
                "price": 0.0, "change": 0.0,
                "change_pct": 0.0, "high_52w": 0.0, "low_52w": 0.0,
            }

    return snapshots


# ── 4. India VIX ─────────────────────────────────────────────────────────────

def fetch_india_vix() -> float:
    """Fetch India VIX (NSE fear gauge) from yfinance with nselib fallback.

    Returns a float (default 15.0 when both sources fail — neutral level).
    Logs the final value regardless of source.
    """
    # Primary: yfinance history — more reliable than fast_info for index tickers
    try:
        df = _silently(lambda: yf.Ticker("^INDIAVIX").history(
            period="5d", interval="1d", auto_adjust=False
        ))
        if not df.empty:
            value = float(df["Close"].dropna().iloc[-1])
            if value > 0:
                logger.info(f"India VIX: {value:.2f}  (source: yfinance history)")
                return value
    except Exception as exc:
        logger.warning(f"yfinance India VIX history failed: {exc}")

    # Fallback: yfinance download (different code path, sometimes succeeds when Ticker fails)
    try:
        df2 = _silently(lambda: yf.download(
            "^INDIAVIX", period="5d", interval="1d",
            progress=False, auto_adjust=False
        ))
        if not df2.empty:
            # yf.download returns a MultiIndex columns DataFrame when called
            # with a single ticker as a string, so df2["Close"] is a 1-col
            # DataFrame. Squeeze before float() to avoid the pandas
            # FutureWarning about implicit float-on-Series casts.
            close_series = df2["Close"].squeeze("columns") if hasattr(df2["Close"], "squeeze") else df2["Close"]
            close_clean = close_series.dropna()
            value = float(close_clean.iloc[-1]) if len(close_clean) else 0.0
            if value > 0:
                logger.info(f"India VIX: {value:.2f}  (source: yfinance download)")
                return value
    except Exception as exc:
        logger.warning(f"yfinance India VIX download failed: {exc}")

    logger.warning("India VIX: yfinance unavailable — using neutral default 15.0")
    return 15.0


# ── 4b. Kite-first live data (indices, VIX, regime daily candles) ────────────
# yfinance is aggressively rate-limited during market hours (HTTP 429), which
# froze the index feed and the regime's daily-candle input, blocking every new
# long on a stale WEAK_BEAR read. Zerodha (Kite) has a paid, reliable feed for
# exactly these symbols, so we now source the decision-critical data from Kite
# and keep yfinance only as a fallback when Kite is unavailable (no token /
# post-403 cooldown). Bulk equity candles still use yfinance for now.

# Daily candles that MUST stay fresh — the 5-state market-regime engine reads
# NIFTYBEES.NS 1d, and the indices back dashboards + shock guard.
_REGIME_DAILY_SYMBOLS: tuple[str, ...] = ("NIFTYBEES.NS", "^NSEI", "^NSEBANK", "^BSESN")


async def fetch_indices_kite_first() -> dict:
    """Live NIFTY50 / SENSEX / BANKNIFTY snapshots from Kite, yfinance fallback.

    Same return shape as fetch_nifty_indices() so callers are unchanged.
    """
    try:
        from crawler.zerodha_market import get_live_prices, get_full_quote
        # our-symbol → human name (matches NIFTY_INDEX_SYMBOLS values)
        wanted = {"^NSEI": "NIFTY50", "^BSESN": "SENSEX", "^NSEBANK": "BANKNIFTY"}
        px = await get_live_prices(list(wanted.keys()))
        out: dict = {}
        for sym, name in wanted.items():
            row = px.get(sym)
            if not row or not row.get("last_price"):
                continue
            price = float(row["last_price"])
            prev = 0.0
            try:                                   # prev close for change% (dashboard only)
                q = await get_full_quote(sym)
                prev = float((q or {}).get("ohlc", {}).get("close") or 0.0)
            except Exception:
                prev = 0.0
            change = price - prev if prev else 0.0
            out[name] = {
                "price":      round(price, 2),
                "change":     round(change, 2),
                "change_pct": round((change / prev * 100.0) if prev else 0.0, 4),
                "high_52w":   0.0,
                "low_52w":    0.0,
            }
        if len(out) == len(wanted):
            logger.info(f"Indices ✓ Kite  " + "  ".join(f"{k}={v['price']:,.1f}" for k, v in out.items()))
            return out
        logger.warning(f"[india_price_feed] Kite indices incomplete ({len(out)}/3) — yfinance fallback")
    except Exception as exc:
        logger.warning(f"[india_price_feed] Kite indices failed ({exc}) — yfinance fallback")
    return await asyncio.get_event_loop().run_in_executor(None, fetch_nifty_indices)


async def fetch_vix_kite_first() -> float:
    """India VIX from Kite, yfinance fallback (never raises)."""
    try:
        from crawler.zerodha_market import get_full_quote
        q = await get_full_quote("^INDIAVIX")
        val = float((q or {}).get("last_price") or 0.0)
        if val > 0:
            logger.info(f"India VIX: {val:.2f}  (source: Kite)")
            return val
    except Exception as exc:
        logger.warning(f"[india_price_feed] Kite VIX failed ({exc}) — yfinance fallback")
    return await asyncio.get_event_loop().run_in_executor(None, fetch_india_vix)


async def sync_regime_daily_candles_kite(session: AsyncSession) -> int:
    """Refresh recent DAILY candles for the regime + index symbols via Kite.

    Keeps NIFTYBEES.NS 1d current so the market-regime engine never decides on
    stale data. Idempotent (save_candles_to_db upserts). Returns rows saved.
    """
    try:
        from crawler.zerodha_market import get_kite_historical, hydrate_tokens_from_db
    except Exception:
        return 0
    try:
        await hydrate_tokens_from_db(session)        # ensure NIFTYBEES/index tokens are loaded
    except Exception:
        pass
    from sqlalchemy import and_ as _and, delete as _delete
    from db.models import Candle as _Candle

    today = datetime.date.today()
    frm = (today - datetime.timedelta(days=15)).isoformat()
    to = today.isoformat()
    window_start = datetime.datetime(today.year, today.month, today.day) - datetime.timedelta(days=15)
    saved_total = 0
    for sym in _REGIME_DAILY_SYMBOLS:
        try:
            candles = await get_kite_historical(sym, frm, to, "1d", session)
            # get_kite_historical applies an intraday IST→UTC shift that pushes a
            # midnight-IST *daily* bar back one calendar day. Undo it so each 1d
            # bar keeps its true IST trading date — otherwise the regime series
            # gets off-by-one bars.
            for c in candles:
                ts = c["timestamp"]
                ist_date = (ts + datetime.timedelta(hours=5, minutes=30)).date()
                c["timestamp"] = datetime.datetime(ist_date.year, ist_date.month, ist_date.day)
            if not candles:
                continue
            # Kite is authoritative for these regime symbols: clear any existing
            # 1d rows in the window first (removes stale/off-by-one yfinance dupes),
            # then insert the clean Kite bars → exactly one bar per trading day.
            await session.execute(
                _delete(_Candle).where(_and(
                    _Candle.symbol == sym,
                    _Candle.timeframe == "1d",
                    _Candle.timestamp >= window_start,
                ))
            )
            await session.commit()
            saved_total += await save_candles_to_db(candles, session)
        except Exception as exc:
            logger.warning(f"[india_price_feed] regime daily sync failed {sym}: {exc}")
    if saved_total:
        logger.info(f"[india_price_feed] regime daily candles refreshed via Kite — {saved_total} rows")
    return saved_total


# ── 5. Single mutual fund NAV ─────────────────────────────────────────────────

def _get_mf_tool():
    """Return the module-level Mftool singleton, creating it on first call."""
    global _MF_TOOL  # noqa: PLW0603
    if _MF_TOOL is None:
        from mftool import Mftool  # noqa: PLC0415
        _MF_TOOL = Mftool()
    return _MF_TOOL


def fetch_mutual_fund_nav(scheme_code: str) -> dict:
    """Fetch the current NAV for one AMFI scheme code using mftool.

    mftool is used here because AMFI changes their flat-file format
    periodically. Delegating the parsing means a format change is fixed
    by a library upgrade, not by editing this file.

    Returns a dict with keys: scheme_code, name, nav, date, change, change_pct.
    Returns zeroed values on failure — never raises.
    """
    try:
        mf   = _get_mf_tool()
        data = mf.get_scheme_quote(scheme_code) or {}

        nav        = _to_float(data.get("nav") or data.get("NAV"))
        change     = _to_float(data.get("change") or data.get("Change"))
        change_pct = _to_float(
            data.get("change_pct")
            or data.get("change_percent")
            or data.get("pChange")
        )

        return {
            "scheme_code": scheme_code,
            "name":        data.get("scheme_name") or data.get("name") or "",
            "nav":         nav,
            "date":        data.get("date") or data.get("last_updated") or "",
            "change":      change,
            "change_pct":  change_pct,
        }

    except Exception as exc:
        logger.warning(f"fetch_mutual_fund_nav: failed scheme {scheme_code}: {exc}")
        return {
            "scheme_code": scheme_code,
            "name": "", "nav": 0.0,
            "date": "", "change": 0.0, "change_pct": 0.0,
        }


# ── 6. All configured mutual fund NAVs ───────────────────────────────────────

def _nav_period_return(historic: pd.DataFrame, days: int) -> float:
    """Compute return (%) from ``days`` ago to today using a NAV DataFrame."""
    if historic is None or historic.empty:
        return 0.0

    historic = historic.sort_index()

    # Column name varies between mftool versions
    nav_col = next(
        (c for c in ("nav", "NAV", "Net Asset Value") if c in historic.columns),
        None,
    )
    if nav_col is None:
        return 0.0

    try:
        series = pd.to_numeric(historic[nav_col], errors="coerce").dropna()
        if len(series) < 2:
            return 0.0
        latest = float(series.iloc[-1])
        base   = float(series.iloc[max(0, len(series) - days)])
        return round(((latest - base) / base) * 100.0, 4) if base else 0.0
    except Exception:
        return 0.0


def fetch_all_mutual_fund_navs() -> list[dict]:
    """Fetch NAVs and historical returns for all configured AMFI schemes.

    Iterates ``settings.WATCHLIST_MUTUAL_FUND_SCHEMES``.
    Each entry includes one-month and one-year returns calculated from
    the historical NAV series returned by mftool.

    Returns list of dicts: {scheme_code, name, nav, one_month_return, one_year_return}.
    Returns [] if mftool is unavailable.
    """
    results: list[dict] = []

    try:
        mf = _get_mf_tool()
    except Exception as exc:
        logger.warning(f"mftool unavailable — skipping MF NAV fetch: {exc}")
        return results

    for scheme_code in settings.WATCHLIST_MUTUAL_FUND_SCHEMES:
        try:
            nav_data = fetch_mutual_fund_nav(scheme_code)
            historic = mf.get_scheme_historical_nav(scheme_code, as_Dataframe=True)

            results.append({
                "scheme_code":      scheme_code,
                "name":             nav_data["name"],
                "nav":              nav_data["nav"],
                "one_month_return": _nav_period_return(historic, 30),
                "one_year_return":  _nav_period_return(historic, 365),
            })
            logger.info(
                f"MF NAV  {scheme_code}  {nav_data['name'][:40]:<40}  "
                f"nav={nav_data['nav']}"
            )
        except Exception as exc:
            logger.warning(f"fetch_all_mutual_fund_navs: failed scheme {scheme_code}: {exc}")
            continue

    return results


# ── 7. BSE announcements via bsedata ─────────────────────────────────────────

def fetch_bse_announcements(symbol_bse_code: str) -> list[dict]:
    """Fetch BSE quote and corporate action context via the bsedata library.

    bsedata is used because BSE HTML is complex to parse and scrape;
    the library absorbs format changes and is actively maintained.

    Parameters
    ----------
    symbol_bse_code : BSE numeric code, e.g. ``'500325'`` for RELIANCE.

    Returns
    -------
    list with one summary dict on success, empty list on failure.
    """
    try:
        from bsedata.bse import BSE  # noqa: PLC0415

        bse    = BSE(update_codes=False)
        result = bse.getQuote(symbol_bse_code)

        if not result:
            logger.warning(f"bsedata: empty response for code {symbol_bse_code}")
            return []

        return [{
            "symbol_bse_code": symbol_bse_code,
            "company":         result.get("companyName") or result.get("company_name") or "",
            "price":           _to_float(result.get("currentValue") or result.get("price")),
            "change":          result.get("change") or "",
            "change_pct":      result.get("pChange") or result.get("change_percent") or "",
            "raw":             result,
        }]

    except Exception as exc:
        logger.warning(f"fetch_bse_announcements: unavailable for {symbol_bse_code}: {exc}")
        return []


# ── 8. FPI investment data via nselib ─────────────────────────────────────────

def fetch_fpi_investment_data() -> dict:
    """Fetch official FPI (Foreign Portfolio Investment) data from NSDL via nselib.

    This is distinct from the FII/DII flows endpoint:
    - FPI is the SEBI-official category reported by NSDL.
    - FII/DII flows come from the NSE API (see fii_dii_crawler.py).

    Returns dict: {net_investment, buy_value, sell_value, date}.
    Returns zeroed values when nselib or NSDL are unavailable.
    """
    ist      = ZoneInfo(settings.IST_TIMEZONE)
    today_str = datetime.datetime.now(ist).strftime("%d-%m-%Y")

    try:
        from nselib import capital_market  # noqa: PLC0415

        df = capital_market.nsdl_fpi_investment_activity(trade_date=today_str)

        if df is None or df.empty:
            logger.warning("nselib FPI: empty response — returning zeroes")
            return {
                "net_investment": 0.0,
                "buy_value":  0.0,
                "sell_value": 0.0,
                "date":       today_str,
            }

        row = df.iloc[-1]

        buy_value = _to_float(
            row.get("Buy Value") or row.get("buy_value")
            or row.get("Gross Purchases") or row.get("gross_purchases")
        )
        sell_value = _to_float(
            row.get("Sell Value") or row.get("sell_value")
            or row.get("Gross Sales") or row.get("gross_sales")
        )
        net_investment = _to_float(
            row.get("Net Value") or row.get("net_investment")
            or row.get("Net Investment"),
            default=buy_value - sell_value,
        )

        logger.info(
            f"FPI  net={net_investment:+,.2f}  "
            f"buy={buy_value:,.2f}  sell={sell_value:,.2f}  date={today_str}"
        )
        return {
            "net_investment": net_investment,
            "buy_value":      buy_value,
            "sell_value":     sell_value,
            "date":           str(row.get("Date") or today_str),
        }

    except Exception as exc:
        logger.warning(f"fetch_fpi_investment_data: unavailable: {exc}")
        return {
            "net_investment": 0.0,
            "buy_value":  0.0,
            "sell_value": 0.0,
            "date":       today_str,
        }


# ── 9. Orchestrator ──────────────────────────────────────────────────────────

async def run_india_price_crawl(
    session: AsyncSession,
    ignore_market_hours: bool = False,
) -> dict:
    """Fetch OHLCV candles for all Indian watchlist symbols and persist to DB.

    Scope
    -----
    Large-cap NSE stocks   — settings.nse_symbols          (e.g. RELIANCE.NS)
    Mid-cap NSE stocks     — settings.nse_mid_symbols       (e.g. PERSISTENT.NS)
    NIFTY / SENSEX indices — settings.WATCHLIST_NIFTY_INDICES (^NSEI, ^BSESN, ^NSEBANK)
    Indian forex pairs     — settings.WATCHLIST_INDIAN_FOREX  (USDINR=X …)
    Commodities            — settings.WATCHLIST_COMMODITIES   (GC=F, SI=F, CL=F)

    yfinance returns historical data 24/7 regardless of market hours, so the
    market-hours guard only applies when called from the Celery beat task.
    Pass ignore_market_hours=True (e.g. from the seed endpoint) to always fetch.

    Returns
    -------
    dict with keys: total_symbols, total_candles_saved, market_open, errors.
    """
    market_open = is_nse_market_open()

    if not ignore_market_hours and not market_open:
        logger.info("NSE closed -- skipping India crawl (pass ignore_market_hours=True to override)")
        return {
            "total_symbols":      0,
            "total_candles_saved": 0,
            "market_open":        False,
            "errors":             [],
        }

    # Build symbol list dynamically from market_shortlist (full-market scanner output).
    # Fallback: top 50 NSE EQ symbols from kite_instruments (bootstrap / cold start).
    # Always include the mandatory indices and VIX symbols regardless of shortlist.
    from sqlalchemy import select as _sel, text as _text
    from db.models import MarketShortlist, KiteInstrument

    # 1. Mandatory: indices + VIX + BSE watchlist (always crawled regardless of shortlist)
    mandatory: list[str] = list(settings.WATCHLIST_NIFTY_INDICES) + settings.bse_symbols + settings.bse_mid_symbols

    # 2. Dynamic equity universe from market_shortlist
    sl_result = await session.execute(
        _sel(MarketShortlist.symbol).order_by(MarketShortlist.rank).limit(100)
    )
    shortlist_syms = [r.symbol for r in sl_result.all()]

    if shortlist_syms:
        equity_syms = shortlist_syms
        source = f"market_shortlist ({len(equity_syms)} symbols)"
    else:
        # Cold start: top 50 NSE EQ + top 30 BSE EQ from kite_instruments.
        # GOI/SDL/digit-prefix-bond exclusion (2026-08-04): without it, digit-
        # prefixed bond tickers (e.g. "675KA33-SG") sort before real stocks in
        # ORDER BY tradingsymbol, so an empty-shortlist cold start would
        # return mostly bonds instead of real top-NSE names. Same filter as
        # crawler/zerodha_market.py::hydrate_tokens_from_db() -- see that
        # function's docstring for why digit-prefix + "-XX" suffix (not just
        # "-SG"/"-SK") is the correct, complete exclusion.
        ki_nse = await session.execute(
            _sel(KiteInstrument.tradingsymbol)
            .where(
                KiteInstrument.instrument_type == "EQ",
                KiteInstrument.segment == "NSE",
                KiteInstrument.name != "",
                KiteInstrument.name.notilike("GOI %"),
                KiteInstrument.name.notilike("SDL %"),
                ~KiteInstrument.tradingsymbol.op("~")(r"^[0-9].*-[A-Z0-9]{2}$"),
            )
            .order_by(KiteInstrument.tradingsymbol)
            .limit(50)
        )
        ki_bse = await session.execute(
            _sel(KiteInstrument.tradingsymbol)
            .where(
                KiteInstrument.instrument_type == "EQ",
                KiteInstrument.segment == "BSE",
                KiteInstrument.name != "",
                KiteInstrument.name.notilike("GOI %"),
                KiteInstrument.name.notilike("SDL %"),
                ~KiteInstrument.tradingsymbol.op("~")(r"^[0-9].*-[A-Z0-9]{2}$"),
            )
            .order_by(KiteInstrument.tradingsymbol)
            .limit(30)
        )
        equity_syms = (
            [f"{r.tradingsymbol}.NS" for r in ki_nse.all()] +
            [f"{r.tradingsymbol}.BO" for r in ki_bse.all()]
        )
        source = f"kite_instruments bootstrap ({len(equity_syms)} symbols, NSE+BSE)"

    # 3. User watchlist additions
    from db.models import UserWatchlist
    wl_result = await session.execute(
        _sel(UserWatchlist.symbol).where(UserWatchlist.is_active == True)
    )
    user_syms = [s for s in wl_result.scalars().all() if s not in equity_syms]

    all_symbols: list[str] = mandatory + equity_syms + user_syms

    logger.info(
        f"━━ India price crawl START ━━  {len(all_symbols)} symbols  "
        f"market_open={market_open}  ignore_market_hours={ignore_market_hours}  "
        f"source={source}  user_extra={len(user_syms)}"
    )

    # Symbols whose 5m/1h bars are now DERIVED from the 1m feed
    # (crawler/candle_resampler.py, 2026-08-24) do not need a yfinance round
    # trip here at all. This crawl fetches 5m and 1h per symbol, one HTTP call
    # each with a 20 s timeout, strictly sequentially; measured runtimes were
    # avg 657 s and max 1,793 s against a 300 s beat, so it could never finish
    # a pass and the last 40-75 minutes of every session went unwritten. It
    # also held one of only two default-queue slots for that whole time, the
    # same contention that starved fast_sl_check and F1.
    #
    # Resampling covers every symbol Kite streams 1m for (~2,350 on 24 Aug),
    # which includes the entire market_shortlist. What is left for yfinance is
    # the genuine remainder: indices, BSE-only names, and user watchlist
    # additions outside the Kite universe. Skipping the rest is not a
    # degradation — the resampled bars are strictly fresher, since they are
    # built from 1m data that is at most one beat old.
    covered_1m: set[str] = set()
    try:
        _cov = await session.execute(text(
            "SELECT DISTINCT symbol FROM candles "
            "WHERE timeframe = '1m' AND timestamp >= now() - INTERVAL '90 minutes'"
        ))
        covered_1m = {r[0] for r in _cov.all()}
    except Exception as exc:                       # pragma: no cover - defensive
        # Fail OPEN: an unreadable coverage set means we fetch everything, the
        # old behaviour. Never let this optimisation cause a coverage hole.
        logger.warning(f"[india_crawl] 1m-coverage lookup failed, fetching all: {exc}")

    all_candles:    list[dict] = []
    total_symbols:  int        = 0
    errors:         list[str]  = []
    skipped_resampled: int     = 0

    # Step 1 — fetch candles for every symbol sequentially (avoids yfinance flood).
    # Per-symbol 20s timeout: yfinance has no native timeout and can hang for
    # minutes when Yahoo's gateway is degraded. Without this guard a single
    # bad symbol burned the whole task budget (Celery hard-limit 600s).
    #
    # We fetch two timeframes per symbol:
    #   5m  (period=5d)  — for the agent's real-time decision-making
    #   1h  (period=60d) — for Hub scoring and longer-window indicators
    # Step 1 — fetch candles for every symbol concurrently using an asyncio Semaphore
    # (max 15 concurrent threads) to avoid Celery SoftTimeLimit exceptions.
    counted: set[str] = set()
    sem = asyncio.Semaphore(15)
    # Persist in batches rather than one all-or-nothing save at the end.
    _FLUSH_EVERY = 5_000          # rows, ~ a couple of hundred symbols
    saved = {"n": 0}

    async def _fetch_symbol(symbol: str):
        nonlocal skipped_resampled
        # Covered by the 1m -> 5m/15m/1h resampler; a yfinance fetch here would
        # be slower, staler, and from a second feed. See the covered_1m comment.
        if symbol in covered_1m:
            skipped_resampled += 1
            counted.add(symbol)
            return
        symbol_ok = False
        async with sem:
            # 5-minute candles
            try:
                candles_5m = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda s=symbol: fetch_nse_candles(s, interval="5m", period="1d"),
                    ),
                    timeout=20.0,
                )
                if candles_5m:
                    all_candles.extend(candles_5m)
                    symbol_ok = True
            except asyncio.TimeoutError:
                errors.append(f"{symbol}/5m: timeout")
            except Exception as exc:
                pass

            # 1-hour candles
            try:
                candles_1h = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda s=symbol: fetch_nse_candles(s, interval="1h", period="5d"),
                    ),
                    timeout=20.0,
                )
                if candles_1h:
                    all_candles.extend(candles_1h)
                    symbol_ok = True
                else:
                    errors.append(f"{symbol}/1h: empty response")
            except asyncio.TimeoutError:
                errors.append(f"{symbol}/1h: timeout")
            except Exception as exc:
                errors.append(f"{symbol}/1h: {exc}")

            if symbol_ok:
                counted.add(symbol)

        # Flush incrementally (2026-08-20). This used to accumulate every
        # candle for all ~1,400 symbols and save ONCE after the gather, so a
        # SoftTimeLimitExceeded anywhere in the fetch discarded the entire
        # batch. That is exactly what had been happening: the crawl started but
        # never once reached the save, and the 5m and 1h feeds were silently
        # dead for over a day (0 rows written today vs 290k for 1m, which a
        # different task writes) while candle_staleness_watchdog alarmed.
        #
        # Flushing in batches means a timeout costs at most the current batch,
        # not the whole run.
        if len(all_candles) >= _FLUSH_EVERY:
            batch, all_candles[:] = list(all_candles), []
            try:
                saved["n"] += await save_candles_to_db(batch, session)
            except Exception as exc:
                logger.warning(f"[india_price] incremental flush failed: {exc}")

    # Launch all symbols concurrently. Whatever has been fetched must be saved
    # even if this raises (soft time limit, cancellation) — see the finally.
    try:
        await asyncio.gather(*[_fetch_symbol(s) for s in all_symbols])
    finally:
        if all_candles:
            try:
                saved["n"] += await save_candles_to_db(list(all_candles), session)
                all_candles.clear()
            except Exception as exc:
                logger.warning(f"[india_price] final flush failed: {exc}")
    total_symbols = len(counted)

    # Step 1b — refresh the regime's daily candles via Kite (fresh, not
    # yfinance-throttled) so buy/sell gating never runs on stale index data.
    await sync_regime_daily_candles_kite(session)

    # Step 2 — fetch index snapshots (Kite-first, yfinance fallback)
    indices = await fetch_indices_kite_first()

    # Step 3 — fetch India VIX (Kite-first, yfinance fallback)
    vix = await fetch_vix_kite_first()

    # Step 4 — persist any remainder (most rows were already flushed above)
    if all_candles:
        saved["n"] += await save_candles_to_db(all_candles, session)
    total_candles_saved = saved["n"]

    result = {
        "total_symbols":       total_symbols,
        "total_candles_saved": total_candles_saved,
        "market_open":         market_open,
        "errors":              errors,
        "skipped_resampled":   skipped_resampled,
    }
    logger.info(
        f"━━ India price crawl DONE  ━━  "
        f"symbols={total_symbols}/{len(all_symbols)}  "
        f"candles_saved={total_candles_saved}  "
        f"skipped_resampled={skipped_resampled}  "
        f"vix={vix:.2f}  "
        f"errors={len(errors)}"
    )
    return result
