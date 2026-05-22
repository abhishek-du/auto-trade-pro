"""Zerodha KiteConnect v3 — real-time WebSocket price feed.

Connection URL: wss://ws.kite.trade?api_key={key}&access_token={token}

The Kite WebSocket protocol uses binary packets (not JSON) for tick data.
JSON messages are used only for subscribe/mode commands.

Binary packet format (full mode, NSE equity):
  Outer envelope:
    [0:2]  int16 big-endian — number of packets in this message
    For each packet:
      [0:2]  int16  — packet length in bytes
      [2:6]  int32  — instrument_token
      [6:10] int32  — last_price  (divide by 100 for NSE equity)
      [10:14] int32 — last_traded_quantity
      [14:18] int32 — average_traded_price (÷100)
      [18:22] int32 — volume_traded_today
      [22:26] int32 — total_buy_quantity
      [26:30] int32 — total_sell_quantity
      [30:34] int32 — open price (÷100)
      [34:38] int32 — high price (÷100)
      [38:42] int32 — low  price (÷100)
      [42:46] int32 — close / prev close (÷100)

Public API
----------
  start_kite_websocket()  — starts the WebSocket loop (runs forever)
  get_live_price(symbol)  — returns latest price from LIVE_PRICES dict
  LIVE_PRICES             — module-level dict updated on each tick
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

from utils.config import settings
from utils.logger import logger

# ── In-memory price store ─────────────────────────────────────────────────────

LIVE_PRICES: dict[str, dict[str, Any]] = {}

# ── Subscribed tokens (populated on connect) ──────────────────────────────────

_WS_URL = "wss://ws.kite.trade"


def get_live_price(symbol: str) -> float | None:
    return LIVE_PRICES.get(symbol, {}).get("price")


# ── Binary packet decoder ─────────────────────────────────────────────────────

def _decode_packets(data: bytes) -> list[dict]:
    """Decode a Kite WebSocket binary message into a list of tick dicts."""
    if len(data) < 2:
        return []

    n_packets = struct.unpack_from(">H", data, 0)[0]
    offset    = 2
    ticks     = []

    for _ in range(n_packets):
        if offset + 2 > len(data):
            break
        pkt_len = struct.unpack_from(">H", data, offset)[0]
        offset += 2

        if pkt_len < 4 or offset + pkt_len > len(data):
            offset += pkt_len
            continue

        pkt = data[offset: offset + pkt_len]
        offset += pkt_len

        token = struct.unpack_from(">I", pkt, 0)[0]

        if pkt_len >= 44:
            # Full mode
            def u32(off: int) -> int:
                return struct.unpack_from(">I", pkt, off)[0]

            ticks.append({
                "instrument_token": token,
                "last_price":         u32(4)  / 100.0,
                "last_traded_qty":    u32(8),
                "avg_traded_price":   u32(12) / 100.0,
                "volume":             u32(16),
                "total_buy_qty":      u32(20),
                "total_sell_qty":     u32(24),
                "open":               u32(28) / 100.0,
                "high":               u32(32) / 100.0,
                "low":                u32(36) / 100.0,
                "close":              u32(40) / 100.0,
            })
        elif pkt_len >= 8:
            # LTP mode
            ticks.append({
                "instrument_token": token,
                "last_price": struct.unpack_from(">I", pkt, 4)[0] / 100.0,
            })

    return ticks


# ── Subscribe/mode helpers ────────────────────────────────────────────────────

def _subscribe_msg(tokens: list[int]) -> str:
    return json.dumps({"a": "subscribe", "v": tokens})


def _mode_msg(tokens: list[int], mode: str = "full") -> str:
    return json.dumps({"a": "mode", "v": [mode, tokens]})


# ── Main WebSocket loop ───────────────────────────────────────────────────────

async def start_kite_websocket() -> None:
    """Connect to Kite WebSocket and stream live prices into LIVE_PRICES.

    Reconnects with exponential backoff (max 5 retries) on connection loss.
    Exits when ZERODHA_ENABLED becomes False or credentials are missing.
    """
    try:
        import websockets
    except ImportError:
        logger.error("[zerodha_ws] websockets library not installed")
        return

    from crawler.zerodha_market import NSE_TOKENS, INDEX_TOKENS, _TOKEN_TO_SYMBOL

    all_tokens = list(set(NSE_TOKENS.values()) | set(INDEX_TOKENS.values()))

    retry = 0
    max_retries = 5

    while retry <= max_retries:
        if not settings.ZERODHA_ENABLED or not settings.ZERODHA_ACCESS_TOKEN:
            logger.info("[zerodha_ws] Not connected — WebSocket not started")
            return

        ws_url = (
            f"{_WS_URL}?"
            f"api_key={settings.ZERODHA_API_KEY}"
            f"&access_token={settings.ZERODHA_ACCESS_TOKEN}"
        )

        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                logger.info(f"[zerodha_ws] Connected — subscribing to {len(all_tokens)} tokens")
                await ws.send(_subscribe_msg(all_tokens))
                await asyncio.sleep(0.5)
                await ws.send(_mode_msg(all_tokens, "full"))
                retry = 0  # reset on successful connect

                async for message in ws:
                    if isinstance(message, bytes):
                        ticks = _decode_packets(message)
                        for tick in ticks:
                            token = tick["instrument_token"]
                            sym = _TOKEN_TO_SYMBOL.get(token)
                            if sym:
                                LIVE_PRICES[sym] = {
                                    "price":     tick.get("last_price", 0.0),
                                    "open":      tick.get("open",       0.0),
                                    "high":      tick.get("high",       0.0),
                                    "low":       tick.get("low",        0.0),
                                    "close":     tick.get("close",      0.0),
                                    "volume":    tick.get("volume",     0),
                                    "total_buy_qty":  tick.get("total_buy_qty",  0),
                                    "total_sell_qty": tick.get("total_sell_qty", 0),
                                }
                    elif isinstance(message, str):
                        # Kite sends a JSON text message on connect/error
                        logger.debug(f"[zerodha_ws] Text message: {message[:200]}")

        except Exception as exc:
            retry += 1
            wait = min(2 ** retry, 60)
            logger.warning(
                f"[zerodha_ws] Connection lost: {exc}. "
                f"Retry {retry}/{max_retries} in {wait}s"
            )
            await asyncio.sleep(wait)

    logger.error("[zerodha_ws] Max retries reached — giving up WebSocket connection")
