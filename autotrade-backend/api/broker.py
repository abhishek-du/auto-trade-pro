"""Broker-agnostic status and control — the single surface the UI talks to.

WHY THIS EXISTS
---------------
Until 2026-08-31 the UI asked Zerodha-specific endpoints whether the system was
"connected": a /zerodha page rendered "Zerodha Connected", and the sidebar
polled /api/v1/zerodha/ticker/status for its live dot. After Kite Connect's
token expired and Upstox became the sole backend, every one of those surfaces
kept reporting on a broker the system no longer uses — the UI was telling the
operator something untrue.

This router answers "what is actually serving market data right now", without
naming a broker in its route. Adding a third broker later means adding a probe
here, not a new page.

READ-ONLY. Toggling a broker on or off stays in /settings/brokers, which
already requires auth and refuses to disable the last remaining broker.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from utils.logger import logger

router = APIRouter(prefix="/broker", tags=["broker"])


async def _upstox_probe() -> dict:
    """Live health of the Upstox backend."""
    from utils.config import settings

    out = {
        "id": "upstox",
        "name": "Upstox",
        "token_present": bool(getattr(settings, "UPSTOX_ACCESS_TOKEN", "")),
        "role": "primary",
    }
    # Feed status. is_connected() reflects the V3 WebSocket; ticks_held says
    # whether anything has actually arrived, which distinguishes "socket open
    # but silent" from "streaming".
    try:
        from crawler.upstox_websocket import stats

        s = stats()
        out["feed_connected"] = bool(s.get("connected"))
        out["subscribed_count"] = int(s.get("subscribed", 0))
        out["ticks_held"] = int(s.get("ticks_held", 0))
    except Exception:
        out["feed_connected"] = False
        out["subscribed_count"] = 0
        out["ticks_held"] = 0

    # Instrument coverage: without an instrument_key nothing can be quoted, so
    # this is the honest measure of how much of the universe is reachable.
    try:
        from sqlalchemy import text

        from db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as s2:
            out["instruments_mapped"] = int((await s2.execute(text(
                "SELECT count(*) FROM kite_instruments "
                "WHERE exchange='NSE' AND instrument_key IS NOT NULL"))).scalar() or 0)
    except Exception:
        out["instruments_mapped"] = None
    return out


async def _zerodha_probe() -> dict:
    """Live health of the (legacy) Zerodha backend."""
    out = {"id": "zerodha", "name": "Zerodha Kite", "role": "legacy",
           "token_present": False, "feed_connected": False,
           "subscribed_count": 0, "ticks_held": 0, "instruments_mapped": None}
    try:
        from crawler.zerodha_kite_lib import get_kite

        out["token_present"] = bool(getattr(get_kite(), "access_token", None))
    except Exception:
        pass
    try:
        from crawler.zerodha_ticker import LIVE_TICKS, is_ticker_running

        out["feed_connected"] = bool(is_ticker_running())
        out["ticks_held"] = len(LIVE_TICKS)
    except Exception:
        pass
    return out


_PROBES = {"upstox": _upstox_probe, "zerodha": _zerodha_probe}


@router.get("/status", summary="Which broker is serving market data, and is it healthy")
async def broker_status(db: AsyncSession = Depends(get_db)):
    """One call the UI can render a truthful status from.

    `active` is the broker the system is actually using: enabled AND holding a
    token. It is deliberately NOT just "the enabled one" — an enabled broker
    with a dead token is precisely the state that made the old UI lie.
    """
    from utils.runtime_config import BROKER_DEFAULTS, BROKER_FLAGS, RuntimeConfig

    try:
        cfg = await RuntimeConfig.load(db)
        flags = {n: bool(cfg._get(k, BROKER_DEFAULTS.get(n, False)))
                 for n, k in BROKER_FLAGS.items()}
    except Exception as exc:
        logger.warning(f"[broker] toggle read failed: {type(exc).__name__}")
        flags = dict(BROKER_DEFAULTS)

    brokers = []
    for bid, probe in _PROBES.items():
        try:
            info = await probe()
        except Exception as exc:
            logger.warning(f"[broker] {bid} probe failed: {type(exc).__name__}")
            info = {"id": bid, "name": bid.title(), "token_present": False}
        info["enabled"] = flags.get(bid, False)
        info["usable"] = bool(info["enabled"] and info.get("token_present"))
        brokers.append(info)

    usable = [b for b in brokers if b["usable"]]
    active = next((b for b in usable if b.get("role") == "primary"), usable[0] if usable else None)

    return {
        "active": active["id"] if active else None,
        "active_name": active["name"] if active else None,
        # The three states the UI must be able to distinguish. "degraded" is the
        # one the old page could not express: a broker is serving REST quotes
        # but its live tick feed is down, which is exactly today's situation
        # while wsfeeder-api.upstox.com is blocked by the corporate firewall.
        "state": ("down" if not active
                  else "connected" if active.get("feed_connected")
                  else "degraded"),
        "brokers": brokers,
        "note": ("No broker is usable — there is no live price source."
                 if not active else
                 f"{active['name']} is serving market data."
                 + ("" if active.get("feed_connected")
                    else " Live tick feed is not connected; prices are being polled.")),
    }


@router.get("/ticker/status", summary="Live feed status (broker-agnostic)")
async def ticker_status(db: AsyncSession = Depends(get_db)):
    """Replaces /zerodha/ticker/status for the sidebar dot.

    Keeps the old response keys (`running`, `subscribed_count`) so the existing
    consumer keeps working, and adds the broker identity alongside.
    """
    st = await broker_status(db)
    active = next((b for b in st["brokers"] if b["id"] == st["active"]), None) if st["active"] else None
    return {
        "running": bool(active and active.get("feed_connected")),
        "subscribed_count": int(active.get("subscribed_count", 0)) if active else 0,
        "broker": st["active"],
        "broker_name": st["active_name"],
        "state": st["state"],
    }
