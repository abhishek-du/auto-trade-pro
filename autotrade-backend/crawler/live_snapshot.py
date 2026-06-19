"""Live market snapshot — single Kite OHLC batch call at the start of every cycle.

PRICE_CACHE and SECTOR_CACHE are process-local in-memory dicts.  Celery worker
processes never receive WebSocket ticks from another process, so their copies
stay stale.  This module fixes that by fetching fresh Kite OHLC data (last
price + prev close → change_pct) once per cycle and hot-patching both caches so
every downstream read within that task sees current market data.

Usage (at the very start of Hub cycle or trade loop):
    from crawler.live_snapshot import fetch_live_snapshot
    await fetch_live_snapshot(extra_symbols=open_position_symbols)
"""
from __future__ import annotations

import time
from utils.logger import logger

# ── Indices we always refresh ─────────────────────────────────────────────────
# Keys are the canonical PRICE_CACHE keys used by macro.py / decision_engine.py
# Values are the kite-compatible symbol strings accepted by _symbol_to_kite().

_ALWAYS_FETCH: dict[str, str] = {
    "^NSEI":              "^NSEI",            # NIFTY 50
    "^NSEBANK":           "^NSEBANK",          # BANKNIFTY
    "^INDIAVIX":          "^INDIAVIX",         # India VIX
    "NIFTY IT":           "NIFTY IT",
    "NIFTY PHARMA":       "NIFTY PHARMA",
    "NIFTY AUTO":         "NIFTY AUTO",
    "NIFTY FMCG":         "NIFTY FMCG",
    "NIFTY ENERGY":       "NIFTY ENERGY",
    "NIFTY INFRA":        "NIFTY INFRA",
    "NIFTY METAL":        "NIFTY METAL",
    "NIFTY PSU BANK":     "NIFTY PSU BANK",
    "NIFTY FIN SERVICE":  "NIFTY FIN SERVICE",
    "NIFTY MEDIA":        "NIFTY MEDIA",
    "NIFTY MIDCAP 50":    "NIFTY MIDCAP 50",
    "NIFTY MIDCAP 100":   "NIFTY MIDCAP 100",
    "NIFTY 100":          "NIFTY 100",
    "NIFTY 200":          "NIFTY 200",
    "NIFTY 500":          "NIFTY 500",
    "NIFTY NEXT 50":      "NIFTY NEXT 50",
    "NIFTY SMALLCAP 100": "NIFTY SMALLCAP 100",
}

# Map Kite response key → our canonical PRICE_CACHE key for indices
_KITE_TO_CANONICAL: dict[str, str] = {
    "NSE:NIFTY 50":          "^NSEI",
    "NSE:NIFTY BANK":        "^NSEBANK",
    "NSE:INDIA VIX":         "^INDIAVIX",
    "NSE:NIFTY IT":          "NIFTY IT",
    "NSE:NIFTY PHARMA":      "NIFTY PHARMA",
    "NSE:NIFTY AUTO":        "NIFTY AUTO",
    "NSE:NIFTY FMCG":        "NIFTY FMCG",
    "NSE:NIFTY ENERGY":      "NIFTY ENERGY",
    "NSE:NIFTY INFRA":       "NIFTY INFRA",
    "NSE:NIFTY METAL":       "NIFTY METAL",
    "NSE:NIFTY PSU BANK":    "NIFTY PSU BANK",
    "NSE:NIFTY FIN SERVICE": "NIFTY FIN SERVICE",
    "NSE:NIFTY MEDIA":       "NIFTY MEDIA",
    "NSE:NIFTY MIDCAP 50":   "NIFTY MIDCAP 50",
    "NSE:NIFTY MIDCAP 100":  "NIFTY MIDCAP 100",
    "NSE:NIFTY 100":         "NIFTY 100",
    "NSE:NIFTY 200":         "NIFTY 200",
    "NSE:NIFTY 500":         "NIFTY 500",
    "NSE:NIFTY NEXT 50":     "NIFTY NEXT 50",
    "NSE:NIFTY SMALLCAP 100": "NIFTY SMALLCAP 100",
}

# Sector index canonical key → SECTOR_CACHE sector key
_INDEX_TO_SECTOR: dict[str, str] = {
    "NIFTY IT":          "IT",
    "NIFTY PHARMA":      "Pharma",
    "NIFTY AUTO":        "Auto",
    "NIFTY FMCG":        "FMCG",
    "NIFTY ENERGY":      "Energy",
    "NIFTY INFRA":       "Infra",
    "NIFTY METAL":       "Metals",
    "NIFTY PSU BANK":    "Banking",
    "NIFTY FIN SERVICE": "Banking",
    "NIFTY MEDIA":       "Consumer",
}


