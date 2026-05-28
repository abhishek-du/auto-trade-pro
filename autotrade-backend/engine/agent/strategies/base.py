"""Strategy base class — mirrors trading_agent/strategies/base.py."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TradeCandidate:
    symbol:     str
    side:       str        # "BUY" | "SELL"
    entry:      float
    stop:       float
    target:     float
    confidence: int        # 0-100
    reasons:    list = field(default_factory=list)
    strategy:   str  = "base"
    timeframe:  str  = "15m"

    @property
    def risk_reward(self) -> float:
        risk   = abs(self.entry - self.stop)
        reward = abs(self.target - self.entry)
        return round(reward / risk, 2) if risk > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "symbol":      self.symbol,
            "side":        self.side,
            "entry":       self.entry,
            "stop":        self.stop,
            "target":      self.target,
            "confidence":  self.confidence,
            "reasons":     self.reasons,
            "strategy":    self.strategy,
            "risk_reward": self.risk_reward,
        }


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        df,
        features,
        macro_bias: int,
        fund_grade: str,
    ) -> Optional[TradeCandidate]:
        ...
