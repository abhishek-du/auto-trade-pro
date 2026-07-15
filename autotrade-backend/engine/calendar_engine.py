"""Indian Market Calendar Engine.

Generates, seeds and queries market events:
  F&O expiry dates (NSE/BSE rules effective Sep 2025)
  RBI MPC meeting and rate-decision dates
  NSE trading holidays
  FII/DII daily data-release schedule
  IPO open/close/listing dates (from NSE API)
  Earnings dates (from yfinance)
"""

from __future__ import annotations

import asyncio
import calendar
import logging
from collections import Counter
from datetime import date, timedelta

import httpx
from dateutil.relativedelta import relativedelta
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MarketEvent
from utils.logger import logger

# ── NSE holiday list 2026 (user-verified) ─────────────────────────────────────

NSE_HOLIDAYS_2026: list[date] = [
    date(2026, 1, 26),
    date(2026, 3, 25),
    date(2026, 4, 14),
    date(2026, 4, 17),
    date(2026, 5,  1),
    date(2026, 8, 15),
    date(2026, 10, 2),
    date(2026, 10, 20),
    date(2026, 11,  5),
    date(2026, 11, 16),
    date(2026, 12, 25),
]

def _get_dynamic_holidays_set() -> set[date]:
    from utils.nse_market_status import fetch_nse_holidays_sync
    dynamic_map = fetch_nse_holidays_sync()
    from datetime import datetime
    if dynamic_map:
        return {datetime.strptime(k, "%Y-%m-%d").date() for k in dynamic_map.keys()}
    return set(NSE_HOLIDAYS_2026)

_HOLIDAY_SET: set[date] = _get_dynamic_holidays_set()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION A — F&O Expiry Generator
# ═══════════════════════════════════════════════════════════════════════════════

def _get_weekday_dates(year: int, month: int, weekday: int) -> list[date]:
    """Return all dates in month that fall on weekday (0=Mon … 6=Sun)."""
    _, days_in_month = calendar.monthrange(year, month)
    return [
        date(year, month, d)
        for d in range(1, days_in_month + 1)
        if date(year, month, d).weekday() == weekday
    ]


def _adjust_for_holiday(d: date) -> date:
    """Shift expiry backward past holidays until a trading day is found."""
    while d in _HOLIDAY_SET or d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def generate_fno_expiry_dates(year: int, month: int) -> list[dict]:
    """All NSE + BSE F&O expiry events for a given month."""
    events: list[dict] = []

    # ── NSE: Tuesday expiries ──────────────────────────────────────────────────
    all_tuesdays = _get_weekday_dates(year, month, 1)  # Tuesday = 1
    if all_tuesdays:
        last_tuesday = max(all_tuesdays)
        for tuesday in all_tuesdays:
            adjusted = _adjust_for_holiday(tuesday)
            is_monthly = (tuesday == last_tuesday)
            events.append({
                "event_type": "FNO_EXPIRY",
                "title":      "NIFTY Monthly + All F&O Expiry" if is_monthly else "NIFTY Weekly Expiry",
                "event_date": adjusted,
                "importance": "HIGH" if is_monthly else "MEDIUM",
                "is_confirmed": True,
                "source":     "HARDCODED",
                "event_metadata": {
                    "exchange":    "NSE",
                    "indices":     ["NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"] if is_monthly else ["NIFTY50"],
                    "is_monthly":  is_monthly,
                    "original_date": str(tuesday),
                    "adjusted":    str(adjusted) != str(tuesday),
                },
            })

    # ── BSE: Thursday expiries ─────────────────────────────────────────────────
    all_thursdays = _get_weekday_dates(year, month, 3)  # Thursday = 3
    if all_thursdays:
        last_thursday = max(all_thursdays)
        for thursday in all_thursdays:
            adjusted = _adjust_for_holiday(thursday)
            is_monthly = (thursday == last_thursday)
            events.append({
                "event_type": "FNO_EXPIRY",
                "title":      "BSE Sensex Monthly Expiry" if is_monthly else "BSE Sensex Weekly Expiry",
                "event_date": adjusted,
                "importance": "MEDIUM",
                "is_confirmed": True,
                "source":     "HARDCODED",
                "event_metadata": {
                    "exchange":   "BSE",
                    "index":      "SENSEX",
                    "is_monthly": is_monthly,
                    "original_date": str(thursday),
                    "adjusted":   str(adjusted) != str(thursday),
                },
            })

    return events


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION B — RBI MPC Events
# ═══════════════════════════════════════════════════════════════════════════════

