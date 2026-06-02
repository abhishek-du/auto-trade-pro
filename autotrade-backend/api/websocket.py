# WebSocket API — real-time portfolio, trade events, prices, and log tail.

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from db.database import AsyncSessionLocal
from db.models import SimulationLog
from utils.config import settings
from utils.logger import logger

router = APIRouter(tags=["WebSocket"])

# ── Shared broadcast helper (used by engine loop to push trade events) ────────

class _Broadcaster:
    def __init__(self):
        self._channels: dict[str, list[WebSocket]] = {}

    def _get(self, channel: str) -> list[WebSocket]:
        return self._channels.setdefault(channel, [])

    async def connect(self, channel: str, ws: WebSocket):
        await ws.accept()
        self._get(channel).append(ws)
        logger.debug(f"WS connect  channel={channel}  total={len(self._get(channel))}")

    def disconnect(self, channel: str, ws: WebSocket):
        conns = self._get(channel)
        if ws in conns:
            conns.remove(ws)

    async def push(self, channel: str, payload: dict):
        dead = []
        for ws in list(self._get(channel)):
            try:
                await ws.send_text(json.dumps(payload, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(channel, ws)


broadcaster = _Broadcaster()


async def broadcast_trade_event(event: str, symbol: str, data: dict):
    """Called by the engine to push trade events to /ws/trades subscribers."""
    await broadcaster.push("trades", {
        "type":      "trade_event",
        "event":     event,
        "symbol":    symbol,
        "timestamp": datetime.utcnow().isoformat(),
        **data,
    })


# ── /ws/portfolio — wallet snapshot every 10 s ───────────────────────────────

@router.websocket("/portfolio")
async def ws_portfolio(ws: WebSocket):
    """Streams wallet balance, equity, unrealised PnL, and open-position count every 10 s."""
    await broadcaster.connect("portfolio", ws)
    try:
        while True:
            async with AsyncSessionLocal() as session:
                from paper_trading.position_tracker import PositionTracker
                from paper_trading.virtual_wallet import VirtualWallet

                summary    = await VirtualWallet.get_summary(session)
                open_count = await PositionTracker.count_open(session)

            await ws.send_text(json.dumps({
                "type":           "portfolio_update",
                "balance":        summary["balance"],
                "equity":         summary["equity"],
                "unrealised_pnl": summary["unrealised_pnl"],
                "realised_pnl":   summary["realised_pnl"],
                "roi_percent":    summary["roi_percent"],
                "open_count":     open_count,
                "timestamp":      datetime.utcnow().isoformat(),
            }))
            await asyncio.sleep(10)
    except WebSocketDisconnect:
        broadcaster.disconnect("portfolio", ws)


# ── /ws/trades — trade open/close events ─────────────────────────────────────

@router.websocket("/trades")
async def ws_trades(ws: WebSocket):
    """Streams TRADE_OPENED / TRADE_CLOSED events from the simulation log."""
    await broadcaster.connect("trades", ws)
    _TRADE_EVENTS = {"TRADE_OPENED", "TRADE_CLOSED", "TRADE_STOPPED"}
    last_id = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.max(SimulationLog.id)))
        last_id = int(result.scalar() or 0)

    try:
        while True:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(SimulationLog)
                    .where(
                        SimulationLog.id > last_id,
                        SimulationLog.event_type.in_(_TRADE_EVENTS),
                    )
                    .order_by(SimulationLog.id)
                    .limit(20)
                )
                new_logs = result.scalars().all()

            for log in new_logs:
                await ws.send_text(json.dumps({
                    "type":      "trade_event",
                    "event":     log.event_type,
                    "symbol":    log.symbol,
                    "message":   log.message,
                    "data":      log.data,
                    "timestamp": log.timestamp.isoformat(),
                }, default=str))
                last_id = log.id

            await asyncio.sleep(2)
    except WebSocketDisconnect:
        broadcaster.disconnect("trades", ws)


# ── /ws/prices — latest prices every 5 s ─────────────────────────────────────

