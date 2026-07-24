"""Core types for the Pre-Event Expectation Gap strategy.

This is a SEPARATE, PARALLEL strategy to the existing News Strategy. It never
imports from or depends on the News Strategy's decision output. See the
strategy spec / architecture audit for the full design.

Phase 1 (foundation): this module defines only the strategy identity, the
decision states, and the structured, audit-first data containers the pipeline
produces. The actual computation (sector nowcasts, expectation gap, price
discount, scoring) lands in later phases — but every prediction, from day one,
is a fully-typed, serializable object so nothing about WHY a decision was made
is ever lost.

Design rules baked into these types:
  * Fail-closed: a NowcastResult can be UNAVAILABLE; an ExpectationEstimate can
    have no consensus. These are first-class states, never silently defaulted
    to a fabricated number.
  * Auditability: PreEventPrediction carries the full component breakdown, not
    just the final LONG/SHORT/WAIT/NO_TRADE.
  * Independence: nothing here references news_discovery_engine, the News
    Strategy's scores, or engine.decision_router. Execution wiring is a later,
    isolated phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum


# ── Strategy identity ────────────────────────────────────────────────────────
# Internal strategy id (persisted in PaperTrade.strategy_name) and the
# high-level SOURCE label (persisted in the new PaperTrade.source column).
# TRADE_SOURCE is intentionally distinct from the News Strategy's conceptual
# "AI" so the two pipelines stay independently attributable.
STRATEGY_ID = "PRE_EVENT_EXPECTATION_GAP"
TRADE_SOURCE = "AI Predict"
STRATEGY_VERSION = "v0.1"


# ── Decision states ──────────────────────────────────────────────────────────

class PreEventDecision(str, Enum):
    """The only four outcomes the strategy may produce. WAIT and NO_TRADE are
    valid, correct decisions — never failures. SHORT is defined here but stays
    disabled for auto-execution in Phase 1 (see the spec's short-side caveat);
    a negative expectation gap should resolve to WAIT/NO_TRADE until the
    short-side is independently validated."""
    LONG     = "LONG"
    SHORT    = "SHORT"
    WAIT     = "WAIT"
    NO_TRADE = "NO_TRADE"


class Direction(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL  = "NEUTRAL"


class NowcastStatus(str, Enum):
    OK          = "OK"
    UNAVAILABLE = "NOWCAST_UNAVAILABLE"   # no reliable sector adapter / insufficient public data


class PriceDiscountStatus(str, Enum):
    NOT_DISCOUNTED       = "NOT_DISCOUNTED"        # underpriced / hasn't moved on the expectation
    MODERATELY_DISCOUNTED = "MODERATELY_DISCOUNTED"
    HEAVILY_DISCOUNTED    = "HEAVILY_DISCOUNTED"
    OVEREXTENDED          = "OVEREXTENDED"          # already ran hard pre-event; poor R:R for a long


# ── Supported event types (Phase 1 is deliberately narrow) ───────────────────

class PreEventType(str, Enum):
    QUARTERLY_RESULT     = "QUARTERLY_RESULT"
    MONTHLY_AUTO_SALES   = "MONTHLY_AUTO_SALES"
    SCHEDULED_BUSINESS_UPDATE = "SCHEDULED_BUSINESS_UPDATE"
    # Future (not implemented in Phase 1): BOARD_MEETING, DIVIDEND, INVESTOR_DAY,
    # CAPACITY_COMMISSIONING, ORDER_MILESTONE, REGULATORY_DECISION, RBI_DECISION, POLICY.


# ── Structured pipeline containers ───────────────────────────────────────────

@dataclass
class ScheduledEvent:
    """A public, scheduled event this strategy may anticipate. Only public
    event-timing information — no UPSI, no private/leaked data, nothing that
    became public only after the prediction cutoff."""
    symbol:           str
    event_type:       PreEventType
    event_date:       date
    event_time:       str | None = None
    event_confidence: float = 0.0          # 0-1 confidence the event happens on this date
    source:           str = ""             # e.g. "market_event_calendar", "nse_board_meeting"
    status:           str = "SCHEDULED"


@dataclass
class NowcastResult:
    """A sector-specific operational nowcast of the likely business outcome,
    from public information only. UNAVAILABLE is a first-class state — a missing
    or unreliable adapter must return UNAVAILABLE, never a fabricated direction."""
    status:            NowcastStatus = NowcastStatus.UNAVAILABLE
    revenue_direction: Direction = Direction.NEUTRAL
    profit_direction:  Direction = Direction.NEUTRAL
    margin_direction:  Direction = Direction.NEUTRAL
    confidence:        float = 0.0         # 0-1
    data_completeness: float = 0.0         # 0-1 — how much of the adapter's inputs were actually available
    sector:            str | None = None
    notes:             list[str] = field(default_factory=list)
    # Optional numeric estimates (fractional, e.g. 0.18 = +18%) an adapter MAY
    # expose so the expectation engine can compute a real gap. `implied_*` is the
    # adapter's recent-trend forward proxy for the pending period; `baseline_*`
    # is the company's established longer-run norm (the "already-expected" level).
    # None when the adapter can't compute them — never fabricated.
    implied_revenue_growth:  float | None = None
    implied_profit_growth:   float | None = None
    baseline_profit_growth:  float | None = None
    # Whether implied_* is a true annual (YoY) rate (needs >=5 point-in-time
    # quarters) or a coarse sequential (QoQ) rate. Lets the expectation engine
    # align dimensions when comparing to the annual 3y-CAGR baseline.
    implied_is_annual:       bool = False

    @property
    def available(self) -> bool:
        return self.status == NowcastStatus.OK


@dataclass
class ExpectationEstimate:
    """Distinguishes, explicitly and separately, the different notions of
    'expected outcome' the spec insists must never be conflated. Any of the
    market-side anchors may be None (unavailable) — the gap is only meaningful
    when at least one credible anchor exists, and `gap_available` says so.

    Anchor semantics are explicit and MUST NOT be mislabelled: a historical
    3-year CAGR is NOT consensus, NOT market expectation, NOT analyst
    expectation. `is_market_expectation` is True ONLY for genuine consensus or
    company guidance — never for the historical baseline."""
    our_expected_pat_growth: float | None = None   # from our nowcast
    consensus_pat_growth:    float | None = None   # public analyst consensus, if any
    company_guidance:        float | None = None   # company's own guidance, if any
    expectation_gap:         float | None = None   # our_expected - best available anchor
    gap_available:           bool = False
    anchor_used:             str | None = None     # mirrors anchor_type (kept for back-compat)
    # ── Explicit anchor semantics (required) ─────────────────────────────────
    anchor_type:             str | None = None     # "CONSENSUS"|"MANAGEMENT_GUIDANCE"|"HISTORICAL_BASELINE_3Y_CAGR"|None
    anchor_value:            float | None = None    # the anchor's raw value (fractional growth)
    anchor_known_at:         str | None = None      # ISO timestamp the anchor was demonstrably known — must be <= as_of
    is_market_expectation:   bool = False           # True ONLY for CONSENSUS/MANAGEMENT_GUIDANCE
    gap_type:                str | None = None       # e.g. "VS_CONSENSUS"|"TREND_VS_HISTORICAL_BASELINE"
    confidence_ceiling:      float | None = None     # cap the overall confidence given this anchor's strength


@dataclass
class PriceDiscount:
    """How much of the anticipated outcome the price appears to have already
    discounted before the event. Pre-event strength is treated as
    price-discovery/positioning, NOT as proof of information leakage."""
    returns:              dict[str, float] = field(default_factory=dict)  # {"1d","3d","5d","10d","20d","60d"}
    rel_strength_nifty:   float | None = None      # stock return - Nifty return over the window
    rel_strength_sector:  float | None = None      # stock return - sector return over the window
    distance_from_high:   float | None = None      # % below recent high (0 = at high)
    abnormal_volume:      bool = False
    status:               PriceDiscountStatus = PriceDiscountStatus.NOT_DISCOUNTED


@dataclass
class RelativeStrength:
    vs_nifty:  float | None = None
    vs_sector: float | None = None
    score:     float = 0.0             # normalized confirmation score, not a standalone signal


@dataclass
class PreEventPrediction:
    """The full, auditable output for one (symbol, event, cutoff). Stored as
    trade metadata for every AI Predict decision — the final decision is NEVER
    stored alone. Mirrors the audit schema in the strategy spec."""
    symbol:             str
    event:              ScheduledEvent
    prediction_cutoff:  datetime
    decision:           PreEventDecision
    # component evidence
    nowcast:            NowcastResult
    expectation:        ExpectationEstimate
    price_discount:     PriceDiscount
    relative_strength:  RelativeStrength
    # scores (0-100 where applicable) + provenance
    pre_event_score:    float = 0.0
    data_quality_score: float = 0.0
    score_breakdown:    dict = field(default_factory=dict)   # component -> contribution, for audit
    decision_reason:    str = ""
    generated_at:       datetime = field(default_factory=datetime.utcnow)
    strategy:           str = STRATEGY_ID
    source:             str = TRADE_SOURCE
    strategy_version:   str = STRATEGY_VERSION

    def to_audit_dict(self) -> dict:
        """Flat, JSON-serializable audit record for trade metadata / logging.
        Enums -> their string values; dates/datetimes -> ISO strings."""
        def _norm(v):
            if isinstance(v, Enum):
                return v.value
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: _norm(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_norm(x) for x in v]
            return v

        return {
            "strategy":              self.strategy,
            "source":                self.source,
            "strategy_version":      self.strategy_version,
            "symbol":                self.symbol,
            "event_type":            self.event.event_type.value,
            "event_date":            self.event.event_date.isoformat(),
            "prediction_cutoff":     self.prediction_cutoff.isoformat(),
            "prediction_direction":  self.decision.value,
            "pre_event_score":       self.pre_event_score,
            "score_breakdown":       _norm(self.score_breakdown),
            "nowcast_status":        self.nowcast.status.value,
            "nowcast_confidence":    self.nowcast.confidence,
            "expectation_gap":       self.expectation.expectation_gap,
            "gap_available":         self.expectation.gap_available,
            "anchor_used":           self.expectation.anchor_used,
            "price_discount_status": self.price_discount.status.value,
            "relative_strength_score": self.relative_strength.score,
            "data_quality_score":    self.data_quality_score,
            "decision_reason":       self.decision_reason,
            "generated_at":          self.generated_at.isoformat(),
            "components": {
                "nowcast":           _norm(asdict(self.nowcast)),
                "expectation":       _norm(asdict(self.expectation)),
                "price_discount":    _norm(asdict(self.price_discount)),
                "relative_strength": _norm(asdict(self.relative_strength)),
            },
        }
