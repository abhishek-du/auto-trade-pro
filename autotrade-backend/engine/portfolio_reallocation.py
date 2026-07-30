"""Thesis-based capital reallocation (added 2026-07-29, user request).

User asked to remove the hard MAX_OPEN_POSITIONS ceiling (see utils/config.py
-- capital/risk budget, not a fixed count, is now the real constraint) and,
connected to that: when a good new candidate is blocked purely by capital,
and an already-open position is losing money in a way its OWN strategy no
longer endorses, let the agent sell that position early -- regardless of
whether its stop-loss or Target 1 has fired -- to free capital for the
better-looking candidate.

This is deliberately a PURE REALLOCATION trigger, not a general "cut every
loser" rule (user explicitly chose this over a standing loss-threshold
rule): it only ever runs from inside risk_manager.validate_signal's
capital-rejection path, for the one candidate that triggered it, and it
never touches a position whose own strategy still endorses it -- a position
can be down a lot and simply sit there, protected by its normal
stop-loss/take-profit/T1-reversal/sector-reversal/confirmation-lost
handling, exactly as before this module existed.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import OpenPosition
from utils.logger import logger

# A position must be losing at least this much before it's even considered
# for reallocation -- keeps this mechanism from ever touching a healthy or
# break-even position, so it can't be mistaken for general loss-cutting.
MIN_LOSS_PCT_TO_CONSIDER = -2.0

# Minimum time held before a position is eligible -- basic hygiene against
# thrashing a position closed minutes after it was opened.
MIN_HOLD_BEFORE_ELIGIBLE = timedelta(minutes=15)

# Strategies this module knows how to re-verify. Anything else (legacy
# unlabeled trades, the News/LLM-debate strategy) is skipped -- never
# selected for reallocation -- rather than guessing at a re-check for logic
# this module doesn't cover. Known gap, extendable later.
_SUPPORTED_STRATEGIES = ("PRE_EVENT_EXPECTATION_GAP", "DIRECT_NEWS")


async def _thesis_still_valid(pos: OpenPosition, session: AsyncSession) -> bool | None:
    """Re-run pos's OWN original strategy logic against current data.

    Returns True (still endorsed), False (no longer endorsed -- eligible for
    reallocation), or None (couldn't verify -- treat as still valid; this is
    an opportunistic mechanism, not a safety gate, so it fails toward
    leaving the position alone rather than force-closing on missing data).
    """
    strategy = pos.trade.strategy_name if pos.trade else None

    if strategy == "PRE_EVENT_EXPECTATION_GAP":
        try:
            from engine.pre_event_expectation_gap.engine import scan
            predictions = await scan(
                session, universe=[pos.symbol], as_of=datetime.utcnow(),
                min_days_until=0, max_days_until=15,
            )
        except Exception as exc:
            logger.debug(f"[reallocation] {pos.symbol}: PRE_EVENT re-check failed: {exc}")
            return None
        if not predictions:
            # Event no longer discoverable (already happened, or a data
            # gap) -- can't re-verify, don't guess.
            return None
        from engine.pre_event_expectation_gap.types import PreEventDecision
        return predictions[0].decision == PreEventDecision.LONG

    if strategy == "DIRECT_NEWS":
        try:
            from crawler.market_snapshot import get_market_snapshot
            from engine.entry_confirmation import check_price_volume_confirmation
            snap = await get_market_snapshot(pos.symbol)
            confirmed, _reason = check_price_volume_confirmation(snap, pos.direction.value)
        except Exception as exc:
            logger.debug(f"[reallocation] {pos.symbol}: DIRECT_NEWS re-check failed: {exc}")
            return None
        return confirmed

    return None  # unsupported strategy -- never selected


async def try_reallocate_for_candidate(
    open_positions: list[OpenPosition], session: AsyncSession,
) -> bool:
    """Called only from inside a capital-blocked rejection path in
    risk_manager.validate_signal. Looks for the single worst-performing
    open position whose own strategy no longer endorses it, and closes it
    to free capital. Returns True if a position was closed (caller should
    recompute its capital figures and retry the gate once), False otherwise
    (nothing touched, candidate stays blocked).
    """
    now = datetime.utcnow()
    # Cheap filter first, on columns already present on the passed-in
    # OpenPosition rows (no relationship access yet -- callers of
    # validate_signal don't eager-load .trade, so touching it here would
    # trigger a lazy-load that fails in async context).
    loss_candidates = [
        p for p in open_positions
        if (p.unrealised_pct or 0.0) <= MIN_LOSS_PCT_TO_CONSIDER
        and p.opened_at
        and (now - p.opened_at) >= MIN_HOLD_BEFORE_ELIGIBLE
    ]
    if not loss_candidates:
        return False

    # Re-fetch just these candidates with .trade eager-loaded so
    # strategy_name is safely accessible.
    ids = [p.id for p in loss_candidates]
    rows = (await session.execute(
        select(OpenPosition).options(selectinload(OpenPosition.trade)).where(OpenPosition.id.in_(ids))
    )).scalars().all()
    candidates = [p for p in rows if p.trade and p.trade.strategy_name in _SUPPORTED_STRATEGIES]
    if not candidates:
        return False

    # Worst first -- if the worst-performing eligible position still passes
    # its own thesis re-check, don't keep digging for a "less bad" one to
    # sacrifice; nothing here is failing its own strategy, so nothing should
    # be sold.
    candidates.sort(key=lambda p: p.unrealised_pct or 0.0)

    for pos in candidates:
        still_valid = await _thesis_still_valid(pos, session)
        if still_valid is False:
            try:
                from paper_trading.trade_simulator import close_paper_trade
                price = pos.current_price or pos.entry_price
                async with session.begin_nested():
                    closed_trade = await close_paper_trade(pos, price, "REALLOCATED", session)
                logger.warning(
                    f"[reallocation] {pos.symbol}: own strategy ({pos.trade.strategy_name}) "
                    f"no longer endorses this position (unrealised {pos.unrealised_pct:+.2f}%) "
                    f"— closed @ ₹{price:.2f} to free capital for a blocked candidate"
                )
                _ = closed_trade
                return True
            except Exception as exc:
                logger.warning(f"[reallocation] {pos.symbol}: close failed: {exc}")
                return False
        # still_valid is True or None (unverifiable) -- leave this one
        # alone, try the next-worst candidate.

    return False
