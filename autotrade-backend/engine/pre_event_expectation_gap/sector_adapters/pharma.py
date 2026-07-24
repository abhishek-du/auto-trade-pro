"""PHARMA sector nowcast adapter.

Pharma surprises are driven by product approvals, USFDA actions, launches and
pricing pressure — event-based drivers that are absent here. Trailing financials
give limited signal, so this adapter carries a LOW confidence ceiling. It reaches
a decision (funnel value) but rarely clears the LONG bar on financials alone.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class PharmaNowcastAdapter(FinancialsTrendAdapter):
    sector = "PHARMA"
    REQUIRED_INPUTS = ("product_approvals", "usfda_actions", "launches", "pricing_pressure",
                       "export_trends", "api_input_costs", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.30          # approval/FDA-event driven; trailing financials limited
    qoq_is_meaningful = False
    economic_rationale = ("Pharma surprises are approval/USFDA/launch driven (event-based, absent). "
                          "Trailing financials give limited signal; confidence capped low.")


register_adapter(PharmaNowcastAdapter())
