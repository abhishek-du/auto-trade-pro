"""Zerodha KiteConnect library wrapper — supplements the raw httpx client.

This module wraps the official `kiteconnect` PyPI library to expose features
that the raw httpx client (crawler/zerodha_client.py) does not currently
implement: GTT triggers, mutual fund orders, alerts, order margins preview,
and the official KiteTicker WebSocket.

The httpx client remains the primary path for everything it already handles
(profile, holdings, positions, orders, quote, historical) because it is fully
async-native.  This wrapper is invoked via `asyncio.to_thread()` from callers
that need the additional features.

SAFETY: every method that places a real order checks
`settings.ZERODHA_PAPER_MODE` first and raises RuntimeError if True.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from utils.config import settings
from utils.logger import logger


# ── Module-level singletons ──────────────────────────────────────────────────

_kite: Any | None = None
_ticker: Any | None = None

# ── Live token source of truth (fixes the stale-singleton bug) ────────────────
# The Kite access_token expires daily at 06:00 IST and is refreshed (to .env) by
# a SEPARATE process at 08:00. Long-running processes (celery worker/beat,
# uvicorn) cached the token at boot and never re-read it, so every Kite REST call
# failed all day until restart. current_access_token() re-reads .env ONLY when
# the file's mtime changes (≈1 stat/call), so a refresh propagates to every
# process within seconds — no restart needed.
_ENV_PATH = Path(__file__).parent.parent / ".env"
_TOKEN_CACHE: dict = {"mtime": -1.0, "token": ""}


def current_access_token() -> str:
    """Authoritative access_token, re-read from .env only when it changes."""
    try:
        mt = _ENV_PATH.stat().st_mtime
        if mt != _TOKEN_CACHE["mtime"]:
            tok = ""
            for line in _ENV_PATH.read_text().splitlines():
                if line.startswith("ZERODHA_ACCESS_TOKEN="):
                    tok = line.split("=", 1)[1].strip()
                    break
            _TOKEN_CACHE["mtime"] = mt
            _TOKEN_CACHE["token"] = tok
            if tok and tok != settings.ZERODHA_ACCESS_TOKEN:
                settings.ZERODHA_ACCESS_TOKEN = tok   # keep process-wide value in sync
        return _TOKEN_CACHE["token"] or settings.ZERODHA_ACCESS_TOKEN
    except Exception:
        return settings.ZERODHA_ACCESS_TOKEN


def invalidate_token_cache() -> None:
    """Force the next current_access_token() to re-read .env (used on auth error)."""
    _TOKEN_CACHE["mtime"] = -1.0


def is_token_error(exc: Exception) -> bool:
    """True if an exception looks like a Kite auth/token failure."""
    s = f"{type(exc).__name__} {exc}".lower()
    return "tokenexception" in s or ("incorrect" in s and ("token" in s or "api_key" in s))


def _write_env(key: str, value: str) -> None:
    """Idempotently set KEY=VALUE in the .env file (relative to crawler/)."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def get_kite():
    """Return the cached KiteConnect client, always carrying the CURRENT token.
    Re-applies the token from .env whenever it has changed, so a daily refresh is
    picked up without a process restart."""
    global _kite
    if _kite is None:
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise RuntimeError(
                "kiteconnect library not installed — run `pip install kiteconnect`"
            ) from exc
        _kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
    tok = current_access_token()
    if tok and getattr(_kite, "access_token", None) != tok:
        _kite.set_access_token(tok)
    return _kite


def reset_kite() -> None:
    """Drop the cached client and ticker so credentials reload next call."""
    global _kite, _ticker
    _kite = None
    _ticker = None


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_login_url() -> str:
    return get_kite().login_url()


def generate_session(request_token: str) -> dict:
    """Exchange request_token for access_token and persist it to .env."""
    kite = get_kite()
    data = kite.generate_session(request_token, api_secret=settings.ZERODHA_API_SECRET)
    access = data["access_token"]
    kite.set_access_token(access)
    settings.ZERODHA_ACCESS_TOKEN = access
    settings.ZERODHA_ENABLED = True
    _write_env("ZERODHA_ACCESS_TOKEN", access)
    _write_env("ZERODHA_ENABLED", "true")
    logger.info(f"[zerodha_kite_lib] Session generated for {data.get('user_name')}")
    return data