def _index_mood(change_pct: float) -> str:
    if change_pct >= 1.5:  return "STRONGLY_BULLISH"
    if change_pct >= 0.3:  return "BULLISH"
    if change_pct <= -1.5: return "STRONGLY_BEARISH"
    if change_pct <= -0.3: return "BEARISH"
    return "NEUTRAL"


async def fetch_live_snapshot(extra_symbols: list[str] | None = None) -> dict[str, dict]:
    """Fetch live OHLC from Kite for all market indices + extra_symbols.

    Hot-patches the local process's PRICE_CACHE and SECTOR_CACHE so every
    downstream read within this Celery task sees current market data.

    Parameters
    ----------
    extra_symbols:
        Additional .NS stock symbols to refresh (e.g. open position symbols).

    Returns
    -------
    dict mapping canonical symbol → {price, change_pct, prev_close, source}
    """
    from crawler.zerodha_client import get_kite_client
    from crawler.zerodha_market import _symbol_to_kite
    from crawler.live_prices import PRICE_CACHE
    from crawler.sector_data import SECTOR_CACHE

    kite = get_kite_client()
    if not kite.access_token:
        logger.debug("[live_snapshot] no Kite token — skipping")
        return {}

    # Build instrument list: always-fetch indices + stock extras
    sym_to_kite: dict[str, str] = {
        canonical: _symbol_to_kite(kite_sym)
        for canonical, kite_sym in _ALWAYS_FETCH.items()
    }
    extra_map: dict[str, str] = {}
    for sym in (extra_symbols or []):
        kite_instr = _symbol_to_kite(sym)
        sym_to_kite[sym] = kite_instr
        extra_map[kite_instr] = sym

    all_instruments = list(set(sym_to_kite.values()))

    # Single batch OHLC call — returns last_price + OHLC (prev_close = ohlc.close)
    try:
        raw = await kite.get_ohlc(all_instruments)
    except Exception as exc:
        logger.warning(f"[live_snapshot] Kite OHLC batch failed: {exc}")
        return {}

    snapshot: dict[str, dict] = {}
    now_ts = time.time()

    for kite_key, data in raw.items():
        last_price = float(data.get("last_price", 0) or 0)
        if last_price <= 0:
            continue
        ohlc        = data.get("ohlc", {})
        prev_close  = float(ohlc.get("close", 0) or 0)
        change_pct  = (
            (last_price - prev_close) / prev_close * 100
            if prev_close > 0 else 0.0
        )
        change      = last_price - prev_close

        entry = {
            "price":      last_price,
            "last_price": last_price,
            "prev_close": prev_close,
            "change":     round(change, 4),
            "change_pct": round(change_pct, 4),
            "open":       float(ohlc.get("open", 0) or 0),
            "high":       float(ohlc.get("high", 0) or 0),
            "low":        float(ohlc.get("low", 0) or 0),
            "source":     "kite_ohlc",
            "_ts":        now_ts,
        }

        # Resolve canonical key: index aliases first, then extra stocks
        canonical = (
            _KITE_TO_CANONICAL.get(kite_key)
            or extra_map.get(kite_key)
            or f"{kite_key.split(':')[-1]}.NS"
        )
        snapshot[canonical] = entry

        # Hot-patch PRICE_CACHE — write under all keys that code might read
        PRICE_CACHE[canonical] = entry
        bare = canonical.replace(".NS", "")
        if bare != canonical:
            PRICE_CACHE[bare] = entry

    # ── Rebuild SECTOR_CACHE from fresh index change_pct ─────────────────────
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _now = _dt.datetime.now(_ZI("Asia/Kolkata")).isoformat()

    for idx_key, sector_key in _INDEX_TO_SECTOR.items():
        entry = snapshot.get(idx_key)
        if not entry:
            continue
        chg_pct = entry["change_pct"]
        mood    = _index_mood(chg_pct)
        existing = SECTOR_CACHE.get(sector_key, {})
        SECTOR_CACHE[sector_key] = {
            **existing,           # keep stock-level data if already populated
            "mood":           mood,
            "avg_change_pct": round(chg_pct, 4),
            "index_price":    entry["price"],
            "index_change_pct": round(chg_pct, 4),
            "updated_at":     _now,
        }

    n_idx   = len([k for k in snapshot if not k.endswith(".NS")])
    n_stock = len(snapshot) - n_idx
    logger.debug(
        f"[live_snapshot] {n_idx} indices + {n_stock} stocks → PRICE_CACHE patched; "
        f"SECTOR_CACHE {len(_INDEX_TO_SECTOR)} sectors updated"
    )
    return snapshot
