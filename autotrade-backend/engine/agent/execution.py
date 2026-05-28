"""Execution Manager — paper or live via existing zerodha_executor.py.

Reference: trading_agent/execution.py (integrated with AutoTrade Pro DB).
Paper mode is always default. Live requires AGENT_PAPER_MODE=false + Zerodha connected.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger


class AgentExecutionManager:

    async def execute(
        self,
        decision,
        session: AsyncSession,
    ) -> str | None:
        if settings.AGENT_PAPER_MODE:
            return await self._paper_execute(decision, session)
        return await self._live_execute(decision, session)

    async def _paper_execute(self, decision, session: AsyncSession) -> str:
        from db.models import AgentDecision, AgentTrade

        order_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"

        db_dec = AgentDecision(
            symbol=decision.symbol,
            action=decision.action,
            confidence=decision.confidence,
            regime=decision.regime,
            strategy=decision.strategy,
            entry=decision.entry,
            stop=decision.stop,
            target=decision.target,
            qty=decision.qty,
            risk_pct=decision.risk_pct,
            reasons=decision.reasons,
            macro_bias=decision.macro_bias,
            fund_score=decision.fund_score,
            is_paper=True,
            order_id=order_id,
        )
        session.add(db_dec)

        trade = AgentTrade(
            decision_id=db_dec.id,
            symbol=decision.symbol,
            side=decision.action,
            qty=decision.qty,
            entry_price=decision.entry,
            stop_price=decision.stop,
            target_price=decision.target,
            entry_ts=datetime.utcnow(),
            strategy=decision.strategy,
            regime=decision.regime,
            is_paper=True,
        )
        session.add(trade)
        await session.commit()

        logger.info(
            f"[PAPER] {decision.action} {decision.qty} {decision.symbol} "
            f"@ ₹{decision.entry:.2f} | stop=₹{decision.stop:.2f} "
            f"target=₹{decision.target:.2f} | conf={decision.confidence}% "
            f"RR={decision.risk_reward} | {decision.strategy}"
        )
        return order_id

    async def _live_execute(self, decision, session: AsyncSession) -> str | None:
        if not settings.ZERODHA_ENABLED:
            logger.error("[agent] Live execution attempted but Zerodha not connected")
            return None
        try:
            from engine.zerodha_executor import place_real_order
            result = await place_real_order(
                symbol=decision.symbol,
                transaction_type=decision.action,
                quantity=decision.qty,
                session=session,
                signal_id=str(decision.ts),
                confidence=float(decision.confidence),
            )
            return result.get("order_id") if result else None
        except Exception as exc:
            logger.error(f"[agent] Live order failed for {decision.symbol}: {exc}")
            return None

    async def check_and_close_positions(
        self,
        portfolio_ctx,
        current_prices: dict,
        session: AsyncSession,
    ) -> None:
        for symbol, pos in list(portfolio_ctx.open_positions.items()):
            price_data = current_prices.get(symbol, {})
            price = float(price_data.get("price", 0) or 0)
            if price <= 0:
                continue

            should_close = False
            exit_reason  = ""

            if pos["side"] == "BUY":
                if pos["stop"] > 0 and price <= pos["stop"]:
                    should_close = True; exit_reason = "STOP_HIT"
                elif pos["target"] > 0 and price >= pos["target"]:
                    should_close = True; exit_reason = "TARGET_HIT"
            else:
                if pos["stop"] > 0 and price >= pos["stop"]:
                    should_close = True; exit_reason = "STOP_HIT"
                elif pos["target"] > 0 and price <= pos["target"]:
                    should_close = True; exit_reason = "TARGET_HIT"

            if should_close:
                pnl = portfolio_ctx.close_position(symbol, price)
                await self._record_exit(symbol, price, exit_reason, pnl, session)
                logger.info(
                    f"[{'PAPER' if settings.AGENT_PAPER_MODE else 'LIVE'}] "
                    f"CLOSED {symbol} @ ₹{price:.2f} | {exit_reason} | pnl=₹{pnl:,.2f}"
                )

    async def _record_exit(
        self,
        symbol: str,
        exit_price: float,
        reason: str,
        pnl: float,
        session: AsyncSession,
    ) -> None:
        from db.models import AgentTrade
        from sqlalchemy import select, update

        res = await session.execute(
            select(AgentTrade).where(
                AgentTrade.symbol == symbol,
                AgentTrade.exit_ts == None,
                AgentTrade.is_paper == settings.AGENT_PAPER_MODE,
            ).order_by(AgentTrade.entry_ts.desc()).limit(1)
        )
        trade = res.scalar_one_or_none()
        if trade:
            trade.exit_price  = exit_price
            trade.exit_ts     = datetime.utcnow()
            trade.exit_reason = reason
            trade.pnl         = pnl
            await session.commit()
