"""Fundamental Analyzer — Screener.in scraper for NSE equity fundamentals.

Scrapes key financial ratios for NSE-listed stocks from Screener.in and
produces a composite fundamental score used by the signal generator.

All HTTP calls use async httpx with browser-impersonation headers.
BeautifulSoup + lxml parse the ratio section; no login is required.

Public API
----------
fetch_fundamental_data(symbol)          -> FundamentalData | None  (async)
calculate_fundamental_score(data)       -> float  (sync, -100 to +100)
analyze_fundamentals(symbol)            -> FundamentalAnalysis | None  (async)
analyze_all_nse_symbols(symbols)        -> list[FundamentalAnalysis]  (async)
"""

from __future__ import annotations

import asyncio
import datetime
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from utils.logger import logger

# ── Optional BeautifulSoup import ─────────────────────────────────────────────

_BS4_AVAILABLE = False
try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    pass

# ── Constants ─────────────────────────────────────────────────────────────────

_SCREENER_BASE    = "https://www.screener.in/company"
_REQUEST_TIMEOUT  = 15.0   # seconds
_REQUEST_DELAY    = 1.5    # seconds between consecutive requests — avoid 429
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.screener.in/",
    "DNT":             "1",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class FundamentalData:
    symbol:             str
    market_cap_cr:      Optional[float]   # INR Crores
    current_price:      Optional[float]
    high_52w:           Optional[float]
    low_52w:            Optional[float]
    pe_ratio:           Optional[float]   # Price / EPS (trailing)
    pb_ratio:           Optional[float]   # Price / Book Value
    dividend_yield_pct: Optional[float]
    roce_pct:           Optional[float]   # Return on Capital Employed
    roe_pct:            Optional[float]   # Return on Equity
    debt_to_equity:     Optional[float]
    eps:                Optional[float]   # EPS (TTM) — derived if not scraped
    book_value:         Optional[float]
    face_value:         Optional[float]
    fetched_at: datetime.datetime = field(default_factory=datetime.datetime.now)


@dataclass
class FundamentalAnalysis:
    symbol:          str
    data:            FundamentalData
    pe_score:        float   # component score
    roe_score:       float
    debt_score:      float
    roce_score:      float
    composite_score: float   # weighted sum, clamped to [-100, +100]
    valuation_label: str     # 'UNDERVALUED'|'FAIR_VALUE'|'OVERVALUED'|'INSUFFICIENT_DATA'
    analyzed_at: datetime.datetime = field(default_factory=datetime.datetime.now)


# ── Number parser ─────────────────────────────────────────────────────────────

def _parse_number(text: str) -> float | None:
    """Strip currency symbols, units, commas and return float or None."""
    if not text:
        return None
    text = str(text).strip()
    text = re.sub(r"[₹,\s]",      "",   text)
    text = re.sub(r"Cr\.?",        "",   text, flags=re.IGNORECASE)
    text = re.sub(r"%",            "",   text)
    # Keep first number token only (handles "2,890 / 2,120" format)
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return None


# ── HTML scraping ─────────────────────────────────────────────────────────────

