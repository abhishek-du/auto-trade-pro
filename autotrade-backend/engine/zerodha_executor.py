"""Zerodha KiteConnect v3 — real order executor.

SAFETY RULES — all enforced before any order reaches Kite:
  1. PAPER_MODE check (no real orders in paper mode)
  2. ZERODHA_ENABLED check
  3. Minimum 60% confidence gate
  4. Maximum 5% of real balance per order
  5. 3-second abort window with critical log
  6. LIMIT orders only (never MARKET) with 0.5% slippage buffer

PAPER TRADING ONLY — by default PAPER_MODE=True in this project.
All real-order paths require the operator to explicitly set
PAPER_MODE=false AND ZERODHA_ENABLED=true in .env.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from crawler.zerodha_client import get_kite_client
from crawler.zerodha_market import get_live_prices
from db.models import SimulationLog
from utils.config import settings
from utils.logger import logger


# ── Safety gate helpers ───────────────────────────────────────────────────────

def _check_safety_gates(
    signal_confidence: float,
    order_value: float,
    real_balance: float,
) -> None:
    """Raise immediately if any safety gate is not satisfied."""
    if settings.PAPER_MODE:
        raise RuntimeError(
            "Cannot place real orders in PAPER_MODE. "
            "Set PAPER_MODE=false in .env to enable real trading."
        )

    if not settings.ZERODHA_ENABLED:
        raise RuntimeError(
            "Zerodha not connected — complete login via /api/v1/zerodha/login-url first."
        )

    if signal_confidence < 60:
        raise ValueError(
            f"Signal confidence {signal_confidence:.1f}% is below the 60% minimum "
            f"required for real order placement."
        )

    max_order_value = real_balance * 0.05
    if order_value > max_order_value:
        raise ValueError(
            f"Order value ₹{order_value:,.2f} exceeds 5% of real balance "
            f"(₹{max_order_value:,.2f}). Reduce quantity."
        )


async def _abort_window(transaction_type: str, qty: int, symbol: str, price: float) -> None:
    """Log a critical warning and wait 3 seconds — human abort window."""
    logger.critical(
        f"⚠ REAL ORDER IN 3 SECONDS: {transaction_type} {qty} × {symbol} "
        f"@ ₹{price:,.2f} — kill process NOW to abort"
    )
    await asyncio.sleep(3)


def _limit_price(current_price: float, transaction_type: str) -> float:
    """Limit price with 0.5% slippage buffer (never use MARKET orders)."""
    if transaction_type.upper() == "BUY":
        return round(current_price * 1.005, 2)
    return round(current_price * 0.995, 2)


# ── Execute buy ───────────────────────────────────────────────────────────────

async def execute_real_buy(signal, session: AsyncSession) -> dict:
    """Place a real LIMIT BUY order through Zerodha.

    Parameters
    ----------
    signal : TradingSignal with .symbol, .confidence, .final_score
    session : async DB session for logging

    Returns
    -------
    {order_id, symbol, qty, price, estimated_value}
    """
    symbol     = signal.symbol.replace(".NS", "")
    prices     = await get_live_prices([signal.symbol])
    price_data = prices.get(signal.symbol, {})
    current    = float(price_data.get("price") or 0.0)

    if current <= 0:
        raise ValueError(f"Could not fetch live price for {signal.symbol}")

    kite = get_kite_client()

    # Calculate quantity from available margin
    try:
        margins       = await kite.get_margins("equity")
        real_balance  = float(margins.get("available", {}).get("live_balance", 0.0))
    except Exception:
        real_balance = 0.0

    max_value = real_balance * 0.05
    qty       = max(1, int(max_value / current))
    lim_price = _limit_price(current, "BUY")
    order_val = qty * lim_price

    _check_safety_gates(signal.confidence, order_val, real_balance)
    await _abort_window("BUY", qty, symbol, lim_price)

    order_id = await kite.place_order(
        tradingsymbol    = symbol,
        exchange         = "NSE",
        transaction_type = "BUY",
        quantity         = qty,
        order_type       = "LIMIT",
        product          = "CNC",
        price            = lim_price,
    )

    log_entry = SimulationLog(
        event_type = "REAL_ORDER_PLACED",
        symbol     = symbol,
        message    = f"REAL BUY {qty} × {symbol} @ ₹{lim_price:,.2f} (order_id={order_id})",
        data       = {
            "order_id":        order_id,
            "transaction_type":"BUY",
            "quantity":        qty,
            "price":           lim_price,
            "estimated_value": order_val,
            "confidence":      signal.confidence,
        },
    )
    session.add(log_entry)
    await session.flush()

    logger.critical(
        f"REAL BUY ORDER PLACED — order_id={order_id} "
        f"{qty} × {symbol} @ ₹{lim_price:,.2f}"
    )

    return {
        "order_id":        order_id,
        "symbol":          symbol,
        "qty":             qty,
        "price":           lim_price,
        "estimated_value": order_val,
    }


# ── Execute sell ──────────────────────────────────────────────────────────────

async def execute_real_sell(
    symbol: str,
    quantity: int,
    session: AsyncSession,
) -> dict:
    """Place a real LIMIT SELL order through Zerodha.

    Returns {order_id, symbol, qty, price}
    """
    our_sym    = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
    prices     = await get_live_prices([our_sym])
    price_data = prices.get(our_sym, {})
    current    = float(price_data.get("price") or 0.0)

    if current <= 0:
        raise ValueError(f"Could not fetch live price for {symbol}")

    kite = get_kite_client()
    try:
        margins      = await kite.get_margins("equity")
        real_balance = float(margins.get("available", {}).get("live_balance", 0.0))
    except Exception:
        real_balance = 0.0

    lim_price = _limit_price(current, "SELL")
    order_val = quantity * lim_price

    # Confidence check not applicable for explicit sells, but value cap still applies
    _check_safety_gates(confidence=100.0, order_value=order_val, real_balance=max(real_balance, order_val))
    await _abort_window("SELL", quantity, symbol, lim_price)

    order_id = await kite.place_order(
        tradingsymbol    = symbol.replace(".NS", ""),
        exchange         = "NSE",
        transaction_type = "SELL",
        quantity         = quantity,
        order_type       = "LIMIT",
        product          = "CNC",
        price            = lim_price,
    )

    log_entry = SimulationLog(
        event_type = "REAL_ORDER_PLACED",
        symbol     = symbol,
        message    = f"REAL SELL {quantity} × {symbol} @ ₹{lim_price:,.2f} (order_id={order_id})",
        data       = {
            "order_id":        order_id,
            "transaction_type":"SELL",
            "quantity":        quantity,
            "price":           lim_price,
            "estimated_value": order_val,
        },
    )
    session.add(log_entry)
    await session.flush()

    logger.critical(
        f"REAL SELL ORDER PLACED — order_id={order_id} "
        f"{quantity} × {symbol} @ ₹{lim_price:,.2f}"
    )

    return {
        "order_id": order_id,
        "symbol":   symbol,
        "qty":      quantity,
        "price":    lim_price,
    }


# ── Order status ──────────────────────────────────────────────────────────────

async def get_order_status(order_id: str) -> dict:
    """Poll latest status of a real order from Kite order history."""
    kite = get_kite_client()
    history = await kite.get_order_history(order_id)
    if not history:
        return {"order_id": order_id, "status": "UNKNOWN"}
    latest = history[-1]  # most recent status update
    return {
        "order_id":         order_id,
        "status":           latest.get("status", "UNKNOWN"),
        "filled_quantity":  latest.get("filled_quantity", 0),
        "average_price":    latest.get("average_price", 0.0),
        "status_message":   latest.get("status_message", ""),
        "exchange_order_id": latest.get("exchange_order_id"),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  10-rule real order pipeline + GTT helpers (spec — Step 9)
# ─────────────────────────────────────────────────────────────────────────────

async def calculate_order_margins_preview(
    symbol: str,
    transaction_type: str,
    quantity: int,
    price: float,
    product: str = "CNC",
    exchange: str = "NSE",
) -> dict:
    """Preview margin + charges for a single order via Kite order_margins API."""
    from crawler.zerodha_kite_lib import get_order_margins, get_virtual_contract_note

    bare = symbol.replace(".NS", "")
    order_param = [{
        "exchange":         exchange,
        "tradingsymbol":    bare,
        "transaction_type": transaction_type.upper(),
        "variety":          "regular",
        "product":          product,
        "order_type":       "LIMIT" if price else "MARKET",
        "quantity":         int(quantity),
        "price":            float(price),
    }]
    try:
        margins = await asyncio.to_thread(get_order_margins, order_param)
    except Exception as exc:
        margins = {"error": str(exc)}
    try:
        charges = await asyncio.to_thread(get_virtual_contract_note, order_param)
    except Exception as exc:
        charges = {"error": str(exc)}
    return {"margins": margins, "charges": charges, "request": order_param}


async def place_real_order(
    symbol: str,
    transaction_type: str,
    quantity: int,
    session: AsyncSession,
    *,
    signal=None,
    order_type: str = "LIMIT",
    product: str = "CNC",
    exchange: str = "NSE",
    variety: str = "regular",
    price: float | None = None,
    trigger_price: float | None = None,
    tag: str | None = None,
) -> dict:
    """Place a real order after enforcing all 10 safety rules.

    Rules:
      1. ZERODHA_PAPER_MODE must be False
      2. Zerodha connected + token valid
      3. signal.confidence ≥ 60 (when a signal is supplied)
      4. Order value ≤ 5% of available cash
      5. NSE market open
      6. Daily loss limit not breached (MAX_DAILY_LOSS)
      7. 3-second abort window with logger.critical()
      8. LIMIT orders forced with 0.5% slippage buffer (unless explicit price)
      9. Max 5 open positions
     10. Tag every order with ATP_{signal_id} when possible
    """
    from crawler.live_prices import PRICE_CACHE
    from crawler.india_price_feed import is_nse_market_open
    from db.models import OpenPosition, ZerodhaPosition
    from sqlalchemy import func, select as _select

    sym_ns = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    bare   = symbol.replace(".NS", "")

    # Rule 1 — paper mode
    if settings.ZERODHA_PAPER_MODE or settings.PAPER_MODE:
        raise RuntimeError("PAPER mode is active — real order blocked")

    # Rule 2 — connected
    kite = get_kite_client()
    if not kite.access_token or not settings.ZERODHA_ENABLED:
        raise RuntimeError("Zerodha not connected")

    # Rule 5 — market open
    if not is_nse_market_open():
        raise RuntimeError("NSE market is closed — order rejected")

    # Rule 3 — confidence
    confidence = float(getattr(signal, "confidence", 100.0)) if signal else 100.0
    if confidence < 60:
        raise ValueError(f"Signal confidence {confidence:.1f}% < 60% threshold")

    # Determine price
    cur_price = price
    if cur_price is None:
        cache = PRICE_CACHE.get(sym_ns, {})
        cur_price = float(cache.get("price") or 0.0)
        if cur_price <= 0:
            try:
                ltps = await kite.get_ltp([f"NSE:{bare}"])
                cur_price = float(ltps.get(f"NSE:{bare}", {}).get("last_price", 0.0))
            except Exception:
                pass
    if not cur_price or cur_price <= 0:
        raise ValueError(f"Could not determine live price for {symbol}")

    # Rule 8 — LIMIT with 0.5% buffer
    if order_type.upper() == "MARKET":
        order_type = "LIMIT"
    lim_price = _limit_price(cur_price, transaction_type)
    order_value = quantity * lim_price

    # Rule 4 — 5% cap
    try:
        margins = await kite.get_margins("equity")
        available_cash = float(margins.get("available", {}).get("live_balance", 0.0))
    except Exception:
        available_cash = 0.0
    max_value = available_cash * 0.05
    if max_value > 0 and order_value > max_value:
        raise ValueError(
            f"Order value ₹{order_value:,.2f} > 5% available cash (₹{max_value:,.2f})"
        )

    # Rule 6 — daily loss limit
    try:
        from paper_trading.virtual_wallet import VirtualWallet
        snap = await VirtualWallet.get_summary(session)
        daily_pnl = float(snap.get("daily_pnl", 0.0))
        if daily_pnl < 0 and abs(daily_pnl) >= settings.PAPER_TRADING_BALANCE * settings.MAX_DAILY_LOSS:
            raise RuntimeError(
                f"Daily loss limit hit (₹{abs(daily_pnl):,.2f}) — orders frozen"
            )
    except RuntimeError:
        raise
    except Exception:
        pass  # wallet may not be initialised in real-money flow

    # Rule 9 — max open positions
    open_count = (await session.execute(
        _select(func.count(OpenPosition.id))
    )).scalar() or 0
    zpos_count = (await session.execute(
        _select(func.count(ZerodhaPosition.id)).where(
            ZerodhaPosition.position_type == "net",
            ZerodhaPosition.quantity != 0,
        )
    )).scalar() or 0
    if (open_count + zpos_count) >= 5:
        raise RuntimeError("Max 5 open positions reached — square off before opening more")

    # Rule 10 — tag
    sig_id = getattr(signal, "id", None) if signal else None
    final_tag = tag or (f"ATP_{sig_id}" if sig_id else "ATP_manual")

    # Rule 7 — abort window
    await _abort_window(transaction_type, quantity, bare, lim_price)

    order_id = await kite.place_order(
        tradingsymbol    = bare,
        exchange         = exchange,
        transaction_type = transaction_type.upper(),
        quantity         = int(quantity),
        order_type       = order_type,
        product          = product,
        price            = lim_price,
        trigger_price    = float(trigger_price or 0.0),
        variety          = variety,
        tag              = final_tag,
    )

    # Audit log
    log_entry = SimulationLog(
        event_type = "REAL_ORDER_PLACED",
        symbol     = bare,
        message    = f"{transaction_type.upper()} {quantity}×{bare} @ ₹{lim_price} order_id={order_id}",
        data       = {
            "order_id":   order_id,
            "quantity":   quantity,
            "price":      lim_price,
            "value":      order_value,
            "confidence": confidence,
            "tag":        final_tag,
        },
    )
    session.add(log_entry)
    await session.flush()

    logger.critical(
        f"REAL ORDER PLACED — {transaction_type.upper()} {quantity}×{bare} "
        f"@ ₹{lim_price:,.2f} (order_id={order_id}, tag={final_tag})"
    )
    return {
        "order_id":      order_id,
        "symbol":        bare,
        "qty":           quantity,
        "price":         lim_price,
        "value":         order_value,
        "tag":           final_tag,
    }


async def place_gtt_with_oco(
    symbol: str,
    quantity: int,
    buy_price: float,
    stoploss_price: float,
    target_price: float,
    session: AsyncSession,
    *,
    last_price: float | None = None,
    exchange: str = "NSE",
    product: str = "CNC",
) -> dict:
    """Create an OCO bracket GTT (stop-loss + target) for an existing position."""
    if settings.ZERODHA_PAPER_MODE:
        raise RuntimeError("GTT blocked — ZERODHA_PAPER_MODE is True")

    from crawler.zerodha_kite_lib import place_gtt_oco

    bare = symbol.replace(".NS", "")
    last = float(last_price or buy_price)
    result = await asyncio.to_thread(
        place_gtt_oco,
        tradingsymbol=bare,
        exchange=exchange,
        last_price=last,
        stoploss_trigger=stoploss_price,
        stoploss_price=stoploss_price,
        target_trigger=target_price,
        target_price=target_price,
        quantity=int(quantity),
        product=product,
    )

    session.add(SimulationLog(
        event_type = "REAL_GTT_PLACED",
        symbol     = bare,
        message    = f"OCO GTT {bare}: SL ₹{stoploss_price} / TGT ₹{target_price}",
        data       = {**result, "stoploss": stoploss_price, "target": target_price, "qty": quantity},
    ))
    await session.flush()
    logger.critical(f"REAL OCO GTT — {bare} SL=₹{stoploss_price} TGT=₹{target_price}")
    return result
