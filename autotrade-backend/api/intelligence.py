"""Master Intelligence Hub API — /api/v1/intelligence endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import MasterIntelligenceScore, MFIntelligenceScore, HubCycleLog

router = APIRouter(tags=["Intelligence Hub"])


# ── GET /context ──────────────────────────────────────────────────────────────

@router.get("/context")
async def get_context(db: AsyncSession = Depends(get_db)):
    """Return the last-built MasterContext (or build a fresh one if none cached)."""
    import engine.intelligence_hub as hub

    macro = hub.LAST_MACRO_CONTEXT
    if macro is None:
        # Build a lightweight macro/sector view on demand
        macro = await hub.build_macro_context(db)
    sectors = hub.build_sector_context()
    news    = hub.LAST_NEWS_CONTEXT
    earnings = hub.LAST_EARNINGS_CONTEXT

    return {
        "built_at": getattr(hub, "LAST_BUILT_AT", None),
        "macro": {
            "fii_net_1d":       macro.fii_net_1d,
            "fii_net_3d":       macro.fii_net_3d,
            "fii_net_5d":       macro.fii_net_5d,
            "dii_net_3d":       macro.dii_net_3d,
            "india_vix":        macro.india_vix,
            "vix_label":        macro.vix_label,
            "nse_market_mood":  macro.nse_market_mood,
            "advance_decline_ratio": macro.advance_decline_ratio,
            "total_macro_bias": macro.total_macro_bias,
        },
        "sectors": {
            "strongest":       sectors.strongest_sector,
            "weakest":         sectors.weakest_sector,
            "rotating_into":   sectors.rotating_into,
            "rotating_out_of": sectors.rotating_out_of,
            "sector_moods":    sectors.sector_moods,
            "sector_biases":   sectors.sector_biases,
        },
        "news": {
            "market_wide_score": news.market_wide_score if news else 0.0,
            "symbols_with_data": len(news.scores_by_symbol) if news else 0,
        },
        "earnings": {
            "tones_by_symbol": earnings.tones_by_symbol if earnings else {},
        },
    }


# ── GET /scores ───────────────────────────────────────────────────────────────

@router.get("/scores")
async def get_scores(
    limit:   int = Query(50, le=200),
    signal:  Optional[str] = None,
    blocked: Optional[bool] = None,
    sector:  Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Ranked universe scores from the most recent cycle (latest scored_at)."""
    latest = (await db.execute(
        select(MasterIntelligenceScore.scored_at)
        .order_by(desc(MasterIntelligenceScore.scored_at)).limit(1)
    )).scalar_one_or_none()
    if latest is None:
        return []

    q = select(MasterIntelligenceScore).where(MasterIntelligenceScore.scored_at == latest)
    if signal:
        q = q.where(MasterIntelligenceScore.signal == signal.upper())
    if blocked is not None:
        q = q.where(MasterIntelligenceScore.is_blocked == blocked)
    q = q.order_by(MasterIntelligenceScore.rank).limit(limit)

    rows = (await db.execute(q)).scalars().all()
    out = []
    for r in rows:
        if sector and (r.reasoning or {}).get("sector_name") != sector:
            continue
        out.append(_score_to_dict(r))
    return out


# ── GET /scores/{symbol} ──────────────────────────────────────────────────────

@router.get("/scores/{symbol}")
async def get_symbol_history(symbol: str, db: AsyncSession = Depends(get_db)):
    """Score history for one symbol (last 5 cycles)."""
    rows = (await db.execute(
        select(MasterIntelligenceScore)
        .where(MasterIntelligenceScore.symbol == symbol)
        .order_by(desc(MasterIntelligenceScore.scored_at)).limit(5)
    )).scalars().all()
    return [_score_to_dict(r) for r in rows]


# ── GET /score-breakdown/{symbol} ────────────────────────────────────────────

