"""Hub universe management — the configurable set of symbols the Master
Intelligence Hub deep-scores (7-factor) each cycle.

Resolution priority (get_hub_universe):
  1. settings.HUB_SYMBOLS env (comma-separated) — manual override
  2. hub_universe DB table (top-N by turnover, rebuilt daily)
  3. settings.nse_symbols — legacy hardcoded fallback (cold start)

rebuild_hub_universe() ranks all NSE equities by average daily turnover
(₹ volume × close over the last 30 days), excludes bonds/SME/illiquid names,
and writes the top-N to the hub_universe table.
"""
from __future__ import annotations

from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger


async def rebuild_hub_universe(
    session: AsyncSession,
    *,
    top_n: int = 500,
    min_turnover_cr: float = 5.0,
) -> dict:
    """Rebuild the hub_universe table: top-N NSE equities by 30-day avg turnover.

    Excludes government bonds / debt (numeric or -SG names) and anything below
    `min_turnover_cr` (₹ Cr/day). Returns a summary dict.
    """
    from db.models import HubUniverse

    min_turnover = min_turnover_cr * 1e7  # ₹ Cr → ₹

    # Average daily turnover (volume × close) over the last 30 calendar days.
    # Exclude bond/debt symbols (contain digits, or -SG/-SM/-ST/-BE/-BZ suffixes).
    rows = (await session.execute(text("""
        SELECT symbol, AVG(volume * close) AS turnover
        FROM candles
        WHERE timeframe = '1d'
          AND timestamp > NOW() - INTERVAL '30 days'
          AND symbol LIKE '%.NS'
          AND symbol !~ '[0-9]'                         -- drop numeric debt codes
          AND symbol NOT LIKE '%-SG.NS'
          AND symbol NOT LIKE '%-SM.NS'
          AND symbol NOT LIKE '%-ST.NS'
          AND symbol NOT LIKE '%-BE.NS'
          AND symbol NOT LIKE '%-BZ.NS'
        GROUP BY symbol
        HAVING AVG(volume * close) >= :min_t
        ORDER BY turnover DESC
        LIMIT :n
    """), {"min_t": min_turnover, "n": top_n})).all()

    await session.execute(delete(HubUniverse))
    for rank, r in enumerate(rows, start=1):
        session.add(HubUniverse(
            symbol=r.symbol,
            turnover_cr=round(float(r.turnover) / 1e7, 2),
            rank=rank,
        ))
    await session.commit()

    summary = {
        "universe_size": len(rows),
        "min_turnover_cr": min_turnover_cr,
        "top": [r.symbol.replace(".NS", "") for r in rows[:5]],
    }
    logger.info(f"[hub_universe] rebuilt → {summary}")
    return summary


async def get_hub_universe(session: AsyncSession) -> list[str]:
    """Resolve the active Hub universe (list of '.NS' symbols)."""
    # 1. Manual env override
    env_syms = (getattr(settings, "HUB_SYMBOLS", "") or "").strip()
    if env_syms:
        syms = [s.strip() for s in env_syms.split(",") if s.strip()]
        return [s if s.endswith(".NS") else f"{s}.NS" for s in syms]

    # 2. hub_universe DB table (top-N by turnover)
    from db.models import HubUniverse
    rows = (await session.execute(
        select(HubUniverse.symbol).order_by(HubUniverse.rank)
    )).scalars().all()
    if rows:
        return list(rows)

    # 3. Legacy fallback
    logger.warning("[hub_universe] empty — falling back to settings.nse_symbols")
    return settings.nse_symbols
