"""Sector nowcast adapters. Importing this package registers all concrete
adapters into the registry (via each module's register_adapter() call at import
time), so callers only need `run_nowcast()` / `get_adapter()`.
"""
from __future__ import annotations

from engine.pre_event_expectation_gap.sector_adapters.base import (
    SectorNowcastAdapter,
    register_adapter,
    get_adapter,
    registered_sectors,
    resolve_strategy_sector,
    run_nowcast,
)

# Register concrete adapters (import side effect). Order = spec adapter priority.
from engine.pre_event_expectation_gap.sector_adapters import fmcg    # noqa: F401  (FMCG)
from engine.pre_event_expectation_gap.sector_adapters import it      # noqa: F401  (IT)
from engine.pre_event_expectation_gap.sector_adapters import auto    # noqa: F401  (AUTO)
from engine.pre_event_expectation_gap.sector_adapters import metals  # noqa: F401  (METALS)
from engine.pre_event_expectation_gap.sector_adapters import pharma  # noqa: F401  (PHARMA)

__all__ = [
    "SectorNowcastAdapter",
    "register_adapter",
    "get_adapter",
    "registered_sectors",
    "resolve_strategy_sector",
    "run_nowcast",
]
