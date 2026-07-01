"""NSE/BSE Market Breadth crawler for AutoTrade Pro.

Computes advances/declines, 52-week movers, top gainers/losers from two sources:
  1. NSE official API (requires browser session; best for market-wide data)
  2. PRICE_CACHE (always available; covers our 35-stock watchlist)

BREADTH_CACHE is updated every 2 minutes by the Celery beat task.
BREADTH_HISTORY stores the last 50 readings for the intraday timeline chart.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from crawler.fii_dii_crawler import BROWSER_HEADERS
from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")

BREADTH_CACHE: dict[str, Any] = {}
BREADTH_HISTORY: list[dict] = []
MAX_HISTORY = 50

_NSE_HOME = "https://www.nseindia.com"
# /api/liveanalysis?index=advances was retired by NSE; allIndices has market-wide
# advances/declines at the top level plus per-index breakdown in the data array.
_ADV_DEC_URL = "https://www.nseindia.com/api/allIndices"
_GAINERS_URL = "https://www.nseindia.com/api/live-analysis-variations?index=gainers"
_LOSERS_URL  = "https://www.nseindia.com/api/live-analysis-variations?index=loosers"
_ACTIVE_URL  = "https://www.nseindia.com/api/live-analysis-variations?index=active"
_52H_URL     = "https://www.nseindia.com/api/live-analysis-variations?index=new52weekhigh"
_52L_URL     = "https://www.nseindia.com/api/live-analysis-variations?index=new52weeklow"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_mood(ad_ratio: float) -> str:
    if ad_ratio >= 2.5: return "STRONGLY_BULLISH"
    if ad_ratio >= 1.2: return "BULLISH"
    if ad_ratio >= 0.8: return "NEUTRAL"
    if ad_ratio >= 0.5: return "BEARISH"
    return "STRONGLY_BEARISH"


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _parse_variation_list(payload: Any) -> list[dict]:
    """Normalise NSE live-analysis-variations response to a flat list."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "gainers", "loosers", "active", "HIGH", "LOW"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        # Try any list-valued key
        for val in payload.values():
            if isinstance(val, list) and val:
                return val
    return []


def _map_variation_row(row: dict) -> dict:
    """Map a raw NSE live-analysis row to our canonical stock dict."""
    sym = row.get("symbol") or row.get("Symbol") or ""
    return {
        "symbol":     sym,
        "name":       row.get("meta", {}).get("companyName", sym) if isinstance(row.get("meta"), dict) else sym,
        "ltp":        _safe_float(row.get("ltp") or row.get("LTP")),
        "open":       _safe_float(row.get("openPrice") or row.get("open")),
        "high":       _safe_float(row.get("highPrice") or row.get("high")),
        "low":        _safe_float(row.get("lowPrice")  or row.get("low")),
        "prev_close": _safe_float(row.get("previousPrice") or row.get("prevClose")),
        "change":     _safe_float(row.get("netPrice")   or row.get("change")),
        "change_pct": _safe_float(row.get("perChange")  or row.get("pChange")),
        "volume":     int(_safe_float(row.get("tradedQuantity") or row.get("totalTradedVolume") or 0)),
    }


# ── NSE API fetchers ──────────────────────────────────────────────────────────

