"""Fundamental Analyzer — PE, ROE, ROCE, promoter holding analysis.

Data sources
------------
yfinance    : PE, P/B, ROE, D/E, current ratio, dividend yield, market cap,
              insider (promoter) %, institutional (FII+DII) %
Screener.in : ROCE, accurate promoter %, pledged %, 3-year revenue/profit CAGR
bsedata     : corporate actions / BSE announcements (optional, BSE code needed)

All Screener.in HTTP calls include a 2-second delay to respect rate limits.
All blocking calls (yfinance, bsedata) run in the thread-pool executor.

Public API
----------
fetch_fundamentals_yfinance(symbol)            -> dict   (sync)
fetch_fundamentals_screener(symbol_bare)       -> dict   (async)
calculate_fundamental_score(data)              -> float  (sync, 0-100)
get_fundamental_contribution(symbol, session)  -> float  (async, -30 to +30)
run_fundamental_update(session)                -> None   (async)
"""

from __future__ import annotations

import asyncio
import datetime
import re
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FundamentalData
from utils.logger import logger

# ── Optional imports ──────────────────────────────────────────────────────────

_BS4_AVAILABLE = False
try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    pass

_BSEDATA_AVAILABLE = False
try:
    from bsedata.bse import BSE as _BSE
    _BSEDATA_AVAILABLE = True
except ImportError:
    pass

# ── Constants ─────────────────────────────────────────────────────────────────

_SCREENER_BASE   = "https://www.screener.in/company"
_REQUEST_TIMEOUT = 15.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer":         "https://www.screener.in/",
}


# ── Scoring helpers (each returns 0–10 or None) ───────────────────────────────

def _score_pe(pe: float | None) -> float | None:
    """PE < 10 = 10 pts … PE > 40 = 1 pt; negative EPS = 0."""
    if pe is None:   return None
    if pe <= 0:      return 0.0
    if pe < 10:      return 10.0
    if pe < 15:      return 8.0
    if pe < 25:      return 6.0
    if pe < 40:      return 3.0
    return 1.0


def _score_roe(roe: float | None) -> float | None:
    """ROE in %; >25 = 10 pts … <10 = 1 pt."""
    if roe is None:  return None
    if roe > 25:     return 10.0
    if roe > 20:     return 8.0
    if roe > 15:     return 6.0
    if roe > 10:     return 4.0
    return 1.0


def _score_roce(roce: float | None) -> float | None:
    """ROCE in %; >20 = 10 pts … <10 = 1 pt."""
    if roce is None: return None
    if roce > 20:    return 10.0
    if roce > 15:    return 7.0
    if roce > 10:    return 4.0
    return 1.0


def _score_de(de: float | None) -> float | None:
    """D/E ratio; <0.3 = 10 pts … >1.5 = 1 pt."""
    if de is None:   return None
    if de < 0.3:     return 10.0
    if de < 0.7:     return 7.0
    if de < 1.5:     return 4.0
    return 1.0


def _score_promoter(ph: float | None) -> float | None:
    """Promoter holding %; >65 = 10 pts … <35 = 2 pts."""
    if ph is None:   return None
    if ph > 65:      return 10.0
    if ph > 50:      return 7.0
    if ph > 35:      return 5.0
    return 2.0


def _score_pledged(p: float | None) -> float | None:
    """Pledged %; 0% = 10 pts … >20% = 0 (red flag)."""
    if p is None:    return None
    if p == 0:       return 10.0
    if p < 5:        return 8.0
    if p < 20:       return 4.0
    return 0.0


def _score_revenue_growth(rg: float | None) -> float | None:
    """3-yr revenue CAGR %; >20 = 10 pts … <5 = 2 pts."""
    if rg is None:   return None
    if rg > 20:      return 10.0
    if rg > 15:      return 8.0
    if rg > 10:      return 6.0
    if rg > 5:       return 4.0
    return 2.0


def _score_profit_growth(pg: float | None) -> float | None:
    """3-yr profit CAGR %; >25 = 10 pts … <10 = 2 pts."""
    if pg is None:   return None
    if pg > 25:      return 10.0
    if pg > 15:      return 8.0
    if pg > 10:      return 5.0
    return 2.0


