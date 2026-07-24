"""IT sector nowcast adapter.

Indian IT services are LOW-seasonality, so sequential (QoQ) revenue growth is
genuinely meaningful (unlike auto). The real drivers are USD revenue growth,
deal wins/bookings, margin, attrition and currency — none available here. This
adapter uses the recent sequential trend (QoQ treated as meaningful) with a
moderate confidence ceiling.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class ITNowcastAdapter(FinancialsTrendAdapter):
    sector = "IT"
    REQUIRED_INPUTS = ("revenue_growth_usd", "deal_wins_bookings", "operating_margin",
                       "attrition", "currency", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.45          # low-seasonality → QoQ meaningful, slightly higher ceiling
    qoq_is_meaningful = True
    economic_rationale = ("IT is low-seasonality; sequential (QoQ) revenue growth + margin "
                          "stability are meaningful. Deal wins/attrition/currency unavailable.")


register_adapter(ITNowcastAdapter())
