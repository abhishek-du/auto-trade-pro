"""Direct News Strategy — trades DIRECTLY off the already-classified event
materiality/sentiment direction (the same classify_event() output the News
strategy's LLM debate consumes), skipping the LLM-debate/grounding/technical-
confirmation gate in engine.agent.decision_engine.llm_tooluse_candidate
entirely.

Rationale (2026-07-27, user-validated live observation): stocks the event-
intelligence system tags BULLISH (any materiality tier) were moving up, and
BEARISH ones down, live-checked against the market — but the News strategy's
own demand for volume/momentum/breakout confirmation ("no volume
confirmation", "priced in", "overextended") was SKIPPING many of those
correct-direction candidates (CONCOR +8.5%, KFINTECH +8.8% on 2026-07-27,
both skipped by the News strategy despite the news direction being right).

This module captures that raw sentiment-direction signal as its own, much
simpler, smaller-sized, fully ISOLATED trade path:
  - It does NOT replace or modify the News strategy's own decision — both may
    independently act on the same event; the duplicate-open-position guard
    in engine.risk_manager.validate_signal prevents a double entry.
  - It requires a REAL, resolvable canonical CausalEvent (event_id) for the
    ticker — same "NO EVENT -> NO TRADE" discipline every other strategy
    follows, enforced HERE (not the router — StrategyFamily.DIRECT_NEWS
    intentionally skips the EVENT_DRIVEN-specific thesis-consistency check,
    since there is no LLM thesis to check).
  - It requires materiality in DIRECT_NEWS_MIN_MATERIALITY and classification
    confidence >= DIRECT_NEWS_MIN_CONFIDENCE — a strong-enough signal, not
    every headline.
  - It is sized deliberately SMALL (DIRECT_NEWS_RISK_PCT, well below the
    normal 1-2% conviction band) — this is a newer, less-validated-at-scale
    strategy than the News/Pre-Event ones.
  - Every trade is tagged source="Direct News" (distinct from News's
    "AI"/None and Pre-Event's "AI Predict"), so it is clearly attributable in
    the Decision Journal / Trades page and can be evaluated or switched off
    independently via its own 3-flag gate.
"""
from __future__ import annotations

from utils.config import settings
from utils.logger import logger

STRATEGY_ID = "DIRECT_NEWS"
TRADE_SOURCE = "Direct News"


async def _size_direct_news_position(entry_price: float, stop_loss: float, session) -> dict | None:
    """Deliberately conservative, fixed risk fraction (DIRECT_NEWS_RISK_PCT) —
    independent of the standard confidence-scaled conviction band other
    strategies use, since this strategy runs without the LLM-debate
    confirmation layer. Same shape as risk_manager.calculate_position_size()'s
    return dict so it plugs into TradeIntent.position_size_hint directly."""
    from paper_trading.virtual_wallet import VirtualWallet

    summary = await VirtualWallet.get_summary(session)
    balance = float(summary["balance"])

    risk_frac = float(getattr(settings, "DIRECT_NEWS_RISK_PCT", 0.5)) / 100.0
    risk_amount = balance * risk_frac
    risk_per_unit = abs(entry_price - stop_loss)
    if risk_per_unit <= 0 or balance <= 0:
        return None

    units = int(risk_amount / risk_per_unit)

    # Same hard cap every other strategy respects — one position never
    # exceeds AGENT_MAX_POSITION_WEIGHT of balance regardless of stop distance.
    max_weight = float(getattr(settings, "AGENT_MAX_POSITION_WEIGHT", 0.05))
    max_notional = balance * max_weight
    if units * entry_price > max_notional:
        units = int(max_notional / entry_price)

    if units <= 0:
        return None

    usd_value = round(units * entry_price, 2)
    return {
        "units": units, "usd_value": usd_value,
        "risk_amount": round(risk_amount, 2), "risk_percent": round(risk_frac * 100, 2),
    }


