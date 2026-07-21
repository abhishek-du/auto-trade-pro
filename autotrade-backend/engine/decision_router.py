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
    BLOCKED_NO_EVENT             = "BLOCKED_NO_EVENT"              # NO EVENT -> NO TRADE
    BLOCKED_EVIDENCE_DRIFT       = "BLOCKED_EVIDENCE_DRIFT"        # snapshot disagrees with canonical CausalEvent
    BLOCKED_TECHNICAL_ORIGIN     = "BLOCKED_TECHNICAL_ORIGIN"      # News-Only hard-block: TECHNICAL may not originate trades (2026-07-21)
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


class StrategyFamily(str, Enum):
    """Closed classification of WHY a trade exists — orthogonal to `strategy`
    (which free-text names like "NEWS_DIRECT"/"FNO_SPREAD" already encode
    inconsistently). Added per the 2026-07-20 execution-authority audit's
    Phase 4 (event-driven-pipeline-audit.md): required on every TradeIntent so
    performance can later be sliced by "why" a trade exists, not just overall
    P&L — a news-driven trade and a technical-scan trade should never be
    silently pooled together when measuring whether news actually has edge.
    """
    EVENT_DRIVEN = "EVENT_DRIVEN"   # news_discovery_engine.py, event_arbitrage.py
    TECHNICAL    = "TECHNICAL"      # agent_loop.py equity scan, india_tasks.py equity/MIS loops
    FNO          = "FNO"            # engine/fno/* — spreads, futures, straddles, NIFTY MIS options


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
    strategy_family:    StrategyFamily
    event_directness:   EventDirectness = EventDirectness.NOT_APPLICABLE
    evidence_ids:       list[str] = field(default_factory=list)
    position_size_hint: dict | None = None
    product:            str = "CNC"
    extra:              dict = field(default_factory=dict)
    # ── News-Only architecture fields (docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md) ──
    # event_id: the CausalEvent.id this trade traces back to. Mandatory for
    # strategy_family=EVENT_DRIVEN — the gate enforces "NO EVENT -> NO TRADE" (see
    # authorize_trade_intent). None is legal only for TECHNICAL/FNO intents (still
    # gated on confidence/risk, just not on an event).
    event_id:           int | None = None
    # evidence: a caller-provided SNAPSHOT of the classification (for audit/logging
    # convenience) — NOT the authority. The gate re-derives the canonical evidence
    # from the CausalEvent row itself (by event_id) and checks the snapshot against
    # it; a caller cannot get a trade approved by passing a rosier DecisionEvidence
    # than what's actually stored. See _verify_canonical_event() below.
    evidence:           "DecisionEvidence | None" = None


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


@dataclass
class AuthorizationResult:
    approved: bool
    mode:     TradeMode
    reason:   str
    outcome:  RoutingOutcome | None = None   # populated only when approved=False
    signal:   Any = None                     # TradingSignal built from the intent — reuse it, don't rebuild
    balance:  float | None = None            # wallet balance fetched during equity validation — reuse for sizing


