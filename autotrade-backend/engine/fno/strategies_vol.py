"""Volatility / delta-neutral F&O strategies.

Phase 6: the "volatility brain" — trade IV instead of direction.

  - LONG straddle  (buy ATM CE + ATM PE) when IV-Rank is LOW  → expecting a
    volatility expansion / big move either way.
  - SHORT straddle is undefined-risk; in paper mode we approximate it but gate it
    behind ENABLE_FUTURES (treats the short legs with the margin model). Disabled
    by default — long-vol is the safe baseline.
  - Delta-neutral check: aggregate portfolio option delta; flag when it drifts.

Both legs are bought options (defined risk) → margin = total premium debit, so
LONG straddle needs no SPAN. Reuses selection.open_option_paper_trade per leg.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OptionContractSnapshot, OpenPosition
from engine.fno import contracts as _contracts
from engine.fno.selection import (
    OptionTradeSpec, open_option_paper_trade, _latest_premium,
)
from utils.config import settings
from utils.logger import logger

# IV-Rank thresholds for the volatility regime.
_IV_RANK_LOW  = 30.0    # below → options cheap → buy vol (long straddle)
_IV_RANK_HIGH = 70.0    # above → options rich → (short vol, gated)


async def _atm_iv_rank(underlying: str, session: AsyncSession) -> tuple[float, float] | None:
    """Return (atm_iv, iv_rank) for an underlying from the latest snapshot + history."""
    from db.models import IVHistory
    from sqlalchemy import func as _f

    last_at = (await session.execute(
        select(_f.max(OptionContractSnapshot.snapshot_at))
        .where(OptionContractSnapshot.underlying == underlying.upper())
    )).scalar()
    if last_at is None:
        return None
    rows = (await session.execute(
        select(OptionContractSnapshot).where(
            OptionContractSnapshot.underlying == underlying.upper(),
            OptionContractSnapshot.snapshot_at == last_at,
        )
    )).scalars().all()
    if not rows:
        return None
    spot = rows[0].spot or 0.0
    atm = min({r.strike for r in rows}, key=lambda k: abs(k - spot), default=0.0)
    ce_iv = next((r.iv for r in rows if r.strike == atm and r.option_type == "CE" and r.iv), None)
    pe_iv = next((r.iv for r in rows if r.strike == atm and r.option_type == "PE" and r.iv), None)
    ivs = [v for v in (ce_iv, pe_iv) if v]
    if not ivs:
        return None
    atm_iv = sum(ivs) / len(ivs)

    hist = (await session.execute(
        select(IVHistory.atm_iv).where(IVHistory.underlying == underlying.upper())
    )).scalars().all()
    if len(hist) < 5:
        return atm_iv, 50.0
    lo, hi = min(hist), max(hist)
    rank = 100 * (atm_iv - lo) / (hi - lo) if hi > lo else 50.0
    return atm_iv, round(rank, 1)


async def _build_leg(underlying: str, option_type: str, spot: float,
                     equity: float, session: AsyncSession) -> OptionTradeSpec | None:
    """Build one ATM option leg spec (1 lot — straddle legs are paired)."""
    contract = await _contracts.resolve_option(underlying, option_type, spot, session)
    if contract is None:
        return None
    premium = await _latest_premium(underlying, contract.strike, option_type, contract.expiry, session)
    if not premium or premium <= 0:
        return None
    lots = 1
    qty = lots * contract.lot_size
    debit = round(qty * premium, 2)
    return OptionTradeSpec(
        underlying=underlying.upper(), tradingsymbol=contract.tradingsymbol,
        option_type=option_type, strike=contract.strike, expiry=contract.expiry,
        lot_size=contract.lot_size, premium=round(premium, 2), lots=lots, qty=qty,
        notional=debit, stop=round(premium * 0.5, 2), target=round(premium * 2.0, 2),
        dte=contract.dte,
    )


async def open_long_straddle(underlying: str, spot: float, equity: float,
                             session: AsyncSession, *, confidence: float = 0.0) -> dict | None:
    """Buy ATM CE + ATM PE (long volatility). Defined risk = total premium."""
    ce = await _build_leg(underlying, "CE", spot, equity, session)
    pe = await _build_leg(underlying, "PE", spot, equity, session)
    if ce is None or pe is None:
        logger.debug(f"[fno/vol] {underlying}: cannot build both straddle legs")
        return None

    t_ce = await open_option_paper_trade(
        ce, session, confidence=confidence,
        ai_reason=f"📊 LONG STRADDLE leg CE {ce.strike:.0f} | long volatility",
    )
    t_pe = await open_option_paper_trade(
        pe, session, confidence=confidence,
        ai_reason=f"📊 LONG STRADDLE leg PE {pe.strike:.0f} | long volatility",
    )
    legs = [t for t in (t_ce, t_pe) if t]
    if not legs:
        return None
    total_debit = sum(l.size_usd for l in legs)
    logger.info(f"[fno/vol] LONG STRADDLE {underlying} {ce.strike:.0f} | "
                f"{len(legs)} leg(s) | debit ₹{total_debit:,.0f}")
    return {"strategy": "LONG_STRADDLE", "underlying": underlying,
            "strike": ce.strike, "legs": len(legs), "debit": total_debit}


async def portfolio_delta(session: AsyncSession) -> float:
    """Aggregate signed option delta across open option positions (qty-weighted).

    Uses each position's strike snapshot delta × size_units × sign(long=+).
    Useful to detect when a 'neutral' book has drifted directional.
    """
    rows = (await session.execute(
        select(OpenPosition).where(OpenPosition.instrument_type.in_(["CE", "PE"]))
    )).scalars().all()
    total = 0.0
    for pos in rows:
        snap = (await session.execute(
            select(OptionContractSnapshot.delta).where(
                OptionContractSnapshot.underlying == pos.underlying_symbol,
                OptionContractSnapshot.strike == pos.strike_price,
                OptionContractSnapshot.option_type == pos.option_type,
            ).order_by(OptionContractSnapshot.snapshot_at.desc()).limit(1)
        )).scalar_one_or_none()
        if snap is not None:
            total += float(snap) * pos.size_units   # long options: +delta
    return round(total, 2)


async def evaluate_volatility(session: AsyncSession, equity: float) -> list[dict]:
    """Open a long straddle on indices whose IV-Rank is low (cheap options).

    Gated by ENABLE_OPTIONS (long straddle = bought options, defined risk).
    Skips an underlying that already has any open option position.
    """
    if not (settings.ENABLE_FNO and settings.ENABLE_OPTIONS):
        return []

    open_unders = set((await session.execute(
        select(OpenPosition.underlying_symbol).where(
            OpenPosition.instrument_type.in_(["CE", "PE"])
        )
    )).scalars().all())

    opened: list[dict] = []
    for under in settings.fno_index_symbols:
        try:
            if under in open_unders:
                continue
            ivr = await _atm_iv_rank(under, session)
            if ivr is None:
                continue
            atm_iv, rank = ivr
            if rank > _IV_RANK_LOW:        # only buy vol when it's cheap
                continue
            # Spot from the latest snapshot.
            spot = (await session.execute(
                select(OptionContractSnapshot.spot).where(
                    OptionContractSnapshot.underlying == under.upper()
                ).order_by(OptionContractSnapshot.snapshot_at.desc()).limit(1)
            )).scalar_one_or_none()
            if not spot:
                continue
            res = await open_long_straddle(under, float(spot), equity, session,
                                           confidence=round(100 - rank, 1))
            if res:
                res["iv_rank"] = rank
                opened.append(res)
        except Exception as exc:
            logger.warning(f"[fno/vol] {under} failed: {exc}")

    return opened