def invalidate_session() -> bool:
    try:
        kite = get_kite()
        kite.invalidate_access_token(access_token=settings.ZERODHA_ACCESS_TOKEN)
    except Exception as exc:
        logger.warning(f"[zerodha_kite_lib] invalidate_access_token failed: {exc}")
    settings.ZERODHA_ACCESS_TOKEN = ""
    settings.ZERODHA_ENABLED = False
    _write_env("ZERODHA_ACCESS_TOKEN", "")
    _write_env("ZERODHA_ENABLED", "false")
    reset_kite()
    return True


def is_connected() -> bool:
    if not settings.ZERODHA_ACCESS_TOKEN:
        return False
    try:
        get_kite().profile()
        return True
    except Exception:
        return False


def verify_token() -> bool:
    return is_connected()


# ── User / margins ────────────────────────────────────────────────────────────

def get_profile() -> dict:
    return get_kite().profile()


def get_margins(segment: str | None = None) -> dict:
    return get_kite().margins(segment=segment)


# ── Orders ────────────────────────────────────────────────────────────────────

def place_order(
    *,
    variety: str = "regular",
    tradingsymbol: str,
    exchange: str = "NSE",
    transaction_type: str,
    quantity: int,
    product: str = "CNC",
    order_type: str = "MARKET",
    price: float | None = None,
    validity: str | None = None,
    validity_ttl: int | None = None,
    disclosed_quantity: int | None = None,
    trigger_price: float | None = None,
    iceberg_legs: int | None = None,
    iceberg_quantity: int | None = None,
    auction_number: str | None = None,
    market_protection: int | None = None,
    autoslice: bool | None = None,
    tag: str | None = None,
) -> str:
    """Place a real order via Kite.  Raises if ZERODHA_PAPER_MODE is True."""
    if settings.ZERODHA_PAPER_MODE:
        raise RuntimeError(
            "Real order blocked — ZERODHA_PAPER_MODE is True. "
            "Set ZERODHA_PAPER_MODE=false in .env to enable real trading."
        )

    kw: dict[str, Any] = {
        "variety": variety,
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "transaction_type": transaction_type,
        "quantity": quantity,
        "product": product,
        "order_type": order_type,
    }
    for k, v in (
        ("price", price),
        ("validity", validity),
        ("validity_ttl", validity_ttl),
        ("disclosed_quantity", disclosed_quantity),
        ("trigger_price", trigger_price),
        ("iceberg_legs", iceberg_legs),
        ("iceberg_quantity", iceberg_quantity),
        ("auction_number", auction_number),
        ("market_protection", market_protection),
        ("tag", tag),
    ):
        if v is not None:
            kw[k] = v
    if autoslice is not None:
        kw["autoslice"] = autoslice

    logger.critical(
        f"[zerodha_kite_lib] REAL ORDER PLACEMENT: {transaction_type} "
        f"{quantity} {tradingsymbol} @ {price or 'MKT'} (variety={variety}, tag={tag})"
    )
    order_id = get_kite().place_order(**kw)
    logger.critical(f"[zerodha_kite_lib] REAL ORDER PLACED — order_id={order_id}")
    return order_id


def modify_order(
    order_id: str,
    *,
    variety: str = "regular",
    quantity: int | None = None,
    price: float | None = None,
    order_type: str | None = None,
    trigger_price: float | None = None,
    validity: str | None = None,
    disclosed_quantity: int | None = None,
) -> str:
    if settings.ZERODHA_PAPER_MODE:
        raise RuntimeError("Modify blocked — ZERODHA_PAPER_MODE is True.")
    kw: dict[str, Any] = {"variety": variety, "order_id": order_id}
    for k, v in (
        ("quantity", quantity),
        ("price", price),
        ("order_type", order_type),
        ("trigger_price", trigger_price),
        ("validity", validity),
        ("disclosed_quantity", disclosed_quantity),
    ):
        if v is not None:
            kw[k] = v
    return get_kite().modify_order(**kw)


