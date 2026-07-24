"""PreEventExpectationGapEngine — the orchestrator.

Runs the full independent pipeline for one (symbol, event, cutoff):

  point-in-time snapshot
    → sector operational nowcast
    → expectation gap
    → price-discount analysis
    → relative strength
    → market-regime read
    → transparent score
    → deterministic decision gates
    → PreEventPrediction (LONG / SHORT / WAIT / NO_TRADE, fully audited)

This produces PREDICTIONS ONLY. No trade is created here — execution wiring is a
separate, later phase (Phase 6) and is independently gated. `scan()` is gated by
settings.PRE_EVENT_GAP_ENABLED and fails closed: disabled → [] ; any per-symbol
error is isolated and skipped, never propagated (the News Strategy shares no code
path with this and is unaffected regardless).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger

from engine.pre_event_expectation_gap.types import (
    ScheduledEvent, PreEventPrediction, PreEventDecision, NowcastStatus,
)
from engine.pre_event_expectation_gap.point_in_time import build_snapshot, PointInTimeSnapshot
from engine.pre_event_expectation_gap.discovery import discover_scheduled_events
from engine.pre_event_expectation_gap.sector_adapters import run_nowcast
from engine.pre_event_expectation_gap.expectation import compute_expectation
from engine.pre_event_expectation_gap.price_discount import analyze_price_discount
from engine.pre_event_expectation_gap.relative_strength import compute_relative_strength
from engine.pre_event_expectation_gap.scoring import compute_score
from engine.pre_event_expectation_gap.decision import decide


async def _regime_score(snapshot: PointInTimeSnapshot) -> float:
    """Lightweight, point-in-time market-regime read from Nifty vs its 50-day
    SMA. 0.7 above (long-friendly) / 0.3 below / 0.5 neutral-or-insufficient."""
    try:
        nifty = await snapshot.nifty_candles(limit=60)
        closes = [c.close for c in nifty]   # newest-first
        if len(closes) < 50:
            return 0.5
        latest, sma50 = closes[0], sum(closes[:50]) / 50
        if latest > sma50 * 1.01:
            return 0.7
        if latest < sma50 * 0.99:
            return 0.3
        return 0.5
    except Exception:
        return 0.5


class PreEventExpectationGapEngine:
    """Stateless orchestrator (constructible without args). One instance can
    serve many predict()/scan() calls."""

    async def predict(
        self, symbol: str, event: ScheduledEvent, as_of: datetime, session: AsyncSession,
    ) -> PreEventPrediction:
        snapshot = build_snapshot(symbol, as_of, session, event)

        nowcast = await run_nowcast(symbol, event, as_of, session)
        expectation = await compute_expectation(nowcast, symbol, snapshot)
        price_discount = await analyze_price_discount(snapshot)
        relative_strength = await compute_relative_strength(snapshot)
        regime_score = await _regime_score(snapshot)

        breakdown = compute_score(nowcast, expectation, price_discount, relative_strength, regime_score)
        decision, reason = decide(breakdown, nowcast, expectation, price_discount, relative_strength, event)

        return PreEventPrediction(
            symbol=symbol,
            event=event,
            prediction_cutoff=as_of,
            decision=decision,
            nowcast=nowcast,
            expectation=expectation,
            price_discount=price_discount,
            relative_strength=relative_strength,
            pre_event_score=breakdown.total,
            data_quality_score=breakdown.data_quality_score,
            score_breakdown={"components": breakdown.components, "subscores": breakdown.subscores},
            decision_reason=reason,
        )

    async def scan(
        self,
        session: AsyncSession,
        *,
        universe: list[str] | None = None,
        as_of: datetime | None = None,
        min_days_until: int = 1,
        max_days_until: int = 15,
    ) -> list[PreEventPrediction]:
        """Discover upcoming scheduled events for `universe` and produce a
        prediction for each, as of `as_of` (default: now). Gated + fail-closed."""
        if not settings.PRE_EVENT_GAP_ENABLED:
            logger.debug("[pre_event_gap] scan skipped — PRE_EVENT_GAP_ENABLED is False")
            return []

        cutoff = as_of or datetime.utcnow()
        try:
            events = await discover_scheduled_events(
                session, universe=universe, min_days_until=min_days_until, max_days_until=max_days_until,
            )
        except Exception as exc:
            logger.warning(f"[pre_event_gap] discovery failed, scan returns nothing: {exc}")
            return []

        predictions: list[PreEventPrediction] = []
        for ev in events:
            try:
                predictions.append(await self.predict(ev.symbol, ev, cutoff, session))
            except Exception as exc:
                logger.warning(f"[pre_event_gap] predict failed for {ev.symbol}, skipping: {exc}")

        # Rank by score (most compelling first) for easy review.
        predictions.sort(key=lambda p: p.pre_event_score, reverse=True)
        return predictions


# Module-level singleton for convenience.
_engine = PreEventExpectationGapEngine()


async def predict(symbol: str, event: ScheduledEvent, as_of: datetime, session: AsyncSession) -> PreEventPrediction:
    return await _engine.predict(symbol, event, as_of, session)


async def scan(session: AsyncSession, **kwargs) -> list[PreEventPrediction]:
    return await _engine.scan(session, **kwargs)
