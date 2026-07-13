"""Level-4 reflection / memory.

reflect_on_closed_trade(trade) — after a trade closes, ask the LLM to distill ONE
transferable lesson from the entry thesis vs the realised outcome, and persist it
to trade_lessons.

get_relevant_lessons(strategy, regime, side) — retrieve recent lessons matching a
candidate so the reasoning gate can inject them into its prompt ("remember last
time a PULLBACK_LONG in a chop regime stopped out…").

Both are gated by AGENT_LLM_REFLECTION_ENABLED, fail-open, and never raise into
the trading flow.
"""
from __future__ import annotations

from utils.config import settings
from utils.logger import logger


async def reflect_on_closed_trade(trade) -> None:
    """Distil + store one lesson from a just-closed trade. Fire-and-forget."""
    if not getattr(settings, "AGENT_LLM_REFLECTION_ENABLED", False):
        return
    try:
        from utils.llm import call_llm_chat
        from db.database import AsyncSessionLocal
        from db.models import TradeLesson

        strategy = getattr(trade, "strategy_name", None) or "?"
        regime   = getattr(trade, "regime_at_entry", None) or "?"
        side     = getattr(trade, "ai_reason", "") and getattr(trade, "side", None)
        exit_rsn = getattr(trade, "exit_reason", None) or "?"
        r_mult   = getattr(trade, "r_multiple", None)
        pnl      = getattr(trade, "pnl", 0.0) or 0.0
        won      = pnl > 0
        thesis   = (getattr(trade, "ai_reason", "") or "")[:400]

        sys_prompt = (
            "You are a trading coach. From ONE closed NSE swing trade — its entry "
            "thesis and its realised outcome — write a single transferable lesson "
            "(<=25 words) that would help judge similar future setups. Be concrete "
            "and self-critical. No preamble, output ONLY the lesson sentence."
        )
        user_prompt = (
            f"Strategy {strategy} | Regime-at-entry {regime} | Exit {exit_rsn} | "
            f"R={r_mult} | {'WIN' if won else 'LOSS'}\n"
            f"Entry thesis: {thesis}"
        )
        lesson = await call_llm_chat(
            [{"role": "system", "content": sys_prompt},
             {"role": "user",   "content": user_prompt}],
            max_tokens=80, temperature=0.3,
        )
        if not lesson:
            return
        lesson = lesson.strip().strip('"')[:300]

        async with AsyncSessionLocal() as s:
            s.add(TradeLesson(
                symbol=getattr(trade, "symbol", "?"), strategy=strategy if strategy != "?" else None,
                regime=regime if regime != "?" else None, side=side,
                exit_reason=exit_rsn if exit_rsn != "?" else None,
                r_multiple=r_mult, won=won, lesson=lesson,
            ))
            await s.commit()
        logger.info(f"[reflection] {getattr(trade,'symbol','?')} lesson stored: {lesson[:80]}")
    except Exception as exc:
        logger.debug(f"[reflection] skipped for {getattr(trade,'symbol','?')}: {exc}")


async def get_relevant_lessons(strategy: str | None, regime: str | None,
                               side: str | None = None, limit: int | None = None) -> list[str]:
    """Return recent lessons most relevant to a candidate (match strategy first,
    then regime), newest first. Empty list if reflection is off or none exist."""
    if not getattr(settings, "AGENT_LLM_REFLECTION_ENABLED", False):
        return []
    try:
        from db.database import AsyncSessionLocal
        from sqlalchemy import text as _t
        k = int(limit or getattr(settings, "AGENT_LLM_LESSONS_IN_PROMPT", 5))
        # Rank: same strategy+regime (2) > same strategy (1) > same regime (1) > other (0).
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(_t("""
                SELECT lesson FROM trade_lessons
                ORDER BY (CASE WHEN strategy = :st AND regime = :rg THEN 3
                               WHEN strategy = :st THEN 2
                               WHEN regime   = :rg THEN 1 ELSE 0 END) DESC,
                         created_at DESC
                LIMIT :k
            """), {"st": strategy, "rg": regime, "k": k})).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception as exc:
        logger.debug(f"[reflection] lesson fetch failed: {exc}")
        return []
