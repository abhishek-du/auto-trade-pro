"""Shared base for financials-trend sector adapters (Phase 5.5).

Each concrete adapter encodes a SECTOR-SPECIFIC interpretation of the recent
financial trend — NOT a generic PAT extrapolation. The base handles the common
mechanics (point-in-time series, direction classification, honest
completeness/confidence) and every subclass MUST declare, explicitly:

  * REQUIRED_INPUTS   — what an ideal nowcast for this sector actually needs
  * AVAILABLE_INPUTS  — what this codebase genuinely has
  * (MISSING_INPUTS is derived and reported)
  * confidence_ceiling — hard cap reflecting how well trailing financials can
                         actually predict this sector's surprise
  * qoq_is_meaningful  — True only for low-seasonality sectors (e.g. IT)
  * economic_rationale — one line on WHY this interpretation is valid
  * sector_unavailable_reason() — sector-specific NOWCAST_UNAVAILABLE conditions

Adapters do NOT fabricate unavailable sector drivers. When a sector's real
drivers are absent (commodities for METALS, FDA actions for PHARMA, monthly
volumes/EV mix for AUTO), the adapter says so, caps confidence low, and — where
the trailing trend is not even a weak proxy — returns NOWCAST_UNAVAILABLE.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from engine.pre_event_expectation_gap.types import (
    ScheduledEvent, NowcastResult, NowcastStatus, Direction,
)
from engine.pre_event_expectation_gap.sector_adapters.base import SectorNowcastAdapter
from engine.pre_event_expectation_gap.financials import (
    get_pit_quarterly_series, recent_growth, QuarterlySeries,
)

DIR_THRESHOLD = 0.03


def direction_of(growth: float | None) -> Direction:
    if growth is None:
        return Direction.NEUTRAL
    if growth > DIR_THRESHOLD:
        return Direction.POSITIVE
    if growth < -DIR_THRESHOLD:
        return Direction.NEGATIVE
    return Direction.NEUTRAL


class FinancialsTrendAdapter(SectorNowcastAdapter):
    sector: str = ""
    REQUIRED_INPUTS: tuple = ("quarterly_financials",)
    AVAILABLE_INPUTS: tuple = ("quarterly_financials",)
    confidence_ceiling: float = 0.40
    qoq_is_meaningful: bool = False
    economic_rationale: str = ""
    min_quarters: int = 2

    @property
    def missing_inputs(self) -> tuple:
        return tuple(i for i in self.REQUIRED_INPUTS if i not in self.AVAILABLE_INPUTS)

    def sector_unavailable_reason(self, series: QuarterlySeries) -> str | None:
        """Sector-specific NOWCAST_UNAVAILABLE hook. Default: none."""
        return None

    def _base_notes(self) -> list[str]:
        return [
            f"rationale: {self.economic_rationale}",
            f"required_inputs={list(self.REQUIRED_INPUTS)}",
            f"available_inputs={list(self.AVAILABLE_INPUTS)}",
            f"missing_inputs={list(self.missing_inputs)}",
            f"confidence_ceiling={self.confidence_ceiling}",
        ]

    async def nowcast(
        self, symbol: str, event: ScheduledEvent, as_of: datetime, session: AsyncSession,
    ) -> NowcastResult:
        as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
        notes = self._base_notes()

        series = await get_pit_quarterly_series(symbol, as_of_date, session)

        reason = self.sector_unavailable_reason(series)
        if reason:
            return NowcastResult(status=NowcastStatus.UNAVAILABLE, sector=self.sector, notes=notes + [reason])

        if series.n_profit() < self.min_quarters and series.n_revenue() < self.min_quarters:
            return NowcastResult(
                status=NowcastStatus.UNAVAILABLE, sector=self.sector,
                notes=notes + [f"insufficient point-in-time quarters "
                               f"(profit={series.n_profit()}, revenue={series.n_revenue()}) at {as_of_date}"],
            )

        p_growth, p_is_yoy = recent_growth(series.net_profit)
        r_growth, _ = recent_growth(series.revenue)
        rev_dir = direction_of(r_growth)
        profit_dir = direction_of(p_growth)

        if p_growth is not None and r_growth is not None:
            spread = p_growth - r_growth
            margin_dir = (Direction.POSITIVE if spread > DIR_THRESHOLD
                          else Direction.NEGATIVE if spread < -DIR_THRESHOLD else Direction.NEUTRAL)
        else:
            margin_dir = Direction.NEUTRAL

        data_completeness = round(len(self.AVAILABLE_INPUTS) / len(self.REQUIRED_INPUTS), 3)
        n_q = max(series.n_profit(), series.n_revenue())
        history_factor = min(n_q / 5.0, 1.0)
        # Coarse (QoQ) trend is penalized unless this sector's QoQ is meaningful.
        coarse_penalty = 1.0 if (p_is_yoy or self.qoq_is_meaningful) else 0.6
        confidence = round(min(
            self.confidence_ceiling,
            self.confidence_ceiling * history_factor * coarse_penalty * (0.5 + data_completeness),
        ), 3)

        notes.append(
            f"trend from {n_q} point-in-time quarters (yoy={p_is_yoy}): "
            f"profit_growth={None if p_growth is None else round(p_growth, 3)}, "
            f"revenue_growth={None if r_growth is None else round(r_growth, 3)}")

        return NowcastResult(
            status=NowcastStatus.OK, sector=self.sector,
            revenue_direction=rev_dir, profit_direction=profit_dir, margin_direction=margin_dir,
            confidence=confidence, data_completeness=data_completeness, notes=notes,
            implied_revenue_growth=(None if r_growth is None else round(r_growth, 4)),
            implied_profit_growth=(None if p_growth is None else round(p_growth, 4)),
            implied_is_annual=p_is_yoy,
        )
