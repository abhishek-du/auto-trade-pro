"""Upstox Market Data Feed V3 — replaces KiteTicker.

Kite Connect's token expired on 2026-08-31. This is the sub-second tick source.

MODE BUDGETING (mirrors the KiteTicker design it replaces)
    Kite                          Upstox V3
    MODE_QUOTE  ~3000  ->  "full"  — open positions, watchlist, indices
    MODE_LTP    ~3000  ->  "ltpc"  — the rest of the universe
    MODE_FULL   ~1000  ->  "full"  — same channel; V3 folds depth into "full"

Upstox documents per-connection caps of 2,000 instruments on `full` and 5,000
on `ltpc`. Priority symbols (anything we hold, watch, or need for market
context) get `full` so depth/volume/OHLC are available; everything else gets
`ltpc`, which is all the bulk universe is ever read for.

TICK SHAPE. UPSTOX_LIVE_TICKS entries carry the SAME field names the Kite
ticker wrote, so crawler/live_prices.py and every downstream consumer are
unchanged: last_price, volume_traded, ohlc, depth, oi, total_buy_qty,
total_sell_qty, change, change_percent, instrument_token, _ts, _age_seconds.

REVERSE LOOKUP is a dict, not a scan. The previous implementation resolved
instrument_key -> symbol by iterating the whole cache on EVERY tick; at a few
thousand subscribed instruments that is O(n) per message on the hot path.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import upstox_client
from upstox_client.feeder.market_data_streamer_v3 import MarketDataStreamerV3

from utils.config import settings
from utils.logger import logger

# symbol -> tick dict. Same role as crawler/zerodha_ticker.py::LIVE_TICKS.
UPSTOX_LIVE_TICKS: dict[str, dict[str, Any]] = {}

_STREAMER: MarketDataStreamerV3 | None = None
_FWD: dict[str, str] = {}     # "RELIANCE.NS" -> "NSE_EQ|INE..."
_REV: dict[str, str] = {}     # "NSE_EQ|INE..." -> "RELIANCE.NS"
_LOCK = threading.Lock()
_CONNECTED = False

# Upstox per-connection subscription ceilings (documented).
_FULL_CAP = 2000
_LTPC_CAP = 5000


def _ikey_to_symbol(ikey: str) -> str | None:
    return _REV.get(ikey)


def _on_open():
    global _CONNECTED
    _CONNECTED = True
    logger.info("[upstox/websocket] connected")


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _extract(feed: dict) -> dict | None:
    """Normalise one V3 feed payload into the Kite-compatible tick shape.

    V3 nests differently per mode:
        ltpc  -> {"ltpc": {"ltp","cp","ltt","ltq"}}
        full  -> {"fullFeed": {"marketFF": {"ltpc": {...}, "marketOHLC": {...},
                               "marketLevel": {"bidAskQuote": [...]}, "vtt", "oi"}}}
    Both spellings of the full block ("ff", "fullFeed") are accepted because
    the SDK has used each across versions.
    """
    ltpc = feed.get("ltpc")
    full = feed.get("fullFeed") or feed.get("ff") or feed.get("full")

    if full:
        mff = full.get("marketFF") or full.get("indexFF") or full
        inner = mff.get("ltpc") or {}
        ltp = _num(inner.get("ltp"))
        prev_close = _num(inner.get("cp"))

        ohlc_src = mff.get("marketOHLC") or {}
        bars = ohlc_src.get("ohlc") or []
        # The day bar is the one this system means by "ohlc".
        day = next((b for b in bars if str(b.get("interval", "")).lower() in ("1d", "d", "day")),
                   bars[0] if bars else {})
        ohlc = {"open": _num(day.get("open")), "high": _num(day.get("high")),
                "low": _num(day.get("low")), "close": prev_close or _num(day.get("close"))}

        depth_src = ((mff.get("marketLevel") or {}).get("bidAskQuote")) or []
        buy, sell, tbq, tsq = [], [], 0.0, 0.0
        for lvl in depth_src:
            bq, sq = _num(lvl.get("bq")), _num(lvl.get("sq"))
            tbq += bq
            tsq += sq
            buy.append({"price": _num(lvl.get("bp")), "quantity": int(bq)})
            sell.append({"price": _num(lvl.get("sp")), "quantity": int(sq)})

        chg = ltp - prev_close if prev_close else 0.0
        return {
            "last_price": ltp,
            "volume_traded": int(_num(mff.get("vtt"))),
            "ohlc": ohlc,
            "depth": {"buy": buy, "sell": sell},
            "oi": int(_num(mff.get("oi"))),
            "total_buy_qty": tbq,
            "total_sell_qty": tsq,
            "change": round(chg, 4),
            "change_percent": round(chg / prev_close * 100, 4) if prev_close else 0.0,
        }

    if ltpc:
        ltp = _num(ltpc.get("ltp"))
        prev_close = _num(ltpc.get("cp"))
        chg = ltp - prev_close if prev_close else 0.0
        # ltpc carries no volume/depth. Zeroes here are the documented mode
        # contract, exactly as Kite's MODE_LTP behaved -- not missing data.
        return {
            "last_price": ltp,
            "volume_traded": 0,
            "ohlc": {"open": 0.0, "high": 0.0, "low": 0.0, "close": prev_close},
            "depth": {"buy": [], "sell": []},
            "oi": 0,
            "total_buy_qty": 0.0,
            "total_sell_qty": 0.0,
            "change": round(chg, 4),
            "change_percent": round(chg / prev_close * 100, 4) if prev_close else 0.0,
        }
    return None


def _on_message(message):
    """Hot path. Must not raise and must not do O(n) work per tick."""
    try:
        if not isinstance(message, dict):
            return
        feeds = message.get("feeds") if "feeds" in message else message
        if not isinstance(feeds, dict):
            return
        now = time.time()
        for ikey, feed in feeds.items():
            sym = _REV.get(ikey)
            if not sym or not isinstance(feed, dict):
                continue
            tick = _extract(feed)
            if not tick or tick["last_price"] <= 0:
                continue
            tick["instrument_token"] = ikey
            tick["_ts"] = now
            UPSTOX_LIVE_TICKS[sym] = tick
    except Exception as exc:
        logger.error(f"[upstox/websocket] parse error: {type(exc).__name__}: {exc}")


def _on_error(error):
    logger.error(f"[upstox/websocket] error: {error}")


def _on_close(status_code=None, close_msg=None):
    global _CONNECTED
    _CONNECTED = False
    logger.warning(f"[upstox/websocket] closed: {status_code} {close_msg}")


def start_upstox_websocket(instrument_map: dict[str, str],
                           priority_symbols: set[str] | None = None) -> bool:
    """Start the streamer with mode budgeting. Returns True if it started.

    `priority_symbols` (open positions, watchlist, indices) get "full"; the
    remainder gets "ltpc". Both are capped at the documented per-connection
    ceilings, priority first, so a large universe can never crowd out the
    instruments we actually hold.
    """
    global _STREAMER, _FWD, _REV

    token = getattr(settings, "UPSTOX_ACCESS_TOKEN", None)
    if not token:
        logger.error("[upstox/websocket] UPSTOX_ACCESS_TOKEN not set")
        return False
    if not instrument_map:
        logger.warning("[upstox/websocket] no instruments to subscribe")
        return False

    with _LOCK:
        _FWD = dict(instrument_map)
        _REV = {v: k for k, v in instrument_map.items()}

    prio = priority_symbols or set()
    full_keys = [instrument_map[s] for s in instrument_map if s in prio][:_FULL_CAP]
    rest = [k for s, k in instrument_map.items() if s not in prio]
    ltpc_keys = rest[:_LTPC_CAP]

    if len(rest) > _LTPC_CAP:
        logger.warning(
            f"[upstox/websocket] universe {len(rest)} exceeds the ltpc cap "
            f"({_LTPC_CAP}); {len(rest) - _LTPC_CAP} symbols will have no live tick")

    try:
        conf = upstox_client.Configuration()
        conf.access_token = token
        api_client = upstox_client.ApiClient(conf)

        # One connection, seeded with the priority set in "full". The bulk
        # "ltpc" set is added after connect: V3 allows changing mode per
        # instrument group on a live socket, and doing it in two steps keeps
        # the priority subscription from being delayed behind a 5,000-key
        # handshake.
        seed = full_keys or ltpc_keys[:1]
        mode = "full" if full_keys else "ltpc"
        _STREAMER = MarketDataStreamerV3(api_client, seed, mode)
        _STREAMER.on("open", _on_open)
        _STREAMER.on("message", _on_message)
        _STREAMER.on("error", _on_error)
        _STREAMER.on("close", _on_close)
        _STREAMER.auto_reconnect(True)

        threading.Thread(target=_STREAMER.connect, daemon=True,
                         name="upstox-ws").start()

        if ltpc_keys:
            def _add_bulk():
                # Give the socket a moment to complete its handshake; a
                # subscribe before "open" is silently dropped by the SDK.
                time.sleep(3)
                try:
                    _STREAMER.subscribe(ltpc_keys, "ltpc")
                    logger.info(f"[upstox/websocket] subscribed {len(ltpc_keys)} in ltpc")
                except Exception as exc:
                    logger.warning(f"[upstox/websocket] bulk subscribe failed: {exc}")
            threading.Thread(target=_add_bulk, daemon=True, name="upstox-ws-bulk").start()

        logger.info(f"[upstox/websocket] starting — full={len(full_keys)} ltpc={len(ltpc_keys)}")
        return True
    except Exception as exc:
        logger.error(f"[upstox/websocket] start failed: {type(exc).__name__}: {exc}")
        return False


def subscribe_symbol(symbol: str, instrument_key: str) -> bool:
    """Add one instrument to the live socket (a new position just opened)."""
    if not _STREAMER:
        return False
    with _LOCK:
        _FWD[symbol] = instrument_key
        _REV[instrument_key] = symbol
    try:
        _STREAMER.subscribe([instrument_key], "full")
        logger.info(f"[upstox/websocket] subscribed {symbol} (full)")
        return True
    except Exception as exc:
        logger.warning(f"[upstox/websocket] subscribe {symbol} failed: {exc}")
        return False


def get_live_tick(symbol: str) -> dict | None:
    """Latest tick for a symbol, with `_age_seconds` filled in."""
    t = UPSTOX_LIVE_TICKS.get(symbol)
    if not t:
        return None
    out = dict(t)
    out["_age_seconds"] = time.time() - float(t.get("_ts", 0) or 0)
    return out


def is_connected() -> bool:
    return _CONNECTED


def stats() -> dict:
    return {"connected": _CONNECTED, "subscribed": len(_FWD),
            "ticks_held": len(UPSTOX_LIVE_TICKS)}
