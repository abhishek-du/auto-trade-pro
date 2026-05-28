"""Decision Engine — fuses candidate + context into a structured decision.

Reference: trading_agent/decision.py (extended with bear-case check, M12).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from utils.config import settings
from utils.logger import logger


@dataclass
class AgentDecisionOutput:
    symbol:       str
    action:       str
    confidence:   int
    regime:       str
    strategy:     str
    entry:        float
    stop:         float
    target:       float
    qty:          int
    risk_pct:     float
    risk_reward:  float
    reasons:      list  = field(default_factory=list)
    macro_bias:   int   = 0
    fund_score:   int   = 0
    fund_grade:   str   = "WATCHLIST"
    ts:           str   = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts or datetime.utcnow().isoformat()
        return d


class DecisionEngine:

    def fuse(
        self,
        symbol: str,
        candidate,
        regime: str,
        macro_bias: int,
        fund_score: int,
        fund_grade: str,
        equity: float,
    ) -> AgentDecisionOutput | None:

        if candidate is None:
            return None

        from engine.agent.risk_manager import position_size

        qty = position_size(
            equity, settings.AGENT_MAX_RISK_PER_TRADE,
            candidate.entry, candidate.stop,
        )
        if qty <= 0:
            return None

        risk_amt  = qty * abs(candidate.entry - candidate.stop)
        risk_pct  = risk_amt / max(equity, 1)

        # Varsity M12 — Innerworth: always check the opposing view
        bear = self._bear_case(candidate, regime, macro_bias)
        if bear:
            candidate.reasons.append(f"bear_case:{bear}")
            if "STRONG" in bear:
                candidate.confidence -= 10

        if candidate.confidence < settings.AGENT_CONFIDENCE_THRESHOLD:
            logger.debug(f"[agent/decision] {symbol} filtered: confidence {candidate.confidence} < {settings.AGENT_CONFIDENCE_THRESHOLD}")
            return None

        return AgentDecisionOutput(
            symbol=symbol,
            action=candidate.side,
            confidence=candidate.confidence,
            regime=regime,
            strategy=candidate.strategy,
            entry=candidate.entry,
            stop=candidate.stop,
            target=candidate.target,
            qty=qty,
            risk_pct=round(risk_pct, 4),
            risk_reward=candidate.risk_reward,
            reasons=candidate.reasons,
            macro_bias=macro_bias,
            fund_score=fund_score,
            fund_grade=fund_grade,
            ts=datetime.utcnow().isoformat(),
        )

    @staticmethod
    def _bear_case(candidate, regime: str, macro_bias: int) -> str:
        """Varsity M12: document the opposing case before committing."""
        if candidate.side == "BUY":
            if regime == "BEAR_TRENDING":   return "STRONG:buying_into_bear_trend"
            if macro_bias <= -2:            return "STRONG:macro_headwind"
        else:
            if regime == "BULL_TRENDING":   return "STRONG:shorting_bull_trend"
            if macro_bias >= 2:             return "STRONG:macro_tailwind"
        return ""
