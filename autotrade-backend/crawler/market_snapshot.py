"""Unified live-market snapshot service.

Single canonical entry point so the LLM's reasoning tools
(_tool_price_action/_tool_market_depth/_tool_predict_next_candle in
engine/agent/decision_engine.py) AND _execute_news_trade()'s entry-price
lookup (news_discovery_engine.py) read the SAME market state — eliminating
decision-vs-execution price drift and making replay/audit deterministic
(a snapshot's `source`/`fetched_at_ist` prove exactly which tick a trade
was decided and executed against).

Root cause this replaces: _execute_news_trade() previously called
crawler.live_prices.get_price(), which only reads in-memory LIVE_TICKS
(WebSocket-fed) / PRICE_CACHE (yfinance-fed) dicts. Those are populated by
the FastAPI/Celery worker process's background tasks — a standalone script
process (e.g. this news pipeline run as its own process) starts with both
dicts empty for any symbol it hasn't independently touched, so get_price()
silently returned None even though Zerodha was reachable the whole time
(confirmed live: _tool_price_action's direct get_full_quote()-equivalent
REST call succeeded in the exact same run that _execute_news_trade()
reported "no live price available").

Priority, matching what _tool_price_action/_tool_market_depth already use
successfully:
  1. Zerodha WebSocket tick (crawler.zerodha_ticker) — sub-second, but only
     populated if an active ticker connection is running in THIS process.
  2. Zerodha REST full quote (crawler.zerodha_market.get_full_quote) —
     needs only kite.access_token, no WebSocket; proven reliable even from
     standalone script processes.
  3. yfinance (crawler.live_prices.yfinance_ltp_batch) — process-independent
     last resort, slowest/staleest.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.logger import logger

_IST = ZoneInfo("Asia/Kolkata")
_CACHE_TTL_SEC = 5.0

_snapshot_cache: dict[str, "MarketSnapshot"] = {}


@dataclass
class MarketSnapshot:
    symbol: str
    ltp: float
    source: str  # "zerodha_ws" | "zerodha_rest" | "yfinance"
    fetched_at: float  # time.monotonic(), for TTL comparisons only
    fetched_at_ist: str
    ohlc: dict = field(default_factory=dict)
    volume: float | None = None
    change_pct: float | None = None
    bid: float | None = None
    ask: float | None = None
    oi: float | None = None
    buy_depth: list = field(default_factory=list)
    sell_depth: list = field(default_factory=list)


def _now_ist() -> str:
    return datetime.now(_IST).isoformat()


async def get_market_snapshot(symbol: str, *, max_age_sec: float = _CACHE_TTL_SEC) -> MarketSnapshot | None:
    """Return a cached snapshot if fresh enough, else fetch and cache a new one.

    The short TTL is what lets the LLM's tool calls and the execution step
    (usually seconds apart in the same ReAct loop) observe the identical
    tick, without also serving stale prices across an entire multi-minute
    pipeline run.
    """
    cached = _snapshot_cache.get(symbol)
    if cached and (time.monotonic() - cached.fetched_at) <= max_age_sec:
        return cached

    snap = await _fetch_fresh(symbol)
    if snap:
        _snapshot_cache[symbol] = snap
    return snap


async def _fetch_fresh(symbol: str) -> MarketSnapshot | None:
    tick = _from_websocket_tick(symbol)
    if tick:
        return tick

    quote = await _from_zerodha_rest(symbol)
    if quote:
        return quote

    yf = await _from_yfinance(symbol)
    if yf:
        return yf

    logger.warning(f"[market_snapshot] {symbol}: all sources exhausted (ws/rest/yfinance) — no price available")
    return None


def _from_websocket_tick(symbol: str) -> MarketSnapshot | None:
    from crawler.zerodha_ticker import get_live_tick

    tick = get_live_tick(symbol)
    if not tick or not tick.get("last_price"):
        return None
    ohlc = tick.get("ohlc") or {}
    depth = tick.get("depth") or {}
    ltp = float(tick["last_price"])
    prev_close = float(ohlc.get("close") or 0.0)
    change_pct = ((ltp - prev_close) / prev_close * 100) if prev_close else None
    return MarketSnapshot(
        symbol=symbol, ltp=ltp, source="zerodha_ws",
        fetched_at=time.monotonic(), fetched_at_ist=_now_ist(),
        ohlc=ohlc, volume=tick.get("volume_traded") or tick.get("volume"),
        change_pct=change_pct,
        bid=(depth.get("buy") or [{}])[0].get("price"),
        ask=(depth.get("sell") or [{}])[0].get("price"),
        oi=tick.get("oi"),
        buy_depth=depth.get("buy") or [], sell_depth=depth.get("sell") or [],
    )


async def _from_zerodha_rest(symbol: str) -> MarketSnapshot | None:
    from crawler.zerodha_market import get_full_quote

    data = await get_full_quote(symbol)
    if not data or not data.get("last_price"):
        return None
    return MarketSnapshot(
        symbol=symbol, ltp=float(data["last_price"]), source="zerodha_rest",
        fetched_at=time.monotonic(), fetched_at_ist=_now_ist(),
        ohlc=data.get("ohlc") or {}, volume=data.get("volume"),
        change_pct=data.get("change_pct"), bid=data.get("bid"), ask=data.get("ask"),
        oi=data.get("oi"), buy_depth=data.get("buy_depth") or [], sell_depth=data.get("sell_depth") or [],
    )


async def _from_yfinance(symbol: str) -> MarketSnapshot | None:
    from crawler.live_prices import yfinance_ltp_batch

    batch = await yfinance_ltp_batch([symbol])
    price = batch.get(symbol)
    if not price or price <= 0:
        return None
    return MarketSnapshot(
        symbol=symbol, ltp=float(price), source="yfinance",
        fetched_at=time.monotonic(), fetched_at_ist=_now_ist(),
    )
