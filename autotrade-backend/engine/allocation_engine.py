"""Asset Allocation Engine — classifies holdings, computes allocation map,
rebalancing actions, and risk scores for the allocation analyzer."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import SIPFund, SIPInvestment
from engine.portfolio_service import calculate_portfolio_summary
from engine.sip_engine import POPULAR_FUNDS, fetch_current_nav
from utils.logger import logger

# ── Fund lookup dict built from POPULAR_FUNDS list ───────────────────────────

FUND_LOOKUP: dict[str, dict] = {f["scheme_code"]: f for f in POPULAR_FUNDS}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION A — ASSET CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

LARGE_CAP_STOCKS: set[str] = {
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS",
    "ICICIBANK.NS", "HINDUNILVR.NS", "SBIN.NS",
    "BHARTIARTL.NS", "ITC.NS", "KOTAKBANK.NS",
    "LT.NS", "AXISBANK.NS", "ASIANPAINT.NS", "MARUTI.NS",
    "BAJFINANCE.NS", "WIPRO.NS", "HCLTECH.NS",
    "ULTRACEMCO.NS", "NESTLEIND.NS", "POWERGRID.NS",
    "NTPC.NS", "COALINDIA.NS", "SUNPHARMA.NS",
    "DRREDDY.NS", "ONGC.NS", "BPCL.NS",
    "TITAN.NS", "BAJAJ-AUTO.NS", "GRASIM.NS",
    "TECHM.NS", "INDUSINDBK.NS", "HINDALCO.NS",
    "JSWSTEEL.NS", "ADANIENT.NS", "ADANIPORTS.NS",
    "VEDL.NS", "TATASTEEL.NS", "BRITANNIA.NS",
    "BAJAJFINSV.NS", "HDFCLIFE.NS", "SBILIFE.NS",
    "TATAMOTORS.NS", "CIPLA.NS", "M&M.NS",
    "DMART.NS", "TRENT.NS", "ZOMATO.NS",
}

MID_CAP_STOCKS: set[str] = {
    "PERSISTENT.NS", "COFORGE.NS", "LTTS.NS",
    "TATAELXSI.NS", "MUTHOOTFIN.NS", "PIDILITIND.NS",
    "ASTRAL.NS", "VOLTAS.NS", "METROPOLIS.NS",
    "LALPATHLAB.NS", "DABUR.NS", "HAVELLS.NS",
    "CROMPTON.NS", "IDFC.NS", "FEDERALBNK.NS",
    "BANDHANBNK.NS", "ABCAPITAL.NS", "CHOLAFIN.NS",
    "MARICO.NS", "GODREJCP.NS", "VBL.NS",
    "BERGEPAINT.NS", "DIVISLAB.NS", "EICHERMOT.NS",
    "HEROMOTOCO.NS", "SHRIRAMFIN.NS",
}

GOLD_SYMBOLS: set[str] = {
    "GC=F", "GOLDBEES.NS", "SGBMAR26.NS", "SGBSEP26.NS", "NIPPON_GOLD",
}

INTERNATIONAL_SYMBOLS: set[str] = {
    "INDA", "EPI", "NIFTYBEES.NS",
}

REITS: set[str] = {
    "EMBASSY.NS", "MINDSPACE.NS", "BROOKFIELD.NS",
}


def classify_symbol(symbol: str) -> str:
    """Return asset class for a stock/ETF symbol."""
    sym = symbol.upper()
    if sym in LARGE_CAP_STOCKS:   return "large_cap"
    if sym in MID_CAP_STOCKS:     return "mid_cap"
    if sym in GOLD_SYMBOLS:       return "gold"
    if sym in INTERNATIONAL_SYMBOLS: return "international"
    if sym in REITS:              return "other"
    if ".NS" in sym or ".BO" in sym: return "small_cap"
    return "other"


_CATEGORY_TO_ASSET: dict[str, str] = {
    "Large Cap":       "large_cap",
    "Flexi Cap":       "large_cap",
    "Large & Mid Cap": "large_cap",
    "Mid Cap":         "mid_cap",
    "Small Cap":       "small_cap",
    "ELSS":            "large_cap",
    "Index":           "large_cap",
    "Debt":            "debt",
    "Liquid":          "cash",
    "Overnight":       "cash",
    "Gold":            "gold",
    "International":   "international",
    "Hybrid":          "large_cap",
    "Sectoral":        "small_cap",
    "Equity":          "large_cap",
}


def classify_mf_scheme(scheme_code: str, category: str) -> str:
    """Map a mutual fund scheme to an asset class."""
    if scheme_code in FUND_LOOKUP:
        cat = FUND_LOOKUP[scheme_code].get("category", category)
        return _CATEGORY_TO_ASSET.get(cat, "other")
    return _CATEGORY_TO_ASSET.get(category, "other")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION B — PORTFOLIO AGGREGATOR
# ─────────────────────────────────────────────────────────────────────────────

def _empty_allocation() -> dict:
    classes = ["large_cap", "mid_cap", "small_cap", "debt", "gold", "international", "cash", "other"]
    return {c: {"value": 0.0, "holdings": [], "total_pct": 0.0} for c in classes}


async def build_allocation_map(
    portfolio_id: Optional[str],
    sip_goal_ids: list[str],
    session: AsyncSession,
) -> tuple[dict, float]:
    """Aggregate all investments into asset class buckets.

    Returns (allocation_dict, total_value).
    """
    result = _empty_allocation()

    # ── Step 1: stock holdings ────────────────────────────────────────────────
    stocks_total = 0.0
    if portfolio_id:
        try:
            summary = await calculate_portfolio_summary(portfolio_id, session)
            if summary:
                for h in summary.get("holdings", []):
                    val = h.get("current_value", 0.0)
                    if val <= 0:
                        continue
                    sym = h.get("symbol", "")
                    asset_class = classify_symbol(sym)
                    result[asset_class]["value"] += val
                    result[asset_class]["holdings"].append({
                        "name":             sym.replace(".NS", "").replace(".BO", ""),
                        "full_name":        h.get("company_name") or sym,
                        "type":             "stock",
                        "value":            round(val, 2),
                        "pnl_pct":          h.get("pnl_pct", 0.0),
                        "weight_in_class":  0.0,
                    })
                    stocks_total += val
        except Exception as exc:
            logger.warning(f"[allocation_engine] portfolio fetch error: {exc}")

    # ── Step 2: mutual fund SIP investments ───────────────────────────────────
    mf_total = 0.0
    if sip_goal_ids:
        for goal_id in sip_goal_ids:
            try:
                res = await session.execute(
                    select(SIPInvestment).where(SIPInvestment.goal_id == goal_id)
                )
                investments = list(res.scalars().all())

                res2 = await session.execute(
                    select(SIPFund).where(SIPFund.goal_id == goal_id)
                )
                funds = {f.scheme_code: f for f in res2.scalars().all()}

                # Group by scheme_code
                scheme_map: dict[str, dict] = {}
                for inv in investments:
                    code = inv.scheme_code
                    if code not in scheme_map:
                        scheme_map[code] = {
                            "units": 0.0,
                            "invested": 0.0,
                            "scheme_name": inv.scheme_name,
                            "fund_obj": funds.get(code),
                        }
                    scheme_map[code]["units"] += inv.units_purchased
                    scheme_map[code]["invested"] += inv.amount

                for code, data in scheme_map.items():
                    total_units = data["units"]
                    if total_units <= 0:
                        continue

                    nav = await fetch_current_nav(code)
                    if nav is None:
                        nav = 0.0

                    current_value = total_units * nav
                    fund_obj = data["fund_obj"]
                    fund_info = FUND_LOOKUP.get(code, {})
                    category  = (
                        fund_obj.category if fund_obj else
                        fund_info.get("category", "Equity")
                    )
                    fund_name = (
                        fund_obj.scheme_name if fund_obj else
                        fund_info.get("scheme_name", data["scheme_name"])
                    )
                    asset_class = classify_mf_scheme(code, category)

                    result[asset_class]["value"] += current_value
                    result[asset_class]["holdings"].append({
                        "name":             code,
                        "full_name":        fund_name or data["scheme_name"],
                        "type":             "mutual_fund",
                        "category":         category,
                        "value":            round(current_value, 2),
                        "nav":              nav,
                        "units":            round(total_units, 4),
                        "weight_in_class":  0.0,
                    })
                    mf_total += current_value
            except Exception as exc:
                logger.warning(f"[allocation_engine] SIP goal {goal_id} error: {exc}")

    # ── Step 3: weight_in_class ───────────────────────────────────────────────
    for cls_data in result.values():
        cls_total = cls_data["value"]
        for h in cls_data["holdings"]:
            h["weight_in_class"] = round(
                h["value"] / cls_total * 100, 1
            ) if cls_total > 0 else 0.0

    # ── Step 4: total percentage ──────────────────────────────────────────────
    total = sum(c["value"] for c in result.values())
    for cls_data in result.values():
        cls_data["total_pct"] = round(
            cls_data["value"] / total * 100, 2
        ) if total > 0 else 0.0

    return result, round(total, 2)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION C — REBALANCING CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

_CLASS_NAMES: dict[str, str] = {
    "large_cap":     "Large Cap Equity",
    "mid_cap":       "Mid Cap Equity",
    "small_cap":     "Small Cap Equity",
    "debt":          "Debt / Fixed Income",
    "gold":          "Gold",
    "international": "International Equity",
    "cash":          "Cash / Liquid",
    "other":         "Other",
}


@dataclass
class RebalancingAction:
    asset_class:   str
    current_value: float
    current_pct:   float
    target_pct:    float
    deviation_pct: float
    action:        str
    amount_inr:    float
    priority:      str
    suggestion:    str


def calculate_rebalancing(
    current_allocation: dict,
    target_allocation: dict,
    total_portfolio_value: float,
    rebalancing_threshold: float = 5.0,
    new_investment: float = 0.0,
) -> list[RebalancingAction]:
    """Compute buy/sell/hold actions for each asset class."""
    actions: list[RebalancingAction] = []
    target_total_value = total_portfolio_value + new_investment

    for asset_class, target_pct in target_allocation.items():
        current_data  = current_allocation.get(asset_class, {"value": 0.0, "total_pct": 0.0})
        current_value = current_data.get("value", 0.0)
        current_pct   = current_data.get("total_pct", 0.0)
        target_value  = target_total_value * target_pct / 100.0
        deviation     = current_pct - target_pct
        amount        = abs(target_value - current_value)

        if abs(deviation) < rebalancing_threshold:
            action, priority = "HOLD", "LOW"
        elif deviation > 0:
            action   = "SELL"
            priority = "HIGH" if abs(deviation) > 10 else "MEDIUM"
        else:
            action   = "BUY"
            priority = "HIGH" if abs(deviation) > 10 else "MEDIUM"

        if new_investment > 0 and action == "BUY":
            amount = min(amount, new_investment)

        class_name = _CLASS_NAMES.get(asset_class, asset_class)
        if action == "BUY":
            suggestion = f"Invest ₹{amount:,.0f} more in {class_name} funds"
        elif action == "SELL":
            suggestion = f"Reduce {class_name} by ₹{amount:,.0f} (currently overweight)"
        else:
            suggestion = f"{class_name} is within target range — no action needed"

        actions.append(RebalancingAction(
            asset_class=asset_class,
            current_value=round(current_value, 2),
            current_pct=round(current_pct, 2),
            target_pct=round(target_pct, 2),
            deviation_pct=round(deviation, 2),
            action=action,
            amount_inr=round(amount, 2),
            priority=priority,
            suggestion=suggestion,
        ))

    return sorted(actions, key=lambda x: abs(x.deviation_pct), reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION D — RISK SCORE CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

_RISK_WEIGHTS: dict[str, float] = {
    "small_cap":     9.0,
    "mid_cap":       7.0,
    "large_cap":     5.0,
    "international": 6.0,
    "gold":          4.0,
    "debt":          2.0,
    "cash":          1.0,
    "other":         5.0,
}


def calculate_portfolio_risk_score(allocation: dict) -> dict:
    """Return a risk score 1-10 with label and color."""
    total_value = sum(c.get("value", 0.0) for c in allocation.values())
    if total_value == 0:
        return {"score": 0, "label": "Unknown", "color": "#64748B",
                "equity_pct": 0.0, "debt_pct": 0.0, "gold_pct": 0.0}

    weighted_score = sum(
        _RISK_WEIGHTS.get(cls, 5.0) * (data.get("value", 0.0) / total_value)
        for cls, data in allocation.items()
    )
    score = round(weighted_score, 1)

    if score <= 2:   label, color = "Very Conservative",   "#3B82F6"
    elif score <= 3: label, color = "Conservative",         "#10B981"
    elif score <= 4: label, color = "Moderate",             "#F59E0B"
    elif score <= 6: label, color = "Moderate Aggressive",  "#F97316"
    elif score <= 7: label, color = "Aggressive",           "#EF4444"
    else:            label, color = "Very Aggressive",      "#7F1D1D"

    equity_pct = (
        allocation.get("large_cap",  {}).get("total_pct", 0.0) +
        allocation.get("mid_cap",    {}).get("total_pct", 0.0) +
        allocation.get("small_cap",  {}).get("total_pct", 0.0)
    )

    return {
        "score":      score,
        "label":      label,
        "color":      color,
        "equity_pct": round(equity_pct, 1),
        "debt_pct":   round(allocation.get("debt",  {}).get("total_pct", 0.0), 1),
        "gold_pct":   round(allocation.get("gold",  {}).get("total_pct", 0.0), 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SECTION E — RISK PROFILER
# ─────────────────────────────────────────────────────────────────────────────

_BASE_ALLOCATIONS: dict[str, dict] = {
    "conservative": {
        "large_cap": 20, "mid_cap": 0,  "small_cap": 0,
        "debt": 60, "gold": 15, "international": 0, "cash": 5,
    },
    "moderate_conservative": {
        "large_cap": 30, "mid_cap": 5,  "small_cap": 0,
        "debt": 45, "gold": 15, "international": 0, "cash": 5,
    },
    "moderate": {
        "large_cap": 40, "mid_cap": 15, "small_cap": 5,
        "debt": 30, "gold": 10, "international": 0, "cash": 0,
    },
    "moderate_aggressive": {
        "large_cap": 45, "mid_cap": 20, "small_cap": 10,
        "debt": 20, "gold": 5,  "international": 0, "cash": 0,
    },
    "aggressive": {
        "large_cap": 35, "mid_cap": 30, "small_cap": 20,
        "debt": 10, "gold": 5,  "international": 0, "cash": 0,
    },
    "very_aggressive": {
        "large_cap": 25, "mid_cap": 35, "small_cap": 35,
        "debt": 5,  "gold": 0,  "international": 0, "cash": 0,
    },
}

_PROFILE_META: dict[str, dict] = {
    "conservative": {
        "color": "#10B981",
        "cagr_range": "7-9%",
        "horizon": "< 3 years",
        "description": "Capital preservation with stable returns",
        "suitable_for": "Short-term goals, near-retirement investors",
    },
    "moderate_conservative": {
        "color": "#06B6D4",
        "cagr_range": "9-11%",
        "horizon": "3-5 years",
        "description": "Modest growth with limited volatility",
        "suitable_for": "Medium-term goals, conservative first-time investors",
    },
    "moderate": {
        "color": "#3B82F6",
        "cagr_range": "11-13%",
        "horizon": "5-7 years",
        "description": "Balanced growth and stability",
        "suitable_for": "Goal-based investing, salaried professionals",
    },
    "moderate_aggressive": {
        "color": "#F59E0B",
        "cagr_range": "12-15%",
        "horizon": "7-10 years",
        "description": "Higher growth with manageable risk",
        "suitable_for": "Wealth creation, investors 30-45 years old",
    },
    "aggressive": {
        "color": "#F97316",
        "cagr_range": "14-18%",
        "horizon": "10+ years",
        "description": "High growth with significant volatility",
        "suitable_for": "Long-term wealth creation, young investors",
    },
    "very_aggressive": {
        "color": "#EF4444",
        "cagr_range": "16-22%",
        "horizon": "15+ years",
        "description": "Maximum growth, very high risk tolerance",
        "suitable_for": "Young investors, high risk tolerance, very long horizon",
    },
}


def get_recommended_allocation(
    age: int,
    risk_profile: str,
    investment_horizon_years: int,
    monthly_income: float = 0.0,
    has_emergency_fund: bool = True,
) -> dict:
    """Return target allocation dict with adjustments for age/horizon/emergency fund."""
    profile = risk_profile.lower().replace(" ", "_")
    if profile not in _BASE_ALLOCATIONS:
        profile = "moderate"

    alloc = dict(_BASE_ALLOCATIONS[profile])

    # Override to conservative if horizon < 3 years
    if investment_horizon_years < 3:
        alloc = dict(_BASE_ALLOCATIONS["conservative"])
    else:
        # Age adjustments
        if age > 55:
            alloc["debt"] = min(70, alloc["debt"] + 10)
            alloc["small_cap"] = 0
            excess = alloc["debt"] - _BASE_ALLOCATIONS[profile]["debt"] - 10
            if excess < 0:
                pass
            alloc["large_cap"] = max(10, alloc["large_cap"] - 5)
            alloc["mid_cap"]   = max(0,  alloc["mid_cap"]   - 5)
        elif age < 30:
            alloc["small_cap"] = min(alloc["small_cap"] + 5, 40)
            alloc["debt"]      = max(0, alloc["debt"] - 5)

        # No emergency fund: add 10% cash, reduce equity
        if not has_emergency_fund:
            alloc["cash"]      = alloc.get("cash", 0) + 10
            alloc["large_cap"] = max(0, alloc["large_cap"] - 5)
            alloc["mid_cap"]   = max(0, alloc["mid_cap"]   - 5)

    # Normalise to 100
    total = sum(alloc.values())
    if total != 100:
        diff  = 100 - total
        alloc["large_cap"] = max(0, alloc["large_cap"] + diff)

    return alloc


def run_risk_questionnaire(answers: dict) -> str:
    """Score a 5-question risk questionnaire and return a profile string."""
    q1 = int(answers.get("q1_horizon", 2))
    q2 = int(answers.get("q2_reaction", 2))
    q3 = int(answers.get("q3_goal", 2))
    q4 = int(answers.get("q4_income", 2))
    q5 = int(answers.get("q5_experience", 2))

    total = q1 + q2 + q3 + q4 + q5
    if total <= 7:  return "conservative"
    if total <= 10: return "moderate_conservative"
    if total <= 13: return "moderate"
    if total <= 16: return "moderate_aggressive"
    if total <= 18: return "aggressive"
    return "very_aggressive"


def get_all_profiles() -> dict:
    """Return all profile definitions for the profile comparison endpoint."""
    result = {}
    for name, alloc in _BASE_ALLOCATIONS.items():
        meta = _PROFILE_META[name]
        result[name] = {
            "allocation":   alloc,
            "color":        meta["color"],
            "cagr_range":   meta["cagr_range"],
            "horizon":      meta["horizon"],
            "description":  meta["description"],
            "suitable_for": meta["suitable_for"],
        }
    return result
