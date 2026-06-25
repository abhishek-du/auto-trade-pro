"""Strategy Selector — runs all enabled strategies and returns the best candidate.

Reference: trading_agent/selector.py
Varsity Module 10: best setup wins (highest confidence + RR ≥ 1.5).
"""
from __future__ import annotations

from engine.agent.strategies.pullback_trend   import PullbackTrendLong
from engine.agent.strategies.mean_reversion   import MeanReversionShort
from engine.agent.strategies.range_reversal   import RangeReversalLong
from engine.agent.strategies.exhaustion_short import ExhaustionShort
from engine.agent.strategies.hub_signal       import HubSignalStrategy
from utils.logger import logger


class StrategySelectorAgent:

    def __init__(self):
        # TREND_BREAKOUT_LONG disabled (Phase 5): backtest mean_R=-0.003 over 400+
        # trades — zero statistical edge; keeping it active dilutes expectancy.
        # Short strategies (MeanReversionShort, ExhaustionShort) require
        # EQUITY_SHORT_ENABLED=True and use MIS product (intraday only — NSE rule).
        self.strategies = [
            PullbackTrendLong(),
            MeanReversionShort(),
            RangeReversalLong(),
            ExhaustionShort(),
            HubSignalStrategy(),   # widest net — always last
        ]

    def propose(self, symbol, df, features, macro_bias: int, fund_grade: str):
        """Evaluate all strategies; return highest-confidence qualifying setup."""
        best = None
        for strat in self.strategies:
            try:
                candidate = strat.evaluate(symbol, df, features, macro_bias, fund_grade)
                if candidate is None:
                    continue
                # Varsity Module 9: minimum 1.5:1 R:R required
                if candidate.risk_reward < 1.5:
                    continue
                if best is None or candidate.confidence > best.confidence:
                    best = candidate
            except Exception as exc:
                logger.debug(f"[agent/selector] {strat.name} error on {symbol}: {exc}")
        return best
