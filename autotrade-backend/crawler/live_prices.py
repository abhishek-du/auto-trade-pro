"""Live price cache for NSE market overview.

Fetches prices for NSE stocks, indices, commodities and forex via yfinance.
PRICE_CACHE is updated every 15 s during market hours, every 60 s outside.
INFO_CACHE holds slow-changing fundamentals (PE, market cap…) refreshed every 24 h.
"""

from __future__ import annotations

import asyncio
import datetime
import math
import time
from zoneinfo import ZoneInfo

import yfinance as yf

from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")

# ── In-memory caches ──────────────────────────────────────────────────────────

PRICE_CACHE: dict[str, dict] = {}
INFO_CACHE:  dict[str, dict] = {}
INFO_CACHE_TTL = 86_400  # 24 hours


# ── Unified price resolver (Zerodha first → yfinance fallback) ───────────────

def get_price(symbol: str) -> dict | None:
    """Single source of truth for a symbol's current price.

    Priority chain:
      1. Zerodha LIVE_TICKS (sub-second latency via KiteTicker)
      2. PRICE_CACHE (15-second refresh — yfinance backed)
      3. None (caller decides whether to do a synchronous yfinance call)

    Always returns a dict with at least {price, source, age_seconds} or None.
    The `source` field tells the UI whether data is real-time broker feed
    or polled, so it can render a latency label.
    """
    from utils.config import settings as _s
    import time as _time

    # 1. Zerodha LIVE_TICKS — sub-second from KiteTicker WebSocket
    if getattr(_s, "ZERODHA_ENABLED", False) and getattr(_s, "ZERODHA_ACCESS_TOKEN", ""):
        try:
            from crawler.zerodha_ticker import get_live_tick
            tick = get_live_tick(symbol)
            if tick and tick.get("last_price"):
                return {
                    "price":      float(tick["last_price"]),
                    "change":     float(tick.get("change", 0) or 0),
                    "change_pct": float(tick.get("change_percent", 0) or 0),
                    "volume":     float(tick.get("volume_traded", 0) or 0),
                    "source":     "zerodha_ticker",
                    "age_seconds": 0.0,
                }
        except Exception:
            pass  # Fall through to PRICE_CACHE

    # 2. PRICE_CACHE (15-sec refresh)
    cached = PRICE_CACHE.get(symbol)
    if cached and cached.get("price"):
        age = _time.time() - cached.get("_ts", _time.time())
        return {
            **{k: v for k, v in cached.items() if not k.startswith("_")},
            "source":     "yfinance_cache",
            "age_seconds": round(age, 2),
        }

    return None


def get_prices_batch(symbols: list[str]) -> dict[str, dict]:
    """Batch wrapper around get_price()."""
    return {s: p for s in symbols if (p := get_price(s)) is not None}

# ── Symbol catalogue ──────────────────────────────────────────────────────────

SYMBOLS_CONFIG: list[dict] = [
    {"symbol": "^NSEI",        "name": "NIFTY 50",     "type": "index"},
    {"symbol": "^NSEBANK",     "name": "BANK NIFTY",   "type": "index"},
    {"symbol": "^BSESN",       "name": "SENSEX",       "type": "index"},
    {"symbol": "^CNXIT",       "name": "NIFTY IT",     "type": "index"},
    {"symbol": "^CNXAUTO",     "name": "NIFTY AUTO",   "type": "index"},
    {"symbol": "^CNXPHARMA",   "name": "NIFTY PHARMA", "type": "index"},
    {"symbol": "^CNXFMCG",     "name": "NIFTY FMCG",  "type": "index"},
    {"symbol": "^CNXMETAL",    "name": "NIFTY METAL",  "type": "index"},
    {"symbol": "^CNXENERGY",   "name": "NIFTY ENERGY", "type": "index"},
    {"symbol": "^CNXINFRA",    "name": "NIFTY INFRA",  "type": "index"},
    {"symbol": "^CNXREALTY",   "name": "NIFTY REALTY", "type": "index"},
    {"symbol": "^INDIAVIX",    "name": "India VIX",    "type": "index"},
    {"symbol": "RELIANCE.NS",  "name": "Reliance",     "type": "stock"},
    {"symbol": "TCS.NS",       "name": "TCS",          "type": "stock"},
    {"symbol": "HDFCBANK.NS",  "name": "HDFC Bank",    "type": "stock"},
    {"symbol": "INFY.NS",      "name": "Infosys",      "type": "stock"},
    {"symbol": "ICICIBANK.NS", "name": "ICICI Bank",   "type": "stock"},
    {"symbol": "SBIN.NS",      "name": "SBI",          "type": "stock"},
    {"symbol": "BHARTIARTL.NS","name": "Airtel",       "type": "stock"},
    {"symbol": "KOTAKBANK.NS", "name": "Kotak Bank",   "type": "stock"},
    {"symbol": "LT.NS",        "name": "L&T",          "type": "stock"},
    {"symbol": "WIPRO.NS",     "name": "Wipro",        "type": "stock"},
    {"symbol": "HCLTECH.NS",   "name": "HCL Tech",     "type": "stock"},
    {"symbol": "AXISBANK.NS",  "name": "Axis Bank",    "type": "stock"},
    {"symbol": "MARUTI.NS",    "name": "Maruti",       "type": "stock"},
    {"symbol": "SUNPHARMA.NS", "name": "Sun Pharma",   "type": "stock"},
    {"symbol": "ITC.NS",       "name": "ITC",          "type": "stock"},
    {"symbol": "BAJFINANCE.NS","name": "Bajaj Fin",    "type": "stock"},
    {"symbol": "GC=F",         "name": "Gold",         "type": "commodity"},
    {"symbol": "CL=F",         "name": "Crude Oil",    "type": "commodity"},
    {"symbol": "USDINR=X",     "name": "USD/INR",      "type": "forex"},
]

