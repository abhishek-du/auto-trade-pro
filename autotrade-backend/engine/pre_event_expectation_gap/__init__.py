"""Pre-Event Expectation Gap strategy — a SEPARATE, PARALLEL trading strategy.

Runs independently of the existing News Strategy. It never depends on the News
Strategy's prediction or decision, produces its own LONG/SHORT/WAIT/NO_TRADE,
and attributes its trades with source="AI Predict" (the News Strategy's are
conceptually "AI"). See the architecture audit / strategy spec for the full
design and the non-negotiable isolation requirements.

Being built in isolated phases. Phase 1 (this commit): foundation only —
strategy identity, decision states, and the structured audit-first types. No
execution wiring, no sector logic yet; all gated OFF by default
(settings.PRE_EVENT_GAP_ENABLED / _PAPER_TRADING / _LIVE_TRADING).
"""
from __future__ import annotations

from engine.pre_event_expectation_gap.types import (
    STRATEGY_ID,
    TRADE_SOURCE,
    STRATEGY_VERSION,
    PreEventDecision,
    Direction,
    NowcastStatus,
    PriceDiscountStatus,
    PreEventType,
    ScheduledEvent,
    NowcastResult,
    ExpectationEstimate,
    PriceDiscount,
    RelativeStrength,
    PreEventPrediction,
)

from engine.pre_event_expectation_gap.discovery import discover_scheduled_events
from engine.pre_event_expectation_gap.sector_adapters import (
    run_nowcast, get_adapter, registered_sectors, resolve_strategy_sector,
)
from engine.pre_event_expectation_gap.point_in_time import (
    PointInTimeSnapshot, build_snapshot,
)
from engine.pre_event_expectation_gap.expectation import compute_expectation
from engine.pre_event_expectation_gap.price_discount import analyze_price_discount
from engine.pre_event_expectation_gap.relative_strength import compute_relative_strength
from engine.pre_event_expectation_gap.scoring import compute_score, ScoreBreakdown
from engine.pre_event_expectation_gap.decision import decide
from engine.pre_event_expectation_gap.engine import (
    PreEventExpectationGapEngine, predict, scan,
)
from engine.pre_event_expectation_gap.replay import (
    replay_event, replay_events, evaluate_outcome, compute_replay_verdict,
    CUTOFF_OFFSETS, REACTION_WINDOWS,
)

__all__ = [
    "STRATEGY_ID",
    "TRADE_SOURCE",
    "STRATEGY_VERSION",
    "PreEventDecision",
    "Direction",
    "NowcastStatus",
    "PriceDiscountStatus",
    "PreEventType",
    "ScheduledEvent",
    "NowcastResult",
    "ExpectationEstimate",
    "PriceDiscount",
    "RelativeStrength",
    "PreEventPrediction",
    # Phase 2: discovery + sector nowcasts
    "discover_scheduled_events",
    "run_nowcast",
    "get_adapter",
    "registered_sectors",
    "resolve_strategy_sector",
    # Phase 3: point-in-time + expectation gap + price discount + relative strength
    "PointInTimeSnapshot",
    "build_snapshot",
    "compute_expectation",
    "analyze_price_discount",
    "compute_relative_strength",
    # Phase 4: scoring + decision + orchestrator
    "compute_score",
    "ScoreBreakdown",
    "decide",
    "PreEventExpectationGapEngine",
    "predict",
    "scan",
    # Phase 5: replay / validation
    "replay_event",
    "replay_events",
    "evaluate_outcome",
    "compute_replay_verdict",
    "CUTOFF_OFFSETS",
    "REACTION_WINDOWS",
]
