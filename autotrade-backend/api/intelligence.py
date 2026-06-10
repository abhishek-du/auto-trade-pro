"""Master Intelligence Hub API — /api/v1/intelligence endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import MasterIntelligenceScore, MFIntelligenceScore, HubCycleLog

router = APIRouter(tags=["Intelligence Hub"])


# ── Factor explanation builder ────────────────────────────────────────────────

def _build_factor_explanations(components: dict, reasoning: dict) -> dict:
    """Generate human-readable WHY explanations for each of the 7 Hub factors."""
    aw          = reasoning.get("active_weights", {})
    headlines   = reasoning.get("headlines", [])
    news_tone   = reasoning.get("news_tone", "NEUTRAL")
    sector_name = reasoning.get("sector_name", "GENERAL")
    sector_mood = reasoning.get("sector_mood", "NEUTRAL")
    regime      = reasoning.get("regime", "UNKNOWN")
    fund_grade  = reasoning.get("fund_grade", "WATCHLIST")

    TRACKED = ["IT", "Banking", "Pharma", "Auto", "FMCG", "Metals", "Energy", "Infra", "Consumer", "Telecom"]

    def _verdict(score: float) -> str:
        if score > 50:  return "Strongly Bullish"
        if score > 20:  return "Bullish"
        if score > -20: return "Neutral"
        if score > -50: return "Bearish"
        return "Strongly Bearish"

    expl: dict = {}

    # 1. Technical ─────────────────────────────────────────────────────────────
    t  = components.get("technical", 0)
    tw = aw.get("technical", 0.35)
    if t > 80:
        tech_detail = (
            f"Near-perfect indicator alignment — score {t:.1f}/100. All major indicators fire bullishly: "
            "EMA stack bullish, RSI not overbought, MACD histogram recovering or positive, "
            "Ichimoku in strong buy configuration (price above cloud with all 5 components aligned), "
            "and/or price at an extreme Bollinger Band level signalling mean-reversion. "
            "This is the strongest technical setup the Hub assigns."
        )
    elif t > 40:
        tech_detail = (
            f"Moderately bullish technicals — score {t:.1f}/100. More bullish signals firing than bearish. "
            "Typical configuration: EMA trend bullish, RSI in healthy range, positive MACD. "
            "Some indicators may still be neutral or slightly bearish — not every indicator is aligned."
        )
    elif t > 0:
        tech_detail = (
            f"Slight bullish edge — score {t:.1f}/100. Mixed picture: some indicators bullish, others neutral/bearish. "
            "Watch for confirmation (breakout with volume, or RSI/MACD alignment) before committing."
        )
    elif t > -40:
        tech_detail = (
            f"Bearish technical structure — score {t:.1f}/100. More bearish signals than bullish. "
            "Typical pattern: price below key EMAs, MACD negative, Supertrend bearish. "
            "Capital preservation is the priority; wait for reversal signals."
        )
    else:
        tech_detail = (
            f"Strong bearish breakdown — score {t:.1f}/100. Nearly all indicators point south. "
            "EMA stack fully bearish, RSI potentially oversold, trend is down. "
            "Only contrarian plays (deep value, mean reversion at extremes) would consider entry here."
        )
    expl["technical"] = {
        "score":        round(t, 1),
        "weight_pct":   round(tw * 100, 1),
        "contribution": round(t * tw, 1),
        "verdict":      _verdict(t),
        "detail":       tech_detail,
    }

    # 2. News / Sentiment ──────────────────────────────────────────────────────
    n  = components.get("news", 0)
    nw = aw.get("news", 0)
    if nw == 0:
        news_detail = (
            "No news articles mentioning this stock were found in the 7-day tracking window. "
            "Weight is set to 0% so this data gap doesn't penalise the overall score — "
            "a silent stock is treated as neutral, not bearish. "
            "The Hub tracks Finnhub headlines and FinBERT-scored RSS feeds; "
            "gaps are common for PSUs, small-caps, and stocks with low media coverage."
        )
        news_verdict = "No Coverage"
    else:
        hl_snippet = f" Latest: \"{headlines[0][:90]}\"" if headlines else ""
        if n > 30:
            news_detail = f"Positive news sentiment (FinBERT score: {n:+.1f}). {len(headlines)} headline(s) tracked.{hl_snippet}"
            news_verdict = "Positive"
        elif n < -30:
            news_detail = f"Negative news flow (FinBERT score: {n:+.1f}). {len(headlines)} headline(s) tracked.{hl_snippet}"
            news_verdict = "Negative"
        else:
            news_detail = (
                f"Neutral news sentiment (FinBERT score: {n:+.1f}). {len(headlines)} headline(s) in window.{hl_snippet} "
                f"Neutral tone: news mentions exist but don't carry a strong directional signal."
            )
            news_verdict = "Neutral"
    expl["news"] = {
        "score":        round(n, 1),
        "weight_pct":   round(nw * 100, 1),
        "contribution": round(n * nw, 1) if nw > 0 else 0,
        "verdict":      news_verdict,
        "detail":       news_detail,
        "headlines":    headlines[:3],
    }

    # 3. Sector ────────────────────────────────────────────────────────────────
    s  = components.get("sector", 0)
    sw = aw.get("sector", 0)
    if sw == 0 or sector_name == "GENERAL":
        sector_detail = (
            f"Sector '{sector_name}' is not in the Hub's 10 tracked sectors: {', '.join(TRACKED)}. "
            "The Hub tracks sector momentum via NSE sector indices (CNXBANK, CNXIT, etc.). "
            "Stocks that don't map to a tracked sector get weight 0% — "
            "the absence of sector data doesn't penalise the score. "
            "Capital Goods, Defense, and Railway PSUs typically fall into this category."
        )
        sector_verdict = "Not Tracked"
    else:
        sector_detail = (
            f"Sector: {sector_name} | Current mood: {sector_mood} | Regime: {regime}. "
            f"Score {s:+.0f} reflects sector momentum vs broader market. "
            f"Sectors outperforming Nifty50 get a positive bias; underperformers get negative. "
            f"Mood '{sector_mood}' comes from comparing sector index vs 20-day mean."
        )
        sector_verdict = _verdict(s)
    expl["sector"] = {
        "score":        round(s, 1),
        "weight_pct":   round(sw * 100, 1),
        "contribution": round(s * sw, 1) if sw > 0 else 0,
        "verdict":      sector_verdict,
        "detail":       sector_detail,
    }

    # 4. Macro ─────────────────────────────────────────────────────────────────
    m  = components.get("macro", 0)
    mw = aw.get("macro", 0.10)
    if m >= 24:
        macro_detail = (
            f"Strong macro tailwind (score {m:+.0f}). FII net buying is strong (3-day net > ₹2,000 Cr), "
            "India VIX is low (<13), and market breadth is bullish (more advances than declines). "
            "All three macro pillars aligned — best environment for longs."
        )
    elif m >= 12:
        macro_detail = (
            f"Mild macro support (score {m:+.0f}, regime: {regime}). FII flows are positive or neutral. "
            "VIX is moderate. Breadth is constructive. Macro isn't a headwind here."
        )
    elif m >= -12:
        macro_detail = (
            f"Neutral macro (score {m:+.0f}, regime: {regime}). FII flows mixed or slightly negative. "
            "India VIX in moderate zone (13–20). Market breadth near-neutral (ADR ~1.0). "
            "Macro isn't helping or hurting — stock-specific catalysts drive the trade."
        )
    elif m >= -24:
        macro_detail = (
            f"Macro headwind (score {m:+.0f}, regime: {regime}). FII net selling active. "
            "VIX elevated or breadth negative. Consider reducing position sizes and waiting "
            "for macro to stabilise before adding new longs."
        )
    else:
        macro_detail = (
            f"Significant macro headwind (score {m:+.0f}, regime: {regime}). Strong FII outflows. "
            "VIX high (>20). Breadth strongly negative. Market in risk-off mode — "
            "only very high-conviction setups are justified; cash is a valid position."
        )
    expl["macro"] = {
        "score":        round(m, 1),
        "weight_pct":   round(mw * 100, 1),
        "contribution": round(m * mw, 1),
        "verdict":      _verdict(m),
        "detail":       macro_detail,
        "regime":       regime,
    }

    # 5. Earnings ──────────────────────────────────────────────────────────────
    e  = components.get("earnings", 0)
    ew = aw.get("earnings", 0)
    if ew == 0:
        earn_detail = (
            "No earnings call transcript found for this stock in the last 90 days. "
            "The Hub analyses earnings call transcripts using NLP to score management tone "
            "(OPTIMISTIC → +30, NEUTRAL → 0, CAUTIOUS → -20, NEGATIVE → -40). "
            "Without a transcript, the weight is set to 0% — "
            "this is not a negative signal. PSUs and some mid-caps often lack indexed transcripts."
        )
        earn_verdict = "No Transcript"
    elif e >= 25:
        earn_detail = (
            f"OPTIMISTIC earnings tone (score {e:+.0f}). Management expressed positive guidance "
            "on revenue growth, margin expansion, or order wins in the most recent call. "
            "Earnings momentum is a bullish tailwind."
        )
        earn_verdict = "Optimistic"
    elif e >= 0:
        earn_detail = (
            f"NEUTRAL earnings tone (score {e:+.0f}). Management guidance was in-line — "
            "no major positive or negative surprises. Earnings factor contributes minimally to score."
        )
        earn_verdict = "Neutral"
    elif e >= -25:
        earn_detail = (
            f"CAUTIOUS earnings tone (score {e:+.0f}). Management flagged headwinds — "
            "margin pressure, demand slowdown, or project delays. Watch for earnings downgrade risk."
        )
        earn_verdict = "Cautious"
    else:
        earn_detail = (
            f"NEGATIVE earnings call (score {e:+.0f}). Management tone was distinctly bearish — "
            "revenue miss, guidance cut, or operational crisis mentioned. High risk of further downside."
        )
        earn_verdict = "Negative"
    expl["earnings"] = {
        "score":        round(e, 1),
        "weight_pct":   round(ew * 100, 1),
        "contribution": round(e * ew, 1) if ew > 0 else 0,
        "verdict":      earn_verdict,
        "detail":       earn_detail,
    }

    # 6. Fundamental ───────────────────────────────────────────────────────────
    f  = components.get("fundamental", 0)
    fw = aw.get("fundamental", 0)
    grade_desc = {
        "STRONG":    "Top-tier fundamentals — ROE/ROCE both >20%, low debt, strong revenue growth.",
        "GOOD":      "Above-average fundamentals — ROE/ROCE healthy (15–20%), manageable leverage.",
        "WATCHLIST": "Average fundamentals — ROE/ROCE around 10–15%, mixed growth metrics. Monitor closely.",
        "WEAK":      "Below-average fundamentals — thin margins, high debt, or negative growth. High risk.",
    }
    if fw == 0:
        fund_detail = (
            "Fundamental score not yet cached for this stock. "
            "The Hub scores ~2,000 NSE stocks weekly using yfinance + Screener data "
            "(ROE, ROCE, D/E, current ratio, revenue growth, promoter holding, earnings quality). "
            "First-time additions and infrequently traded stocks may not yet have a cached score."
        )
        fund_verdict = "No Data"
    else:
        fund_score_100 = round(50 + f, 0)
        fund_detail = (
            f"Fundamental grade: {fund_grade} (score {fund_score_100:.0f}/100). "
            f"{grade_desc.get(fund_grade, '')} "
            f"Score 50 = average; above 60 = top-quartile quality. "
            f"Derived from ROE, ROCE, debt/equity, revenue CAGR, profit CAGR, and promoter holding. "
            f"Contributes {f * fw:+.1f} points to master score."
        )
        fund_verdict = fund_grade.title()
    expl["fundamental"] = {
        "score":        round(f, 1),
        "weight_pct":   round(fw * 100, 1),
        "contribution": round(f * fw, 1) if fw > 0 else 0,
        "verdict":      fund_verdict,
        "detail":       fund_detail,
    }

    # 7. Options ───────────────────────────────────────────────────────────────
    opt = components.get("options", 0)
    ow  = aw.get("options", 0.05)
    if opt > 10:
        opt_detail = (
            f"NIFTY PCR > 1.3 — heavy put open interest signals fear and active hedging. "
            "Contrarian interpretation: when everyone is hedging (buying puts), "
            "the market is often near a bottom. Bullish signal for all stocks."
        )
        opt_verdict = "Contrarian Bullish"
    elif opt < -10:
        opt_detail = (
            f"NIFTY PCR < 0.7 — low put-call ratio signals complacency (call buyers dominate). "
            "Contrarian warning: when the market is too optimistic (heavy call OI), "
            "a reversal is more likely. Mild caution signal."
        )
        opt_verdict = "Complacency Warning"
    else:
        opt_detail = (
            f"NIFTY PCR in neutral zone (0.7–1.3) — balanced put/call activity. "
            "No strong contrarian signal from the index options market. "
            "Note: this is an INDEX-LEVEL indicator, not {symbol}-specific stock options. "
            "It acts as a tide-level factor affecting all stocks equally."
        )
        opt_verdict = "Neutral"
    expl["options"] = {
        "score":        round(opt, 1),
        "weight_pct":   round(ow * 100, 1),
        "contribution": round(opt * ow, 1),
        "verdict":      opt_verdict,
        "detail":       opt_detail,
    }

    return expl


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
    components = {
        "technical":   row.technical_score,
        "news":        row.news_score,
        "sector":      row.sector_score,
        "macro":       row.macro_score,
        "earnings":    row.earnings_score,
        "fundamental": row.fundamental_score,
        "options":     row.options_score,
    }
    return {
        **_score_to_dict(row),
        "components": components,
        "full_reasoning": row.reasoning,
        "factor_explanations": _build_factor_explanations(components, row.reasoning or {}),
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
