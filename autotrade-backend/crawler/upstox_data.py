"""Upstox data crawler — used ONLY for data Zerodha Kite Connect doesn't expose.

Data sourced from Upstox:
  - News (per stock)                   → get_news()
  - Company Profile / Overview          → get_company_profile()
  - Income Statement (P&L)             → get_income_statement()
  - Balance Sheet                       → get_balance_sheet()
  - Cash Flow                           → get_cash_flow()
  - Key Ratios (PE, ROE, ROCE …)       → get_key_ratios()
  - Shareholding Pattern                → get_shareholding()
  - Corporate Actions (div/split/bonus) → get_corporate_actions()
  - Competitors                         → get_competitors()
  - Market Intel (PCR, Max Pain, OI)   → get_market_intel()

Cross-check (Zerodha is primary, Upstox is fallback/validation):
  - Live price                          → get_ltp()
  - Historical OHLCV                    → get_historical()
  - Options chain OI                    → get_option_chain()

Authentication:
  - OAuth2 flow — access token stored in .env as UPSTOX_ACCESS_TOKEN
  - Visit /api/v1/upstox/login to get the auth URL
  - After browser login Upstox redirects to /api/v1/upstox/callback
  - Token is saved to .env automatically
"""
import asyncio
import time
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from crawler.upstox_auth import ensure_upstox_token_fresh
from utils.config import settings
from utils.logger import logger

# ── Constants ─────────────────────────────────────────────────────────────────

_BASE = "https://api.upstox.com"
_V2   = f"{_BASE}/v2"

# Simple TTL cache: key → (data, expires_at)
_CACHE: dict[str, tuple[Any, float]] = {}
_TTL = {
    "news":              300,    # 5 min
    "profile":           3600,   # 1 hr
    "financials":        3600,   # 1 hr
    "ratios":            3600,
    "shareholding":      3600,
    "corporate_actions": 3600,
    "competitors":       3600,
    "market_intel":      60,     # 1 min (PCR/OI)
    "ltp":               5,      # 5 s
    "historical":        300,
    "option_chain":      30,
    "isin_map":          86400,  # 24 hr
}

# ISIN lookup cache: NSE symbol (bare, no .NS) → ISIN string
_ISIN_CACHE: dict[str, str] = {}


