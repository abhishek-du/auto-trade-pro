"""Main Agent Loop — orchestrates the per-bar decision cycle.

Reference: trading_agent/main.py → evaluate_universe()
Runs on every 15-minute bar close via Celery beat.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from engine.agent.analyzer        import MarketAnalyzerAgent
from engine.agent.selector        import StrategySelectorAgent
from engine.agent.fundamentals    import FundamentalsAgent
from engine.agent.macro           import MacroSectorAgent
from engine.agent.risk_manager    import RiskManagerAgent
from engine.agent.decision_engine import DecisionEngine
from engine.agent.execution       import AgentExecutionManager
from engine.agent.portfolio_context import AgentPortfolioContext
from utils.config import settings
from utils.logger import logger

# ── Module-level singletons ───────────────────────────────────────────────────

_analyzer   = MarketAnalyzerAgent()
_selector   = StrategySelectorAgent()
_fund_agent = FundamentalsAgent()
_macro      = MacroSectorAgent()
_decision   = DecisionEngine()
_executor   = AgentExecutionManager()

# Shared in-memory portfolio (paper mode uses this; live mode syncs from Kite)
_portfolio: AgentPortfolioContext | None = None


def _get_portfolio() -> AgentPortfolioContext:
    global _portfolio
    if _portfolio is None:
        _portfolio = AgentPortfolioContext(
            equity=settings.AGENT_EQUITY,
            cash=settings.AGENT_EQUITY,
        )
    return _portfolio


def _is_market_hours() -> bool:
    now = datetime.now().time()
    start_h, start_m = map(int, settings.AGENT_SESSION_START.split(":"))
    end_h,   end_m   = map(int, settings.AGENT_SESSION_END.split(":"))
    return dtime(start_h, start_m) <= now <= dtime(end_h, end_m)


def _is_trading_day() -> bool:
    return datetime.now().weekday() < 5  # Mon-Fri


async def run_agent_cycle(session: AsyncSession) -> dict:
    """Top-level entry point called by the Celery task."""

    if not settings.AGENT_ENABLED:
        return {"status": "disabled"}

    if not _is_trading_day():
        return {"status": "non_trading_day"}

    if not _is_market_hours():
        return {"status": "outside_market_hours"}

    portfolio = _get_portfolio()

    # Check stop/target on all open positions first
    from crawler.live_prices import PRICE_CACHE
    await _executor.check_and_close_positions(portfolio, PRICE_CACHE, session)

    universe  = settings.nse_symbols
    results   = []
    skipped   = 0

    for symbol in universe:
        try:
            result = await _process_symbol(symbol, portfolio, session)
            if result:
                results.append(result)
            else:
                skipped += 1
        except Exception as exc:
            logger.warning(f"[agent] cycle error on {symbol}: {exc}")
            skipped += 1

    return {
        "status":           "ok",
        "cycle_ts":         datetime.utcnow().isoformat(),
        "paper_mode":       settings.AGENT_PAPER_MODE,
        "symbols_scanned":  len(universe),
        "decisions":        len(results),
        "skipped":          skipped,
        "portfolio": {
            "equity":             portfolio.equity,
            "cash":               round(portfolio.cash, 2),
            "open_positions":     len(portfolio.open_positions),
            "daily_pnl_pct":      round(portfolio.daily_pnl_pct * 100, 2),
            "weekly_pnl_pct":     round(portfolio.weekly_pnl_pct * 100, 2),
        },
        "decisions_data": results,
    }


async def _process_symbol(
    symbol: str,
    portfolio: AgentPortfolioContext,
    session: AsyncSession,
) -> dict | None:

    # 1. Get candle data from DB
    from crawler.price_feed import get_latest_candles

    candles = await get_latest_candles(symbol, settings.AGENT_TIMEFRAME, 300, session)
    if not candles or len(candles) < settings.AGENT_WARMUP_BARS:
        return None

    candles_sorted = sorted(candles, key=lambda c: c.timestamp)
    df = pd.DataFrame([{
        "open":      float(c.open),
        "high":      float(c.high),
        "low":       float(c.low),
        "close":     float(c.close),
        "volume":    float(c.volume),
        "timestamp": c.timestamp,
    } for c in candles_sorted])
    df.set_index("timestamp", inplace=True)

    # 2. Compute market features
    try:
        features = _analyzer.compute_features(df)
    except Exception as exc:
        logger.debug(f"[agent] features failed for {symbol}: {exc}")
        return None

    # 3. Macro and fundamentals (cached)
    macro_bias          = _macro.bias(symbol)
    fund_score, fund_grade = await _fund_agent.get_cached_grade(symbol)

    # 4. Strategy proposal
    candidate = _selector.propose(symbol, df, features, macro_bias, fund_grade)

    # 5. Decision fusion
    decision = _decision.fuse(
        symbol=symbol,
        candidate=candidate,
        regime=features.regime,
        macro_bias=macro_bias,
        fund_score=fund_score,
        fund_grade=fund_grade,
        equity=portfolio.equity,
    )

    if decision is None:
        skip_reason = (
            f"no_qualifying_setup" if candidate is None
            else f"decision_filtered:conf={candidate.confidence}"
        )
        logger.debug(f"[agent] SKIP {symbol} | {skip_reason} | regime={features.regime}")
        return None

    # 6. Risk Manager veto
    risk_ok, risk_reason = RiskManagerAgent(portfolio.to_risk_ctx()).can_take_trade(
        candidate=candidate if candidate else decision,
        equity=portfolio.equity,
    )

    if not risk_ok:
        logger.info(f"[agent] BLOCKED {symbol} | {risk_reason} | {decision.strategy}")
        await _log_skipped_decision(symbol, decision, risk_reason, session)
        return None

    # 7. Execute
    order_id = await _executor.execute(decision, session)

    if order_id:
        portfolio.add_position(decision)

    return decision.to_dict()


async def _log_skipped_decision(
    symbol: str,
    decision,
    risk_reason: str,
    session: AsyncSession,
) -> None:
    try:
        from db.models import AgentDecision
        db_dec = AgentDecision(
            symbol=symbol,
            action=decision.action,
            confidence=decision.confidence,
            regime=decision.regime,
            strategy=decision.strategy,
            entry=decision.entry,
            stop=decision.stop,
            target=decision.target,
            qty=0,
            risk_pct=decision.risk_pct,
            reasons=decision.reasons,
            macro_bias=decision.macro_bias,
            fund_score=decision.fund_score,
            skip_reason=risk_reason,
            is_paper=settings.AGENT_PAPER_MODE,
            order_id=None,
        )
        session.add(db_dec)
        await session.commit()
    except Exception as exc:
        logger.debug(f"[agent] skip log failed: {exc}")


def eod_reconcile() -> None:
    """Reset daily counters at EOD."""
    portfolio = _get_portfolio()
    portfolio.reset_day()
    logger.info("[agent] EOD reset complete")
