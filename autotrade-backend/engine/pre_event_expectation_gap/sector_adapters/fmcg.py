"""FMCG sector nowcast adapter.

FMCG is a low-growth, defensive sector: quarters are stable and surprises are
small. The real drivers are volume growth, price growth, input-cost inflation
and gross margin — none available as structured data here. So this adapter
reads the recent financial trend and flags deviation from a steady norm, with a
LOW confidence ceiling because FMCG's genuine surprises are modest and its
operational drivers are missing.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class FMCGNowcastAdapter(FinancialsTrendAdapter):
    sector = "FMCG"
    REQUIRED_INPUTS = ("volume_growth", "price_growth", "input_cost_inflation",
                       "gross_margin", "distribution_expansion", "rural_urban_demand",
                       "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.35          # small, stable surprises → low ceiling
    qoq_is_meaningful = False          # festive seasonality present
    economic_rationale = ("FMCG is low-growth/defensive; signal is the recent trend's deviation "
                          "from a steady single-digit norm. Volume/pricing/input-cost split unavailable.")


register_adapter(FMCGNowcastAdapter())