@router.websocket("/prices")
async def ws_prices(ws: WebSocket):
    """Streams latest price for every watchlist symbol every 5 s."""
    await broadcaster.connect("prices", ws)
    all_symbols = (settings.forex_symbols + settings.stock_symbols)[:15]

    try:
        while True:
            from crawler.price_feed import get_latest_price

            prices = []
            for symbol in all_symbols:
                price = await get_latest_price(symbol)
                if price is not None:
                    prices.append({"symbol": symbol, "price": price})

            await ws.send_text(json.dumps({
                "type":      "price_update",
                "prices":    prices,
                "timestamp": datetime.utcnow().isoformat(),
            }))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        broadcaster.disconnect("prices", ws)


# ── /ws/logs — real-time simulation log tail ──────────────────────────────────

@router.websocket("/logs")
async def ws_logs(ws: WebSocket):
    """Streams SimulationLog entries as they arrive (tail -f style).
    Sends the 10 most recent entries first, then polls for new ones every second."""
    await broadcaster.connect("logs", ws)
    last_id = 0

    async with AsyncSessionLocal() as session:
        # Seed with last 10 entries
        result = await session.execute(
            select(SimulationLog)
            .order_by(SimulationLog.id.desc())
            .limit(10)
        )
        recent = list(reversed(result.scalars().all()))

        if recent:
            last_id = recent[-1].id
            for log in recent:
                await ws.send_text(json.dumps({
                    "type":       "log_entry",
                    "id":         log.id,
                    "event_type": log.event_type,
                    "symbol":     log.symbol,
                    "message":    log.message,
                    "data":       log.data,
                    "timestamp":  log.timestamp.isoformat(),
                }, default=str))

    try:
        while True:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(SimulationLog)
                    .where(SimulationLog.id > last_id)
                    .order_by(SimulationLog.id)
                    .limit(50)
                )
                new_logs = result.scalars().all()

            for log in new_logs:
                await ws.send_text(json.dumps({
                    "type":       "log_entry",
                    "id":         log.id,
                    "event_type": log.event_type,
                    "symbol":     log.symbol,
                    "message":    log.message,
                    "data":       log.data,
                    "timestamp":  log.timestamp.isoformat(),
                }, default=str))
                last_id = log.id

            await asyncio.sleep(1)
    except WebSocketDisconnect:
        broadcaster.disconnect("logs", ws)


# ── /ws/live-prices — NSE live price broadcast ────────────────────────────────