# ── Number parser ─────────────────────────────────────────────────────────────

def _to_float(text: Any, default: float | None = None) -> float | None:
    """Strip currency symbols/units and return float, or default."""
    if text is None:
        return default
    cleaned = re.sub(r"[₹,\s%]", "", str(text))
    cleaned = re.sub(r"Cr\.?", "", cleaned, flags=re.IGNORECASE)
    m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return default


# ── 1. yfinance fetch (sync) ──────────────────────────────────────────────────

def fetch_fundamentals_yfinance(symbol: str) -> dict:
    """Fetch fundamental ratios from Yahoo Finance via yfinance.

    Parameters
    ----------
    symbol : NSE ticker including suffix, e.g. ``RELIANCE.NS``

    Returns
    -------
    dict with keys: company_name, pe_ratio, pb_ratio, roe, debt_to_equity,
    current_ratio, revenue_growth_ttm, profit_growth_ttm, dividend_yield,
    market_cap_cr, promoter_holding, fii_holding.

    NOTE:  yfinance ``debtToEquity`` is stored as a percentage (50 = D/E 0.5).
           ``returnOnEquity`` is a decimal (0.18 = 18 %).
           ``heldPercentInsiders`` approximates promoter holding (decimal).
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("fetch_fundamentals_yfinance: yfinance not installed")
        return {}

    try:
        info: dict = yf.Ticker(symbol).info
    except Exception as exc:
        logger.warning(f"fetch_fundamentals_yfinance {symbol}: {exc}")
        return {}

    def _g(key: str) -> Any:
        return info.get(key)

    # ROE: decimal → percent
    roe_raw = _g("returnOnEquity")
    roe = round(roe_raw * 100, 2) if roe_raw is not None else None

    # D/E: Yahoo Finance stores as ratio * 100 (e.g., 50.5 means 0.505)
    de_raw = _g("debtToEquity")
    de = round(de_raw / 100, 4) if de_raw is not None else None

    # Dividend yield: decimal → percent
    dy_raw = _g("dividendYield")
    div_yield = round(dy_raw * 100, 2) if dy_raw is not None else None

    # Market cap: INR → Crores (1 Cr = 10^7)
    mc_raw = _g("marketCap")
    market_cap_cr = round(mc_raw / 1e7, 2) if mc_raw else None

    # Promoter holding (heldPercentInsiders): decimal → percent
    ins_raw = _g("heldPercentInsiders")
    promoter_holding = round(ins_raw * 100, 2) if ins_raw is not None else None

    # FII holding (heldPercentInstitutions): decimal → percent
    inst_raw = _g("heldPercentInstitutions")
    fii_holding = round(inst_raw * 100, 2) if inst_raw is not None else None

    # TTM growth rates: decimal → percent
    rev_g = _g("revenueGrowth")
    rev_growth_ttm = round(rev_g * 100, 2) if rev_g is not None else None

    earn_g = _g("earningsGrowth")
    profit_growth_ttm = round(earn_g * 100, 2) if earn_g is not None else None

    return {
        "company_name":       _g("longName") or _g("shortName") or "",
        "pe_ratio":           _g("trailingPE"),
        "pb_ratio":           _g("priceToBook"),
        "roe":                roe,
        "debt_to_equity":     de,
        "current_ratio":      _g("currentRatio"),
        "revenue_growth_ttm": rev_growth_ttm,
        "profit_growth_ttm":  profit_growth_ttm,
        "dividend_yield":     div_yield,
        "market_cap_cr":      market_cap_cr,
        "promoter_holding":   promoter_holding,
        "fii_holding":        fii_holding,
    }


# ── 2. Screener.in fetch (async) ──────────────────────────────────────────────

async def fetch_fundamentals_screener(symbol_bare: str) -> dict:
    """Scrape fundamental ratios from Screener.in for an NSE symbol.

    Parameters
    ----------
    symbol_bare : NSE symbol WITHOUT the ``.NS`` suffix, e.g. ``RELIANCE``

    Returns
    -------
    dict with keys: pe_ratio, pb_ratio, roe, roce, debt_to_equity,
    promoter_holding, fii_holding, pledged_pct,
    revenue_growth_3yr, profit_growth_3yr.

    A 2-second sleep is applied after the HTTP call to respect rate limits.
    """
    if not _BS4_AVAILABLE:
        logger.error("fetch_fundamentals_screener: beautifulsoup4 not installed")
        return {}

    sym = symbol_bare.replace(".NS", "").replace(".BO", "").upper()
    urls = [
        f"{_SCREENER_BASE}/{sym}/consolidated/",
        f"{_SCREENER_BASE}/{sym}/",
    ]

    html: str | None = None
    async with httpx.AsyncClient(
        headers=_HEADERS, timeout=_REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                html = resp.text
                break
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    f"fetch_fundamentals_screener {sym}: HTTP {exc.response.status_code}"
                )
            except httpx.RequestError as exc:
                logger.warning(f"fetch_fundamentals_screener {sym}: {exc}")

    # Always wait 2 seconds after the HTTP call (even on failure)
    await asyncio.sleep(2)

    if not html:
        return {}

    soup = BeautifulSoup(html, "lxml")
    result: dict = {}

    result.update(_parse_top_ratios(soup))
    result.update(_parse_shareholding(soup))
    result.update(_parse_growth_tables(soup))

    return result


def _parse_top_ratios(soup: BeautifulSoup) -> dict:
    """Extract key ratios from the #top-ratios list."""
    ratios_ul = soup.find(id="top-ratios") or soup.find(class_="company-ratios")
    if not ratios_ul:
        return {}

    raw: dict[str, float | None] = {}
    for li in ratios_ul.find_all("li"):
        name_tag  = li.find(class_="name")
        value_tag = li.find(class_="value") or li.find(class_="number")
        if not (name_tag and value_tag):
            continue
        key = name_tag.get_text(strip=True).lower()
        raw[key] = _to_float(value_tag.get_text(strip=True))

    def _pick(*keys: str) -> float | None:
        for k in keys:
            if raw.get(k) is not None:
                return raw[k]
        return None

    price  = _pick("current price", "price")
    bv     = _pick("book value")
    pe     = _pick("stock p/e", "p/e", "pe")
    pb     = (round(price / bv, 2) if (price and bv and bv > 0) else _pick("price to book", "p/b"))
    roe    = _pick("roe")
    roce   = _pick("roce")
    de     = _pick("debt to equity", "debt / equity", "d/e")
    dy     = _pick("dividend yield", "div yield")

    return {k: v for k, v in {
        "pe_ratio":      pe,
        "pb_ratio":      pb,
        "roe":           roe,
        "roce":          roce,
        "debt_to_equity": de,
        "dividend_yield": dy,
    }.items() if v is not None}


