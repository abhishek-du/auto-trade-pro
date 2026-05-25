"""My Mutual Funds Tracker — personal fund + SIP tracking with AI analysis."""
from __future__ import annotations

import asyncio
import threading
from datetime import date
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import MutualFundNAV, UserMutualFund, UserSIP
from engine.mutual_fund_analyzer import fetch_and_save_nav, project_sip
from utils.llm import quick_analysis
from utils.logger import logger

router = APIRouter(tags=["MF Tracker"])

# ── In-memory AMFI scheme cache (loaded once) ─────────────────────────────────

_scheme_cache: dict[str, str] = {}   # {code: name}
_cache_lock = threading.Lock()
_cache_loaded = False


def _load_scheme_cache() -> None:
    global _scheme_cache, _cache_loaded
    try:
        from mftool import Mftool
        mf = Mftool()
        codes = mf.get_scheme_codes()
        if isinstance(codes, dict):
            with _cache_lock:
                _scheme_cache = {str(k): str(v) for k, v in codes.items()}
                _cache_loaded = True
            logger.info(f"MF scheme cache loaded: {len(_scheme_cache)} schemes")
    except Exception as exc:
        logger.warning(f"Could not load AMFI scheme list: {exc}")


def _ensure_cache_loaded() -> None:
    global _cache_loaded
    if not _cache_loaded:
        _load_scheme_cache()


# ── Schemas ───────────────────────────────────────────────────────────────────

class AddFundRequest(BaseModel):
    scheme_code: str
    scheme_name: str
    category: str = ""


class AddSIPRequest(BaseModel):
    fund_id: str
    monthly_amount: float
    start_date: date
    notes: Optional[str] = ""


class UpdateSIPRequest(BaseModel):
    monthly_amount: Optional[float] = None
    status: Optional[str] = None   # active | paused
    notes: Optional[str] = None


# ── Fund search ───────────────────────────────────────────────────────────────

