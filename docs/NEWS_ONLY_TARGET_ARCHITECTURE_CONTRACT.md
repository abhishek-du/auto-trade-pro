# News-Only Target Architecture & Migration Contract

**Date:** 2026-07-20
**Type:** Design contract only. Zero code changes. This document is the frozen specification that future implementation work must be checked against — it does not itself disable, delete, or modify anything.
**Builds on:** `docs/COMPLETE_SYSTEM_DEEP_AUDIT_HINGLISH.md` (verified facts) and `docs/STRATEGY_CONSOLIDATION_REPORT.md` (classification). Reuses today's already-built `TradeIntent`/`DecisionEvidence`/`engine/decision_router.py` infrastructure — this contract extends that schema, it does not replace it with a parallel one.

---

## 1. The Frozen Architecture

```text
                    ┌─────────────────────────┐
                    │   OFFICIAL NEWS/FILING   │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │   CANONICAL EVENT        │
                    │      CausalEvent         │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │  EVENT INTELLIGENCE      │
                    │  materiality + direction │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │   ENTITY GRAPH           │
                    │ direct / second-order    │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ CANDIDATE RANKING         │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ TECHNICAL VALIDATION      │
                    │ timing only               │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │   NEWS TRADE INTENT       │
                    └────────────┬────────────┘
                                 ↓
                    ┌─────────────────────────┐
                    │ CENTRAL EXECUTION GATE    │
                    └────────────┬────────────┘
                                 ↓
                         PAPER / LIVE
```

**Core principle (verbatim, non-negotiable):** News creates the trade thesis. Technicals only decide whether and when to enter. No component downstream of "Technical Validation" may originate a trade — it may only pass or block a candidate that already exists.

---

## 1a. As-Built Flow (updated 2026-07-21, after Phases 1–3C)

The diagram in §1 is the original target sketch and is left unchanged above as
the historical record of intent. Implementation collapsed/renamed a few
stages along the way — "Event Intelligence," "Entity Graph," and "Candidate
Ranking" turned out not to need separate discrete stages; they're subsumed
into `classify_event()`'s single output and the `NewsCandidate` it produces.
"Technical Validation" is not a standalone stage either — it happens *inside*
the LLM's own reasoning loop, as one of several tool calls it makes before
producing a verdict, not as a separate step after it. This is what's actually
running today:

```text
                    ┌──────────────────────────┐
                    │    REAL NEWS / FILING     │
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │      CANONICAL EVENT      │
                    │   CausalEvent — deduped:   │
                    │   an existing recent row   │
                    │   is reused before a new    │
                    │   classify_event() call     │
                    │   is ever made               │
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │      NEWS CANDIDATE       │
                    │  event_id + DecisionEvidence│
                    │  snapshot attached          │
                    └────────────┬─────────────┘
                                 ↓
                    ┌─────────────────────────────────────┐
                    │       LLM TRADEABILITY LOOP           │
                    │  think → call ONE tool → observe →    │
                    │  repeat until decided. Tools:          │
                    │  price_action, market_depth, sector,   │
                    │  macro, fundamentals, options,          │
                    │  intraday_candles, predict_candle.      │
                    │  news / expert_research tools are       │
                    │  structurally REMOVED from the menu —   │
                    │  the canonical event is already given,  │
                    │  the LLM cannot fetch an alternate one.  │
                    └────────────┬─────────────────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │          VERDICT          │
                    │  action, confidence,       │
                    │  bull/bear, thesis,         │
                    │  market_confirmation         │
                    └────────────┬─────────────┘
                                 ↓
                    ┌──────────────────────────┐
                    │  EVIDENCE CONSISTENCY     │
                    │  CHECK (pre-gate)          │
                    │  validate_evidence_        │
                    │  consistency() — blocks     │
                    │  before a TradeIntent is     │
                    │  even built                   │
                    └────────────┬─────────────┘
                                 ↓ survives
                    ┌──────────────────────────┐
                    │        TRADE INTENT       │
                    └────────────┬─────────────┘
                                 ↓
                    ┌─────────────────────────────────────┐
                    │      CENTRAL EXECUTION GATE           │
                    │  canonical-event re-verification       │
                    │  (materiality floor, direction          │
                    │  affirmation, thesis-vs-canonical) →     │
                    │  second-order factor check → confidence  │
                    │  provenance → equity risk/wallet checks   │
                    └────────────┬─────────────────────────┘
                                 ↓
                  ┌──────────────┴───────────────┐
                  ↓                               ↓
             PAPER / LIVE                   WATCHLIST_ONLY
           (approved trade)          (second-order with incomplete
                                       scoring, or speculative —
                                       logged, never executed)
```