_RBI_MPC_FY27 = [
    {"start": "2026-04-06", "end": "2026-04-08", "decision": "2026-04-08", "confirmed": True},
    {"start": "2026-06-03", "end": "2026-06-05", "decision": "2026-06-05", "confirmed": True},
    {"start": "2026-08-01", "end": "2026-08-03", "decision": "2026-08-03", "confirmed": False},
    {"start": "2026-10-01", "end": "2026-10-03", "decision": "2026-10-03", "confirmed": False},
    {"start": "2026-12-01", "end": "2026-12-03", "decision": "2026-12-03", "confirmed": False},
    {"start": "2027-02-01", "end": "2027-02-03", "decision": "2027-02-03", "confirmed": False},
]


def get_rbi_mpc_events() -> list[dict]:
    events: list[dict] = []
    for m in _RBI_MPC_FY27:
        start    = date.fromisoformat(m["start"])
        decision = date.fromisoformat(m["decision"])
        end      = date.fromisoformat(m["end"])
        confirmed = m["confirmed"]

        events.append({
            "event_type":   "RBI_MPC",
            "title":        "RBI MPC Meeting Begins",
            "event_date":   start,
            "start_date":   start,
            "end_date":     end,
            "importance":   "HIGH",
            "is_confirmed": confirmed,
            "source":       "HARDCODED",
            "description":  "Reserve Bank of India Monetary Policy Committee meeting begins.",
            "event_metadata": {"current_rate": 5.25, "governor": "Sanjay Malhotra"},
        })
        events.append({
            "event_type":   "RBI_MPC",
            "title":        "RBI Rate Decision Announcement",
            "event_date":   decision,
            "start_date":   start,
            "end_date":     end,
            "time_ist":     "10:00 AM",
            "importance":   "HIGH",
            "is_confirmed": confirmed,
            "source":       "HARDCODED",
            "description":  "RBI Governor announces repo rate decision. Current rate: 5.25%",
            "event_metadata": {"current_rate": 5.25, "governor": "Sanjay Malhotra"},
        })
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION C — NSE Holidays
# ═══════════════════════════════════════════════════════════════════════════════

_HOLIDAYS_2026 = [
    {"date": "2026-01-26", "name": "Republic Day"},
    {"date": "2026-03-25", "name": "Holi"},
    {"date": "2026-04-14", "name": "Dr. Ambedkar Jayanti"},
    {"date": "2026-04-17", "name": "Good Friday"},
    {"date": "2026-05-01", "name": "Maharashtra Day"},
    {"date": "2026-08-15", "name": "Independence Day"},
    {"date": "2026-10-02", "name": "Gandhi Jayanti"},
    {"date": "2026-10-20", "name": "Diwali Laxmi Puja"},
    {"date": "2026-11-05", "name": "Diwali Balipratipada"},
    {"date": "2026-11-16", "name": "Gurunanak Jayanti"},
    {"date": "2026-12-25", "name": "Christmas"},
]


