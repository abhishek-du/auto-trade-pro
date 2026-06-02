"""Shared mutual-fund NAV cache.

Consolidates the three independent NAV caches that previously lived in
``engine/portfolio_service``, ``engine/sip_engine``, and ``engine/mf_signal_engine``
behind one async helper. mfapi.in is the upstream; the cache is in-process
(per-worker) with separate TTLs for the latest-NAV view and the history view.

This is intentionally not Redis-backed yet — when M2 (shared state on Redis)
lands, swap the ``_LATEST_CACHE`` / ``_HISTORY_CACHE`` reads/writes for Redis
``GET``/``SETEX`` calls and every caller is fixed at once.

Public API
----------
``await get_latest_nav(scheme_code)``           → float | None
``await get_nav_history(scheme_code, days)``    → list[float]  (oldest → newest)
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx

from utils.logger import logger

_MFAPI_BASE = "https://api.mfapi.in/mf"

# Latest-NAV cache: 1 hour TTL (NAVs are published end-of-day, so an hour of
# staleness in mid-session is fine; the history cache covers analytical loads).
_LATEST_CACHE: dict[str, tuple[float, float]] = {}     # {code: (nav, ts)}
_LATEST_TTL = 60 * 60

# History cache: 1 hour TTL on the underlying fetch, separate slot keyed by
# (scheme_code, days) so callers asking for 30 vs 90 days don't collide.
_HISTORY_CACHE: dict[tuple[str, int], tuple[list[float], float]] = {}
_HISTORY_TTL = 60 * 60


async def get_latest_nav(scheme_code: str) -> float | None:
    """Return the most recent NAV for ``scheme_code`` or None on any failure.

    Never raises. Uses ``httpx.AsyncClient`` so this is safe to await from the
    FastAPI / Celery event loop — the previous implementations used blocking
    ``httpx.get()`` which froze the loop for the entire 5–8s timeout.
    """
    if not scheme_code:
        return None
    now = time.time()
    hit = _LATEST_CACHE.get(scheme_code)
    if hit and (now - hit[1]) < _LATEST_TTL:
        return hit[0]

    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{_MFAPI_BASE}/{scheme_code}")
            r.raise_for_status()
            data = r.json().get("data") or []
            if not data:
                return None
            nav = float(data[0]["nav"])
    except Exception as exc:
        logger.debug(f"[nav_cache] latest fetch failed for {scheme_code}: {exc}")
        return None

    _LATEST_CACHE[scheme_code] = (nav, now)
    return nav


async def get_nav_history(scheme_code: str, days: int = 90) -> list[float]:
    """Return up to ``days`` NAV closes (oldest → newest) for ``scheme_code``.

    Empty list on any failure. Cached separately per (scheme_code, days).
    """
    if not scheme_code or days <= 0:
        return []
    now = time.time()
    key = (scheme_code, days)
    hit = _HISTORY_CACHE.get(key)
    if hit and (now - hit[1]) < _HISTORY_TTL:
        return hit[0]

    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{_MFAPI_BASE}/{scheme_code}")
            r.raise_for_status()
            raw = r.json().get("data") or []
    except Exception as exc:
        logger.debug(f"[nav_cache] history fetch failed for {scheme_code}: {exc}")
        return []

    # mfapi.in returns newest→oldest; we want oldest→newest for trend math.
    navs: list[float] = []
    for entry in raw[:days]:
        try:
            navs.append(float(entry["nav"]))
        except (KeyError, TypeError, ValueError):
            continue
    navs.reverse()

    _HISTORY_CACHE[key] = (navs, now)
    return navs