**Not shown above, but true today**: `TECHNICAL`-family candidates
(`agent_loop.py`'s equity scan, `india_tasks.py`'s MIS/swing loops) still run
their own scoring + LLM-reasoning-gate pipeline, but are hard-blocked at the
Central Execution Gate before anything can execute — they never reach "Paper
/ Live." `FNO`-family paths are separate and gated off by default. Position
exits/stop-losses bypass this entire diagram — they close positions directly
against `OpenPosition` rows and never construct a `TradeIntent`.

---

## 2. What Qualifies as a News-Driven Trade — Formal Definition

A trade is **news-driven** if and only if it can be traced, without a gap, through this exact chain:

```text
A real NewsItem or filing
    → a CausalEvent (with a real, non-null id)
    → an event classification (category, materiality, direction — all from the SAME
      classification call, not two independent LLM calls interpreting the same headline
      differently)
    → a candidate (DIRECT or SECOND_ORDER, per §4)
    → a TradeIntent carrying that CausalEvent's id and evidence
    → the Central Execution Gate
```

If any link in this chain is missing, synthetic, or independently re-derived by a second, disconnected process, the trade is **not** news-driven — regardless of what its `strategy_family` field claims. `strategy_family=EVENT_DRIVEN` is a label a caller sets; it is not proof. This contract's enforcement mechanism (§5) is what makes the label trustworthy.

---

## 3. Mandatory TradeIntent Schema for Automatic Trades

Extends the existing `TradeIntent` dataclass (`engine/decision_router.py`) — does not replace it. Two new fields, reusing the existing `DecisionEvidence` dataclass (`engine/event_classifier.py`, already built today) rather than flattening its contents into duplicate flat fields:

```python
@dataclass
class TradeIntent:
    # ... existing fields unchanged (strategy, symbol, action, instrument_type,
    #     entry_price, stop_loss, take_profit, confidence, confidence_source,
    #     strategy_family, event_directness, evidence_ids, position_size_hint,
    #     product, extra) ...

    event_id:  str | None = None   # NEW — the CausalEvent.id this trade traces back to.
                                    # None is legal ONLY for the manual-override exception (§6).
    evidence:  "DecisionEvidence | None" = None   # NEW — reuses today's existing dataclass
                                    # (source_type, source_id, title, summary, event_category,
                                    # materiality, direction, confidence, published_at).
                                    # Carries the classification itself, not just a reference to
                                    # it, so the gate can inspect materiality/direction directly
                                    # without a second DB round-trip.
```

`evidence_ids` (already existing) keeps its current meaning — the underlying source document IDs (NewsItem/announcement references) — but must now be non-empty whenever `event_id` is set. `event_id` is the single canonical event; `evidence_ids` are the documents that support it.

**Example, matching the format specified:**

```json
{
  "strategy_family": "EVENT_DRIVEN",
  "event_id": "CE-2026-07-20-001",
  "evidence_ids": ["NSE-ANN-12345", "NEWS-67890"],
  "event_directness": "DIRECT",
  "confidence_source": "CALCULATED",
  "evidence": {
    "event_category": "EARNINGS_BEAT",
    "materiality": "HIGH",
    "direction": "BULLISH",
    "confidence": 0.92
  }
}
```

---

## 4. Direct and Second-Order Candidates — One Strategy, Two Candidate Types

Per the correction: these are **not** two strategies. They are two candidate-generation modes inside `NEWS_EVENT_STRATEGY`, distinguished by `event_directness`, both terminating in the same `TradeIntent` schema and the same gate.

