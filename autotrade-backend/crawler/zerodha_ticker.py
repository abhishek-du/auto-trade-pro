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
    """Subscribe the configured watchlist tokens on connect.

    Kite's WebSocket caps subscriptions per connection:
      MODE_LTP    — ~3000 instruments  (least data, only last price)
      MODE_QUOTE  — ~3000 instruments  (LTP + OHLC + volume + change)
      MODE_FULL   — ~1000 instruments  (everything incl. 5-level depth)

    Since the post-startup hydration loads ~9,800 tokens but we only
    actually display ~80–100 of them, subscribe in two tiers:
      1. The watchlist (large + mid + extras) + indices in MODE_QUOTE.
      2. Anything else, capped at the remaining budget, in MODE_LTP.

    This stops the "Can't subscribe to more than 4000 instruments" error
    AND keeps the rich quote data flowing for the symbols the UI cares
    about.
    """
    global CONNECTED
    CONNECTED = True

    _QUOTE_BUDGET = 3000   # safe headroom below Kite's published cap
    _LTP_TOTAL_CAP = 3000  # MODE_LTP shares the same connection budget

    from utils.config import settings as _settings

    # Build the priority list: hand-picked symbols first
    priority_symbols: list[str] = []
    seen: set[str] = set()
    for src in (
        getattr(_settings, "WATCHLIST_NSE_LARGE_CAP", []),
        getattr(_settings, "WATCHLIST_NSE_MID_CAP", []),
    ):
        for s in src:
            bare = (s or "").replace(".NS", "").strip().upper()
            if bare and bare not in seen:
                seen.add(bare)
                priority_symbols.append(f"{bare}.NS")
    # Include all indices (NIFTY 50 / Bank NIFTY etc.) — small, always useful.
    for idx_sym in INDEX_TOKENS:
        if idx_sym not in seen:
            seen.add(idx_sym)
            priority_symbols.append(idx_sym)

    priority_tokens: list[int] = []
    for sym in priority_symbols:
        tok = NSE_TOKENS.get(sym) or INDEX_TOKENS.get(sym)
        if tok and tok not in priority_tokens:
            priority_tokens.append(tok)
    priority_tokens = priority_tokens[:_QUOTE_BUDGET]

    # Fill remaining budget from the hydrated map (everything else NSE has).
    remaining_budget = max(0, _LTP_TOTAL_CAP - len(priority_tokens))
    bulk_tokens: list[int] = []
    if remaining_budget > 0:
        priority_set = set(priority_tokens)
        for tok in NSE_TOKENS.values():
            if tok in priority_set:
                continue
            bulk_tokens.append(tok)
            if len(bulk_tokens) >= remaining_budget:
                break

    try:
        if priority_tokens:
            ws.subscribe(priority_tokens)
            ws.set_mode(ws.MODE_QUOTE, priority_tokens)
        if bulk_tokens:
            ws.subscribe(bulk_tokens)
            ws.set_mode(ws.MODE_LTP, bulk_tokens)
        logger.info(
            f"[zerodha_ticker] Connected — subscribed "
            f"{len(priority_tokens)} QUOTE + {len(bulk_tokens)} LTP "
            f"(of {len(NSE_TOKENS)} hydrated tokens)"
        )
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
