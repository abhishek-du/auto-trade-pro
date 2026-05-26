"""KiteTicker live feed — populates LIVE_TICKS and syncs to PRICE_CACHE.

Wraps the official kiteconnect.KiteTicker.  All tick data is stored in
LIVE_TICKS (instrument_token → dict) and mirrored into PRICE_CACHE so
existing market-overview endpoints see real-time prices automatically.

Usage:
    from crawler.zerodha_ticker import start_kite_ticker, stop_kite_ticker
    start_kite_ticker()
"""

from __future__ import annotations

from typing import Any

from crawler.live_prices import PRICE_CACHE
from crawler.zerodha_market import INDEX_TOKENS, NSE_TOKENS, _TOKEN_TO_SYMBOL
from db.models import SimulationLog
from utils.config import settings
from utils.logger import logger

# ── Module-level state ────────────────────────────────────────────────────────

LIVE_TICKS: dict[int, dict[str, Any]] = {}
CONNECTED: bool = False


def token_to_symbol(token: int) -> str | None:
    """Reverse lookup instrument_token → '.NS' symbol."""
    return _TOKEN_TO_SYMBOL.get(token)


def get_live_tick(symbol: str) -> dict | None:
    """Get latest tick for a symbol (e.g. 'RELIANCE.NS' or 'RELIANCE')."""
    sym = symbol if symbol.endswith(".NS") or symbol.startswith("^") else f"{symbol}.NS"
    token = NSE_TOKENS.get(sym) or INDEX_TOKENS.get(sym)
    if token is None:
        return None
    return LIVE_TICKS.get(token)


def is_ticker_running() -> bool:
    """True when ws.is_connected returns True."""
    from crawler.zerodha_kite_lib import is_ticker_running as _ticker_running
    return CONNECTED and _ticker_running()


# ── Callback handlers ────────────────────────────────────────────────────────

def on_ticks(ws, ticks: list[dict]) -> None:
    """Update LIVE_TICKS and mirror into PRICE_CACHE."""
    for t in ticks:
        token = t.get("instrument_token")
        if token is None:
            continue
        LIVE_TICKS[token] = t
        sym = _TOKEN_TO_SYMBOL.get(token)
        if not sym:
            continue
        ohlc = t.get("ohlc", {}) or {}
        ltp = float(t.get("last_price", 0.0))
        prev = float(ohlc.get("close", 0.0)) or ltp
        change = ltp - prev if prev else 0.0
        change_pct = (change / prev * 100) if prev else 0.0
        existing = PRICE_CACHE.get(sym, {})
        existing.update({
            "symbol": sym,
            "price": ltp,
            "open": float(ohlc.get("open", 0.0)) or existing.get("open", 0.0),
            "high": float(ohlc.get("high", 0.0)) or existing.get("high", 0.0),
            "low":  float(ohlc.get("low", 0.0))  or existing.get("low", 0.0),
            "prev_close": prev,
            "volume": int(t.get("volume_traded", t.get("volume", 0)) or 0),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "data_source": "kite_ws",
        })
        PRICE_CACHE[sym] = existing


def on_connect(ws, response) -> None:
    """Subscribe all configured tokens in MODE_FULL on connect."""
    global CONNECTED
    CONNECTED = True
    tokens = list(set(NSE_TOKENS.values()) | set(INDEX_TOKENS.values()))
    try:
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        logger.info(f"[zerodha_ticker] Connected — subscribed {len(tokens)} tokens (MODE_FULL)")
    except Exception as exc:
        logger.warning(f"[zerodha_ticker] Subscribe failed: {exc}")


def on_close(ws, code, reason) -> None:
    global CONNECTED
    CONNECTED = False
    logger.warning(f"[zerodha_ticker] Closed code={code} reason={reason}")


def on_error(ws, code, reason) -> None:
    logger.error(f"[zerodha_ticker] Error code={code} reason={reason}")


def on_order_update(ws, data: dict) -> None:
    """Persist order postback for audit; can be extended to broadcast to clients."""
    logger.info(f"[zerodha_ticker] order_update: {data.get('order_id')} status={data.get('status')}")
    store_order_postback(data)


def store_order_postback(message: dict) -> None:
    """Best-effort persistence of postbacks via SimulationLog.

    Synchronous — uses a fresh DB connection.  Failures are swallowed so the
    ticker keeps running even if Postgres hiccups.
    """
    try:
        import asyncio
        from db.database import async_session_factory

        async def _save():
            async with async_session_factory() as sess:
                sess.add(SimulationLog(
                    event_type="KITE_ORDER_POSTBACK",
                    symbol=str(message.get("tradingsymbol", ""))[:20],
                    message=f"Order {message.get('order_id')} → {message.get('status')}",
                    data=dict(message) if isinstance(message, dict) else {"raw": str(message)},
                ))
                await sess.commit()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_save())
            else:
                loop.run_until_complete(_save())
        except RuntimeError:
            asyncio.run(_save())
    except Exception as exc:
        logger.debug(f"[zerodha_ticker] postback store failed: {exc}")


# ── Lifecycle ────────────────────────────────────────────────────────────────

def start_kite_ticker() -> bool:
    """Start the official KiteTicker.  Returns True on dispatch (connection is async)."""
    if not settings.ZERODHA_ACCESS_TOKEN:
        logger.info("[zerodha_ticker] No access token — cannot start ticker")
        return False
    from crawler.zerodha_kite_lib import start_ticker
    start_ticker(
        on_ticks=on_ticks,
        on_connect=on_connect,
        on_close=on_close,
        on_error=on_error,
        on_order_update=on_order_update,
    )
    return True


def stop_kite_ticker() -> None:
    global CONNECTED
    from crawler.zerodha_kite_lib import stop_ticker
    stop_ticker()
    CONNECTED = False
    logger.info("[zerodha_ticker] Stopped")