def _parse_shareholding(soup: BeautifulSoup) -> dict:
    """Extract promoter %, FII %, and pledged % from the shareholding table."""
    result: dict = {}

    section = (
        soup.find(id="shareholding")
        or soup.find(id="shareholding-pattern")
        or soup.find("section", string=re.compile("shareholding", re.I))
    )
    if not section:
        return result

    table = section.find("table")
    if not table:
        return result

    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        label = cells[0].get_text(strip=True).lower()
        # Second column = most recent quarter
        val = _to_float(cells[1].get_text(strip=True))
        if val is None:
            continue

        if "promoter" in label and "pledg" not in label:
            result["promoter_holding"] = val
        elif "fii" in label or ("foreign" in label and "institution" in label):
            result["fii_holding"] = val
        elif "pledg" in label:
            result["pledged_pct"] = val

    # Fallback: look for "Pledged" anywhere in the page if not found in table
    if "pledged_pct" not in result:
        for tag in soup.find_all(string=re.compile(r"pledg", re.I)):
            parent = tag.parent
            if parent:
                next_num = _to_float(parent.find_next(string=re.compile(r"^\s*\d")))
                if next_num is not None:
                    result["pledged_pct"] = next_num
                    break

    return result


def _parse_growth_tables(soup: BeautifulSoup) -> dict:
    """Extract 3-year revenue and profit CAGR from Screener.in growth tables."""
    result: dict = {}

    def _extract_3yr(heading_pattern: str) -> float | None:
        for h in soup.find_all(["h2", "h3", "h4", "b"]):
            if re.search(heading_pattern, h.get_text(strip=True), re.I):
                table = h.find_next("table")
                if not table:
                    continue
                for tr in table.find_all("tr"):
                    cells = tr.find_all("td")
                    if len(cells) < 2:
                        continue
                    if re.search(r"3\s*year", cells[0].get_text(strip=True), re.I):
                        return _to_float(cells[1].get_text(strip=True))
        return None

    result["revenue_growth_3yr"] = _extract_3yr(r"(compounded\s+)?sales\s+growth|revenue\s+growth")
    result["profit_growth_3yr"]  = _extract_3yr(r"(compounded\s+)?profit\s+growth|earnings\s+growth")

    return {k: v for k, v in result.items() if v is not None}