async def fetch_nse_advances_declines() -> dict:
    """Fetch advances/declines from NSE API using curl_cffi (Chrome TLS fingerprint).

    NSE's Akamai bot-detection blocks plain httpx requests. curl_cffi with
    Chrome impersonation bypasses TLS fingerprinting and Brotli encoding.
    Falls back to plain httpx (gzip-only) when curl_cffi is unavailable.
    """
    _curl_headers = {
        **BROWSER_HEADERS,
        "Accept-Encoding": "gzip, deflate, br",   # curl_cffi decodes br natively
    }

    async def _parse_response(payload) -> dict:
        # allIndices API: market-wide totals at top level, per-index in data[]
        if isinstance(payload, dict) and "advances" in payload and "declines" in payload:
            total_adv = int(_safe_float(payload.get("advances") or 0))
            total_dec = int(_safe_float(payload.get("declines") or 0))
            total_unc = int(_safe_float(payload.get("unchanged") or 0))
            # Per-index breakdown from data array
            by_index: dict[str, dict] = {}
            for row in (payload.get("data") or []):
                idx_name = row.get("indexSymbol") or row.get("index") or ""
                if idx_name:
                    by_index[idx_name] = {
                        "advances":  int(_safe_float(row.get("advances") or 0)),
                        "declines":  int(_safe_float(row.get("declines") or 0)),
                        "unchanged": int(_safe_float(row.get("unchanged") or 0)),
                    }
        else:
            # Legacy list-of-rows format (old liveanalysis endpoint)
            rows = payload if isinstance(payload, list) else []
            total_adv = total_dec = total_unc = 0
            by_index = {}
            for row in rows:
                idx_name = row.get("indexSymbol") or row.get("index") or row.get("name") or ""
                adv = int(_safe_float(row.get("advances")  or row.get("advance")  or 0))
                dec = int(_safe_float(row.get("declines")  or row.get("decline")  or 0))
                unc = int(_safe_float(row.get("unchanged") or row.get("noChange") or 0))
                if idx_name:
                    by_index[idx_name] = {"advances": adv, "declines": dec, "unchanged": unc}
                if "TOTAL" in idx_name.upper() or "ALL" in idx_name.upper():
                    total_adv, total_dec, total_unc = adv, dec, unc
            if total_adv == 0 and by_index:
                for v in by_index.values():
                    total_adv += v["advances"]
                    total_dec += v["declines"]
                    total_unc += v["unchanged"]

        if total_adv == 0 and total_dec == 0:
            return {}

        total    = total_adv + total_dec + total_unc
        ad_ratio = total_adv / max(total_dec, 1)
        return {
            "advances":     total_adv,
            "declines":     total_dec,
            "unchanged":    total_unc,
            "total":        total,
            "ad_ratio":     round(ad_ratio, 2),
            "advance_pct":  round(total_adv / max(total, 1) * 100, 1),
            "market_mood":  _get_mood(ad_ratio),
            "by_index":     by_index,
        }

    # Primary: curl_cffi with Chrome TLS fingerprint
    try:
        from curl_cffi.requests import AsyncSession as _CurlSession  # noqa: PLC0415
        async with _CurlSession(impersonate="chrome124") as curl:
            await curl.get(_NSE_HOME, headers=_curl_headers, timeout=15)
            await asyncio.sleep(1)
            r = await curl.get(_ADV_DEC_URL, headers=_curl_headers, timeout=15)
        if r.status_code == 200:
            result = await _parse_response(r.json())
            if result:
                logger.debug(f"[breadth] NSE curl_cffi: {result['advances']} adv/{result['declines']} dec")
                return result
    except Exception as exc:
        logger.warning(f"[breadth] curl_cffi fetch failed: {exc}")

    # Fallback: plain httpx (no Brotli — gzip only)
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            await client.get(_NSE_HOME, headers=BROWSER_HEADERS)
            await asyncio.sleep(1)
            r = await client.get(_ADV_DEC_URL, headers=BROWSER_HEADERS)
        if r.status_code == 200:
            result = await _parse_response(r.json())
            if result:
                return result
        logger.warning(f"[breadth] NSE advances API returned {r.status_code}")
    except Exception as exc:
        logger.warning(f"[breadth] NSE advances/declines fetch failed: {exc}")

    return {}


async def fetch_nse_gainers_losers() -> dict:
    """Fetch top gainers, losers and most-active from NSE API."""
    result: dict[str, list] = {"gainers": [], "losers": [], "most_active": []}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            await client.get(_NSE_HOME, headers=BROWSER_HEADERS)
            await asyncio.sleep(1)
            for key, url in [("gainers", _GAINERS_URL), ("losers", _LOSERS_URL), ("most_active", _ACTIVE_URL)]:
                try:
                    r = await client.get(url, headers=BROWSER_HEADERS)
                    if r.status_code == 200:
                        rows = _parse_variation_list(r.json())
                        result[key] = [_map_variation_row(row) for row in rows[:10]]
                except Exception as exc:
                    logger.warning(f"[breadth] NSE {key} fetch failed: {exc}")
    except Exception as exc:
        logger.warning(f"[breadth] NSE gainers/losers session failed: {exc}")
    return result


async def fetch_nse_52week_movers() -> dict:
    """Fetch stocks hitting new 52-week highs or lows today from NSE API."""
    result: dict[str, list] = {"week52_high": [], "week52_low": []}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            await client.get(_NSE_HOME, headers=BROWSER_HEADERS)
            await asyncio.sleep(1)
            for key, url in [("week52_high", _52H_URL), ("week52_low", _52L_URL)]:
                try:
                    r = await client.get(url, headers=BROWSER_HEADERS)
                    if r.status_code == 200:
                        rows = _parse_variation_list(r.json())
                        result[key] = [_map_variation_row(row) for row in rows[:20]]
                except Exception as exc:
                    logger.warning(f"[breadth] NSE 52W {key} fetch failed: {exc}")
    except Exception as exc:
        logger.warning(f"[breadth] NSE 52W session failed: {exc}")
    return result


