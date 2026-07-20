"""Decision Router — single source of truth for paper/live routing.

Every trading decision in the system funnels through `route_decision()`.
It decides whether a signal becomes a paper trade or a real Zerodha order
based on a unified confidence gate and the current mode flag.

This is the bridge between:
  - engine/signal_generator.py        (confidence score)
  - engine/agent/                     (agent decisions)
  - paper_trading/trade_simulator.py  (paper mode execution)
  - engine/zerodha_executor.py        (live Zerodha order placement)

Modes (resolved at call time, runtime-mutable via /api/v1/settings):
  - PAPER       — execute virtually, log to paper_trades
  - LIVE        — execute on Zerodha (requires ZERODHA_ENABLED + token valid)
  - DRY_RUN     — log decision only, never execute (used for new strategies)

Single confidence gate (configurable):
  - signal.confidence_score >= TRADE_CONFIDENCE_THRESHOLD

Any caller wanting to place a trade calls `route_decision(signal, session)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger


class TradeMode(str, Enum):
    PAPER   = "PAPER"
    LIVE    = "LIVE"
    DRY_RUN = "DRY_RUN"


class RoutingOutcome(str, Enum):
    EXECUTED_PAPER  = "EXECUTED_PAPER"
    EXECUTED_LIVE   = "EXECUTED_LIVE"
    DRY_RUN_LOGGED  = "DRY_RUN_LOGGED"
    BLOCKED_CONF    = "BLOCKED_LOW_CONFIDENCE"
    BLOCKED_GATE    = "BLOCKED_SAFETY_GATE"
    BLOCKED_NO_TOKEN = "BLOCKED_NO_ZERODHA_TOKEN"
    BLOCKED_DISABLED = "BLOCKED_AGENT_DISABLED"
    BLOCKED_CONFIDENCE_INTEGRITY = "BLOCKED_CONFIDENCE_INTEGRITY"
    BLOCKED_SECOND_ORDER         = "BLOCKED_SECOND_ORDER_CONFIDENCE"
    WATCHLIST_ONLY               = "WATCHLIST_ONLY"
    ERROR           = "ERROR"


# ═══════════════════════════════════════════════════════════════════════════════
# Central Execution Gate — TradeIntent contract
#
# Every strategy that wants to open a trade builds a TradeIntent and calls
# execute_trade_intent(). This exists because an audit of the live trade log
# (2026-07-20) found 10+ independent code paths calling open_paper_trade /
# open_option_paper_trade / AgentExecutionManager.execute directly, each with
# its own ad-hoc confidence threshold (10, 30, 55, ...) and — in two cases
# (news_discovery_engine.py's 2nd-order cascade, event_arbitrage.py's instant
# trade) — a hardcoded confidence number standing in for a real evaluation.
# The gate enforces confidence provenance and event-directness before a
# TradeIntent is even allowed to reach the existing mode/threshold routing
# in route_decision() below.
# ═══════════════════════════════════════════════════════════════════════════════

class ConfidenceSource(str, Enum):
    CALCULATED = "calculated"   # produced by a real scoring/evaluation function
    HARDCODED  = "hardcoded"    # a literal number standing in for an evaluation
    OVERRIDE   = "override"     # explicit manual/admin override (not yet supported — blocked like HARDCODED)


class EventDirectness(str, Enum):
    DIRECT         = "direct"          # company's own filing/result/announcement
    SECOND_ORDER   = "second_order"    # inferred via sector/supplier/competitor graph
    SPECULATIVE    = "speculative"     # pure narrative/thematic inference — never auto-trades
    NOT_APPLICABLE = "n/a"             # non-news strategies (technical/F&O scans)


@dataclass
class TradeIntent:
    strategy:           str                        # e.g. "NEWS_DIRECT", "NEWS_CASCADE"
    symbol:             str
    action:             str                         # BUY | SELL
    instrument_type:    str                         # EQUITY | CE | PE | FUTURE
    entry_price:        float
    stop_loss:          float
    take_profit:        float
    confidence:         float
    confidence_source:  ConfidenceSource
    event_directness:   EventDirectness = EventDirectness.NOT_APPLICABLE
    evidence_ids:       list[str] = field(default_factory=list)
    position_size_hint: dict | None = None
    product:            str = "CNC"
    extra:              dict = field(default_factory=dict)


@dataclass
class RoutingResult:
    outcome:    RoutingOutcome
    mode:       TradeMode
    reason:     str
    order_id:   str | None = None
    pnl:        float | None = None
    metadata:   dict | None = None

    def to_dict(self) -> dict:
        return {
            "outcome":  self.outcome.value,
            "mode":     self.mode.value,
            "reason":   self.reason,
            "order_id": self.order_id,
            "pnl":      self.pnl,
            "metadata": self.metadata or {},
        }


async def resolve_mode(session: AsyncSession | None = None) -> TradeMode:
    """Return the current trade mode by checking runtime config + env flags.

    Resolution priority (highest first):
      1. AGENT_DRY_RUN env flag (always wins)
      2. ZERODHA_PAPER_MODE / PAPER_MODE — DB override via runtime_config
      3. .env defaults
    """
    # 1. Dry run wins everything (used during validation phase)
    if getattr(settings, "AGENT_DRY_RUN", False):
        return TradeMode.DRY_RUN

    # 2. Runtime config DB override
    if session is not None:
        try:
            from utils.runtime_config import RuntimeConfig
            cfg = await RuntimeConfig.load(session)
            db_paper_mode = cfg._get("paper_mode", None)
            if db_paper_mode is not None:
                return TradeMode.PAPER if bool(db_paper_mode) else TradeMode.LIVE
        except Exception as exc:
            logger.debug(f"[decision_router] runtime_config load failed: {exc}")

    # 3. .env defaults — LIVE only when both flags say so
    if (
        settings.PAPER_MODE is False
        and settings.ZERODHA_PAPER_MODE is False
        and settings.ZERODHA_ENABLED is True
    ):
        return TradeMode.LIVE
    return TradeMode.PAPER


def _confidence_threshold(mode: TradeMode) -> float:
    """Unified confidence gate.

    LIVE mode has a tighter gate than PAPER to avoid bad live trades.
    Settings override .env defaults via runtime_config.
    """
    if mode == TradeMode.LIVE:
        return float(getattr(settings, "LIVE_CONFIDENCE_THRESHOLD", 70.0))
    if mode == TradeMode.PAPER:
        return float(getattr(settings, "PAPER_CONFIDENCE_THRESHOLD", 60.0))
    return 0.0   # DRY_RUN doesn't gate


async def route_decision(
    signal:       Any,            # TradingSignal-like (must have symbol, action, confidence, entry_price)
    session:      AsyncSession,
    position_size: dict | None = None,
    source:       str = "signal_engine",
) -> RoutingResult:
    """Route a trading signal to paper or live execution.

    Returns RoutingResult capturing the outcome. NEVER raises — every
    failure is captured as a BLOCKED_* or ERROR outcome with a reason.
    """
    mode = await resolve_mode(session)

    # ── Universal confidence gate ─────────────────────────────────────────────
    # TradingSignal stores this as `confidence`; the legacy alias `confidence_score`
    # was retired in the post-audit cleanup. Keep one getattr so a future dict-like
    # caller (e.g. tests) still works, but stop pretending we don't know the name.
    conf = float(getattr(signal, "confidence", 0) or 0)
    threshold = _confidence_threshold(mode)

    if conf < threshold and mode != TradeMode.DRY_RUN:
        logger.info(
            f"[decision_router] BLOCKED {signal.symbol} {signal.action} "
            f"conf={conf:.1f} < {threshold:.1f} mode={mode.value} source={source}"
        )
        return RoutingResult(
            outcome=RoutingOutcome.BLOCKED_CONF,
            mode=mode,
            reason=f"confidence {conf:.1f} below threshold {threshold:.1f}",
            metadata={"confidence": conf, "threshold": threshold, "source": source},
        )

    # ── DRY_RUN — log only, never execute ─────────────────────────────────────
    if mode == TradeMode.DRY_RUN:
        logger.info(
            f"[decision_router] DRY_RUN {signal.symbol} {signal.action} "
            f"conf={conf:.1f} entry={getattr(signal, 'entry_price', 0):.2f} source={source}"
        )
        await _log_decision_audit(signal, mode, "DRY_RUN_LOGGED", source, session)
        return RoutingResult(
            outcome=RoutingOutcome.DRY_RUN_LOGGED,
            mode=mode,
            reason="dry-run mode — decision recorded, no execution",
            metadata={"confidence": conf, "source": source},
        )

    # ── LIVE — Zerodha execution ──────────────────────────────────────────────
    if mode == TradeMode.LIVE:
        # Verify token validity before attempting live
        from crawler.zerodha_client import get_kite_client
        kite = get_kite_client()
        if not kite.access_token:
            logger.warning(f"[decision_router] LIVE blocked: no Zerodha token")
            return RoutingResult(
                outcome=RoutingOutcome.BLOCKED_NO_TOKEN,
                mode=mode,
                reason="Zerodha access token missing or expired",
                metadata={"source": source},
            )

        try:
            from engine.zerodha_executor import place_real_order
            qty = int(position_size.get("units", 1)) if position_size else 1
            result = await place_real_order(
                symbol=signal.symbol,
                transaction_type=signal.action,
                quantity=qty,
                session=session,
                signal_id=str(getattr(signal, "id", "")),
                confidence=conf,
            )
            order_id = (result or {}).get("order_id")
            outcome = (
                RoutingOutcome.EXECUTED_LIVE if order_id
                else RoutingOutcome.BLOCKED_GATE
            )
            reason = "live order placed" if order_id else (result or {}).get("error", "live order failed")
            logger.info(
                f"[decision_router] LIVE {signal.symbol} {signal.action} "
                f"qty={qty} conf={conf:.1f} → {outcome.value} order_id={order_id}"
            )
            return RoutingResult(
                outcome=outcome, mode=mode, reason=reason, order_id=order_id,
                metadata={"confidence": conf, "source": source},
            )
        except Exception as exc:
            logger.error(f"[decision_router] LIVE error for {signal.symbol}: {exc}")
            return RoutingResult(
                outcome=RoutingOutcome.ERROR, mode=mode,
                reason=f"live execution error: {exc}",
                metadata={"source": source},
            )

    # ── PAPER — simulator execution ───────────────────────────────────────────
    try:
        from paper_trading.trade_simulator import open_paper_trade
        if position_size is None:
            position_size = {"units": 1, "usd_value": getattr(signal, "entry_price", 0) * 1}
        trade = await open_paper_trade(signal, position_size, session)
        order_id = f"PAPER-{trade.id}" if trade else None
        logger.info(
            f"[decision_router] PAPER {signal.symbol} {signal.action} "
            f"qty={position_size.get('units', 1)} conf={conf:.1f} → {order_id}"
        )
        return RoutingResult(
            outcome=RoutingOutcome.EXECUTED_PAPER, mode=mode,
            reason="paper trade opened", order_id=order_id,
            metadata={"confidence": conf, "source": source, "trade_id": trade.id if trade else None},
        )
    except Exception as exc:
        logger.error(f"[decision_router] PAPER error for {signal.symbol}: {exc}")
        return RoutingResult(
            outcome=RoutingOutcome.ERROR, mode=mode,
            reason=f"paper execution error: {exc}",
            metadata={"source": source},
        )


def _intent_to_signal(intent: TradeIntent) -> Any:
    """Build a TradingSignal from a TradeIntent for route_decision()/validate_signal()."""
    from engine.signal_generator import TradingSignal
    return TradingSignal(
        symbol=intent.symbol, timeframe="event", action=intent.action,
        confidence=intent.confidence, entry_price=intent.entry_price,
        stop_loss=intent.stop_loss, take_profit=intent.take_profit,
        pattern_score=0.0, indicator_score=0.0, sentiment_score=0.0,
        final_score=intent.confidence,
        reasoning_points=intent.extra.get("reasoning_points", []),
        regime=intent.extra.get("regime", ""),
    )


async def execute_trade_intent(intent: TradeIntent, session: AsyncSession) -> RoutingResult:
    """Central execution gate. Every strategy must call this instead of
    open_paper_trade / open_option_paper_trade / AgentExecutionManager.execute /
    place_real_order directly.

    Checks, in order:
      1. Confidence provenance — only CALCULATED may auto-execute.
      2. Event-directness tier — SPECULATIVE never auto-trades; SECOND_ORDER
         needs a stricter confidence bar and evidence_ids.
      3. Equity risk validation (validate_signal) when instrument_type == EQUITY.
         F&O intents are not yet supported here — those strategies still call
         their own open_option_paper_trade/open_spread_paper_trade/
         open_future_paper_trade directly (Phase 2 migration).
      4. Existing mode/threshold/execution routing via route_decision().
    """
    mode = await resolve_mode(session)

    if intent.confidence_source != ConfidenceSource.CALCULATED:
        result = RoutingResult(
            outcome=RoutingOutcome.BLOCKED_CONFIDENCE_INTEGRITY, mode=mode,
            reason=(
                f"confidence_source={intent.confidence_source.value} — only 'calculated' "
                f"confidence may auto-execute; a hardcoded/override value is not a real "
                f"evaluation and cannot silently authorize a trade"
            ),
            metadata={"strategy": intent.strategy, "confidence": intent.confidence,
                      "event_directness": intent.event_directness.value},
        )
        logger.warning(
            f"[execution_gate] BLOCKED (confidence integrity) {intent.symbol} {intent.action} "
            f"strategy={intent.strategy} source={intent.confidence_source.value} conf={intent.confidence}"
        )
        await _log_intent_audit(intent, mode, result, session)
        return result

    if intent.event_directness == EventDirectness.SPECULATIVE:
        result = RoutingResult(
            outcome=RoutingOutcome.WATCHLIST_ONLY, mode=mode,
            reason="speculative inferred event — candidate only, not auto-tradable",
            metadata={"strategy": intent.strategy, "confidence": intent.confidence},
        )
        logger.info(f"[execution_gate] WATCHLIST_ONLY {intent.symbol} strategy={intent.strategy}")
        await _log_intent_audit(intent, mode, result, session)
        return result

    if intent.event_directness == EventDirectness.SECOND_ORDER:
        if not intent.evidence_ids:
            result = RoutingResult(
                outcome=RoutingOutcome.BLOCKED_GATE, mode=mode,
                reason="second-order intent has no evidence_ids — cannot verify the originating event",
                metadata={"strategy": intent.strategy},
            )
            await _log_intent_audit(intent, mode, result, session)
            return result
        _min_second_order = float(getattr(settings, "SECOND_ORDER_MIN_CONFIDENCE", 70.0))
        if intent.confidence < _min_second_order:
            result = RoutingResult(
                outcome=RoutingOutcome.BLOCKED_SECOND_ORDER, mode=mode,
                reason=f"second-order candidate conf={intent.confidence:.1f} below stricter bar {_min_second_order:.1f}",
                metadata={"strategy": intent.strategy},
            )
            await _log_intent_audit(intent, mode, result, session)
            return result

    signal = _intent_to_signal(intent)
    position_size = intent.position_size_hint

    if intent.instrument_type == "EQUITY":
        from sqlalchemy import select as _select
        from paper_trading.virtual_wallet import VirtualWallet
        from engine.risk_manager import validate_signal, calculate_position_size
        from db.models import OpenPosition

        summary = await VirtualWallet.get_summary(session)
        balance = summary["balance"]
        open_positions = list((await session.execute(_select(OpenPosition))).scalars().all())
        ok, reason = await validate_signal(signal, balance, open_positions, session)
        if not ok:
            result = RoutingResult(
                outcome=RoutingOutcome.BLOCKED_GATE, mode=mode, reason=reason,
                metadata={"strategy": intent.strategy},
            )
            await _log_intent_audit(intent, mode, result, session)
            return result
        if position_size is None:
            position_size = calculate_position_size(signal, balance)
    elif position_size is None:
        position_size = {"units": 1, "usd_value": intent.entry_price}

    result = await route_decision(signal, session, position_size=position_size, source=intent.strategy)
    await _log_intent_audit(intent, mode, result, session)
    return result


async def _log_intent_audit(
    intent: TradeIntent, mode: TradeMode, result: RoutingResult, session: AsyncSession,
) -> None:
    """Append a row to SimulationLog with the full TradeIntent provenance —
    confidence_source and event_directness are what let this be audited later,
    unlike the plain signal-based _log_decision_audit() below."""
    try:
        from db.models import SimulationLog
        entry = SimulationLog(
            event_type="EXECUTION_GATE",
            symbol=intent.symbol,
            message=f"{result.outcome.value} | {intent.strategy} | mode={mode.value}",
            data={
                "action":            intent.action,
                "instrument_type":   intent.instrument_type,
                "confidence":        intent.confidence,
                "confidence_source": intent.confidence_source.value,
                "event_directness":  intent.event_directness.value,
                "evidence_ids":      intent.evidence_ids,
                "entry":             intent.entry_price,
                "stop_loss":         intent.stop_loss,
                "take_profit":       intent.take_profit,
                "outcome":           result.outcome.value,
                "reason":            result.reason,
                "mode":              mode.value,
                "strategy":          intent.strategy,
            },
            timestamp=datetime.utcnow(),
        )
        session.add(entry)
        await session.commit()
    except Exception as exc:
        logger.debug(f"[execution_gate] audit log failed: {exc}")


async def _log_decision_audit(
    signal:  Any, mode: TradeMode, outcome_str: str,
    source:  str, session: AsyncSession,
) -> None:
    """Append a row to SimulationLog for traceability."""
    try:
        from db.models import SimulationLog
        entry = SimulationLog(
            event_type="DECISION_ROUTER",
            symbol=signal.symbol,
            message=f"{outcome_str} | {source} | mode={mode.value}",
            data={
                "action":     getattr(signal, "action", ""),
                "confidence": float(getattr(signal, "confidence", 0) or 0),
                "entry":      float(getattr(signal, "entry_price", 0)),
                "stop_loss":  float(getattr(signal, "stop_loss", 0)),
                "take_profit": float(getattr(signal, "take_profit", 0)),
                "outcome":    outcome_str,
                "mode":       mode.value,
                "source":     source,
            },
            timestamp=datetime.utcnow(),
        )
        session.add(entry)
        await session.commit()
    except Exception as exc:
        logger.debug(f"[decision_router] audit log failed: {exc}")
