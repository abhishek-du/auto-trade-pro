"""Path F — do not stack tactical exposure on top of a news position.

The brief specified filtering `positions` on `strategy_family IN (...)`, but
`open_positions` has no `strategy_family` column (verified while planning). The
family lives on the linked `paper_trades` row instead, as `strategy_name` /
`source`, so the guard joins through `open_positions.trade_id`.

READ-ONLY: this module only ever SELECTs.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OpenPosition, PaperTrade
from utils.logger import logger

# Strategy-name fragments that indicate a news-family position. Matched
# case-insensitively against PaperTrade.strategy_name / source.
NEWS_FAMILY_MARKERS = ("NEWS", "EVENT", "PRE_EVENT", "DIRECT", "CASCADE")


def _normalise(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".BO", "").upper()


async def existing_positions(session: AsyncSession) -> dict[str, str]:
    """Map of normalised symbol -> owning strategy, for every OPEN position."""
    try:
        rows = (
            await session.execute(
                select(OpenPosition.symbol, PaperTrade.strategy_name, PaperTrade.source)
                .outerjoin(PaperTrade, OpenPosition.trade_id == PaperTrade.id)
            )
        ).all()
    except Exception as exc:
        logger.warning(f"[tactical_dupe] position lookup failed: {exc}")
        # Fail CLOSED-ish: an empty map would let every signal through as
        # "no duplicate". Signal that we could not tell.
        raise

    out: dict[str, str] = {}
    for sym, strategy, source in rows:
        if sym:
            out[_normalise(sym)] = strategy or source or "UNKNOWN"
    return out


def is_duplicate(symbol: str, open_map: dict[str, str]) -> tuple[bool, str]:
    """True when this symbol already has an open position from any strategy.

    Deliberately blocks on ANY open position, not just the news families. A
    second tactical position in the same name is doubled exposure just as much
    as stacking on a news trade, and the brief's own intent is to avoid
    doubling.
    """
    owner = open_map.get(_normalise(symbol))
    if owner is None:
        return False, ""
    is_news = any(m in owner.upper() for m in NEWS_FAMILY_MARKERS)
    kind = "news-family" if is_news else "existing"
    return True, f"{kind} position already open in {symbol} (strategy={owner})"
