"""Earnings Call Analyzer API — /api/v1/earnings endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import EarningsCallSummary
from crawler.earnings_crawler import get_all_transcripts

router = APIRouter(tags=["Earnings"])


# ── GET /earnings/{symbol} ────────────────────────────────────────────────────

@router.get("/summary/{symbol}")
async def get_earnings_summary(
    symbol: str,
    quarter: Optional[str] = Query(None),
    refresh: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Fetch and AI-summarize the latest earnings call for a symbol.

    First call triggers transcript fetch + PDF extraction + Groq analysis (~20-30s).
    Subsequent calls return the cached result (<1s).
    Use ?refresh=true to force re-fetch.
    """
    from engine.earnings_summarizer import get_earnings_summary

    if refresh and quarter:
        # Delete cached entry so it re-summarizes
        await db.execute(
            select(EarningsCallSummary).where(
                EarningsCallSummary.symbol == symbol,
                EarningsCallSummary.quarter == quarter.upper(),
            )
        )
        existing = (await db.execute(
            select(EarningsCallSummary).where(
                EarningsCallSummary.symbol == symbol,
                EarningsCallSummary.quarter == quarter.upper(),
            )
        )).scalar_one_or_none()
        if existing:
            await db.delete(existing)
            await db.commit()

    try:
        summary = await get_earnings_summary(
            symbol=symbol,
            quarter=quarter,
            session=db,
            refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Earnings fetch failed: {exc}")

    if not summary:
        raise HTTPException(
            status_code=404,
            detail=f"No earnings transcript found for {symbol}. "
                   f"Transcript may not yet be filed with BSE/NSE."
        )

    return summary.to_dict()


# ── GET /earnings/{symbol}/list ───────────────────────────────────────────────

@router.get("/list/{symbol}")
async def list_transcripts(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """Return available transcript list without triggering summarization."""
    try:
        transcripts = await get_all_transcripts(symbol, limit=10)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Enrich with has_summary flag from DB
    cached_quarters = set(
        row.quarter
        for row in (await db.execute(
            select(EarningsCallSummary.quarter).where(
                EarningsCallSummary.symbol == symbol
            )
        )).scalars().all()
    )

    return [
        {
            **t,
            "has_summary": t.get("quarter", "") in cached_quarters,
        }
        for t in transcripts
    ]


# ── GET /earnings/{symbol}/history ───────────────────────────────────────────

@router.get("/history/{symbol}")
async def get_earnings_history(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """Return all cached summaries for a symbol, sorted by quarter."""
    rows = (await db.execute(
        select(EarningsCallSummary)
        .where(EarningsCallSummary.symbol == symbol)
        .order_by(desc(EarningsCallSummary.created_at))
        .limit(8)
    )).scalars().all()

    return [
        {
            "id":               r.id,
            "symbol":           r.symbol,
            "company_name":     r.company_name,
            "quarter":          r.quarter,
            "call_date":        r.call_date,
            "source":           r.source,
            "management_tone":  r.management_tone,
            "ai_confidence":    r.ai_confidence,
            "is_ai":            r.is_ai,
            "revenue_guidance": r.revenue_guidance,
            "margin_guidance":  r.margin_guidance,
            "word_count":       r.word_count,
            "created_at":       r.created_at.isoformat(),
        }
        for r in rows
    ]


# ── GET /earnings/recent ─────────────────────────────────────────────────────

@router.get("/recent")
async def get_recent_earnings(
    limit: int = Query(10, le=20),
    db: AsyncSession = Depends(get_db),
):
    """Most recently summarized earnings across all companies."""
    rows = (await db.execute(
        select(EarningsCallSummary)
        .order_by(desc(EarningsCallSummary.created_at))
        .limit(limit)
    )).scalars().all()

    return [
        {
            "id":                   r.id,
            "symbol":               r.symbol,
            "company_name":         r.company_name,
            "quarter":              r.quarter,
            "call_date":            r.call_date,
            "source":               r.source,
            "management_tone":      r.management_tone,
            "tone_reason":          r.tone_reason,
            "ai_confidence":        r.ai_confidence,
            "is_ai":                r.is_ai,
            "revenue_guidance":     r.revenue_guidance,
            "financial_highlights": (r.financial_highlights or [])[:1],
            "word_count":           r.word_count,
            "created_at":           r.created_at.isoformat(),
        }
        for r in rows
    ]


# ── POST /earnings/{symbol}/refresh ──────────────────────────────────────────

@router.post("/refresh/{symbol}")
async def refresh_earnings(
    symbol: str,
    quarter: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Force re-fetch and re-summarize, deleting the cached entry first."""
    from engine.earnings_summarizer import get_earnings_summary

    if quarter:
        existing = (await db.execute(
            select(EarningsCallSummary).where(
                EarningsCallSummary.symbol == symbol,
                EarningsCallSummary.quarter == quarter.upper(),
            )
        )).scalar_one_or_none()
        if existing:
            await db.delete(existing)
            await db.commit()

    try:
        summary = await get_earnings_summary(symbol=symbol, quarter=quarter, session=db, refresh=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not summary:
        raise HTTPException(status_code=404, detail=f"No transcript found for {symbol}")

    return summary.to_dict()


# ── GET /earnings/{symbol}/compare ───────────────────────────────────────────

@router.get("/compare/{symbol}")
async def compare_quarters(
    symbol: str,
    quarters: list[str] = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Return multiple summaries for side-by-side comparison."""
    rows = (await db.execute(
        select(EarningsCallSummary).where(
            EarningsCallSummary.symbol == symbol,
            EarningsCallSummary.quarter.in_([q.upper() for q in quarters]),
        )
    )).scalars().all()

    result = []
    for r in rows:
        result.append({
            "quarter":          r.quarter,
            "call_date":        r.call_date,
            "management_tone":  r.management_tone,
            "tone_reason":      r.tone_reason,
            "revenue_guidance": r.revenue_guidance,
            "margin_guidance":  r.margin_guidance,
            "capex_guidance":   r.capex_guidance,
            "ai_confidence":    r.ai_confidence,
            "financial_highlights": r.financial_highlights,
            "management_guidance":  r.management_guidance,
        })

    result.sort(key=lambda x: x["quarter"])
    return result
