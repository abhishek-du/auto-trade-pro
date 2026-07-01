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
    top_n: int = 3000,
    min_turnover_cr: float = 1.0,
) -> dict:
    """Rebuild the hub_universe table: top-N NSE equities by 30-day avg turnover.

    Threshold lowered from ₹5Cr → ₹1Cr to include small-caps:
    JTEKTINDIA (~₹4Cr), SAKSOFT (~₹4.5Cr), SIGNPOST (~₹3Cr) now qualify.
    Universe expanded from 1,500 → 3,000 to cover the wider small-cap space.

    Excludes government bonds / debt (numeric or -SG names) and anything below
    `min_turnover_cr` (₹ Cr/day). Returns a summary dict.
    """
    from db.models import HubUniverse

    min_turnover = min_turnover_cr * 1e7  # ₹ Cr → ₹

    # Include both NSE (.NS) and BSE (.BO); exclude bonds/SME/illiquid.
    _exclude = """
        AND (symbol LIKE '%.NS' OR symbol LIKE '%.BO')
        AND symbol !~ '[0-9]'
        AND symbol NOT LIKE '%-SG.NS'
        AND symbol NOT LIKE '%-SM.NS'
        AND symbol NOT LIKE '%-ST.NS'
        AND symbol NOT LIKE '%-BE.NS'
        AND symbol NOT LIKE '%-BZ.NS'
        AND symbol NOT LIKE '%-SG.BO'
        AND symbol NOT LIKE '%-SM.BO'
    """

    # Primary: 1d candles (most accurate for daily turnover).
    rows = (await session.execute(text(f"""
        SELECT symbol, AVG(volume * close) AS turnover
        FROM candles
        WHERE timeframe = '1d'
          AND timestamp > NOW() - INTERVAL '30 days'
          {_exclude}
        GROUP BY symbol
        HAVING AVG(volume * close) >= :min_t
        ORDER BY turnover DESC
        LIMIT :n
    """), {"min_t": min_turnover, "n": top_n})).all()

    # Fallback: when 1d candles are absent (pre-backfill cold start), use 1h
    # bars aggregated to a daily-equivalent turnover estimate.  A trading day
    # has ~6.25 NSE hours, so summing 1h volume*close gives a comparable figure.
    if not rows:
        logger.info("[hub_universe] no 1d candles — falling back to 1h for turnover ranking")
        rows = (await session.execute(text(f"""
            SELECT symbol,
                   AVG(daily_turnover) AS turnover
            FROM (
                SELECT symbol,
                       DATE(timestamp) AS day,
                       SUM(volume * close) AS daily_turnover
                FROM candles
                WHERE timeframe = '1h'
                  AND timestamp > NOW() - INTERVAL '30 days'
                  {_exclude}
                GROUP BY symbol, DATE(timestamp)
            ) daily
            GROUP BY symbol
            HAVING AVG(daily_turnover) >= :min_t
            ORDER BY turnover DESC
            LIMIT :n
        """), {"min_t": min_turnover, "n": top_n})).all()

    await session.execute(delete(HubUniverse))
    for rank, r in enumerate(rows, start=1):
        # Swing mode: stocks ranked 50-1500 are swing candidates.
        # Top-49 are index heavyweights (intraday/positional dominated).
        # Breakout-injected stocks are also set is_swing=True by the screener.
        # Zerodha Varsity: swing works best on liquid mid/large-caps → rank 50-1500.
        session.add(HubUniverse(
            symbol=r.symbol,
            turnover_cr=round(float(r.turnover) / 1e7, 2),
            rank=rank,
            is_swing=True,
        ))
    await session.commit()

    summary = {
        "universe_size": len(rows),
        "min_turnover_cr": min_turnover_cr,
        "top": [r.symbol.replace(".NS", "").replace(".BO", "") for r in rows[:5]],
    }
    logger.info(f"[hub_universe] rebuilt → {summary}")
    return summary


async def get_hub_universe(session: AsyncSession) -> list[str]:
    """Resolve the active Hub universe (list of '.NS' / '.BO' symbols)."""
    # 1. Manual env override
    env_syms = (getattr(settings, "HUB_SYMBOLS", "") or "").strip()
    if env_syms:
        syms = [s.strip() for s in env_syms.split(",") if s.strip()]
        # Preserve explicit suffix; default bare names to .NS
        return [s if (s.endswith(".NS") or s.endswith(".BO")) else f"{s}.NS" for s in syms]

    # 2. hub_universe DB table (top-N by turnover)
    from db.models import HubUniverse
    rows = (await session.execute(
        select(HubUniverse.symbol).order_by(HubUniverse.rank)
    )).scalars().all()
    if rows:
        return list(rows)

    # 3. Legacy fallback — include BSE watchlist alongside NSE
    logger.warning("[hub_universe] empty — falling back to settings watchlists")
    return settings.nse_symbols + settings.bse_symbols
