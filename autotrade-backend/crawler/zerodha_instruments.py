"""Zerodha instrument token cache — symbol ↔ instrument_token lookup.

Provides a fast in-memory mapping for the 35 NSE equities + 3 indices used
by AutoTrade Pro.  When connected, refresh_instrument_cache() fetches the
full NSE instrument list from Kite and overrides the hardcoded fallback.
"""

from __future__ import annotations

import asyncio

from utils.logger import logger

# ── Hardcoded fallback tokens (verified against Zerodha NSE master) ──────────

HARDCODED_TOKENS: dict[str, int] = {
    # Large-cap NSE equities (refreshed 2026-06-18)
    "RELIANCE":    738561,
    "TCS":        2953217,
    "HDFCBANK":    341249,
    "INFY":        408065,
    "ICICIBANK":  1270529,
    "HINDUNILVR":  356865,
    "SBIN":        779521,
    "BHARTIARTL": 2714625,
    "ITC":         424961,
    "KOTAKBANK":   492033,
    "LT":         2939649,
    "AXISBANK":   1510401,
    "ASIANPAINT":   60417,
    "MARUTI":     2815745,
    "BAJFINANCE":   81153,
    "WIPRO":       969473,
    "HCLTECH":    1850625,
    "ULTRACEMCO": 2952193,
    "NESTLEIND":  4598529,
    "POWERGRID":  3834113,
    "SUNPHARMA":   857857,
    "DRREDDY":     225537,
    # Mid-cap
    "PIDILITIND":  681985,
    "VOLTAS":      951809,
    "MUTHOOTFIN": 6054401,
    "PERSISTENT": 4701441,
    "COFORGE":    2955009,
    "LTTS":       4752385,
    "TATAELXSI":   873217,
    "METROPOLIS": 2452737,
    "LALPATHLAB": 2983425,
    "ASTRAL":     3691009,
    # Energy / utilities (extras)
    "NTPC":       2977281,
    "COALINDIA":  5215745,
    "ONGC":       633601,
    # Broad indices
    "NIFTY 50":           256265,
    "SENSEX":             265,        # BSE token
    "NIFTY BANK":         260105,
    "INDIA VIX":          264969,
    "NIFTY 100":          260617,
    "NIFTY 200":          264457,
    "NIFTY 500":          268041,
    "NIFTY NEXT 50":      270857,
    "NIFTY MIDCAP 50":    260873,
    "NIFTY MIDCAP 100":   256777,
    "NIFTY SMALLCAP 100": 267017,
    # Sector indices
    "NIFTY IT":           259849,
    "NIFTY PHARMA":       262409,
    "NIFTY AUTO":         263433,
    "NIFTY FMCG":         261897,
    "NIFTY ENERGY":       261641,
    "NIFTY INFRA":        261385,
    "NIFTY METAL":        263689,
    "NIFTY PSU BANK":     262921,
    "NIFTY FIN SERVICE":  257801,
    "NIFTY MEDIA":        263945,
}

# Live cache — populated by refresh_instrument_cache()
INSTRUMENT_CACHE: dict[str, dict] = {}


# ── Token lookup ─────────────────────────────────────────────────────────────

def get_token(symbol: str) -> int | None:
    """Resolve a symbol to its instrument_token.

    Accepts "RELIANCE", "RELIANCE.NS", or full "NSE:RELIANCE" forms.
    """
    sym = symbol.strip().upper()
    if sym.endswith(".NS"):
        sym = sym[:-3]
    if ":" in sym:
        sym = sym.split(":", 1)[1]
    # Try live cache first
    if sym in INSTRUMENT_CACHE:
        return INSTRUMENT_CACHE[sym].get("instrument_token")
    # NSE surveillance / Trade-to-Trade / SME suffix fallback. NSE lists stocks
    # under special segments with a suffixed tradingsymbol (e.g. ORBTEXP-BE for
    # Trade-to-Trade, -BZ/-BT for other surveillance tiers, -SM/-ST for SME). The
    # plain ticker ("ORBTEXP") then isn't a key in the NSE instrument cache, so
    # token resolution returned None → no candles and no live price → the position
    # froze at entry with a fake ₹0.00 P&L (observed 9-Jul for ORBTEXP). Fall back
    # to the suffixed variant so these names can still be priced/marked correctly.
    for _sfx in ("-BE", "-BZ", "-BT", "-SM", "-ST"):
        if (sym + _sfx) in INSTRUMENT_CACHE:
            return INSTRUMENT_CACHE[sym + _sfx].get("instrument_token")
    # Hardcoded fallback (also try the .NS-style key for indices)
    if sym in HARDCODED_TOKENS:
        return HARDCODED_TOKENS[sym]
    # Index aliases
    aliases = {
        "^NSEI": "NIFTY 50",
        "^BSESN": "SENSEX",
        "^NSEBANK": "NIFTY BANK",
        "^INDIAVIX": "INDIA VIX",
    }
    if symbol in aliases:
        return HARDCODED_TOKENS.get(aliases[symbol])
    return None


def symbol_to_kite(symbol: str) -> str:
    """Convert yfinance symbol → 'EXCHANGE:TRADINGSYMBOL' form for Kite."""
    s = symbol.strip()
    upper = s.upper()
    index_map = {
        "^NSEI": "NSE:NIFTY 50",
        "^BSESN": "BSE:SENSEX",
        "^NSEBANK": "NSE:NIFTY BANK",
        "^INDIAVIX": "NSE:INDIA VIX",
    }
    if upper in index_map:
        return index_map[upper]
    if upper.endswith(".NS"):
        return f"NSE:{upper[:-3]}"
    if upper.endswith(".BO"):
        return f"BSE:{upper[:-3]}"
    if ":" in upper:
        return upper
    return f"NSE:{upper}"


# ── Refresh from Kite ────────────────────────────────────────────────────────

async def refresh_instrument_cache() -> int:
    """Download the full NSE instrument list from Kite into INSTRUMENT_CACHE.

    Falls back silently if Kite is not connected or the call fails.
    """
    try:
        from crawler.zerodha_kite_lib import get_instruments
        rows = await asyncio.to_thread(get_instruments, "NSE")
    except Exception as exc:
        logger.warning(f"[zerodha_instruments] Refresh failed, keeping hardcoded: {exc}")
        return 0

    count = 0
    for r in rows:
        try:
            sym = str(r.get("tradingsymbol", "")).strip().upper()
            if not sym:
                continue
            INSTRUMENT_CACHE[sym] = {
                "instrument_token": int(r.get("instrument_token") or 0),
                "exchange_token":   int(r.get("exchange_token") or 0),
                "name":             str(r.get("name") or ""),
                "tick_size":        float(r.get("tick_size") or 0.05),
                "lot_size":         int(float(r.get("lot_size") or 1)),
                "instrument_type":  str(r.get("instrument_type") or "EQ"),
                "segment":          str(r.get("segment") or "NSE"),
                "exchange":         str(r.get("exchange") or "NSE"),
            }
            count += 1
        except (TypeError, ValueError):
            continue

    logger.info(f"[zerodha_instruments] Cache refreshed — {count} NSE instruments")
    return count
