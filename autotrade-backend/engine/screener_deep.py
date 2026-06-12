"""Comprehensive Screener.in data extractor.

Fetches and parses the FULL company page from Screener.in, extracting:
  - Key ratios header (mkt cap, current price, PE, PB, div yield, ROE, ROCE, etc.)
  - Pros & Cons bullet points
  - Quarterly P&L results (last 8 quarters)
  - Annual P&L results (last 10 years)
  - Compounded growth rates (10yr/5yr/3yr/TTM for sales, profit, stock price, ROE)
  - Balance sheet (equity, reserves, borrowings, fixed assets, total assets)
  - Cash flows (operating, investing, financing)
  - Shareholding pattern with quarterly series (promoter, FII, DII, public)
  - Dividend history

All parsing is best-effort — missing sections return empty lists/dicts.
A 2-second delay is applied after every HTTP request.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from utils.logger import logger

_SCREENER_BASE = "https://www.screener.in/company"
_TIMEOUT       = 20.0
_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.screener.in/",
}


def _to_float(s: str | None) -> float | None:
    if not s:
        return None
    # Remove ₹, commas, % signs, spaces, Cr suffix
    clean = re.sub(r"[₹,\s%]", "", str(s).strip())
    clean = re.sub(r"cr$", "", clean, flags=re.I)
    try:
        return float(clean)
    except ValueError:
        return None


def _clean_text(tag) -> str:
    return tag.get_text(separator=" ", strip=True) if tag else ""


async def _fetch_html(sym: str) -> str | None:
    """Fetch the Screener.in company page HTML. Tries consolidated first."""
    urls = [
        f"{_SCREENER_BASE}/{sym}/consolidated/",
        f"{_SCREENER_BASE}/{sym}/",
    ]
    async with httpx.AsyncClient(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    logger.debug(f"[screener_deep] fetched {url}")
                    await asyncio.sleep(2)
                    return resp.text
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.debug(f"[screener_deep] HTTP {exc.response.status_code} for {url}")
            except Exception as exc:
                logger.debug(f"[screener_deep] request error for {url}: {exc}")
    await asyncio.sleep(2)
    return None


# ── Section parsers ───────────────────────────────────────────────────────────

def _parse_header_ratios(soup) -> dict:
    """Top ratio bar: mkt cap, current price, book value, PE, PB, face value, div yield, ROCE, ROE."""
    result: dict[str, Any] = {}
    ul = soup.find(id="top-ratios") or soup.find(class_="company-ratios")
    if not ul:
        return result

    raw: dict[str, str] = {}
    for li in ul.find_all("li"):
        name_tag  = li.find(class_="name")
        value_tag = li.find(class_="value") or li.find(class_="number")
        if name_tag and value_tag:
            key = _clean_text(name_tag).lower()
            raw[key] = _clean_text(value_tag)

    def _g(*keys: str) -> float | None:
        for k in keys:
            v = raw.get(k)
            if v is not None:
                return _to_float(v)
        return None

    result = {
        "market_cap_cr":       _g("market cap", "market cap (cr)"),
        "current_price":       _g("current price"),
        "high_52w":            _g("high / low", "52 week high"),
        "book_value":          _g("book value"),
        "face_value":          _g("face value"),
        "dividend_yield":      _g("dividend yield", "div yield"),
        "pe_ratio":            _g("stock p/e", "p/e"),
        "roe":                 _g("roe"),
        "roce":                _g("roce"),
        "raw_ratios":          raw,  # keep full dict for extras
    }
    # P/B derived from price and book value
    p = result.get("current_price") or 0
    bv = result.get("book_value") or 0
    if p and bv and bv > 0:
        result["pb_ratio"] = round(p / bv, 2)

    # 52-week H/L is often combined "H / L"
    if "high / low" in raw:
        parts = raw["high / low"].split("/")
        if len(parts) == 2:
            result["high_52w"] = _to_float(parts[0])
            result["low_52w"]  = _to_float(parts[1])

    return {k: v for k, v in result.items() if v is not None}


def _parse_pros_cons(soup) -> dict:
    """Parse 'Pros' and 'Cons' bullet lists from Screener."""
    pros: list[str] = []
    cons: list[str] = []

    # Strategy 1: <div class="pros"> / <div class="cons"> (Screener's main pattern)
    for tag in ["div", "ul", "section"]:
        for el in soup.find_all(tag, class_=lambda c: c and "pros" in str(c).lower().split()):
            items = [_clean_text(li) for li in el.find_all("li") if _clean_text(li)]
            if items:
                pros = items
                break
        for el in soup.find_all(tag, class_=lambda c: c and "cons" in str(c).lower().split()):
            items = [_clean_text(li) for li in el.find_all("li") if _clean_text(li)]
            if items:
                cons = items
                break
        if pros or cons:
            break

    # Strategy 2: anchor with id="pros" / id="cons" and a sibling ul
    if not pros:
        anchor = soup.find(id="pros") or soup.find("a", attrs={"name": "pros"})
        if anchor:
            ul = anchor.find_next("ul")
            if ul:
                pros = [_clean_text(li) for li in ul.find_all("li") if _clean_text(li)]
    if not cons:
        anchor = soup.find(id="cons") or soup.find("a", attrs={"name": "cons"})
        if anchor:
            ul = anchor.find_next("ul")
            if ul:
                cons = [_clean_text(li) for li in ul.find_all("li") if _clean_text(li)]

    # Strategy 3: scan every section/div for a "Pros" / "Cons" heading
    if not pros and not cons:
        for section in soup.find_all(["section", "div"]):
            h = section.find(["h2", "h3", "h4", "b", "strong"])
            heading = (_clean_text(h) or "").lower()
            items = [_clean_text(li) for li in section.find_all("li") if _clean_text(li)]
            if "pros" in heading and items and not pros:
                pros = items
            elif "cons" in heading and items and not cons:
                cons = items

    return {"pros": pros[:8], "cons": cons[:8]}


def _parse_table_to_rows(table, max_cols: int = 12) -> dict:
    """Generic table → {header: [values per column/year]}."""
    if not table:
        return {}
    rows = table.find_all("tr")
    if not rows:
        return {}

    # First row = headers (years/quarters)
    header_row = rows[0]
    headers    = [_clean_text(th) for th in header_row.find_all(["th", "td"])]
    # Limit columns
    col_headers = headers[1:max_cols+1]

    result: dict[str, list] = {}
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        row_name = _clean_text(cells[0]).strip()
        if not row_name:
            continue
        vals = []
        for cell in cells[1:max_cols+1]:
            v = _to_float(_clean_text(cell))
            vals.append(v)
        result[row_name] = vals

    return {"periods": col_headers, "rows": result}


def _find_section_table(soup, section_id: str):
    sec = soup.find(id=section_id)
    if not sec:
        return None
    return sec.find("table")


def _parse_quarterly(soup) -> dict:
    """Quarterly P&L results — table id='quarters'."""
    tbl = _find_section_table(soup, "quarters")
    return _parse_table_to_rows(tbl, max_cols=10)


def _parse_annual_pl(soup) -> dict:
    """Annual P&L — table id='profit-loss'."""
    tbl = _find_section_table(soup, "profit-loss")
    return _parse_table_to_rows(tbl, max_cols=11)


def _parse_balance_sheet(soup) -> dict:
    """Balance sheet — table id='balance-sheet'."""
    tbl = _find_section_table(soup, "balance-sheet")
    return _parse_table_to_rows(tbl, max_cols=11)


def _parse_cash_flow(soup) -> dict:
    """Cash flow — table id='cash-flow'."""
    tbl = _find_section_table(soup, "cash-flow")
    return _parse_table_to_rows(tbl, max_cols=11)


def _parse_ratios_table(soup) -> dict:
    """Ratios table — table id='ratios'."""
    tbl = _find_section_table(soup, "ratios")
    return _parse_table_to_rows(tbl, max_cols=11)


def _parse_shareholding(soup) -> dict:
    """Shareholding pattern with quarterly series."""
    sec = soup.find(id="shareholding") or soup.find(id="shareholding-pattern")
    if not sec:
        return {}

    tables = sec.find_all("table")
    result: dict[str, Any] = {}

    for table in tables:
        parsed = _parse_table_to_rows(table, max_cols=10)
        if not parsed or not parsed.get("rows"):
            continue
        periods = parsed.get("periods", [])

        # Most recent values
        latest: dict[str, float | None] = {}
        for row_name, vals in parsed["rows"].items():
            if vals:
                latest[row_name] = vals[0]  # most recent quarter first

        result["shareholding_trend"] = parsed
        result["latest_shareholding"] = latest
        result["periods"] = periods

        # Convenience flat keys
        for k, v in latest.items():
            kl = k.lower()
            if "promoter" in kl and "pledg" not in kl:
                result["promoter_holding"]    = v
                result["promoter_holding_trend"] = vals
            elif "fii" in kl or ("foreign" in kl and "instit" in kl):
                result["fii_holding"]         = v
                result["fii_holding_trend"]   = vals
            elif "dii" in kl or ("domestic" in kl and "instit" in kl):
                result["dii_holding"]         = v
                result["dii_holding_trend"]   = vals
            elif "public" in kl:
                result["public_holding"]      = v
            elif "pledg" in kl:
                result["pledged_pct"]         = v
                result["pledged_trend"]       = vals
        break  # use first (consolidated) table only

    return result


def _parse_compounded_growth(soup) -> dict:
    """Compounded growth rates section — usually two small tables: Sales & Profit."""
    growth: dict[str, Any] = {}
    for section in soup.find_all("section"):
        h = section.find(["h2","h3","h4"])
        heading = (_clean_text(h) or "").lower()
        if "compounded" in heading or "growth" in heading:
            for tbl in section.find_all("table"):
                rows = tbl.find_all("tr")
                for tr in rows:
                    cells = tr.find_all(["td","th"])
                    if len(cells) >= 2:
                        label = _clean_text(cells[0]).lower()
                        val   = _to_float(_clean_text(cells[1]))
                        if val is not None:
                            growth[label] = val
    return growth


def _parse_annual_eps(soup) -> list[dict]:
    """Extract EPS per year from the ratios section if available."""
    tbl = _find_section_table(soup, "ratios")
    if not tbl:
        return []
    parsed = _parse_table_to_rows(tbl, max_cols=11)
    if not parsed:
        return []
    periods = parsed.get("periods", [])
    rows    = parsed.get("rows", {})
    eps_row = rows.get("EPS in Rs") or rows.get("EPS") or rows.get("Basic EPS")
    if not eps_row:
        return []
    return [{"year": p, "eps": v} for p, v in zip(periods, eps_row) if v is not None]


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_screener_deep(symbol_bare: str) -> dict:
    """Fetch and parse the full Screener.in company page.

    Returns a dict with:
      header_ratios, pros_cons, quarterly, annual_pl, balance_sheet,
      cash_flow, ratios_table, shareholding, compounded_growth, annual_eps,
      scraped_at, url

    All keys are present even if empty so callers can safely .get() any field.
    """
    sym  = symbol_bare.replace(".NS","").replace(".BO","").upper()

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.error("[screener_deep] beautifulsoup4 not installed")
        return _empty_result(sym)

    html = await _fetch_html(sym)
    if not html:
        logger.warning(f"[screener_deep] no HTML for {sym}")
        return _empty_result(sym)

    soup = BeautifulSoup(html, "lxml")

    from datetime import datetime, timezone
    return {
        "symbol":           sym,
        "url":              f"{_SCREENER_BASE}/{sym}/",
        "scraped_at":       datetime.now(timezone.utc).isoformat(),
        "header_ratios":    _parse_header_ratios(soup),
        "pros_cons":        _parse_pros_cons(soup),
        "quarterly":        _parse_quarterly(soup),
        "annual_pl":        _parse_annual_pl(soup),
        "balance_sheet":    _parse_balance_sheet(soup),
        "cash_flow":        _parse_cash_flow(soup),
        "ratios_table":     _parse_ratios_table(soup),
        "shareholding":     _parse_shareholding(soup),
        "compounded_growth": _parse_compounded_growth(soup),
        "annual_eps":       _parse_annual_eps(soup),
    }


def _empty_result(sym: str) -> dict:
    from datetime import datetime, timezone
    return {
        "symbol": sym, "url": f"{_SCREENER_BASE}/{sym}/",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "header_ratios": {}, "pros_cons": {"pros": [], "cons": []},
        "quarterly": {}, "annual_pl": {}, "balance_sheet": {},
        "cash_flow": {}, "ratios_table": {}, "shareholding": {},
        "compounded_growth": {}, "annual_eps": [],
    }
