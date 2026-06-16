"""Decision Engine — fuses candidate + context into a structured decision.

Reference: trading_agent/decision.py (extended with bear-case check, M12).

Pipeline order:
  1. fetch_hub_candidate()  — regime restriction + conflict detection (hard skips)
  2. DecisionEngine.fuse()  — multiplicative confidence + threshold check + position sizing
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
    symbol:             str
    action:             str
    confidence:         int
    regime:             str
    strategy:           str
    entry:              float
    stop:               float
    target:             float
    qty:                int
    risk_pct:           float
    risk_reward:        float
    product:            str   = "CNC"   # CNC=delivery positional | MIS=intraday | NRML=F&O
    reasons:            list  = field(default_factory=list)
    macro_bias:         int   = 0
    fund_score:         int   = 0
    fund_grade:         str   = "WATCHLIST"
    ts:                 str   = ""
    master_score:       float | None = None   # raw hub score before confidence calc
    confidence_factors: dict  | None = None   # breakdown for audit log
    # ── F&O fields (EQUITY for cash trades; populated for FUTURE/CE/PE) ────────
    instrument_type:    str   = "EQUITY"        # EQUITY | FUTURE | CE | PE
    underlying_symbol:  str   | None = None     # e.g. "NIFTY" for a NIFTY option
    tradingsymbol:      str   | None = None      # broker NFO symbol, e.g. NIFTY26JAN24500CE
    strike_price:       float | None = None
    option_type:        str   | None = None     # CE | PE
    expiry_date:        str   | None = None     # ISO date string
    lot_size:           int   = 1
    contract_multiplier: float = 1.0
    exchange:           str   = "NSE"           # NSE | NFO

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
    ) -> tuple["AgentDecisionOutput | None", "str | None"]:
        """Return (decision, None) on success or (None, reject_reason) when filtered."""

        if candidate is None:
            return None, "no_candidate"

        # NSE/BSE: equity delivery short is illegal; intraday short requires EQUITY_SHORT_ENABLED.
        # This guard applies to ALL candidate sources (hub override + strategy selector).
        if candidate.side == "SELL" and not getattr(settings, "EQUITY_SHORT_ENABLED", False):
            logger.debug(f"[agent/decision] {symbol} SELL blocked — EQUITY_SHORT_ENABLED=False")
            return None, "EQUITY_SHORT_ENABLED=False"

        from engine.agent.risk_manager import capital_utilization_size

        # Apply regime-based position size reduction flag (set by fetch_hub_candidate)
        size_factor = getattr(candidate, "size_factor", 1.0)

        # Capital-utilization sizing: deploy toward a conviction-weighted target
        # (so the ₹20L is actually used) while keeping the per-trade risk guard,
        # the 20% per-position cap, and the cash buffer. `deployed_notional` is
        # passed by the caller so the cash-buffer room is respected portfolio-wide.
        deployed_notional = getattr(candidate, "deployed_notional", 0.0)
        conviction = abs(getattr(candidate, "master_score", None) or candidate.confidence)
        qty, _size_reason = capital_utilization_size(
            equity, conviction, candidate.entry, candidate.stop,
            deployed_notional, size_factor=size_factor,
        )
        if qty <= 0:
            return None, f"qty_zero:{_size_reason}"

        risk_amt = qty * abs(candidate.entry - candidate.stop)
        risk_pct = risk_amt / max(equity, 1)

        # Varsity M12 — Innerworth: always check the opposing view
        bear = self._bear_case(candidate, regime, macro_bias)
        if bear:
            candidate.reasons.append(f"bear_case:{bear}")

        # ── Conflict detection ────────────────────────────────────────────────
        # Hard skips when hub context disagrees with the BUY signal.
        # Checked BEFORE confidence calculation so we never emit a low-conf order.
        if candidate.side == "BUY":
            conflict_reason = self._check_conflicts(symbol, candidate)
            if conflict_reason:
                candidate.reasons.append(conflict_reason)
                logger.info(
                    f"[agent/decision] {symbol} CONFLICT SKIP — {conflict_reason}"
                )
                return None, conflict_reason

        # ── Multiplicative confidence ─────────────────────────────────────────
        # Replaces the old additive hub modifier.
        bare = symbol.replace(".NS", "")
        raw_master = getattr(candidate, "master_score", None)
        if raw_master is not None:
            signal_strength = abs(raw_master) / 100.0
        else:
            signal_strength = candidate.confidence / 100.0

        regime_factor    = self._regime_factor(candidate.side, regime)
        news_factor      = 1.0
        earnings_factor  = 1.0
        fii_factor       = 1.0
        news_raw         = 0.0
        earnings_tone    = "NEUTRAL"
        fii_bias_val     = 0

        try:
            from engine import intelligence_hub as hub
            if hub.LAST_NEWS_CONTEXT is not None:
                news_raw    = hub.LAST_NEWS_CONTEXT.scores_by_symbol.get(bare, 0.0)
                news_factor = max(0.5, min(1.5, 1.0 + news_raw * 0.5))

            if hub.LAST_EARNINGS_CONTEXT is not None:
                earnings_tone  = hub.LAST_EARNINGS_CONTEXT.tones_by_symbol.get(bare, "NEUTRAL")
                earnings_bonus = {"OPTIMISTIC": 5, "NEUTRAL": 0, "CAUTIOUS": -10, "NEGATIVE": -20}.get(earnings_tone, 0)
                earnings_factor = max(0.5, min(1.5, 1.0 + earnings_bonus / 100.0))

            if hub.LAST_MACRO_CONTEXT is not None:
                fii_bias_val = hub.LAST_MACRO_CONTEXT.fii_bias
                fii_factor   = max(0.6, min(1.4, 1.0 + fii_bias_val * 0.2))

        except Exception as exc:
            logger.debug(f"[agent/decision] hub factors skipped for {symbol}: {exc}")

        market_support    = regime_factor * news_factor * earnings_factor * fii_factor
        final_confidence  = max(0, min(100, int(signal_strength * market_support * 100)))

        conf_factors = {
            "signal_strength":  round(signal_strength, 4),
            "regime_factor":    round(regime_factor, 4),
            "news_raw":         round(news_raw, 4),
            "news_factor":      round(news_factor, 4),
            "earnings_tone":    earnings_tone,
            "earnings_factor":  round(earnings_factor, 4),
            "fii_bias":         fii_bias_val,
            "fii_factor":       round(fii_factor, 4),
            "market_support":   round(market_support, 4),
            "final_confidence": final_confidence,
        }

        candidate.reasons.append(
            f"conf_multi:sig={signal_strength:.2f},regime={regime_factor:.2f},"
            f"news={news_factor:.2f},earn={earnings_factor:.2f},fii={fii_factor:.2f}"
            f"→{final_confidence}"
        )

        if final_confidence < settings.AGENT_CONFIDENCE_THRESHOLD:
            reject = f"confidence<threshold:{final_confidence}<{settings.AGENT_CONFIDENCE_THRESHOLD}"
            logger.debug(f"[agent/decision] {symbol} filtered: {reject}")
            return None, reject

        # NSE rule: short selling only allowed intraday (MIS). CNC delivery
        # shorts are rejected by Zerodha / SEBI.
        product = (
            "MIS"
            if candidate.strategy in _MIS_STRATEGIES or candidate.side == "SELL"
            else getattr(settings, "AGENT_DEFAULT_PRODUCT", "CNC")
        )

        decision = AgentDecisionOutput(
            symbol=symbol,
            action=candidate.side,
            confidence=final_confidence,
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
            master_score=raw_master,
            confidence_factors=conf_factors,
        )
        logger.info(
            f"[agent/decision] {symbol} → {candidate.side} | conf={final_confidence}% "
            f"(sig={signal_strength:.2f}×support={market_support:.2f}) | {candidate.strategy}"
        )
        return decision, None

    @staticmethod
    def _regime_factor(side: str, regime: str) -> float:
        """Reduce confidence for counter-trend trades."""
        if side == "BUY"  and regime == "BEAR_TRENDING": return 0.7
        if side == "SELL" and regime == "BULL_TRENDING":  return 0.7
        return 1.0

    @staticmethod
    def _check_conflicts(symbol: str, candidate) -> str:
        """Return conflict reason string if BUY signal conflicts with hub context."""
        bare = symbol.replace(".NS", "")
        try:
            from engine import intelligence_hub as hub

            news_raw      = hub.LAST_NEWS_CONTEXT.scores_by_symbol.get(bare, 0.0) if hub.LAST_NEWS_CONTEXT else 0.0
            earnings_tone = hub.LAST_EARNINGS_CONTEXT.tones_by_symbol.get(bare, "NEUTRAL") if hub.LAST_EARNINGS_CONTEXT else "NEUTRAL"
            fii_bias      = hub.LAST_MACRO_CONTEXT.fii_bias if hub.LAST_MACRO_CONTEXT else 0

            hard: list[str] = []
            if news_raw < -0.3:
                hard.append(f"news_negative({news_raw:.2f})")
            if earnings_tone == "NEGATIVE":
                hard.append("earnings_NEGATIVE")
            if fii_bias <= -1:  # fii_bias < -0.5 → integer equivalent is <= -1
                hard.append(f"fii_bearish({fii_bias})")

            if hard:
                return f"conflict:{','.join(hard)}"

            # Soft check: two or more moderate negatives
            soft: list[str] = []
            if news_raw < 0:
                soft.append("news_mild")
            if earnings_tone in ("CAUTIOUS", "NEGATIVE"):
                soft.append("earnings_cautious")
            if fii_bias < 0:
                soft.append("fii_mild")

            if len(soft) >= 2:
                return f"conflict_soft:{','.join(soft)}"

        except Exception as exc:
            logger.debug(f"[agent/decision] conflict check skipped for {symbol}: {exc}")

        return ""

    @staticmethod
    def _bear_case(candidate, regime: str, macro_bias: int) -> str:
        """Varsity M12: document the opposing case before committing."""
        if candidate.side == "BUY":
            if regime == "BEAR_TRENDING": return "STRONG:buying_into_bear_trend"
            if macro_bias <= -2:          return "STRONG:macro_headwind"
        else:
            if regime == "BULL_TRENDING": return "STRONG:shorting_bull_trend"
            if macro_bias >= 2:           return "STRONG:macro_tailwind"
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
      - Regime restriction passes (HIGH_VOL_RANGE blocked, BEAR+BUY needs reversal)
      - No hard conflict between master_score direction and news/earnings/fii

    Sets candidate.size_factor=0.5 for RANGE/LOW_VOL_RANGE regimes.
    """
    from db.models import MasterIntelligenceScore
    from sqlalchemy import select as _sel
    from engine.agent.strategies.base import TradeCandidate

    threshold = settings.AGENT_CONFIDENCE_THRESHOLD
    cutoff    = datetime.utcnow() - timedelta(hours=2)
    regime    = features.regime

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

    # ── Regime restriction ────────────────────────────────────────────────────
    if regime == "HIGH_VOL_RANGE":
        reason = "regime:HIGH_VOL_RANGE_blocks_all"
        logger.info(f"[hub_override] {symbol} SKIP — {reason}")
        await _log_hub_rejection(symbol, master_score, regime, reason, 0, session)
        return None

    if regime == "BEAR_TRENDING" and side == "BUY":
        # Allow BUY only when a reversal pattern is detected:
        # price closes above EMA20 after having made a new lower low
        reversal = (features.close > features.ema20 and
                    features.low < features.swing_low_20)
        if not reversal:
            reason = "regime:BEAR_TRENDING_no_reversal"
            logger.info(
                f"[hub_override] {symbol} SKIP — {reason} "
                f"(close={features.close:.2f} ema20={features.ema20:.2f})"
            )
            await _log_hub_rejection(symbol, master_score, regime, reason, 0, session)
            return None

    # Respect EQUITY_SHORT_ENABLED flag for SELL signals
    if side == "SELL" and not getattr(settings, "EQUITY_SHORT_ENABLED", False):
        reason = "EQUITY_SHORT_ENABLED=False"
        logger.debug(f"[hub_override] {symbol} SELL skipped — {reason}")
        await _log_hub_rejection(symbol, master_score, regime, reason, 0, session)
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

    # Position size reduction flag for range regimes
    size_factor = 0.5 if regime in ("RANGE", "LOW_VOL_RANGE") else 1.0
    if size_factor < 1.0:
        logger.info(
            f"[hub_override] {symbol} {regime} → size_factor=0.5 (50% position)"
        )

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
        f"regime:{regime}",
    ]
    if size_factor < 1.0:
        reasons.append(f"size_reduced:range_regime")

    # Confidence starts at raw master_score magnitude; fuse() will apply
    # the multiplicative model on top of this.
    confidence = min(int(abs(master_score)), 90)

    logger.info(
        f"[hub_override] {symbol} → {side} | score={master_score:.1f} "
        f"conf={confidence}% | signal={row.signal} | regime={regime} "
        f"| scored_at={row.scored_at.isoformat()}"
    )

    candidate = TradeCandidate(
        symbol=symbol,
        side=side,
        entry=round(entry, 2),
        stop=stop,
        target=target,
        confidence=confidence,
        reasons=reasons,
        strategy="HUB_7FACTOR",
        size_factor=size_factor,
        master_score=master_score,
        regime=row.regime or regime,  # carry real regime through to Telegram alerts
        hub_subscores={
            "technical":   row.technical_score,
            "news":        row.news_score,
            "sector":      row.sector_score,
            "macro":       row.macro_score,
            "earnings":    row.earnings_score,
            "fundamental": row.fundamental_score,
            "options":     row.options_score,
            "signal":      row.signal,
            "regime":      row.regime or regime,
            "reasoning":   row.reasoning or {},
            "scored_at":   row.scored_at.isoformat(),
        },
    )
    return candidate


async def _log_hub_rejection(
    symbol: str,
    master_score: float,
    regime: str,
    drop_reason: str,
    final_confidence: int,
    session,
) -> None:
    """Persist a rejected hub candidate to agent_decisions before dropping."""
    try:
        from db.models import AgentDecision
        db_dec = AgentDecision(
            symbol=symbol,
            action="SKIP",
            confidence=final_confidence,
            regime=regime,
            strategy="HUB_7FACTOR",
            entry=None, stop=None, target=None,
            qty=0,
            risk_pct=0.0,
            reasons=[],
            macro_bias=0,
            fund_score=0,
            skip_reason=drop_reason,
            master_score=master_score,
            confidence_factors=None,
            is_paper=settings.AGENT_PAPER_MODE,
            order_id=None,
        )
        session.add(db_dec)
        await session.commit()
    except Exception as exc:
        logger.debug(f"[hub_override] rejection log failed for {symbol}: {exc}")
