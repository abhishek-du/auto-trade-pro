"""AUTO sector nowcast adapter (improved for Phase 5.5).

Autos are HIGHLY seasonal, so YoY is the right comparison and QoQ is misleading.
With only ~4 quarters of point-in-time data a YoY is rarely computable, so the
adapter falls back to a penalized QoQ read (via the shared base) and keeps
confidence modest. Real drivers — monthly wholesale/retail volumes, domestic/
export split, EV mix, ASP, commodity input costs, currency — are unavailable.
Margin is read through operating leverage (profit vs revenue spread).
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class AutoNowcastAdapter(FinancialsTrendAdapter):
    sector = "AUTO"
    REQUIRED_INPUTS = ("monthly_sales_volume", "domestic_export_split", "ev_mix", "asp",
                       "commodity_input_costs", "currency_impact", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.50
    qoq_is_meaningful = False          # seasonal → QoQ penalized, YoY preferred (rarely available)
    economic_rationale = ("Auto is highly seasonal; YoY preferred but rarely available at 4-quarter "
                          "depth. Margin via operating leverage. Monthly volumes/EV mix/ASP/commodities unavailable.")


register_adapter(AutoNowcastAdapter())
