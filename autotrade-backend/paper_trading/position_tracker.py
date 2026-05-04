"""Tracks all open and closed virtual paper-trade positions.

Single source of truth for what is 'in the book' at any moment.
All positions are VIRTUAL — no real money is involved.
"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OpenPosition, PaperTrade, TradeDirection, TradeStatus
from paper_trading.pnl_calculator import PnLCalculator
from paper_trading.simulation_logger import SimulationLogger
from paper_trading.trade_simulator import FillResult
from paper_trading.virtual_wallet import VirtualWallet
from utils.logger import logger


class PositionTracker:
    """Stateless position lifecycle manager — all methods are static."""

    # ── Open ──────────────────────────────────────────────────────────────────

    @staticmethod
    async def open_position(
        session: AsyncSession,
        fill: FillResult,
        stop_loss: float,
        take_profit: float,
        signal_confidence: float = 0.0,
        pattern_name: str = "",
        ai_reason: str = "",
        indicator_snapshot: dict | None = None,
        news_sentiment_score: float = 0.0,
    ) -> PaperTrade | None:
        """Reserve margin, persist PaperTrade + OpenPosition.

        Returns the new PaperTrade, or None if margin was refused.
        """
        ok, msg = await VirtualWallet.deduct_margin(session, fill.size_usd, fill.symbol)
        if not ok:
            await SimulationLogger.log(
                session, "MARGIN_REFUSED", fill.symbol,
                f"Cannot open {fill.direction} {fill.symbol}: {msg}",
                {"requested_usd": fill.size_usd, "reason": msg},
            )
            return None

        trade = PaperTrade(
            symbol=fill.symbol,
            direction=TradeDirection(fill.direction),
            status=TradeStatus.OPEN,
            entry_price=fill.fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            size_units=fill.size_units,
            size_usd=fill.size_usd,
            signal_confidence=signal_confidence,
            pattern_name=pattern_name,
            ai_reason=ai_reason,
            indicator_snapshot=indicator_snapshot or {},
            news_sentiment_score=news_sentiment_score,
            slippage_applied=fill.slippage_usd,
            opened_at=fill.executed_at,
        )
        session.add(trade)
        await session.flush()  # populate trade.id

        position = OpenPosition(
            symbol=fill.symbol,
            direction=TradeDirection(fill.direction),
            entry_price=fill.fill_price,
            current_price=fill.fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            size_units=fill.size_units,
            size_usd=fill.size_usd,
            unrealised_pnl=0.0,
            unrealised_pct=0.0,
            trade_id=trade.id,
            opened_at=fill.executed_at,
        )
        session.add(position)
        await session.flush()

        logger.info(
            f"POSITION OPENED  #{trade.id}  {fill.direction} {fill.symbol} "
            f"@ {fill.fill_price:.4f}  units={fill.size_units:.4f}  "
            f"size=${fill.size_usd:.2f}  SL={stop_loss:.4f}  TP={take_profit:.4f}"
        )
        await SimulationLogger.log(
            session, "TRADE_OPENED", fill.symbol,
            f"Opened {fill.direction} {fill.symbol} @ {fill.fill_price:.4f}",
            {
                "trade_id":    trade.id,
                "direction":   fill.direction,
                "entry_price": fill.fill_price,
                "size_units":  fill.size_units,
                "size_usd":    fill.size_usd,
                "stop_loss":   stop_loss,
                "take_profit": take_profit,
                "slippage":    fill.slippage_usd,
            },
        )
        return trade

    # ── Close ─────────────────────────────────────────────────────────────────

    @staticmethod
    async def close_position(
        session: AsyncSession,
        trade_id: int,
        fill: FillResult,
        reason: str = "MANUAL",
    ) -> PaperTrade | None:
        """Close an open position at the given fill price.

        Updates PaperTrade, deletes OpenPosition, returns margin + PnL to wallet.
        Returns the updated PaperTrade, or None if trade_id not found / already closed.
        """
        trade_row = await session.execute(
            select(PaperTrade).where(
                PaperTrade.id == trade_id,
                PaperTrade.status == TradeStatus.OPEN,
            )
        )
        trade = trade_row.scalar_one_or_none()
        if trade is None:
            logger.warning(f"close_position: trade #{trade_id} not found or already closed")
            return None

        pnl     = PnLCalculator.realised_for_close(trade, fill.fill_price)
        pnl_pct = PnLCalculator.realised_pct_for_close(trade, fill.fill_price)

        trade.status      = TradeStatus.CLOSED
        trade.exit_price  = fill.fill_price
        trade.pnl         = pnl
        trade.pnl_percent = pnl_pct
        trade.closed_at   = fill.executed_at

        # Remove the live snapshot
        await session.execute(
            delete(OpenPosition).where(OpenPosition.trade_id == trade_id)
        )
        await session.flush()

        # Return margin + PnL to wallet
        new_balance = await VirtualWallet.return_margin(
            session, trade.size_usd, pnl, trade.symbol
        )

        sign = "+" if pnl >= 0 else ""
        logger.info(
            f"POSITION CLOSED  #{trade_id}  {trade.symbol} "
            f"@ {fill.fill_price:.4f}  PnL={sign}${pnl:.2f} ({sign}{pnl_pct:.2f}%)  "
            f"reason={reason}  new_balance=${new_balance:,.2f}"
        )
        await SimulationLogger.log(
            session, "TRADE_CLOSED", trade.symbol,
            f"Closed {trade.symbol} @ {fill.fill_price:.4f} — PnL {sign}${pnl:.2f} ({reason})",
            {
                "trade_id":    trade_id,
                "exit_price":  fill.fill_price,
                "pnl":         pnl,
                "pnl_pct":     pnl_pct,
                "reason":      reason,
                "new_balance": new_balance,
            },
        )
        return trade

    # ── Stop-loss / Take-profit checker ───────────────────────────────────────

    @staticmethod
    async def check_sl_tp(
        session: AsyncSession,
        current_prices: dict[str, float],
    ) -> list[PaperTrade]:
        """Scan all open positions and close any that hit SL or TP.

        Returns the list of trades that were closed this cycle.
        """
        from paper_trading.trade_simulator import TradeSimulator

        open_result = await session.execute(select(OpenPosition))
        positions = list(open_result.scalars().all())
        closed: list[PaperTrade] = []

        for pos in positions:
            price = current_prices.get(pos.symbol)
            if price is None:
                continue

            hit_sl = (
                pos.direction == TradeDirection.BUY  and price <= pos.stop_loss
                or pos.direction == TradeDirection.SELL and price >= pos.stop_loss
            )
            hit_tp = (
                pos.direction == TradeDirection.BUY  and price >= pos.take_profit
                or pos.direction == TradeDirection.SELL and price <= pos.take_profit
            )

            if not (hit_sl or hit_tp):
                # Update unrealised PnL snapshot
                upnl = PnLCalculator.unrealised_for_position(pos, price)
                upct = PnLCalculator.unrealised_pct_for_position(pos, price)
                pos.current_price  = price
                pos.unrealised_pnl = upnl
                pos.unrealised_pct = upct
                continue

            reason = "STOP_LOSS" if hit_sl else "TAKE_PROFIT"
            fill = TradeSimulator.execute_sell(pos.symbol, price, pos.size_units) \
                if pos.direction == TradeDirection.BUY \
                else TradeSimulator.execute_buy(pos.symbol, price, pos.size_units)

            trade_row = await session.execute(
                select(PaperTrade).where(PaperTrade.id == pos.trade_id)
            )
            trade = trade_row.scalar_one_or_none()
            if trade is None:
                continue

            pnl     = PnLCalculator.realised_for_close(trade, fill.fill_price)
            pnl_pct = PnLCalculator.realised_pct_for_close(trade, fill.fill_price)
            status  = TradeStatus.STOPPED if hit_sl else TradeStatus.CLOSED

            trade.status      = status
            trade.exit_price  = fill.fill_price
            trade.pnl         = pnl
            trade.pnl_percent = pnl_pct
            trade.closed_at   = fill.executed_at

            await session.execute(
                delete(OpenPosition).where(OpenPosition.trade_id == pos.trade_id)
            )
            await session.flush()

            new_balance = await VirtualWallet.return_margin(
                session, trade.size_usd, pnl, trade.symbol
            )
            sign = "+" if pnl >= 0 else ""
            logger.info(
                f"{reason}  #{trade.id}  {trade.symbol} "
                f"@ {fill.fill_price:.4f}  PnL={sign}${pnl:.2f}  "
                f"new_balance=${new_balance:,.2f}"
            )
            await SimulationLogger.log(
                session, reason, trade.symbol,
                f"{reason} hit for {trade.symbol} @ {fill.fill_price:.4f} — PnL {sign}${pnl:.2f}",
                {"trade_id": trade.id, "exit_price": fill.fill_price,
                 "pnl": pnl, "new_balance": new_balance},
            )
            closed.append(trade)

        await session.flush()

        # Sync total unrealised PnL into wallet
        total_unrealised = sum(
            p.unrealised_pnl
            for p in (
                await session.execute(select(OpenPosition))
            ).scalars().all()
        )
        await VirtualWallet.update_unrealised_pnl(session, total_unrealised)

        return closed

    # ── Queries ───────────────────────────────────────────────────────────────

    @staticmethod
    async def get_open_positions(session: AsyncSession) -> list[OpenPosition]:
        result = await session.execute(select(OpenPosition))
        return list(result.scalars().all())

    @staticmethod
    async def get_trade(session: AsyncSession, trade_id: int) -> PaperTrade | None:
        result = await session.execute(
            select(PaperTrade).where(PaperTrade.id == trade_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def count_open(session: AsyncSession) -> int:
        result = await session.execute(select(OpenPosition))
        return len(result.scalars().all())

    @staticmethod
    async def is_already_open(session: AsyncSession, symbol: str) -> bool:
        """Return True if a position for this symbol is currently open."""
        result = await session.execute(
            select(OpenPosition.id).where(OpenPosition.symbol == symbol).limit(1)
        )
        return result.scalar_one_or_none() is not None
