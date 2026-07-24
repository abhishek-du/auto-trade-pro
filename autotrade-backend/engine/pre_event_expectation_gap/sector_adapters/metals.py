"""METALS sector nowcast adapter.

Metals earnings are COMMODITY-CYCLE driven — realizations track global metal
prices and spreads, which are the true predictors and are entirely absent here.
Trailing financials are a WEAK predictor of the next quarter, so this adapter
carries a very LOW confidence ceiling: it will reach a decision (useful for the
funnel) but should almost never clear the LONG bar on trailing financials alone.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class MetalsNowcastAdapter(FinancialsTrendAdapter):
    sector = "METALS"
    REQUIRED_INPUTS = ("commodity_prices", "spreads", "production", "realization",
                       "input_costs", "export_demand", "china_global_demand", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.25          # commodity-driven; trailing financials a poor predictor
    qoq_is_meaningful = False
    economic_rationale = ("Metals earnings are commodity-price/spread driven (absent). Trailing "
                          "financials are a weak predictor; confidence capped very low.")


register_adapter(MetalsNowcastAdapter())
