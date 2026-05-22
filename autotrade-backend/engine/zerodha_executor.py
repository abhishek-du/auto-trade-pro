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
