"""NSE sector performance engine for AutoTrade Pro.

Computes per-sector advances/declines, avg change, breadth, and rotation
signals from PRICE_CACHE — always synchronous, always available.

SECTOR_CACHE is refreshed every 60 seconds by the Celery beat task.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")

SECTOR_CACHE: dict[str, Any] = {}

# ── Sector definitions ────────────────────────────────────────────────────────

SECTOR_DEFINITIONS: dict[str, dict] = {
    "IT": {
        "name": "Information Technology",
        "short": "IT",
        "index_symbol": "^CNXIT",
        "color_base": "blue",
        "stocks": [
            "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS",
            "PERSISTENT.NS", "COFORGE.NS", "LTTS.NS", "TATAELXSI.NS",
        ],
    },
    "Banking": {
        "name": "Banking & Finance",
        "short": "Banking",
        "index_symbol": "^NSEBANK",
        "color_base": "purple",
        "stocks": [
            "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS",
            "AXISBANK.NS", "BAJFINANCE.NS", "MUTHOOTFIN.NS",
        ],
    },
    "Pharma": {
        "name": "Pharmaceuticals",
        "short": "Pharma",
        "index_symbol": "^CNXPHARMA",
        "color_base": "green",
        "stocks": [
            "SUNPHARMA.NS", "DRREDDY.NS", "METROPOLIS.NS", "LALPATHLAB.NS",
        ],
    },
    "Auto": {
        "name": "Automobiles",
        "short": "Auto",
        "index_symbol": "^CNXAUTO",
        "color_base": "orange",
        "stocks": [
            "MARUTI.NS", "TATAMOTORS.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS",
        ],
    },
    "FMCG": {
        "name": "Fast Moving Consumer Goods",
        "short": "FMCG",
        "index_symbol": "^CNXFMCG",
        "color_base": "teal",
        "stocks": [
            "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS",
        ],
    },
    "Metals": {
        "name": "Metals & Mining",
        "short": "Metals",
        "index_symbol": "^CNXMETAL",
        "color_base": "amber",
        "stocks": [
            "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "COALINDIA.NS", "VEDL.NS",
        ],
    },
    "Energy": {
        "name": "Oil, Gas & Energy",
        "short": "Energy",
        "index_symbol": "^CNXENERGY",
        "color_base": "red",
        "stocks": [
            "RELIANCE.NS", "ONGC.NS", "POWERGRID.NS", "NTPC.NS", "BPCL.NS",
        ],
    },
    "Infra": {
        "name": "Infrastructure",
        "short": "Infra",
        "index_symbol": "^CNXINFRA",
        "color_base": "coral",
        "stocks": [
            "LT.NS", "ULTRACEMCO.NS", "ASIANPAINT.NS", "PIDILITIND.NS", "ASTRAL.NS",
        ],
    },
    "Consumer": {
        "name": "Consumer Durables",
        "short": "Consumer",
        "index_symbol": "^CNXCONSUMER",
        "color_base": "pink",
        "stocks": [
            "VOLTAS.NS", "CROMPTON.NS", "HAVELLS.NS", "TITAN.NS",
        ],
    },
    "Telecom": {
        "name": "Telecommunications",
        "short": "Telecom",
        "index_symbol": "^CNXTELECOM",
        "color_base": "indigo",
        "stocks": [
            "BHARTIARTL.NS", "IDEA.NS",
        ],
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sector_mood(avg_change: float, advances: int, total: int) -> str:
    breadth = advances / max(total, 1)
    if avg_change > 1.5 and breadth > 0.7:  return "STRONGLY_BULLISH"
    if avg_change > 0.3 and breadth > 0.5:  return "BULLISH"
    if avg_change < -1.5 and breadth < 0.3: return "STRONGLY_BEARISH"
    if avg_change < -0.3 and breadth < 0.5: return "BEARISH"
    return "NEUTRAL"


def _find_sector_leaders(sectors: dict) -> str:
    if not sectors:
        return "no data"
    top = max(sectors.values(), key=lambda s: s.get("avg_change_pct", 0))
    bot = min(sectors.values(), key=lambda s: s.get("avg_change_pct", 0))
    return (
        f"Best: {top['short']} ({top['avg_change_pct']:+.2f}%), "
        f"Worst: {bot['short']} ({bot['avg_change_pct']:+.2f}%)"
    )


# ── Core computation ──────────────────────────────────────────────────────────

def compute_sector_from_cache() -> dict:
    """Synchronously compute sector data from PRICE_CACHE, grouped by
    utils.sector_cache.get_sector() — a persistent, disk-backed cache
    (data/sector_cache.json, ~1,500+ NSE symbols already resolved, weekly
    rebuild + live yfinance fallback on miss) instead of the previous
    ~45-stock SECTOR_DEFINITIONS["stocks"] lists.

    Confirmed live: those hardcoded lists didn't even cover TVS Motor, while
    get_sector() already had it cached ("Consumer"). SECTOR_DEFINITIONS is
    still consulted per sector for its display name/index_symbol/color —
    get_sector() returns the SAME 10 canonical keys (Banking/IT/Energy/
    Pharma/FMCG/Auto/Metals/Infra/Consumer/Telecom) plus "GENERAL" for
    anything unclassified, by design (see _YF_SECTOR_MAP in that module) —
    GENERAL is excluded here rather than lumped into one noisy catch-all
    bucket. Never fails.
    """
    from crawler.live_prices import PRICE_CACHE
    from utils.sector_cache import get_sector

    by_sector: dict[str, list[dict]] = {}
    for symbol, cached in PRICE_CACHE.items():
        if not cached or symbol.startswith("^"):   # skip index entries themselves
            continue
        sector_key = get_sector(symbol)
        if sector_key == "GENERAL":
            continue
        by_sector.setdefault(sector_key, []).append({
            "symbol":       symbol,
            "name":         cached.get("name", symbol.replace(".NS", "")),
            "price":        cached.get("price", 0),
            "change_pct":   cached.get("change_pct", 0),
            "change":       cached.get("change", 0),
            "volume":       cached.get("volume", 0),
            "market_cap_cr": cached.get("market_cap") or 0,
        })

    result: dict[str, Any] = {}
    now = datetime.now(_IST).isoformat()

    for sector_key, stocks_data in by_sector.items():
        if not stocks_data:
            continue
        sector_def = SECTOR_DEFINITIONS.get(sector_key, {})

        total     = len(stocks_data)
        advances  = sum(1 for s in stocks_data if s["change_pct"] > 0)
        declines  = sum(1 for s in stocks_data if s["change_pct"] < 0)
        unchanged = total - advances - declines

        # Volume-weighted average change
        total_vol  = sum(s["volume"] for s in stocks_data) or 1
        avg_change = sum(
            s["change_pct"] * (s["volume"] / total_vol)
            for s in stocks_data
        )

        # Weight each stock by market cap within sector
        total_cap = sum(s["market_cap_cr"] for s in stocks_data) or 1
        for s in stocks_data:
            s["weight_pct"] = round(s["market_cap_cr"] / total_cap * 100, 1)

        sorted_by_cap = sorted(stocks_data, key=lambda x: x["market_cap_cr"], reverse=True)
        top_gainer    = max(stocks_data, key=lambda x: x["change_pct"])
        top_loser     = min(stocks_data, key=lambda x: x["change_pct"])

        # Sector index from PRICE_CACHE if available
        idx = PRICE_CACHE.get(sector_def.get("index_symbol", ""), {})

        result[sector_key] = {
            "sector_key":       sector_key,
            "name":             sector_def.get("name", sector_key),
            "short":            sector_def.get("short", sector_key),
            "index_symbol":     sector_def.get("index_symbol"),
            "color_base":       sector_def.get("color_base", "gray"),
            "stocks":           sorted_by_cap,
            "advances":         advances,
            "declines":         declines,
            "unchanged":        unchanged,
            "total":            total,
            "avg_change_pct":   round(avg_change, 2),
            "index_price":      idx.get("price"),
            "index_change_pct": idx.get("change_pct"),
            "breadth_pct":      round(advances / total * 100, 1),
            "top_gainer": {"symbol": top_gainer["symbol"], "change_pct": top_gainer["change_pct"]},
            "top_loser":  {"symbol": top_loser["symbol"],  "change_pct": top_loser["change_pct"]},
            "mood":         _sector_mood(avg_change, advances, total),
            "last_updated": now,
        }

    return result


def get_sector_rotation_signal() -> dict:
    """Return outperforming / underperforming sectors vs NIFTY 50."""
    from crawler.live_prices import PRICE_CACHE

    cache = SECTOR_CACHE or compute_sector_from_cache()
    nifty_change = PRICE_CACHE.get("^NSEI", {}).get("change_pct", 0) or 0

    outperforming = sorted(
        [
            {"sector": k, "short": v["short"], "avg_change_pct": v["avg_change_pct"],
             "vs_nifty": round(v["avg_change_pct"] - nifty_change, 2)}
            for k, v in cache.items()
            if v["avg_change_pct"] > nifty_change + 0.3
        ],
        key=lambda x: x["vs_nifty"], reverse=True,
    )
    underperforming = sorted(
        [
            {"sector": k, "short": v["short"], "avg_change_pct": v["avg_change_pct"],
             "vs_nifty": round(v["avg_change_pct"] - nifty_change, 2)}
            for k, v in cache.items()
            if v["avg_change_pct"] < nifty_change - 0.3
        ],
        key=lambda x: x["vs_nifty"],
    )

    leaders  = ", ".join(s["short"] for s in outperforming[:3]) or "None"
    laggards = ", ".join(s["short"] for s in underperforming[:3]) or "None"
    note = f"{leaders} leading today's move. {laggards} lagging."

    return {
        "nifty_change_pct": round(nifty_change, 2),
        "outperforming":    outperforming,
        "underperforming":  underperforming,
        "rotation_note":    note,
    }


def get_sector_cache() -> dict:
    """Return SECTOR_CACHE, computing from PRICE_CACHE if not yet populated."""
    return dict(SECTOR_CACHE) if SECTOR_CACHE else compute_sector_from_cache()


async def refresh_sector_data() -> dict:
    """Refresh SECTOR_CACHE from PRICE_CACHE. Async for Celery compatibility."""
    global SECTOR_CACHE
    SECTOR_CACHE = compute_sector_from_cache()
    logger.info(
        f"[sectors] {len(SECTOR_CACHE)} sectors refreshed — "
        f"{_find_sector_leaders(SECTOR_CACHE)}"
    )
    return dict(SECTOR_CACHE)


def get_sector_summary() -> list[dict]:
    """Return sorted sector summary list for heatmap rendering."""
    cache = get_sector_cache()
    summary = []
    for k, v in cache.items():
        summary.append({
            "sector_key":       k,
            "name":             v["name"],
            "short":            v["short"],
            "avg_change_pct":   v["avg_change_pct"],
            "index_change_pct": v.get("index_change_pct"),
            "index_symbol":     v.get("index_symbol"),
            "color_base":       v.get("color_base"),
            "advances":         v["advances"],
            "declines":         v["declines"],
            "unchanged":        v["unchanged"],
            "total":            v["total"],
            "breadth_pct":      v["breadth_pct"],
            "mood":             v["mood"],
            "top_gainer":       v["top_gainer"],
            "top_loser":        v["top_loser"],
            "stock_count":      v["total"],
        })
    return sorted(summary, key=lambda x: x["avg_change_pct"], reverse=True)