def cancel_order(order_id: str, variety: str = "regular") -> str:
    return get_kite().cancel_order(variety=variety, order_id=order_id)


def get_orders() -> list[dict]:
    return get_kite().orders()


def get_order_history(order_id: str) -> list[dict]:
    return get_kite().order_history(order_id=order_id)


def get_trades() -> list[dict]:
    return get_kite().trades()


def get_order_trades(order_id: str) -> list[dict]:
    return get_kite().order_trades(order_id=order_id)


# ── GTT ───────────────────────────────────────────────────────────────────────

def _gtt_orders_block(symbol: str, exchange: str, qty: int, price: float, txn: str, product: str) -> list[dict]:
    return [{
        "exchange": exchange,
        "tradingsymbol": symbol,
        "transaction_type": txn,
        "quantity": qty,
        "order_type": "LIMIT",
        "product": product,
        "price": price,
    }]


def place_gtt_single(
    *,
    tradingsymbol: str,
    exchange: str = "NSE",
    last_price: float,
    trigger_price: float,
    quantity: int,
    order_price: float,
    transaction_type: str = "BUY",
    product: str = "CNC",
) -> dict:
    if settings.ZERODHA_PAPER_MODE:
        raise RuntimeError("GTT blocked — ZERODHA_PAPER_MODE is True.")
    return get_kite().place_gtt(
        trigger_type=get_kite().GTT_TYPE_SINGLE,
        tradingsymbol=tradingsymbol,
        exchange=exchange,
        trigger_values=[trigger_price],
        last_price=last_price,
        orders=_gtt_orders_block(tradingsymbol, exchange, quantity, order_price, transaction_type, product),
    )


def place_gtt_oco(
    *,
    tradingsymbol: str,
    exchange: str = "NSE",
    last_price: float,
    stoploss_trigger: float,
    stoploss_price: float,
    target_trigger: float,
    target_price: float,
    quantity: int,
    product: str = "CNC",
) -> dict:
    """OCO bracket — SELL leg at stop-loss + SELL leg at target."""
    if settings.ZERODHA_PAPER_MODE:
        raise RuntimeError("GTT blocked — ZERODHA_PAPER_MODE is True.")
    kite = get_kite()
    orders = [
        {
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": "SELL",
            "quantity": quantity,
            "order_type": "LIMIT",
            "product": product,
            "price": stoploss_price,
        },
        {
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": "SELL",
            "quantity": quantity,
            "order_type": "LIMIT",
            "product": product,
            "price": target_price,
        },
    ]
    return kite.place_gtt(
        trigger_type=kite.GTT_TYPE_OCO,
        tradingsymbol=tradingsymbol,
        exchange=exchange,
        trigger_values=[stoploss_trigger, target_trigger],
        last_price=last_price,
        orders=orders,
    )


def get_gtts() -> list[dict]:
    return get_kite().get_gtts()


def get_gtt(trigger_id: int) -> dict:
    return get_kite().get_gtt(trigger_id=int(trigger_id))


def modify_gtt(
    trigger_id: int,
    *,
    trigger_type: str,
    tradingsymbol: str,
    exchange: str,
    trigger_values: list[float],
    last_price: float,
    orders: list[dict],
) -> dict:
    return get_kite().modify_gtt(
        trigger_id=int(trigger_id),
        trigger_type=trigger_type,
        tradingsymbol=tradingsymbol,
        exchange=exchange,
        trigger_values=trigger_values,
        last_price=last_price,
        orders=orders,
    )


def delete_gtt(trigger_id: int) -> dict:
    return get_kite().delete_gtt(trigger_id=int(trigger_id))


# ── Portfolio ─────────────────────────────────────────────────────────────────

def get_holdings() -> list[dict]:
    return get_kite().holdings()


def get_positions() -> dict:
    return get_kite().positions()


def convert_position(
    *,
    exchange: str,
    tradingsymbol: str,
    transaction_type: str,
    position_type: str,
    quantity: int,
    old_product: str,
    new_product: str,
) -> bool:
    return get_kite().convert_position(
        exchange=exchange,
        tradingsymbol=tradingsymbol,
        transaction_type=transaction_type,
        position_type=position_type,
        quantity=quantity,
        old_product=old_product,
        new_product=new_product,
    )


