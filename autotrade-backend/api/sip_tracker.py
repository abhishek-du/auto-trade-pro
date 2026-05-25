"""SIP Tracker & Goal Planner API."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import SIPFund, SIPGoal, SIPInvestment
from engine.sip_engine import (
    calculate_months_to_target,
    calculate_required_sip,
    fetch_current_nav,
    record_sip_installment,
    run_sip_calculator,
    search_mutual_funds,
    simulate_sip,
    update_current_navs,
    calculate_goal_progress,
)

router = APIRouter(tags=["SIP Tracker"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    name: str
    goal_type: str = "wealth"
    target_amount: float
    target_date: date
    monthly_sip: float = 0.0
    expected_return: float = 12.0
    sip_date: int = 1
    notes: Optional[str] = None


class GoalUpdate(BaseModel):
    name: Optional[str] = None
    goal_type: Optional[str] = None
    target_amount: Optional[float] = None
    target_date: Optional[date] = None
    monthly_sip: Optional[float] = None
    expected_return: Optional[float] = None
    sip_date: Optional[int] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class FundAdd(BaseModel):
    scheme_code: str
    scheme_name: str
    fund_house: str = ""
    category: str = ""
    monthly_amount: float
    start_date: date


class InstallmentAdd(BaseModel):
    fund_id: Optional[str] = None
    scheme_code: str
    scheme_name: str = ""
    amount: float
    investment_date: date


class SIPCalculatorRequest(BaseModel):
    monthly_amount: float
    years: int = 10
    expected_return_pct: float = 12.0
    current_corpus: float = 0.0
    step_up_pct: float = 0.0


class RequiredSIPRequest(BaseModel):
    target_amount: float
    months: int
    expected_return_pct: float = 12.0


class TimeToTargetRequest(BaseModel):
    monthly_sip: float
    target_amount: float
    expected_return_pct: float = 12.0
    current_corpus: float = 0.0


# ── Goals ─────────────────────────────────────────────────────────────────────

@router.get("/goals")
async def list_goals(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SIPGoal).order_by(SIPGoal.created_at))
    goals = list(res.scalars().all())

    result = []
    for g in goals:
        # quick progress snapshot without nav refresh
        investments = list((await db.execute(
            select(SIPInvestment).where(SIPInvestment.goal_id == g.id)
        )).scalars().all())

        total_invested = sum(inv.amount for inv in investments)
        current_value  = sum((inv.current_value or inv.amount) for inv in investments)
        progress_pct   = min(100.0, current_value / g.target_amount * 100) if g.target_amount else 0.0
        today = date.today()
        months_remaining = max(0, (g.target_date.year - today.year) * 12 + (g.target_date.month - today.month))

        result.append({
            "id":               g.id,
            "name":             g.name,
            "goal_type":        g.goal_type,
            "target_amount":    g.target_amount,
            "target_date":      g.target_date.isoformat(),
            "monthly_sip":      g.monthly_sip,
            "expected_return":  g.expected_return,
            "sip_date":         g.sip_date,
            "notes":            g.notes,
            "is_active":        g.is_active,
            "created_at":       g.created_at.isoformat(),
            "total_invested":   round(total_invested, 2),
            "current_value":    round(current_value, 2),
            "progress_pct":     round(progress_pct, 2),
            "months_remaining": months_remaining,
            "installment_count": len(investments),
        })
    return result


@router.post("/goals", status_code=201)
async def create_goal(body: GoalCreate, db: AsyncSession = Depends(get_db)):
    if body.target_amount <= 0:
        raise HTTPException(400, "Target amount must be positive")
    if body.target_date <= date.today():
        raise HTTPException(400, "Target date must be in the future")

    goal = SIPGoal(
        name=body.name,
        goal_type=body.goal_type,
        target_amount=body.target_amount,
        target_date=body.target_date,
        monthly_sip=body.monthly_sip,
        expected_return=body.expected_return,
        sip_date=body.sip_date,
        notes=body.notes or "",
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return {"id": goal.id, "name": goal.name, "goal_type": goal.goal_type}


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: str, db: AsyncSession = Depends(get_db)):
    goal = (await db.execute(
        select(SIPGoal).where(SIPGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Goal not found")
    return await calculate_goal_progress(goal_id, db)


@router.put("/goals/{goal_id}")
async def update_goal(goal_id: str, body: GoalUpdate, db: AsyncSession = Depends(get_db)):
    goal = (await db.execute(
        select(SIPGoal).where(SIPGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Goal not found")

    if body.name is not None:            goal.name            = body.name
    if body.goal_type is not None:       goal.goal_type       = body.goal_type
    if body.target_amount is not None:   goal.target_amount   = body.target_amount
    if body.target_date is not None:     goal.target_date     = body.target_date
    if body.monthly_sip is not None:     goal.monthly_sip     = body.monthly_sip
    if body.expected_return is not None: goal.expected_return  = body.expected_return
    if body.sip_date is not None:        goal.sip_date        = body.sip_date
    if body.notes is not None:           goal.notes           = body.notes
    if body.is_active is not None:       goal.is_active       = body.is_active

    await db.commit()
    return {"id": goal.id, "name": goal.name}


@router.delete("/goals/{goal_id}", status_code=204)
async def delete_goal(goal_id: str, db: AsyncSession = Depends(get_db)):
    goal = (await db.execute(
        select(SIPGoal).where(SIPGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Goal not found")
    await db.delete(goal)
    await db.commit()


# ── Funds within a goal ───────────────────────────────────────────────────────

@router.get("/goals/{goal_id}/funds-list")
async def list_goal_funds(goal_id: str, db: AsyncSession = Depends(get_db)):
    """List all funds linked to a goal."""
    res = await db.execute(
        select(SIPFund).where(SIPFund.goal_id == goal_id).order_by(SIPFund.created_at)
    )
    funds = list(res.scalars().all())
    return [
        {
            "id":             f.id,
            "goal_id":        f.goal_id,
            "scheme_code":    f.scheme_code,
            "scheme_name":    f.scheme_name,
            "fund_house":     f.fund_house,
            "category":       f.category,
            "monthly_amount": f.monthly_amount,
            "start_date":     f.start_date.isoformat(),
            "is_active":      f.is_active,
        }
        for f in funds
    ]


@router.post("/goals/{goal_id}/funds", status_code=201)
async def add_fund_to_goal(goal_id: str, body: FundAdd, db: AsyncSession = Depends(get_db)):
    goal = (await db.execute(
        select(SIPGoal).where(SIPGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Goal not found")

    fund = SIPFund(
        goal_id=goal_id,
        scheme_code=body.scheme_code,
        scheme_name=body.scheme_name,
        fund_house=body.fund_house,
        category=body.category,
        monthly_amount=body.monthly_amount,
        start_date=body.start_date,
    )
    db.add(fund)
    await db.commit()
    await db.refresh(fund)

    # update goal monthly_sip total
    all_funds = list((await db.execute(
        select(SIPFund).where(SIPFund.goal_id == goal_id, SIPFund.is_active == True)
    )).scalars().all())
    goal.monthly_sip = sum(f.monthly_amount for f in all_funds)
    await db.commit()

    return {"id": fund.id, "scheme_code": fund.scheme_code, "monthly_amount": fund.monthly_amount}


@router.delete("/goals/{goal_id}/funds/{fund_id}", status_code=204)
async def remove_fund_from_goal(goal_id: str, fund_id: str, db: AsyncSession = Depends(get_db)):
    fund = (await db.execute(
        select(SIPFund).where(SIPFund.id == fund_id, SIPFund.goal_id == goal_id)
    )).scalar_one_or_none()
    if not fund:
        raise HTTPException(404, "Fund not found in this goal")
    await db.delete(fund)

    # update goal monthly_sip
    goal = (await db.execute(select(SIPGoal).where(SIPGoal.id == goal_id))).scalar_one_or_none()
    if goal:
        remaining = list((await db.execute(
            select(SIPFund).where(SIPFund.goal_id == goal_id, SIPFund.is_active == True)
        )).scalars().all())
        goal.monthly_sip = sum(f.monthly_amount for f in remaining if f.id != fund_id)

    await db.commit()


# ── Installments ──────────────────────────────────────────────────────────────

@router.post("/goals/{goal_id}/installments", status_code=201)
async def add_installment(goal_id: str, body: InstallmentAdd, db: AsyncSession = Depends(get_db)):
    goal = (await db.execute(
        select(SIPGoal).where(SIPGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Goal not found")
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    try:
        inv = await record_sip_installment(
            goal_id=goal_id,
            fund_id=body.fund_id,
            scheme_code=body.scheme_code,
            scheme_name=body.scheme_name or body.scheme_code,
            amount=body.amount,
            investment_date=body.investment_date,
            session=db,
        )
        await db.commit()
        return {
            "id":              inv.id,
            "scheme_code":     inv.scheme_code,
            "amount":          inv.amount,
            "nav_at_purchase": inv.nav_at_purchase,
            "units_purchased": round(inv.units_purchased, 4),
        }
    except Exception as exc:
        raise HTTPException(500, f"Failed to record installment: {exc}")


@router.get("/goals/{goal_id}/installments")
async def list_installments(goal_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(SIPInvestment)
        .where(SIPInvestment.goal_id == goal_id)
        .order_by(SIPInvestment.investment_date.desc())
    )
    investments = list(res.scalars().all())
    return [
        {
            "id":               inv.id,
            "fund_id":          inv.fund_id,
            "scheme_code":      inv.scheme_code,
            "scheme_name":      inv.scheme_name,
            "investment_date":  inv.investment_date.isoformat(),
            "amount":           inv.amount,
            "nav_at_purchase":  inv.nav_at_purchase,
            "units_purchased":  round(inv.units_purchased, 4),
            "current_nav":      inv.current_nav,
            "current_value":    inv.current_value,
            "gain":             round((inv.current_value or inv.amount) - inv.amount, 2),
            "gain_pct":         round(((inv.current_value or inv.amount) / inv.amount - 1) * 100, 2) if inv.amount else 0,
        }
        for inv in investments
    ]


# ── Projection ────────────────────────────────────────────────────────────────

@router.get("/goals/{goal_id}/projection")
async def get_projection(goal_id: str, db: AsyncSession = Depends(get_db)):
    goal = (await db.execute(
        select(SIPGoal).where(SIPGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Goal not found")

    investments = list((await db.execute(
        select(SIPInvestment).where(SIPInvestment.goal_id == goal_id)
    )).scalars().all())
    current_value = sum((inv.current_value or inv.amount) for inv in investments)

    today = date.today()
    months_remaining = max(1, (goal.target_date.year - today.year) * 12 + (goal.target_date.month - today.month))

    scenarios = {}
    for label, pct in [("conservative", goal.expected_return * 0.7),
                       ("moderate",     goal.expected_return),
                       ("optimistic",   goal.expected_return * 1.3)]:
        sim = simulate_sip(goal.monthly_sip, pct, months_remaining)
        r = pct / 100 / 12
        corpus_growth = current_value * (1 + r) ** months_remaining
        final = sim["projected_value"] + corpus_growth
        scenarios[label] = {
            "return_pct":  round(pct, 1),
            "projected":   round(final, 2),
            "hits_target": final >= goal.target_amount,
            "data_points": sim["data_points"][::3],  # every 3rd point for chart
        }

    return {
        "goal_id":          goal_id,
        "target_amount":    goal.target_amount,
        "months_remaining": months_remaining,
        "current_corpus":   round(current_value, 2),
        "monthly_sip":      goal.monthly_sip,
        "scenarios":        scenarios,
    }


# ── Calculators ───────────────────────────────────────────────────────────────

@router.post("/calculator")
async def sip_calculator(body: SIPCalculatorRequest):
    return run_sip_calculator(
        monthly_amount=body.monthly_amount,
        years=body.years,
        expected_return_pct=body.expected_return_pct,
        current_corpus=body.current_corpus,
        step_up_pct=body.step_up_pct,
    )


@router.post("/calculator/required-sip")
async def required_sip(body: RequiredSIPRequest):
    if body.target_amount <= 0 or body.months <= 0:
        raise HTTPException(400, "target_amount and months must be positive")
    monthly = calculate_required_sip(body.target_amount, body.months, body.expected_return_pct)
    return {
        "required_monthly_sip": monthly,
        "target_amount":        body.target_amount,
        "months":               body.months,
        "expected_return_pct":  body.expected_return_pct,
    }


@router.post("/calculator/time-to-target")
async def time_to_target(body: TimeToTargetRequest):
    months = calculate_months_to_target(
        body.monthly_sip, body.target_amount, body.expected_return_pct, body.current_corpus
    )
    years, rem = divmod(months, 12)
    return {
        "months":               months,
        "years":                years,
        "remaining_months":     rem,
        "monthly_sip":          body.monthly_sip,
        "target_amount":        body.target_amount,
        "expected_return_pct":  body.expected_return_pct,
        "current_corpus":       body.current_corpus,
    }


# ── Fund search & NAV ─────────────────────────────────────────────────────────

@router.get("/funds/search")
async def search_funds(q: str = Query(..., min_length=2)):
    return await search_mutual_funds(q)


@router.get("/funds/nav/{scheme_code}")
async def get_nav(scheme_code: str):
    nav = await fetch_current_nav(scheme_code)
    if nav is None:
        raise HTTPException(404, "NAV not available for this scheme")
    return {"scheme_code": scheme_code, "nav": nav}


# ── Refresh NAVs for a goal ───────────────────────────────────────────────────

@router.post("/goals/{goal_id}/refresh")
async def refresh_goal_navs(goal_id: str, db: AsyncSession = Depends(get_db)):
    goal = (await db.execute(
        select(SIPGoal).where(SIPGoal.id == goal_id)
    )).scalar_one_or_none()
    if not goal:
        raise HTTPException(404, "Goal not found")
    await update_current_navs(goal_id, db)
    await db.commit()
    return await calculate_goal_progress(goal_id, db)