# ── PRICE_CACHE computation (always available) ────────────────────────────────

def compute_breadth_from_cache() -> dict:
    """Synchronously compute breadth from PRICE_CACHE — always succeeds."""
    from crawler.live_prices import PRICE_CACHE

    # Kite WebSocket items have type=None; include any entry that has a real price.
    # Exclude index symbols (start with ^ or match known index names without .NS).
    _INDEX_PREFIXES = ("^", "NIFTY", "SENSEX", "INDIA VIX", "NIFTYBEES")
    stocks = [
        v for v in PRICE_CACHE.values()
        if v.get("price") is not None
        and not any(str(v.get("symbol", "")).upper().startswith(p) for p in _INDEX_PREFIXES)
    ]

    if not stocks:
        return {
            "advances": 0, "declines": 0, "unchanged": 0,
            "total": 0, "ad_ratio": 1.0, "advance_pct": 0.0,
            "market_mood": "NEUTRAL",
            "top_gainers": [], "top_losers": [], "most_active": [],
            "week52_high": [], "week52_low": [],
        }

    advances  = [s for s in stocks if (s.get("change_pct") or 0) > 0]
    declines  = [s for s in stocks if (s.get("change_pct") or 0) < 0]
    unchanged = [s for s in stocks if (s.get("change_pct") or 0) == 0]

    # Stocks within 2% of 52W extremes
    near_52w_high = [
        s for s in stocks
        if s.get("52w_high") and s.get("price", 0) >= s["52w_high"] * 0.98
    ]
    near_52w_low = [
        s for s in stocks
        if s.get("52w_low") and s.get("price", 0) <= s["52w_low"] * 1.02
    ]

    sorted_by_change = sorted(stocks, key=lambda x: x.get("change_pct") or 0)
    top_losers  = sorted_by_change[:10]
    top_gainers = sorted_by_change[-10:][::-1]

    most_active = sorted(
        stocks,
        key=lambda x: x.get("volume_ratio") or 0,
        reverse=True
    )[:10]

    total    = len(stocks)
    adv      = len(advances)
    dec      = len(declines)
    ad_ratio = adv / max(dec, 1)

    # Map PRICE_CACHE keys to canonical breadth stock keys
    def _map(s: dict) -> dict:
        return {
            "symbol":     s.get("symbol", ""),
            "name":       s.get("name", ""),
            "ltp":        s.get("price", 0),
            "open":       s.get("open", 0),
            "high":       s.get("high", 0),
            "low":        s.get("low", 0),
            "prev_close": s.get("prev_close", 0),
            "change":     s.get("change", 0),
            "change_pct": s.get("change_pct", 0),
            "volume":     s.get("volume", 0),
            "volume_ratio": s.get("volume_ratio"),
            "from_52w_high": s.get("from_52w_high"),
            "from_52w_low":  s.get("from_52w_low"),
        }

    return {
        "advances":    adv,
        "declines":    dec,
        "unchanged":   len(unchanged),
        "total":       total,
        "ad_ratio":    round(ad_ratio, 2),
        "advance_pct": round(adv / max(total, 1) * 100, 1),
        "market_mood": _get_mood(ad_ratio),
        "top_gainers": [_map(s) for s in top_gainers],
        "top_losers":  [_map(s) for s in top_losers],
        "most_active": [_map(s) for s in most_active],
        "week52_high": [_map(s) for s in near_52w_high],
        "week52_low":  [_map(s) for s in near_52w_low],
    }


# ── Main refresh orchestrator ─────────────────────────────────────────────────

