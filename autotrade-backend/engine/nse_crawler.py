"""NSE India data crawler.

Fetches rich live and historical data from the NSE public JSON API.
NSE requires a real browser session (cookies obtained by hitting the
homepage first).

Data returned per symbol:
  - Quote: LTP, open, high, low, close, prev close, 52w H/L, circuit limits
  - Trade info: total traded qty, delivery qty/%, VWAP, 5-day avg volume
  - Corporate actions: dividends, bonuses, splits, rights (last 3 years)
  - Financial results: last 8 quarters of P&L from NSE filings
  - Board meetings: recent/upcoming
  - Security info: ISIN, face value, market lot, listing date
  - Index membership: Nifty 50, Nifty 500, etc.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from utils.logger import logger

_BASE       = "https://www.nseindia.com"
_TIMEOUT    = 15.0
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent":      _BROWSER_UA,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
    "DNT":             "1",
    "X-Requested-With": "XMLHttpRequest",
}


async def _get_nse_session() -> httpx.AsyncClient:
    """Return a client with valid NSE cookies (obtained by hitting the homepage)."""
    client = httpx.AsyncClient(
        headers=_HEADERS,
        timeout=_TIMEOUT,
        follow_redirects=True,
    )
    try:
        # Hit the homepage to obtain session cookies
        await client.get(f"{_BASE}/", headers={
            **_HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        await asyncio.sleep(1)  # mimic human delay
        # Hit the equity page to get a stock-page cookie
    except Exception as exc:
        logger.debug(f"[nse_crawler] session init warning: {exc}")
    return client


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        s = re.sub(r"[,₹\s%]", "", str(v))
        return float(s) if s not in ("", "-", "NA", "N/A") else None
    except (ValueError, TypeError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


# ── API fetchers ──────────────────────────────────────────────────────────────

async def _api_get(client: httpx.AsyncClient, path: str) -> dict | list | None:
    url = f"{_BASE}{path}"
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.debug(f"[nse_crawler] HTTP {exc.response.status_code}: {url}")
    except Exception as exc:
        logger.debug(f"[nse_crawler] {url}: {exc}")
    return None


# ── Data parsers ──────────────────────────────────────────────────────────────

def _parse_quote(data: dict) -> dict:
    """Parse /api/quote-equity?symbol=X response."""
    if not data:
        return {}

    info  = data.get("info", {}) or {}
    price = data.get("priceInfo", {}) or {}
    trade = data.get("industryInfo", {}) or {}
    meta  = data.get("metadata", {}) or {}
    sec   = data.get("securityInfo", {}) or {}
    premarket = data.get("preOpenMarket", {}) or {}

    ltp      = _to_float(price.get("lastPrice"))
    prev_c   = _to_float(price.get("previousClose"))
    open_p   = _to_float(price.get("open"))
    day_high = _to_float(price.get("intraDayHighLow", {}).get("max"))
    day_low  = _to_float(price.get("intraDayHighLow", {}).get("min"))
    week52h  = _to_float(price.get("weekHighLow", {}).get("max"))
    week52l  = _to_float(price.get("weekHighLow", {}).get("min"))
    vwap     = _to_float(price.get("vwap"))

    pct_chg = None
    if ltp and prev_c and prev_c > 0:
        pct_chg = round((ltp - prev_c) / prev_c * 100, 2)

    circuit = price.get("pPriceBand", {}) or {}
    upper_c = _to_float(circuit.get("upper"))
    lower_c = _to_float(circuit.get("lower"))

    return {
        "symbol":           info.get("symbol", ""),
        "company_name":     info.get("companyName", ""),
        "isin":             info.get("isin", ""),
        "industry":         info.get("industry", ""),
        "series":           info.get("series", "EQ"),
        "ltp":              ltp,
        "prev_close":       prev_c,
        "open":             open_p,
        "day_high":         day_high,
        "day_low":          day_low,
        "change_pct":       pct_chg,
        "vwap":             vwap,
        "week52_high":      week52h,
        "week52_low":       week52l,
        "upper_circuit":    upper_c,
        "lower_circuit":    lower_c,
        "face_value":       _to_float(sec.get("faceValue")),
        "market_lot":       _to_int(sec.get("marketLot")),
        "listing_date":     sec.get("listingDate", ""),
        "impact_cost":      _to_float(sec.get("impactCost")),
        "total_traded_qty": _to_int(price.get("totalTradedVolume")),
        "total_traded_val": _to_float(price.get("totalTradedValue")),  # ₹ Cr
    }


def _parse_trade_info(data: dict) -> dict:
    """Parse /api/quote-equity?symbol=X&section=trade_info response."""
    if not data:
        return {}

    bulk  = data.get("bulkBlockDeals", []) or []
    sdd   = data.get("secDelDpData", []) or []
    vol   = data.get("marketDeptOrderBook", {}) or {}
    trade = data.get("tradeInfo", {}) or {}

    # Delivery % from the last 5 days
    delivery_rows = []
    for row in sdd[:5]:
        qty   = _to_int(row.get("quantityTraded"))
        deliv = _to_int(row.get("deliveryQuantity"))
        pct   = _to_float(row.get("deliveryToTradedQuantity"))
        date  = row.get("date", row.get("recordDate", ""))
        if qty:
            delivery_rows.append({
                "date": date, "qty": qty,
                "delivery_qty": deliv, "delivery_pct": pct,
            })

    return {
        "delivery_last5":   delivery_rows,
        "delivery_pct_avg": (
            round(sum(r["delivery_pct"] for r in delivery_rows if r["delivery_pct"]) /
                  len([r for r in delivery_rows if r["delivery_pct"]]), 1)
            if any(r["delivery_pct"] for r in delivery_rows) else None
        ),
        "total_buy_qty":    _to_int(vol.get("totalBuyQuantity")),
        "total_sell_qty":   _to_int(vol.get("totalSellQuantity")),
    }


def _parse_corporate_actions(data: list) -> list[dict]:
    """Parse corporate actions (dividends, bonuses, splits, rights)."""
    if not data:
        return []
    out = []
    for item in data[:20]:
        date_str = item.get("exDate") or item.get("recordDate") or ""
        out.append({
            "ex_date":    date_str,
            "purpose":    item.get("purpose") or item.get("subject", ""),
            "remarks":    item.get("remarks", ""),
        })
    return out


def _parse_financial_results(data: dict) -> list[dict]:
    """Parse quarterly financial results from NSE."""
    if not data:
        return []
    rows = []
    for item in (data.get("data") or data if isinstance(data, list) else [])[:8]:
        rows.append({
            "period":          item.get("period", item.get("fromDate", "")),
            "sales":           _to_float(item.get("income") or item.get("revenue")),
            "net_profit":      _to_float(item.get("profitLoss") or item.get("netProfit")),
            "eps":             _to_float(item.get("eps")),
            "result_type":     item.get("resultType", ""),
        })
    return [r for r in rows if r["sales"] or r["net_profit"]]


def _parse_board_meetings(data: list) -> list[dict]:
    if not data:
        return []
    out = []
    for item in data[:5]:
        out.append({
            "meeting_date": item.get("meetingDate", ""),
            "purpose":      item.get("purpose", ""),
        })
    return out


def _parse_index_membership(data: dict) -> list[str]:
    if not data:
        return []
    indices = data.get("indexList") or data.get("data") or []
    return [i.get("indexName", "") for i in indices if i.get("indexName")]


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_nse_deep(symbol_bare: str) -> dict:
    """Fetch comprehensive NSE data for a symbol.

    Makes 4-6 parallel API calls through the same cookie session.
    Total time: ~3-5 seconds. Never raises — returns partial data on errors.
    """
    sym = symbol_bare.replace(".NS", "").replace(".BO", "").upper()
    out: dict[str, Any] = {
        "symbol":             sym,
        "fetched_at":         datetime.now(timezone.utc).isoformat(),
        "quote":              {},
        "trade_info":         {},
        "corporate_actions":  [],
        "financial_results":  [],
        "board_meetings":     [],
        "index_membership":   [],
        "error":              None,
    }

    try:
        client = await _get_nse_session()

        # Fetch primary quote + trade info + ancillary data concurrently
        results = await asyncio.gather(
            _api_get(client, f"/api/quote-equity?symbol={sym}"),
            _api_get(client, f"/api/quote-equity?symbol={sym}&section=trade_info"),
            _api_get(client, f"/api/corporateInfo?symbol={sym}"),
            _api_get(client, f"/api/corp-info?symbol={sym}&corpType=boardMeeting&market=equities"),
            _api_get(client, f"/api/indices-stocks?index=NIFTY%2050&symbol={sym}"),
            return_exceptions=True,
        )

        quote_raw, trade_raw, corp_raw, board_raw, idx_raw = results

        if isinstance(quote_raw, dict):
            out["quote"] = _parse_quote(quote_raw)

        if isinstance(trade_raw, dict):
            out["trade_info"] = _parse_trade_info(trade_raw)

        if isinstance(corp_raw, dict):
            # Corporate actions section
            actions = (
                corp_raw.get("corporate_actions")
                or corp_raw.get("corpAction")
                or corp_raw.get("data")
                or []
            )
            out["corporate_actions"] = _parse_corporate_actions(actions)

            # Financial results section
            fin = corp_raw.get("financial_results") or corp_raw.get("finResult") or []
            out["financial_results"] = _parse_financial_results(fin if isinstance(fin, list) else fin)

        if isinstance(board_raw, dict):
            bm = board_raw.get("data") or board_raw.get("boardMeetings") or []
            out["board_meetings"] = _parse_board_meetings(bm)

        if isinstance(idx_raw, dict):
            out["index_membership"] = _parse_index_membership(idx_raw)

        await client.aclose()

    except Exception as exc:
        logger.warning(f"[nse_crawler] {sym}: {exc}")
        out["error"] = str(exc)

    return out
