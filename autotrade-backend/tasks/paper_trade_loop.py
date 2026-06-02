# Main automated paper-trading loop.
# Runs every 60 s via Celery beat.  One cycle = update positions → generate
# signals → risk-check → open trades → snapshot.
# ALL positions use FAKE/VIRTUAL currency — no real money is involved.

import asyncio

from tasks.celery_app import celery_app
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
            trade    = await open_paper_trade(signal, pos_size, session)

            balance -= pos_size["usd_value"] * 0.1
            pos_result     = await session.execute(select(OpenPosition))
            open_positions = list(pos_result.scalars().all())

            explanation  = await generate_trade_explanation(signal)
            notification = format_paper_trade_notification(trade, explanation)
            logger.info(notification)

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
    """Celery task: one full paper-trading cycle.

    PAPER TRADING ONLY — virtual currency, no real money involved.
    """
    logger.info("[paper_trade_loop] Starting cycle")
    _run_async(_loop())
