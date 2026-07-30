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
from engine.pre_event_expectation_gap.sector_adapters import fmcg     # noqa: F401  (FMCG)
from engine.pre_event_expectation_gap.sector_adapters import it       # noqa: F401  (IT)
from engine.pre_event_expectation_gap.sector_adapters import auto     # noqa: F401  (AUTO)
from engine.pre_event_expectation_gap.sector_adapters import metals   # noqa: F401  (METALS)
from engine.pre_event_expectation_gap.sector_adapters import pharma   # noqa: F401  (PHARMA)
# Added 2026-07-29 (user request: all sectors, not just 5) — see each
# module's docstring for sector-specific rationale/confidence-ceiling notes.
from engine.pre_event_expectation_gap.sector_adapters import banking  # noqa: F401  (BANKING)
from engine.pre_event_expectation_gap.sector_adapters import consumer # noqa: F401  (CONSUMER)
from engine.pre_event_expectation_gap.sector_adapters import energy   # noqa: F401  (ENERGY)
from engine.pre_event_expectation_gap.sector_adapters import infra    # noqa: F401  (INFRA)
from engine.pre_event_expectation_gap.sector_adapters import telecom  # noqa: F401  (TELECOM)

__all__ = [
    "SectorNowcastAdapter",
    "register_adapter",
    "get_adapter",
    "registered_sectors",
    "resolve_strategy_sector",
    "run_nowcast",
]