class LivePriceManager:
    def __init__(self):
        self.connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        from crawler.live_prices import get_all_cached_prices, get_market_summary
        await ws.accept()
        self.connections.add(ws)
        await self._send_to(ws, {
            "type":           "full_snapshot",
            "data":           get_all_cached_prices(),
            "market_summary": get_market_summary(),
            "timestamp":      datetime.utcnow().isoformat(),
        })

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)

    async def _send_to(self, ws: WebSocket, data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            self.connections.discard(ws)

    async def broadcast_prices(self, updated_prices: dict):
        if not self.connections:
            return
        from crawler.live_prices import get_market_summary
        message = {
            "type":           "price_update",
            "data":           updated_prices,
            "market_summary": get_market_summary(),
            "timestamp":      datetime.utcnow().isoformat(),
        }
        dead: set[WebSocket] = set()
        for ws in set(self.connections):
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self.connections -= dead

    async def broadcast_event(self, payload: dict) -> None:
        """Fire a generic event over the live WS to all subscribers.

        Used by non-price producers (news_crawler, alerts, …) that want
        push-to-frontend without standing up their own WS endpoint. The
        payload must already carry a ``type`` discriminator so the
        frontend can route it; this method does not wrap or augment it.
        """
        if not self.connections:
            return
        dead: set[WebSocket] = set()
        for ws in set(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self.connections -= dead


live_price_manager = LivePriceManager()


# ── /ws/candles/{symbol} — real-time candle updates ───────────────────────────

@router.websocket("/candles/{symbol}")
async def ws_candles(ws: WebSocket, symbol: str, timeframe: str = "1h"):
    """Streams the latest candle bar for a symbol every 15 s.

    On connect: sends last 5 candles as an 'init' message.
    Then every 15 s: fetches the latest bar and sends a 'candle_update' message.
    Falls back to DB polling if live fetch fails.
    """
    await ws.accept()

    from api.india import TIMEFRAME_CONFIG, _normalize_symbol, _ts_to_unix
    if timeframe not in TIMEFRAME_CONFIG:
        await ws.close(code=1003, reason="Invalid timeframe")
        return

    sym        = _normalize_symbol(symbol)
    bar_secs   = TIMEFRAME_CONFIG[timeframe]["seconds"]
    yf_interval = TIMEFRAME_CONFIG[timeframe]["yf_interval"]
    yf_period   = TIMEFRAME_CONFIG[timeframe]["yf_period"]
    last_bar_time: int | None = None

    # Send last 5 candles on connect
    try:
        async with AsyncSessionLocal() as session:
            from db.models import Candle
            from sqlalchemy import desc, select, and_
            rows = (await session.execute(
                select(Candle)
                .where(and_(Candle.symbol == sym, Candle.timeframe == timeframe))
                .order_by(desc(Candle.timestamp))
                .limit(5)
            )).scalars().all()

        if rows:
            init_candles = sorted(rows, key=lambda r: r.timestamp)
            last_bar_time = _ts_to_unix(init_candles[-1].timestamp)
            await ws.send_json({
                "type":      "init",
                "symbol":    sym,
                "timeframe": timeframe,
                "candles": [
                    {
                        "time":   _ts_to_unix(r.timestamp),
                        "open":   round(float(r.open),  4),
                        "high":   round(float(r.high),  4),
                        "low":    round(float(r.low),   4),
                        "close":  round(float(r.close), 4),
                        "volume": round(float(r.volume), 2),
                    }
                    for r in init_candles
                ],
            })
    except Exception as exc:
        # repr() captures empty-string exception messages too, so the log line
        # stops looking like "init failed for ^NSEI: " with nothing after it.
        logger.warning(f"[ws/candles] init failed for {sym}: {exc!r}")

    try:
        while True:
            await asyncio.sleep(15)

            # Fetch latest bar from yfinance in executor
            try:
                import yfinance as yf

                def _fetch():
                    df = yf.Ticker(sym).history(period="1d", interval=yf_interval)
                    if df.empty:
                        return None
                    row = df.iloc[-1]
                    ts = df.index[-1]
                    if hasattr(ts, "timestamp"):
                        t = int(ts.timestamp())
                    else:
                        import calendar
                        t = calendar.timegm(ts.timetuple())
                    return {
                        "time":   t,
                        "open":   round(float(row["Open"]),   4),
                        "high":   round(float(row["High"]),   4),
                        "low":    round(float(row["Low"]),    4),
                        "close":  round(float(row["Close"]),  4),
                        "volume": round(float(row.get("Volume", 0) or 0), 2),
                    }

                candle = await asyncio.get_event_loop().run_in_executor(None, _fetch)

                if candle:
                    is_new = last_bar_time is None or candle["time"] > last_bar_time
                    last_bar_time = candle["time"]
                    try:
                        await ws.send_json({
                            "type":      "candle_update",
                            "symbol":    sym,
                            "timeframe": timeframe,
                            "candle":    candle,
                            "is_new_bar": is_new,
                        })
                    except (WebSocketDisconnect, RuntimeError):
                        return  # client disconnected — stop polling
            except (WebSocketDisconnect, RuntimeError):
                return  # bubble up disconnects from sleep or init send
            except Exception as exc:
                logger.debug(f"[ws/candles] tick failed for {sym}: {exc}")

    except (WebSocketDisconnect, Exception):
        pass


@router.websocket("/live-prices")
async def live_prices_ws(websocket: WebSocket):
    """Streams live NSE prices to the Live Market page."""
    await live_price_manager.connect(websocket)
    try:
        while True:
            data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, asyncio.TimeoutError):
        live_price_manager.disconnect(websocket)
    except Exception:
        live_price_manager.disconnect(websocket)
