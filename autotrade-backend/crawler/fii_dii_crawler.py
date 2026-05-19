"""NSE FII/DII institutional flow crawler for AutoTrade Pro.

NSE publishes daily Foreign Institutional Investor (FII) and Domestic
Institutional Investor (DII) buy/sell data at ~6 PM IST.  All monetary
values are stored in INR Crores.

NSE requires a browser-like session cookie before it will serve JSON from
any /api/* endpoint.  The two-step pattern (main page → API call) is
mandatory and is applied consistently throughout this module.

Custom httpx code is used here intentionally — the endpoint is a simple
JSON GET, and owning the 30-line fetcher means a URL or header change is
fixed in seconds rather than waiting for a library release.

Public API
----------
fetch_fii_dii_data(session?)    -> dict
calculate_fii_dii_score(session) -> float
save_fii_dii_to_db(data, session)
get_fii_sentiment_label(session) -> str
"""

from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FIIDIIFlow
from utils.config import settings
from utils.logger import logger

# ── NSE endpoint and browser impersonation ────────────────────────────────────

_NSE_HOME = "https://www.nseindia.com"
_FIIDII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

# NSE blocks non-browser User-Agents. These headers mimic a Chrome request and
# are required to receive a valid session cookie from the home-page prefetch.
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
    "sec-fetch-dest":  "empty",
    "sec-fetch-mode":  "cors",
    "sec-fetch-site":  "same-origin",
}

# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_float(value, default: float = 0.0) -> float:
    """Safe float conversion; strips commas and 'Cr' suffix."""
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").replace("Cr", "").strip())
    except (ValueError, TypeError):
        return default


def _first(row: dict, *keys: str, default=None):
    """Return the first non-None value found under any of the given key names.

    Falls back to a case-and-separator-insensitive match so the function
    survives minor NSE API field-name variations between releases.
    """
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    lower_map = {
        k.lower().replace(" ", "").replace("_", ""): v
        for k, v in row.items()
    }
    for key in keys:
        norm = key.lower().replace(" ", "").replace("_", "")
        if norm in lower_map and lower_map[norm] not in (None, ""):
            return lower_map[norm]
    return default


def _parse_date(value) -> datetime.date:
    """Parse a date string from the NSE API into a Python date object."""
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.datetime):
        return value.date()
    if value:
        raw = str(value).strip()
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y"):
            try:
                return datetime.datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return datetime.datetime.now(ZoneInfo(settings.IST_TIMEZONE)).date()


def _market_direction(fii_net: float, dii_net: float) -> str:
    """Classify the market direction from combined institutional net flow."""
    combined = fii_net + dii_net
    if combined > 0:
        return "BULLISH"
    if combined < 0:
        return "BEARISH"
    return "NEUTRAL"


