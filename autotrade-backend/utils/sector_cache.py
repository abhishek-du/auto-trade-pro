"""Persistent sector mapping for NSE stocks.

Two layers:
  1. In-memory dict loaded from data/sector_cache.json (built by rebuild_sector_cache).
  2. Live yfinance fallback on total miss, result stored back to the cache.

All lookups return one of the keys tracked in SectorContext:
  Banking, IT, Energy, Pharma, FMCG, Auto, Metals, Infra, Consumer, Telecom, GENERAL
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from utils.logger import logger

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "sector_cache.json")
_CACHE_PATH = os.path.normpath(_CACHE_PATH)

# yfinance sector label → our internal sector key
_YF_SECTOR_MAP: dict[str, str] = {
    "technology":               "IT",
    "information technology":   "IT",
    "software":                 "IT",
    "financial services":       "Banking",
    "financial":                "Banking",
    "banks":                    "Banking",
    "insurance":                "Banking",
    "healthcare":               "Pharma",
    "pharmaceutical":           "Pharma",
    "health care":              "Pharma",
    "consumer defensive":       "FMCG",
    "consumer staples":         "FMCG",
    "consumer cyclical":        "Consumer",
    "consumer discretionary":   "Consumer",
    "energy":                   "Energy",
    "utilities":                "Energy",
    "basic materials":          "Metals",
    "materials":                "Metals",
    "metals & mining":          "Metals",
    "industrials":              "Infra",
    "capital goods":            "Infra",
    "infrastructure":           "Infra",
    "real estate":              "Infra",
    "communication services":   "Telecom",
    "telecom":                  "Telecom",
    "automobile":               "Auto",
    "auto":                     "Auto",
}


def _load() -> dict[str, str]:
    """Load the JSON cache from disk; return empty dict if missing or corrupt."""
    try:
        with open(_CACHE_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"[sector_cache] load error: {exc}")
    return {}


def _save(mapping: dict[str, str]) -> None:
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w") as f:
            json.dump(mapping, f, sort_keys=True, indent=2)
    except Exception as exc:
        logger.warning(f"[sector_cache] save error: {exc}")


# Loaded once at module import
_cache: dict[str, str] = _load()
logger.debug(f"[sector_cache] loaded {len(_cache)} symbol→sector mappings from disk")


def get_sector(symbol: str) -> str:
    """Return the sector key for a bare symbol (no .NS/.BO suffix).

    Lookup order:
      1. In-memory cache (disk-backed)
      2. Live yfinance lookup (capped — won't retry a seen symbol)
    Returns 'GENERAL' on total miss.
    """
    bare = symbol.replace(".NS", "").replace(".BO", "")
    if bare in _cache:
        return _cache[bare]

    # Avoid re-querying the same unknown symbol every cycle
    if bare in _seen_unknowns:
        return "GENERAL"
    _seen_unknowns.add(bare)

    # Live yfinance fallback (synchronous; this is fine — it's called from sync context)
    sector = _yf_lookup(f"{bare}.NS")
    if sector != "GENERAL":
        _cache[bare] = sector
        _save(_cache)
    return sector


# Symbols we've already failed to resolve — avoids thrashing yfinance on unknown tickers
_seen_unknowns: set[str] = set()


@lru_cache(maxsize=512)
def _yf_lookup(ns_symbol: str) -> str:
    """Single yfinance sector lookup; result cached by lru_cache."""
    try:
        import yfinance as yf
        info = yf.Ticker(ns_symbol).info
        raw = (info.get("sector") or info.get("sectorDisp") or "").lower().strip()
        sector = _YF_SECTOR_MAP.get(raw, "")
        if sector:
            logger.debug(f"[sector_cache] live lookup {ns_symbol} → {sector} ({raw!r})")
            return sector
    except Exception as exc:
        logger.debug(f"[sector_cache] yfinance {ns_symbol}: {exc}")
    return "GENERAL"


# ── Bulk rebuild (run once / weekly) ─────────────────────────────────────────

async def rebuild_sector_cache(session=None) -> int:
    """Fetch sector for all NSE EQ symbols in kite_instruments using yfinance.

    Runs concurrently (max 20 workers). Updates the JSON cache file.
    Returns the count of symbols successfully mapped.
    """
    import asyncio
    from sqlalchemy import text

    if session is None:
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            return await rebuild_sector_cache(session)

    result = await session.execute(
        text("""
            SELECT tradingsymbol FROM kite_instruments
            WHERE exchange = 'NSE' AND instrument_type = 'EQ'
            ORDER BY tradingsymbol
        """)
    )
    symbols = [row[0] for row in result.fetchall()]
    logger.info(f"[sector_cache] rebuilding cache for {len(symbols)} NSE EQ symbols")

    semaphore = asyncio.Semaphore(20)
    loop = asyncio.get_running_loop()

    async def _fetch_one(sym: str) -> tuple[str, str]:
        async with semaphore:
            sector = await loop.run_in_executor(None, lambda: _yf_lookup(f"{sym}.NS"))
            return sym, sector

    tasks = [_fetch_one(sym) for sym in symbols if sym not in _cache]
    if not tasks:
        logger.info("[sector_cache] cache already up-to-date, nothing to fetch")
        return len(_cache)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    added = 0
    for item in results:
        if isinstance(item, Exception):
            continue
        sym, sector = item
        if sector != "GENERAL":
            _cache[sym] = sector
            added += 1

    _save(_cache)
    logger.info(f"[sector_cache] rebuild complete: {added} new mappings, {len(_cache)} total")
    return len(_cache)
