"""T1-hit re-analysis (2026-07-22, user-requested).

Previously, touching Target 1 always did the SAME mechanical thing
regardless of context: book a 50% partial, move SL to breakeven, and either
hold the rest fixed or trail it toward T2 — no fresh look at whether the
stock is actually likely to keep going or about to reverse.

analyze_t1_hit() runs a fresh LLM analysis the MOMENT T1 is touched (not on
the next periodic cycle) using the same 7-Hub score / recent-news context
dynamic_management.py already gathers, and decides:
  - CONTINUE: proceed with the existing partial-book + trail-to-T2 behavior.
  - EXIT: close the WHOLE remaining position now (not just the 50% partial)
    and name a support/resistance level worth watching — the caller
    (paper_trading/trade_simulator.py) registers this as a ReentryWatch row.
    If price later breaks that level, a fresh full re-analysis re-authorizes
    a brand-new TradeIntent against the SAME canonical event (NO EVENT -> NO
    TRADE still applies to the re-entry — this is what satisfies it without
    needing a new news trigger).

Fails open to CONTINUE on any LLM/parse failure — an inability to get a
fresh opinion must not block the existing, already-safe mechanical behavior.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MasterIntelligenceScore, NewsItem
from engine.agent.decision_engine import _parse_first_json
from utils.logger import logger


async def analyze_t1_hit(
    *, symbol: str, direction: str, entry_price: float, price: float,
    t1: float, t2: float, unrealised_pct: float, session: AsyncSession,
) -> dict:
    """Returns {"decision": "CONTINUE" | "EXIT", "reasoning": str,
    "watch_level": float | None}. watch_level is only meaningful when
    decision == "EXIT" (a support level for a BUY re-entry, or a resistance
    level for a SELL re-entry)."""
    from utils.llm import call_llm_chat

    try:
        since = datetime.utcnow() - timedelta(minutes=90)
        news = (await session.execute(
            select(NewsItem.headline).where(NewsItem.crawled_at > since)
            .order_by(NewsItem.crawled_at.desc()).limit(10)
        )).scalars().all()
        hub_row = (await session.execute(
            select(MasterIntelligenceScore).where(MasterIntelligenceScore.symbol == symbol)
            .order_by(MasterIntelligenceScore.id.desc()).limit(1)
        )).scalar_one_or_none()
    except Exception as exc:
        logger.debug(f"[t1_reanalysis] {symbol}: context fetch failed, proceeding with empty context: {exc}")
        news, hub_row = [], None

    news_text = "\n".join(f"- {n}" for n in news) or "(no recent news in the last 90 minutes)"
    hub_text = (
        f"Master Score: {hub_row.master_score} | Technical: {hub_row.technical_score} | "
        f"News: {hub_row.news_score} | Sector: {hub_row.sector_score} | "
        f"Fundamental: {hub_row.fundamental_score}"
    ) if hub_row else "No Hub score available"

    prompt = f"""You are an elite intraday/swing trader. This position JUST touched Target 1 (T1) — the
mechanical default is to book 50% profit and ride the rest to Target 2 (T2) with a trailing stop.
Your job: decide if that default is still right, or if there's real reversal risk that means we
should get out of the WHOLE remaining position right now instead.

Symbol: {symbol}
Direction: {direction}
Entry: {entry_price}
T1 (just hit): {t1}
T2 (final target): {t2}
Current price: {price}
Unrealised PnL: {unrealised_pct:.2f}%

[7-FACTOR HUB SCORE]:
{hub_text}

[RECENT NEWS (last 90 min)]:
{news_text}

Instructions:
1. If the setup still supports a real move toward T2 (momentum/Hub/news all still favourable,
   no sign of exhaustion or reversal), respond CONTINUE — the position keeps riding toward T2.
2. If you see genuine reversal risk (momentum stalling right at T1, negative news, weakening
   Hub scores, or T1 was reached on an exhaustion spike rather than sustained strength), respond
   EXIT — the WHOLE remaining position closes now, not just half. Also give a concrete price level
   (a real support/resistance from the recent price structure) worth watching: if price later
   breaks back through that level in the trade's original direction, that is a legitimate fresh
   entry signal, not a continuation of a dead move.
3. Be decisive. Most of the time when momentum is genuinely intact the answer is CONTINUE — only
   choose EXIT when you can point to a real reason, not vague caution.

Respond ONLY with valid JSON:
{{
    "decision": "CONTINUE" or "EXIT",
    "watch_level": <price level to watch for re-entry, ONLY if decision is EXIT, else null>,
    "reasoning": "Cite specific Hub scores and/or news that justify this call."
}}
"""
    try:
        resp = await call_llm_chat(
            [
                {"role": "system", "content": "You are a disciplined intraday risk manager. Output only the requested JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800, temperature=0.2,
        )
        data = _parse_first_json(resp)
        decision = str((data or {}).get("decision") or "").strip().upper()
        if decision not in ("CONTINUE", "EXIT"):
            return {"decision": "CONTINUE", "reasoning": "fail-open: no/invalid LLM decision", "watch_level": None}

        watch_level = None
        if decision == "EXIT":
            try:
                watch_level = float(data.get("watch_level"))
                if watch_level <= 0:
                    watch_level = None
            except (TypeError, ValueError):
                watch_level = None

        return {
            "decision": decision,
            "reasoning": str(data.get("reasoning") or "")[:500],
            "watch_level": watch_level,
        }
    except Exception as exc:
        logger.debug(f"[t1_reanalysis] {symbol}: LLM analysis failed, fail-open to CONTINUE: {exc}")
        return {"decision": "CONTINUE", "reasoning": f"fail-open (error: {exc})", "watch_level": None}
