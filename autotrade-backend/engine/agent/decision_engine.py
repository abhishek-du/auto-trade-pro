"""Decision Engine — fuses candidate + context into a structured decision.

Reference: trading_agent/decision.py (extended with bear-case check, M12).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from utils.config import settings
from utils.logger import logger


# Strategies that require MIS (intraday) product per NSE/SEBI rules:
# - Short-selling is only allowed intraday; delivery short is illegal on NSE/BSE
# - MIS positions must be squared off before 3:20 PM IST (Zerodha auto-squareoff)
_MIS_STRATEGIES = {"MEAN_REVERSION_SHORT"}


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
    product:      str   = "CNC"   # CNC=delivery positional | MIS=intraday (short allowed)
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

        # Master Intelligence Hub modifiers — earnings tone, news, FII bias
        tone_mod = news_mod = fii_mod = 0
        try:
            from engine import intelligence_hub as hub
            if hub.LAST_EARNINGS_CONTEXT is not None:
                tone = hub.LAST_EARNINGS_CONTEXT.tones_by_symbol.get(symbol, "NEUTRAL")
                tone_mod = {"OPTIMISTIC": 5, "NEUTRAL": 0, "CAUTIOUS": -10, "NEGATIVE": -20}.get(tone, 0)
            if hub.LAST_NEWS_CONTEXT is not None:
                news_raw = hub.LAST_NEWS_CONTEXT.scores_by_symbol.get(symbol, 0.0)
                news_mod = int(news_raw * 8)
            if hub.LAST_MACRO_CONTEXT is not None:
                fii_mod = hub.LAST_MACRO_CONTEXT.fii_bias * 3
            if tone_mod or news_mod or fii_mod:
                candidate.confidence += tone_mod + news_mod + fii_mod
                candidate.confidence = max(0, min(100, candidate.confidence))
                candidate.reasons.append(
                    f"hub_context:news={news_mod:+d},earnings={tone_mod:+d},fii={fii_mod:+d}"
                )
        except Exception as exc:
            logger.debug(f"[agent/decision] hub modifier skipped for {symbol}: {exc}")

        if candidate.confidence < settings.AGENT_CONFIDENCE_THRESHOLD:
            logger.debug(f"[agent/decision] {symbol} filtered: confidence {candidate.confidence} < {settings.AGENT_CONFIDENCE_THRESHOLD}")
            return None

        # NSE rule: short selling only allowed intraday (MIS). CNC delivery
        # shorts are rejected by Zerodha / SEBI. Any strategy that opens a
        # SELL without an existing long position must use MIS.
        product = (
            "MIS"
            if candidate.strategy in _MIS_STRATEGIES or candidate.side == "SELL"
            else getattr(settings, "AGENT_DEFAULT_PRODUCT", "CNC")
        )

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
            product=product,
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


# ── Hub 7-Factor Override ─────────────────────────────────────────────────────

async def fetch_hub_candidate(
    symbol: str,
    features,
    session,
) -> "TradeCandidate | None":
    """Query master_intelligence_scores for a fresh 7-factor score.

    Returns a TradeCandidate built from the Hub master_score if:
      - A row exists scored within the last 2 hours
      - abs(master_score) >= AGENT_CONFIDENCE_THRESHOLD
      - Symbol is not blocked (is_blocked=False)
      - For SELL signals: EQUITY_SHORT_ENABLED must be True

    ATR-based stops (2×ATR) and 2:1 R:R targets are derived from features,
    same as HubSignalStrategy. The rest of the agent pipeline (risk manager,
    position sizing, exits) is unchanged.
    """
    from db.models import MasterIntelligenceScore
    from sqlalchemy import select as _sel
    from engine.agent.strategies.base import TradeCandidate

    threshold = settings.AGENT_CONFIDENCE_THRESHOLD
    cutoff    = datetime.utcnow() - timedelta(hours=2)

    bare = symbol.replace(".NS", "")
    try:
        row = (await session.execute(
            _sel(MasterIntelligenceScore)
            .where(
                MasterIntelligenceScore.symbol.in_([bare, symbol]),
                MasterIntelligenceScore.scored_at >= cutoff,
                MasterIntelligenceScore.is_blocked == False,
            )
            .order_by(MasterIntelligenceScore.scored_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    except Exception as exc:
        logger.debug(f"[hub_override] DB query failed for {symbol}: {exc}")
        return None

    if row is None:
        return None

    master_score = row.master_score
    if abs(master_score) < threshold:
        logger.debug(
            f"[hub_override] {symbol} score={master_score:.1f} below threshold {threshold}"
        )
        return None

    side = "BUY" if master_score > 0 else "SELL"

    # Respect EQUITY_SHORT_ENABLED flag for SELL signals
    if side == "SELL" and not getattr(settings, "EQUITY_SHORT_ENABLED", False):
        logger.debug(f"[hub_override] {symbol} SELL skipped — EQUITY_SHORT_ENABLED=False")
        return None

    entry = features.close
    atr   = features.atr14
    if entry <= 0 or atr <= 0:
        return None

    if side == "BUY":
        stop   = round(entry - 2.0 * atr, 2)
        target = round(entry + 4.0 * atr, 2)
    else:
        stop   = round(entry + 2.0 * atr, 2)
        target = round(entry - 4.0 * atr, 2)

    confidence = min(int(abs(master_score)), 90)

    # Build sub-score breakdown for reasons
    reasons = [
        f"hub_7factor:score={master_score:.1f}",
        f"technical={row.technical_score:.1f}",
        f"news={row.news_score:.1f}",
        f"sector={row.sector_score:.1f}",
        f"macro={row.macro_score:.1f}",
        f"earnings={row.earnings_score:.1f}",
        f"fundamental={row.fundamental_score:.1f}",
        f"options={row.options_score:.1f}",
        f"hub_signal:{row.signal}",
        f"regime:{row.regime or features.regime}",
    ]

    logger.info(
        f"[hub_override] {symbol} → {side} | score={master_score:.1f} "
        f"conf={confidence}% | signal={row.signal} | scored_at={row.scored_at.isoformat()}"
    )

    return TradeCandidate(
        symbol=symbol,
        side=side,
        entry=round(entry, 2),
        stop=stop,
        target=target,
        confidence=confidence,
        reasons=reasons,
        strategy="HUB_7FACTOR",
    )
