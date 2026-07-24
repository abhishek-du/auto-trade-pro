"""Shared financial-data layer for sector adapters (Phase 5.5).

Centralizes the two data sources adapters legitimately have:
  1. Point-in-time quarterly financial series (Upstox), filtered so only
     quarters whose results were public by `as_of` are used.
  2. The HISTORICAL 3-year profit CAGR (FundamentalData.profit_growth_3yr),
     used as a historical-baseline anchor — WITH its `known_at` timestamp so
     the point-in-time rule (anchor_known_at <= as_of) can be enforced.

Nothing here fabricates a value. Adapters do the sector-specific interpretation;
this module only sources and point-in-time-filters the raw data.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger

# Typical lag (days) between an Indian quarter-end and its results being public.
RESULTS_LAG_DAYS = 40

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def period_end_date(period: str | None) -> date | None:
    """Parse 'Jun 2026' / 'Mar 2025' → approximate quarter-END date, else None."""
    if not period:
        return None
    parts = str(period).strip().lower().replace(",", " ").split()
    mon = yr = None
    for p in parts:
        if p[:3] in _MONTHS and mon is None:
            mon = _MONTHS[p[:3]]
        elif p.isdigit() and len(p) == 4:
            yr = int(p)
    if mon is None or yr is None:
        return None
    return date(yr, mon, monthrange(yr, mon)[1])


def available_series(history: list[dict], as_of: date) -> list[tuple[date, float]]:
    """From Upstox history rows (most-recent-first), keep only quarters whose
    results were plausibly public at `as_of`, returned OLDEST-first."""
    out: list[tuple[date, float]] = []
    for row in history or []:
        val = row.get("value")
        pend = period_end_date(row.get("period"))
        if val is None or pend is None:
            continue
        if (as_of - pend).days < RESULTS_LAG_DAYS:
            continue   # not yet public at as_of (includes the pending quarter)
        try:
            out.append((pend, float(val)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda t: t[0])
    return out


def _category_history(income_rows: list[dict], category: str) -> list[dict]:
    for row in income_rows or []:
        if row.get("category") == category:
            return row.get("history") or []
    return []


@dataclass
class QuarterlySeries:
    revenue: list[tuple[date, float]]
    net_profit: list[tuple[date, float]]
    operating_profit: list[tuple[date, float]]

    def n_profit(self) -> int:
        return len(self.net_profit)

    def n_revenue(self) -> int:
        return len(self.revenue)


async def get_pit_quarterly_series(symbol: str, as_of: date, session: AsyncSession) -> QuarterlySeries:
    """Point-in-time quarterly series (oldest-first) for revenue/net_profit/
    operating_profit. Empty lists on any failure (fail-soft)."""
    income = {}
    try:
        from crawler.upstox_data import get_income_statement
        income = await get_income_statement(symbol, period="quarterly") or {}
    except Exception as exc:
        logger.debug(f"[pre_event_gap/financials] {symbol}: quarterly income fetch failed: {exc}")
    rows = income.get("income_statement") or []
    return QuarterlySeries(
        revenue=available_series(_category_history(rows, "revenue"), as_of),
        net_profit=available_series(_category_history(rows, "net_profit"), as_of),
        operating_profit=available_series(_category_history(rows, "operating_profit"), as_of),
    )


def recent_growth(series: list[tuple[date, float]]) -> tuple[float | None, bool]:
    """(growth, is_yoy). YoY (latest vs 4-back) when >=5 quarters — seasonal-safe
    and annual. Otherwise QoQ (latest vs prior) — coarse and NOT annual. None if
    not computable or base <= 0."""
    if len(series) >= 5:
        latest, base = series[-1][1], series[-5][1]
        is_yoy = True
    elif len(series) >= 2:
        latest, base = series[-1][1], series[-2][1]
        is_yoy = False
    else:
        return None, False
    if base is None or base <= 0:
        return None, is_yoy
    return (latest - base) / base, is_yoy


@dataclass
class HistoricalBaseline:
    value: float | None            # fractional 3y profit CAGR (e.g. 0.30)
    known_at: datetime | None      # when this value was demonstrably known (FundamentalData.last_updated)


async def get_historical_baseline_3y_cagr(symbol: str, session: AsyncSession) -> HistoricalBaseline:
    """HISTORICAL_BASELINE_3Y_CAGR from FundamentalData.profit_growth_3yr (stored
    as a percentage → returned fractional). `known_at` = the row's last_updated,
    the only timestamp we have for when this value was known. Callers MUST
    enforce known_at <= as_of before using it in historical replay.

    NOTE: FundamentalData is a single row per symbol, updated in place, so
    last_updated is ~current — this value can be trusted point-in-time only for
    live (as_of≈now) prediction, not for a past replay cutoff. That is by design
    and is what the point-in-time gate below relies on."""
    try:
        from db.models import FundamentalData
        bare = symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}.NS"
        row = (await session.execute(
            select(FundamentalData).where(FundamentalData.symbol.in_([symbol, bare]))
        )).scalars().first()
        if row is None or row.profit_growth_3yr is None:
            return HistoricalBaseline(value=None, known_at=None)
        return HistoricalBaseline(value=row.profit_growth_3yr / 100.0, known_at=row.last_updated)
    except Exception as exc:
        logger.debug(f"[pre_event_gap/financials] {symbol}: 3y CAGR lookup failed: {exc}")
        return HistoricalBaseline(value=None, known_at=None)
