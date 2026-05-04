"""PnL calculations for virtual paper-trade positions.

All figures are FAKE — no real monetary value is implied.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OpenPosition, PaperTrade, TradeDirection, TradeStatus


@dataclass
class PnLSummary:
    realised_pnl:  float   # sum of all closed/stopped trades
    unrealised_pnl: float  # mark-to-market value of open positions
    total_pnl:     float
    win_count:     int
    loss_count:    int
    win_rate:      float   # 0.0 – 1.0
    avg_win:       float
    avg_loss:      float
    largest_win:   float
    largest_loss:  float


class PnLCalculator:
    """Stateless PnL helpers — all methods are static."""

    # ── Per-position calculations ─────────────────────────────────────────────

    @staticmethod
    def unrealised_for_position(pos: OpenPosition, current_price: float) -> float:
        """Mark-to-market PnL for one open position."""
        if pos.direction == TradeDirection.BUY:
            return (current_price - pos.entry_price) * pos.size_units
        return (pos.entry_price - current_price) * pos.size_units

    @staticmethod
    def unrealised_pct_for_position(pos: OpenPosition, current_price: float) -> float:
        """Unrealised PnL as a percentage of the position's cost basis."""
        if pos.size_usd == 0:
            return 0.0
        pnl = PnLCalculator.unrealised_for_position(pos, current_price)
        return round(pnl / pos.size_usd * 100, 4)

    @staticmethod
    def realised_for_close(trade: PaperTrade, exit_fill_price: float) -> float:
        """Realised PnL that will be booked when this trade is closed.

        Does NOT write to the DB — that is PositionTracker.close_position()'s job.
        """
        if trade.direction == TradeDirection.BUY:
            gross = (exit_fill_price - trade.entry_price) * trade.size_units
        else:
            gross = (trade.entry_price - exit_fill_price) * trade.size_units
        return round(gross, 4)

    @staticmethod
    def realised_pct_for_close(trade: PaperTrade, exit_fill_price: float) -> float:
        """Realised PnL as a percentage of the trade's entry cost."""
        if trade.size_usd == 0:
            return 0.0
        pnl = PnLCalculator.realised_for_close(trade, exit_fill_price)
        return round(pnl / trade.size_usd * 100, 4)

    # ── Portfolio aggregation ─────────────────────────────────────────────────

    @staticmethod
    async def portfolio_summary(
        session: AsyncSession,
        current_prices: dict[str, float],
    ) -> PnLSummary:
        """Aggregate PnL across all virtual positions.

        current_prices: {symbol: latest_price} for open positions.
        Falls back to last known current_price if a symbol is missing.
        """
        # Total realised PnL from completed trades
        realised_result = await session.execute(
            select(func.sum(PaperTrade.pnl)).where(
                PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
                PaperTrade.pnl.isnot(None),
            )
        )
        realised = float(realised_result.scalar() or 0.0)

        # Live unrealised PnL
        open_result = await session.execute(select(OpenPosition))
        positions = list(open_result.scalars().all())
        unrealised = sum(
            PnLCalculator.unrealised_for_position(
                p, current_prices.get(p.symbol, p.current_price)
            )
            for p in positions
        )

        # Per-trade win / loss distribution
        pnl_rows = await session.execute(
            select(PaperTrade.pnl).where(
                PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
                PaperTrade.pnl.isnot(None),
            )
        )
        pnls = [float(r) for r in pnl_rows.scalars().all()]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        return PnLSummary(
            realised_pnl   = round(realised, 2),
            unrealised_pnl = round(unrealised, 2),
            total_pnl      = round(realised + unrealised, 2),
            win_count      = len(wins),
            loss_count     = len(losses),
            win_rate       = round(len(wins) / len(pnls), 4) if pnls else 0.0,
            avg_win        = round(sum(wins)   / len(wins),   2) if wins   else 0.0,
            avg_loss       = round(sum(losses) / len(losses), 2) if losses else 0.0,
            largest_win    = round(max(wins),  2) if wins   else 0.0,
            largest_loss   = round(min(losses), 2) if losses else 0.0,
        )
