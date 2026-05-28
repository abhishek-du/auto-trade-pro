"""Macro/Sector Agent — Varsity Modules 8 + 15.

Reads from existing PRICE_CACHE and SECTOR_CACHE to determine
how macro conditions and sector momentum bias a trade.

Returns -2 to +2 bias score for each symbol.
"""
from __future__ import annotations

from utils.logger import logger

# Sector membership per symbol (keyed to SECTOR_DEFINITIONS in crawler/sector_data.py)
_SECTOR_MAP: dict[str, str] = {
    "TCS.NS": "IT", "INFY.NS": "IT", "WIPRO.NS": "IT",
    "HCLTECH.NS": "IT", "TECHM.NS": "IT", "PERSISTENT.NS": "IT",
    "HDFCBANK.NS": "Banking", "ICICIBANK.NS": "Banking", "SBIN.NS": "Banking",
    "KOTAKBANK.NS": "Banking", "AXISBANK.NS": "Banking", "BAJFINANCE.NS": "Banking",
    "SUNPHARMA.NS": "Pharma", "DRREDDY.NS": "Pharma", "DIVISLAB.NS": "Pharma",
    "CIPLA.NS": "Pharma",
    "MARUTI.NS": "Auto", "TATAMOTORS.NS": "Auto", "BAJAJ-AUTO.NS": "Auto",
    "EICHERMOT.NS": "Auto",
    "HINDUNILVR.NS": "FMCG", "ITC.NS": "FMCG", "NESTLEIND.NS": "FMCG",
    "DABUR.NS": "FMCG",
    "TATASTEEL.NS": "Metals", "JSWSTEEL.NS": "Metals", "HINDALCO.NS": "Metals",
    "RELIANCE.NS": "Energy", "ONGC.NS": "Energy", "NTPC.NS": "Energy",
    "POWERGRID.NS": "Energy",
    "LT.NS": "Infra", "ULTRACEMCO.NS": "Infra",
}


class MacroSectorAgent:

    def read_macro(self) -> dict:
        from crawler.live_prices import PRICE_CACHE
        def pct(sym: str) -> float:
            return float(PRICE_CACHE.get(sym, {}).get("change_pct", 0) or 0) / 100

        return {
            "usdinr_chg":   pct("USDINR=X"),
            "crude_chg":    pct("CL=F"),
            "india_vix":    float(PRICE_CACHE.get("^INDIAVIX", {}).get("price", 15) or 15),
            "nifty_chg":    pct("^NSEI"),
        }

    def sector_of(self, symbol: str) -> str:
        return _SECTOR_MAP.get(symbol, "GENERAL")

    def bias(self, symbol: str) -> int:
        """Return integer bias -2 … +2 (Varsity M8 + M15)."""
        try:
            m = self.read_macro()
        except Exception:
            return 0

        sector = self.sector_of(symbol)
        b = 0

        # USDINR — Varsity M8: weak rupee benefits exporters
        if m["usdinr_chg"] >= 0.015:
            if sector in ("IT", "Pharma"):   b += 1
            if sector in ("Energy", "Auto"): b -= 1

        # Crude oil impact
        if m["crude_chg"] >= 0.05:
            if sector == "Energy":               b += 1
            if sector in ("Auto", "FMCG"):       b -= 1

        # India VIX — Varsity M9
        if m["india_vix"] >= 22:   b -= 1
        elif m["india_vix"] <= 12: b += 1

        # Sector momentum from SECTOR_CACHE
        try:
            from crawler.sector_data import SECTOR_CACHE
            sector_data = SECTOR_CACHE.get(sector, {})
            mood = sector_data.get("mood", "NEUTRAL")
            if mood == "STRONGLY_BULLISH": b += 1
            if mood == "STRONGLY_BEARISH": b -= 1
        except Exception:
            pass

        return max(-2, min(2, b))
