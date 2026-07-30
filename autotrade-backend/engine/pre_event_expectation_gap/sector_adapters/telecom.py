"""TELECOM sector nowcast adapter (the yfinance-label bucket sector_cache
folds "communication services" into).

Telecom earnings are subscriber-count and ARPU driven — comparatively
steady, low-seasonality metrics (closer to IT's predictability than to
Metals'/Energy's commodity volatility), so this adapter carries a
moderate-to-higher confidence ceiling among the newly-added sectors.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class TelecomNowcastAdapter(FinancialsTrendAdapter):
    sector = "TELECOM"
    REQUIRED_INPUTS = ("subscriber_growth", "arpu_trend", "churn", "spectrum_capex",
                       "tariff_hikes", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.40          # subscriber/ARPU driven; comparatively steady, low seasonality
    qoq_is_meaningful = False
    economic_rationale = ("Telecom earnings are subscriber-count/ARPU driven (absent) but "
                          "comparatively steady/low-seasonality; confidence capped moderately.")


register_adapter(TelecomNowcastAdapter())
