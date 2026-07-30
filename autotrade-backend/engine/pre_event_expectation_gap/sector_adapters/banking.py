"""BANKING sector nowcast adapter (banks + NBFCs — the largest single
sector_cache bucket, 212 symbols in the live cache).

Added 2026-07-29 after BAJFINANCE.NS (results due the next day, a genuinely
strong-looking pre-event setup on every other check) hit an automatic
NO_TRADE purely because no Banking adapter existed at all -- a structural
gap, not a per-symbol judgment call, that excluded this entire sector.

KNOWN v1 LIMITATION (deliberate, not an oversight): a bank's real earnings
drivers are NII/NIM trend, credit growth, CASA mix, and asset quality (Net
NPA) -- NOT generic topline revenue, which for a bank conflates interest
income, fee income, and treasury gains in ways that don't cleanly separate
the way a manufacturer's revenue line does. Confirmed live that Upstox's
key-ratios endpoint (crawler/upstox_data.py::get_key_ratios) DOES return
real banking metrics (NIM, ROA, ROE, Net NPA, CASA) -- e.g. HDFCBANK.NS
returned NIM=3.28%, Net NPA=0.38%, CASA=34.0 when tested. But that endpoint
is a CURRENT-SNAPSHOT dict, not a historical time series like
get_income_statement()'s quarterly history -- there is today no
point-in-time-safe way to compute a NIM/NPA *trend* from it (see
point_in_time.py's own docstring: "Fundamentals remain a known limitation
(no historical as-of table)"). Building that (persisting key-ratios
snapshots over time, point-in-time filtering them) is real follow-up work,
out of scope here.

Until then, this adapter reuses the same generic quarterly revenue/net-
profit trend as every other sector (via FinancialsTrendAdapter) -- but
because that's a genuinely weaker proxy for a bank than for most other
sectors, confidence_ceiling is set BELOW every other adapter's, making this
sector's LONG bar structurally harder to clear than the rest. This is a
deliberate "reachable but appropriately cautious" v1, not a claim that
generic P&L trend is a good banking predictor.
"""
from __future__ import annotations
from engine.pre_event_expectation_gap.sector_adapters.common import FinancialsTrendAdapter
from engine.pre_event_expectation_gap.sector_adapters.base import register_adapter


class BankingNowcastAdapter(FinancialsTrendAdapter):
    sector = "BANKING"
    REQUIRED_INPUTS = ("nii_trend", "nim_trend", "credit_growth", "casa_mix",
                       "asset_quality_npa", "provisioning", "quarterly_financials")
    AVAILABLE_INPUTS = ("quarterly_financials",)
    confidence_ceiling = 0.20          # generic revenue/profit is a WEAK proxy for a bank; see
                                        # module docstring -- lowest ceiling of all 9 sectors, on
                                        # purpose, until a real NIM/NPA trend data source exists.
    qoq_is_meaningful = False
    economic_rationale = ("Bank earnings are NII/NIM/credit-growth/asset-quality driven (absent -- "
                          "real key-ratio data exists via Upstox but only as a current snapshot, "
                          "not a point-in-time-safe trend yet). Generic revenue/profit trend is a "
                          "materially weaker proxy for a bank than for most other sectors; "
                          "confidence capped lowest of all sectors as a result.")


register_adapter(BankingNowcastAdapter())