def _extract_rows(payload) -> list[dict]:
    """Normalise the NSE API response to a flat list of row dicts.

    NSE has returned different shapes over the years:
      - A bare list of row dicts (most common as of 2026).
      - A dict with a 'data' key containing the list.
      - A dict with a 'fiidiiTradeReact' or 'records' key.
      - A flat single-row dict (edge case).
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "fiidiiTradeReact", "records", "table"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    # Flat dict treated as a single row
    return [payload]


def _parse_fii_dii_payload(payload) -> dict:
    """Extract FII/DII flow data from the raw NSE JSON payload.

    Tolerates NSE's inconsistent field naming:
      - 'buyValue' vs 'buy_value' vs 'grossBuyValue'
      - 'FII' vs 'FPI' for the foreign investor category
      - dates as '10-May-2026', '10-05-2026', or '2026-05-10'

    Raises ValueError when the payload contains no parseable rows.
    """
    rows = _extract_rows(payload)
    if not rows:
        raise ValueError("NSE FII/DII payload contained no parseable rows")

    parsed_date = None
    fii_buy = fii_sell = fii_net = 0.0
    dii_buy = dii_sell = dii_net = 0.0

    for row in rows:
        category = str(
            _first(row, "category", "cat", "name", "investorCategory",
                   "investor_category", default="")
        ).upper()

        # Extract trade date from whichever field is present
        date_val = _first(row, "date", "tradeDate", "asOnDate", "trade_date",
                          "Date", "tradedate")
        if date_val and parsed_date is None:
            parsed_date = _parse_date(date_val)

        gross_buy  = _to_float(_first(row, "buyValue",  "buy_value",  "grossBuyValue",
                                      "grossBuy",  "gross_buy",  "buy",  "buyAmt"))
        gross_sell = _to_float(_first(row, "sellValue", "sell_value", "grossSellValue",
                                      "grossSell", "gross_sell", "sell", "sellAmt"))
        net        = _to_float(_first(row, "netValue",  "net_value",  "netBuyValue",
                                      "net",       "netAmt",     "netBuy"))

        # Derive net from gross when the API omits the net field
        if net == 0.0 and (gross_buy or gross_sell):
            net = gross_buy - gross_sell

        if "FII" in category or "FPI" in category:
            fii_buy, fii_sell, fii_net = gross_buy, gross_sell, net
        elif "DII" in category:
            dii_buy, dii_sell, dii_net = gross_buy, gross_sell, net

    if parsed_date is None:
        parsed_date = datetime.datetime.now(ZoneInfo(settings.IST_TIMEZONE)).date()

    return {
        "date":             parsed_date,
        "fii_net_buy":      fii_net,
        "dii_net_buy":      dii_net,
        "fii_gross_buy":    fii_buy,
        "fii_gross_sell":   fii_sell,
        "dii_gross_buy":    dii_buy,
        "dii_gross_sell":   dii_sell,
        "market_direction": _market_direction(fii_net, dii_net),
    }


def _empty_flow(today: datetime.date) -> dict:
    return {
        "date":             today,
        "fii_net_buy":      0.0,
        "dii_net_buy":      0.0,
        "fii_gross_buy":    0.0,
        "fii_gross_sell":   0.0,
        "dii_gross_buy":    0.0,
        "dii_gross_sell":   0.0,
        "market_direction": "NEUTRAL",
    }


# ── DB fallback helper ────────────────────────────────────────────────────────

async def _last_flow_from_db(session: AsyncSession | None) -> dict:
    """Return the most recent FIIDIIFlow row from the DB.

    Accepts an optional session to reuse the caller's transaction.
    When no session is provided, opens a fresh one via AsyncSessionLocal.
    Both paths handle the case where the table is empty.
    """
    today = datetime.datetime.now(ZoneInfo(settings.IST_TIMEZONE)).date()

    async def _query(s: AsyncSession) -> dict:
        result = await s.execute(
            select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return _empty_flow(today)
        return {
            "date":             row.date,
            "fii_net_buy":      row.fii_net_buy,
            "dii_net_buy":      row.dii_net_buy,
            "fii_gross_buy":    row.fii_gross_buy,
            "fii_gross_sell":   row.fii_gross_sell,
            "dii_gross_buy":    row.dii_gross_buy,
            "dii_gross_sell":   row.dii_gross_sell,
            "market_direction": row.market_direction,
        }

    if session is not None:
        return await _query(session)

    # No session provided — open a fresh one (FastAPI context / ad-hoc calls)
    from db.database import AsyncSessionLocal  # noqa: PLC0415 — lazy import avoids circular dep

    async with AsyncSessionLocal() as s:
        return await _query(s)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Fetch
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_fii_dii_data(session: AsyncSession | None = None) -> dict:
    """Fetch today's FII/DII institutional flow from NSE.

    Two-step request pattern (required by NSE):
      1. GET the NSE homepage to acquire a session cookie.
      2. GET the FII/DII JSON API endpoint within the same client session.

    Falls back to the most recently stored DB row when:
      - The HTTP request fails for any reason.
      - NSE returns a non-200 status code.
      - The response JSON cannot be parsed.

    Parameters
    ----------
    session : Optional SQLAlchemy async session.  When provided, the DB
              fallback reuses it (safe in both FastAPI and Celery contexts).
              When absent, the fallback opens its own ``AsyncSessionLocal`` session.

    Returns
    -------
    dict with keys: date, fii_net_buy, dii_net_buy, fii_gross_buy,
    fii_gross_sell, dii_gross_buy, dii_gross_sell, market_direction.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            # Step 1 — acquire NSE session cookie
            await client.get(_NSE_HOME, headers=BROWSER_HEADERS)
            await asyncio.sleep(1)   # brief pause; avoids bot-detection 429s

            # Step 2 — call the actual JSON API
            response = await client.get(_FIIDII_URL, headers=BROWSER_HEADERS)

        if response.status_code != 200:
            raise ValueError(
                f"NSE returned HTTP {response.status_code} for FII/DII endpoint"
            )

        data = _parse_fii_dii_payload(response.json())

    except Exception as exc:
        logger.warning(f"FII/DII fetch failed: {exc}; using last stored flow")
        data = await _last_flow_from_db(session)

    logger.info(
        f"FII net: {data['fii_net_buy']:+,.2f} Cr  │  "
        f"DII net: {data['dii_net_buy']:+,.2f} Cr  │  "
        f"Direction: {data['market_direction']}  │  "
        f"Date: {data['date']}"
    )
    return data


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Score
# ═══════════════════════════════════════════════════════════════════════════════

def _fii_base_points(fii_net: float) -> int:
    """Convert one day's FII net flow to a score contribution."""
    if   fii_net  >  3000: return  30
    elif fii_net  >= 1000: return  20
    elif fii_net  >     0: return  10
    elif fii_net  >= -1000: return -10
    elif fii_net  >= -3000: return -20
    else:                   return -30


