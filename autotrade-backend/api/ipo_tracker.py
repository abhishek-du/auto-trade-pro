# IPO Tracker API — live data from ipoalerts.in + Groq AI analysis.
# PAPER TRADING ONLY — no real order execution.

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import IPOAnalysisCache
from crawler.ipo_crawler import (
    IPO_CACHE,
    enrich_ipo_data,
    fetch_single_ipo,
    fetch_subscription_status,
    get_ipo_cache,
    refresh_ipo_cache,
)
from engine.ipo_analyzer import analyze_ipo, generate_ipo_analysis
from utils.config import settings
from utils.logger import logger

router = APIRouter(tags=["ipo"])


# ── GET /  — list all IPOs with optional filters ──────────────────────────────

@router.get("/")
async def list_ipos(
    status: Optional[str] = Query(None, description="open|upcoming|listed|announced"),
    ipo_type: Optional[str] = Query(None, alias="type", description="EQ|SME|DEBT"),
    limit: int = Query(50, ge=1, le=200),
):
    data = await get_ipo_cache()
    if status:
        data = [i for i in data if i.get("status", "").lower() == status.lower()]
    if ipo_type:
        data = [i for i in data if i.get("ipo_type", "").upper() == ipo_type.upper()]
    return {
        "count":    len(data[:limit]),
        "total":    len(data),
        "ipos":     data[:limit],
        "cached_at": datetime.utcfromtimestamp(IPO_CACHE["last_refresh"]).isoformat() if IPO_CACHE["last_refresh"] else None,
    }


# ── GET /{slug}  — single IPO detail ──────────────────────────────────────────

@router.get("/{slug}")
async def get_ipo(slug: str):
    await get_ipo_cache()  # ensure cache is warm
    ipo = IPO_CACHE["by_slug"].get(slug) or IPO_CACHE["by_id"].get(slug)
    if ipo is None:
        # Try live API as fallback
        raw = await fetch_single_ipo(slug)
        if raw is None:
            raise HTTPException(status_code=404, detail=f"IPO '{slug}' not found")
        ipo = enrich_ipo_data(raw)

    # Attach subscription if NSE URL is present and not cached
    nse_url = ipo.get("nse_url") or ipo.get("nse_info_url") or ""
    if nse_url and not ipo.get("subscription"):
        sub = await fetch_subscription_status(nse_url)
        ipo = dict(ipo)
        ipo["subscription"] = sub

    return ipo


# ── GET /{slug}/analysis  — AI / rule-based analysis ─────────────────────────

@router.get("/{slug}/analysis")
async def get_ipo_analysis(
    slug: str,
    refresh: bool = Query(False),
    session: AsyncSession = Depends(get_db),
):
    # Return persisted analysis if fresh (< 6 hours) and not forcing refresh
    if not refresh:
        cached = await session.scalar(
            select(IPOAnalysisCache).where(IPOAnalysisCache.ipo_slug == slug)
        )
        if cached:
            age_hours = (datetime.utcnow() - cached.updated_at).total_seconds() / 3600
            if age_hours < 6:
                return {
                    "ipo":      cached.ipo_data_json,
                    "analysis": cached.analysis_json,
                    "cached":   True,
                    "age_hours": round(age_hours, 1),
                }

    result = await analyze_ipo(slug)
    if result is None:
        raise HTTPException(status_code=404, detail=f"IPO '{slug}' not found")

    ipo      = result["ipo"]
    analysis = result["analysis"]
    ipo_id   = ipo.get("id") or ipo.get("_id") or slug

    # Upsert into DB
    try:
        existing = await session.scalar(
            select(IPOAnalysisCache).where(IPOAnalysisCache.ipo_id == ipo_id)
        )
        if existing:
            existing.verdict       = analysis.get("verdict", "NEUTRAL")
            existing.score         = analysis.get("score", 5)
            existing.analysis_json = analysis
            existing.ipo_data_json = ipo
            existing.source        = analysis.get("source", "rule_based")
            existing.updated_at    = datetime.utcnow()
        else:
            session.add(IPOAnalysisCache(
                ipo_id       = ipo_id,
                ipo_slug     = slug,
                company_name = ipo.get("company_name") or ipo.get("name", ""),
                status       = ipo.get("status", "upcoming"),
                verdict      = analysis.get("verdict", "NEUTRAL"),
                score        = analysis.get("score", 5),
                analysis_json= analysis,
                ipo_data_json= ipo,
                source       = analysis.get("source", "rule_based"),
            ))
        await session.commit()
    except Exception as exc:
        logger.warning("Failed to persist IPO analysis: %s", exc)
        await session.rollback()

    return {**result, "cached": False}


# ── GET /{slug}/subscription  — live NSE subscription data ───────────────────

@router.get("/{slug}/subscription")
async def get_subscription(slug: str):
    await get_ipo_cache()
    ipo = IPO_CACHE["by_slug"].get(slug) or IPO_CACHE["by_id"].get(slug)
    if ipo is None:
        raise HTTPException(status_code=404, detail=f"IPO '{slug}' not found")

    nse_url = ipo.get("nse_url") or ipo.get("nse_info_url") or ""
    if not nse_url:
        return {"slug": slug, "subscription": None, "message": "No NSE info URL available"}

    sub = await fetch_subscription_status(nse_url)
    return {"slug": slug, "subscription": sub}


# ── POST /refresh  — force cache refresh ─────────────────────────────────────

@router.post("/refresh")
async def force_refresh():
    await refresh_ipo_cache()
    return {
        "message":  "IPO cache refreshed",
        "count":    len(IPO_CACHE["data"]),
        "refreshed_at": datetime.utcnow().isoformat(),
    }


# ── GET /stats/summary  — aggregate stats ─────────────────────────────────────

@router.get("/stats/summary")
async def ipo_stats():
    data = await get_ipo_cache()

    by_status: dict[str, int] = {}
    by_type:   dict[str, int] = {}
    open_ipos  = []
    upcoming   = []

    for ipo in data:
        st = ipo.get("status", "unknown").lower()
        tp = ipo.get("ipo_type", "EQ")
        by_status[st] = by_status.get(st, 0) + 1
        by_type[tp]   = by_type.get(tp, 0) + 1
        if st == "open":
            open_ipos.append({"name": ipo.get("company_name") or ipo.get("name"), "slug": ipo.get("slug"), "closes": ipo.get("close_date_parsed")})
        elif st in ("upcoming", "announced"):
            upcoming.append({"name": ipo.get("company_name") or ipo.get("name"), "slug": ipo.get("slug"), "opens": ipo.get("open_date_parsed")})

    return {
        "total":      len(data),
        "by_status":  by_status,
        "by_type":    by_type,
        "open_ipos":  open_ipos[:10],
        "upcoming":   upcoming[:10],
        "source":     "ipoalerts" if settings.ipoalerts_available else "nse_fallback",
        "api_key_configured": settings.ipoalerts_available,
    }
