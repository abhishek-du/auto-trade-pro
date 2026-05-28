"""Earnings call transcript crawler for Indian listed companies.

Sources (in priority order):
  1. BSE filing API — most reliable (SEBI LODR mandate)
  2. NSE filing announcements
  3. Trendlyne conference-calls (fallback)

Public API
----------
get_all_transcripts(symbol, limit)     -> list[dict]
fetch_bse_transcripts(symbol, limit)   -> list[dict]
fetch_nse_transcripts(symbol, limit)   -> list[dict]
extract_transcript_text(pdf_url)       -> str
chunk_transcript(text, max_tokens)     -> list[str]
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from crawler.fii_dii_crawler import BROWSER_HEADERS
from utils.logger import logger

# ── BSE scrip code map ────────────────────────────────────────────────────────

BSE_SCRIP_MAP: dict[str, int] = {
    "RELIANCE.NS":    500325,
    "TCS.NS":         532540,
    "HDFCBANK.NS":    500180,
    "INFY.NS":        500209,
    "ICICIBANK.NS":   532174,
    "SBIN.NS":        500112,
    "BHARTIARTL.NS":  532454,
    "KOTAKBANK.NS":   500247,
    "AXISBANK.NS":    532215,
    "BAJFINANCE.NS":  500034,
    "HINDUNILVR.NS":  500696,
    "LT.NS":          500510,
    "MARUTI.NS":      532500,
    "ASIANPAINT.NS":  500820,
    "WIPRO.NS":       507685,
    "HCLTECH.NS":     532281,
    "ULTRACEMCO.NS":  532538,
    "NESTLEIND.NS":   500790,
    "SUNPHARMA.NS":   524715,
    "DRREDDY.NS":     500124,
    "ITC.NS":         500875,
    "TATASTEEL.NS":   500470,
    "TECHM.NS":       532755,
    "PERSISTENT.NS":  533179,
    "COFORGE.NS":     532541,
    "TITAN.NS":       500114,
    "BAJAJ-AUTO.NS":  532977,
    "ONGC.NS":        500312,
    "POWERGRID.NS":   532898,
    "NTPC.NS":        532555,
    "COALINDIA.NS":   533278,
    "ADANIENT.NS":    512599,
    "INDUSINDBK.NS":  532187,
    "HINDALCO.NS":    500440,
    "TATAMOTORS.NS":  500570,
    "BAJAJFINSV.NS":  532978,
    "DIVISLAB.NS":    532488,
    "CIPLA.NS":       500087,
    "EICHERMOT.NS":   505200,
    "M&M.NS":         500520,
    "ZOMATO.NS":      543320,
}

# Trendlyne numeric IDs for conference-call pages
_TRENDLYNE_ID_MAP: dict[str, int] = {
    "INFY.NS":      630,
    "TCS.NS":       628,
    "HDFCBANK.NS":  1333,
    "RELIANCE.NS":  1214,
    "WIPRO.NS":     197,
    "ICICIBANK.NS": 669,
    "SBIN.NS":      3045,
    "HCLTECH.NS":   1326,
    "TECHM.NS":     1331,
    "AXISBANK.NS":  1099,
}

_BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
    "Referer":    "https://www.bseindia.com/",
    "Origin":     "https://www.bseindia.com",
}

# ── Dynamic BSE scrip code resolver ──────────────────────────────────────────
# Cache: nse_ticker → bse_scrip_code (in-process, no expiry needed)
_SCRIP_CODE_CACHE: dict[str, int] = {}

_BSE_SEARCH_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/listofscripdata/w"
    "?Group=&Exchange=C&ScrCd=&segment=Equity&SectorIndex=0"
    "&Category=A&PageNo=1&PageSize=25&strSearch={ticker}"
)


async def _resolve_bse_scrip_code(symbol: str) -> int | None:
    """Dynamically resolve BSE scrip code for any NSE-listed company.

    1. Check hardcoded BSE_SCRIP_MAP first (fast, no network).
    2. Check in-process cache.
    3. Call BSE search API and match on scrip_id == NSE ticker.
    """
    if symbol in BSE_SCRIP_MAP:
        return BSE_SCRIP_MAP[symbol]

    ticker = symbol.replace(".NS", "").replace(".BO", "").upper()

    if ticker in _SCRIP_CODE_CACHE:
        return _SCRIP_CODE_CACHE[ticker]

    url = _BSE_SEARCH_URL.format(ticker=ticker)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_BSE_HEADERS)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.debug(f"[earnings] BSE scrip lookup failed for {ticker}: {exc}")
        return None

    if not isinstance(data, list):
        return None

    # Find exact match on scrip_id (NSE ticker)
    for row in data:
        if str(row.get("scrip_id", "")).upper() == ticker:
            code = int(row["SCRIP_CD"])
            _SCRIP_CODE_CACHE[ticker] = code
            logger.debug(f"[earnings] Resolved {ticker} → BSE scrip {code}")
            return code

    # Fuzzy match on company name (first result containing ticker)
    for row in data:
        if ticker.lower() in str(row.get("Scrip_Name", "")).lower():
            code = int(row["SCRIP_CD"])
            _SCRIP_CODE_CACHE[ticker] = code
            logger.debug(f"[earnings] Resolved {ticker} (fuzzy) → BSE scrip {code}")
            return code

    logger.debug(f"[earnings] No BSE scrip code found for {ticker}")
    return None


# ── Quarter parser ─────────────────────────────────────────────────────────────

def _parse_quarter_from_text(text: str) -> str:
    """Extract quarter label (e.g. 'Q4FY26') from announcement text."""
    text_upper = text.upper()

    patterns = [
        r'Q([1-4])\s*FY\s*(\d{2,4})',
        r'Q([1-4])\s*(\d{4})',
        r'QUARTER\s*([1-4])\s*FY\s*(\d{2,4})',
    ]
    for pat in patterns:
        m = re.search(pat, text_upper)
        if m:
            q = m.group(1)
            yr = m.group(2)
            if len(yr) == 4:
                yr = yr[2:]
            return f"Q{q}FY{yr}"

    quarter_map = {
        "JAN-MAR": "Q4", "JANUARY-MARCH": "Q4", "MARCH QUARTER": "Q4",
        "APR-JUN": "Q1", "APRIL-JUNE": "Q1", "JUNE QUARTER": "Q1",
        "JUL-SEP": "Q2", "JULY-SEPTEMBER": "Q2", "SEPTEMBER QUARTER": "Q2",
        "OCT-DEC": "Q3", "OCTOBER-DECEMBER": "Q3", "DECEMBER QUARTER": "Q3",
    }
    for key, q in quarter_map.items():
        if key in text_upper:
            return q

    return ""


# ── BSE source ────────────────────────────────────────────────────────────────

_BSE_PDF_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"


async def fetch_bse_transcripts(symbol: str, limit: int = 5) -> list[dict]:
    """Fetch transcript announcements from BSE filing API.

    Uses strCat=-1 (all categories) to search all announcements, then
    filters for SUBCATNAME='Earnings Call Transcript'.
    PDF URL: https://www.bseindia.com/xml-data/corpfiling/AttachLive/{ATTACHMENTNAME}
    Resolves BSE scrip code dynamically for any NSE-listed company.
    """
    scrip_code = await _resolve_bse_scrip_code(symbol)
    if not scrip_code:
        logger.debug(f"[earnings] Could not resolve BSE scrip code for {symbol}")
        return []

    url = (
        f"https://api.bseindia.com/BseIndiaAPI/api/"
        f"AnnSubCategoryGetData/w?"
        f"pageno=1&strCat=-1"
        f"&strPrevDate=&strScrip={scrip_code}"
        f"&strSearch=E&strToDate=&strType=C&subcategory=49"
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers=_BSE_HEADERS)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning(f"[earnings] BSE fetch failed for {symbol}: {exc}")
        return []

    # Filter for earnings call transcripts
    all_anns = data.get("Table", [])
    transcript_anns = [
        ann for ann in all_anns
        if "transcript" in (ann.get("SUBCATNAME", "") or "").lower()
        or "earnings call" in (ann.get("NEWSSUB", "") or "").lower()
        or "concall" in (ann.get("NEWSSUB", "") or "").lower()
    ]

    if not transcript_anns:
        # Try broader search: look for any PDF attachments mentioning transcript
        transcript_anns = [
            ann for ann in all_anns
            if "transcript" in (ann.get("NEWSSUB", "") or "").lower()
        ]

    results = []
    for ann in transcript_anns[:limit]:
        attachment = ann.get("ATTACHMENTNAME", "")
        if not attachment:
            continue
        pdf_url = _BSE_PDF_BASE + attachment

        desc = ann.get("NEWSSUB", "") or ""
        quarter = _parse_quarter_from_text(desc)
        date_str = (ann.get("DissemDT", "") or ann.get("NEWS_DT", "") or "")[:10]

        # If no quarter in description, try to infer from filing date
        if not quarter and date_str:
            year = date_str[:4]
            month = int(date_str[5:7]) if len(date_str) >= 7 else 0
            if month in (4, 5):    quarter = f"Q4FY{str(int(year))[-2:]}"
            elif month in (7, 8):  quarter = f"Q1FY{str(int(year)+1)[-2:]}"
            elif month in (10, 11):quarter = f"Q2FY{str(int(year)+1)[-2:]}"
            elif month in (1, 2):  quarter = f"Q3FY{str(int(year))[-2:]}"

        results.append({
            "source":     "BSE",
            "symbol":     symbol,
            "scrip_code": scrip_code,
            "title":      desc[:150],
            "pdf_url":    pdf_url,
            "date":       date_str,
            "quarter":    quarter,
            "filing_id":  str(ann.get("NEWSID", "") or ""),
        })

    logger.info(f"[earnings] BSE: {len(results)} transcripts for {symbol}")
    return results


# ── NSE source ────────────────────────────────────────────────────────────────

async def fetch_nse_transcripts(symbol: str, limit: int = 5) -> list[dict]:
    """Fetch transcript announcements from NSE API (two-step session)."""
    nse_symbol = symbol.replace(".NS", "").replace(".BO", "")
    url = (
        f"https://www.nseindia.com/api/corp-info-equities-announcement"
        f"?symbol={nse_symbol}&category=transcript&index=equities"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            await client.get("https://www.nseindia.com", headers=BROWSER_HEADERS)
            await asyncio.sleep(1.5)
            r = await client.get(url, headers=BROWSER_HEADERS)
            if r.status_code != 200:
                logger.debug(f"[earnings] NSE returned {r.status_code} for {symbol}")
                return []
            data = r.json()
    except Exception as exc:
        logger.warning(f"[earnings] NSE fetch failed for {symbol}: {exc}")
        return []

    items = (data.get("data") or [])[:limit]
    results = []
    for item in items:
        attachment = item.get("attchmnt", "")
        if not attachment:
            continue
        pdf_url = f"https://nsearchives.nseindia.com/{attachment}"
        desc = item.get("desc", "") or ""
        results.append({
            "source":    "NSE",
            "symbol":    symbol,
            "title":     desc[:150],
            "pdf_url":   pdf_url,
            "date":      (item.get("an_dt", "") or "")[:10],
            "quarter":   _parse_quarter_from_text(desc),
            "filing_id": str(item.get("seq_id", "") or ""),
        })

    logger.info(f"[earnings] NSE: {len(results)} transcripts for {symbol}")
    return results


# ── Trendlyne fallback ────────────────────────────────────────────────────────

async def fetch_trendlyne_transcripts(symbol: str, limit: int = 5) -> list[dict]:
    """Scrape Trendlyne conference-calls as last resort."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("[earnings] BeautifulSoup not available for Trendlyne fallback")
        return []

    tl_id = _TRENDLYNE_ID_MAP.get(symbol)
    if not tl_id:
        return []

    nse_symbol = symbol.replace(".NS", "").replace(".BO", "")
    url = f"https://trendlyne.com/conference-calls/{tl_id}/{nse_symbol}/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept":     "text/html,application/xhtml+xml",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        logger.warning(f"[earnings] Trendlyne fetch failed for {symbol}: {exc}")
        return []

    results = []
    pdf_links = soup.find_all("a", href=lambda h: h and "/get-document/post/pdf/" in h)
    for link in pdf_links[:limit]:
        pdf_url = "https://trendlyne.com" + link["href"]
        parent  = link.find_parent(class_=lambda c: c and "post" in c.lower())
        title   = parent.get_text(strip=True)[:150] if parent else "Earnings call transcript"
        fid     = link["href"].split("/")[-2] if "/" in link["href"] else ""
        results.append({
            "source":    "TRENDLYNE",
            "symbol":    symbol,
            "title":     title,
            "pdf_url":   pdf_url,
            "date":      "",
            "quarter":   _parse_quarter_from_text(title),
            "filing_id": fid,
        })

    logger.info(f"[earnings] Trendlyne: {len(results)} transcripts for {symbol}")
    return results


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def get_all_transcripts(symbol: str, limit: int = 5) -> list[dict]:
    """Fetch available transcript list from all sources, deduplicated."""
    bse_results, nse_results = await asyncio.gather(
        fetch_bse_transcripts(symbol, limit),
        fetch_nse_transcripts(symbol, limit),
    )

    combined = bse_results + nse_results

    if not combined:
        combined = await fetch_trendlyne_transcripts(symbol, limit)

    seen: set[str] = set()
    unique: list[dict] = []
    for item in combined:
        key = item.get("quarter") or item.get("filing_id") or item["pdf_url"]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    unique.sort(key=lambda x: x.get("date", ""), reverse=True)
    return unique[:limit]


