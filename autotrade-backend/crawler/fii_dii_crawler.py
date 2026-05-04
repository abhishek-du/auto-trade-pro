"""NSE FII/DII institutional flow crawler.

Values are stored in INR Crores. NSE requires a browser-like session cookie
before it serves JSON API endpoints, so every request first opens the main page.
"""

from __future__ import annotations

import asyncio
import datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import FIIDIIFlow
from utils.config import settings
from utils.logger import logger

FIIDII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", "").replace("Cr", "").strip())
    except Exception:
        return default


def _first(row: dict, names: tuple[str, ...], default=None):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    lower_map = {str(k).lower().replace(" ", "").replace("_", ""): v for k, v in row.items()}
    for name in names:
        key = name.lower().replace(" ", "").replace("_", "")
        if key in lower_map and lower_map[key] not in (None, ""):
            return lower_map[key]
    return default


def _parse_date(value) -> datetime.date:
    if isinstance(value, datetime.date):
        return value
    if value:
        raw = str(value).strip()
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y"):
            try:
                return datetime.datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return datetime.datetime.now(ZoneInfo(settings.IST_TIMEZONE)).date()


def _market_direction(fii_net: float, dii_net: float) -> str:
    combined = fii_net + dii_net
    if combined > 0:
        return "BULLISH"
    if combined < 0:
        return "BEARISH"
    return "NEUTRAL"


def _flow_row_to_dict(row: FIIDIIFlow) -> dict:
    return {
        "date": row.date,
        "fii_net_buy": row.fii_net_buy,
        "dii_net_buy": row.dii_net_buy,
        "fii_gross_buy": row.fii_gross_buy,
        "fii_gross_sell": row.fii_gross_sell,
        "dii_gross_buy": row.dii_gross_buy,
        "dii_gross_sell": row.dii_gross_sell,
        "market_direction": row.market_direction,
    }


def _payload_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "fiidiiTradeReact", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return [payload]


def _parse_fii_dii_payload(payload) -> dict:
    rows = _payload_rows(payload)
    if not rows:
        raise ValueError("NSE FII/DII response had no rows")

    parsed_date = None
    fii_buy = fii_sell = fii_net = 0.0
    dii_buy = dii_sell = dii_net = 0.0

    for row in rows:
        category = str(_first(row, ("category", "cat", "name", "investorCategory"), "")).upper()
        date_value = _first(row, ("date", "tradeDate", "asOnDate", "trade_date"))
        if date_value and parsed_date is None:
            parsed_date = _parse_date(date_value)

        gross_buy = _to_float(_first(row, ("buyValue", "buy_value", "grossBuyValue", "grossBuy", "buy")))
        gross_sell = _to_float(_first(row, ("sellValue", "sell_value", "grossSellValue", "grossSell", "sell")))
        net_value = _to_float(_first(row, ("netValue", "net_value", "netBuyValue", "net")))
        if net_value == 0.0 and (gross_buy or gross_sell):
            net_value = gross_buy - gross_sell

        if "FII" in category or "FPI" in category:
            fii_buy = gross_buy
            fii_sell = gross_sell
            fii_net = net_value
        elif "DII" in category:
            dii_buy = gross_buy
            dii_sell = gross_sell
            dii_net = net_value

    if parsed_date is None:
        parsed_date = datetime.datetime.now(ZoneInfo(settings.IST_TIMEZONE)).date()

    return {
        "date": parsed_date,
        "fii_net_buy": fii_net,
        "dii_net_buy": dii_net,
        "fii_gross_buy": fii_buy,
        "fii_gross_sell": fii_sell,
        "dii_gross_buy": dii_buy,
        "dii_gross_sell": dii_sell,
        "market_direction": _market_direction(fii_net, dii_net),
    }


async def _last_flow_from_db() -> dict:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            today = datetime.datetime.now(ZoneInfo(settings.IST_TIMEZONE)).date()
            return {
                "date": today,
                "fii_net_buy": 0.0,
                "dii_net_buy": 0.0,
                "fii_gross_buy": 0.0,
                "fii_gross_sell": 0.0,
                "dii_gross_buy": 0.0,
                "dii_gross_sell": 0.0,
                "market_direction": "NEUTRAL",
            }
        return _flow_row_to_dict(row)


async def fetch_fii_dii_data() -> dict:
    """Fetch latest NSE FII/DII flows, falling back to the last DB row on failure."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.get("https://www.nseindia.com", headers=BROWSER_HEADERS)
            await asyncio.sleep(1)
            response = await client.get(FIIDII_URL, headers=BROWSER_HEADERS)
            if response.status_code != 200:
                raise ValueError(f"NSE returned {response.status_code}")

        data = _parse_fii_dii_payload(response.json())
    except Exception as exc:
        logger.warning(f"FII/DII fetch failed: {exc}; using last stored flow")
        data = await _last_flow_from_db()

    logger.info(f"FII net: {data['fii_net_buy']:.2f} Cr | DII net: {data['dii_net_buy']:.2f} Cr")
    return data


def _fii_points(value: float) -> int:
    if value > 3000:
        return 30
    if value >= 1000:
        return 20
    if value > 0:
        return 10
    if value < -3000:
        return -30
    if value <= -1000:
        return -20
    if value < 0:
        return -10
    return 0


async def calculate_fii_dii_score(session: AsyncSession) -> float:
    """Score recent institutional flow impact on a -100 to +100 scale."""
    result = await session.execute(
        select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(5)
    )
    rows = list(result.scalars().all())
    if not rows:
        return 0.0

    latest = rows[0]
    score = _fii_points(latest.fii_net_buy)

    if latest.fii_net_buy < 0 and latest.dii_net_buy > 0:
        score += 10

    if len(rows) >= 5 and sum(row.fii_net_buy for row in rows) > 0:
        score += 10

    return float(max(-100, min(100, score)))


async def save_fii_dii_to_db(data: dict, session: AsyncSession) -> FIIDIIFlow:
    """Upsert one FII/DII flow row by date."""
    flow_date = _parse_date(data.get("date"))
    values = {
        "date": flow_date,
        "fii_net_buy": _to_float(data.get("fii_net_buy")),
        "dii_net_buy": _to_float(data.get("dii_net_buy")),
        "fii_gross_buy": _to_float(data.get("fii_gross_buy")),
        "fii_gross_sell": _to_float(data.get("fii_gross_sell")),
        "dii_gross_buy": _to_float(data.get("dii_gross_buy")),
        "dii_gross_sell": _to_float(data.get("dii_gross_sell")),
        "market_direction": data.get("market_direction") or "NEUTRAL",
    }

    stmt = pg_insert(FIIDIIFlow).values(values)
    update_values = {
        key: value
        for key, value in values.items()
        if key != "date"
    }
    update_values["created_at"] = datetime.datetime.utcnow()
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["date"],
            set_=update_values,
        )
    )
    await session.flush()

    result = await session.execute(select(FIIDIIFlow).where(FIIDIIFlow.date == flow_date))
    return result.scalar_one()


async def get_fii_sentiment_label(session: AsyncSession) -> str:
    """Return a sentiment label from the 3-day average FII flow."""
    result = await session.execute(
        select(FIIDIIFlow).order_by(desc(FIIDIIFlow.date)).limit(3)
    )
    rows = list(result.scalars().all())
    if not rows:
        return "NEUTRAL"

    avg = sum(row.fii_net_buy for row in rows) / len(rows)
    if avg > 3000:
        return "STRONG_BUY"
    if avg > 1000:
        return "BUY"
    if avg < -3000:
        return "STRONG_SELL"
    if avg < -1000:
        return "SELL"
    return "NEUTRAL"