async def _verify_canonical_event(intent: TradeIntent, session: AsyncSession) -> tuple[bool, str]:
    """"NO EVENT -> NO TRADE" invariant (News-Only Target Architecture Contract,
    §5/§2). Two checks, not one:

      1. event_id must reference a REAL CausalEvent row. A dangling/fake id is
         rejected, not silently treated as "no event."
      2. The caller's own DecisionEvidence SNAPSHOT (intent.evidence) — if
         provided — must agree with what's actually stored in that row. The
         canonical DB row is the authority; the snapshot is only a convenience
         for audit logging. A caller cannot get a trade approved by attaching a
         rosier DecisionEvidence than what the classifier actually persisted.

    Only enforced for strategy_family == EVENT_DRIVEN — TECHNICAL/FNO intents
    have no event to check (event_id stays None for them, by design).
    """
    if intent.strategy_family != StrategyFamily.EVENT_DRIVEN:
        return True, "not event-driven — no event check required"

    if not intent.event_id or not intent.evidence_ids:
        return False, "NO EVENT -> NO TRADE: event_id/evidence_ids missing for an EVENT_DRIVEN intent"

    from db.models import CausalEvent
    canonical = await session.get(CausalEvent, intent.event_id)
    if canonical is None:
        return False, f"event_id={intent.event_id} does not reference an existing CausalEvent row"

    canonical_materiality = (canonical.country or "").upper()  # country column stores impact/materiality (event_pipeline.py convention)

    # Phase 2 — materiality floor for DIRECT candidates (News-Only Target
    # Architecture Contract, "Direct Candidates" requirements: "materiality
    # must meet the minimum threshold"). A LOW/NONE-materiality event means
    # the classifier itself judged the event as not very meaningful — trading
    # on it contradicts "news creates the thesis." Only applies to DIRECT;
    # SECOND_ORDER has its own stricter WATCHLIST_ONLY path (Phase 2.3) and
    # SPECULATIVE is already WATCHLIST_ONLY-only elsewhere in this function.
    if intent.event_directness == EventDirectness.DIRECT and canonical_materiality in ("LOW", "NONE"):
        return False, (
            f"materiality floor: canonical CausalEvent id={intent.event_id} has "
            f"materiality={canonical_materiality or 'UNSET'} — below the minimum for a DIRECT trade"
        )

    if intent.evidence is not None:
        snapshot_materiality  = (intent.evidence.materiality or "").upper()
        if canonical_materiality and snapshot_materiality and canonical_materiality != snapshot_materiality:
            return False, (
                f"evidence drift: snapshot claims materiality={snapshot_materiality} but canonical "
                f"CausalEvent id={intent.event_id} has materiality={canonical_materiality}"
            )

        # Phase 2 — tightened from "reject only if affirmatively in the
        # OPPOSITE list" to "require affirmative presence in the MATCHING
        # list." The old version let a caller claim a direction the canonical
        # event never actually confirmed for this symbol, as long as it also
        # wasn't confirmed opposite. Direct candidates require canonical
        # confirmation, not merely absence of contradiction.
        bare_symbol = intent.symbol.replace(".NS", "").replace(".BO", "").upper()
        bullish = {s.upper() for s in (canonical.bullish_stocks or [])}
        bearish = {s.upper() for s in (canonical.bearish_stocks or [])}
        snapshot_direction = (intent.evidence.direction or "").upper()
        if (bullish or bearish) and snapshot_direction in ("BULLISH", "BEARISH"):
            if snapshot_direction == "BULLISH" and bare_symbol not in bullish:
                return False, (
                    f"evidence drift: snapshot claims BULLISH for {intent.symbol} but canonical "
                    f"CausalEvent id={intent.event_id} does not list it under bullish_stocks "
                    f"(bullish={sorted(bullish)}, bearish={sorted(bearish)})"
                )
            if snapshot_direction == "BEARISH" and bare_symbol not in bearish:
                return False, (
                    f"evidence drift: snapshot claims BEARISH for {intent.symbol} but canonical "
                    f"CausalEvent id={intent.event_id} does not list it under bearish_stocks "
                    f"(bullish={sorted(bullish)}, bearish={sorted(bearish)})"
                )

    # Phase 2 — thesis-vs-canonical check, moved to the gate itself so it
    # applies to ANY EVENT_DRIVEN TradeIntent, not just ones that happened to
    # go through news_discovery_engine.py's own pre-gate call to
    # validate_evidence_consistency(). Reuses that SAME function (not a
    # reimplementation) by adapting the intent's own reasoning text into the
    # verdict-dict shape it expects. This is what would catch a downstream
    # LLM producing a "Strong earnings beat" thesis against a canonical
    # "routine filing, no financial figures" event, regardless of which
    # caller built the TradeIntent.
    from engine.event_classifier import validate_evidence_consistency, DecisionEvidence as _DE
    thesis_evidence = intent.evidence or _DE(
        source_type="CANONICAL", source_id=str(canonical.id), title="", summary="",
        event_category=canonical.event_title, materiality=canonical_materiality,
        direction="BULLISH" if intent.action == "BUY" else "BEARISH",
        confidence=canonical.confidence,
    )
    thesis_text = " ".join(str(r) for r in (intent.extra or {}).get("reasoning_points", []))
    consistency = validate_evidence_consistency(thesis_evidence, {"bull": thesis_text, "confidence": intent.confidence})
    if not consistency.consistent:
        return False, f"thesis-vs-canonical drift: {consistency.reason}"

    return True, "canonical event verified"