_SYMBOL_META: dict[str, dict] = {cfg["symbol"]: cfg for cfg in SYMBOLS_CONFIG}

# ── Sector map (authoritative override for watchlist display) ─────────────────

SECTOR_MAP: dict[str, str] = {
    "RELIANCE.NS":   "Energy",
    "TCS.NS":        "IT",
    "INFY.NS":       "IT",
    "HCLTECH.NS":    "IT",
    "WIPRO.NS":      "IT",
    "HDFCBANK.NS":   "Banking",
    "ICICIBANK.NS":  "Banking",
    "SBIN.NS":       "Banking",
    "KOTAKBANK.NS":  "Banking",
    "AXISBANK.NS":   "Banking",
    "BAJFINANCE.NS": "Finance",
    "HINDUNILVR.NS": "FMCG",
    "ITC.NS":        "FMCG",
    "NESTLEIND.NS":  "FMCG",
    "SUNPHARMA.NS":  "Pharma",
    "DRREDDY.NS":    "Pharma",
    "MARUTI.NS":     "Auto",
    "BHARTIARTL.NS": "Telecom",
    "LT.NS":         "Infra",
    "ULTRACEMCO.NS": "Cement",
    "ASIANPAINT.NS": "Consumer",
    "PIDILITIND.NS": "Consumer",
    "POWERGRID.NS":  "Energy",
    "NTPC.NS":       "Energy",
    "COALINDIA.NS":  "Energy",
    "MUTHOOTFIN.NS": "Finance",
    "PERSISTENT.NS": "IT",
    "COFORGE.NS":    "IT",
    "LTTS.NS":       "IT",
    "TATAELXSI.NS":  "IT",
    "METROPOLIS.NS": "Pharma",
    "LALPATHLAB.NS": "Pharma",
    "ASTRAL.NS":     "Consumer",
    "VOLTAS.NS":     "Consumer",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if not math.isnan(f) and not math.isinf(f) else None
    except (TypeError, ValueError):
        return None


def _safe_cr(v) -> float | None:
    """Convert raw market cap (INR) to Crores (÷ 1e7)."""
    f = _safe_float(v)
    return round(f / 1e7, 2) if f else None


def _safe_pct(v) -> float | None:
    """Convert fractional yield (e.g. 0.02) to percentage (2.0)."""
    f = _safe_float(v)
    return round(f * 100, 2) if f else None


def _get_market_status() -> str:
    now = datetime.datetime.now(_IST)
    if now.weekday() >= 5:
        return "CLOSED"
    h, m = now.hour, now.minute
    if (h, m) >= (9, 0) and (h, m) < (9, 15):
        return "PRE_OPEN"
    if (h, m) >= (9, 15) and (h, m) < (15, 30):
        return "OPEN"
    return "CLOSED"


# ── Price fetch (fast, 15-second cycle) ───────────────────────────────────────

async def fetch_prices_batch(symbols: list[str]) -> dict[str, dict]:
    """Fetch latest prices for a list of symbols using yfinance fast_info.
    Merges INFO_CACHE fundamentals and computes derived ratio fields.
    """
    loop = asyncio.get_event_loop()

    def _fetch_sync() -> dict[str, dict]:
        results: dict[str, dict] = {}
        try:
            tickers = yf.Tickers(" ".join(symbols))
        except Exception as exc:
            logger.warning(f"[live_prices] yf.Tickers init failed: {exc}")
            return results

        for sym in symbols:
            try:
                t = tickers.tickers.get(sym)
                if t is None:
                    continue
                fi = t.fast_info

                last_price  = float(getattr(fi, "last_price",              0) or 0)
                prev_close  = float(getattr(fi, "previous_close",          0) or 0)
                open_price  = float(getattr(fi, "open",                    0) or 0)
                day_high    = float(getattr(fi, "day_high",                0) or 0)
                day_low     = float(getattr(fi, "day_low",                 0) or 0)
                w52_high    = float(getattr(fi, "fifty_two_week_high",     0) or 0)
                w52_low     = float(getattr(fi, "fifty_two_week_low",      0) or 0)
                volume      = int(getattr(fi,   "last_volume",             0) or 0)
                avg_vol_3m  = int(getattr(fi,   "three_month_average_volume", 0) or 0)

                if last_price == 0:
                    continue

                change     = last_price - prev_close if prev_close else 0.0
                change_pct = (change / prev_close * 100) if prev_close else 0.0

                # ── Derived ratio fields ──────────────────────────────────────
                info_data  = INFO_CACHE.get(sym, {})
                avg_vol_10 = info_data.get("avg_volume_10d") or (avg_vol_3m or None)
                vol_ratio  = (
                    round(volume / avg_vol_10, 2)
                    if avg_vol_10 and avg_vol_10 > 0 and volume > 0
                    else None
                )
                day_range_pct = (
                    round((day_high - day_low) / prev_close * 100, 2)
                    if prev_close > 0 and day_high > 0 and day_low > 0
                    else None
                )
                from_52w_high = (
                    round((w52_high - last_price) / w52_high * 100, 2)
                    if w52_high > 0
                    else None
                )
                from_52w_low = (
                    round((last_price - w52_low) / w52_low * 100, 2)
                    if w52_low > 0
                    else None
                )

                # Preserve existing signal data across price refreshes
                prev = PRICE_CACHE.get(sym, {})

                meta = _SYMBOL_META.get(sym, {})
                results[sym] = {
                    "symbol":         sym,
                    "name":           meta.get("name", sym),
                    "type":           meta.get("type", "stock"),
                    "price":          round(last_price, 2),
                    "change":         round(change, 2),
                    "change_pct":     round(change_pct, 2),
                    "open":           round(open_price, 2),
                    "high":           round(day_high, 2),
                    "low":            round(day_low, 2),
                    "prev_close":     round(prev_close, 2),
                    "volume":         volume,
                    "52w_high":       round(w52_high, 2),
                    "52w_low":        round(w52_low, 2),
                    # ── enriched fields ───────────────────────────────────────
                    "avg_volume_10d":  avg_vol_10,
                    "volume_ratio":    vol_ratio,
                    "day_range_pct":   day_range_pct,
                    "from_52w_high":   from_52w_high,
                    "from_52w_low":    from_52w_low,
                    "price_vs_ema20":  None,  # requires historical data
                    "market_cap":      info_data.get("market_cap"),
                    "pe_ratio":        info_data.get("pe_ratio"),
                    "pb_ratio":        info_data.get("pb_ratio"),
                    "dividend_yield":  info_data.get("dividend_yield"),
                    "beta":            info_data.get("beta"),
                    "sector":          info_data.get("sector") or SECTOR_MAP.get(sym),
                    # ── signal fields (preserved across refreshes) ─────────────
                    "signal":             prev.get("signal"),
                    "signal_confidence":  prev.get("signal_confidence"),
                    # ── meta ──────────────────────────────────────────────────
                    "last_updated":   datetime.datetime.now(_IST).isoformat(),
                    "market_status":  _get_market_status(),
                }
            except Exception as exc:
                logger.warning(f"[live_prices] Failed to fetch {sym}: {exc}")

        return results

    return await loop.run_in_executor(None, _fetch_sync)


async def refresh_all_prices() -> dict[str, dict]:
    """Refresh PRICE_CACHE for all configured symbols. Returns updated cache."""
    t0      = time.monotonic()
    symbols = [cfg["symbol"] for cfg in SYMBOLS_CONFIG]
    updated = await fetch_prices_batch(symbols)
    PRICE_CACHE.update(updated)
    elapsed = int((time.monotonic() - t0) * 1000)
    logger.info(f"[live_prices] Cache refreshed — {len(updated)} symbols — {elapsed}ms")
    return PRICE_CACHE


# ── Info cache (slow, 24-hour cycle) ──────────────────────────────────────────

async def refresh_info_cache(symbols: list[str]) -> None:
    """Fetch fundamental data (.info) from yfinance for stale or missing symbols.
    Results are cached for 24 hours so this is called once per day per symbol.
    """
    now = time.time()
    to_refresh = [
        sym for sym in symbols
        if sym not in INFO_CACHE
        or (now - INFO_CACHE[sym].get("_fetched_at", 0)) > INFO_CACHE_TTL
    ]

    if not to_refresh:
        logger.debug("[live_prices] INFO_CACHE all fresh — skipping")
        return

    loop = asyncio.get_event_loop()
    semaphore = asyncio.Semaphore(5)  # max 5 concurrent yf.info calls

    async def _fetch_one(sym: str) -> None:
        async with semaphore:
            def _sync() -> dict:
                try:
                    info = yf.Ticker(sym).info
                    return {
                        "market_cap":     _safe_cr(info.get("marketCap")),
                        "pe_ratio":       _safe_float(info.get("trailingPE")),
                        "pb_ratio":       _safe_float(info.get("priceToBook")),
                        "dividend_yield": _safe_pct(info.get("dividendYield")),
                        "beta":           _safe_float(info.get("beta")),
                        "avg_volume_10d": _safe_float(info.get("averageVolume10days")),
                        "sector":         SECTOR_MAP.get(sym) or info.get("sector"),
                        "_fetched_at":    time.time(),
                    }
                except Exception as exc:
                    logger.debug(f"[live_prices] .info fetch failed for {sym}: {exc}")
                    return {"sector": SECTOR_MAP.get(sym), "_fetched_at": time.time()}

            INFO_CACHE[sym] = await loop.run_in_executor(None, _sync)

    await asyncio.gather(*[_fetch_one(sym) for sym in to_refresh])
    logger.info(f"[live_prices] INFO_CACHE refreshed for {len(to_refresh)} symbols")


# ── Signal enrichment ─────────────────────────────────────────────────────────

async def enrich_cache_with_signals(session) -> None:
    """Inject the latest signal type + confidence into PRICE_CACHE for each stock."""
    from sqlalchemy import desc, select
    from db.models import Signal

    stock_syms = [sym for sym, cfg in _SYMBOL_META.items() if cfg.get("type") == "stock"]

    for sym in stock_syms:
        if sym not in PRICE_CACHE:
            continue
        try:
            result = await session.execute(
                select(Signal)
                .where(Signal.symbol == sym)
                .order_by(desc(Signal.created_at))
                .limit(1)
            )
            sig = result.scalar_one_or_none()
            if sig:
                PRICE_CACHE[sym]["signal"] = (
                    sig.signal_type.value
                    if hasattr(sig.signal_type, "value")
                    else str(sig.signal_type)
                )
                PRICE_CACHE[sym]["signal_confidence"] = sig.confidence
            else:
                PRICE_CACHE[sym].setdefault("signal", None)
                PRICE_CACHE[sym].setdefault("signal_confidence", None)
        except Exception as exc:
            logger.debug(f"[live_prices] signal enrich failed for {sym}: {exc}")


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_cached_price(symbol: str) -> dict | None:
    return PRICE_CACHE.get(symbol)


def get_all_cached_prices() -> dict:
    return dict(PRICE_CACHE)


def get_market_summary() -> dict:
    now_ist   = datetime.datetime.now(_IST)
    stocks    = [v for v in PRICE_CACHE.values() if v.get("type") == "stock"]
    advances  = sum(1 for s in stocks if s.get("change", 0) > 0)
    declines  = sum(1 for s in stocks if s.get("change", 0) < 0)
    unchanged = sum(1 for s in stocks if s.get("change", 0) == 0)
    last_ts   = max(
        (v.get("last_updated", "") for v in PRICE_CACHE.values()),
        default=None,
    )
    # market_open + data_mode let the UI render a 🟢 LIVE vs ● CLOSED badge and
    # decide how fast to poll. LIVE = streaming during market hours; CLOSED =
    # showing the last close (no ticks happen when the market is shut).
    try:
        from crawler.india_price_feed import is_nse_market_open
        market_open = is_nse_market_open()
    except Exception:
        market_open = False

    return {
        # get_price() resolves live ticks → cache → yfinance fallback, so an index
        # never shows None just because it isn't in PRICE_CACHE yet.
        "nifty50":        get_price("^NSEI"),
        "bank_nifty":     get_price("^NSEBANK"),
        "sensex":         get_price("^BSESN"),
        "india_vix":      get_price("^INDIAVIX"),
        "market_status":  _get_market_status(),
        "market_open":    market_open,
        "data_mode":      "LIVE" if market_open else "CLOSED",
        "ist_time":       now_ist.strftime("%H:%M:%S"),
        "ist_date":       now_ist.strftime("%A, %d %B %Y"),
        "advances":       advances,
        "declines":       declines,
        "unchanged":      unchanged,
        "last_refreshed": last_ts,
    }