# ── Market data ───────────────────────────────────────────────────────────────

def get_quote(instruments: Iterable[str]) -> dict:
    return get_kite().quote(list(instruments))


def get_ohlc(instruments: Iterable[str]) -> dict:
    return get_kite().ohlc(list(instruments))


def get_ltp(instruments: Iterable[str]) -> dict:
    return get_kite().ltp(list(instruments))


def get_instruments(exchange: str | None = None) -> list[dict]:
    return get_kite().instruments(exchange=exchange)


def get_historical_data(
    *,
    instrument_token: int,
    from_date,
    to_date,
    interval: str,
    continuous: bool = False,
    oi: bool = False,
) -> list[dict]:
    def _call():
        return get_kite().historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            continuous=continuous,
            oi=oi,
        )
    try:
        return _call()
    except Exception as exc:
        # Reactive self-heal: a token error force-reloads from .env and retries
        # once, so a refreshed token is adopted even without a restart.
        if is_token_error(exc):
            invalidate_token_cache()
            get_kite()  # re-applies the fresh token to the singleton
            return _call()
        raise


# ── Margins preview ───────────────────────────────────────────────────────────

def get_order_margins(orders: list[dict]) -> list[dict]:
    return get_kite().order_margins(params=orders)


def get_basket_margins(orders: list[dict], consider_positions: bool = True) -> dict:
    return get_kite().basket_order_margins(params=orders, consider_positions=consider_positions)


def get_virtual_contract_note(orders: list[dict]) -> list[dict]:
    return get_kite().virtual_contract_note(params=orders)


# ── Mutual Funds ──────────────────────────────────────────────────────────────

def get_mf_instruments() -> list[dict]:
    return get_kite().mf_instruments()


def place_mf_order(
    *,
    tradingsymbol: str,
    transaction_type: str = "BUY",
    amount: float | None = None,
    quantity: float | None = None,
    tag: str | None = None,
) -> str:
    if settings.ZERODHA_PAPER_MODE:
        raise RuntimeError("MF order blocked — ZERODHA_PAPER_MODE is True.")
    kw: dict[str, Any] = {
        "tradingsymbol": tradingsymbol,
        "transaction_type": transaction_type,
    }
    if amount is not None:
        kw["amount"] = amount
    if quantity is not None:
        kw["quantity"] = quantity
    if tag is not None:
        kw["tag"] = tag
    return get_kite().place_mf_order(**kw)


def cancel_mf_order(order_id: str) -> dict:
    return get_kite().cancel_mf_order(order_id=order_id)


def get_mf_orders(order_id: str | None = None) -> list[dict] | dict:
    return get_kite().mf_orders(order_id=order_id) if order_id else get_kite().mf_orders()


def get_mf_order(order_id: str) -> dict:
    return get_kite().mf_orders(order_id=order_id)


def get_mf_holdings() -> list[dict]:
    return get_kite().mf_holdings()


# ── SIP ───────────────────────────────────────────────────────────────────────

def place_mf_sip(
    *,
    tradingsymbol: str,
    amount: float,
    instalments: int,
    frequency: str = "monthly",
    initial_amount: float | None = None,
    instalment_day: int | None = None,
    tag: str | None = None,
) -> dict:
    if settings.ZERODHA_PAPER_MODE:
        raise RuntimeError("SIP blocked — ZERODHA_PAPER_MODE is True.")
    kw: dict[str, Any] = {
        "tradingsymbol": tradingsymbol,
        "amount": amount,
        "instalments": instalments,
        "frequency": frequency,
    }
    if initial_amount is not None:
        kw["initial_amount"] = initial_amount
    if instalment_day is not None:
        kw["instalment_day"] = instalment_day
    if tag is not None:
        kw["tag"] = tag
    return get_kite().place_mf_sip(**kw)


