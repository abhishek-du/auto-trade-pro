"""CONSUMER sector nowcast adapter (Consumer discretionary/cyclical — retail,
durables, apparel, etc.; the yfinance-label bucket sector_cache folds
"consumer cyclical"/"consumer discretionary" into).

Consumer-discretionary demand is driven by footfall/same-store-sales trends,
discretionary income cycles, and seasonal/festive demand swings — absent
here. Somewhat more cyclical than FMCG (staples demand is steadier), so this
adapter's confidence ceiling sits just below FMCG's.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class ConsumerNowcastAdapter(FinancialsTrendAdapter):
    sector = "CONSUMER"
    REQUIRED_INPUTS = ("same_store_sales", "footfall_trends", "discretionary_income",
                       "seasonal_demand", "input_costs", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.35          # cyclical discretionary demand; trailing financials limited
    qoq_is_meaningful = False
    economic_rationale = ("Consumer-discretionary demand is footfall/same-store-sales and "
                          "discretionary-income-cycle driven (absent). More cyclical than FMCG "
                          "staples; confidence capped moderately.")


register_adapter(ConsumerNowcastAdapter())
