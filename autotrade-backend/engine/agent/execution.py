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
        from paper_trading.virtual_wallet import VirtualWallet

        order_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        trade_value = round(decision.qty * decision.entry, 2)

        # Deduct full trade value from the shared VirtualWallet (same ₹5L pool as scanner).
        # Block the trade if wallet has insufficient funds.
        ok, msg = await VirtualWallet.deduct_margin(session, trade_value, decision.symbol)
        if not ok:
            logger.warning(
                f"[agent] PAPER BUY blocked for {decision.symbol}: {msg} "
                f"(needed ₹{trade_value:,.0f})"
            )
            return ""

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
            product=getattr(decision, "product", "CNC"),
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
            f"RR={decision.risk_reward} | {decision.strategy} "
            f"| wallet deducted ₹{trade_value:,.0f}"
        )
        return order_id

    async def _live_execute(self, decision, session: AsyncSession) -> str | None:
        if not settings.ZERODHA_ENABLED:
            logger.error("[agent] Live execution attempted but Zerodha not connected")
            return None

        product = getattr(decision, "product", "CNC")

        # NSE/BSE Rule: CNC delivery SELL requires an existing holding.
        # Short selling without owning shares is illegal in delivery segment.
        # Only MIS (intraday) permits selling without prior ownership.
        if decision.action == "SELL" and product == "CNC":
            from db.models import ZerodhaPosition
            from sqlalchemy import select as _sel
            bare = decision.symbol.replace(".NS", "")
            held = (await session.execute(
                _sel(ZerodhaPosition.quantity).where(
                    ZerodhaPosition.tradingsymbol == bare,
                    ZerodhaPosition.product == "CNC",
                )
            )).scalar_one_or_none()
            if not held or int(held) < decision.qty:
                logger.warning(
                    f"[agent] SELL {bare} blocked — not in CNC holdings "
                    f"(held={held or 0}, requested={decision.qty}). "
                    f"SEBI/NSE rule: delivery short selling not allowed. "
                    f"Use MIS product for intraday shorts."
                )
                return None

        try:
            from engine.zerodha_executor import place_real_order
            result = await place_real_order(
                symbol=decision.symbol,
                transaction_type=decision.action,
                quantity=decision.qty,
                session=session,
                product=product,
                signal=decision,
            )
            return result.get("order_id") if result else None
        except Exception as exc:
            logger.error(f"[agent] Live order failed for {decision.symbol}: {exc}")
            return None

    async def _fetch_hub_scores_for_exits(
        self,
        symbols: list[str],
        session: AsyncSession,
    ) -> dict[str, float]:
        """Batch-fetch latest Hub master_score for all open positions (one query).

        Returns {bare_symbol: master_score}. Scores older than 2 hours are
        excluded — stale data is worse than no data for exit decisions.
        """
        from db.models import MasterIntelligenceScore
        from sqlalchemy import select
        from datetime import timedelta

        if not symbols:
            return {}
        bare = [s.replace(".NS", "") for s in symbols]
        cutoff = datetime.utcnow() - timedelta(hours=24)
        try:
            rows = (await session.execute(
                select(
                    MasterIntelligenceScore.symbol,
                    MasterIntelligenceScore.master_score,
                    MasterIntelligenceScore.scored_at,
                )
                .where(
                    MasterIntelligenceScore.symbol.in_(bare + symbols),
                    MasterIntelligenceScore.scored_at >= cutoff,
                )
                .order_by(MasterIntelligenceScore.scored_at.desc())
            )).all()

            result: dict[str, float] = {}
            for row in rows:
                key = row.symbol.replace(".NS", "")
                if key not in result:          # keep most recent per symbol
                    result[key] = row.master_score
            return result
        except Exception as exc:
            logger.debug(f"[exits] hub score batch fetch failed: {exc}")
            return {}

    async def check_and_close_positions(
        self,
        portfolio_ctx,
        current_prices: dict,
        session: AsyncSession,
    ) -> None:
        open_syms = list(portfolio_ctx.open_positions.keys())

        # Batch-fetch Hub scores once for all open positions
        hub_exit_enabled = getattr(settings, "AGENT_HUB_EXIT_ENABLED", True)
        hub_scores: dict[str, float] = {}
        if hub_exit_enabled and open_syms:
            hub_scores = await self._fetch_hub_scores_for_exits(open_syms, session)

        for symbol, pos in list(portfolio_ctx.open_positions.items()):
            price_data = current_prices.get(symbol, {})
            price = float(price_data.get("price", 0) or 0)

            # PRICE_CACHE is empty after market hours (KiteTicker stops at 15:30
            # IST, yfinance cache TTL expires) — fall back to the most recent
            # 1h candle close so end-of-day SL/target sweeps still process.
            if price <= 0:
                try:
                    from db.models import Candle
                    from sqlalchemy import select
                    row = (await session.execute(
                        select(Candle.close)
                        .where(Candle.symbol == symbol, Candle.timeframe == "1h")
                        .order_by(Candle.timestamp.desc())
                        .limit(1)
                    )).scalar_one_or_none()
                    if row:
                        price = float(row)
                        logger.debug(
                            f"[exits] {symbol}: PRICE_CACHE empty, "
                            f"using last 1h candle ₹{price:.2f}"
                        )
                except Exception as exc:
                    logger.debug(f"[exits] candle fallback failed {symbol}: {exc}")

            if price <= 0:
                continue

            should_close = False
            exit_reason  = ""

            # ── Hub 7-Factor exit check ───────────────────────────────────────
            # Exit a BUY position early when company/market intelligence changes:
            #   HUB_REVERSAL    — score crossed to negative (bad news/earnings/macro)
            #   HUB_DETERIORATION — score still positive but too weak to justify holding
            # This fires BEFORE the price-based checks so we get out before
            # the ATR stop is reached (better fill, smaller loss).
            if hub_exit_enabled and not should_close:
                bare_sym = symbol.replace(".NS", "")
                hub_score = hub_scores.get(bare_sym)
                reversal_threshold = getattr(settings, "AGENT_HUB_EXIT_REVERSAL_THRESHOLD", -10)
                score_floor        = getattr(settings, "AGENT_HUB_EXIT_SCORE_FLOOR", 5)

                if hub_score is not None:
                    if pos["side"] == "BUY":
                        if hub_score <= reversal_threshold:
                            should_close = True
                            exit_reason  = f"HUB_REVERSAL:{hub_score:.1f}"
                            logger.info(
                                f"[hub_exit] {symbol} BUY → EXIT | "
                                f"score={hub_score:.1f} ≤ reversal threshold {reversal_threshold} | "
                                f"company/market turned bearish"
                            )
                        elif hub_score < score_floor:
                            should_close = True
                            exit_reason  = f"HUB_DETERIORATION:{hub_score:.1f}"
                            logger.info(
                                f"[hub_exit] {symbol} BUY → EXIT | "
                                f"score={hub_score:.1f} < floor {score_floor} | "
                                f"conviction too weak to hold"
                            )
                    elif pos["side"] == "SELL":
                        # Reverse: SELL position exits when score flips positive
                        if hub_score >= abs(reversal_threshold):
                            should_close = True
                            exit_reason  = f"HUB_REVERSAL_BULLISH:{hub_score:.1f}"
                            logger.info(
                                f"[hub_exit] {symbol} SELL → EXIT | "
                                f"score={hub_score:.1f} flipped positive"
                            )

            if pos["side"] == "BUY":
                # ── Multi-target exit ladder ──────────────────────────────────
                # Stage 1 — SL hit (trailing if set after T1, else original stop).
                # Stage 2 — T1 hit: close 50%, trail SL to near-breakeven.
                # Stage 3 — T2 hit (after T1): close remaining 50%.
                # Stage 4 — Trailing SL update after T1: trail by 1.5× initial risk
                #           below price (ATR proxy); only widen, never tighten.
                # Stage 5 — Max-hold escape: full close after 10 days if T1 never hit.
                entry        = pos["entry"]
                stop_orig    = pos["stop"]
                t1           = pos.get("target1") or (entry + abs(entry - stop_orig))
                t2           = (
                    pos.get("target2")
                    or pos.get("target")
                    or (entry + 2 * abs(entry - stop_orig))
                )
                partial_done = pos.get("partial_done", False)
                trailing_sl  = pos.get("trailing_sl")
                entry_ts_str = pos.get("entry_ts")
                qty          = pos.get("qty", 1)

                effective_stop = trailing_sl if trailing_sl else stop_orig

                should_partial    = False
                should_full_close = False

                if effective_stop > 0 and price <= effective_stop:
                    should_full_close = True
                    exit_reason = "SL_HIT"
                elif not partial_done and price >= t1:
                    should_partial = True
                    exit_reason = "T1_PARTIAL"
                elif partial_done and price >= t2:
                    should_full_close = True
                    exit_reason = "T2_TARGET"
                elif partial_done:
                    # Widen trailing SL only — never pull it back
                    atr_proxy = abs(entry - stop_orig)
                    new_trail = price - 1.5 * atr_proxy
                    current_trail = pos.get("trailing_sl") or (entry + 0.1 * atr_proxy)
                    if new_trail > current_trail:
                        portfolio_ctx.open_positions[symbol]["trailing_sl"] = round(new_trail, 2)
                        logger.debug(f"[exits] {symbol}: trailing SL → ₹{new_trail:.2f}")

                # Max-hold escape (only checked if no other exit fired)
                if (
                    not should_full_close
                    and not should_partial
                    and not partial_done
                    and entry_ts_str
                ):
                    try:
                        entry_ts = datetime.fromisoformat(entry_ts_str)
                        if (datetime.utcnow() - entry_ts).days > 10:
                            should_full_close = True
                            exit_reason = "MAX_HOLD_EXCEEDED"
                    except Exception:
                        pass

                # Execute partial — split the position, move SL to near-breakeven
                if should_partial:
                    half_qty = max(1, qty // 2)
                    partial_pnl = half_qty * (price - entry)
                    portfolio_ctx.open_positions[symbol]["qty"]          = qty - half_qty
                    portfolio_ctx.open_positions[symbol]["partial_done"] = True
                    new_sl = entry + 0.1 * abs(entry - stop_orig)
                    portfolio_ctx.open_positions[symbol]["trailing_sl"]  = round(new_sl, 2)
                    portfolio_ctx.cash += half_qty * price
                    await self._record_exit(symbol, price, "T1_PARTIAL", partial_pnl, session)
                    logger.info(
                        f"[{'PAPER' if settings.AGENT_PAPER_MODE else 'LIVE'}] "
                        f"T1 HIT {symbol} @ ₹{price:.2f} | "
                        f"Sold {half_qty} of {qty} | pnl=₹{partial_pnl:,.2f} | "
                        f"SL moved to breakeven ₹{new_sl:.2f}"
                    )
                # Defer the shared full-close handler below
                elif should_full_close:
                    should_close = True
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
        from paper_trading.virtual_wallet import VirtualWallet
        from sqlalchemy import select

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
            trade_value = round(trade.qty * trade.entry_price, 2)
            await VirtualWallet.return_margin(session, trade_value, pnl, symbol)
            await session.commit()