async def calculate_fii_dii_score(session: AsyncSession) -> float:
    """Score the last 5 days of institutional flow on a -100 to +100 scale.

    Scoring rules
    -------------
    Base score (today's FII net buy):
        > +3000 Cr  →  +30 pts
        +1000–3000  →  +20 pts
        0–1000      →  +10 pts
        0 to -1000  →  -10 pts
        -1000–-3000 →  -20 pts
        < -3000     →  -30 pts

    Bonus: FII selling while DII buying  →  +10 pts  (support / accumulation)
    Bonus: 5-day cumulative FII flow > 0 →  +10 pts  (sustained inflow trend)

    Result is clamped to [-100, +100].
    """
    result = await session.execute(
        select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(5)
    )
    rows = list(result.scalars().all())

    if not rows:
        return 0.0

    latest = rows[0]
    score  = _fii_base_points(latest.fii_net_buy)

    # Bonus: FII distributing while DII accumulating → market finding support
    if latest.fii_net_buy < 0 and latest.dii_net_buy > 0:
        score += 10

    # Bonus: sustained 5-day net positive inflow from FII
    if len(rows) >= 5 and sum(r.fii_net_buy for r in rows) > 0:
        score += 10

    clamped = float(max(-100, min(100, score)))
    logger.debug(
        f"FII/DII score: {clamped:+.0f}  "
        f"(base={_fii_base_points(latest.fii_net_buy)},  "
        f"fii={latest.fii_net_buy:+,.0f}Cr,  "
        f"dii={latest.dii_net_buy:+,.0f}Cr)"
    )
    return clamped


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Persist
# ═══════════════════════════════════════════════════════════════════════════════

async def save_fii_dii_to_db(data: dict, session: AsyncSession) -> FIIDIIFlow:
    """Upsert one FII/DII flow record, keyed on date.

    Uses INSERT … ON CONFLICT (date) DO UPDATE so calling this function
    multiple times on the same day safely refreshes the row values.
    ``created_at`` is set only on INSERT — it is not overwritten on update.

    Returns the persisted FIIDIIFlow ORM row.
    """
    flow_date = _parse_date(data.get("date"))

    insert_values = {
        "date":             flow_date,
        "fii_net_buy":      _to_float(data.get("fii_net_buy")),
        "dii_net_buy":      _to_float(data.get("dii_net_buy")),
        "fii_gross_buy":    _to_float(data.get("fii_gross_buy")),
        "fii_gross_sell":   _to_float(data.get("fii_gross_sell")),
        "dii_gross_buy":    _to_float(data.get("dii_gross_buy")),
        "dii_gross_sell":   _to_float(data.get("dii_gross_sell")),
        "market_direction": data.get("market_direction") or "NEUTRAL",
    }

    # Only update the data columns on conflict — never touch created_at
    update_values = {k: v for k, v in insert_values.items() if k != "date"}

    stmt = (
        pg_insert(FIIDIIFlow)
        .values(insert_values)
        .on_conflict_do_update(
            index_elements=["date"],
            set_=update_values,
        )
    )
    await session.execute(stmt)
    await session.flush()

    result = await session.execute(
        select(FIIDIIFlow).where(FIIDIIFlow.date == flow_date)
    )
    row = result.scalar_one()

    logger.info(
        f"Saved FII/DII  date={flow_date}  "
        f"fii={insert_values['fii_net_buy']:+,.2f}Cr  "
        f"dii={insert_values['dii_net_buy']:+,.2f}Cr  "
        f"direction={insert_values['market_direction']}"
    )
    return row


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Sentiment label
# ═══════════════════════════════════════════════════════════════════════════════

async def get_fii_sentiment_label(session: AsyncSession) -> str:
    """Return a human-readable FII sentiment label from the 3-day average.

    Thresholds
    ----------
    avg > +3000 Cr  → 'STRONG_BUY'
    avg > +1000 Cr  → 'BUY'
    avg < -3000 Cr  → 'STRONG_SELL'
    avg < -1000 Cr  → 'SELL'
    else            → 'NEUTRAL'
    """
    result = await session.execute(
        select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(3)
    )
    rows = list(result.scalars().all())

    if not rows:
        return "NEUTRAL"

    avg = sum(r.fii_net_buy for r in rows) / len(rows)

    if   avg >  3000: label = "STRONG_BUY"
    elif avg >  1000: label = "BUY"
    elif avg < -3000: label = "STRONG_SELL"
    elif avg < -1000: label = "SELL"
    else:             label = "NEUTRAL"

    logger.debug(f"FII sentiment label: {label}  (3-day avg={avg:+,.0f} Cr)")
    return label