async def maybe_direct_trade(ticker: str, side: str, event_id: int | None, evidence, headline: str) -> bool:
    """Given an ALREADY-CLASSIFIED news event (evidence/event_id from the same
    pipeline news_discovery_engine.process_ticker() uses, right after
    _build_evidence() succeeds), independently decide whether this qualifies
    for a direct sentiment-based trade, and if so, route it through the
    central execution gate. Returns True only if a position was opened.

    Fully isolated from process_ticker()'s own TAKE/SKIP decision — called
    BEFORE the LLM debate, never blocks on it and is never blocked by it.
    Any exception here is swallowed (logged) so a bug in this newer strategy
    can never take down news processing for the primary News strategy.
    """
    try:
        if not getattr(settings, "DIRECT_NEWS_ENABLED", False):
            return False
        if event_id is None or evidence is None:
            return False

        materiality = (getattr(evidence, "materiality", "") or "").upper()
        confidence  = float(getattr(evidence, "confidence", 0.0) or 0.0)
        min_materiality = tuple(getattr(settings, "DIRECT_NEWS_MIN_MATERIALITY", ("HIGH", "MEDIUM")))
        min_confidence  = float(getattr(settings, "DIRECT_NEWS_MIN_CONFIDENCE", 0.65))

        if materiality not in min_materiality:
            return False
        if confidence < min_confidence:
            return False

        direction = (getattr(evidence, "direction", "") or "").upper()
        expected_side = "BUY" if direction == "BULLISH" else ("SELL" if direction == "BEARISH" else None)
        if expected_side is None or expected_side != side:
            # side/direction disagreement means something upstream is
            # inconsistent -- fail closed rather than trade against the
            # classifier's own stated direction.
            logger.debug(
                f"[direct_news] {ticker}: side={side} disagrees with classified "
                f"direction={direction} — skipping"
            )
            return False

        from crawler.market_snapshot import get_market_snapshot
        from news_discovery_engine import _compute_news_trade_levels
        from engine.decision_router import (
            TradeIntent, ConfidenceSource, EventDirectness, StrategyFamily,
            execute_trade_intent, RoutingOutcome,
        )
        from db.database import AsyncSessionLocal

        snap = await get_market_snapshot(ticker)
        entry_price = snap.ltp if snap else None
        if not entry_price or entry_price <= 0:
            logger.debug(f"[direct_news] {ticker}: no live price available — skipping")
            return False

        levels = await _compute_news_trade_levels(ticker, side, entry_price)
        stop_loss, take_profit = levels["stop_loss"], levels["target_1"]

        async with AsyncSessionLocal() as session:
            position_size_hint = await _size_direct_news_position(entry_price, stop_loss, session)
            if position_size_hint is None:
                logger.debug(f"[direct_news] {ticker}: position sizing produced 0 units — skipping")
                return False

            confidence_pct = round(confidence * 100, 1)
            intent = TradeIntent(
                strategy=STRATEGY_ID,
                symbol=ticker, action=side, instrument_type="EQUITY",
                entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
                confidence=confidence_pct, confidence_source=ConfidenceSource.CALCULATED,
                strategy_family=StrategyFamily.DIRECT_NEWS,
                event_directness=EventDirectness.DIRECT, evidence_ids=[str(event_id)],
                event_id=event_id, evidence=evidence,
                position_size_hint=position_size_hint,
                product=("MIS" if side == "SELL" else "CNC"),
                extra={
                    "source": TRADE_SOURCE,
                    "reasoning_points": [
                        f"Direct sentiment/event signal: {headline}",
                        f"materiality={materiality}  classification_confidence={confidence_pct}%",
                        "No LLM debate or technical-confirmation gate — traded directly off "
                        "the classified news direction (isolated strategy; see module docstring).",
                    ],
                },
                target_2=levels["target_2"], atr=levels["atr"],
                confidence_factors={
                    "kind": "direct_news_sentiment",
                    "materiality": materiality,
                    "classification_confidence": confidence_pct,
                    "headline": headline,
                },
            )
            result = await execute_trade_intent(intent, session)

        if result.outcome in (RoutingOutcome.EXECUTED_PAPER, RoutingOutcome.EXECUTED_LIVE):
            logger.warning(
                f"✅ DIRECT-NEWS TRADE OPENED: {ticker} {side} @ ₹{entry_price} "
                f"({result.outcome.value} via {TRADE_SOURCE})"
            )
            return True
        logger.info(f"[direct_news] {ticker} not executed: {result.outcome.value} — {result.reason}")
        return False
    except Exception as exc:
        logger.warning(f"[direct_news] {ticker}: unexpected error, skipping: {exc}")
        return False
