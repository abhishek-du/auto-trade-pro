"""Zerodha KiteConnect v3 — live market data and historical candles.

Replaces yfinance for Indian stocks during market hours.

Instrument token mapping
------------------------
NSE_TOKENS maps our .NS yfinance symbols to Kite integer instrument_tokens.
Use refresh_instrument_tokens() daily (beat schedule at 08:00 IST) to keep
the mapping in sync with NSE/Kite's master list.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from crawler.zerodha_client import get_kite_client
from db.models import KiteInstrument
from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")

# These are set to False after the first 403 — Zerodha market-data APIs
# (quotes, LTP, historical) require a paid Kite Connect subscription.
# Free plan: OAuth, portfolio, orders only.
_kite_historical_available: bool = True
_kite_quotes_available:     bool = True


def _handle_market_data_403(api_name: str) -> None:
    """Log a single clear message when Kite returns 403 on a market-data endpoint."""
    logger.info(
        f"[zerodha_market] Kite {api_name} API returned 403 — "
        "market data requires a paid Kite Connect subscription (₹2000/month). "
        "Falling back to yfinance for this session."
    )

# ── Static token map (last-resort fallback if DB refresh hasn't run yet) ──────

NSE_TOKENS: dict[str, int] = {
    "RELIANCE.NS":    738561,
    "TCS.NS":        2953217,
    "HDFCBANK.NS":    341249,
    "INFY.NS":        408065,
    "ICICIBANK.NS":  1270529,
    "SBIN.NS":        779521,
    "BHARTIARTL.NS": 2714625,
    "KOTAKBANK.NS":   492033,
    "AXISBANK.NS":   1510401,
    "BAJFINANCE.NS": 4268801,
    "HINDUNILVR.NS":  356865,
    "LT.NS":         2939649,
    "MARUTI.NS":     2815745,
    "ASIANPAINT.NS":   60417,
    "WIPRO.NS":       969473,
    "HCLTECH.NS":    1850625,
    "ULTRACEMCO.NS": 2952193,
    "NESTLEIND.NS":  4598529,
    "SUNPHARMA.NS":   857857,
    "DRREDDY.NS":     225537,
    "ITC.NS":         424961,
    "POWERGRID.NS":  3834113,
    "NTPC.NS":       2977281,
    "COALINDIA.NS":  5215745,
    "PIDILITIND.NS": 2765825,
    "VOLTAS.NS":      951809,
    "MUTHOOTFIN.NS": 3400705,
    "PERSISTENT.NS": 4701186,
    "COFORGE.NS":     635649,
    "LTTS.NS":       4561409,
    "TATAELXSI.NS":  2420225,
}

INDEX_TOKENS: dict[str, int] = {
    "^NSEI":    256265,   # NIFTY 50
    "^BSESN":   274441,   # SENSEX
    "^NSEBANK": 260105,   # BANK NIFTY
}

# Reverse lookup: instrument_token → symbol
_TOKEN_TO_SYMBOL: dict[int, str] = {
    **{v: k for k, v in NSE_TOKENS.items()},
    **{v: k for k, v in INDEX_TOKENS.items()},
}

# ── Timeframe mapping ─────────────────────────────────────────────────────────

_TF_MAP: dict[str, str] = {
    "1m":  "minute",
    "5m":  "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h":  "60minute",
    "1d":  "day",
}


def _to_kite_interval(tf: str) -> str:
    return _TF_MAP.get(tf, tf)


def _symbol_to_kite(symbol: str) -> str:
    """Convert 'RELIANCE.NS' → 'NSE:RELIANCE', '^NSEI' → 'NSE:NIFTY 50'."""
    _index_map = {"^NSEI": "NSE:NIFTY 50", "^BSESN": "BSE:SENSEX", "^NSEBANK": "NSE:NIFTY BANK"}
    if symbol in _index_map:
        return _index_map[symbol]
    if symbol.endswith(".NS"):
        return f"NSE:{symbol[:-3]}"
    if symbol.endswith(".BO"):
        return f"BSE:{symbol[:-3]}"
    return f"NSE:{symbol}"


# ── Instrument token refresh ──────────────────────────────────────────────────

async def refresh_instrument_tokens(session: AsyncSession) -> int:
    """Download NSE instrument master from Kite and upsert into kite_instruments.

    Scheduled daily at 08:00 IST so tokens are fresh before market open.
    Returns number of instruments saved.
    """
    kite = get_kite_client()
    if not kite.access_token:
        logger.warning("[zerodha_market] No access token — skipping instrument refresh")
        return 0

    try:
        rows = await kite.get_instruments("NSE")
    except Exception as exc:
        logger.error(f"[zerodha_market] Instrument download failed: {exc}", exc_info=True)
        return 0

    # Clear existing NSE rows, then bulk-insert
    await session.execute(
        delete(KiteInstrument).where(KiteInstrument.exchange == "NSE")
    )

    now = datetime.datetime.utcnow()
    batch: list[KiteInstrument] = []
    for r in rows:
        try:
            token = int(r.get("instrument_token") or 0)
            if not token:
                continue
            batch.append(KiteInstrument(
                instrument_token = token,
                exchange_token   = int(r.get("exchange_token") or 0),
                tradingsymbol    = str(r.get("tradingsymbol") or ""),
                name             = str(r.get("name") or ""),
                last_price       = float(r.get("last_price") or 0.0),
                expiry           = str(r.get("expiry") or ""),
                strike           = float(r.get("strike") or 0.0),
                tick_size        = float(r.get("tick_size") or 0.05),
                lot_size         = int(float(r.get("lot_size") or 1)),
                instrument_type  = str(r.get("instrument_type") or "EQ"),
                segment          = str(r.get("segment") or "NSE"),
                exchange         = "NSE",
                refreshed_at     = now,
            ))
        except (ValueError, TypeError):
            continue

    if batch:
        session.add_all(batch)
        await session.flush()

        # Update in-memory NSE_TOKENS for EQ instruments
        for inst in batch:
            if inst.instrument_type == "EQ":
                sym = f"{inst.tradingsymbol}.NS"
                NSE_TOKENS[sym] = inst.instrument_token
                _TOKEN_TO_SYMBOL[inst.instrument_token] = sym

    logger.info(f"[zerodha_market] Instrument tokens refreshed: {len(batch)} NSE rows")
    return len(batch)


async def hydrate_tokens_from_db(session: AsyncSession) -> int:
    """Preload NSE_TOKENS from kite_instruments at startup.

    Without this, the in-memory ``NSE_TOKENS`` dict only contains the 30
    hardcoded fallbacks until the daily ``kite_refresh_instruments`` task
    runs at 08:00 IST. Any historical-fetch caller that doesn't pass a
    ``session`` (and thus can't hit ``_get_token_from_db``) will fail
    with "No instrument token" warnings for legitimate symbols.

    Idempotent: safe to call on every startup. Returns the count loaded.
    """
    rows = (await session.execute(
        select(KiteInstrument.tradingsymbol, KiteInstrument.instrument_token).where(
            KiteInstrument.exchange == "NSE",
            KiteInstrument.instrument_type == "EQ",
        )
    )).all()
    loaded = 0
    for ts, token in rows:
        if not ts or not token:
            continue
        sym = f"{ts}.NS"
        NSE_TOKENS[sym] = int(token)
        _TOKEN_TO_SYMBOL[int(token)] = sym
        loaded += 1
    logger.info(f"[zerodha_market] Hydrated NSE_TOKENS from DB: {loaded} symbols")
    return loaded


async def _get_token_from_db(symbol: str, session: AsyncSession | None) -> int | None:
    """Look up instrument_token from kite_instruments DB table."""
    if session is None:
        return None
    bare = symbol.replace(".NS", "").replace(".BO", "")
    result = await session.execute(
        select(KiteInstrument).where(
            KiteInstrument.tradingsymbol == bare,
            KiteInstrument.exchange == "NSE",
            KiteInstrument.instrument_type == "EQ",
        ).limit(1)
    )
    inst = result.scalar_one_or_none()
    return inst.instrument_token if inst else None


def _get_token(symbol: str) -> int | None:
    """Resolve symbol → instrument_token from in-memory map."""
    return NSE_TOKENS.get(symbol) or INDEX_TOKENS.get(symbol)


# ── 1. Live prices (LTP) ──────────────────────────────────────────────────────

async def get_live_prices(symbols: list[str]) -> dict[str, dict]:
    """Fetch last traded price for a list of .NS symbols via Kite LTP endpoint.

    Returns {symbol: {price, last_price, change, change_pct}}.

    Batches the input in chunks of 200 instruments per call: Kite's LTP
    endpoint encodes instruments in the URL query string, and the gateway
    rejects URLs longer than ~8KB with ``URL component 'query' too long``.
    With NSE_TOKENS hydrated to ~9,800 symbols post-startup, a one-shot
    call now overflows immediately — chunking keeps each request well
    under the cap.
    """
    global _kite_quotes_available

    kite = get_kite_client()
    if not kite.access_token or not _kite_quotes_available:
        return {}

    instruments = [_symbol_to_kite(s) for s in symbols]
    if not instruments:
        return {}

    _CHUNK = 200   # ~28 chars per "NSE:SYMBOL," × 200 ≈ 5.6KB query, safe margin
    result: dict[str, dict] = {}
    for i in range(0, len(instruments), _CHUNK):
        chunk = instruments[i:i + _CHUNK]
        try:
            raw: dict = await kite.get_ltp(chunk)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                _kite_quotes_available = False
                _handle_market_data_403("LTP/quote")
                return result
            logger.warning(f"[zerodha_market] LTP chunk {i}+{len(chunk)} failed: {exc}")
            continue
        except Exception as exc:
            logger.warning(f"[zerodha_market] LTP chunk {i}+{len(chunk)} failed: {exc}")
            continue

        for kite_sym, data in raw.items():
            # Reverse-map kite_sym ("NSE:RELIANCE") to our symbol ("RELIANCE.NS")
            bare = kite_sym.split(":")[-1]
            our_sym = f"{bare}.NS"
            ltp = float(data.get("last_price", 0.0))
            result[our_sym] = {
                "price":       ltp,
                "last_price":  ltp,
                "change":      0.0,   # LTP endpoint doesn't include change
                "change_pct":  0.0,
            }
    return result


# ── 2. Full quote ─────────────────────────────────────────────────────────────

async def get_full_quote(symbol: str) -> dict:
    """Full quote for one symbol — includes OHLC, volume, bid/ask, OI."""
    global _kite_quotes_available

    kite = get_kite_client()
    if not kite.access_token or not _kite_quotes_available:
        return {}

    instr = _symbol_to_kite(symbol)
    try:
        raw = await kite.get_quote([instr])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            _kite_quotes_available = False
            _handle_market_data_403("quote")
        else:
            logger.warning(f"[zerodha_market] Quote fetch failed for {symbol}: {exc}")
        return {}
    except Exception as exc:
        logger.warning(f"[zerodha_market] Quote fetch failed for {symbol}: {exc}")
        return {}

    data = raw.get(instr, {})
    ohlc = data.get("ohlc", {})
    depth = data.get("depth", {})
    return {
        "symbol":             symbol,
        "last_price":         data.get("last_price", 0.0),
        "ohlc":               ohlc,
        "volume":             data.get("volume", 0),
        "bid":                data.get("depth", {}).get("buy", [{}])[0].get("price", 0.0),
        "ask":                data.get("depth", {}).get("sell", [{}])[0].get("price", 0.0),
        "oi":                 data.get("oi", 0),
        "last_trade_time":    data.get("last_trade_time"),
        "instrument_token":   data.get("instrument_token"),
        "buy_depth":          depth.get("buy", []),
        "sell_depth":         depth.get("sell", []),
        "change":             data.get("net_change", 0.0),
        "change_pct":         (
            (data.get("last_price", 0) - ohlc.get("close", 0)) / ohlc.get("close", 1) * 100
            if ohlc.get("close") else 0.0
        ),
    }


# ── 3. Historical candles ─────────────────────────────────────────────────────

async def get_kite_historical(
    symbol: str,
    from_date: str,
    to_date: str,
    interval: str = "60minute",
    session: AsyncSession | None = None,
) -> list[dict]:
    """Fetch OHLCV candles from Kite and return in save_candles_to_db-compatible format."""
    token = _get_token(symbol)
    if token is None and session:
        token = await _get_token_from_db(symbol, session)
    if token is None:
        logger.warning(f"[zerodha_market] No instrument token for {symbol}")
        return []

    global _kite_historical_available

    kite = get_kite_client()
    if not kite.access_token:
        return []

    if not _kite_historical_available:
        return []  # plan doesn't include historical API — use yfinance fallback

    kite_interval = _to_kite_interval(interval)
    # Map interval back to our timeframe string for the candles table
    tf_reverse = {v: k for k, v in _TF_MAP.items()}
    timeframe = tf_reverse.get(kite_interval, interval)

    try:
        raw = await kite.get_historical_data(token, from_date, to_date, kite_interval)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            _kite_historical_available = False
            logger.info(
                "[zerodha_market] Kite historical API returned 403 — "
                "historical data requires a paid Kite Connect subscription. "
                "Falling back to yfinance for all historical requests this session."
            )
        else:
            logger.warning(f"[zerodha_market] Historical fetch failed for {symbol}: {exc}")
        return []
    except Exception as exc:
        logger.warning(f"[zerodha_market] Historical fetch failed for {symbol}: {exc}")
        return []

    candles = []
    for c in raw:
        ts_raw = c["timestamp"]
        # Kite returns ISO-8601 string; convert to naive UTC datetime
        if isinstance(ts_raw, str):
            try:
                ts_ist = datetime.datetime.fromisoformat(ts_raw)
                if ts_ist.tzinfo is not None:
                    ts_utc = ts_ist.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                else:
                    # Kite historical data is in IST even without tzinfo marker
                    ts_utc = ts_ist.replace(tzinfo=_IST).astimezone(
                        datetime.timezone.utc
                    ).replace(tzinfo=None)
            except ValueError:
                continue
        else:
            ts_utc = ts_raw

        candles.append({
            "symbol":    symbol,
            "timeframe": timeframe,
            "open":      float(c["open"]),
            "high":      float(c["high"]),
            "low":       float(c["low"]),
            "close":     float(c["close"]),
            "volume":    float(c["volume"]),
            "timestamp": ts_utc,
        })
    return candles


# ── 4. Sync candles to DB ─────────────────────────────────────────────────────

async def sync_kite_candles_to_db(session: AsyncSession) -> dict:
    """Fetch 60 days of 1-hour candles for all NSE_TOKENS symbols and save to DB.

    This replaces yfinance for Indian stocks during market hours.
    """
    from crawler.price_feed import save_candles_to_db

    kite = get_kite_client()
    if not kite.access_token:
        return {"error": "No active Zerodha session"}

    today      = datetime.date.today()
    from_date  = (today - datetime.timedelta(days=60)).isoformat()
    to_date    = today.isoformat()
    interval   = "60minute"

    symbols_synced  = 0
    total_candles   = 0
    errors: list[str] = []

    # Kite caps the historical endpoint at 3 req/sec. After the startup
    # hydration NSE_TOKENS holds ~9.8k symbols, so a tight serial loop would
    # still burst above the limit on cached connections. Sleep 0.4s between
    # iterations → ~2.5 req/sec, with comfortable headroom.
    import asyncio as _asyncio
    for symbol in NSE_TOKENS:
        try:
            candles = await get_kite_historical(symbol, from_date, to_date, interval, session)
            if candles:
                saved = await save_candles_to_db(candles, session)
                total_candles  += saved
                symbols_synced += 1
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            logger.warning(f"[zerodha_market] Candle sync error for {symbol}: {exc}")
        await _asyncio.sleep(0.4)

    result = {
        "symbols_synced":    symbols_synced,
        "total_candles_saved": total_candles,
        "errors":            errors,
    }
    logger.info(
        f"[zerodha_market] Candle sync complete — "
        f"symbols={symbols_synced}  candles={total_candles}  errors={len(errors)}"
    )
    return result


# ── 5. Market depth ───────────────────────────────────────────────────────────

async def get_market_depth(symbol: str) -> dict:
    """Order book (top 5 bids and asks) for a symbol."""
    quote = await get_full_quote(symbol)
    return {
        "symbol":         symbol,
        "last_price":     quote.get("last_price", 0.0),
        "buy":            quote.get("buy_depth", []),
        "sell":           quote.get("sell_depth", []),
        "total_buy_qty":  sum(b.get("quantity", 0) for b in quote.get("buy_depth", [])),
        "total_sell_qty": sum(s.get("quantity", 0) for s in quote.get("sell_depth", [])),
    }