def _parse_screener_html(html: str, symbol: str) -> FundamentalData:
    """Extract financial ratios from a Screener.in company page."""
    if not _BS4_AVAILABLE:
        raise ImportError("beautifulsoup4 not installed — run: pip install beautifulsoup4 lxml")

    soup = BeautifulSoup(html, "lxml")

    # Locate the top-ratios block (id first, then class fallback)
    ratios_ul = soup.find(id="top-ratios") or soup.find(class_="company-ratios")

    raw: dict[str, float | None] = {}

    if ratios_ul:
        for li in ratios_ul.find_all("li"):
            name_tag  = li.find(class_="name")
            value_tag = li.find(class_="value") or li.find(class_="number")
            if not name_tag or not value_tag:
                continue
            key = name_tag.get_text(strip=True).lower()
            val = value_tag.get_text(strip=True)
            raw[key] = _parse_number(val)

            # 52-week High / Low lives in one cell: "2,890 / 2,120"
            if "high" in key and "/" in value_tag.get_text():
                parts = value_tag.get_text().split("/")
                raw["high_52w"] = _parse_number(parts[0])
                raw["low_52w"]  = _parse_number(parts[1])

    # Normalise keys that Screener.in labels inconsistently across versions
    def _get(*candidates: str) -> float | None:
        for k in candidates:
            if raw.get(k) is not None:
                return raw[k]
        return None

    price   = _get("current price", "price")
    pe      = _get("stock p/e", "p/e", "pe")
    bv      = _get("book value", "book val")
    pb      = round(price / bv, 2) if (price and bv and bv > 0) else _get("price to book", "p/b")
    dy      = _get("dividend yield", "div yield")
    roce    = _get("roce")
    roe     = _get("roe")
    de      = _get("debt to equity", "debt / equity", "d/e")
    mc      = _get("market cap", "mkt cap")
    fv      = _get("face value")

    # EPS derived from P/E and current price when not directly available
    eps: float | None = None
    if price and pe and pe > 0:
        eps = round(price / pe, 2)

    return FundamentalData(
        symbol=symbol,
        market_cap_cr=mc,
        current_price=price,
        high_52w=raw.get("high_52w", _get("high / low")),
        low_52w=raw.get("low_52w"),
        pe_ratio=pe,
        pb_ratio=pb,
        dividend_yield_pct=dy,
        roce_pct=roce,
        roe_pct=roe,
        debt_to_equity=de,
        eps=eps,
        book_value=bv,
        face_value=fv,
    )


# ── Scoring functions ─────────────────────────────────────────────────────────

def _pe_score(pe: float | None) -> float:
    """Score: +25 (cheap) to -25 (expensive). Returns 0 when unavailable."""
    if pe is None or pe <= 0:
        return 0.0
    if pe < 10:   return  25.0
    if pe < 15:   return  20.0
    if pe < 20:   return  10.0
    if pe < 25:   return   0.0
    if pe < 35:   return -15.0
    if pe < 50:   return -20.0
    return -25.0


def _roe_score(roe: float | None) -> float:
    """Score: +20 (excellent returns on equity) to -20 (very weak)."""
    if roe is None:
        return 0.0
    if roe >= 25:  return  20.0
    if roe >= 20:  return  15.0
    if roe >= 15:  return   8.0
    if roe >= 10:  return   0.0
    if roe >= 5:   return -10.0
    return -20.0


def _debt_score(de: float | None) -> float:
    """Score: +20 (debt-free) to -20 (over-leveraged)."""
    if de is None or de < 0:
        return 0.0
    if de == 0:   return  20.0
    if de < 0.3:  return  15.0
    if de < 0.5:  return  10.0
    if de < 1.0:  return   0.0
    if de < 2.0:  return -10.0
    return -20.0


def _roce_score(roce: float | None) -> float:
    """Score: +15 (strong capital efficiency) to -15 (poor)."""
    if roce is None:
        return 0.0
    if roce >= 30:  return  15.0
    if roce >= 20:  return  10.0
    if roce >= 15:  return   5.0
    if roce >= 10:  return   0.0
    if roce >= 5:   return  -8.0
    return -15.0


def calculate_fundamental_score(data: FundamentalData) -> float:
    """Weighted composite fundamental score in the range [-100, +100].

    Weights
    -------
    P/E   : 35 %  — primary valuation metric
    ROE   : 25 %  — management efficiency
    Debt  : 25 %  — financial risk
    ROCE  : 15 %  — capital efficiency
    """
    pe   = _pe_score(data.pe_ratio)
    roe  = _roe_score(data.roe_pct)
    debt = _debt_score(data.debt_to_equity)
    roce = _roce_score(data.roce_pct)

    # Normalise each component's max contribution by weight
    # Max raw: pe=25, roe=20, debt=20, roce=15  →  total = 80 points
    # We scale so 80 → 100 by multiplying by 100/80 = 1.25
    raw    = pe * 0.35 + roe * 0.25 + debt * 0.25 + roce * 0.15
    scaled = raw * (100.0 / 80.0 * 4)   # bring ±20 average range → ±100
    return round(max(-100.0, min(100.0, scaled)), 2)