def get_nse_holidays_2026() -> list[dict]:
    return [
        {
            "event_type":   "HOLIDAY",
            "title":        f"NSE Holiday — {h['name']}",
            "event_date":   date.fromisoformat(h["date"]),
            "importance":   "HIGH",
            "is_confirmed": True,
            "source":       "HARDCODED",
            "description":  f"NSE market closed: {h['name']}",
        }
        for h in _HOLIDAYS_2026
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION D — FII/DII Release Schedule
# ═══════════════════════════════════════════════════════════════════════════════

def generate_fiidii_release_dates(year: int, month: int) -> list[dict]:
    """One event per trading day in the month (weekdays excluding holidays)."""
    _, days_in_month = calendar.monthrange(year, month)
    events: list[dict] = []
    for d in range(1, days_in_month + 1):
        day = date(year, month, d)
        if day.weekday() >= 5:        # weekend
            continue
        if day in _HOLIDAY_SET:       # NSE holiday
            continue
        events.append({
            "event_type":   "FII_DII_RELEASE",
            "title":        "FII/DII Provisional Data Release",
            "event_date":   day,
            "time_ist":     "6:00 PM",
            "importance":   "LOW",
            "is_confirmed": True,
            "source":       "HARDCODED",
            "description":  "NSE publishes daily FII and DII buy/sell provisional data",
        })
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION E — IPO Fetcher
# ═══════════════════════════════════════════════════════════════════════════════

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_NSE_BASE = "https://www.nseindia.com"


async def fetch_upcoming_ipos() -> list[dict]:
    """Fetch upcoming IPOs from NSE; returns [] on any failure."""
    try:
        async with httpx.AsyncClient(headers=_BROWSER_HEADERS, timeout=15, follow_redirects=True) as client:
            await client.get(_NSE_BASE)
            await asyncio.sleep(1)
            urls = [
                f"{_NSE_BASE}/api/ipos-current-allotment",
                f"{_NSE_BASE}/api/ipos-upcoming",
            ]
            data: list[dict] = []
            for url in urls:
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        payload = r.json()
                        items = payload if isinstance(payload, list) else payload.get("data", [])
                        data.extend(items)
                        if items:
                            break
                except Exception:
                    continue

        events: list[dict] = []
        seen: set[str] = set()
        for ipo in data:
            company = ipo.get("companyName") or ipo.get("name", "Unknown IPO")
            if company in seen:
                continue
            seen.add(company)

            def _parse_date(val):
                if not val:
                    return None
                for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        from datetime import datetime as _dt
                        return _dt.strptime(val, fmt).date()
                    except ValueError:
                        continue
                return None

            open_dt    = _parse_date(ipo.get("openDate") or ipo.get("open"))
            close_dt   = _parse_date(ipo.get("closeDate") or ipo.get("close"))
            listing_dt = _parse_date(ipo.get("listingDate") or ipo.get("listDate"))
            price_band = ipo.get("priceBand") or ipo.get("issuePrice", "")
            lot_size   = ipo.get("lotSize") or ipo.get("minBidQty")
            issue_size = ipo.get("issueSize") or ipo.get("issueSizeCr")

            meta = {
                "issue_price_range": str(price_band),
                "lot_size":          lot_size,
                "issue_size_cr":     issue_size,
            }

            for ev_date, ev_title, ev_imp in [
                (open_dt,    f"{company} IPO Opens",   "MEDIUM"),
                (close_dt,   f"{company} IPO Closes",  "MEDIUM"),
                (listing_dt, f"{company} IPO Listing", "HIGH"),
            ]:
                if ev_date:
                    events.append({
                        "event_type":     "IPO",
                        "title":          ev_title,
                        "company_name":   company,
                        "event_date":     ev_date,
                        "importance":     ev_imp,
                        "source":         "NSE_API",
                        "is_confirmed":   True,
                        "event_metadata": meta,
                    })

        logger.info(f"[calendar] Fetched {len(events)} IPO events for {len(seen)} IPOs")
        return events

    except Exception as exc:
        logger.warning(f"[calendar] IPO fetch failed: {exc}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION F — Earnings Fetcher
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_earnings_calendar() -> list[dict]:
    """Pull earnings dates from yfinance for all NSE watchlist symbols."""
    from utils.config import settings
    import yfinance as yf

    events: list[dict] = []
    symbols = settings.nse_symbols + settings.nse_mid_symbols

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            cal = ticker.calendar
            if not cal:
                continue

            earnings_date = cal.get("Earnings Date")
            if not earnings_date:
                continue
            if isinstance(earnings_date, list):
                earnings_date = earnings_date[0]

            # May be a Timestamp or date
            if hasattr(earnings_date, "date"):
                earnings_date = earnings_date.date()

            company = ticker.info.get("longName", symbol)
            info    = ticker.info

            events.append({
                "event_type":   "EARNINGS",
                "title":        f"{company} — Quarterly Results",
                "symbol":       symbol,
                "company_name": company,
                "event_date":   earnings_date,
                "importance":   "HIGH",
                "source":       "YFINANCE",
                "is_confirmed": True,
                "event_metadata": {
                    "est_eps": info.get("forwardEps"),
                    "sector":  info.get("sector"),
                },
            })
        except Exception as exc:
            logger.debug(f"[calendar] No earnings for {symbol}: {exc}")

    logger.info(f"[calendar] Fetched {len(events)} earnings events")
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION G — Seed Database
# ═══════════════════════════════════════════════════════════════════════════════

async def seed_calendar_events(
    session: AsyncSession,
    months_ahead: int = 3,
) -> dict:
    """Generate and persist all calendar events for today + months_ahead months."""
    today = date.today()

    # Generate F&O expiry for current month + ahead
    expiry_events: list[dict] = []
    for i in range(months_ahead + 1):
        target = today + relativedelta(months=i)
        expiry_events += generate_fno_expiry_dates(target.year, target.month)

    # Fetch async sources in parallel
    ipos, earnings = await asyncio.gather(
        fetch_upcoming_ipos(),
        fetch_earnings_calendar(),
        return_exceptions=True,
    )
    if isinstance(ipos, Exception):
        logger.warning(f"[calendar] IPO fetch error: {ipos}")
        ipos = []
    if isinstance(earnings, Exception):
        logger.warning(f"[calendar] Earnings fetch error: {earnings}")
        earnings = []

    all_events: list[dict] = (
        expiry_events
        + get_rbi_mpc_events()
        + get_nse_holidays_2026()
        + list(ipos)
        + list(earnings)
    )

    # Delete only future events (keep historical record)
    await session.execute(
        delete(MarketEvent).where(MarketEvent.event_date >= today)
    )

    for ev in all_events:
        row = MarketEvent(**ev)
        session.add(row)

    await session.commit()

    by_type = dict(Counter(e["event_type"] for e in all_events))
    logger.info(f"[calendar] Seeded {len(all_events)} events: {by_type}")
    return {"total_inserted": len(all_events), "by_type": by_type}


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION H — Query Helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def get_events_for_range(
    session: AsyncSession,
    from_date: date,
    to_date: date,
    event_types: list[str] | None = None,
    symbol: str | None = None,
) -> list[MarketEvent]:
    from sqlalchemy import and_
    filters = [
        MarketEvent.event_date >= from_date,
        MarketEvent.event_date <= to_date,
    ]
    if event_types:
        filters.append(MarketEvent.event_type.in_(event_types))
    if symbol:
        filters.append(MarketEvent.symbol == symbol)

    result = await session.execute(
        select(MarketEvent)
        .where(and_(*filters))
        .order_by(MarketEvent.event_date.asc(), MarketEvent.importance.asc())
    )
    return list(result.scalars().all())


async def get_upcoming_events(
    session: AsyncSession,
    days: int = 30,
    event_types: list[str] | None = None,
) -> list[MarketEvent]:
    today   = date.today()
    to_date = today + timedelta(days=days)

    # Exclude noisy FII/DII release events by default
    exclude_types = {"FII_DII_RELEASE"}
    if event_types:
        effective_types = [t for t in event_types if t not in exclude_types]
    else:
        # All types except the excluded ones
        effective_types = None
        exclude_types_list = list(exclude_types)

    from sqlalchemy import and_, not_

    filters = [
        MarketEvent.event_date >= today,
        MarketEvent.event_date <= to_date,
    ]
    if event_types:
        filters.append(MarketEvent.event_type.in_(event_types))
    else:
        filters.append(MarketEvent.event_type.not_in(list(exclude_types)))

    result = await session.execute(
        select(MarketEvent)
        .where(and_(*filters))
        .order_by(MarketEvent.event_date.asc(), MarketEvent.importance.asc())
    )
    return list(result.scalars().all())


def get_events_by_date(events: list[MarketEvent]) -> dict[str, list]:
    """Group events by ISO date string for calendar grid rendering."""
    grouped: dict[str, list] = {}
    for ev in events:
        key = str(ev.event_date)
        grouped.setdefault(key, []).append(ev)
    return grouped


def _event_to_dict(ev: MarketEvent) -> dict:
    return {
        "id":             ev.id,
        "event_type":     ev.event_type,
        "title":          ev.title,
        "symbol":         ev.symbol,
        "company_name":   ev.company_name,
        "event_date":     str(ev.event_date),
        "start_date":     str(ev.start_date) if ev.start_date else None,
        "end_date":       str(ev.end_date) if ev.end_date else None,
        "time_ist":       ev.time_ist,
        "description":    ev.description,
        "importance":     ev.importance,
        "source":         ev.source,
        "metadata":       ev.event_metadata,
        "is_confirmed":   ev.is_confirmed,
    }
