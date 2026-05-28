"""Portfolio Doctor API — /api/v1/doctor endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import PortfolioDiagnosis, TrackerPortfolio
from engine.portfolio_doctor import run_full_diagnosis, run_quick_diagnosis

router = APIRouter(tags=["Portfolio Doctor"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class DiagnosisRequest(BaseModel):
    portfolio_id:  str
    sip_goal_ids:  list[str] = []
    risk_profile:  str = "moderate"
    annual_income: float = 1_000_000


# ── POST /diagnose ────────────────────────────────────────────────────────────

@router.post("/diagnose")
async def create_diagnosis(
    req: DiagnosisRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run full AI diagnosis and save to DB. Takes 15–30 seconds."""
    # Verify portfolio exists
    port = (await db.execute(
        select(TrackerPortfolio).where(TrackerPortfolio.id == req.portfolio_id)
    )).scalar_one_or_none()
    if not port:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    report = await run_full_diagnosis(
        portfolio_id=req.portfolio_id,
        sip_goal_ids=req.sip_goal_ids,
        risk_profile=req.risk_profile,
        annual_income=req.annual_income,
        session=db,
    )

    # Persist
    row = PortfolioDiagnosis(
        portfolio_id=req.portfolio_id,
        overall_score=report.overall_score,
        overall_grade=report.overall_grade,
        summary=report.summary,
        findings=[f.to_dict() if hasattr(f, "to_dict") else f for f in report.findings],
        ai_narrative=report.ai_narrative,
        quick_wins=report.quick_wins,
        data_snapshot=report.data_snapshot,
        is_ai=report.is_ai_generated,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {**report.to_dict(), "id": row.id}


# ── GET /diagnose/{portfolio_id} ──────────────────────────────────────────────

@router.get("/diagnose/{portfolio_id}")
async def get_latest_diagnosis(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent saved diagnosis for a portfolio."""
    row = (await db.execute(
        select(PortfolioDiagnosis)
        .where(PortfolioDiagnosis.portfolio_id == portfolio_id)
        .order_by(PortfolioDiagnosis.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="No diagnosis found")

    return {
        "id":             row.id,
        "portfolio_id":   row.portfolio_id,
        "overall_score":  row.overall_score,
        "overall_grade":  row.overall_grade,
        "summary":        row.summary,
        "findings":       row.findings,
        "ai_narrative":   row.ai_narrative,
        "quick_wins":     row.quick_wins,
        "data_snapshot":  row.data_snapshot,
        "is_ai_generated": row.is_ai,
        "generated_at":   row.created_at.isoformat(),
    }


# ── GET /history/{portfolio_id} ───────────────────────────────────────────────

@router.get("/history/{portfolio_id}")
async def get_diagnosis_history(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return last 5 diagnoses with scores and dates for trend chart."""
    rows = (await db.execute(
        select(PortfolioDiagnosis)
        .where(PortfolioDiagnosis.portfolio_id == portfolio_id)
        .order_by(PortfolioDiagnosis.created_at.desc())
        .limit(5)
    )).scalars().all()

    return [
        {
            "id":         r.id,
            "score":      r.overall_score,
            "grade":      r.overall_grade,
            "summary":    r.summary,
            "created_at": r.created_at.isoformat(),
        }
        for r in reversed(rows)
    ]


# ── GET /quick-check/{portfolio_id} ──────────────────────────────────────────

@router.get("/quick-check/{portfolio_id}")
async def quick_check(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Fast check — no AI, no fundamentals. Returns in < 3 seconds."""
    report = await run_quick_diagnosis(portfolio_id, db)
    return report.to_dict()


# ── DELETE /diagnose/{diagnosis_id} ──────────────────────────────────────────

@router.delete("/diagnose/{diagnosis_id}")
async def delete_diagnosis(
    diagnosis_id: str,
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(PortfolioDiagnosis).where(PortfolioDiagnosis.id == diagnosis_id)
    )
    await db.commit()
    return {"deleted": True}