def _valuation_label(score: float, data: FundamentalData) -> str:
    has_data = any(
        v is not None for v in (data.pe_ratio, data.roe_pct, data.roce_pct)
    )
    if not has_data:
        return "INSUFFICIENT_DATA"
    if score >= 30:
        return "UNDERVALUED"
    if score <= -30:
        return "OVERVALUED"
    return "FAIR_VALUE"


# ── Public async API ──────────────────────────────────────────────────────────

async def fetch_fundamental_data(symbol: str) -> FundamentalData | None:
    """Fetch Screener.in page for *symbol* and return parsed fundamentals.

    Tries the consolidated view first, then falls back to the standalone page.
    *symbol* may include the `.NS` suffix — it is stripped automatically.
    """
    if not _BS4_AVAILABLE:
        logger.error("fetch_fundamental_data: beautifulsoup4 not installed")
        return None

    clean = symbol.replace(".NS", "").replace(".BO", "").upper()
    urls  = [
        f"{_SCREENER_BASE}/{clean}/consolidated/",
        f"{_SCREENER_BASE}/{clean}/",
    ]

    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        for url in urls:
            try:
                response = await client.get(url)
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                return _parse_screener_html(response.text, symbol)
            except httpx.HTTPStatusError as exc:
                logger.warning(f"fetch_fundamental_data {symbol}: HTTP {exc.response.status_code} at {url}")
            except httpx.RequestError as exc:
                logger.warning(f"fetch_fundamental_data {symbol}: request error — {exc}")
            except Exception as exc:
                logger.warning(f"fetch_fundamental_data {symbol}: parse error — {exc}")

    return None


async def analyze_fundamentals(symbol: str) -> FundamentalAnalysis | None:
    """Fetch + score fundamentals for *symbol*."""
    data = await fetch_fundamental_data(symbol)
    if data is None:
        return None

    pe   = _pe_score(data.pe_ratio)
    roe  = _roe_score(data.roe_pct)
    debt = _debt_score(data.debt_to_equity)
    roce = _roce_score(data.roce_pct)
    comp = calculate_fundamental_score(data)
    label = _valuation_label(comp, data)

    logger.info(
        f"Fundamentals {symbol}  P/E={data.pe_ratio}  ROE={data.roe_pct}%  "
        f"D/E={data.debt_to_equity}  ROCE={data.roce_pct}%  "
        f"score={comp:+.1f}  [{label}]"
    )

    return FundamentalAnalysis(
        symbol=symbol,
        data=data,
        pe_score=pe,
        roe_score=roe,
        debt_score=debt,
        roce_score=roce,
        composite_score=comp,
        valuation_label=label,
    )


async def analyze_all_nse_symbols(
    symbols: list[str] | None = None,
) -> list[FundamentalAnalysis]:
    """Analyze fundamentals for all watchlist NSE symbols.

    Falls back to settings.nse_symbols + settings.nse_mid_symbols.
    A 1.5-second delay is inserted between requests to avoid rate-limiting.
    Results are sorted by composite_score descending.
    """
    from utils.config import settings

    all_symbols = symbols or (settings.nse_symbols + settings.nse_mid_symbols)
    results: list[FundamentalAnalysis] = []

    for sym in all_symbols:
        analysis = await analyze_fundamentals(sym)
        if analysis:
            results.append(analysis)
        else:
            logger.warning(f"analyze_all_nse_symbols: skipped {sym}")
        await asyncio.sleep(_REQUEST_DELAY)

    results.sort(key=lambda a: a.composite_score, reverse=True)
    return results