def modify_mf_sip(
    sip_id: str,
    *,
    amount: float | None = None,
    status: str | None = None,
    instalments: int | None = None,
    frequency: str | None = None,
    instalment_day: int | None = None,
) -> dict:
    kw: dict[str, Any] = {"sip_id": sip_id}
    for k, v in (
        ("amount", amount),
        ("status", status),
        ("instalments", instalments),
        ("frequency", frequency),
        ("instalment_day", instalment_day),
    ):
        if v is not None:
            kw[k] = v
    return get_kite().modify_mf_sip(**kw)


def cancel_mf_sip(sip_id: str) -> dict:
    return get_kite().cancel_mf_sip(sip_id=sip_id)


def get_mf_sips(sip_id: str | None = None) -> list[dict] | dict:
    return get_kite().mf_sips(sip_id=sip_id) if sip_id else get_kite().mf_sips()


def get_mf_sip(sip_id: str) -> dict:
    return get_kite().mf_sips(sip_id=sip_id)


# ── Alerts ────────────────────────────────────────────────────────────────────

def get_alerts() -> list[dict]:
    fn = getattr(get_kite(), "get_alerts", None)
    return fn() if fn else []


def get_alert(alert_id: str) -> dict:
    fn = getattr(get_kite(), "get_alert", None)
    return fn(alert_id=alert_id) if fn else {}


def place_alert(**kwargs) -> dict:
    fn = getattr(get_kite(), "create_alert", None) or getattr(get_kite(), "place_alert", None)
    if not fn:
        raise RuntimeError("Alerts API not available in installed kiteconnect version")
    return fn(**kwargs)


def modify_alert(alert_id: str, **kwargs) -> dict:
    fn = getattr(get_kite(), "modify_alert", None)
    if not fn:
        raise RuntimeError("modify_alert not available in installed kiteconnect version")
    return fn(alert_id=alert_id, **kwargs)


def delete_alert(alert_id: str) -> dict:
    fn = getattr(get_kite(), "delete_alert", None) or getattr(get_kite(), "delete_alerts", None)
    if not fn:
        raise RuntimeError("delete_alert not available in installed kiteconnect version")
    try:
        return fn(alert_id=alert_id)
    except TypeError:
        return fn(alert_ids=[alert_id])


# ── KiteTicker (official WebSocket) ───────────────────────────────────────────

def start_ticker(on_ticks=None, on_connect=None, on_close=None, on_error=None, on_order_update=None):
    """Start the official KiteTicker.  Callbacks default to module helpers in
    crawler.zerodha_ticker (the supplementary ticker module)."""
    global _ticker
    try:
        from kiteconnect import KiteTicker
    except ImportError as exc:
        raise RuntimeError("kiteconnect library not installed") from exc

    if _ticker is not None:
        return _ticker

    _ticker = KiteTicker(settings.ZERODHA_API_KEY, settings.ZERODHA_ACCESS_TOKEN)
    if on_ticks:        _ticker.on_ticks = on_ticks
    if on_connect:      _ticker.on_connect = on_connect
    if on_close:        _ticker.on_close = on_close
    if on_error:        _ticker.on_error = on_error
    if on_order_update: _ticker.on_order_update = on_order_update
    _ticker.connect(threaded=True)
    logger.info("[zerodha_kite_lib] KiteTicker started (threaded mode)")
    return _ticker


def subscribe_tokens(tokens: Iterable[int], mode: str = "full") -> None:
    if _ticker is None:
        raise RuntimeError("Ticker not started — call start_ticker() first")
    toks = list(tokens)
    _ticker.subscribe(toks)
    mode_const = {
        "ltp": _ticker.MODE_LTP,
        "quote": _ticker.MODE_QUOTE,
        "full": _ticker.MODE_FULL,
    }.get(mode.lower(), _ticker.MODE_FULL)
    _ticker.set_mode(mode_const, toks)


def unsubscribe_tokens(tokens: Iterable[int]) -> None:
    if _ticker is None:
        return
    _ticker.unsubscribe(list(tokens))


def stop_ticker() -> None:
    global _ticker
    if _ticker is not None:
        try:
            _ticker.close()
        except Exception as exc:
            logger.warning(f"[zerodha_kite_lib] ticker close failed: {exc}")
        _ticker = None


def is_ticker_running() -> bool:
    return _ticker is not None and getattr(_ticker, "is_connected", lambda: False)()
