"""Corporate Buyback Tracker API.

GET  /api/v1/buyback/             → list all active/upcoming buyback offers with live spread
POST /api/v1/buyback/refresh      → trigger a manual data + price refresh
"""
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db as get_async_db
from db.models import BuybackOffer
from utils.logger import logger

router = APIRouter(tags=["buyback"])

_NSE_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


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
        "opportunity":    opportunity,
        "last_refreshed": b.last_refreshed.isoformat() if b.last_refreshed else None,
    }


async def _purge_expired(db: AsyncSession) -> int:
    """Delete rows whose close_date is in the past — they are no longer actionable."""
    today = date.today()
    result = await db.execute(
        delete(BuybackOffer).where(
            BuybackOffer.close_date != None,
            BuybackOffer.close_date < today,
        )
    )
    await db.commit()
    if result.rowcount:
        logger.info(f"[buyback] purged {result.rowcount} expired rows (close_date < {today})")
    return result.rowcount


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_buybacks(db: AsyncSession = Depends(get_async_db)):
    # Always purge expired rows before returning
    await _purge_expired(db)

    rows = (await db.execute(
        select(BuybackOffer)
        .where(BuybackOffer.status.in_(["UPCOMING", "OPEN"]))
        .order_by(BuybackOffer.spread_pct.desc().nulls_last())
    )).scalars().all()

    # Enrich with live prices from WebSocket cache
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
    """Scrape NSE for current buyback data and upsert into DB."""
    # Expire closed offers first
    purged = await _purge_expired(db)
    added, updated = await _fetch_and_upsert(db)
    await db.commit()
    return {"status": "ok", "added": added, "updated": updated, "purged": purged}


# ── Data fetcher ──────────────────────────────────────────────────────────────

async def _fetch_and_upsert(db: AsyncSession) -> tuple[int, int]:
    offers = await _scrape_nse_buybacks()
    if not offers:
        logger.info("[buyback] No active buyback offers from NSE — nothing to upsert")
        return 0, 0

    added = updated = 0
    for o in offers:
        existing = (await db.execute(
            select(BuybackOffer).where(
                BuybackOffer.symbol == o["symbol"],
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
    """Fetch live buyback announcements from NSE corporate actions API.

    Tries two subjects: 'Buy Back of Shares' (tender offers) and 'Buyback'
    (open-market repurchases). Returns only offers whose close_date is in the
    future (or unknown). Returns an empty list if NSE is unreachable — never
    falls back to hardcoded data.
    """
    import asyncio
    import httpx
    from datetime import timedelta

    today = date.today()
    from_date = today - timedelta(days=7)
    to_date   = today + timedelta(days=180)

    offers: list[dict] = []

    try:
        async with httpx.AsyncClient(
            headers=_NSE_BROWSER_HEADERS,
            timeout=20,
            follow_redirects=True,
        ) as client:
            # Seed session cookie (required by NSE)
            await client.get("https://www.nseindia.com/")
            await asyncio.sleep(1.5)

            # NSE uses different subject strings for different announcement types
            subjects = [
                "Buy Back of Shares",   # Tender offer buybacks
                "Buyback",              # Open-market repurchases
            ]
            for subject in subjects:
                try:
                    r = await client.get(
                        "https://www.nseindia.com/api/corporate-announcements",
                        params={
                            "index":   "equities",
                            "subject": subject,
                            "from_date": from_date.strftime("%d-%m-%Y"),
                            "to_date":   to_date.strftime("%d-%m-%Y"),
                        },
                    )
                    if r.status_code != 200:
                        logger.warning(f"[buyback] NSE/{subject} → {r.status_code}")
                        continue
                    items = r.json()
                    if not isinstance(items, list):
                        items = items.get("data", [])
                    parsed = [_parse_nse_announcement(item) for item in items]
                    parsed = [p for p in parsed if p]
                    offers.extend(parsed)
                    logger.info(f"[buyback] NSE '{subject}' → {len(items)} items, {len(parsed)} valid")
                except Exception as e:
                    logger.warning(f"[buyback] NSE '{subject}' failed: {e}")

    except Exception as e:
        logger.warning(f"[buyback] NSE session failed: {e}")

    # Deduplicate by symbol+record_date
    seen: set[tuple] = set()
    unique: list[dict] = []
    for o in offers:
        key = (o["symbol"], o.get("record_date"))
        if key not in seen:
            seen.add(key)
            unique.append(o)

    # Only return offers that are not yet closed
    active = [o for o in unique if not _is_closed(o)]
    logger.info(f"[buyback] {len(unique)} total offers → {len(active)} still active")
    return active


def _is_closed(o: dict) -> bool:
    cd = o.get("close_date")
    if cd and isinstance(cd, date) and cd < date.today():
        return True
    return False


def _parse_nse_announcement(item: dict) -> dict | None:
    """Parse one NSE corporate-announcements row into BuybackOffer schema."""
    try:
        symbol_raw = (
            item.get("symbol") or item.get("Symbol") or
            item.get("sm_symbol") or ""
        ).strip()
        if not symbol_raw:
            return None
        symbol = symbol_raw + ".NS" if not symbol_raw.endswith((".NS", ".BO")) else symbol_raw

        # NSE announcement rows often lack price — skip if absent
        price_raw = (
            item.get("buybackPrice") or item.get("BuybackPrice") or
            item.get("offer_price") or item.get("OfferPrice") or
            item.get("price") or 0
        )
        buyback_price = 0.0
        try:
            buyback_price = float(str(price_raw).replace(",", ""))
        except (ValueError, TypeError):
            pass

        company = (
            item.get("companyName") or item.get("company") or
            item.get("sm_name") or symbol_raw
        )[:120]

        def _parse_date(keys: list[str]) -> date | None:
            for k in keys:
                raw = item.get(k)
                if raw:
                    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%b %d, %Y"):
                        try:
                            return datetime.strptime(str(raw).strip(), fmt).date()
                        except ValueError:
                            continue
            return None

        record_date = _parse_date(["recordDate", "record_date", "RecordDate", "rec_date"])
        open_date   = _parse_date(["openDate",   "open_date",   "OpenDate",   "offerOpenDate"])
        close_date  = _parse_date(["closeDate",  "close_date",  "CloseDate",  "offerCloseDate"])

        # Infer status from dates
        today = date.today()
        if close_date and close_date < today:
            return None  # skip already-closed offers
        elif open_date and open_date > today:
            status = "UPCOMING"
        elif close_date and close_date >= today:
            status = "OPEN"
        else:
            status = "UPCOMING"  # unknown dates → treat as upcoming

        total_size_raw = (
            item.get("totalAmount") or item.get("total_amount") or
            item.get("TotalAmount") or item.get("size")
        )
        total_size_cr = None
        if total_size_raw:
            try:
                val = float(str(total_size_raw).replace(",", ""))
                # If value looks like it's in rupees (not crores), convert
                total_size_cr = round(val / 1e7, 2) if val > 1e6 else round(val, 2)
            except Exception:
                pass

        buyback_type_raw = (
            item.get("buybackType") or item.get("type") or
            item.get("purpose") or "TENDER"
        ).upper()
        buyback_type = "OPEN_MARKET" if "OPEN" in buyback_type_raw or "MARKET" in buyback_type_raw else "TENDER"

        # For announcement-type rows without a price, we still record the symbol/dates
        # so the UI can show upcoming offers (price will be 0 until enriched)
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
