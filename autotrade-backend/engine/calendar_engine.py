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
from sqlalchemy import delete, select, text
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

async def _get_full_nse_bse_universe(session: AsyncSession) -> list[str]:
    """Every real NSE + BSE equity tradingsymbol from kite_instruments, with
    the correct .NS/.BO suffix per row, bonds/T-bills excluded. Same query
    shape as tasks/india_tasks.py::_backfill_hub_1d_candles() (2026-07-28
    BSE-candle-coverage fix) -- reused here so "the full universe" means the
    same ~10,000 symbols in both places, not two different definitions.

    The `name NOT ILIKE 'GOI %'/'SDL %'` check only catches bonds whose name
    STARTS with that token. Confirmed live (2026-07-29, full-backfill script)
    that ~800 government/corporate bonds slip through with the token
    elsewhere in the name or missing entirely -- e.g. tradingsymbol
    '727MHSGS36' (name '727MHSGS36', a Maharashtra state security),
    '1018GOI26' (name '10.18% GOI 2026'), '733KRSDL54' (name '733KRSDL54').
    Each one made yfinance hang for a full 30s curl timeout instead of
    failing fast, burning ~15min per ~30 of them hit in sequence. No real
    NSE/BSE-listed operating company's tradingsymbol starts with a digit --
    that pattern is exclusive to bonds/NCDs/T-bills -- so excluding on that
    is a robust catch-all regardless of how the name field is formatted.
    """
    rows = (await session.execute(text("""
        SELECT tradingsymbol, segment
        FROM kite_instruments
        WHERE segment IN ('NSE', 'BSE') AND instrument_type = 'EQ'
          AND name != '' AND instrument_token > 0
          AND name NOT ILIKE 'GOI %' AND name NOT ILIKE 'SDL %'
          AND tradingsymbol !~ '^[0-9]'
        ORDER BY tradingsymbol
    """))).all()
    suffix = {"NSE": ".NS", "BSE": ".BO"}
    return [f"{sym}{suffix[seg]}" for sym, seg in rows]


# Sequential pacing for yfinance, NOT concurrency. This codebase already has
# a documented, empirically-measured finding for this exact API
# (tasks/india_tasks.py's backfill_hub_1d_candles-adjacent comment, 2026-07-06
# postmortem): yfinance sustains only ~0.2-0.25 symbols/sec, and pushing
# concurrency higher triggers Yahoo's opaque per-IP throttling, which makes
# throughput WORSE, not better -- unlike Kite's historical API, which has a
# documented, predictable 3 req/sec ceiling that a small concurrency pool
# safely stays under. A first version of this function used 10-way
# concurrency for the full-universe path and confirmed this the hard way
# live (2026-07-28): 8,258 of 10,197 symbols failed with "Too Many Requests"
# -- including the exact 4 companies (VBL, AMBUJACEM, TATACAP, CHOLAFIN) that
# motivated building this path in the first place, each of which resolved
# fine moments earlier under a plain sequential call. Fixed to sequential
# with a fixed delay matching the documented safe rate.
_YFINANCE_SAFE_DELAY_SEC = 4.0  # ~0.25 req/sec, matching the documented safe rate

# At the safe rate above, scanning the full ~10,197-symbol universe in one
# run would take ~11-12 hours -- not remotely fittable in one daily run. So
# "full_universe" scans a ROTATING DAILY SLICE instead: today's calendar date
# picks which 1/N of the universe gets scanned today (stateless -- no cursor
# to persist, just date.today().toordinal() % num_slices), sized so one
# day's slice finishes comfortably inside the celery task's time budget.
# Full coverage converges over one rotation cycle (~3 weeks); this is the
# same "idempotent, gradually-converging" philosophy already used for the
# BSE candle backfill (tasks.backfill_hub_1d_candles) elsewhere in this
# session's fixes -- earnings dates don't change minute to minute, so a
# multi-day convergence window is a fine trade for never re-triggering
# yfinance's throttling.
_DAILY_SLICE_BUDGET_SEC = 1800  # 30 min/day devoted to the full-universe scan


