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

# Open positions get QUOTE-mode priority. Populated at ticker start and whenever
# a new position is opened so live PnL updates are driven by Kite ticks.
_OPEN_POSITION_SYMBOLS: set[str] = set()
_active_ws = None   # reference to the running WebSocket for dynamic subscribe


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


def _normalise_symbol(s: str) -> str:
    """Equity symbols need .NS suffix; NFO option/future symbols must not get it."""
    if not s:
        return s
    # NFO symbols: end with CE/PE (options) or contain FUT
    if s.endswith("CE") or s.endswith("PE") or "FUT" in s:
        return s
    return s if s.endswith(".NS") else f"{s}.NS"


def set_open_position_symbols(symbols: set[str]) -> None:
    """Replace the open-position symbol set and resubscribe if ticker is live."""
    global _OPEN_POSITION_SYMBOLS
    _OPEN_POSITION_SYMBOLS = {_normalise_symbol(s) for s in symbols if s}
    logger.info(f"[zerodha_ticker] open positions set: {len(_OPEN_POSITION_SYMBOLS)} symbols")
    _resubscribe_open_positions()


def subscribe_open_position(symbol: str) -> None:
    """Add a single symbol (new trade opened) and subscribe it immediately."""
    sym = _normalise_symbol(symbol)
    _OPEN_POSITION_SYMBOLS.add(sym)
    _resubscribe_open_positions(symbols={sym})
    logger.info(f"[zerodha_ticker] subscribed new position: {sym}")


