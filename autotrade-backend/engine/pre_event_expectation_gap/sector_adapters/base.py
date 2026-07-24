"""Module 3: Sector-Specific Operational Nowcast — adapter architecture.

Per the strategy spec: do NOT use one generic fundamental formula for every
sector. Each sector gets a pluggable adapter that knows that sector's real
operational drivers. If no reliable adapter exists for a symbol's sector, or an
adapter can't gather enough public data, the result is NOWCAST_UNAVAILABLE —
never a fabricated prediction.

This module defines the adapter contract, the registry, sector resolution, and
the fail-closed `run_nowcast()` dispatcher. Concrete adapters (auto.py, …)
register themselves.

Point-in-time from day one: every adapter takes an `as_of` cutoff and must only
use data knowable at that timestamp — this is what makes the strategy
replay-safe (the spec's single most important requirement).
"""
from __future__ import annotations

import abc
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger
from engine.pre_event_expectation_gap.types import (
    ScheduledEvent, NowcastResult, NowcastStatus,
)


class SectorNowcastAdapter(abc.ABC):
    """One sector's operational nowcast. Subclasses set `sector` (an uppercase
    strategy-sector key like "AUTO") and implement `nowcast()`."""

    sector: str = ""

    @abc.abstractmethod
    async def nowcast(
        self, symbol: str, event: ScheduledEvent, as_of: datetime, session: AsyncSession,
    ) -> NowcastResult:
        """Return a NowcastResult from PUBLIC data available at `as_of` only.
        Must return NowcastResult(status=UNAVAILABLE) — never raise, never
        fabricate — when its required inputs aren't available."""
        raise NotImplementedError


# ── Registry ─────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, SectorNowcastAdapter] = {}


def register_adapter(adapter: SectorNowcastAdapter) -> None:
    _REGISTRY[adapter.sector.upper()] = adapter


def get_adapter(sector: str | None) -> SectorNowcastAdapter | None:
    if not sector:
        return None
    return _REGISTRY.get(sector.upper())


def registered_sectors() -> list[str]:
    return sorted(_REGISTRY.keys())


# ── Sector resolution ────────────────────────────────────────────────────────
# Maps a symbol to one of the strategy's supported UPPERCASE sector keys, or
# None if it can't be confidently classified (→ NOWCAST_UNAVAILABLE downstream).
# Order: authoritative hardcoded map first (india_specific.SECTOR_MAP, already
# uppercase strategy keys), then the disk-cached yfinance sector (sector_cache,
# Title-case) normalized to our keys. Conservative: an unknown/GENERAL sector
# returns None rather than guessing.

_SECTOR_CACHE_TO_STRATEGY = {
    "AUTO": "AUTO",
    "BANKING": "BANKING",
    "PHARMA": "PHARMA",
    "FMCG": "FMCG",
    "IT": "IT",
    "METALS": "METALS",
    # sector_cache also emits: Consumer, Energy, Infra, Telecom, GENERAL — none
    # of which map cleanly to a Phase-1 supported adapter, so they resolve to None.
}


def resolve_strategy_sector(symbol: str) -> str | None:
    """Best-effort, non-fabricating sector resolution. Never triggers a live
    network lookup here — reads the authoritative hardcoded map and whatever the
    sector cache already knows; an uncached/unknown symbol resolves to None."""
    try:
        from engine.india_specific import SECTOR_MAP
        hard = SECTOR_MAP.get(symbol) or SECTOR_MAP.get(f"{symbol}.NS")
        if hard and hard.upper() in _SECTOR_CACHE_TO_STRATEGY:
            return hard.upper()
    except Exception:
        pass

    try:
        from utils import sector_cache
        bare = symbol.replace(".NS", "").replace(".BO", "")
        cached = sector_cache._cache.get(bare)  # disk-backed cache, already loaded; no live lookup
        if cached:
            return _SECTOR_CACHE_TO_STRATEGY.get(cached.upper())
    except Exception:
        pass

    return None


# ── Fail-closed dispatcher ───────────────────────────────────────────────────

async def run_nowcast(
    symbol: str, event: ScheduledEvent, as_of: datetime, session: AsyncSession,
) -> NowcastResult:
    """Resolve the symbol's sector, dispatch to its adapter, and return the
    NowcastResult. Fail-closed on EVERY path: unknown sector, no adapter, or an
    adapter that raises → NowcastResult(UNAVAILABLE). A nowcast failure must
    never produce a tradeable signal and must never propagate an exception."""
    sector = resolve_strategy_sector(symbol)
    if sector is None:
        return NowcastResult(status=NowcastStatus.UNAVAILABLE,
                             notes=[f"no supported sector adapter for {symbol} (unresolved sector)"])
    adapter = get_adapter(sector)
    if adapter is None:
        return NowcastResult(status=NowcastStatus.UNAVAILABLE, sector=sector,
                             notes=[f"no adapter registered for sector {sector}"])
    try:
        result = await adapter.nowcast(symbol, event, as_of, session)
        if result is None:
            return NowcastResult(status=NowcastStatus.UNAVAILABLE, sector=sector,
                                 notes=[f"{sector} adapter returned None"])
        return result
    except Exception as exc:
        logger.warning(f"[pre_event_gap/nowcast] {symbol} ({sector}) adapter raised, failing closed: {exc}")
        return NowcastResult(status=NowcastStatus.UNAVAILABLE, sector=sector,
                             notes=[f"{sector} adapter raised: {str(exc)[:120]}"])
