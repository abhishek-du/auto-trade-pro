"""Upstox WebSocket Feed Integration using Upstox Python SDK."""

import time
import threading
from typing import Any
import upstox_client
from upstox_client.feeder.market_data_streamer_v3 import MarketDataStreamerV3
from utils.config import settings
from utils.logger import logger
import asyncio

# In-memory store for ticks
UPSTOX_LIVE_TICKS: dict[str, dict[str, Any]] = {}

_STREAMER: MarketDataStreamerV3 | None = None
_SUBSCRIBED_SYMBOLS: set[str] = set()
_IKEY_CACHE: dict[str, str] = {}

def _ikey_to_symbol(ikey: str) -> str | None:
    for sym, key in _IKEY_CACHE.items():
        if key == ikey:
            return sym
    return None

def _on_open():
    logger.info("[upstox/websocket] Streamer Connected")

def _on_message(message):
    try:
        # According to Upstox documentation, the message is a dict where keys are instrument_keys
        # and values are feed data
        if isinstance(message, dict):
            for instrument_key, feed in message.items():
                symbol = _ikey_to_symbol(instrument_key)
                if not symbol:
                    continue
                    
                if "ltpc" in feed:
                    data = feed["ltpc"]
                    UPSTOX_LIVE_TICKS[symbol] = {
                        "last_price": data.get("ltp"),
                        "change": data.get("cp", 0), # Using close price as reference might need calculation if it's cp, but SDK handles it
                        "_age_timestamp": time.time()
                    }
                elif "ff" in feed or "full" in feed:
                    data = feed.get("ff") or feed.get("full")
                    if data:
                        UPSTOX_LIVE_TICKS[symbol] = {
                            "last_price": data.get("marketFF", {}).get("ltpc", {}).get("ltp") or data.get("ltp"),
                            "volume_traded": data.get("marketFF", {}).get("vtt") or data.get("vtt"),
                            "_age_timestamp": time.time()
                        }
    except Exception as e:
        logger.error(f"[upstox/websocket] Parse error: {e}")

def _on_error(error):
    logger.error(f"[upstox/websocket] Error: {error}")

def _on_close(status_code, close_msg):
    logger.warning(f"[upstox/websocket] Closed: {status_code} {close_msg}")

def start_upstox_websocket(instrument_map: dict[str, str]):
    """Start websocket streamer.
    instrument_map: mapping of symbol -> instrument_key
    """
    global _STREAMER, _IKEY_CACHE
    token = getattr(settings, "UPSTOX_ACCESS_TOKEN", None)
    if not token:
        logger.error("[upstox/websocket] UPSTOX_ACCESS_TOKEN is not set.")
        return

    _IKEY_CACHE.update(instrument_map)
    instrument_keys = list(instrument_map.values())
    
    if not instrument_keys:
        logger.warning("[upstox/websocket] No instruments to subscribe.")
        return

    try:
        conf = upstox_client.Configuration()
        conf.access_token = token
        api_client = upstox_client.ApiClient(conf)

        _STREAMER = MarketDataStreamerV3(api_client, instrument_keys, "ltpc")
        
        _STREAMER.on("open", _on_open)
        _STREAMER.on("message", _on_message)
        _STREAMER.on("error", _on_error)
        _STREAMER.on("close", _on_close)
        
        _STREAMER.auto_reconnect(True)
        
        # Connect is blocking due to websocket-client, run in thread
        t = threading.Thread(target=_STREAMER.connect, daemon=True)
        t.start()
        logger.info(f"[upstox/websocket] Starting streamer for {len(instrument_keys)} instruments.")

    except Exception as e:
        logger.error(f"[upstox/websocket] Start failed: {e}")

def subscribe_symbol(symbol: str, instrument_key: str):
    """Dynamically subscribe a symbol to the running streamer."""
    global _STREAMER, _IKEY_CACHE, _SUBSCRIBED_SYMBOLS
    if symbol in _SUBSCRIBED_SYMBOLS:
        return
        
    _IKEY_CACHE[symbol] = instrument_key
    _SUBSCRIBED_SYMBOLS.add(symbol)
    
    if _STREAMER:
        try:
            _STREAMER.subscribe([instrument_key], "ltpc")
            logger.info(f"[upstox/websocket] Subscribed {symbol}")
        except Exception as e:
            logger.error(f"[upstox/websocket] Subscribe error {symbol}: {e}")

def get_live_tick(symbol: str) -> dict | None:
    tick = UPSTOX_LIVE_TICKS.get(symbol)
    if tick:
        # Create a new dict with _age_seconds for compatibility
        out = dict(tick)
        out["_age_seconds"] = time.time() - out.pop("_age_timestamp", time.time())
        return out
    return None