async def authorize_trade_intent(intent: TradeIntent, session: AsyncSession) -> AuthorizationResult:
    """Runs the gate's pass/fail checks WITHOUT executing anything:
      0. "NO EVENT -> NO TRADE" — for EVENT_DRIVEN intents, event_id must
         reference a real, canonical CausalEvent, and any caller-provided
         evidence snapshot must agree with it (gate is the authority, not the
         caller). See _verify_canonical_event().
      1. Confidence provenance — only CALCULATED may auto-execute.
      2. Event-directness tier — SPECULATIVE never auto-trades; SECOND_ORDER
         needs a stricter confidence bar and evidence_ids.
      3. Equity risk validation (validate_signal) when instrument_type == EQUITY.

    Use this directly (instead of execute_trade_intent()) when the caller has
    its own execution mechanics that do more than plain open_paper_trade —
    e.g. AgentExecutionManager, which writes AgentDecision/AgentTrade audit
    tables, has its own idempotency guard, and subscribes the live ticker.
    Call authorize_trade_intent() first; only proceed with your own executor
    if `approved` is True. Rejections are still centrally audit-logged here.
    """
    mode = await resolve_mode(session)

    # ── News-Only architecture hard-block (2026-07-21) ─────────────────────────
    # User-directed strategic pivot: "Make the system pure News-Only. Hard-block
    # all independent TECHNICAL strategy trade origination... The final
    # automatic trade origin must be a canonical CausalEvent-backed EVENT_DRIVEN
    # TradeIntent." TECHNICAL-family trade origination (agent_loop.py's equity
    # scan, tasks/india_tasks.py's MIS/swing entry loops) is blocked HERE, in the
    # one place every trade-creation call site already funnels through (verified
    # by the Phase 2 zero-bypass sweep: open_paper_trade/place_real_order have no
    # caller that reaches them without first passing authorize_trade_intent()) —
    # not via a scattered settings flag in each caller, which "is reversible by
    # anyone who flips it without knowing this decision exists" (the same
    # reasoning event_arbitrage.py's own hard block already used). A hardcoded
    # boolean here is not.
    #
    # This does NOT affect position exits/stop-losses — confirmed by reading
    # tasks/india_tasks.py::_fast_sl_check() and the slower exit path: both close
    # positions directly via close_paper_trade() on OpenPosition rows and never
    # construct a TradeIntent, so they are structurally untouched by this block.
    #
    # TECHNICAL-family logic itself is NOT deleted and keeps serving the
    # News-Only pipeline as context/validation: entry timing, trend confirmation,
    # SL/TP placement, position sizing, risk validation, and market context for
    # EVENT_DRIVEN candidates. It may no longer independently create, authorize,
    # or execute a trade of its own.
    _TECHNICAL_TRADE_ORIGINATION_BLOCKED = True
    if _TECHNICAL_TRADE_ORIGINATION_BLOCKED and intent.strategy_family == StrategyFamily.TECHNICAL:
        reason = (
            "TECHNICAL strategy_family trade origination is hard-blocked — the system is "
            "News-Only by design. Technical signals may inform timing/sizing/risk for an "
            "EVENT_DRIVEN candidate but may not independently originate a trade."
        )
        result = RoutingResult(outcome=RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN, mode=mode, reason=reason,
                                metadata={"strategy": intent.strategy})
        logger.warning(
            f"[execution_gate] BLOCKED (News-Only hard-block) {intent.symbol} {intent.action} "
            f"strategy={intent.strategy}"
        )
        await _log_intent_audit(intent, mode, result, session)
        return AuthorizationResult(approved=False, mode=mode, reason=reason, outcome=result.outcome)

    _event_ok, _event_reason = await _verify_canonical_event(intent, session)
    if not _event_ok:
        _outcome = (RoutingOutcome.BLOCKED_EVIDENCE_DRIFT if "drift" in _event_reason
                    else RoutingOutcome.BLOCKED_NO_EVENT)
        result = RoutingResult(outcome=_outcome, mode=mode, reason=_event_reason,
                                metadata={"strategy": intent.strategy, "event_id": intent.event_id})
        logger.warning(f"[execution_gate] BLOCKED (no-event invariant) {intent.symbol} {intent.action} "
                        f"strategy={intent.strategy} reason={_event_reason}")
        await _log_intent_audit(intent, mode, result, session)
        return AuthorizationResult(approved=False, mode=mode, reason=_event_reason, outcome=result.outcome)

    # Phase 2.3 — second-order candidates are "just another candidate mode,"
    # not a separate strategy (News-Only Target Architecture Contract §4b):
    # second_order_confidence = event_strength x causal_relationship_strength
    # x company_exposure x market_confirmation. sector_graph.py's
    # get_second_order_trades() does not yet compute causal_relationship_strength/
    # company_exposure/market_confirmation for a candidate — per the user's own
    # explicit fallback clause ("if the full scoring system cannot safely be
    # implemented in Phase 2, keep second-order candidate generation disabled
    # for execution, preserve the candidate as WATCHLIST_ONLY, do not create a
    # tradeable TradeIntent"), any second-order intent missing one of these
    # factors is routed to WATCHLIST_ONLY here — before the confidence_source
    # check below — so it can never slip through even if a future caller
    # attaches a genuinely CALCULATED confidence_source to it. No default, no
    # fallback number, no fake confidence: an absent factor blocks, it is
    # never substituted.
    if intent.event_directness == EventDirectness.SECOND_ORDER:
        _required_factors = ("relationship_type", "relationship_strength", "company_exposure", "market_confirmation")
        _missing = [f for f in _required_factors if intent.extra.get(f) is None]
        if _missing:
            reason = (
                f"second-order candidate missing required scoring factor(s) {_missing} — "
                f"cannot compute second_order_confidence; preserved as WATCHLIST_ONLY, not auto-tradable"
            )
            result = RoutingResult(
                outcome=RoutingOutcome.WATCHLIST_ONLY, mode=mode, reason=reason,
                metadata={"strategy": intent.strategy, "missing_factors": _missing, "event_id": intent.event_id},
            )
            logger.info(
                f"[execution_gate] WATCHLIST_ONLY (second-order incomplete scoring) {intent.symbol} "
                f"strategy={intent.strategy} missing={_missing}"
            )
            await _log_intent_audit(intent, mode, result, session)
            return AuthorizationResult(approved=False, mode=mode, reason=reason, outcome=result.outcome)

    if intent.confidence_source != ConfidenceSource.CALCULATED:
        reason = (
            f"confidence_source={intent.confidence_source.value} — only 'calculated' "
            f"confidence may auto-execute; a hardcoded/override value is not a real "
            f"evaluation and cannot silently authorize a trade"
        )
        result = RoutingResult(
            outcome=RoutingOutcome.BLOCKED_CONFIDENCE_INTEGRITY, mode=mode, reason=reason,
            metadata={"strategy": intent.strategy, "confidence": intent.confidence,
                      "event_directness": intent.event_directness.value},
        )
        logger.warning(
            f"[execution_gate] BLOCKED (confidence integrity) {intent.symbol} {intent.action} "
            f"strategy={intent.strategy} source={intent.confidence_source.value} conf={intent.confidence}"
        )
        await _log_intent_audit(intent, mode, result, session)
        return AuthorizationResult(approved=False, mode=mode, reason=reason, outcome=result.outcome)

    if intent.event_directness == EventDirectness.SPECULATIVE:
        reason = "speculative inferred event — candidate only, not auto-tradable"
        result = RoutingResult(
            outcome=RoutingOutcome.WATCHLIST_ONLY, mode=mode, reason=reason,
            metadata={"strategy": intent.strategy, "confidence": intent.confidence},
        )
        logger.info(f"[execution_gate] WATCHLIST_ONLY {intent.symbol} strategy={intent.strategy}")
        await _log_intent_audit(intent, mode, result, session)
        return AuthorizationResult(approved=False, mode=mode, reason=reason, outcome=result.outcome)

    if intent.event_directness == EventDirectness.SECOND_ORDER:
        if not intent.evidence_ids:
            reason = "second-order intent has no evidence_ids — cannot verify the originating event"
            result = RoutingResult(outcome=RoutingOutcome.BLOCKED_GATE, mode=mode, reason=reason,
                                    metadata={"strategy": intent.strategy})
            await _log_intent_audit(intent, mode, result, session)
            return AuthorizationResult(approved=False, mode=mode, reason=reason, outcome=result.outcome)
        _min_second_order = float(getattr(settings, "SECOND_ORDER_MIN_CONFIDENCE", 70.0))
        if intent.confidence < _min_second_order:
            reason = f"second-order candidate conf={intent.confidence:.1f} below stricter bar {_min_second_order:.1f}"
            result = RoutingResult(outcome=RoutingOutcome.BLOCKED_SECOND_ORDER, mode=mode, reason=reason,
                                    metadata={"strategy": intent.strategy})
            await _log_intent_audit(intent, mode, result, session)
            return AuthorizationResult(approved=False, mode=mode, reason=reason, outcome=result.outcome)

    signal  = _intent_to_signal(intent)
    balance = None

    if intent.instrument_type == "EQUITY":
        from sqlalchemy import select as _select
        from paper_trading.virtual_wallet import VirtualWallet
        from engine.risk_manager import validate_signal
        from db.models import OpenPosition

        summary = await VirtualWallet.get_summary(session)
        balance = summary["balance"]
        open_positions = list((await session.execute(_select(OpenPosition))).scalars().all())
        ok, reason = await validate_signal(signal, balance, open_positions, session)
        if not ok:
            result = RoutingResult(outcome=RoutingOutcome.BLOCKED_GATE, mode=mode, reason=reason,
                                    metadata={"strategy": intent.strategy})
            await _log_intent_audit(intent, mode, result, session)
            return AuthorizationResult(approved=False, mode=mode, reason=reason, outcome=result.outcome)

    return AuthorizationResult(approved=True, mode=mode, reason="approved", signal=signal, balance=balance)


