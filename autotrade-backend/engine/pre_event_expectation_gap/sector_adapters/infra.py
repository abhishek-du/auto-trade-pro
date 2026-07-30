"""INFRA sector nowcast adapter (infrastructure, construction, real estate —
the yfinance-label bucket sector_cache folds "real estate" into).

Infra/construction revenue recognition is lumpy and project-milestone
driven — order-book execution pace and project completions are the true
predictors and are absent here, making trailing quarter-over-quarter
financials a noisy, low-confidence signal.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class InfraNowcastAdapter(FinancialsTrendAdapter):
    sector = "INFRA"
    REQUIRED_INPUTS = ("order_book", "execution_pace", "project_completions",
                       "receivables_trend", "input_costs", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.30          # lumpy, project-milestone-driven revenue recognition
    qoq_is_meaningful = False
    economic_rationale = ("Infra/construction revenue is order-book/execution-pace driven "
                          "(absent) with lumpy, milestone-based recognition; confidence capped low.")


register_adapter(InfraNowcastAdapter())
