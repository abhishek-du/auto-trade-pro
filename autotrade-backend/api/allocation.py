"""Asset Allocation API — /api/v1/allocation endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from engine.allocation_engine import (
    _BASE_ALLOCATIONS,
    _CLASS_NAMES,
    _PROFILE_META,
    build_allocation_map,
    calculate_portfolio_risk_score,
    calculate_rebalancing,
    get_all_profiles,
    get_recommended_allocation,
    run_risk_questionnaire,
)

router = APIRouter(tags=["Asset Allocation"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RiskQuestionnaire(BaseModel):
    q1_horizon:    int
    q2_reaction:   int
    q3_goal:       int
    q4_income:     int
    q5_experience: int


class CustomAllocationTarget(BaseModel):
    large_cap:     float
    mid_cap:       float
    small_cap:     float
    debt:          float
    gold:          float
    international: float = 0.0
    cash:          float = 0.0


class RebalancingRequest(BaseModel):
    current_allocation: dict
    target_allocation:  dict
    total_value:        float
    new_investment:     float = 0.0
    threshold:          float = 5.0


# ── Helper: build and return full analysis ────────────────────────────────────

async def _full_analysis(
    portfolio_id:          Optional[str],
    sip_goal_ids:          list[str],
    risk_profile:          str,
    age:                   Optional[int],
    rebalancing_threshold: float,
    new_investment:        float,
    session:               AsyncSession,
) -> dict:
    allocation, total = await build_allocation_map(
        portfolio_id, sip_goal_ids, session
    )

    target_alloc = get_recommended_allocation(
        age or 35,
        risk_profile,
        investment_horizon_years=10,
    )

    rebalancing = calculate_rebalancing(
        allocation,
        target_alloc,
        total,
        rebalancing_threshold,
        new_investment,
    )

    risk_score = calculate_portfolio_risk_score(allocation)

    return {
        "current_allocation":  allocation,
        "target_allocation":   target_alloc,
        "rebalancing":         [
            {
                "asset_class":   a.asset_class,
                "class_name":    _CLASS_NAMES.get(a.asset_class, a.asset_class),
                "current_value": a.current_value,
                "current_pct":   a.current_pct,
                "target_pct":    a.target_pct,
                "deviation_pct": a.deviation_pct,
                "action":        a.action,
                "amount_inr":    a.amount_inr,
                "priority":      a.priority,
                "suggestion":    a.suggestion,
            }
            for a in rebalancing
        ],
        "risk_score":           risk_score,
        "recommended_profile":  risk_profile,
        "portfolio_total":      total,
        "last_updated":         datetime.now().isoformat(),
    }


# ── GET /analysis ─────────────────────────────────────────────────────────────

@router.get("/analysis")
async def get_analysis(
    portfolio_id:          Optional[str]  = Query(None),
    sip_goal_ids:          list[str]      = Query(default=[]),
    risk_profile:          str            = Query("moderate"),
    age:                   Optional[int]  = Query(None),
    rebalancing_threshold: float          = Query(5.0),
    new_investment:        float          = Query(0.0),
    db: AsyncSession = Depends(get_db),
):
    """Full allocation analysis for portfolio + SIP goals combined."""
    return await _full_analysis(
        portfolio_id, sip_goal_ids, risk_profile, age,
        rebalancing_threshold, new_investment, db,
    )


# ── POST /analysis ────────────────────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    portfolio_id:          Optional[str]  = None
    sip_goal_ids:          list[str]      = []
    risk_profile:          str            = "moderate"
    age:                   Optional[int]  = None
    rebalancing_threshold: float          = 5.0
    new_investment:        float          = 0.0


@router.post("/analysis")
async def post_analysis(
    body: AnalysisRequest,
    db: AsyncSession = Depends(get_db),
):
    return await _full_analysis(
        body.portfolio_id, body.sip_goal_ids, body.risk_profile, body.age,
        body.rebalancing_threshold, body.new_investment, db,
    )


# ── POST /risk-profile ────────────────────────────────────────────────────────

@router.post("/risk-profile")
async def assess_risk_profile(body: RiskQuestionnaire):
    """Score the questionnaire and return recommended profile + allocation."""
    profile = run_risk_questionnaire(body.model_dump())
    alloc   = get_recommended_allocation(35, profile, 10)
    meta    = _PROFILE_META.get(profile, {})
    return {
        "profile":                  profile,
        "recommended_allocation":   alloc,
        "description":              meta.get("description", ""),
        "suitable_for":             meta.get("suitable_for", ""),
        "color":                    meta.get("color", "#3B82F6"),
        "cagr_range":               meta.get("cagr_range", ""),
        "horizon":                  meta.get("horizon", ""),
    }


# ── GET /profiles ─────────────────────────────────────────────────────────────

@router.get("/profiles")
async def list_profiles():
    """Return all 6 risk profiles with target allocations and metadata."""
    return get_all_profiles()


# ── POST /rebalancing ─────────────────────────────────────────────────────────

@router.post("/rebalancing")
async def calculate_rebalancing_actions(body: RebalancingRequest):
    """What-if rebalancing: given current and target, return actions."""
    actions = calculate_rebalancing(
        body.current_allocation,
        body.target_allocation,
        body.total_value,
        body.threshold,
        body.new_investment,
    )
    return [
        {
            "asset_class":   a.asset_class,
            "class_name":    _CLASS_NAMES.get(a.asset_class, a.asset_class),
            "current_value": a.current_value,
            "current_pct":   a.current_pct,
            "target_pct":    a.target_pct,
            "deviation_pct": a.deviation_pct,
            "action":        a.action,
            "amount_inr":    a.amount_inr,
            "priority":      a.priority,
            "suggestion":    a.suggestion,
        }
        for a in actions
    ]


# ── GET /benchmark ────────────────────────────────────────────────────────────

@router.get("/benchmark")
async def get_benchmarks():
    """Reference benchmark portfolio allocations."""
    return {
        "warren_buffett": {
            "large_cap": 90, "cash": 10,
            "description": "90/10 — low-cost index + cash",
        },
        "nifty_50_etf": {
            "large_cap": 100,
            "description": "100% Nifty 50 index fund",
        },
        "balanced_india": {
            "large_cap": 40, "mid_cap": 15, "small_cap": 10,
            "debt": 30, "gold": 5,
            "description": "Classic 65/35 balanced Indian portfolio",
        },
        "sebi_balanced": {
            "large_cap": 55, "mid_cap": 10,
            "debt": 35,
            "description": "SEBI-inspired balanced allocation",
        },
        "golden_butterfly": {
            "large_cap": 20, "small_cap": 20, "debt": 20, "gold": 40,
            "description": "Golden Butterfly — all-weather Indian variant",
        },
    }
