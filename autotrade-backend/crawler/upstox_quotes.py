"""Upstox market-data quotes — the drop-in replacement for Kite's /quote family.

Kite Connect's token expired on 2026-08-31. These functions return the EXACT
dict shapes the rest of the system already consumes, so downstream code
(price_feed, market_snapshot, live_snapshot, the exit loop, the resampler)
needs no change:

    get_live_prices(symbols)  -> {symbol: {price, last_price, change, change_pct}}
    get_full_quote(symbol)    -> {symbol, last_price, ohlc, volume, bid, ask, oi,
                                  buy_depth, sell_depth, change, change_pct, ...}
    get_ohlc_batch(symbols)   -> {symbol: {open, high, low, close, last_price, volume}}

ENDPOINT MAPPING (Kite -> Upstox)
    /quote/ltp        ->  /v2/market-quote/ltp        (batch, instrument_key)
    /quote            ->  /v2/market-quote/quotes     (batch, full depth + OI)
    /quote/ohlc       ->  /v3/market-quote/ohlc       (live + previous candle)

BATCHING. Kite chunked at 200 instruments because it encodes them in the query
string and the gateway rejects URLs over ~8KB. Upstox has the same constraint
and documents a 500-instrument ceiling, but its keys are LONGER
("NSE_EQ|INE002A01018" is 19 chars against Kite's "NSE:RELIANCE" at 12), so the
URL fills faster. 250 keeps each request comfortably inside the limit.

EXIT-BUCKET ISOLATION is preserved. The `exit_bucket` flag still routes a read
through the reserved quota so a universe scan or dashboard burst can never make
a stop-loss check queue behind it. That property was load-bearing under Kite
(KITE_EXIT_RPS=1 was a dedicated bucket) and is kept under Upstox with the same
semantics; see utils/config.py::UPSTOX_EXIT_RPS.
"""
from __future__ import annotations

import asyncio

import httpx

from utils.config import settings
from utils.logger import logger

_API_V2 = "https://api.upstox.com/v2"
_API_V3 = "https://api.upstox.com/v3"

# See the module docstring: Upstox keys are ~60% longer than Kite's, so the
# safe instrument count per URL is lower than the documented 500 ceiling.
_BATCH = 250

# Cached symbol -> instrument_key map. Rebuilt lazily; the instrument master
# changes once a day, so a process-lifetime cache is correct and avoids a DB
# round trip on every 15-second price tick.
_KEY_MAP: dict[str, str] = {}
_REV_MAP: dict[str, str] = {}
_MAP_LOADED = False


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.UPSTOX_ACCESS_TOKEN}",
        "Accept": "application/json",
    }


async def ensure_key_map(force: bool = False) -> int:
    """Load symbol<->instrument_key both ways. Returns the entry count."""
    global _KEY_MAP, _REV_MAP, _MAP_LOADED
    if _MAP_LOADED and not force:
        return len(_KEY_MAP)
    try:
        from crawler.upstox_instruments import build_key_maps
        from db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as s:
            _KEY_MAP, _REV_MAP = await build_key_maps(s)
        _MAP_LOADED = True
        logger.info(f"[upstox_quotes] instrument key map loaded: {len(_KEY_MAP):,} symbols")
    except Exception as exc:
        logger.warning(f"[upstox_quotes] key map load failed: {type(exc).__name__}: {exc}")
    return len(_KEY_MAP)


def _to_key(symbol: str) -> str | None:
    """'RELIANCE.NS' -> 'NSE_EQ|INE002A01018'. NSE equities only.

    Returns None for anything without a mapped key rather than guessing. A
    fabricated key would address a DIFFERENT instrument, which is worse than a
    missing price -- the caller already handles absence.
    """
    if not symbol:
        return None
    s = symbol.strip().upper()
    if s in _KEY_MAP:
        return _KEY_MAP[s]
    if not s.endswith(".NS"):
        return _KEY_MAP.get(f"{s}.NS")
    return None


def _from_key(key: str) -> str | None:
    return _REV_MAP.get(key)


async def _get(url: str, keys: list[str], *, exit_bucket: bool = False) -> dict:
    """One batched GET. Returns the raw `data` dict, or {} on any failure."""
    from crawler.upstox_limiter import acquire

    await acquire(exit_bucket=exit_bucket)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url, params={"instrument_key": ",".join(keys)},
                            headers=_headers())
        if r.status_code == 401:
            logger.error("[upstox_quotes] 401 — Upstox access token is invalid or expired")
            return {}
        if r.status_code != 200:
            logger.warning(f"[upstox_quotes] {url.rsplit('/', 1)[-1]} HTTP {r.status_code}")
            return {}
        return r.json().get("data") or {}
    except Exception as exc:
        logger.warning(f"[upstox_quotes] request failed: {type(exc).__name__}: {exc}")
        return {}


