# IPO data crawler — ipoalerts.in API + NSE subscription scraping.
# Cache TTL: 30 minutes. NSE two-step session pattern for subscription data.

import asyncio
import re
import time
from datetime import datetime, date
from typing import Any

import httpx

from utils.config import settings
from utils.logger import logger

# ── Constants ─────────────────────────────────────────────────────────────────

_CACHE_TTL = 1800  # 30 minutes

_NSE_BROWSER_HEADERS: dict[str, str] = {
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

# ── In-memory cache ────────────────────────────────────────────────────────────

IPO_CACHE: dict[str, Any] = {
    "data":        [],   # merged list from all statuses
    "by_slug":     {},   # slug → ipo dict
    "by_id":       {},   # id → ipo dict
    "last_refresh": 0.0,
}

# ── ipoalerts.in API helpers ───────────────────────────────────────────────────

def _api_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.ipoalerts_available:
        h["x-api-key"] = settings.IPOALERTS_API_KEY
    return h


async def fetch_ipos_by_status(status: str) -> list[dict]:
    """
    Fetch all IPOs from ipoalerts.in for a given status.

    Free-plan constraints:
      - limit=1 per request (hard cap)
      - only status=open is supported; others return 400
    So we paginate sequentially with a short delay to stay under the rate limit.
    """
    if not settings.ipoalerts_available:
        return []
    url    = f"{settings.IPOALERTS_BASE_URL}/ipos"
    params: dict[str, Any] = {"status": status, "limit": 1, "page": 1}
    if settings.IPOALERTS_INCLUDE_GMP:
        params["includeGmp"] = "true"

    all_items: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # First page — also tells us totalPages
            resp = await client.get(url, headers=_api_headers(), params=params)
            if resp.status_code == 400:
                # Free plan doesn't support this status — skip silently
                logger.debug("ipoalerts: status=%s not supported on free plan", status)
                return []
            resp.raise_for_status()
            raw        = resp.json()
            items      = raw.get("ipos") or raw.get("data") or (raw if isinstance(raw, list) else [])
            all_items.extend(items)

            total_pages = raw.get("meta", {}).get("totalPages", 1) if isinstance(raw, dict) else 1

            # Paginate remaining pages — stop on 429 (free plan bursts ~6 req/window)
            for page in range(2, total_pages + 1):
                await asyncio.sleep(1.0)
                params["page"] = page
                try:
                    r = await client.get(url, headers=_api_headers(), params=params)
                    if r.status_code == 429:
                        logger.info(
                            "ipoalerts: rate limit hit at page %d/%d — free plan cap reached, "
                            "got %d/%d IPOs. Cache will be refreshed next cycle.",
                            page, total_pages, len(all_items), raw.get("meta", {}).get("count", "?"),
                        )
                        break
                    if r.is_success:
                        page_items = r.json().get("ipos") or r.json().get("data") or []
                        all_items.extend(page_items)
                except Exception as exc:
                    logger.debug("ipoalerts page %d failed: %s", page, exc)

    except Exception as exc:
        logger.warning("fetch_ipos_by_status(%s) failed: %s", status, exc)

    logger.info("ipoalerts: fetched %d IPOs for status=%s", len(all_items), status)
    return all_items


async def fetch_single_ipo(identifier: str) -> dict | None:
    """Fetch a single IPO by id or slug from ipoalerts.in."""
    if not settings.ipoalerts_available:
        return None
    url = f"{settings.IPOALERTS_BASE_URL}/ipos/{identifier}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=_api_headers())
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            raw = resp.json()
            if isinstance(raw, dict) and "data" in raw:
                return raw["data"]
            return raw if isinstance(raw, dict) else None
    except Exception as exc:
        logger.warning("fetch_single_ipo(%s) failed: %s", identifier, exc)
        return None


async def fetch_ipos_from_nse() -> list[dict]:
    """
    Fallback: fetch live IPO data from NSE's public API (no key required).
    Uses the two-step browser-session pattern.
    """
    results: list[dict] = []
    nse_endpoints = [
        ("https://www.nseindia.com/api/ipo",               "open"),
        ("https://www.nseindia.com/api/ipo?category=sme",  "open"),
        ("https://www.nseindia.com/api/ipo-upcoming",       "upcoming"),
        ("https://www.nseindia.com/api/ipo-recent-listed",  "listed"),
    ]
    try:
        async with httpx.AsyncClient(
            headers=_NSE_BROWSER_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            await client.get("https://www.nseindia.com/")
            await asyncio.sleep(1.5)
            for url, default_status in nse_endpoints:
                try:
                    resp = await client.get(url, headers=_NSE_BROWSER_HEADERS)
                    if resp.status_code != 200:
                        continue
                    raw = resp.json()
                    items = raw if isinstance(raw, list) else raw.get("data", raw.get("ipoList", []))
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        item.setdefault("status", default_status)
                        results.append(item)
                except Exception as exc:
                    logger.debug("NSE IPO endpoint %s failed: %s", url, exc)
    except Exception as exc:
        logger.warning("fetch_ipos_from_nse failed: %s", exc)
    logger.info("NSE fallback: %d IPOs fetched", len(results))
    return results


async def refresh_ipo_cache() -> None:
    """Fetch all IPOs and rebuild cache.

    Free-plan ipoalerts.in only supports status=open (sequential pagination).
    All other statuses (upcoming, listed, announced) are attempted but silently
    return empty on the free plan — NSE is tried as a supplementary source for those.
    """
    merged: list[dict] = []
    if settings.ipoalerts_available:
        # open: paginated sequentially (free plan: limit=1 per request)
        open_ipos = await fetch_ipos_by_status("open")
        merged.extend(open_ipos)

        # upcoming / listed / announced — free plan returns 400, logged at DEBUG level
        for status in ("upcoming", "listed", "announced"):
            items = await fetch_ipos_by_status(status)
            merged.extend(items)

        # Supplement with NSE fallback for any statuses ipoalerts couldn't serve
        statuses_from_api = {i.get("status", "").lower() for i in merged}
        if not {"upcoming", "listed"}.issubset(statuses_from_api):
            nse_items = await fetch_ipos_from_nse()
            # Only add NSE items whose status isn't already covered
            for item in nse_items:
                if item.get("status", "").lower() not in statuses_from_api:
                    merged.append(item)
    else:
        # No API key — NSE public API only
        merged = await fetch_ipos_from_nse()

    # Deduplicate by id
    seen: set = set()
    unique: list[dict] = []
    for ipo in merged:
        ipo_id = ipo.get("id") or ipo.get("_id") or ""
        if ipo_id and ipo_id in seen:
            continue
        if ipo_id:
            seen.add(ipo_id)
        enriched = enrich_ipo_data(ipo)
        unique.append(enriched)

    if not unique and IPO_CACHE["data"]:
        # Rate-limited or total API failure — keep existing cache rather than wiping it.
        # Still bump last_refresh so we don't spin-retry immediately.
        IPO_CACHE["last_refresh"] = time.time()
        logger.warning("IPO refresh returned 0 results — keeping %d cached IPOs", len(IPO_CACHE["data"]))
        return

    IPO_CACHE["data"]         = unique
    IPO_CACHE["by_slug"]      = {i["slug"]: i for i in unique if i.get("slug")}
    IPO_CACHE["by_id"]        = {i.get("id", i.get("_id", "")): i for i in unique}
    IPO_CACHE["last_refresh"] = time.time()
    logger.info("IPO cache refreshed — %d IPOs loaded", len(unique))


async def get_ipo_cache() -> list[dict]:
    """Return cached IPO list, auto-refreshing if stale."""
    if time.time() - IPO_CACHE["last_refresh"] > _CACHE_TTL:
        await refresh_ipo_cache()
    return IPO_CACHE["data"]


# ── NSE subscription scraping ─────────────────────────────────────────────────

async def fetch_subscription_status(nse_info_url: str) -> dict:
    """
    Scrape QIB/NII/Retail/Total subscription data from the NSE IPO info page.
    Uses the two-step session pattern: GET home → wait → GET target.
    """
    result = {"qib": None, "nii": None, "retail": None, "total": None, "raw_text": ""}
    if not nse_info_url:
        return result
    try:
        async with httpx.AsyncClient(
            headers=_NSE_BROWSER_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            # Step 1 — prime the NSE session
            await client.get("https://www.nseindia.com/")
            await asyncio.sleep(1.5)
            # Step 2 — fetch IPO page
            resp = await client.get(nse_info_url, headers=_NSE_BROWSER_HEADERS)
            resp.raise_for_status()
            from bs4 import BeautifulSoup  # lazy — only when actually scraping
            soup = BeautifulSoup(resp.text, "html.parser")

            # Try structured table first
            for row in soup.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) < 2:
                    continue
                label = cells[0].lower()
                val   = _parse_subscription_value(cells[-1])
                if "qualified" in label or "qib" in label:
                    result["qib"] = val
                elif "non.instit" in label or "nii" in label or "hni" in label:
                    result["nii"] = val
                elif "retail" in label:
                    result["retail"] = val
                elif "total" in label:
                    result["total"] = val

            # Fallback: regex on raw text
            if result["total"] is None:
                text = soup.get_text(" ")
                result["raw_text"] = text[:500]
                m = re.search(r"Total.*?(\d+\.\d+)x", text, re.IGNORECASE)
                if m:
                    result["total"] = float(m.group(1))
    except Exception as exc:
        logger.warning("fetch_subscription_status(%s) failed: %s", nse_info_url, exc)
    return result


def _parse_subscription_value(text: str) -> float | None:
    text = text.strip().rstrip("x").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


# ── Data enrichment ────────────────────────────────────────────────────────────

def enrich_ipo_data(ipo: dict) -> dict:
    """Normalise and enrich raw ipoalerts.in response into a consistent shape."""
    ipo = dict(ipo)

    # ── Normalise ipoalerts.in camelCase field names ──────────────────────────
    # company name
    ipo.setdefault("company_name", ipo.get("name", ""))
    # dates: ipoalerts uses startDate/endDate/listingDate
    ipo.setdefault("open_date",    ipo.get("startDate",   ""))
    ipo.setdefault("close_date",   ipo.get("endDate",     ""))
    ipo.setdefault("listing_date", ipo.get("listingDate", ""))
    # issue size: "67cr", "120 Cr", etc.
    if not ipo.get("issue_size"):
        ipo["issue_size"] = ipo.get("issueSize", "")
    # type: ipoalerts uses "SME" / "IPO" (mainboard)
    if not ipo.get("ipo_type"):
        raw_t = (ipo.get("type") or "").upper()
        ipo["ipo_type"] = "SME" if raw_t == "SME" else "EQ"
    # logo
    ipo.setdefault("logo_url", ipo.get("logo", ""))
    # lot size: ipoalerts uses minQty
    if not ipo.get("lot_size"):
        ipo["lot_size"] = ipo.get("minQty") or ipo.get("lotSize") or None

    # Normalise id/slug
    ipo.setdefault("id",   str(ipo.get("_id", "")))
    ipo.setdefault("slug", ipo.get("slug") or _slugify(ipo.get("company_name", "")))

    # ── Price range ──────────────────────────────────────────────────────────
    price_band = ipo.get("price_band") or ipo.get("priceRange") or ""
    lower, upper = _parse_price_range(str(price_band))
    ipo["price_lower"] = lower
    ipo["price_upper"] = upper
    ipo["price_display"] = f"₹{lower}–{upper}" if lower and upper else (f"₹{upper}" if upper else "TBA")

    # ── Issue size ───────────────────────────────────────────────────────────
    raw_size = ipo.get("issue_size") or ipo.get("issue_size_cr") or 0
    try:
        ipo["issue_size_cr"] = float(
            str(raw_size).replace(",", "").replace("₹", "").replace("cr", "").replace("Cr", "").replace("CR", "").strip()
        )
    except (ValueError, TypeError):
        ipo["issue_size_cr"] = 0.0

    # ── Dates ────────────────────────────────────────────────────────────────
    open_date  = _parse_date(ipo.get("open_date")  or ipo.get("openDate")  or ipo.get("startDate"))
    close_date = _parse_date(ipo.get("close_date") or ipo.get("closeDate") or ipo.get("endDate"))
    today      = date.today()

    ipo["open_date_parsed"]  = open_date.isoformat()  if open_date  else None
    ipo["close_date_parsed"] = close_date.isoformat() if close_date else None

    if open_date and close_date:
        ipo["days_open"] = (close_date - open_date).days + 1
    else:
        ipo["days_open"] = None

    if close_date and close_date >= today:
        ipo["days_to_close"] = (close_date - today).days
    else:
        ipo["days_to_close"] = None

    # Allotment date — prefer schedule array from ipoalerts, else compute
    schedule = ipo.get("schedule") or []
    for event in schedule:
        label = (event.get("event") or "").lower()
        if "allotment" in label:
            ipo.setdefault("allotment_date", event.get("date", ""))
            break
    if not ipo.get("allotment_date") and close_date:
        allotment_days = 6
        allotment = close_date
        added = 0
        while added < allotment_days:
            allotment = _next_day(allotment)
            if allotment.weekday() < 5:
                added += 1
        ipo["allotment_date"] = allotment.isoformat()

    # ── GMP ──────────────────────────────────────────────────────────────────
    gmp = ipo.get("gmp") or ipo.get("grey_market_premium") or 0
    try:
        gmp_val = float(str(gmp).replace("₹", "").strip())
    except (ValueError, TypeError):
        gmp_val = 0.0
    ipo["gmp_inr"] = gmp_val

    if upper and gmp_val:
        ipo["gmp_pct"] = round((gmp_val / upper) * 100, 2)
        ipo["estimated_listing_price"] = round(upper + gmp_val, 2)
    else:
        ipo["gmp_pct"] = None
        ipo["estimated_listing_price"] = None

    # ── IPO type normalisation ───────────────────────────────────────────────
    raw_type = (ipo.get("ipo_type") or ipo.get("type") or "EQ").upper()
    if "SME" in raw_type:
        ipo["ipo_type"] = "SME"
    elif "DEBT" in raw_type:
        ipo["ipo_type"] = "DEBT"
    else:
        ipo["ipo_type"] = "EQ"

    # ── Status normalisation ─────────────────────────────────────────────────
    ipo["status"] = (ipo.get("status") or "upcoming").lower()

    return ipo


# ── Small helpers ─────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    name = re.sub(r"[^\w\s-]", "", name.lower())
    return re.sub(r"[\s_]+", "-", name).strip("-")


def _parse_price_range(text: str) -> tuple[float | None, float | None]:
    text = text.replace("₹", "").replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-–to]+\s*(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m2 = re.search(r"(\d+(?:\.\d+)?)", text)
    if m2:
        v = float(m2.group(1))
        return v, v
    return None, None


def _parse_date(val: Any) -> date | None:
    if not val:
        return None
    if isinstance(val, date):
        return val
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(val)[:20], fmt).date()
        except ValueError:
            continue
    return None


def _next_day(d: date) -> date:
    from datetime import timedelta
    return d + timedelta(days=1)