async def execute_trade_intent(intent: TradeIntent, session: AsyncSession) -> RoutingResult:
    """Central execution gate for callers happy with generic execution
    semantics (open_paper_trade for PAPER mode). Wraps authorize_trade_intent()
    + route_decision(). Every strategy must call this (or authorize_trade_intent()
    directly, if it needs its own executor) instead of open_paper_trade /
    open_option_paper_trade / AgentExecutionManager.execute / place_real_order
    directly.
    """
    auth = await authorize_trade_intent(intent, session)
    if not auth.approved:
        return RoutingResult(outcome=auth.outcome, mode=auth.mode, reason=auth.reason,
                              metadata={"strategy": intent.strategy})

    position_size = intent.position_size_hint
    if position_size is None:
        if intent.instrument_type == "EQUITY":
            from engine.risk_manager import calculate_position_size
            position_size = calculate_position_size(auth.signal, auth.balance)
        else:
            position_size = {"units": 1, "usd_value": intent.entry_price}

    result = await route_decision(auth.signal, session, position_size=position_size, source=intent.strategy)
    await _log_intent_audit(intent, auth.mode, result, session)
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
                "strategy_family":   intent.strategy_family.value,
                "event_id":          intent.event_id,
                "event_directness":  intent.event_directness.value,
                "evidence_ids":      intent.evidence_ids,
                # Phase 2 §3 (canonical News->Candidate handoff): every automatic
                # candidate must carry event_category/event_direction/event_materiality
                # in addition to event_id/evidence_ids/event_directness — these live on
                # intent.evidence (a DecisionEvidence), not on TradeIntent directly, so
                # they must be pulled out explicitly or this audit trail loses them.
                "event_category":    intent.evidence.event_category if intent.evidence else None,
                "event_direction":   intent.evidence.direction if intent.evidence else None,
                "event_materiality": intent.evidence.materiality if intent.evidence else None,
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
