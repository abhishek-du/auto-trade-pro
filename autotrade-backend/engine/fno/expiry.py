"""F&O expiry management — settle paper positions at expiry.

On (or after) expiry day, any open F&O paper position is cash-settled at its
intrinsic value (options) or the underlying spot (futures), then closed. This
prevents broker-style forced square-off and stale positions riding past expiry.

Run daily after market close via the `fno_expiry_sweep` Celery task.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OpenPosition, PaperTrade, Candle, TradeDirection, TradeStatus
from utils.logger import logger

_INDEX_CANDLE = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
_FNO_TYPES = ("CE", "PE", "FUTURE")


async def _spot_for(underlying: str, session: AsyncSession) -> float | None:
    """Latest spot for an underlying — index candle, else the .NS equity candle."""
    csym = _INDEX_CANDLE.get((underlying or "").upper(), f"{underlying}.NS")
    for tf in ("1d", "1h"):
        row = (await session.execute(
            select(Candle.close).where(Candle.symbol == csym, Candle.timeframe == tf)
            .order_by(Candle.timestamp.desc()).limit(1)
        )).scalar_one_or_none()
        if row:
            return float(row)
    return None


def _settlement_price(pos: OpenPosition, spot: float) -> float:
    """Cash-settlement price: option intrinsic value, or futures = spot."""
    if pos.instrument_type == "CE":
        return max(0.0, spot - float(pos.strike_price or 0.0))
    if pos.instrument_type == "PE":
        return max(0.0, float(pos.strike_price or 0.0) - spot)
    return spot  # FUTURE


def _realised_pnl(pos: OpenPosition, settle: float) -> float:
    """Realised P&L at settlement. Long options + long futures: settle − entry."""
    if pos.direction == TradeDirection.SELL:
        return (pos.entry_price - settle) * pos.size_units
    return (settle - pos.entry_price) * pos.size_units


async def settle_expired_positions(session: AsyncSession) -> list[dict]:
    """Settle + close every F&O position at/after its expiry. Returns summaries."""
    from paper_trading.virtual_wallet import VirtualWallet

    today = date.today()
    expired = (await session.execute(
        select(OpenPosition).where(
            OpenPosition.instrument_type.in_(_FNO_TYPES),
            OpenPosition.expiry_date != None,
            OpenPosition.expiry_date <= today,
        )
    )).scalars().all()

    settled: list[dict] = []
    for pos in expired:
        spot = await _spot_for(pos.underlying_symbol, session)
        if spot is None:
            logger.warning(f"[fno/expiry] no spot for {pos.underlying_symbol} — skipping {pos.symbol}")
            continue

        settle = round(_settlement_price(pos, spot), 2)
        pnl = round(_realised_pnl(pos, settle), 2)
        pct = round((pnl / pos.margin_blocked * 100) if pos.margin_blocked else 0.0, 2)

        # Close the PaperTrade
        trade = (await session.execute(
            select(PaperTrade).where(PaperTrade.id == pos.trade_id)
        )).scalar_one_or_none()
        if trade:
            trade.exit_price  = settle
            trade.exit_ts     = datetime.utcnow() if hasattr(trade, "exit_ts") else None
            trade.closed_at   = datetime.utcnow()
            trade.status      = TradeStatus.CLOSED
            trade.pnl         = pnl
            trade.pnl_percent = pct

        await session.execute(delete(OpenPosition).where(OpenPosition.id == pos.id))
        await VirtualWallet.return_margin(session, float(pos.margin_blocked or 0.0), pnl, pos.symbol)

        settled.append({
            "symbol": pos.symbol, "type": pos.instrument_type,
            "settle": settle, "spot": spot, "pnl": pnl,
        })
        logger.info(
            f"[fno/expiry] SETTLED {pos.symbol} ({pos.instrument_type}) @ ₹{settle} "
            f"(spot ₹{spot:,.0f}) | pnl ₹{pnl:,.0f}"
        )

    if settled:
        await session.commit()
    return settled