def _resubscribe_open_positions(symbols: set[str] | None = None) -> None:
    """Subscribe (or resubscribe) open position tokens in QUOTE mode on the live ws."""
    if not CONNECTED or _active_ws is None:
        return
    targets = symbols if symbols is not None else _OPEN_POSITION_SYMBOLS
    try:
        from crawler.zerodha_instruments import get_token as _get_token
    except Exception:
        _get_token = None  # type: ignore[assignment]

    tokens = []
    for sym in targets:
        tok = NSE_TOKENS.get(sym) or INDEX_TOKENS.get(sym)
        if not tok and _get_token is not None:
            tok = _get_token(sym)
        # NFO options/futures: look up token directly from the hydrated NSE_TOKENS
        # (which includes NFO instruments after hydrate_tokens_from_db runs).
        # The key is stored without .NS for NFO symbols.
        if not tok:
            tok = NSE_TOKENS.get(sym.replace(".NS", "")) or NSE_TOKENS.get(sym)
        if tok:
            _TOKEN_TO_SYMBOL[int(tok)] = sym
            tokens.append(int(tok))
    if not tokens:
        return
    try:
        _active_ws.subscribe(tokens)
        _active_ws.set_mode(_active_ws.MODE_QUOTE, tokens)
        logger.info(f"[zerodha_ticker] resubscribed {len(tokens)} open position tokens (QUOTE)")
    except Exception as exc:
        logger.warning(f"[zerodha_ticker] resubscribe failed: {exc}")


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
      1. Open positions + watchlist + indices in MODE_QUOTE.
      2. Anything else, capped at the remaining budget, in MODE_LTP.

    This stops the "Can't subscribe to more than 4000 instruments" error
    AND keeps the rich quote data flowing for the symbols the UI cares
    about.
    """
    global CONNECTED, _active_ws
    CONNECTED = True
    _active_ws = ws

    _QUOTE_BUDGET = 3000   # safe headroom below Kite's published cap
    _LTP_TOTAL_CAP = 3000  # MODE_LTP shares the same connection budget

    from utils.config import settings as _settings

    # Build the priority list: open positions first, then watchlist, then indices.
    priority_symbols: list[str] = []
    seen: set[str] = set()

    # 1. Open positions — must have QUOTE-mode data for live PnL.
    for sym in sorted(_OPEN_POSITION_SYMBOLS):
        bare = sym.replace(".NS", "").strip().upper()
        if bare and bare not in seen:
            seen.add(bare)
            priority_symbols.append(sym)

    # 2. Watchlist symbols.
    for src in (
        getattr(_settings, "WATCHLIST_NSE_LARGE_CAP", []),
        getattr(_settings, "WATCHLIST_NSE_MID_CAP", []),
    ):
        for s in src:
            bare = (s or "").replace(".NS", "").strip().upper()
            if bare and bare not in seen:
                seen.add(bare)
                priority_symbols.append(f"{bare}.NS")

    # 3. Include all indices (NIFTY 50 / Bank NIFTY etc.) — small, always useful.
    for idx_sym in INDEX_TOKENS:
        if idx_sym not in seen:
            seen.add(idx_sym)
            priority_symbols.append(idx_sym)

    # Import full instrument cache for mid/small-cap symbols not in NSE_TOKENS.
    try:
        from crawler.zerodha_instruments import get_token as _get_token
    except Exception:
        _get_token = None  # type: ignore[assignment]

    priority_tokens: list[int] = []
    for sym in priority_symbols:
        tok = NSE_TOKENS.get(sym) or INDEX_TOKENS.get(sym)
        if not tok and _get_token is not None:
            tok = _get_token(sym)
            if tok:
                # Register in reverse-lookup so on_ticks can resolve this token back
                # to a symbol and mirror it into PRICE_CACHE.
                _TOKEN_TO_SYMBOL[tok] = sym
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
            f"(of {len(NSE_TOKENS)} hydrated tokens) "
            f"[{len(_OPEN_POSITION_SYMBOLS)} open positions in QUOTE]"
        )
    except Exception as exc:
        logger.warning(f"[zerodha_ticker] Subscribe failed: {exc}")


def on_close(ws, code, reason) -> None:
    global CONNECTED, _active_ws
    CONNECTED = False
    _active_ws = None
    logger.warning(f"[zerodha_ticker] Closed code={code} reason={reason}")


def on_error(ws, code, reason) -> None:
    logger.error(f"[zerodha_ticker] Error code={code} reason={reason}")
    # 403 = expired/invalid access token.  Reconnecting is pointless without a
    # fresh token — stop the ticker to silence the retry storm and prompt re-login.
    if "403" in str(reason) or "Forbidden" in str(reason):
        logger.warning(
            "[zerodha_ticker] 403 Forbidden — access token expired. "
            "Re-login at /zerodha in the app to resume live feed."
        )
        stop_kite_ticker()


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
        from db.database import AsyncSessionLocal as async_session_factory

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

    # Pre-populate open position symbols so on_connect subscribes them in QUOTE mode.
    _load_open_positions_sync()

    from crawler.zerodha_kite_lib import start_ticker
    start_ticker(
        on_ticks=on_ticks,
        on_connect=on_connect,
        on_close=on_close,
        on_error=on_error,
        on_order_update=on_order_update,
    )
    return True


def _load_open_positions_sync() -> None:
    """Fetch open position symbols from DB and populate _OPEN_POSITION_SYMBOLS.

    Works in both sync (celery tasks) and async (FastAPI) contexts:
    - No running loop  → asyncio.run() fetches and sets synchronously.
    - Running loop     → caller is expected to have called set_open_position_symbols()
                         before start_kite_ticker(); we skip to avoid deadlock.
    """
    import asyncio
    from sqlalchemy import text

    async def _fetch():
        from db.database import AsyncSessionLocal as async_session_factory
        async with async_session_factory() as sess:
            r = await sess.execute(text("SELECT DISTINCT symbol FROM open_positions"))
            return {row[0] for row in r.fetchall()}

    try:
        asyncio.get_running_loop()
        # Inside FastAPI event loop — caller must pre-populate via set_open_position_symbols().
        logger.debug("[zerodha_ticker] async context detected — skipping sync DB fetch")
    except RuntimeError:
        # No running loop (celery worker / script) — safe to block.
        try:
            syms = asyncio.run(_fetch())
            set_open_position_symbols(syms)
        except Exception as exc:
            logger.debug(f"[zerodha_ticker] open position preload failed: {exc}")


def stop_kite_ticker() -> None:
    global CONNECTED
    from crawler.zerodha_kite_lib import stop_ticker
    stop_ticker()
    CONNECTED = False
    logger.info("[zerodha_ticker] Stopped")