# ── Auth headers ──────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    token = settings.UPSTOX_ACCESS_TOKEN
    if not token:
        raise RuntimeError("Upstox access token not set. Visit /api/v1/upstox/login to authenticate.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    }


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _get_cache(key: str) -> Any | None:
    entry = _CACHE.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _set_cache(key: str, data: Any, ttl_key: str) -> None:
    _CACHE[key] = (data, time.time() + _TTL.get(ttl_key, 300))


# ── ISIN mapping ──────────────────────────────────────────────────────────────

async def get_isin(symbol: str) -> str | None:
    """Resolve NSE symbol (e.g. 'RELIANCE') → ISIN via Upstox instrument master.

    Falls back to yfinance .info if the Upstox instrument list doesn't match.
    """
    bare = symbol.upper().replace(".NS", "").replace(".BO", "")
    if bare in _ISIN_CACHE:
        return _ISIN_CACHE[bare]

    # Try yfinance first — fast and doesn't need Upstox token
    try:
        import yfinance as yf
        info = yf.Ticker(f"{bare}.NS").fast_info
        isin = getattr(info, "isin", None)
        if not isin:
            t = yf.Ticker(f"{bare}.NS")
            isin = t.isin
        if isin and len(isin) == 12:
            _ISIN_CACHE[bare] = isin
            return isin
    except Exception:
        pass

    # Fallback: Upstox instrument CSV (public, no token needed)
    try:
        ck = f"isin_csv"
        csv_text = _get_cache(ck)
        if csv_text is None:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz")
                import gzip, io
                csv_text = gzip.decompress(r.content).decode("utf-8")
                _set_cache(ck, csv_text, "isin_map")
        for line in csv_text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 5 and parts[3].strip().upper() == bare:
                isin = parts[1].strip()
                if isin and len(isin) == 12:
                    _ISIN_CACHE[bare] = isin
                    return isin
    except Exception as e:
        logger.debug(f"[upstox] ISIN CSV lookup failed: {e}")

    return None


async def get_instrument_key(symbol: str) -> str | None:
    """Resolve symbol → Upstox instrument_key (e.g. 'NSE_EQ|INE002A01018')."""
    isin = await get_isin(symbol)
    return f"NSE_EQ|{isin}" if isin else None


# ── News ──────────────────────────────────────────────────────────────────────

async def get_news(symbol: str, limit: int = 10) -> list[dict]:
    """Fetch stock-specific news via Upstox News API."""
    ck = f"news:{symbol}:{limit}"
    if cached := _get_cache(ck):
        return cached

    if not await ensure_upstox_token_fresh():
        return []

    ikey = await get_instrument_key(symbol)
    if not ikey:
        logger.warning(f"[upstox/news] No instrument key for {symbol}")
        return []

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_V2}/news/articles",
                headers=_headers(),
                params={"instrument_key": ikey, "page_size": limit, "page": 1},
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                articles = data.get("articles", data) if isinstance(data, dict) else data
                out = [_parse_news_item(a) for a in (articles if isinstance(articles, list) else [])]
                _set_cache(ck, out, "news")
                return out
            logger.warning(f"[upstox/news] {symbol} → {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"[upstox/news] {symbol} failed: {e}")
    return []


def _parse_news_item(a: dict) -> dict:
    return {
        "title":       a.get("title") or a.get("headline", ""),
        "url":         a.get("url") or a.get("link", ""),
        "source":      a.get("source") or a.get("publisher", ""),
        "published_at": a.get("published_at") or a.get("date", ""),
        "summary":     a.get("summary") or a.get("description", ""),
    }


# ── Fundamentals ──────────────────────────────────────────────────────────────

async def _fundamentals(endpoint: str, path_id: str, cache_key: str, params: dict | None = None) -> dict:
    """path_id is whatever identifier this specific endpoint wants in its URL
    path (ISIN for most; `competitors` is the one exception — see
    get_competitors below), and endpoint is the URL segment after it, e.g.
    "profile", "key-ratios".

    Real shape confirmed against live Upstox API docs + live calls (2026-07-21):
    GET /v2/fundamentals/{path_id}/{endpoint} — the identifier is a PATH
    segment, not a query param. The previous ?isin=... query-param form
    404'd on every single call (verified live against RELIANCE — profile/
    income-statement/balance-sheet/cash-flow/key-ratios/shareholding/
    corporate-actions all returned UDAPI100060 "Resource not Found" before
    this fix); market-data endpoints (ltp/historical/market_intel) were
    unaffected since they don't go through this helper.
    """
    ck = f"{cache_key}:{endpoint}:{path_id}:{sorted((params or {}).items())}"
    if cached := _get_cache(ck):
        return cached
    if not await ensure_upstox_token_fresh():
        return {}
    try:
        from urllib.parse import quote
        encoded_id = quote(path_id, safe="")   # instrument_key contains "|" — must be percent-encoded
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(f"{_V2}/fundamentals/{encoded_id}/{endpoint}", headers=_headers(), params=params or {})
            if r.status_code == 200:
                data = r.json().get("data", r.json())
                _set_cache(ck, data, cache_key)
                return data
            logger.warning(f"[upstox/{endpoint}] {path_id} → {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"[upstox/{endpoint}] {path_id} failed: {e}")
    return {}


async def get_company_profile(symbol: str) -> dict:
    isin = await get_isin(symbol)
    # Upstox's path segment for this one is "profile", not "company-profile".
    return await _fundamentals("profile", isin, "profile") if isin else {}


async def get_income_statement(symbol: str, period: str = "annual") -> dict:
    isin = await get_isin(symbol)
    time_period = "quarterly" if period == "quarterly" else "yearly"
    return await _fundamentals(
        "income-statement", isin, "financials",
        {"type": "consolidated", "time_period": time_period, "fs": "true"},
    ) if isin else {}


async def get_balance_sheet(symbol: str, period: str = "annual") -> dict:
    # Upstox's balance-sheet endpoint has no yearly/quarterly param — only
    # `type` (consolidated/standalone) and `fs` (line-item detail). `period`
    # is accepted for call-signature compatibility with the other two
    # statement fetchers but has no effect here.
    isin = await get_isin(symbol)
    return await _fundamentals(
        "balance-sheet", isin, "financials", {"type": "consolidated", "fs": "true"},
    ) if isin else {}


async def get_cash_flow(symbol: str, period: str = "annual") -> dict:
    # Same caveat as get_balance_sheet — no time_period param on Upstox's side.
    isin = await get_isin(symbol)
    return await _fundamentals(
        "cash-flow", isin, "financials", {"type": "consolidated", "fs": "true"},
    ) if isin else {}


async def get_key_ratios(symbol: str) -> dict:
    isin = await get_isin(symbol)
    return await _fundamentals("key-ratios", isin, "ratios") if isin else {}


async def get_shareholding(symbol: str) -> dict:
    isin = await get_isin(symbol)
    return await _fundamentals("share-holdings", isin, "shareholding") if isin else {}


async def get_corporate_actions(symbol: str) -> list:
    isin = await get_isin(symbol)
    if not isin:
        return []
    data = await _fundamentals("corporate-actions", isin, "corporate_actions")
    return data if isinstance(data, list) else data.get("actions", data.get("data", []))


async def get_competitors(symbol: str) -> list:
    # Unlike every other fundamentals endpoint, /competitors rejects a bare
    # ISIN (verified live: HTTP 400 "Invalid Instrument key") and requires the
    # full instrument_key (e.g. "NSE_EQ|INE002A01018") in the path instead.
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return []
    data = await _fundamentals("competitors", ikey, "competitors")
    return data if isinstance(data, list) else data.get("competitors", [])


# ── Market Intel (cross-platform — also available on Zerodha via NSE) ─────────

async def get_market_intel(symbol: str) -> dict:
    """PCR, Max Pain, OI — Upstox provides this in one call."""
    ck = f"market_intel:{symbol}"
    if cached := _get_cache(ck):
        return cached
    if not await ensure_upstox_token_fresh():
        return {}
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return {}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_V2}/market/option-chain/pcr",
                headers=_headers(),
                params={"instrument_key": ikey},
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                _set_cache(ck, data, "market_intel")
                return data
    except Exception as e:
        logger.debug(f"[upstox/market_intel] {symbol}: {e}")
    return {}


# ── Cross-check: Live price (Zerodha primary → Upstox fallback) ───────────────

async def get_ltp(symbol: str) -> float | None:
    """Get LTP from Upstox for cross-check against Zerodha WebSocket price."""
    ck = f"ltp:{symbol}"
    if cached := _get_cache(ck):
        return cached
    if not await ensure_upstox_token_fresh():
        return None
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{_V2}/market-quote/ltp",
                headers=_headers(),
                params={"instrument_key": ikey},
            )
            if r.status_code == 200:
                data = r.json().get("data", {})
                ltp = None
                for v in data.values():
                    ltp = v.get("last_price") or v.get("ltp")
                    break
                if ltp:
                    _set_cache(ck, float(ltp), "ltp")
                    return float(ltp)
    except Exception as e:
        logger.debug(f"[upstox/ltp] {symbol}: {e}")
    return None


