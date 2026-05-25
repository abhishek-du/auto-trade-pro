"""SIP Tracker engine — NAV fetching, projections, goal progress calculations."""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from db.models import SIPGoal, SIPFund, SIPInvestment
from engine.portfolio_service import calculate_xirr
from utils.logger import logger

# ── NAV cache (4-hour TTL) ────────────────────────────────────────────────────

_nav_cache: dict[str, tuple[float, float]] = {}   # {scheme_code: (nav, timestamp)}
_NAV_TTL = 4 * 3600


def _nav_cached(scheme_code: str) -> Optional[float]:
    entry = _nav_cache.get(scheme_code)
    if entry and (time.time() - entry[1]) < _NAV_TTL:
        return entry[0]
    return None


def _nav_set(scheme_code: str, nav: float) -> None:
    _nav_cache[scheme_code] = (nav, time.time())


# ── NAV fetching ──────────────────────────────────────────────────────────────

async def fetch_current_nav(scheme_code: str) -> Optional[float]:
    cached = _nav_cached(scheme_code)
    if cached is not None:
        return cached
    loop = asyncio.get_event_loop()
    try:
        nav = await loop.run_in_executor(None, _fetch_nav_sync, scheme_code)
        if nav:
            _nav_set(scheme_code, nav)
        return nav
    except Exception as exc:
        logger.warning(f"[sip_engine] fetch_current_nav {scheme_code}: {exc}")
        return None


def _fetch_nav_sync(scheme_code: str) -> Optional[float]:
    try:
        from mftool import Mftool
        mf = Mftool()
        quote = mf.get_scheme_quote(scheme_code)
        if isinstance(quote, dict):
            nav_str = quote.get("nav") or quote.get("Net Asset Value") or ""
            nav_str = str(nav_str).replace(",", "").strip()
            return float(nav_str) if nav_str else None
    except Exception as exc:
        logger.warning(f"[sip_engine] _fetch_nav_sync {scheme_code}: {exc}")
    return None


