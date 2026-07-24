"""Phase 5.5 — Evidence funnel (diagnostic instrumentation).

Answers the question the raw "572 predictions, 0 LONG" number cannot: WHERE do
events drop out of the pipeline, and is zero-LONG caused by (A) the strategy
being genuinely restrictive on valid data, or (B) historical-data / sector-
adapter coverage being insufficient?

For every (event, cutoff) it records the FIRST stage that failed (the exclusion
reason) or, if all data was available, the final decision. This REUSES the real
pipeline components — it does not reimplement or weaken any of them, and it does
NOT touch the v0.1 replay validation logic in replay.py.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger
from engine.pre_event_expectation_gap.types import (
    ScheduledEvent, PreEventType, PreEventDecision, NowcastStatus,
)
from engine.pre_event_expectation_gap.point_in_time import build_snapshot
from engine.pre_event_expectation_gap.sector_adapters import (
    resolve_strategy_sector, get_adapter, run_nowcast,
)
from engine.pre_event_expectation_gap.expectation import compute_expectation
from engine.pre_event_expectation_gap.price_discount import analyze_price_discount
from engine.pre_event_expectation_gap.relative_strength import compute_relative_strength
from engine.pre_event_expectation_gap.scoring import compute_score
from engine.pre_event_expectation_gap.decision import decide
from engine.pre_event_expectation_gap.replay import CUTOFF_OFFSETS

# Ordered funnel stages — a prediction that fails one never reaches the next.
FUNNEL_STAGES = (
    "total",
    "valid_event_timestamp",
    "candles_available",
    "sector_resolved",
    "adapter_available",
    "nowcast_available",
    "expectation_anchor_available",
    "price_discount_available",
    "relative_strength_available",
    "reached_decision",
)


@dataclass
class FunnelRecord:
    symbol: str
    event_date: date
    cutoff_offset: int
    as_of: datetime
    reached: dict = field(default_factory=dict)   # stage -> bool
    sector: str | None = None
    decision: str | None = None
    exclusion_reason: str | None = None           # first failing stage, or None if reached a decision


async def funnel_for_prediction(symbol: str, event: ScheduledEvent, as_of: datetime,
                                session: AsyncSession) -> FunnelRecord:
    rec = FunnelRecord(symbol=symbol, event_date=event.event_date,
                       cutoff_offset=(event.event_date - as_of.date()).days, as_of=as_of)
    reached = {s: False for s in FUNNEL_STAGES}
    reached["total"] = True

    # 2. valid event timestamp
    if not event.event_date:
        rec.reached, rec.exclusion_reason = reached, "no_event_date"
        return rec
    reached["valid_event_timestamp"] = True

    snap = build_snapshot(symbol, as_of, session, event)

    # 3. historical candle availability (point-in-time)
    candles = await snap.self_candles(limit=90)
    if not candles:
        rec.reached, rec.exclusion_reason = reached, "no_historical_candles"
        return rec
    reached["candles_available"] = True

    # 4. sector resolution
    sector = resolve_strategy_sector(symbol)
    rec.sector = sector
    if sector is None:
        rec.reached, rec.exclusion_reason = reached, "sector_unresolved"
        return rec
    reached["sector_resolved"] = True

    # 5. adapter availability
    if get_adapter(sector) is None:
        rec.reached, rec.exclusion_reason = reached, f"no_adapter_for_sector:{sector}"
        return rec
    reached["adapter_available"] = True

    # 6/7. nowcast (this is also where fundamental-data availability shows up —
    # an adapter that runs but returns UNAVAILABLE means its inputs were missing)
    nowcast = await run_nowcast(symbol, event, as_of, session)
    if nowcast.status != NowcastStatus.OK:
        note = nowcast.notes[-1] if nowcast.notes else "unavailable"
        rec.reached, rec.exclusion_reason = reached, f"nowcast_unavailable:{note[:60]}"
        return rec
    reached["nowcast_available"] = True

    # 8. expectation anchor
    expectation = await compute_expectation(nowcast, symbol, snap)
    if not expectation.gap_available:
        rec.reached, rec.exclusion_reason = reached, "no_expectation_anchor"
        return rec
    reached["expectation_anchor_available"] = True

    # 9. price discount
    price_discount = await analyze_price_discount(snap)
    if not price_discount.returns:
        rec.reached, rec.exclusion_reason = reached, "no_price_discount_data"
        return rec
    reached["price_discount_available"] = True

    # 10. relative strength
    relative_strength = await compute_relative_strength(snap)
    reached["relative_strength_available"] = relative_strength.vs_nifty is not None

    # 11-13. reached the decision — record it (NOT an exclusion; WAIT/NO_TRADE
    # here are legitimate strategy decisions on complete data)
    from engine.pre_event_expectation_gap.engine import _regime_score
    regime = await _regime_score(snap)
    breakdown = compute_score(nowcast, expectation, price_discount, relative_strength, regime)
    decision, reason = decide(breakdown, nowcast, expectation, price_discount, relative_strength, event)
    reached["reached_decision"] = True
    rec.reached = reached
    rec.decision = decision.value
    rec.exclusion_reason = None if decision == PreEventDecision.LONG else f"decision:{decision.value}:{reason[:60]}"
    return rec


async def run_evidence_funnel(
    events: list[tuple[str, date, PreEventType]], session: AsyncSession,
) -> dict:
    """Full funnel over (symbol, event_date, event_type) at all cutoffs."""
    records: list[FunnelRecord] = []
    for symbol, ev_date, ev_type in events:
        for offset in CUTOFF_OFFSETS:
            as_of = datetime.combine(ev_date - timedelta(days=offset), datetime.min.time())
            ev = ScheduledEvent(symbol=symbol, event_type=ev_type, event_date=ev_date,
                                event_confidence=0.9, source="funnel")
            try:
                records.append(await funnel_for_prediction(symbol, ev, as_of, session))
            except Exception as exc:
                logger.debug(f"[pre_event_gap/funnel] {symbol}@{ev_date}-{offset}d failed: {exc}")

    # Stage pass-counts (how many predictions reached each stage).
    stage_counts = {s: sum(1 for r in records if r.reached.get(s)) for s in FUNNEL_STAGES}
    # Exclusion-reason distribution (grouped by prefix before ':' for readability).
    reason_dist = Counter(
        (r.exclusion_reason.split(":")[0] if r.exclusion_reason else "reached_LONG")
        for r in records
    )
    decision_dist = Counter(r.decision for r in records if r.decision)
    sector_dist = Counter(r.sector for r in records if r.sector)

    long_ct = sum(1 for r in records if r.decision == PreEventDecision.LONG.value)
    wait_ct = sum(1 for r in records if r.decision == PreEventDecision.WAIT.value)
    no_trade_ct = sum(1 for r in records if r.decision == PreEventDecision.NO_TRADE.value)

    total = len(records)
    data_exclusions = sum(1 for r in records
                          if r.exclusion_reason and not r.exclusion_reason.startswith(("decision", "reached")))
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "total_predictions": total,
        "stage_pass_counts": stage_counts,
        "exclusion_reason_distribution": dict(reason_dist.most_common()),
        "sector_distribution": dict(sector_dist.most_common()),
        "decision_distribution": dict(decision_dist.most_common()),
        "long_count": long_ct,
        "wait_count": wait_ct,
        "no_trade_count": no_trade_ct,
        "data_exclusion_rate": round(data_exclusions / total, 3) if total else None,
        # verdict on the A-vs-B question, from the numbers themselves
        "diagnosis": _diagnose(stage_counts, reason_dist, total),
    }


def _diagnose(stage_counts: dict, reason_dist: Counter, total: int) -> str:
    if not total:
        return "no predictions — nothing to diagnose"
    reached_nowcast = stage_counts.get("nowcast_available", 0)
    reached_decision = stage_counts.get("reached_decision", 0)
    # If most events die BEFORE producing a nowcast, it's a COVERAGE problem (B).
    if reached_nowcast / total < 0.2:
        top = reason_dist.most_common(1)[0][0] if reason_dist else "unknown"
        return (f"COVERAGE-LIMITED (B): only {reached_nowcast}/{total} predictions produced a nowcast; "
                f"dominant drop-out = '{top}'. Zero-LONG is driven by missing sector adapters / "
                f"fundamental data, not by strategy restrictiveness.")
    # If plenty reach the decision but still no LONG, the strategy is restrictive (A) on valid data.
    if reached_decision / total >= 0.2:
        return (f"STRATEGY-LIMITED (A): {reached_decision}/{total} predictions reached a decision on "
                f"complete data; the gates are the binding constraint, not coverage.")
    return (f"MIXED: {reached_nowcast}/{total} produced a nowcast, {reached_decision}/{total} reached a "
            f"decision — both coverage and gate factors contribute.")