# ── Cross-check: Historical OHLCV (Zerodha primary → Upstox gap-fill) ─────────

async def get_historical(
    symbol: str,
    interval: str = "day",
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    """Fetch OHLCV candles from Upstox as Zerodha fallback / gap-fill.

    interval: 1minute | 30minute | day | week | month
    """
    if not from_date:
        from_date = (date.today() - timedelta(days=365)).isoformat()
    if not to_date:
        to_date = date.today().isoformat()

    ck = f"hist:{symbol}:{interval}:{from_date}:{to_date}"
    if cached := _get_cache(ck):
        return cached
    if not await ensure_upstox_token_fresh():
        return []
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_V2}/historical-candle/{ikey}/{interval}/{to_date}/{from_date}",
                headers=_headers(),
            )
            if r.status_code == 200:
                candles = r.json().get("data", {}).get("candles", [])
                out = [
                    {
                        "timestamp": c[0], "open": c[1], "high": c[2],
                        "low": c[3], "close": c[4], "volume": c[5],
                    }
                    for c in candles
                ]
                _set_cache(ck, out, "historical")
                return out
    except Exception as e:
        logger.debug(f"[upstox/historical] {symbol}: {e}")
    return []


# ── Cross-check: Options chain OI (vs Zerodha quote) ─────────────────────────

async def get_option_chain(symbol: str, expiry: str | None = None) -> dict:
    """Get option chain with OI from Upstox — cross-check against Zerodha quote OI."""
    if not await ensure_upstox_token_fresh():
        return {}
    ikey = await get_instrument_key(symbol)
    if not ikey:
        return {}
    ck = f"option_chain:{symbol}:{expiry}"
    if cached := _get_cache(ck):
        return cached
    try:
        params: dict = {"instrument_key": ikey}
        if expiry:
            params["expiry_date"] = expiry
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(f"{_V2}/option/chain", headers=_headers(), params=params)
            if r.status_code == 200:
                data = r.json().get("data", {})
                _set_cache(ck, data, "option_chain")
                return data
    except Exception as e:
        logger.debug(f"[upstox/option_chain] {symbol}: {e}")
    return {}


# ── OAuth helpers (called from api/upstox_auth.py) ────────────────────────────

def get_auth_url() -> str:
    from urllib.parse import urlencode
    params = {
        "response_type": "code",
        "client_id":     settings.UPSTOX_API_KEY,
        "redirect_uri":  settings.UPSTOX_REDIRECT_URL,
    }
    return f"https://api.upstox.com/v2/login/authorization/dialog?{urlencode(params)}"


async def exchange_code_for_token(code: str) -> str:
    """Exchange OAuth code for access token and persist to .env."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            "https://api.upstox.com/v2/login/authorization/token",
            data={
                "code":          code,
                "client_id":     settings.UPSTOX_API_KEY,
                "client_secret": settings.UPSTOX_API_SECRET,
                "redirect_uri":  settings.UPSTOX_REDIRECT_URL,
                "grant_type":    "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        token = r.json().get("access_token", "")
        if not token:
            raise ValueError(f"No access_token in response: {r.text}")

    # Persist to .env so it survives restarts
    _update_env("UPSTOX_ACCESS_TOKEN", token)
    settings.UPSTOX_ACCESS_TOKEN = token   # update in-process too
    logger.info("[upstox] Access token obtained and saved to .env")
    return token


def _update_env(key: str, value: str) -> None:
    """Update or append a key=value line in .env."""
    import re
    env_path = ".env"
    try:
        with open(env_path, "r") as f:
            content = f.read()
        pattern = rf"^{re.escape(key)}=.*$"
        new_line = f"{key}={value}"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
        else:
            content += f"\n{new_line}\n"
        with open(env_path, "w") as f:
            f.write(content)
    except Exception as e:
        logger.warning(f"[upstox] Could not update .env: {e}")