@router.get("/score-breakdown/{symbol}")
async def get_score_breakdown(symbol: str, db: AsyncSession = Depends(get_db)):
    """Full reasoning breakdown for a symbol at the last cycle."""
    row = (await db.execute(
        select(MasterIntelligenceScore)
        .where(MasterIntelligenceScore.symbol == symbol)
        .order_by(desc(MasterIntelligenceScore.scored_at)).limit(1)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, f"No score for {symbol}")
    return {
        **_score_to_dict(row),
        "components": {
            "technical":   row.technical_score,
            "news":        row.news_score,
            "sector":      row.sector_score,
            "macro":       row.macro_score,
            "earnings":    row.earnings_score,
            "fundamental": row.fundamental_score,
            "options":     row.options_score,
        },
        "full_reasoning": row.reasoning,
    }


# ── GET /mf-signals ───────────────────────────────────────────────────────────

@router.get("/mf-signals")
async def get_mf_signals(limit: int = Query(20, le=100), db: AsyncSession = Depends(get_db)):
    latest = (await db.execute(
        select(MFIntelligenceScore.scored_at)
        .order_by(desc(MFIntelligenceScore.scored_at)).limit(1)
    )).scalar_one_or_none()
    if latest is None:
        return []
    rows = (await db.execute(
        select(MFIntelligenceScore).where(MFIntelligenceScore.scored_at == latest)
        .order_by(desc(MFIntelligenceScore.master_score)).limit(limit)
    )).scalars().all()
    return [{
        "scheme_code":  r.scheme_code,
        "scheme_name":  r.scheme_name,
        "category":     r.category,
        "signal":       r.signal,
        "master_score": r.master_score,
        "reasoning":    (r.reasoning or {}).get("text", ""),
    } for r in rows]


# ── GET /cycle-log ────────────────────────────────────────────────────────────

@router.get("/cycle-log")
async def get_cycle_log(limit: int = Query(10, le=50), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(HubCycleLog).order_by(desc(HubCycleLog.cycle_start)).limit(limit)
    )).scalars().all()
    return [{
        "id":               r.id,
        "cycle_start":      r.cycle_start.isoformat() if r.cycle_start else None,
        "cycle_end":        r.cycle_end.isoformat() if r.cycle_end else None,
        "symbols_scored":   r.symbols_scored,
        "top_buys":         r.top_buys,
        "top_sells":        r.top_sells,
        "macro_context":    r.macro_context,
        "decisions_made":   r.decisions_made,
        "skipped_count":    r.skipped_count,
        "status":           r.status,
        "duration_seconds": r.duration_seconds,
    } for r in rows]


# ── GET /top-opportunities ────────────────────────────────────────────────────

@router.get("/top-opportunities")
async def get_top_opportunities(db: AsyncSession = Depends(get_db)):
    """Top BUYs + SELLs from the latest cycle (non-blocked)."""
    latest = (await db.execute(
        select(MasterIntelligenceScore.scored_at)
        .order_by(desc(MasterIntelligenceScore.scored_at)).limit(1)
    )).scalar_one_or_none()
    if latest is None:
        return {"buys": [], "sells": []}

    rows = (await db.execute(
        select(MasterIntelligenceScore)
        .where(MasterIntelligenceScore.scored_at == latest,
               MasterIntelligenceScore.is_blocked == False)  # noqa: E712
        .order_by(MasterIntelligenceScore.rank)
    )).scalars().all()

    buys = [_score_to_dict(r) for r in rows if r.signal in ("STRONG_BUY", "BUY")][:10]
    sells = [_score_to_dict(r) for r in rows if r.signal in ("STRONG_SELL", "SELL")][:5]
    return {"buys": buys, "sells": sells}


# ── POST /trigger ─────────────────────────────────────────────────────────────

@router.post("/trigger")
async def trigger_cycle():
    """Fire one master intelligence cycle via Celery (async)."""
    try:
        from tasks.india_tasks import run_master_intelligence_cycle
        async_result = run_master_intelligence_cycle.delay()
        return {"triggered": True, "task_id": str(async_result.id)}
    except Exception as exc:
        # Fallback: run inline if broker unavailable
        raise HTTPException(503, f"Could not queue cycle: {exc}")


# ── helper ────────────────────────────────────────────────────────────────────

def _score_to_dict(r: MasterIntelligenceScore) -> dict:
    return {
        "rank":           r.rank,
        "symbol":         r.symbol,
        "master_score":   r.master_score,
        "signal":         r.signal,
        "regime":         r.regime,
        "is_blocked":     r.is_blocked,
        "blocked_reason": r.blocked_reason,
        "scored_at":      r.scored_at.isoformat() if r.scored_at else None,
        "reasoning": {
            "technical":   r.technical_score,
            "news":        r.news_score,
            "sector":      r.sector_score,
            "macro":       r.macro_score,
            "earnings":    r.earnings_score,
            "fundamental": r.fundamental_score,
            "options":     r.options_score,
        },
    }