async def fetch_historical_nav(
    scheme_code: str,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Returns [{date, nav}] sorted ascending."""
    loop = asyncio.get_event_loop()
    try:
        rows = await loop.run_in_executor(
            None, _fetch_historical_sync, scheme_code, from_date, to_date
        )
        return rows
    except Exception as exc:
        logger.warning(f"[sip_engine] fetch_historical_nav {scheme_code}: {exc}")
        return []


def _fetch_historical_sync(
    scheme_code: str,
    from_date: date,
    to_date: date,
) -> list[dict]:
    try:
        from mftool import Mftool
        mf = Mftool()
        data = mf.get_scheme_historical_nav(scheme_code, as_Pandas=False)
        if not isinstance(data, dict):
            return []
        rows = data.get("data", [])
        result = []
        for row in rows:
            try:
                d = datetime.strptime(row["date"], "%d-%m-%Y").date()
                if from_date <= d <= to_date:
                    result.append({"date": d, "nav": float(row["nav"])})
            except (KeyError, ValueError):
                continue
        result.sort(key=lambda x: x["date"])
        return result
    except Exception as exc:
        logger.warning(f"[sip_engine] _fetch_historical_sync {scheme_code}: {exc}")
        return []


async def fetch_nav_on_date(scheme_code: str, target_date: date) -> Optional[float]:
    """Returns NAV on target_date or the closest prior available date."""
    rows = await fetch_historical_nav(scheme_code, target_date - timedelta(days=10), target_date)
    if not rows:
        return await fetch_current_nav(scheme_code)
    for row in reversed(rows):
        if row["date"] <= target_date:
            return row["nav"]
    return rows[0]["nav"] if rows else None


# ── Fund search ───────────────────────────────────────────────────────────────

POPULAR_FUNDS = [
    {"scheme_code": "120503", "scheme_name": "Mirae Asset Large Cap Fund - Direct Plan - Growth",         "fund_house": "Mirae Asset",       "category": "Large Cap"},
    {"scheme_code": "119551", "scheme_name": "Axis Bluechip Fund Direct Plan Growth",                     "fund_house": "Axis",              "category": "Large Cap"},
    {"scheme_code": "120716", "scheme_name": "Mirae Asset Emerging Bluechip Fund - Direct Plan - Growth", "fund_house": "Mirae Asset",       "category": "Large & Mid Cap"},
    {"scheme_code": "125497", "scheme_name": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",        "fund_house": "PPFAS",             "category": "Flexi Cap"},
    {"scheme_code": "118989", "scheme_name": "SBI Small Cap Fund - Direct Plan - Growth",                 "fund_house": "SBI",               "category": "Small Cap"},
    {"scheme_code": "119598", "scheme_name": "Kotak Small Cap Fund - Direct Plan - Growth",               "fund_house": "Kotak",             "category": "Small Cap"},
    {"scheme_code": "120828", "scheme_name": "HDFC Mid-Cap Opportunities Fund - Direct Plan - Growth",    "fund_house": "HDFC",              "category": "Mid Cap"},
    {"scheme_code": "120847", "scheme_name": "HDFC Index Fund Nifty 50 Plan - Direct Plan - Growth",      "fund_house": "HDFC",              "category": "Index"},
    {"scheme_code": "120837", "scheme_name": "HDFC Top 100 Fund - Direct Plan - Growth",                  "fund_house": "HDFC",              "category": "Large Cap"},
    {"scheme_code": "127042", "scheme_name": "Nippon India Index Fund - Nifty 50 Plan - Direct Plan",     "fund_house": "Nippon",            "category": "Index"},
    {"scheme_code": "118825", "scheme_name": "UTI Nifty 50 Index Fund Direct Growth",                     "fund_house": "UTI",               "category": "Index"},
    {"scheme_code": "101305", "scheme_name": "ICICI Prudential Technology Fund - Direct Plan - Growth",   "fund_house": "ICICI Prudential",  "category": "Sectoral"},
    {"scheme_code": "120505", "scheme_name": "Mirae Asset Tax Saver Fund - Direct Plan - Growth",         "fund_house": "Mirae Asset",       "category": "ELSS"},
    {"scheme_code": "120597", "scheme_name": "Axis Long Term Equity Fund - Direct Plan - Growth",         "fund_house": "Axis",              "category": "ELSS"},
    {"scheme_code": "119775", "scheme_name": "Aditya Birla Sun Life PSU Equity Fund - Direct Plan",       "fund_house": "Aditya Birla",      "category": "Sectoral"},
]


async def search_mutual_funds(query: str) -> list[dict]:
    """Search popular funds first, then AMFI cache."""
    q = query.strip().lower()
    if len(q) < 2:
        return []

    results = []
    seen_codes = set()

    for f in POPULAR_FUNDS:
        if q in f["scheme_name"].lower():
            nav = await fetch_current_nav(f["scheme_code"])
            results.append({**f, "nav": nav, "popular": True})
            seen_codes.add(f["scheme_code"])

    # fill up to 15 from AMFI cache if available
    if len(results) < 15:
        try:
            from api.mf_tracker import _scheme_cache, _cache_lock, _cache_loaded
            if _cache_loaded:
                with _cache_lock:
                    cache = dict(_scheme_cache)
                for code, name in cache.items():
                    if code in seen_codes:
                        continue
                    if q in name.lower():
                        results.append({
                            "scheme_code": code,
                            "scheme_name": name,
                            "fund_house": "",
                            "category": _infer_cat(name),
                            "nav": None,
                            "popular": False,
                        })
                        if len(results) >= 15:
                            break
        except Exception:
            pass

    results.sort(key=lambda x: (not x.get("popular"), len(x["scheme_name"])))
    return results[:15]


def _infer_cat(name: str) -> str:
    n = name.lower()
    if "elss" in n or "tax saver" in n:          return "ELSS"
    if "index" in n or "nifty" in n:             return "Index"
    if "mid cap" in n or "midcap" in n:           return "Mid Cap"
    if "small cap" in n or "smallcap" in n:       return "Small Cap"
    if "large cap" in n or "largecap" in n:       return "Large Cap"
    if "hybrid" in n or "balanced" in n:          return "Hybrid"
    if "liquid" in n or "overnight" in n:         return "Liquid"
    if "debt" in n or "bond" in n or "gilt" in n: return "Debt"
    return "Equity"


# ── SIP projection formulas ───────────────────────────────────────────────────

def simulate_sip(
    monthly_amount: float,
    expected_return_pct: float,
    months: int,
    start_date: Optional[date] = None,
    step_up_pct: float = 0.0,
) -> dict:
    """Standard SIP future-value with optional annual step-up. Returns monthly data points."""
    if start_date is None:
        start_date = date.today().replace(day=1)

    r = expected_return_pct / 100 / 12
    points = []
    total_invested = 0.0
    corpus = 0.0
    current_amount = monthly_amount
    current_year = start_date.year

    for i in range(months):
        inv_date = date(
            start_date.year + (start_date.month + i - 1) // 12,
            (start_date.month + i - 1) % 12 + 1,
            1,
        )
        if step_up_pct > 0 and inv_date.year > current_year:
            current_year = inv_date.year
            current_amount = current_amount * (1 + step_up_pct / 100)

        corpus = corpus * (1 + r) + current_amount
        total_invested += current_amount
        points.append({
            "month": i + 1,
            "date": inv_date.isoformat(),
            "invested": round(total_invested, 2),
            "corpus": round(corpus, 2),
            "gain": round(corpus - total_invested, 2),
        })

    return {
        "monthly_amount": monthly_amount,
        "expected_return_pct": expected_return_pct,
        "months": months,
        "step_up_pct": step_up_pct,
        "total_invested": round(total_invested, 2),
        "projected_value": round(corpus, 2),
        "absolute_gain": round(corpus - total_invested, 2),
        "absolute_gain_pct": round((corpus / total_invested - 1) * 100, 2) if total_invested else 0,
        "data_points": points,
    }


def calculate_required_sip(
    target_amount: float,
    months: int,
    expected_return_pct: float,
) -> float:
    """Reverse PMT — how much monthly SIP needed to reach target_amount?"""
    r = expected_return_pct / 100 / 12
    if r == 0:
        return round(target_amount / months, 2)
    fv_factor = ((1 + r) ** months - 1) / r * (1 + r)
    return round(target_amount / fv_factor, 2)


def calculate_months_to_target(
    monthly_sip: float,
    target_amount: float,
    expected_return_pct: float,
    current_corpus: float = 0.0,
) -> int:
    """Binary search for months needed to reach target."""
    if monthly_sip <= 0:
        return 0
    for months in range(1, 601):
        r = expected_return_pct / 100 / 12
        fv = current_corpus * (1 + r) ** months
        if r > 0:
            fv += monthly_sip * ((1 + r) ** months - 1) / r * (1 + r)
        else:
            fv += monthly_sip * months
        if fv >= target_amount:
            return months
    return 600


def run_sip_calculator(
    monthly_amount: float,
    years: int,
    expected_return_pct: float,
    current_corpus: float = 0.0,
    step_up_pct: float = 0.0,
) -> dict:
    """Full SIP calculator with step-up support and yearly summary."""
    months = years * 12
    sim = simulate_sip(monthly_amount, expected_return_pct, months, step_up_pct=step_up_pct)

    # initial corpus growth
    r = expected_return_pct / 100 / 12
    corpus_growth = current_corpus * (1 + r) ** months if current_corpus else 0
    total_final = sim["projected_value"] + corpus_growth

    # yearly summary
    yearly = []
    for yr in range(1, years + 1):
        pt = sim["data_points"][yr * 12 - 1]
        yearly.append({
            "year": yr,
            "invested": pt["invested"],
            "corpus": round(pt["corpus"] + current_corpus * (1 + r) ** (yr * 12), 2),
            "gain": round(pt["gain"] + current_corpus * (1 + r) ** (yr * 12) - current_corpus, 2),
        })

    return {
        **sim,
        "current_corpus": current_corpus,
        "final_corpus_with_existing": round(total_final, 2),
        "yearly_summary": yearly,
    }


# ── Goal operations ───────────────────────────────────────────────────────────

async def record_sip_installment(
    goal_id: str,
    fund_id: Optional[str],
    scheme_code: str,
    scheme_name: str,
    amount: float,
    investment_date: date,
    session: AsyncSession,
) -> SIPInvestment:
    nav = await fetch_nav_on_date(scheme_code, investment_date) or 0.0
    units = amount / nav if nav > 0 else 0.0

    inv = SIPInvestment(
        goal_id=goal_id,
        fund_id=fund_id,
        scheme_code=scheme_code,
        scheme_name=scheme_name,
        investment_date=investment_date,
        amount=amount,
        nav_at_purchase=nav,
        units_purchased=units,
        current_nav=nav,
        current_value=amount,
    )
    session.add(inv)
    await session.flush()
    return inv


async def update_current_navs(goal_id: str, session: AsyncSession) -> None:
    """Refresh current_nav and current_value for all investments of a goal."""
    res = await session.execute(
        select(SIPInvestment).where(SIPInvestment.goal_id == goal_id)
    )
    investments = list(res.scalars().all())

    codes = list({inv.scheme_code for inv in investments})
    nav_map: dict[str, Optional[float]] = {}
    for code in codes:
        nav_map[code] = await fetch_current_nav(code)

    for inv in investments:
        nav = nav_map.get(inv.scheme_code)
        if nav:
            inv.current_nav = nav
            inv.current_value = round(inv.units_purchased * nav, 2)

    await session.flush()


async def calculate_goal_progress(goal_id: str, session: AsyncSession) -> dict:
    """Full progress snapshot for a goal."""
    goal = (await session.execute(
        select(SIPGoal).where(SIPGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        return {}

    res = await session.execute(
        select(SIPInvestment).where(SIPInvestment.goal_id == goal_id)
    )
    investments = list(res.scalars().all())

    total_invested = sum(inv.amount for inv in investments)
    current_value  = sum((inv.current_value or inv.amount) for inv in investments)
    total_gain     = current_value - total_invested
    gain_pct       = (total_gain / total_invested * 100) if total_invested else 0.0

    today = date.today()
    months_remaining = max(0, (goal.target_date.year - today.year) * 12 + (goal.target_date.month - today.month))
    months_elapsed   = max(0, (today.year - goal.created_at.year) * 12 + (today.month - goal.created_at.month))
    progress_pct     = min(100.0, (current_value / goal.target_amount * 100) if goal.target_amount else 0.0)

    # XIRR
    xirr = None
    if investments:
        cashflows = []
        for inv in sorted(investments, key=lambda x: x.investment_date):
            cashflows.append((inv.investment_date, -inv.amount))
        cashflows.append((today, current_value))
        xirr = calculate_xirr(cashflows)

    # Projection to reach target with current monthly SIP
    proj_months = calculate_months_to_target(
        goal.monthly_sip, goal.target_amount, goal.expected_return, current_value
    ) if goal.monthly_sip > 0 else None
    projected_end = None
    if proj_months is not None:
        projected_end = date(
            today.year + (today.month + proj_months - 1) // 12,
            (today.month + proj_months - 1) % 12 + 1,
            1,
        ).isoformat()

    on_track = proj_months is not None and proj_months <= months_remaining

    # 3-scenario projections
    scenarios = {}
    for label, ret in [("conservative", goal.expected_return * 0.7),
                       ("moderate",     goal.expected_return),
                       ("optimistic",   goal.expected_return * 1.3)]:
        sim = simulate_sip(goal.monthly_sip, ret, max(1, months_remaining))
        final = sim["projected_value"] + current_value * (1 + ret / 100 / 12) ** max(1, months_remaining)
        scenarios[label] = {
            "return_pct":    round(ret, 1),
            "projected":     round(final, 2),
            "hits_target":   final >= goal.target_amount,
        }

    return {
        "goal_id":          goal_id,
        "goal_name":        goal.name,
        "goal_type":        goal.goal_type,
        "target_amount":    goal.target_amount,
        "target_date":      goal.target_date.isoformat(),
        "monthly_sip":      goal.monthly_sip,
        "expected_return":  goal.expected_return,
        "total_invested":   round(total_invested, 2),
        "current_value":    round(current_value, 2),
        "total_gain":       round(total_gain, 2),
        "gain_pct":         round(gain_pct, 2),
        "xirr":             xirr,
        "progress_pct":     round(progress_pct, 2),
        "months_elapsed":   months_elapsed,
        "months_remaining": months_remaining,
        "on_track":         on_track,
        "projected_end":    projected_end,
        "scenarios":        scenarios,
        "installment_count": len(investments),
    }
