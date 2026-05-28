"""Fundamentals Agent — Varsity Module 3 scoring.

Fetches data via existing engine/fundamental_analyzer.py.
Returns a 0-100 score and investment grade tier.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from utils.logger import logger

# In-process 24-hour cache: {symbol: (score, grade, fetched_at)}
_FUND_CACHE: dict[str, tuple[int, str, datetime]] = {}
_FUND_CACHE_TTL = timedelta(hours=24)


@dataclass
class FundamentalProfile:
    pe:                  Optional[float]
    pb:                  Optional[float]
    roe_ttm:             Optional[float]
    debt_equity:         Optional[float]
    sales_growth_5y:     Optional[float]
    eps_growth_5y:       Optional[float]
    promoter_pledge_pct: Optional[float]


class FundamentalsAgent:

    async def fetch_profile(self, symbol: str) -> FundamentalProfile:
        from engine.fundamental_analyzer import fetch_fundamentals_yfinance
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, fetch_fundamentals_yfinance, symbol)
        de_raw = data.get("debtToEquity")
        de = de_raw / 100 if de_raw is not None else None
        roe_raw = data.get("returnOnEquity")
        roe = roe_raw * 100 if roe_raw is not None else None
        return FundamentalProfile(
            pe=data.get("trailingPE"),
            pb=data.get("priceToBook"),
            roe_ttm=roe,
            debt_equity=de,
            sales_growth_5y=data.get("revenueGrowth"),
            eps_growth_5y=data.get("earningsGrowth"),
            promoter_pledge_pct=None,
        )

    def score(self, p: FundamentalProfile) -> int:
        """Varsity Module 3 deterministic scoring."""
        s = 50  # neutral baseline

        if p.roe_ttm is not None:
            if p.roe_ttm >= 15:   s += 15
            elif p.roe_ttm >= 10: s += 5
            else:                 s -= 5

        if p.debt_equity is not None:
            if p.debt_equity <= 1.0:   s += 10
            elif p.debt_equity > 3.0:  s -= 10

        if p.sales_growth_5y is not None:
            if p.sales_growth_5y >= 0.10:  s += 10
            elif p.sales_growth_5y < 0:    s -= 10

        if p.eps_growth_5y is not None:
            if p.eps_growth_5y >= 0.12:    s += 10
            elif p.eps_growth_5y < 0:      s -= 10

        if p.pe is not None:
            if p.pe <= 15:   s += 10
            elif p.pe <= 25: s += 5
            elif p.pe > 50:  s -= 10

        if p.promoter_pledge_pct is not None:
            if p.promoter_pledge_pct < 10:   s += 5
            elif p.promoter_pledge_pct > 30: s -= 15

        return max(0, min(100, s))

    def grade(self, score: int) -> str:
        if score >= 65:  return "INVESTMENT"
        if score >= 45:  return "WATCHLIST"
        return "REJECT"

    async def get_cached_grade(self, symbol: str) -> tuple[int, str]:
        now = datetime.utcnow()
        cached = _FUND_CACHE.get(symbol)
        if cached:
            score, grade, fetched = cached
            if now - fetched < _FUND_CACHE_TTL:
                return score, grade

        try:
            profile = await self.fetch_profile(symbol)
            sc = self.score(profile)
            gr = self.grade(sc)
            _FUND_CACHE[symbol] = (sc, gr, now)
            return sc, gr
        except Exception as exc:
            logger.debug(f"[agent/fundamentals] fetch failed for {symbol}: {exc}")
            return 50, "WATCHLIST"