@router.get("/funds/search")
async def search_funds(q: str = Query(..., min_length=2)):
    """Search AMFI fund list by name. Returns up to 15 matches."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _ensure_cache_loaded)

    with _cache_lock:
        cache = dict(_scheme_cache)

    if not cache:
        return []

    q_lower = q.strip().lower()
    matches = [
        {"scheme_code": code, "scheme_name": name, "category": _infer_category(name)}
        for code, name in cache.items()
        if q_lower in name.lower()
    ]
    matches.sort(key=lambda x: (
        0 if x["scheme_name"].lower().startswith(q_lower) else 1,
        len(x["scheme_name"]),
    ))
    return matches[:15]


def _infer_category(name: str) -> str:
    n = name.lower()
    if "elss" in n or "tax saver" in n:
        return "ELSS"
    if "index" in n or "nifty" in n or "sensex" in n:
        return "Index"
    if "mid cap" in n or "midcap" in n:
        return "Mid Cap"
    if "large cap" in n or "largecap" in n:
        return "Large Cap"
    if "small cap" in n or "smallcap" in n:
        return "Small Cap"
    if "hybrid" in n or "equity & debt" in n or "balanced" in n:
        return "Hybrid"
    if "liquid" in n or "overnight" in n or "money market" in n:
        return "Liquid"
    if "debt" in n or "bond" in n or "gilt" in n or "income" in n:
        return "Debt"
    return "Equity"


# ── Funds CRUD ────────────────────────────────────────────────────────────────

@router.get("/funds")
async def list_user_funds(db: AsyncSession = Depends(get_db)):
    """List user-tracked funds with latest NAV data."""
    res = await db.execute(select(UserMutualFund).order_by(UserMutualFund.added_at))
    funds = list(res.scalars().all())

    result = []
    for f in funds:
        nav_row = (await db.execute(
            select(MutualFundNAV)
            .where(MutualFundNAV.scheme_code == f.scheme_code)
            .order_by(desc(MutualFundNAV.recorded_at))
            .limit(1)
        )).scalar_one_or_none()

        sips_res = await db.execute(
            select(UserSIP).where(UserSIP.fund_id == f.id, UserSIP.status == "active")
        )
        active_sips = list(sips_res.scalars().all())
        total_monthly = sum(s.monthly_amount for s in active_sips)

        result.append({
            "id":                f.id,
            "scheme_code":       f.scheme_code,
            "scheme_name":       f.scheme_name,
            "category":          f.category,
            "added_at":          f.added_at.isoformat(),
            "nav":               nav_row.nav               if nav_row else None,
            "one_month_return":  nav_row.one_month_return  if nav_row else None,
            "one_year_return":   nav_row.one_year_return   if nav_row else None,
            "three_year_return": nav_row.three_year_return if nav_row else None,
            "change_pct":        nav_row.change_pct        if nav_row else None,
            "total_monthly_sip": total_monthly,
            "sip_count":         len(active_sips),
        })
    return result


@router.post("/funds", status_code=201)
async def add_user_fund(body: AddFundRequest, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(
        select(UserMutualFund).where(UserMutualFund.scheme_code == body.scheme_code)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Fund already in your tracker")

    fund = UserMutualFund(
        scheme_code=body.scheme_code,
        scheme_name=body.scheme_name,
        category=body.category or _infer_category(body.scheme_name),
    )
    db.add(fund)
    await db.commit()
    await db.refresh(fund)

    # Kick off NAV fetch in background — don't block the response
    async def _fetch():
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as bg_db:
            try:
                await fetch_and_save_nav(body.scheme_code, bg_db)
                await bg_db.commit()
            except Exception as exc:
                logger.warning(f"Background NAV fetch {body.scheme_code}: {exc}")

    asyncio.create_task(_fetch())

    return {"id": fund.id, "scheme_code": fund.scheme_code, "scheme_name": fund.scheme_name, "category": fund.category}


@router.delete("/funds/{fund_id}", status_code=204)
async def remove_user_fund(fund_id: str, db: AsyncSession = Depends(get_db)):
    fund = (await db.execute(
        select(UserMutualFund).where(UserMutualFund.id == fund_id)
    )).scalar_one_or_none()
    if not fund:
        raise HTTPException(404, "Fund not found")
    await db.delete(fund)
    await db.commit()


@router.post("/funds/{fund_id}/refresh")
async def refresh_fund_nav(fund_id: str, db: AsyncSession = Depends(get_db)):
    """Manually refresh NAV for a tracked fund."""
    fund = (await db.execute(
        select(UserMutualFund).where(UserMutualFund.id == fund_id)
    )).scalar_one_or_none()
    if not fund:
        raise HTTPException(404, "Fund not found")
    try:
        result = await fetch_and_save_nav(fund.scheme_code, db)
        await db.commit()
        return result or {"message": "NAV refresh attempted, no new data"}
    except Exception as exc:
        raise HTTPException(502, f"NAV fetch failed: {exc}")


# ── SIPs CRUD ─────────────────────────────────────────────────────────────────

@router.get("/sips")
async def list_sips(db: AsyncSession = Depends(get_db)):
    """List all SIPs with corpus projections (12% assumed return)."""
    rows = (await db.execute(
        select(UserSIP, UserMutualFund)
        .join(UserMutualFund, UserSIP.fund_id == UserMutualFund.id)
        .order_by(UserSIP.created_at)
    )).all()

    result = []
    for sip, fund in rows:
        months_invested = max(1, (date.today() - sip.start_date).days // 30)
        proj = project_sip(sip.monthly_amount, 12.0, months_invested)
        result.append({
            "id":               sip.id,
            "fund_id":          sip.fund_id,
            "scheme_code":      sip.scheme_code,
            "scheme_name":      fund.scheme_name,
            "category":         fund.category,
            "monthly_amount":   sip.monthly_amount,
            "start_date":       sip.start_date.isoformat(),
            "status":           sip.status,
            "notes":            sip.notes,
            "months_invested":  months_invested,
            "total_invested":   round(sip.monthly_amount * months_invested, 2),
            "projected_value":  proj["projected_value"],
            "estimated_gain":   proj["absolute_return"],
            "created_at":       sip.created_at.isoformat(),
        })
    return result


@router.post("/sips", status_code=201)
async def add_sip(body: AddSIPRequest, db: AsyncSession = Depends(get_db)):
    fund = (await db.execute(
        select(UserMutualFund).where(UserMutualFund.id == body.fund_id)
    )).scalar_one_or_none()
    if not fund:
        raise HTTPException(404, "Fund not found — add the fund first")
    if body.monthly_amount <= 0:
        raise HTTPException(400, "Monthly amount must be positive")

    sip = UserSIP(
        fund_id=body.fund_id,
        scheme_code=fund.scheme_code,
        monthly_amount=body.monthly_amount,
        start_date=body.start_date,
        notes=body.notes or "",
    )
    db.add(sip)
    await db.commit()
    await db.refresh(sip)
    return {"id": sip.id, "scheme_code": sip.scheme_code, "monthly_amount": sip.monthly_amount, "status": sip.status}


@router.patch("/sips/{sip_id}")
async def update_sip(sip_id: str, body: UpdateSIPRequest, db: AsyncSession = Depends(get_db)):
    sip = (await db.execute(
        select(UserSIP).where(UserSIP.id == sip_id)
    )).scalar_one_or_none()
    if not sip:
        raise HTTPException(404, "SIP not found")
    if body.monthly_amount is not None:
        if body.monthly_amount <= 0:
            raise HTTPException(400, "Monthly amount must be positive")
        sip.monthly_amount = body.monthly_amount
    if body.status is not None:
        if body.status not in ("active", "paused"):
            raise HTTPException(400, "Status must be active or paused")
        sip.status = body.status
    if body.notes is not None:
        sip.notes = body.notes
    await db.commit()
    return {"id": sip.id, "status": sip.status, "monthly_amount": sip.monthly_amount}


@router.delete("/sips/{sip_id}", status_code=204)
async def delete_sip(sip_id: str, db: AsyncSession = Depends(get_db)):
    sip = (await db.execute(
        select(UserSIP).where(UserSIP.id == sip_id)
    )).scalar_one_or_none()
    if not sip:
        raise HTTPException(404, "SIP not found")
    await db.delete(sip)
    await db.commit()


# ── AI Analysis ───────────────────────────────────────────────────────────────

@router.get("/analysis")
async def ai_portfolio_analysis(db: AsyncSession = Depends(get_db)):
    """Generate AI analysis of the user's mutual fund portfolio and SIPs via Groq."""
    funds = list((await db.execute(
        select(UserMutualFund).order_by(UserMutualFund.added_at)
    )).scalars().all())

    sips = list((await db.execute(
        select(UserSIP).order_by(UserSIP.created_at)
    )).scalars().all())

    if not funds and not sips:
        return {
            "analysis": "Add some mutual funds and SIPs first to get AI-powered portfolio analysis.",
            "total_monthly_sip": 0, "fund_count": 0, "sip_count": 0,
        }

    fund_lines = []
    for f in funds:
        nav_row = (await db.execute(
            select(MutualFundNAV)
            .where(MutualFundNAV.scheme_code == f.scheme_code)
            .order_by(desc(MutualFundNAV.recorded_at))
            .limit(1)
        )).scalar_one_or_none()

        nav_info = (
            f"NAV=₹{nav_row.nav:.2f}, 1M={nav_row.one_month_return}%, "
            f"1Y={nav_row.one_year_return}%, 3Y={nav_row.three_year_return}%"
            if nav_row else "NAV data pending"
        )
        fund_lines.append(f"  • {f.scheme_name} [{f.category}]: {nav_info}")

    total_monthly = 0
    sip_lines = []
    for sip in sips:
        fund = next((f for f in funds if f.id == sip.fund_id), None)
        fname = fund.scheme_name[:60] if fund else sip.scheme_code
        months = max(1, (date.today() - sip.start_date).days // 30)
        invested = sip.monthly_amount * months
        total_monthly += sip.monthly_amount if sip.status == "active" else 0
        sip_lines.append(
            f"  • {fname}: ₹{sip.monthly_amount:,.0f}/month × {months} months "
            f"= ₹{invested:,.0f} invested [{sip.status}]"
        )

    prompt = f"""Analyze this Indian retail investor's mutual fund portfolio:

TRACKED FUNDS:
{chr(10).join(fund_lines) if fund_lines else '  (none)'}

SIP HISTORY:
{chr(10).join(sip_lines) if sip_lines else '  (none)'}

Monthly SIP commitment: ₹{total_monthly:,.0f}

Provide a concise analysis (under 300 words) covering:
1. Diversification quality — equity/debt/hybrid mix, market-cap spread
2. SIP strategy — are amounts and tenures appropriate for wealth creation?
3. Top 2 specific, actionable recommendations
4. Key risk or gap to address

Be direct, India-specific, use ₹ for amounts."""

    loop = asyncio.get_event_loop()
    analysis = await loop.run_in_executor(
        None, lambda: quick_analysis(
            prompt,
            system="You are a seasoned Indian mutual fund advisor. Be concise and actionable."
        )
    )

    return {
        "analysis": analysis or "AI analysis unavailable. Check GROQ_API_KEY configuration.",
        "total_monthly_sip": total_monthly,
        "fund_count": len(funds),
        "sip_count": len(sips),
    }