# ── 3. Score calculation ──────────────────────────────────────────────────────

def calculate_fundamental_score(data: dict) -> float:
    """Compute weighted fundamental score normalized to 0–100.

    Eight metrics each score 0–10.  Missing (None) metrics are excluded from
    the denominator so a company with fewer data points is not penalized.

    Returns 50.0 (neutral) when no metric is available.
    """
    metrics: dict[str, float | None] = {
        "pe":             _score_pe(data.get("pe_ratio")),
        "roe":            _score_roe(data.get("roe")),
        "roce":           _score_roce(data.get("roce")),
        "de":             _score_de(data.get("debt_to_equity")),
        "promoter":       _score_promoter(data.get("promoter_holding")),
        "pledged":        _score_pledged(data.get("pledged_pct")),
        "revenue_growth": _score_revenue_growth(
            data.get("revenue_growth_3yr") or data.get("revenue_growth_ttm")
        ),
        "profit_growth":  _score_profit_growth(
            data.get("profit_growth_3yr") or data.get("profit_growth_ttm")
        ),
    }

    available = [(k, v) for k, v in metrics.items() if v is not None]
    if not available:
        return 50.0

    raw_score   = sum(v for _, v in available)
    max_possible = len(available) * 10.0
    return round(raw_score / max_possible * 100.0, 2)


# ── 4. Signal contribution ────────────────────────────────────────────────────

async def get_fundamental_contribution(
    symbol: str,
    session: AsyncSession,
) -> float:
    """Convert DB fundamental_score to the range [-30, +30].

    Formula: (score - 50) × 0.6
      score = 100  →  +30  (excellent fundamentals)
      score =  50  →    0  (average)
      score =   0  →  -30  (poor fundamentals)

    Returns 0.0 when no DB record exists for the symbol.
    """
    row = (await session.execute(
        select(FundamentalData).where(FundamentalData.symbol == symbol)
    )).scalar_one_or_none()

    if row is None or row.fundamental_score is None:
        return 0.0

    return round((row.fundamental_score - 50.0) * 0.6, 2)


# ── 5. Full update run ────────────────────────────────────────────────────────

