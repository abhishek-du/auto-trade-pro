"""Live price cache for NSE market overview.

Fetches prices for NSE stocks, indices, commodities and forex via yfinance.
PRICE_CACHE is updated every 15 s during market hours, every 60 s outside.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from zoneinfo import ZoneInfo

import yfinance as yf

from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")

PRICE_CACHE: dict[str, dict] = {}

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


async def fetch_prices_batch(symbols: list[str]) -> dict[str, dict]:
    """Fetch latest prices for a list of symbols using yfinance fast_info."""
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

                last_price = float(getattr(fi, "last_price",          0) or 0)
                prev_close = float(getattr(fi, "previous_close",      0) or 0)
                open_price = float(getattr(fi, "open",                0) or 0)
                day_high   = float(getattr(fi, "day_high",            0) or 0)
                day_low    = float(getattr(fi, "day_low",             0) or 0)
                w52_high   = float(getattr(fi, "fifty_two_week_high", 0) or 0)
                w52_low    = float(getattr(fi, "fifty_two_week_low",  0) or 0)
                volume     = int(getattr(fi, "three_month_average_volume", 0) or 0)

                if last_price == 0:
                    continue

                change     = last_price - prev_close if prev_close else 0.0
                change_pct = (change / prev_close * 100) if prev_close else 0.0

                meta = _SYMBOL_META.get(sym, {})
                results[sym] = {
                    "symbol":        sym,
                    "name":          meta.get("name", sym),
                    "type":          meta.get("type", "stock"),
                    "price":         round(last_price, 2),
                    "change":        round(change, 2),
                    "change_pct":    round(change_pct, 2),
                    "open":          round(open_price, 2),
                    "high":          round(day_high, 2),
                    "low":           round(day_low, 2),
                    "prev_close":    round(prev_close, 2),
                    "volume":        volume,
                    "52w_high":      round(w52_high, 2),
                    "52w_low":       round(w52_low, 2),
                    "last_updated":  datetime.datetime.now(_IST).isoformat(),
                    "market_status": _get_market_status(),
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
    return {
        "nifty50":        PRICE_CACHE.get("^NSEI"),
        "bank_nifty":     PRICE_CACHE.get("^NSEBANK"),
        "sensex":         PRICE_CACHE.get("^BSESN"),
        "india_vix":      PRICE_CACHE.get("^INDIAVIX"),
        "market_status":  _get_market_status(),
        "ist_time":       now_ist.strftime("%H:%M:%S"),
        "ist_date":       now_ist.strftime("%A, %d %B %Y"),
        "advances":       advances,
        "declines":       declines,
        "unchanged":      unchanged,
        "last_refreshed": last_ts,
    }