async def refresh_breadth_data() -> dict:
    """Fetch and merge breadth data from NSE API + PRICE_CACHE."""
    # Step 1: instant PRICE_CACHE computation — always works
    watchlist = compute_breadth_from_cache()

    # Step 2: NSE API calls in parallel (may fail outside market hours)
    nse_ad, gl_data, w52_data = await asyncio.gather(
        fetch_nse_advances_declines(),
        fetch_nse_gainers_losers(),
        fetch_nse_52week_movers(),
        return_exceptions=True,
    )

    # Treat exceptions as empty dicts
    if isinstance(nse_ad, Exception):  nse_ad = {}
    if isinstance(gl_data, Exception): gl_data = {}
    if isinstance(w52_data, Exception): w52_data = {}

    has_nse = bool(nse_ad and nse_ad.get("advances", 0) > 0)
    source = "MIXED" if has_nse else "COMPUTED"

    now_ist = datetime.datetime.now(_IST)

    # Build merged breadth structure
    nse_section = nse_ad if has_nse else {
        "advances":    watchlist["advances"],
        "declines":    watchlist["declines"],
        "unchanged":   watchlist["unchanged"],
        "total":       watchlist["total"],
        "ad_ratio":    watchlist["ad_ratio"],
        "advance_pct": watchlist["advance_pct"],
        "market_mood": watchlist["market_mood"],
        "by_index":    {},
    }

    # Gainers/losers: prefer NSE API (broader universe), fall back to watchlist
    top_gainers = (gl_data.get("gainers") or []) if has_nse else watchlist["top_gainers"]
    top_losers  = (gl_data.get("losers")  or []) if has_nse else watchlist["top_losers"]
    most_active = (gl_data.get("most_active") or []) if has_nse else watchlist["most_active"]
    week52_high = (w52_data.get("week52_high") or []) if has_nse else watchlist["week52_high"]
    week52_low  = (w52_data.get("week52_low")  or []) if has_nse else watchlist["week52_low"]

    BREADTH_CACHE.update({
        "nse": nse_section,
        "bse": {},
        "watchlist": {
            "advances":    watchlist["advances"],
            "declines":    watchlist["declines"],
            "unchanged":   watchlist["unchanged"],
            "total":       watchlist["total"],
            "ad_ratio":    watchlist["ad_ratio"],
            "advance_pct": watchlist["advance_pct"],
            "market_mood": watchlist["market_mood"],
        },
        "top_gainers":  top_gainers,
        "top_losers":   top_losers,
        "most_active":  most_active,
        "week52_high":  week52_high,
        "week52_low":   week52_low,
        "upper_circuit": [],
        "lower_circuit": [],
        "last_updated": now_ist.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
        "source": source,
    })

    # Append to intraday history
    BREADTH_HISTORY.append({
        "timestamp":            now_ist.isoformat(),
        "advances":             nse_section.get("advances", 0),
        "declines":             nse_section.get("declines", 0),
        "ad_ratio":             nse_section.get("ad_ratio", 1.0),
        "watchlist_advances":   watchlist["advances"],
        "watchlist_declines":   watchlist["declines"],
    })
    if len(BREADTH_HISTORY) > MAX_HISTORY:
        BREADTH_HISTORY.pop(0)

    # Reset history at market open each day
    if now_ist.hour == 9 and now_ist.minute < 20:
        BREADTH_HISTORY.clear()

    logger.info(
        f"[breadth] NSE: {nse_section.get('advances')} adv / "
        f"{nse_section.get('declines')} dec | "
        f"Watchlist: {watchlist['advances']}/{watchlist['declines']} | "
        f"Source: {source}"
    )
    return dict(BREADTH_CACHE)


def get_breadth_cache() -> dict:
    """Return BREADTH_CACHE, computing from PRICE_CACHE if not yet populated."""
    if BREADTH_CACHE:
        return dict(BREADTH_CACHE)
    # First call before any Celery task ran — compute synchronously
    watchlist = compute_breadth_from_cache()
    now_ist = datetime.datetime.now(_IST)
    return {
        "nse": {
            "advances":    watchlist["advances"],
            "declines":    watchlist["declines"],
            "unchanged":   watchlist["unchanged"],
            "total":       watchlist["total"],
            "ad_ratio":    watchlist["ad_ratio"],
            "advance_pct": watchlist["advance_pct"],
            "market_mood": watchlist["market_mood"],
            "by_index":    {},
        },
        "bse": {},
        "watchlist": {
            "advances":    watchlist["advances"],
            "declines":    watchlist["declines"],
            "unchanged":   watchlist["unchanged"],
            "total":       watchlist["total"],
            "ad_ratio":    watchlist["ad_ratio"],
            "advance_pct": watchlist["advance_pct"],
            "market_mood": watchlist["market_mood"],
        },
        "top_gainers":  watchlist["top_gainers"],
        "top_losers":   watchlist["top_losers"],
        "most_active":  watchlist["most_active"],
        "week52_high":  watchlist["week52_high"],
        "week52_low":   watchlist["week52_low"],
        "upper_circuit": [],
        "lower_circuit": [],
        "last_updated": now_ist.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
        "source": "COMPUTED",
    }