# ── PDF extractor ─────────────────────────────────────────────────────────────

async def extract_transcript_text(pdf_url: str) -> str:
    """Download PDF and extract plain text. Primary: pdfplumber. Fallback: PyPDF2."""
    pdf_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept":     "application/pdf,*/*",
        "Referer":    "https://www.bseindia.com/",
    }

    try:
        async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
            r = await client.get(pdf_url, headers=pdf_headers)
            if r.status_code != 200:
                raise ValueError(f"PDF download failed: HTTP {r.status_code} — {pdf_url[:80]}")
            pdf_bytes = r.content
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"PDF download error: {exc}") from exc

    if len(pdf_bytes) < 1000:
        raise ValueError(f"PDF too small ({len(pdf_bytes)} bytes) — likely not a valid transcript")

    import io

    text = ""

    # Primary: pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                pt = page.extract_text()
                if pt:
                    pages.append(pt)
            text = "\n".join(pages)
    except Exception as exc:
        logger.warning(f"[earnings] pdfplumber failed: {exc} — trying PyPDF2")

    # Fallback: PyPDF2
    if not text.strip():
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
            pages = []
            for page in reader.pages:
                pt = page.extract_text()
                if pt:
                    pages.append(pt)
            text = "\n".join(pages)
        except Exception as exc:
            raise ValueError(f"Both PDF extractors failed: {exc}") from exc

    if not text.strip():
        raise ValueError("PDF extracted but no text found — may be a scanned image PDF")

    text = _clean_transcript_text(text)
    logger.info(f"[earnings] Extracted {len(text)} chars, {len(text.split())} words from PDF")
    return text


def _clean_transcript_text(text: str) -> str:
    """Clean common PDF extraction artefacts."""
    # Remove standalone page numbers
    text = re.sub(r'\n\s*\d{1,4}\s*\n', '\n', text)

    lines = text.split('\n')
    lines = [l for l in lines if len(l.strip()) > 3]

    text = '\n'.join(lines)

    # Merge hyphenated line breaks
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)

    # Collapse multiple newlines
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Trim trailing disclaimers (last 15% of doc)
    disclaimer_markers = [
        "Forward-Looking Statements",
        "This transcript has been edited",
        "DISCLAIMER",
        "This document is solely for information",
        "Safe Harbour Statement",
    ]
    for marker in disclaimer_markers:
        idx = text.rfind(marker)
        if idx > 0 and idx > len(text) * 0.85:
            text = text[:idx]

    return text.strip()


# ── Chunker ───────────────────────────────────────────────────────────────────

def chunk_transcript(text: str, max_tokens: int = 90000) -> list[str]:
    """Split long transcripts at paragraph boundaries."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        if len(current) + len(para) > max_chars:
            if current:
                chunks.append(current)
            current = para
        else:
            current += "\n\n" + para
    if current:
        chunks.append(current)

    logger.info(f"[earnings] Transcript split into {len(chunks)} chunks")
    return chunks