def _resolve_symbol(api_key: str, payload: dict, requested: dict) -> str | None:
    """Map an Upstox response key back to the caller's symbol.

    Upstox echoes a response key that is usually "NSE_EQ:RELIANCE" (segment and
    TRADING SYMBOL) rather than the "NSE_EQ|ISIN" instrument_key that was sent.
    The payload also carries the real instrument_token, so three routes are
    tried before giving up -- and giving up drops the row rather than guessing.
    """
    ikey = payload.get("instrument_token") or ""
    if ikey and ikey in requested:
        return requested[ikey]
    sym = _from_key(ikey) if ikey else None
    if sym:
        return sym
    if ":" in api_key:
        bare = api_key.split(":")[-1]
        cand = f"{bare}.NS"
        if cand in _KEY_MAP:
            return cand
    return None


# ── 1. LTP ───────────────────────────────────────────────────────────────────
async def get_live_prices(symbols: list[str], *, exit_bucket: bool = False) -> dict[str, dict]:
    """{symbol: {price, last_price, change, change_pct}} — same shape as Kite's."""
    if not symbols:
        return {}
    await ensure_key_map()

    requested: dict[str, str] = {}
    for s in symbols:
        k = _to_key(s)
        if k:
            requested[k] = s
    if not requested:
        return {}

    keys = list(requested)
    result: dict[str, dict] = {}
    for i in range(0, len(keys), _BATCH):
        data = await _get(f"{_API_V2}/market-quote/ltp", keys[i:i + _BATCH],
                          exit_bucket=exit_bucket)
        for api_key, payload in (data or {}).items():
            sym = _resolve_symbol(api_key, payload, requested)
            if not sym:
                continue
            ltp = float(payload.get("last_price") or 0.0)
            # The LTP endpoint carries no change field, exactly as Kite's did.
            # Zeroes here are the documented contract, not missing data.
            result[sym] = {"price": ltp, "last_price": ltp,
                           "change": 0.0, "change_pct": 0.0}
    return result


# ── 2. Full quote ────────────────────────────────────────────────────────────
async def get_full_quote(symbol: str) -> dict:
    """Full quote for one symbol — OHLC, volume, 5-level depth, OI."""
    await ensure_key_map()
    key = _to_key(symbol)
    if not key:
        return {}

    data = await _get(f"{_API_V2}/market-quote/quotes", [key])
    if not data:
        return {}
    payload = next(iter(data.values()), {}) or {}

    ohlc = payload.get("ohlc") or {}
    depth = (payload.get("depth") or {})
    buy = depth.get("buy") or []
    sell = depth.get("sell") or []
    ltp = float(payload.get("last_price") or 0.0)
    close = float(ohlc.get("close") or 0.0)

    return {
        "symbol":           symbol,
        "last_price":       ltp,
        "ohlc":             ohlc,
        "volume":           payload.get("volume", 0),
        "bid":              float((buy[0] or {}).get("price", 0.0)) if buy else 0.0,
        "ask":              float((sell[0] or {}).get("price", 0.0)) if sell else 0.0,
        "oi":               payload.get("oi", 0),
        "last_trade_time":  payload.get("last_trade_time"),
        # Kite returned a numeric token here; Upstox's identifier is the string
        # instrument_key. Callers use it only for logging/joins, never
        # arithmetic, so the type change is safe -- and keeping the field name
        # avoids touching every consumer.
        "instrument_token": payload.get("instrument_token") or key,
        "buy_depth":        buy,
        "sell_depth":       sell,
        "change":           float(payload.get("net_change") or 0.0),
        "change_pct":       ((ltp - close) / close * 100) if close else 0.0,
    }


# ── 3. OHLC batch ────────────────────────────────────────────────────────────
async def get_ohlc_batch(symbols: list[str], *, interval: str = "1d") -> dict[str, dict]:
    """{symbol: {open, high, low, close, last_price, volume}} for breadth/regime."""
    if not symbols:
        return {}
    await ensure_key_map()
    requested = {k: s for s in symbols if (k := _to_key(s))}
    if not requested:
        return {}

    keys = list(requested)
    out: dict[str, dict] = {}
    for i in range(0, len(keys), _BATCH):
        chunk = keys[i:i + _BATCH]
        from crawler.upstox_limiter import acquire

        await acquire()
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(f"{_API_V3}/market-quote/ohlc",
                                params={"instrument_key": ",".join(chunk), "interval": interval},
                                headers=_headers())
            data = r.json().get("data") or {} if r.status_code == 200 else {}
        except Exception as exc:
            logger.warning(f"[upstox_quotes] ohlc failed: {type(exc).__name__}")
            data = {}

        for api_key, payload in data.items():
            sym = _resolve_symbol(api_key, payload, requested)
            if not sym:
                continue
            # v3 nests the candle under live_ohlc/prev_ohlc; v2 returned it flat.
            candle = payload.get("live_ohlc") or payload.get("prev_ohlc") or payload.get("ohlc") or {}
            out[sym] = {
                "open":       float(candle.get("open") or 0.0),
                "high":       float(candle.get("high") or 0.0),
                "low":        float(candle.get("low") or 0.0),
                "close":      float(candle.get("close") or 0.0),
                "volume":     int(candle.get("volume") or 0),
                "last_price": float(payload.get("last_price") or 0.0),
            }
    return out


async def get_ltp(symbol: str) -> float | None:
    """Single-symbol convenience wrapper."""
    got = await get_live_prices([symbol])
    q = got.get(symbol)
    return q["last_price"] if q else None
