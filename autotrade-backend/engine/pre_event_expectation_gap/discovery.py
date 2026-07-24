"""Module 1: Event Discovery for the Pre-Event Expectation Gap strategy.

Finds PUBLIC, SCHEDULED events the strategy may anticipate. Reuses the existing
MarketEvent/calendar infrastructure and the NSE board-meeting fetch — it does
NOT build a second calendar system (per the audit).

Only public event-timing information is used. No UPSI, no private/leaked data,
nothing that became public only after a prediction cutoff.

Phase 1 event types are supported via a small extensible registry so future
types (board meetings, dividends, RBI, policy…) can be added without touching
callers. Phase 2 implements QUARTERLY_RESULT discovery fully; the registry hook
for other types is present but intentionally not yet populated.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger
from engine.pre_event_expectation_gap.types import ScheduledEvent, PreEventType


# Keywords that mark a board-meeting purpose as a quarterly-results meeting
# (the meeting itself is the scheduled event we anticipate). Mirrors the
# classification used elsewhere for NSE filings; deliberately conservative.
_RESULT_PURPOSE_KEYWORDS = (
    "financial result", "unaudited result", "audited result", "quarterly result",
    "quarterly financial", "results for the quarter", "q1", "q2", "q3", "q4",
)


def _looks_like_results_meeting(purpose: str) -> bool:
    p = (purpose or "").lower()
    return any(k in p for k in _RESULT_PURPOSE_KEYWORDS)


async def _discover_quarterly_results(
    session: AsyncSession, universe: set[str], from_date: date, to_date: date,
) -> list[ScheduledEvent]:
    """QUARTERLY_RESULT events from two public sources, merged by (symbol, date):
      1. MarketEvent rows of type EARNINGS (calendar_engine seeds these).
      2. NSE board-meeting filings whose purpose is a results meeting.
    """
    events: dict[tuple[str, date], ScheduledEvent] = {}

    # Source 1 — MarketEvent EARNINGS
    try:
        from engine.calendar_engine import get_events_for_range
        rows = await get_events_for_range(session, from_date, to_date, event_types=["EARNINGS"])
        for ev in rows:
            if not ev.symbol or (universe and ev.symbol not in universe):
                continue
            key = (ev.symbol, ev.event_date)
            events[key] = ScheduledEvent(
                symbol=ev.symbol,
                event_type=PreEventType.QUARTERLY_RESULT,
                event_date=ev.event_date,
                event_time=ev.time_ist,
                event_confidence=0.9 if ev.is_confirmed else 0.5,
                source="market_event_calendar",
                status="CONFIRMED" if ev.is_confirmed else "ESTIMATED",
            )
    except Exception as exc:
        logger.warning(f"[pre_event_gap/discovery] MarketEvent EARNINGS fetch failed: {exc}")

    # Source 2 — NSE board-meeting filings (results meetings). A live filing of
    # an upcoming results-meeting date is the strongest public confirmation, so
    # it overrides a calendar estimate for the same symbol/date.
    try:
        from engine.nse_crawler import fetch_board_meetings_for_symbols
        by_symbol = await fetch_board_meetings_for_symbols(sorted(universe)) if universe else {}
        for symbol, items in by_symbol.items():
            # Defensive: only ever emit universe symbols, regardless of what the
            # fetch returns (discovery must never surface an off-universe name).
            if universe and symbol not in universe:
                continue
            for item in items:
                md = item.get("meeting_date")
                if md is None or not (from_date <= md <= to_date):
                    continue
                if not _looks_like_results_meeting(item.get("purpose", "")):
                    continue
                key = (symbol, md)
                events[key] = ScheduledEvent(
                    symbol=symbol,
                    event_type=PreEventType.QUARTERLY_RESULT,
                    event_date=md,
                    event_time=None,
                    event_confidence=0.95,
                    source="nse_board_meeting",
                    status="CONFIRMED",
                )
    except Exception as exc:
        logger.debug(f"[pre_event_gap/discovery] board-meeting fetch skipped/failed: {exc}")

    return list(events.values())


# Extensible registry: event_type -> discovery coroutine. Phase 2 populates
# QUARTERLY_RESULT only; the shape is here so future types are additive.
_DISCOVERERS = {
    PreEventType.QUARTERLY_RESULT: _discover_quarterly_results,
    # PreEventType.MONTHLY_AUTO_SALES: _discover_monthly_auto_sales,   # future
    # PreEventType.SCHEDULED_BUSINESS_UPDATE: ...                      # future
}


async def discover_scheduled_events(
    session: AsyncSession,
    *,
    universe: list[str] | None = None,
    min_days_until: int = 1,
    max_days_until: int = 15,
    event_types: list[PreEventType] | None = None,
) -> list[ScheduledEvent]:
    """Public entry point. Returns scheduled events for symbols in `universe`
    whose date is `min_days_until`..`max_days_until` trading-ish days out.

    Fail-soft: a failure in any single discoverer logs and is skipped; it never
    raises into the caller (the strategy must degrade to 'no candidates', never
    crash — and it must never take down the News Strategy, which it shares no
    code path with anyway)."""
    today = date.today()
    from_date = today + timedelta(days=min_days_until)
    to_date = today + timedelta(days=max_days_until)
    uni = set(universe or [])
    types = event_types or list(_DISCOVERERS.keys())

    out: list[ScheduledEvent] = []
    for et in types:
        discoverer = _DISCOVERERS.get(et)
        if discoverer is None:
            continue
        try:
            out.extend(await discoverer(session, uni, from_date, to_date))
        except Exception as exc:
            logger.warning(f"[pre_event_gap/discovery] discoverer for {et.value} failed: {exc}")

    out.sort(key=lambda e: (e.event_date, e.symbol))
    return out
