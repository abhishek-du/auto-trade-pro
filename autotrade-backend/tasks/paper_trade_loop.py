# Main automated paper-trading loop.
# Runs every 60 s via Celery beat.  One cycle = update positions → generate
# signals → risk-check → open trades → snapshot.
# ALL positions use FAKE/VIRTUAL currency — no real money is involved.

import asyncio

from tasks.celery_app import celery_app
from utils.config import settings
from utils.logger import logger


def _run_async(coro):
    return asyncio.run(coro)


async def _loop():
    from sqlalchemy import select

    from db.models import OpenPosition
    from engine.llm_explainer import (
        format_paper_trade_notification,
        generate_trade_explanation,
    )
    from engine.risk_manager import calculate_position_size, validate_signal
    # Unified on the India 15-factor engine; the base signal_generator's
    # analyze_all_symbols was US/forex-flavored and silently diverged from
    # what the India scanner page showed users.
    from engine.india_signal_generator import analyze_all_india_symbols as analyze_all_symbols
    from paper_trading.simulation_logger import SimLogger
    from paper_trading.trade_simulator import (
        open_paper_trade,
        update_positions_with_current_prices,
    )
    from paper_trading.virtual_wallet import VirtualWallet
    from tasks._db import celery_session

    async with celery_session() as session:

        # ── Step 1: close SL/TP hits, refresh unrealised PnL ─────────────────
        auto_closed = await update_positions_with_current_prices(session)
        if auto_closed:
            logger.info(
                f"[paper_trade_loop] {len(auto_closed)} position(s) auto-closed "
                f"this cycle"
            )
            # Telegram exit alerts
            if settings.telegram_available:
                from integrations.telegram_service import send, fmt_exit
                for c in auto_closed:
                    await send(fmt_exit(
                        symbol=c["symbol"],
                        side=c["direction"],
                        entry=c["entry_price"],
                        exit_price=c["exit_price"],
                        qty=c["size_units"],
                        pnl=c["pnl"],
                        reason=c["reason"],
                    ))

        # ── Step 2: generate actionable signals for all watchlist symbols ─────
        signals = await analyze_all_symbols(session)
        logger.info(
            f"[paper_trade_loop] {len(signals)} actionable signal(s) generated"
        )

        if not signals:
            await VirtualWallet.take_daily_snapshot(session)
            await session.commit()
            return

        # ── Step 3: current wallet state ──────────────────────────────────────
        summary = await VirtualWallet.get_summary(session)
        balance = summary["balance"]

        # ── Step 4: process each signal through the risk gate ─────────────────
        pos_result = await session.execute(select(OpenPosition))
        open_positions = list(pos_result.scalars().all())

        for signal in signals:
            validated, reason = await validate_signal(
                signal, balance, open_positions, session
            )

            await SimLogger.log_analysis_cycle(
                session, signal.symbol, signal,
                rejected=not validated,
                reject_reason=reason if not validated else None,
            )

            if not validated:
                continue

            pos_size = calculate_position_size(signal, balance)
            try:
                trade = await open_paper_trade(signal, pos_size, session)
            except ValueError as exc:
                logger.warning(f"[paper_trade_loop] {exc}")
                continue

            balance -= pos_size["usd_value"]
            pos_result     = await session.execute(select(OpenPosition))
            open_positions = list(pos_result.scalars().all())

            explanation  = await generate_trade_explanation(signal)
            notification = format_paper_trade_notification(trade, explanation)
            logger.info(notification)

            if settings.telegram_available:
                from integrations.telegram_service import send, fmt_entry
                await send(fmt_entry(signal, qty=pos_size.get("units", 0)))

        # ── Step 5: persist today's performance snapshot ──────────────────────
        await VirtualWallet.take_daily_snapshot(session)

        final = await VirtualWallet.get_summary(session)
        logger.info(
            f"[paper_trade_loop] Cycle complete — "
            f"balance=${final['balance']:.2f}  "
            f"equity=${final['equity']:.2f}  "
            f"roi={final['roi_percent']:+.2f}%  "
            f"open_positions={len(open_positions)}"
        )

        await session.commit()


@celery_app.task(name="tasks.paper_trade_loop.run_paper_trade_loop")
def run_paper_trade_loop():
    """DISABLED — legacy loop replaced by india_trade_loop.

    This loop used 20% position sizing and had no duplicate detection,
    causing oversized and duplicate trades. Kept as a registered task
    so Celery doesn't error on stale schedules, but it refuses to run.
    """
    logger.warning("[paper_trade_loop] DISABLED — this legacy loop is dead. Use india_trade_loop.")
    return
