"""Approximate F&O margin model for paper trading.

This is an HONEST APPROXIMATION, not exchange-exact SPAN. Real SPAN runs a
portfolio scenario analysis; here we use:

    margin ≈ notional × (SPAN_PCT + EXPOSURE_PCT) × (1 + BUFFER)

which lands in the right ballpark for index futures/options (~15-20% of
notional). Must be revisited before any real-money use.

For BOUGHT options the margin is simply the premium debit (handled in
selection.py) — defined risk, no SPAN needed. This module covers FUTURES and
SHORT options where margin is a fraction of notional.
"""
from __future__ import annotations

from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OpenPosition
from utils.config import settings


def span_exposure_margin(notional: float) -> float:
    """Approximate SPAN + exposure margin for an index F&O notional."""
    rate = (settings.FNO_SPAN_PCT_INDEX + settings.FNO_EXPOSURE_PCT) * (1 + settings.FNO_MARGIN_BUFFER)
    return round(abs(notional) * rate, 2)


async def blocked_margin(session: AsyncSession) -> float:
    """Total margin currently blocked across all open positions."""
    total = (await session.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(OpenPosition.margin_blocked), 0.0))
    )).scalar()
    return float(total or 0.0)


async def available_margin(equity: float, session: AsyncSession) -> float:
    """Deployable margin = equity − already-blocked margin − min cash buffer."""
    blocked = await blocked_margin(session)
    buffer  = equity * settings.AGENT_CASH_BUFFER_MIN
    return max(0.0, equity - blocked - buffer)


async def can_block_margin(required: float, equity: float, session: AsyncSession) -> tuple[bool, str]:
    """Margin-authorization gate: is there room to block `required` more margin?"""
    avail = await available_margin(equity, session)
    if required > avail:
        return False, f"MARGIN_INSUFFICIENT need ₹{required:,.0f} avail ₹{avail:,.0f}"
    return True, "OK"
