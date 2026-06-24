"""Virtual wallet engine for AutoTrade Pro paper trading.

All monetary values are FAKE / VIRTUAL currency.
No real money is ever involved at any stage.
"""

from datetime import date, datetime

from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    OpenPosition,
    PaperTrade,
    PerformanceSnapshot,
    SimulationLog,
    TradeStatus,
    VirtualWallet as WalletRow,
)
from utils.config import settings
from utils.logger import logger
from utils.runtime_config import RuntimeConfig

# Every wallet event is logged in this fixed-width format for easy grepping.
_FMT = (
    "WALLET │ {event:<18} │ Symbol: {symbol:<6} │ "
    "Amount: ${amount:>10,.2f} │ Balance: ${balance:>10,.2f}"
)


class VirtualWallet:
    """All paper-trading cash operations.

    Every method is a @staticmethod — pass the AsyncSession from FastAPI's
    get_db() dependency.  Methods call session.flush() but never session.commit();
    the outer request handler or Celery task owns the transaction boundary.
    """

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    async def _fetch(session: AsyncSession) -> WalletRow | None:
        result = await session.execute(select(WalletRow).limit(1))
        return result.scalar_one_or_none()

    @staticmethod
    def _log(event: str, symbol: str, amount: float, balance: float, extra: str = "") -> None:
        line = _FMT.format(event=event, symbol=symbol, amount=amount, balance=balance)
        if extra:
            line += f" │ {extra}"
        logger.info(line)

    @staticmethod
    async def _simlog(
        session: AsyncSession,
        event_type: str,
        symbol: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        session.add(SimulationLog(
            event_type=event_type,
            symbol=symbol,
            message=message,
            data=data or {},
        ))
        await session.flush()

    @staticmethod
    async def _start_balance(session: AsyncSession) -> float:
        """Return the configured paper-trading starting balance from RuntimeSettings.

        Falls back to the .env default if the key has not been set in the DB.
        """
        cfg = await RuntimeConfig.load(session)
        return cfg.paper_trading_balance

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    async def get_or_create(session: AsyncSession) -> WalletRow:
        """Return the wallet row, creating it with the configured starting balance if absent.

        Also seeds today's PerformanceSnapshot so the equity curve always has
        a row for the current day even before the first trade closes.
        """
        wallet = await VirtualWallet._fetch(session)

        if wallet is None:
            start = await VirtualWallet._start_balance(session)
            wallet = WalletRow(
                balance=start,
                equity=start,
                peak_balance=start,
                realised_pnl=0.0,
                unrealised_pnl=0.0,
                total_trades=0,
                winning_trades=0,
                max_drawdown=0.0,
            )
            session.add(wallet)
            await session.flush()
            VirtualWallet._log("WALLET_CREATED", "—", start, wallet.balance)
            await VirtualWallet._simlog(
                session, "WALLET_CREATED", "—",
                f"Paper-trading wallet initialised with ${start:,.2f} virtual balance",
                {"starting_balance": start},
            )

        # Seed today's snapshot row — DO NOTHING if row already exists.
        today = date.today()
        await session.execute(
            pg_insert(PerformanceSnapshot)
            .values(
                date=today,
                balance=wallet.balance,
                equity=wallet.equity,
                daily_pnl=0.0,
                trades_today=0,
                win_rate_today=0.0,
            )
            .on_conflict_do_nothing(constraint="uq_perf_date")
        )
        await session.flush()

        return wallet

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def deduct_margin(
        session: AsyncSession, amount: float, symbol: str
    ) -> tuple[bool, str]:
        """Deduct virtual margin when opening a position.

        Returns (True, 'OK') on success.
        Returns (False, reason_string) when balance is insufficient or amount is invalid.
        """
        wallet = await VirtualWallet.get_or_create(session)

        if amount <= 0:
            return False, f"Invalid amount: {amount:.2f}"

        if wallet.balance < amount:
            shortfall = amount - wallet.balance
            msg = (
                f"Insufficient virtual funds — need ${amount:,.2f}, "
                f"have ${wallet.balance:,.2f} (short ${shortfall:,.2f})"
            )
            VirtualWallet._log("MARGIN_REJECTED", symbol, amount, wallet.balance)
            return False, msg

        wallet.balance -= amount
        start = await VirtualWallet._start_balance(session)
        wallet.equity = start + wallet.realised_pnl + wallet.unrealised_pnl
        await session.flush()

        VirtualWallet._log("MARGIN_DEDUCTED", symbol, amount, wallet.balance)
        await VirtualWallet._simlog(
            session, "MARGIN_DEDUCTED", symbol,
            f"Opened position in {symbol} — deducted ${amount:,.2f} virtual margin",
            {"amount": amount, "symbol": symbol, "remaining_balance": wallet.balance},
        )
        return True, "OK"

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def return_margin(
        session: AsyncSession, amount: float, pnl: float, symbol: str
    ) -> float:
        """Return virtual margin + PnL to balance on position close.

        Updates: balance, equity, realised_pnl, total_trades, winning_trades,
                 peak_balance, max_drawdown.
        Returns the new balance.
        """
        wallet = await VirtualWallet.get_or_create(session)

        returned = amount + pnl
        wallet.balance      += returned
        wallet.realised_pnl += pnl
        wallet.total_trades += 1

        if pnl > 0:
            wallet.winning_trades += 1

        # Update high-water mark
        if wallet.balance > wallet.peak_balance:
            wallet.peak_balance = wallet.balance

        # Recalculate max drawdown
        if wallet.peak_balance > 0:
            current_dd = (wallet.peak_balance - wallet.balance) / wallet.peak_balance * 100
            if current_dd > wallet.max_drawdown:
                wallet.max_drawdown = current_dd

        start = await VirtualWallet._start_balance(session)
        wallet.equity = start + wallet.realised_pnl + wallet.unrealised_pnl
        await session.flush()

        sign = "+" if pnl >= 0 else ""
        VirtualWallet._log(
            "POSITION_CLOSED", symbol, returned, wallet.balance,
            f"PnL: {sign}${pnl:,.2f} ({sign}{pnl / amount * 100:.2f}%)" if amount else f"PnL: {sign}${pnl:,.2f}",
        )
        await VirtualWallet._simlog(
            session, "POSITION_CLOSED", symbol,
            f"Closed {symbol} — PnL {sign}${pnl:,.2f}",
            {
                "pnl":            round(pnl, 4),
                "symbol":         symbol,
                "new_balance":    round(wallet.balance, 4),
                "total_trades":   wallet.total_trades,
                "winning_trades": wallet.winning_trades,
            },
        )
        return wallet.balance

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def update_unrealised_pnl(
        session: AsyncSession, total_unrealised: float
    ) -> None:
        """Refresh unrealised PnL and recompute equity.

        Full-equity model: each trade commits its full purchase value (qty × price).
        Equity = cash remaining + capital locked in positions + unrealised PnL.
        Capital locked = starting_balance − balance (what was actually deducted).
        """
        wallet = await VirtualWallet.get_or_create(session)
        start  = await VirtualWallet._start_balance(session)
        wallet.unrealised_pnl = total_unrealised
        wallet.equity = start + wallet.realised_pnl + total_unrealised
        await session.flush()

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def get_summary(session: AsyncSession) -> dict:
        """Return the full wallet state as a JSON-serialisable dict."""
        wallet = await VirtualWallet.get_or_create(session)
        start  = await VirtualWallet._start_balance(session)

        win_rate = (
            wallet.winning_trades / wallet.total_trades * 100
            if wallet.total_trades > 0 else 0.0
        )

        # Derive realised P&L from paper_trades directly (same source as agent/status)
        # so the header, trades page, and agent status all agree. Reading from
        # wallet.realised_pnl can drift when a partial close or corporate-action
        # phantom triggers a wallet debit without a matching closed trade record.
        from sqlalchemy import func as sqlfunc
        from db.models import PaperTrade
        realised_row = (await session.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(PaperTrade.pnl), 0.0))
            .where(PaperTrade.exit_price != None, PaperTrade.pnl != None)
        )).scalar()
        realised = float(realised_row or 0.0)

        # Compute unrealised P&L LIVE from open positions — the SAME compute_live_pnl
        # path the /positions page uses — so the navbar summary matches it instead of
        # lagging on the last periodic mark-to-market snapshot.
        try:
            from paper_trading.position_tracker import PositionTracker
            from paper_trading.trade_simulator import compute_live_pnl
            positions = await PositionTracker.get_open_positions(session)
            live = await compute_live_pnl(positions, session)
            unrealised = sum(
                float(live.get(p.id, (None, p.unrealised_pnl, None))[1] or 0.0)
                for p in positions
            )
        except Exception:
            unrealised = wallet.unrealised_pnl  # fall back to last stored snapshot

        equity = start + realised + unrealised
        roi = (equity - start) / start * 100 if start else 0.0

        return {
            "balance":        round(wallet.balance, 2),
            "equity":         round(equity, 2),
            "realised_pnl":   round(realised, 2),
            "unrealised_pnl": round(unrealised, 2),
            "total_trades":   wallet.total_trades,
            "winning_trades": wallet.winning_trades,
            "win_rate":       round(win_rate, 2),
            "max_drawdown":   round(wallet.max_drawdown, 2),
            "peak_balance":   round(wallet.peak_balance, 2),
            "roi_percent":    round(roi, 2),
            "mode":           "PAPER_TRADING — VIRTUAL CURRENCY ONLY",
        }

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def take_daily_snapshot(session: AsyncSession) -> None:
        """Upsert today's PerformanceSnapshot row.

        Called by Celery beat once per day (and on-demand after any trade close).
        Multiple calls on the same day safely overwrite the row with the latest state.
        """
        wallet = await VirtualWallet.get_or_create(session)
        today = date.today()

        # Count trades closed today
        closed_q = select(func.count(PaperTrade.id)).where(
            and_(
                func.date(PaperTrade.closed_at) == today,
                PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
            )
        )
        closed_today: int = (await session.execute(closed_q)).scalar_one()

        winning_q = select(func.count(PaperTrade.id)).where(
            and_(
                func.date(PaperTrade.closed_at) == today,
                PaperTrade.status.in_([TradeStatus.CLOSED, TradeStatus.STOPPED]),
                PaperTrade.pnl > 0,
            )
        )
        winning_today: int = (await session.execute(winning_q)).scalar_one()

        win_rate_today = winning_today / closed_today * 100 if closed_today > 0 else 0.0

        # Daily PnL vs prior day's closing equity (falls back to starting balance)
        prev_snap = (
            await session.execute(
                select(PerformanceSnapshot)
                .where(PerformanceSnapshot.date < today)
                .order_by(PerformanceSnapshot.date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        start = await VirtualWallet._start_balance(session)
        prev_equity = prev_snap.equity if prev_snap else start
        daily_pnl = wallet.equity - prev_equity

        # Atomic upsert — avoids race condition when multiple workers call
        # take_daily_snapshot on the same day (two concurrent SELECTs both
        # return None, then both INSERT → unique-constraint violation).
        stmt = (
            pg_insert(PerformanceSnapshot)
            .values(
                date=today,
                balance=wallet.balance,
                equity=wallet.equity,
                daily_pnl=daily_pnl,
                trades_today=closed_today,
                win_rate_today=win_rate_today,
            )
            .on_conflict_do_update(
                constraint="uq_perf_date",
                set_={
                    "balance":        wallet.balance,
                    "equity":         wallet.equity,
                    "daily_pnl":      daily_pnl,
                    "trades_today":   closed_today,
                    "win_rate_today": win_rate_today,
                },
            )
        )
        await session.execute(stmt)
        await session.flush()

        sign = "+" if daily_pnl >= 0 else ""
        logger.info(
            f"WALLET │ DAILY_SNAPSHOT     │ Date: {today} │ "
            f"Equity: ${wallet.equity:,.2f} │ "
            f"Daily PnL: {sign}${daily_pnl:,.2f} │ "
            f"Trades: {closed_today} │ Win rate: {win_rate_today:.1f}%"
        )

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def reset(session: AsyncSession) -> dict:
        """Reset the wallet to the starting virtual balance.

        1. Sets all OPEN PaperTrade rows to CLOSED (with ai_reason noting the reset).
        2. Deletes every OpenPosition row.
        3. Resets all wallet counters.
        4. Writes a WALLET_RESET SimulationLog entry.

        Returns the fresh wallet summary dict.
        """
        now = datetime.utcnow()

        # 1 — Archive open trades: mark closed at the entry price (0 PnL)
        await session.execute(
            update(PaperTrade)
            .where(PaperTrade.status == TradeStatus.OPEN)
            .values(
                status=TradeStatus.CLOSED,
                pnl=0.0,
                pnl_percent=0.0,
                exit_price=PaperTrade.entry_price,
                closed_at=now,
                ai_reason="WALLET_RESET — position voided, virtual balance cleared",
            )
        )

        # 2 — Remove all live position snapshots
        await session.execute(delete(OpenPosition))
        await session.flush()

        # 3 — Reset the wallet row
        start  = await VirtualWallet._start_balance(session)
        wallet = await VirtualWallet._fetch(session)
        if wallet is None:
            wallet = WalletRow()
            session.add(wallet)

        wallet.balance        = start
        wallet.equity         = start
        wallet.realised_pnl   = 0.0
        wallet.unrealised_pnl = 0.0
        wallet.total_trades   = 0
        wallet.winning_trades = 0
        wallet.peak_balance   = start
        wallet.max_drawdown   = 0.0
        await session.flush()

        # 4 — Log the event
        logger.warning(
            _FMT.format(
                event="WALLET_RESET",
                symbol="—",
                amount=start,
                balance=wallet.balance,
            )
        )
        await VirtualWallet._simlog(
            session, "WALLET_RESET", "—",
            f"WALLET RESET — starting fresh with ${start:,.2f} virtual balance",
            {"starting_balance": start, "reset_at": now.isoformat()},
        )

        return await VirtualWallet.get_summary(session)