async def run_fundamental_update(session: AsyncSession) -> None:
    """Fetch and persist fundamentals for all NSE large + mid cap symbols.

    For each symbol:
    1. Call fetch_fundamentals_yfinance() in the thread pool (non-blocking).
    2. Call fetch_fundamentals_screener() (async, includes 2s delay).
    3. Screener.in values override yfinance where both are present.
    4. Calculate fundamental_score.
    5. UPSERT the FundamentalData row (update existing or insert new).

    Designed to run weekly via Celery beat (Sunday 00:00 IST).
    """
    from utils.config import settings

    # Primary: hub universe (all top-traded NSE stocks the agent actually scores).
    # Fallback: watchlist large + mid caps when hub_universe table is empty.
    try:
        from sqlalchemy import text as _text
        r = await session.execute(_text("SELECT symbol FROM hub_universe ORDER BY rank LIMIT 800"))
        hub_syms = [row[0] for row in r.fetchall()]
    except Exception:
        hub_syms = []

    if hub_syms:
        symbols = hub_syms  # already have .NS suffix
    else:
        symbols = settings.nse_symbols + settings.nse_mid_symbols
    loop    = asyncio.get_event_loop()

    logger.info(f"[fundamental_update] Starting for {len(symbols)} symbols")

    for idx, symbol in enumerate(symbols, start=1):
        bare = symbol.replace(".NS", "")

        # ── Fetch yfinance (sync → executor) ─────────────────────────────────
        try:
            yf_data = await loop.run_in_executor(
                None, fetch_fundamentals_yfinance, symbol
            )
        except Exception as exc:
            logger.warning(f"[fundamental_update] yfinance {symbol}: {exc}")
            yf_data = {}

        # ── Fetch Screener.in (async, includes sleep) ─────────────────────────
        try:
            sc_data = await fetch_fundamentals_screener(bare)
        except Exception as exc:
            logger.warning(f"[fundamental_update] screener {bare}: {exc}")
            sc_data = {}

        # Merge: Screener.in overrides yfinance for shared keys
        merged: dict = {**yf_data, **sc_data}
        if not merged:
            logger.warning(f"[fundamental_update] no data for {symbol} — skipping")
            continue

        score = calculate_fundamental_score(merged)
        now = datetime.datetime.utcnow()

        # ── True upsert — safe to re-run without duplicate key errors ──────────
        stmt = pg_insert(FundamentalData).values(
            symbol=symbol,
            company_name=merged.get("company_name", ""),
            pe_ratio=merged.get("pe_ratio"),
            pb_ratio=merged.get("pb_ratio"),
            roe=merged.get("roe"),
            roce=merged.get("roce"),
            debt_to_equity=merged.get("debt_to_equity"),
            current_ratio=merged.get("current_ratio"),
            revenue_growth_3yr=merged.get("revenue_growth_3yr"),
            profit_growth_3yr=merged.get("profit_growth_3yr"),
            promoter_holding=merged.get("promoter_holding"),
            fii_holding=merged.get("fii_holding"),
            pledged_pct=merged.get("pledged_pct"),
            market_cap_cr=merged.get("market_cap_cr"),
            dividend_yield=merged.get("dividend_yield"),
            fundamental_score=score,
            last_updated=now,
        ).on_conflict_do_update(
            index_elements=["symbol"],
            set_={
                "company_name":       merged.get("company_name", ""),
                "pe_ratio":           merged.get("pe_ratio"),
                "pb_ratio":           merged.get("pb_ratio"),
                "roe":                merged.get("roe"),
                "roce":               merged.get("roce"),
                "debt_to_equity":     merged.get("debt_to_equity"),
                "current_ratio":      merged.get("current_ratio"),
                "revenue_growth_3yr": merged.get("revenue_growth_3yr"),
                "profit_growth_3yr":  merged.get("profit_growth_3yr"),
                "promoter_holding":   merged.get("promoter_holding"),
                "fii_holding":        merged.get("fii_holding"),
                "pledged_pct":        merged.get("pledged_pct"),
                "market_cap_cr":      merged.get("market_cap_cr"),
                "dividend_yield":     merged.get("dividend_yield"),
                "fundamental_score":  score,
                "last_updated":       now,
            },
        )
        await session.execute(stmt)
        logger.info(
            f"[fundamental_update] [{idx}/{len(symbols)}] {symbol} ({merged.get('company_name', '')[:30]})  "
            f"score={score:.1f}  pe={merged.get('pe_ratio')}  "
            f"roe={merged.get('roe')}%  roce={merged.get('roce')}%  "
            f"promoter={merged.get('promoter_holding')}%  pledged={merged.get('pledged_pct')}%"
        )

        # Commit every 50 symbols so progress survives a timeout or crash
        if idx % 50 == 0:
            await session.commit()
            logger.info(f"[fundamental_update] checkpoint committed ({idx}/{len(symbols)})")

    logger.info("[fundamental_update] Complete")


# ── DB lookup helper (used by api/india.py) ───────────────────────────────────

async def get_fundamentals_for_symbol(
    symbol: str,
    session: AsyncSession,
) -> FundamentalData | None:
    """Return the FundamentalData DB row for *symbol*, or None if missing.

    Does NOT auto-fetch — call run_fundamental_update() to populate the DB.
    """
    return (await session.execute(
        select(FundamentalData).where(FundamentalData.symbol == symbol)
    )).scalar_one_or_none()


# How long a cached fundamentals row stays fresh before an on-demand refetch.
_FUND_TTL = datetime.timedelta(days=7)


