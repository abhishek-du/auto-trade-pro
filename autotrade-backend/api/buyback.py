"""Corporate Buyback Tracker API.

GET  /api/v1/buyback/             → list all active/upcoming buyback offers with live spread
POST /api/v1/buyback/refresh      → trigger a manual data + price refresh
"""
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db as get_async_db
from db.models import BuybackOffer
from utils.logger import logger

router = APIRouter(tags=["buyback"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(b: BuybackOffer) -> dict[str, Any]:
    spread = b.spread_pct
    if spread is None and b.market_price and b.market_price > 0:
        spread = round((b.buyback_price - b.market_price) / b.market_price * 100, 2)

    opportunity = False
    if b.market_price and b.market_price > 0 and b.buyback_price > 0:
        opportunity = b.buyback_price > b.market_price

    return {
        "id":             b.id,
        "symbol":         b.symbol,
        "company_name":   b.company_name,
        "buyback_price":  b.buyback_price,
        "buyback_type":   b.buyback_type,
        "total_size_cr":  b.total_size_cr,
        "record_date":    b.record_date.isoformat() if b.record_date else None,
        "open_date":      b.open_date.isoformat()   if b.open_date   else None,
        "close_date":     b.close_date.isoformat()  if b.close_date  else None,
        "status":         b.status,
        "market_price":   b.market_price,
        "spread_pct":     spread,
        "opportunity":    opportunity,   # True = market < buyback → guaranteed arbitrage
        "last_refreshed": b.last_refreshed.isoformat() if b.last_refreshed else None,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_buybacks(db: AsyncSession = Depends(get_async_db)):
    rows = (await db.execute(
        select(BuybackOffer)
        .where(BuybackOffer.status.in_(["UPCOMING", "OPEN"]))
        .order_by(BuybackOffer.spread_pct.desc().nulls_last())
    )).scalars().all()

    # Enrich with live prices
    try:
        from crawler.live_prices import PRICE_CACHE
        for b in rows:
            base = b.symbol.replace(".NS", "").replace(".BO", "")
            cached = PRICE_CACHE.get(b.symbol) or PRICE_CACHE.get(base)
            if isinstance(cached, dict):
                price = float(cached.get("price") or 0)
            elif cached:
                price = float(getattr(cached, "price", 0) or 0)
            else:
                price = 0
            if price > 0:
                b.market_price = round(price, 2)
                b.spread_pct   = round((b.buyback_price - price) / price * 100, 2)
                b.last_refreshed = datetime.utcnow()
    except Exception as e:
        logger.debug(f"[buyback] price enrichment skipped: {e}")

    return [_serialize(b) for b in rows]


@router.post("/refresh")
async def refresh_buybacks(db: AsyncSession = Depends(get_async_db)):
    """Scrape NSE/BSE for current buyback data and upsert into DB."""
    added, updated = await _fetch_and_upsert(db)
    await db.commit()
    return {"status": "ok", "added": added, "updated": updated}


# ── Data fetcher ──────────────────────────────────────────────────────────────

async def _fetch_and_upsert(db: AsyncSession) -> tuple[int, int]:
    """Fetch buyback data from NSE and upsert. Returns (added, updated)."""
    import httpx
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    offers = await _scrape_nse_buybacks()
    if not offers:
        logger.warning("[buyback] No offers fetched from NSE")
        return 0, 0

    added = updated = 0
    for o in offers:
        existing = (await db.execute(
            select(BuybackOffer).where(
                BuybackOffer.symbol      == o["symbol"],
                BuybackOffer.record_date == o.get("record_date"),
            )
        )).scalar_one_or_none()

        if existing:
            for k, v in o.items():
                if v is not None:
                    setattr(existing, k, v)
            updated += 1
        else:
            db.add(BuybackOffer(**o))
            added += 1

    logger.info(f"[buyback] upserted {added} new + {updated} updated offers")
    return added, updated


async def _scrape_nse_buybacks() -> list[dict]:
    """Fetch live buyback announcements from NSE corporate actions API."""
    import httpx
    from datetime import timedelta

    today = date.today()
    from_date = today - timedelta(days=30)
    to_date   = today + timedelta(days=180)

    headers = {
        "User-Agent":  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept":      "application/json",
        "Referer":     "https://www.nseindia.com/",
        "Accept-Language": "en-US,en;q=0.9",
    }

    offers: list[dict] = []

    try:
        async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
            # Seed session cookie
            await client.get("https://www.nseindia.com/")

            params = {
                "index":    "equities",
                "from_date": from_date.strftime("%d-%m-%Y"),
                "to_date":   to_date.strftime("%d-%m-%Y"),
            }
            r = await client.get(
                "https://www.nseindia.com/api/buyback-current",
                params=params,
            )
            if r.status_code != 200:
                logger.warning(f"[buyback] NSE API returned {r.status_code}")
                # Fall through to yfinance-seeded static list below
            else:
                data = r.json()
                items = data if isinstance(data, list) else data.get("data", [])
                for item in items:
                    o = _parse_nse_item(item)
                    if o:
                        offers.append(o)
                logger.info(f"[buyback] NSE returned {len(items)} items → {len(offers)} parsed")
    except Exception as e:
        logger.warning(f"[buyback] NSE scrape failed: {e}")

    # If NSE scrape got nothing, seed with known active buybacks (fallback)
    if not offers:
        offers = _known_buybacks_fallback()

    return offers


def _parse_nse_item(item: dict) -> dict | None:
    """Parse one NSE buyback API row into our BuybackOffer schema."""
    try:
        symbol_raw = (item.get("symbol") or item.get("Symbol") or "").strip()
        if not symbol_raw:
            return None
        symbol = symbol_raw + ".NS" if not symbol_raw.endswith(".NS") else symbol_raw

        price_raw = (
            item.get("buybackPrice") or item.get("BuybackPrice") or
            item.get("offer_price") or item.get("OfferPrice") or 0
        )
        buyback_price = float(str(price_raw).replace(",", "")) if price_raw else 0
        if buyback_price <= 0:
            return None

        company = (item.get("companyName") or item.get("company") or symbol_raw)[:120]

        def _parse_date(key_list):
            for k in key_list:
                raw = item.get(k)
                if raw:
                    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                        try:
                            return datetime.strptime(str(raw).strip(), fmt).date()
                        except ValueError:
                            continue
            return None

        record_date = _parse_date(["recordDate", "record_date", "RecordDate"])
        open_date   = _parse_date(["openDate", "open_date", "OpenDate", "offerOpenDate"])
        close_date  = _parse_date(["closeDate", "close_date", "CloseDate", "offerCloseDate"])

        status = "OPEN"
        if close_date and close_date < date.today():
            status = "CLOSED"
        elif open_date and open_date > date.today():
            status = "UPCOMING"

        total_size_raw = item.get("totalAmount") or item.get("total_amount") or item.get("TotalAmount")
        total_size_cr = None
        if total_size_raw:
            try:
                total_size_cr = float(str(total_size_raw).replace(",", "")) / 1e7
            except Exception:
                pass

        buyback_type_raw = (item.get("buybackType") or item.get("type") or "TENDER").upper()
        buyback_type = "OPEN_MARKET" if "OPEN" in buyback_type_raw else "TENDER"

        return {
            "symbol":        symbol,
            "company_name":  company,
            "buyback_price": round(buyback_price, 2),
            "buyback_type":  buyback_type,
            "total_size_cr": total_size_cr,
            "record_date":   record_date,
            "open_date":     open_date,
            "close_date":    close_date,
            "status":        status,
        }
    except Exception as e:
        logger.debug(f"[buyback] parse error: {e} | item={item}")
        return None


def _known_buybacks_fallback() -> list[dict]:
    """Hardcoded recent buybacks as seed data when NSE API is unavailable.
    Update this list whenever major buybacks are announced.
    """
    today = date.today()
    return [
        {
            "symbol":        "INFY.NS",
            "company_name":  "Infosys Limited",
            "buyback_price": 1750.0,
            "buyback_type":  "TENDER",
            "total_size_cr": 9200.0,
            "record_date":   None,
            "open_date":     None,
            "close_date":    None,
            "status":        "UPCOMING",
        },
        {
            "symbol":        "TCS.NS",
            "company_name":  "Tata Consultancy Services",
            "buyback_price": 4150.0,
            "buyback_type":  "TENDER",
            "total_size_cr": 17000.0,
            "record_date":   None,
            "open_date":     None,
            "close_date":    None,
            "status":        "UPCOMING",
        },
        {
            "symbol":        "WIPRO.NS",
            "company_name":  "Wipro Limited",
            "buyback_price": 500.0,
            "buyback_type":  "TENDER",
            "total_size_cr": 12000.0,
            "record_date":   None,
            "open_date":     None,
            "close_date":    None,
            "status":        "UPCOMING",
        },
        {
            "symbol":        "HCLTECH.NS",
            "company_name":  "HCL Technologies",
            "buyback_price": 1900.0,
            "buyback_type":  "TENDER",
            "total_size_cr": 2500.0,
            "record_date":   None,
            "open_date":     None,
            "close_date":    None,
            "status":        "UPCOMING",
        },
    ]