def _todays_universe_slice(all_symbols: list[str]) -> list[str]:
    slice_size = max(1, int(_DAILY_SLICE_BUDGET_SEC / _YFINANCE_SAFE_DELAY_SEC))
    num_slices = max(1, -(-len(all_symbols) // slice_size))  # ceil division
    idx = date.today().toordinal() % num_slices
    return all_symbols[idx * slice_size: (idx + 1) * slice_size]


async def _fetch_one_earnings_event(symbol: str, *, raise_on_error: bool = False) -> dict | None:
    """One symbol's yfinance calendar lookup, run off the event loop (yfinance
    is a blocking/sync library).

    raise_on_error (added 2026-07-28, for scripts/backfill_full_earnings_calendar.py):
    by default this swallows every failure (network error, rate-limit,
    genuinely-no-data) identically and returns None, which is fine for the
    daily-slice caller (a handful of misses in ~450 symbols isn't worth
    reacting to). A long overnight full-universe run needs to tell "yfinance
    is rate-limiting me" apart from "this ticker legitimately has no
    calendar data" so it can back off -- confirmed live that continuing to
    hammer yfinance every 4s through a rate-limit window, rather than
    pausing, does not self-recover (10197-symbol run: 0 successes for 43
    straight symbols once triggered, no sign of clearing). Set True to let
    the caller catch and inspect the exception itself.
    """
    import yfinance as yf

    def _blocking():
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if not cal:
            return None
        earnings_date = cal.get("Earnings Date")
        if not earnings_date:
            return None
        if isinstance(earnings_date, list):
            earnings_date = earnings_date[0]
        if hasattr(earnings_date, "date"):
            earnings_date = earnings_date.date()
        info = ticker.info
        company = info.get("longName", symbol)
        return {
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
        }

    try:
        return await asyncio.to_thread(_blocking)
    except Exception as exc:
        if raise_on_error:
            raise
        logger.debug(f"[calendar] No earnings for {symbol}: {exc}")
        return None


async def fetch_earnings_calendar(
    session: AsyncSession | None = None,
    *,
    full_universe: bool = False,
    slice_mode: bool = True,
) -> list[dict]:
    """Pull earnings dates from yfinance.

    Two modes (added 2026-07-28, after Varun Beverages/Ambuja Cements/Tata
    Capital/Cholamandalam Finance were all confirmed missing despite having
    real yfinance earnings dates -- the calendar only ever queried a 32-symbol
    hardcoded watchlist):

    - full_universe=False (default): settings.earnings_calendar_symbols, a
      curated ~100-name list. Sequential, fast (seconds -- no inter-request
      delay; small enough that yfinance doesn't throttle it), used by the
      API-triggered manual "Refresh Data" button (api/india.py) which needs
      to return within one HTTP request.
    - full_universe=True, slice_mode=True (default for this mode): today's
      rotating slice (see _todays_universe_slice) of every real NSE+BSE
      equity symbol from kite_instruments (~10,000 total, ~450/day), fetched
      strictly sequentially at the documented safe yfinance rate. Used by the
      daily celery reseed (tasks.seed_calendar_events).
    - full_universe=True, slice_mode=False: the ENTIRE universe in one pass,
      still strictly sequential at the same safe rate -- ~11-12 hours for
      ~10,000 symbols. Only for a deliberate one-time overnight catch-up run
      (see scripts/backfill_full_earnings_calendar.py), never the daily task
      -- a run this long has no business inside a bounded celery time_limit.

    `session` is required whenever full_universe=True (to query
    kite_instruments) and unused otherwise.
    """
    from utils.config import settings

    if full_universe:
        if session is None:
            raise ValueError("full_universe=True requires a DB session")
        full = await _get_full_nse_bse_universe(session)
        symbols = _todays_universe_slice(full) if slice_mode else full
        logger.info(f"[calendar] full_universe: scanning {len(symbols)} of {len(full)} total symbols "
                    f"(slice_mode={slice_mode})")
        events: list[dict] = []
        for i, sym in enumerate(symbols):
            ev = await _fetch_one_earnings_event(sym)
            if ev is not None:
                events.append(ev)
            # A full (non-sliced) scan takes ~11-12 hours -- log progress
            # periodically so an overnight run is actually observable instead
            # of going silent until the single "Fetched N events" line at the
            # very end.
            if not slice_mode and (i + 1) % 100 == 0:
                logger.info(f"[calendar] full scan progress: {i + 1}/{len(symbols)} symbols "
                            f"({len(events)} earnings dates found so far)")
            if i < len(symbols) - 1:
                await asyncio.sleep(_YFINANCE_SAFE_DELAY_SEC)
    else:
        symbols = settings.earnings_calendar_symbols
        results = await asyncio.gather(*[_fetch_one_earnings_event(s) for s in symbols])
        events = [r for r in results if r is not None]

    logger.info(f"[calendar] Fetched {len(events)} earnings events (full_universe={full_universe}, "
                f"scanned {len(symbols)} symbols)")
    return events


def _parse_nse_board_meeting_date(raw: str) -> date | None:
    from datetime import datetime as _dt
    try:
        return _dt.strptime(raw.strip(), "%d-%b-%Y").date()
    except (ValueError, AttributeError):
        return None


async def fetch_nse_board_meetings_calendar() -> list[dict]:
    """Confirmed near-term earnings dates straight from NSE's own board-meeting
    filings (added 2026-07-29, replacing the yfinance full-universe scan as
    the primary earnings-calendar source).

    In India, "earnings date" legally IS the board-meeting date: SEBI LODR
    requires listed companies to intimate the exchange of the board meeting
    at which quarterly results will be considered, days ahead of the meeting.
    NSE's /api/event-calendar aggregates every company's upcoming
    "Financial Results" board-meeting filing into ONE response -- no
    per-symbol looping, so none of the yfinance rate-limiting problems apply
    (confirmed live: 530 rows / ~519 companies in a single ~1s call, vs.
    yfinance's ~10,000 separate requests needed to cover the same universe,
    of which a large fraction get rate-limited on any sustained run).

    Tradeoff vs. yfinance: this only covers the NEAR TERM (rolling ~2-3 weeks
    ahead, since companies file on a rolling basis rather than announcing a
    board meeting months out) -- it cannot give a Q4FY27 date 8 months in
    advance the way yfinance's analyst-estimate field attempts to. That's an
    acceptable trade: near-term dates are exactly what a "what's reporting
    this week" trading calendar actually needs, and unlike yfinance's
    estimate these are the company's own confirmed filing, not a guess.

    Requires the `brotli` package (added to requirements.txt 2026-07-29) --
    NSE serves this endpoint brotli-encoded and httpx silently fails to
    decode it (returns garbage bytes, not an error) without that package
    installed, which is why this endpoint looked broken before it was
    diagnosed.
    """
    from engine.nse_crawler import _get_nse_session

    try:
        client = await _get_nse_session()
    except Exception as exc:
        logger.warning(f"[calendar] NSE board-meeting session init failed: {exc}")
        return []

    try:
        resp = await client.get("https://www.nseindia.com/api/event-calendar")
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:
        logger.warning(f"[calendar] NSE board-meeting fetch failed: {exc}")
        return []
    finally:
        await client.aclose()

    if not isinstance(rows, list):
        return []

    events: list[dict] = []
    for row in rows:
        symbol = (row.get("symbol") or "").strip()
        purpose = row.get("purpose") or ""
        if not symbol or "Financial Results" not in purpose:
            continue
        event_date = _parse_nse_board_meeting_date(row.get("date") or "")
        if event_date is None:
            continue
        company = row.get("company") or symbol
        events.append({
            "event_type":     "EARNINGS",
            "title":          f"{company} — Quarterly Results",
            "symbol":         f"{symbol}.NS",
            "company_name":   company,
            "event_date":     event_date,
            "importance":     "HIGH",
            "source":         "NSE_BOARD_MEETING",
            "is_confirmed":   True,
            "event_metadata": {"purpose": purpose, "bm_desc": row.get("bm_desc", "")},
        })

    logger.info(f"[calendar] NSE board-meeting calendar: {len(events)} confirmed earnings dates "
                f"from {len(rows)} total board-meeting filings")
    return events


async def fetch_nse_declared_results_today() -> list[dict]:
    """Results actually DECLARED today, as NSE files them live (added 2026-07-29).

    fetch_nse_board_meetings_calendar()'s /api/event-calendar is forward-only:
    it drops a company from its list the instant that company's meeting date
    arrives, so it can never show TODAY's earnings (confirmed live: queried
    on 29-Jul-2026, the earliest date returned was 30-Jul -- the ~100+
    companies, including Adani Ports, Colgate-Palmolive, CarTrade, actually
    reporting results ON THE 29th were invisible to it). This covers that gap
    from the other direction: NSE's own corporate-announcements feed, filtered
    to subject "Outcome of Board Meeting" (the filing a company makes the
    moment its board approves the quarter's results), gives the real
    as-it-happens list for today specifically.

    This result set only GROWS through the trading day as companies file
    (unlike the board-meeting feed, nothing rolls off), so re-fetching it on
    every seed_calendar_events() call (including the manual "Refresh Data"
    button) is safe to blanket-delete-and-reinsert same as any other source
    -- no special replace-by-symbol handling needed here, unlike
    fetch_nse_board_meetings_calendar().
    """
    from urllib.parse import quote

    from engine.nse_crawler import _get_nse_session

    try:
        client = await _get_nse_session()
    except Exception as exc:
        logger.warning(f"[calendar] NSE declared-results session init failed: {exc}")
        return []

    try:
        subject = quote("Outcome of Board Meeting")
        resp = await client.get(
            f"https://www.nseindia.com/api/corporate-announcements?index=equities&subject={subject}"
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning(f"[calendar] NSE declared-results fetch failed: {exc}")
        return []
    finally:
        await client.aclose()

    rows = data if isinstance(data, list) else data.get("data", [])
    if not isinstance(rows, list):
        return []

    today_str = date.today().strftime("%d-%b-%Y")
    events_by_symbol: dict[str, dict] = {}
    for row in rows:
        an_dt = row.get("an_dt") or ""
        if not an_dt.startswith(today_str):
            continue
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            continue
        company = row.get("sm_name") or symbol
        # Rows are sorted most-recent-first; a company can file more than one
        # "Outcome of Board Meeting" announcement in a day (amendments,
        # follow-ups) -- keep only the first (latest) one seen per symbol.
        if symbol in events_by_symbol:
            continue
        events_by_symbol[symbol] = {
            "event_type":     "EARNINGS",
            "title":          f"{company} — Quarterly Results (Declared)",
            "symbol":         f"{symbol}.NS",
            "company_name":   company,
            "event_date":     date.today(),
            "importance":     "HIGH",
            "source":         "NSE_RESULT_DECLARED",
            "is_confirmed":   True,
            "event_metadata": {"announced_at": an_dt, "desc": row.get("desc", "")},
        }

    events = list(events_by_symbol.values())
    logger.info(f"[calendar] NSE declared-results today: {len(events)} companies "
                f"(from {len(rows)} total board-meeting-outcome filings)")
    return events


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION G — Seed Database
# ═══════════════════════════════════════════════════════════════════════════════

async def seed_calendar_events(
    session: AsyncSession,
    months_ahead: int = 3,
    *,
    full_universe: bool = False,
    slice_mode: bool = True,
) -> dict:
    """Generate and persist all calendar events for today + months_ahead months.

    full_universe=True fetches earnings for every real NSE+BSE symbol
    (~10,000) instead of the curated ~100-name list -- see
    fetch_earnings_calendar()'s docstring. Only the daily celery reseed
    should pass this; the API-triggered manual refresh needs to stay fast.
    slice_mode=False (only meaningful with full_universe=True) scans the
    whole universe in one very long pass instead of one daily rotating
    slice -- see fetch_earnings_calendar()'s docstring; only
    scripts/backfill_full_earnings_calendar.py should pass this.
    """
    today = date.today()

    # Generate F&O expiry for current month + ahead
    expiry_events: list[dict] = []
    for i in range(months_ahead + 1):
        target = today + relativedelta(months=i)
        expiry_events += generate_fno_expiry_dates(target.year, target.month)

    # Fetch async sources in parallel
    ipos, earnings, board_meetings, declared_results = await asyncio.gather(
        fetch_upcoming_ipos(),
        fetch_earnings_calendar(session, full_universe=full_universe, slice_mode=slice_mode),
        fetch_nse_board_meetings_calendar(),
        fetch_nse_declared_results_today(),
        return_exceptions=True,
    )
    if isinstance(ipos, Exception):
        logger.warning(f"[calendar] IPO fetch error: {ipos}")
        ipos = []
    if isinstance(earnings, Exception):
        logger.warning(f"[calendar] Earnings fetch error: {earnings}")
        earnings = []
    if isinstance(board_meetings, Exception):
        logger.warning(f"[calendar] NSE board-meeting fetch error: {board_meetings}")
        board_meetings = []
    if isinstance(declared_results, Exception):
        logger.warning(f"[calendar] NSE declared-results fetch error: {declared_results}")
        declared_results = []

    # NSE board-meeting filings and same-day declared results are the
    # company's own CONFIRMED data; a yfinance analyst-estimate for the same
    # symbol is redundant (and sometimes wrong/stale) once either exists, so
    # it's dropped in favor of the confirmed one rather than showing both.
    confirmed_symbols = {ev["symbol"] for ev in board_meetings} | {ev["symbol"] for ev in declared_results}
    earnings = [ev for ev in earnings if ev["symbol"] not in confirmed_symbols]

    all_events: list[dict] = (
        expiry_events
        + get_rbi_mpc_events()
        + get_nse_holidays_2026()
        + list(ipos)
        + list(earnings)
        + list(board_meetings)
        + list(declared_results)
    )

    # Delete only future events (keep historical record). NSE_BOARD_MEETING
    # rows are excluded from this blanket wipe -- see below.
    await session.execute(
        delete(MarketEvent).where(
            MarketEvent.event_date >= today,
            MarketEvent.source != "NSE_BOARD_MEETING",
        )
    )

    # NSE_BOARD_MEETING replace-by-symbol, not blanket delete (2026-07-29 fix):
    # confirmed live that NSE's /api/event-calendar is a rolling ~3-week AHEAD
    # window that drops a company the instant its meeting date arrives (queried
    # on 29-Jul-2026, the earliest date returned was 30-Jul -- 29-Jul itself,
    # the day 100+ companies including Adani Enterprises/Asian Paints/Eicher
    # Motors/Dabur actually reported, was already gone from NSE's own feed).
    # The blanket "delete all future, reinsert from today's fetch" pattern
    # above works for every OTHER source because their fetches are a stable
    # or growing superset -- but applying it to NSE_BOARD_MEETING would wipe
    # each company's entry the moment its day arrives, since that day's fresh
    # NSE fetch no longer includes it, silently recreating this exact "empty
    # calendar today" bug every single day. Instead: only replace a symbol's
    # existing future NSE_BOARD_MEETING row if that symbol reappears in
    # TODAY's fresh fetch (i.e. a genuine reschedule) -- a symbol simply
    # aging out of NSE's forward window is never treated as a reason to
    # delete its already-captured entry.
    board_meeting_symbols = [ev["symbol"] for ev in board_meetings]
    if board_meeting_symbols:
        await session.execute(
            delete(MarketEvent).where(
                MarketEvent.source == "NSE_BOARD_MEETING",
                MarketEvent.event_date >= today,
                MarketEvent.symbol.in_(board_meeting_symbols),
            )
        )

    # A company whose result gets DECLARED today supersedes any earlier-
    # captured "upcoming board meeting" row for that same company (which
    # would otherwise sit un-deleted per the rule above, alongside the new
    # declared-result row, showing the same company/date twice).
    declared_symbols = [ev["symbol"] for ev in declared_results]
    if declared_symbols:
        await session.execute(
            delete(MarketEvent).where(
                MarketEvent.source == "NSE_BOARD_MEETING",
                MarketEvent.event_date >= today,
                MarketEvent.symbol.in_(declared_symbols),
            )
        )

    # Dedup against already-recorded PAST events (2026-07-28 fix): this reseed
    # runs daily, and fetch_earnings_calendar() keeps returning the SAME past
    # earnings date from yfinance until it rolls the ticker over to the next
    # quarter's estimate -- with no dedup here, every day's run re-inserted an
    # identical row for any event whose date had already passed, since the
    # delete above deliberately only clears future events. Confirmed live:
    # MARUTI/NESTLEIND/PIDILITIND/HINDUNILVR each had 20-27 duplicate rows,
    # every one of them past-dated; future-dated events had zero duplicates,
    # which is exactly what you'd expect from this exact mechanism. Past rows
    # are never deleted (preserves the historical record, per the comment
    # above) -- just no longer re-inserted once already present.
    past_keys_result = await session.execute(
        select(MarketEvent.event_type, MarketEvent.symbol, MarketEvent.event_date)
        .where(MarketEvent.event_date < today)
    )
    existing_past_keys = {(t, s, d) for t, s, d in past_keys_result.all()}

    inserted = 0
    for ev in all_events:
        key = (ev.get("event_type"), ev.get("symbol"), ev.get("event_date"))
        if ev.get("event_date") is not None and ev["event_date"] < today and key in existing_past_keys:
            continue
        session.add(MarketEvent(**ev))
        inserted += 1

    await session.commit()

    by_type = dict(Counter(e["event_type"] for e in all_events))
    logger.info(f"[calendar] Seeded {inserted} events (of {len(all_events)} fetched, "
                f"{len(all_events) - inserted} already-recorded past events skipped): {by_type}")
    return {"total_inserted": inserted, "by_type": by_type}


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
