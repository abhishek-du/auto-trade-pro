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


# ─────────────────────────────────────────────────────────────────────────────
# Exit management (2026-08-21)
# ─────────────────────────────────────────────────────────────────────────────
#
# THE GAP THIS CLOSES
# -------------------
# After the T1 50% scale-out, fast_sl_check sets `take_profit = 0.0` and moves
# the stop to breakeven, with a comment deferring to "trailing logic in
# update_positions_with_current_prices". That trailing logic does not exist --
# grepped, there is exactly one stop_loss reassignment in the whole exit path
# and it is the breakeven line itself. So the runner had NO upside management:
# it could only end at breakeven or at the 15:10 squareoff.


def update_trailing_stop(pos, current_price: float, atr: float,
                         ratchet: bool = True) -> tuple[bool, str | None]:
    """Ratchet the stop up behind the peak. Returns (changed, note).

    Two stages, in order:
      1. Once the trade is +TRAILING_BREAKEVEN_TRIGGER_PCT, the stop moves to
         entry. Risk on the position becomes zero.
      2. Thereafter the stop trails at TRAILING_STOP_ATR_MULT ATR below the
         highest high seen SINCE ENTRY (a chandelier exit).

    The stop only ever moves in the favourable direction -- `max()` for a long,
    `min()` for a short. A trailing stop that can loosen is not a stop.

    Mutates `pos` but does not commit; the caller owns the transaction.

    `ratchet=False` tracks the extreme (highest_high / lowest_low) but leaves
    pos.stop_loss alone. V2 needs this: the ratchet and the hard stop share one
    column, so a stop moved to breakeven at +2% IS a profit-management exit
    wearing a STOP_LOSS label, and deferring the profit-management layer means
    deferring the ratchet with it. The peak keeps being tracked throughout, so
    when the minimum hold ends the chandelier applies from the true peak rather
    than restarting from wherever price happens to be. See engine/exit_policy.py.
    """
    from utils.config import settings

    if not bool(getattr(settings, "ENABLE_TRAILING_STOP", True)):
        return False, None
    if current_price <= 0 or pos.entry_price <= 0:
        return False, None

    is_long = str(getattr(pos.direction, "value", pos.direction)).upper() == "BUY"
    note = None

    # Track the extreme. Seeded from entry so a position opened before this
    # column existed does not trail from a NULL.
    if is_long:
        peak = max(float(pos.highest_high or pos.entry_price), current_price)
        pos.highest_high = peak
    else:
        peak = min(float(pos.lowest_low or pos.entry_price), current_price)
        pos.lowest_low = peak

    gain_pct = ((current_price / pos.entry_price - 1.0) * 100.0) if is_long else \
               ((pos.entry_price / current_price - 1.0) * 100.0)

    old_sl = float(pos.stop_loss or 0.0)
    new_sl = old_sl

    # Stage 1 — breakeven.
    trigger = float(getattr(settings, "TRAILING_BREAKEVEN_TRIGGER_PCT", 2.0))
    if gain_pct >= trigger:
        new_sl = max(new_sl, pos.entry_price) if is_long else min(new_sl, pos.entry_price)
        if new_sl != old_sl:
            note = f"breakeven at +{gain_pct:.1f}%"

    # Stage 2 — chandelier, and ONLY once the position has actually earned it.
    #
    # Gating this on peak gain is not cosmetic. Without it the chandelier applies
    # to flat and losing positions too, converting the original wide stop into a
    # tight one: a dry run on the live book (2026-08-21) moved CEIGALL's stop
    # from 285.81 to 314.72 while the position was DOWN 0.04% — 1% under the
    # live price, so any ordinary pullback would have stopped it out. A trailing
    # stop is for protecting profit, not for tightening a thesis that has not
    # worked yet.
    peak_gain_pct = ((peak / pos.entry_price - 1.0) * 100.0) if is_long else \
                    ((pos.entry_price / peak - 1.0) * 100.0)
    mult = float(getattr(settings, "TRAILING_STOP_ATR_MULT", 2.5))
    if atr and atr > 0 and peak_gain_pct >= trigger:
        chandelier = peak - mult * atr if is_long else peak + mult * atr
        cand = max(new_sl, chandelier) if is_long else min(new_sl, chandelier)
        # Never trail past the current price -- that would stop out instantly.
        if is_long and cand < current_price and cand > new_sl:
            new_sl, note = cand, f"trailed to {cand:.2f} (peak {peak:.2f})"
        elif (not is_long) and cand > current_price and cand < new_sl:
            new_sl, note = cand, f"trailed to {cand:.2f} (trough {peak:.2f})"

    if not ratchet:
        # Extreme tracked above; the stop is deliberately left where it is.
        return False, None

    if new_sl != old_sl:
        pos.stop_loss = new_sl
        return True, note
    return False, None


def check_time_exit(pos, now_ist) -> bool:
    """True when an intraday position must be flattened before the close.

    NOTE: a scheduled squareoff already exists (`_intraday_squareoff_task`,
    15:10 IST). This is a per-tick backstop for the 5s loop so a position is not
    left open if that task is starved on the worker -- which has happened.
    """
    from utils.config import settings

    if not bool(getattr(settings, "TIME_BASED_EXIT_ENABLED", True)):
        return False
    if str(getattr(pos, "product", "") or "").upper() != "MIS":
        return False
    hour = int(getattr(settings, "TIME_BASED_EXIT_HOUR", 15))
    minute = int(getattr(settings, "TIME_BASED_EXIT_MINUTE", 10))
    return (now_ist.hour, now_ist.minute) >= (hour, minute)
