"""ENERGY sector nowcast adapter (oil & gas, power/utilities — the
yfinance-label bucket sector_cache folds "utilities" into).

Energy earnings are driven by commodity/crude prices, regulated tariff
resets, and capacity-utilization swings — absent here, and the same
"trailing financials are a weak predictor of a commodity-linked business"
rationale as METALS applies, so this adapter carries an equally low
confidence ceiling.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class EnergyNowcastAdapter(FinancialsTrendAdapter):
    sector = "ENERGY"
    REQUIRED_INPUTS = ("commodity_prices", "regulated_tariffs", "capacity_utilization",
                       "input_costs", "demand_trends", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.25          # commodity/regulated-pricing driven; trailing financials weak
    qoq_is_meaningful = False
    economic_rationale = ("Energy earnings are commodity-price/regulated-tariff driven (absent), "
                          "similar unpredictability profile to Metals; confidence capped very low.")


register_adapter(EnergyNowcastAdapter())