### 4a. Direct event

```text
Evidence → Company → Trade candidate
```

`event_directness=DIRECT`. Confidence comes straight from the event classification + technical validation. Example: PNB profit +3x → PNB candidate.

### 4b. Second-order event

```text
Evidence → Causal relationship → Affected company → Independent validation → Trade candidate
```

`event_directness=SECOND_ORDER`. **Mandatory scoring formula** — replacing today's hardcoded `confidence=80`:

```text
second_order_confidence =
    event_strength
    × causal_relationship_strength
    × company_exposure
    × market_confirmation
```

Where, using infrastructure already in the codebase (per the consolidation report's "reuse, don't rebuild" principle):

| Factor | Source (already exists) |
|---|---|
| `event_strength` | The primary event's own `DecisionEvidence.confidence` × materiality weight (HIGH/MEDIUM/LOW → numeric) |
| `causal_relationship_strength` | **Currently missing** — `sector_graph.py::get_second_order_trades()` must be extended to output a relationship-type + strength (e.g. `{"relationship": "COMPETITOR", "strength": 0.7}`), not free-text `reason` only |
| `company_exposure` | Can reuse `MasterIntelligenceScore.sector_score`/fundamental data — how much of this company's actual business is exposed to the affected sector (a real number, not currently computed anywhere — flagged as new work) |
| `market_confirmation` | `MasterIntelligenceScore.technical_score` — is the market already agreeing with this thesis independently (§7's "context, not trigger" rule applies: this confirms, it does not originate) |

**If any factor cannot be independently calculated:** the candidate does not get a confidence number substituted or defaulted. It is emitted as `WATCHLIST_ONLY` and does not reach the execution gate as a tradeable intent. This is stricter than today's behavior (today: hardcoded 80, gate blocks on `confidence_source=HARDCODED`) — the difference is that today's block is a safety net catching a bad value; the target state is that no bad value is ever produced in the first place.

---

## 5. The Global Invariant

Enforced at the gate, not trusted from callers:

```python
# engine/decision_router.py — authorize_trade_intent(), new check, before all others
if intent.strategy_family == StrategyFamily.EVENT_DRIVEN:
    if not intent.event_id or not intent.evidence_ids:
        return BLOCKED  # "NO EVENT → NO TRADE"
```

**Exception — manual/administrative override.** A human-initiated trade (not an automatic strategy) is not required to carry an `event_id`. This is not a loophole in the invariant; it is a different, explicitly-labeled category. It must use `confidence_source=OVERRIDE` (already defined in today's `ConfidenceSource` enum, currently unused/unimplemented) and must be logged with an explicit human-actor identifier, separately auditable from automatic `EVENT_DRIVEN` trades. No code path today implements this override; until it is built, `OVERRIDE` should continue to be treated as blocked, same as `HARDCODED` — the enum value existing is not the same as the override path being safe to use.

---

## 6. Component Authority Matrix — Who May Create a Trade

| Component | Today | Target authority |
|---|---|---|
| `news_discovery_engine.py` (News Direct) | Creates trades | **ALLOWED** — the only automatic originator |
| `news_discovery_engine.py` + `sector_graph.py` (Cascade) | Creates trades (blocked by gate) | **ALLOWED**, once §4b's scoring exists |
| `event_arbitrage.py` | Disabled, structurally capable | **FORBIDDEN as a separate originator** — merges into News Direct as a fast-path (per consolidation report §3), does not retain its own authority |
| `agent_loop.py` (Equity Hub scan) | Creates trades (no scheduler) | **FORBIDDEN** |
| `india_tasks.py::run_master_intelligence_cycle` | Creates trades (production, ungated) | **FORBIDDEN** |
| `india_tasks.py::_india_trade_loop` | Creates trades | **FORBIDDEN** |
| `india_tasks.py` intraday burst / NIFTY MIS scalp | Creates trades | **FORBIDDEN** |
| `engine/fno/selection.py` (spreads) | Creates trades (flagged off) | **FORBIDDEN** |
| `engine/fno/selection.py::evaluate_portfolio_hedge` | Creates trades (flagged off) | **CONTEXT-ONLY** — relocates under Central Risk, triggered by portfolio state, not an independent evaluation pass. Even here: the hedge action itself still goes through the same `TradeIntent`/gate mechanism, just with `strategy_family=FNO` reserved specifically for risk-triggered hedges, never for speculative F&O entries. |
| `engine/fno/futures.py`, `strategies_vol.py` | Creates trades (flagged off) | **FORBIDDEN** |
| `intelligence_hub.py` (`MasterIntelligenceScore`) | Feeds trade-triggering thresholds directly (§4-9 above) | **CONTEXT/VALIDATION ONLY** — see §7 |
| Technical indicators / `compute_trade_levels` | Already filter-only for News Direct | **FILTER ONLY**, universally — never a trigger, for any strategy |
| `validate_signal` / `RiskManagerAgent` / `check_drawdown_breakers` | Fragmented, strategy-dependent | **UNIFIED, MANDATORY** for every `TradeIntent` regardless of family |
| Central Execution Gate | Gate for 11/12 paths | **THE ONLY DOOR** — target is 1/1, not 11/12 |

**FORBIDDEN, precisely defined:** the component must not call `open_paper_trade`, `open_option_paper_trade`, `open_spread_paper_trade`, `open_future_paper_trade`, `open_iron_condor_paper_trade`, `AgentExecutionManager.execute`, or `place_real_order`, directly or indirectly, under any code path, regardless of feature flags. A feature-flagged-off strategy that still contains a live call to one of these functions is not "safely disabled" — it is disabled *by configuration*, which is reversible by anyone who flips the flag without knowing this contract exists. Genuine disabling means the call site itself is gone or hard-blocked at the function entry (see §8's two-phase process).

---

## 7. Master Intelligence Hub's New Role

**Rule, verbatim:**

```text
Master Intelligence Hub
        ↓
Context / Validation Only
```

Never:

```text
Master Intelligence Hub
        ↓
BUY / SELL
```

Concretely, the 7 sub-scores (`technical`, `news`, `sector`, `macro`, `earnings`, `fundamental`, `options`) may be **read** by:
- §4b's `market_confirmation` factor (second-order candidate scoring)
- The Technical Validation stage (§1's diagram) — confirming a news-triggered candidate isn't fighting an overwhelming opposite technical trend, or is/isn't overextended
- Position sizing (existing `calculate_position_size`, which already uses confidence as an input — this is validation-adjacent, acceptable)

They may **never** be read by anything that independently decides to construct a `TradeIntent` without an `event_id` already in hand. The distinction is causal ordering: a `TradeIntent` must already exist (from a news event) before any Hub score is consulted. A Hub score must never be the reason a `TradeIntent` is constructed in the first place.

`intelligence_hub.py`'s 15-minute scoring cycle itself is unaffected by this contract — it keeps running, keeps writing `MasterIntelligenceScore`. What changes is exclusively *who is allowed to act on the output* and *at what stage of the pipeline*.

---

## 8. Migration Safety Principle — Two-Phase, Not Delete-on-Sight

Per the explicit correction: disabling trade authority and deleting code are separate phases with a verification gate between them.

**Phase A — Disable trade authority.**
For each FORBIDDEN component (§6): remove or hard-block its ability to reach `open_*_paper_trade`/`AgentExecutionManager.execute`/`place_real_order` — e.g., an early-return guard at the function entry (the same pattern already used for `event_arbitrage.py`'s `EVENT_ARBITRAGE_ENABLED` flag today, but as a hard block, not a soft flag someone could re-enable unknowingly). The code, data models, and scoring logic underneath remain intact and untouched. Nothing is deleted in this phase.

**Phase B — Verify, then delete.**
Only after Phase A is live and confirmed (via the acceptance checklist in §9) for a component, run an exhaustive dependency check: does anything else in the codebase import or call this component's *supporting* functions (as opposed to its trade-creation entry point, already neutralized in Phase A)? If the dependency check comes back clean, the component's dead code may be removed. If Phase A revealed the component is providing something else still in use (e.g., `market_scanner.py`'s `MarketShortlist` might still be wanted per §6's Context/Validation carve-out), it moves to **REUSE AS COMPONENT** instead of deletion — this is precisely why the phases are separated: Phase A is reversible and low-risk; Phase B is irreversible and must not be rushed.

This two-phase approach directly prevents the risk named in the correction: disabling 7 paths and later discovering a hidden `open_paper_trade()` call still alive somewhere, because Phase B's dependency check is exhaustive by design, not assumed from the disabling step alone.

---

## 9. Acceptance Criteria — How to Verify the Contract Is Actually Honored

Before any future work is considered "done" against this contract, it must pass all of the following, verified by direct code inspection (grep + read, not assumption):

1. **Zero-bypass sweep:** grep for every trade-creation function (`open_paper_trade`, `open_option_paper_trade`, `open_spread_paper_trade`, `open_future_paper_trade`, `open_iron_condor_paper_trade`, `AgentExecutionManager.execute`, `place_real_order`) across the entire repo. Every call site must sit either inside `engine/decision_router.py` itself, or immediately behind a preceding `authorize_trade_intent()`/`execute_trade_intent()` call whose `TradeIntent` satisfies §5's invariant. This repeats the verification method already used earlier today (Phase 3 of the execution-authority audit) but must now also re-confirm `run_master_intelligence_cycle` specifically, since that was the confirmed bypass.
2. **Invariant enforcement test:** construct a `TradeIntent` with `strategy_family=EVENT_DRIVEN` and `event_id=None` — confirm the gate blocks it (mirrors the live tests already run today for `confidence_source`).
3. **Second-order scoring test:** construct a second-order candidate where one of the four §4b factors cannot be computed — confirm the system emits `WATCHLIST_ONLY`, not a trade with a substituted/default confidence.
4. **Hub-as-context-only test:** confirm no code path exists where a `TradeIntent` is constructed as a direct consequence of a `MasterIntelligenceScore` read, without a prior `event_id` already present in scope.
5. **No orphaned FORBIDDEN authority:** for every component listed FORBIDDEN in §6, confirm its trade-creation entry point is hard-blocked (Phase A), not merely feature-flagged.

---

## 10. Explicitly Forbidden Patterns (for future code review, not just this migration)

```python
# FORBIDDEN — constructing a TradeIntent from a technical/score condition alone
if master_score > threshold:
    intent = TradeIntent(..., strategy_family=StrategyFamily.TECHNICAL, ...)

# FORBIDDEN — second-order candidate with a substituted/default confidence
confidence = 80  # or any other stand-in number
intent = TradeIntent(..., event_directness=EventDirectness.SECOND_ORDER, confidence_source=ConfidenceSource.CALCULATED, ...)
# (this is exactly today's bug — CALCULATED here would be a lie; HARDCODED is honest but should
#  never reach this line in the first place per §4b)

# FORBIDDEN — EVENT_DRIVEN intent with no event_id
intent = TradeIntent(..., strategy_family=StrategyFamily.EVENT_DRIVEN, event_id=None, ...)
# must be caught by §5's gate check even if a caller tries this

# ALLOWED — the only shape an automatic trade may take
intent = TradeIntent(
    strategy_family=StrategyFamily.EVENT_DRIVEN,
    event_id=causal_event.id,
    evidence_ids=[news_item.id, ...],
    evidence=decision_evidence,
    confidence_source=ConfidenceSource.CALCULATED,
    ...
)
```

---

## 11. What This Contract Deliberately Does Not Decide Yet

- The exact numeric weights/formula for §4b's four factors — flagged as new work, not specified here, since specifying it without building `causal_relationship_strength`/`company_exposure` first would be premature.
- Whether `event_arbitrage.py`'s fast-reaction capability becomes a literal code merge or a conceptual one (a new trigger condition inside the existing pipeline vs. moving its file's logic wholesale) — an implementation-time decision, not an architecture-time one.
- The manual-override (`ConfidenceSource.OVERRIDE`) implementation — intentionally deferred; today it should remain blocked, not built, until explicitly requested.

No code has been changed to produce this contract. Awaiting review and explicit go-ahead before any Phase A work begins.