async def fetch_and_cache_fundamentals(
    symbol: str,
    session: AsyncSession,
) -> FundamentalData | None:
    """Return fundamentals for *symbol*, fetching on demand if the DB row is
    missing or stale (>7 days).

    Unlike the weekly batch ``run_fundamental_update`` (which only covers the
    curated large/mid-cap list), this lets the unified Stock Detail page show
    fundamentals for ANY of the ~9,600 searchable NSE symbols. Reuses the same
    yfinance + Screener.in fetchers and the same scoring/upsert logic.
    """
    sym  = symbol if symbol.endswith(".NS") else symbol + ".NS"
    bare = sym.replace(".NS", "")

    existing = (await session.execute(
        select(FundamentalData).where(FundamentalData.symbol == sym)
    )).scalar_one_or_none()

    fresh = (
        existing is not None
        and existing.last_updated is not None
        and (datetime.datetime.utcnow() - existing.last_updated) < _FUND_TTL
    )
    if fresh:
        return existing

    loop = asyncio.get_event_loop()
    try:
        yf_data = await asyncio.wait_for(
            loop.run_in_executor(None, fetch_fundamentals_yfinance, sym),
            timeout=20.0,
        )
    except Exception as exc:
        logger.debug(f"[fundamentals on-demand] yfinance {sym}: {exc}")
        yf_data = {}
    try:
        sc_data = await fetch_fundamentals_screener(bare)
    except Exception as exc:
        logger.debug(f"[fundamentals on-demand] screener {bare}: {exc}")
        sc_data = {}

    merged: dict = {**yf_data, **sc_data}
    if not merged:
        # Nothing fetched — return any stale row we have rather than nothing.
        return existing

    score = calculate_fundamental_score(merged)
    now   = datetime.datetime.utcnow()

    if existing:
        for field in (
            "company_name", "pe_ratio", "pb_ratio", "roe", "roce",
            "debt_to_equity", "current_ratio", "revenue_growth_3yr",
            "profit_growth_3yr", "promoter_holding", "fii_holding",
            "pledged_pct", "market_cap_cr", "dividend_yield",
        ):
            if merged.get(field) is not None:
                setattr(existing, field, merged[field])
        existing.fundamental_score = score
        existing.last_updated      = now
        row = existing
    else:
        row = FundamentalData(
            symbol=sym,
            company_name=merged.get("company_name", ""),
            pe_ratio=merged.get("pe_ratio"),
            pb_ratio=merged.get("pb_ratio"),
            roe=merged.get("roe"),
            roce=merged.get("roce"),
            debt_to_equity=merged.get("debt_to_equity"),
            current_ratio=merged.get("current_ratio"),
            revenue_growth_3yr=merged.get("revenue_growth_3yr"),
            profit_growth_3yr=merged.get("profit_growth_3yr"),
            promoter_holding=merged.get("promoter_holding"),
            fii_holding=merged.get("fii_holding"),
            pledged_pct=merged.get("pledged_pct"),
            market_cap_cr=merged.get("market_cap_cr"),
            dividend_yield=merged.get("dividend_yield"),
            fundamental_score=score,
            last_updated=now,
        )
        session.add(row)

    await session.flush()
    await session.commit()
    return row


# ── Optional: BSE corporate actions ──────────────────────────────────────────

def fetch_bse_corporate_actions(bse_code: str) -> list[dict]:
    """Fetch corporate actions for a BSE-listed company using bsedata.

    Parameters
    ----------
    bse_code : BSE security ID, e.g. ``'500325'`` for Reliance Industries.
               The BSE code must be looked up separately from the NSE symbol.

    Returns
    -------
    list of dicts: [{'subject': str, 'ex_date': str, 'record_date': str, ...}]
    Returns [] when bsedata is unavailable or the code is invalid.
    """
    if not _BSEDATA_AVAILABLE:
        logger.debug("fetch_bse_corporate_actions: bsedata not installed")
        return []
    try:
        b = _BSE()
        actions = b.corporateActions(bse_code)
        return actions if isinstance(actions, list) else []
    except Exception as exc:
        logger.warning(f"fetch_bse_corporate_actions {bse_code}: {exc}")
        return []
