# Phase 2 — Canonical Event Intelligence & News-Only Pipeline Integration

Status: **Implemented and tested.** Not pushed to origin (per explicit instruction).

---

## 1. Executive Summary

Phase 1 (commit `14e2720`) built the central execution gate's "NO EVENT →
NO TRADE" invariant: an `EVENT_DRIVEN` `TradeIntent` must carry a real
`event_id` pointing at a `CausalEvent` row, and the gate re-derives evidence
from that row rather than trusting the caller's own snapshot.

Phase 2 closes the three gaps Phase 1 left open:

1. **Duplicate classification** — `news_discovery_engine.py` (root) and
   `crawler/event_pipeline.py` each independently called `classify_event()`
   on the same real-world headline, producing two disconnected `CausalEvent`
   rows for one event. `news_discovery_engine.py` now checks for an existing
   canonical `CausalEvent` (via headline similarity, reusing the same
   `difflib`/0.5-threshold pattern `DuplicateEventEngine` already used) before
   classifying fresh — one canonical classification, not two independent ones.

2. **Under-enforced evidence drift** — the gate's evidence check only
   rejected a symbol *explicitly* placed in the opposite direction list, had
   no materiality floor, and never checked the trade's own thesis text
   against the canonical event at all. All three gaps are closed in
   `_verify_canonical_event()` (§5).

3. **Second-order candidates could still reach the gate as if they were real
   trades** — the cascade path built a full `TradeIntent`, fetched a live
   price, and computed SL/TP for every knowledge-graph candidate, only to be
   rejected deep in the gate by an unrelated `confidence_source` check. It's
   now blocked immediately and explicitly as `WATCHLIST_ONLY`, keyed on the
   contract's own `second_order_confidence` formula's missing factors — using
   the user's own explicit fallback permission rather than inventing a scoring
   model this codebase doesn't yet have the inputs for (§7).

The self-check the user posed — *"if every technical score, MasterIntelligenceScore,
options score, macro score, and indicator were deleted, could the News-Only
pipeline still produce a valid event candidate from a real CausalEvent?"* —
is answered **yes**: `news_discovery_engine.py::_build_evidence()` produces a
`DecisionEvidence` and a real `CausalEvent.id` from `classify_event()` alone;
`_verify_canonical_event()` checks it against the DB row alone; nothing in
that chain reads a technical/options/macro score. Those scores only ever
enter downstream of a candidate that already has an `event_id` (§8).

## 2. Before vs After Architecture

**Before (end of Phase 1):**

```
crawler/event_pipeline.py::process_latest_events()  ─┐
  classify_event(headline)                            ├─ TWO independent
  → CausalEvent(news_id=<real>)                        │  classifications of
                                                         │  the same news,
news_discovery_engine.py::_build_evidence()             │  never compared
  classify_event(headline, summary)                     │
  → CausalEvent(news_id=None)  ─────────────────────────┘

_verify_canonical_event(): event_id required + DB lookup, but:
  - direction check only caught the OPPOSITE-list case
  - no materiality floor
  - no thesis-vs-canonical check at the gate itself

process_ticker() second-order cascade:
  sector_graph.get_second_order_trades() → full TradeIntent → live price
  fetch → SL/TP compute → execute_trade_intent() → blocked deep inside the
  gate by confidence_source=HARDCODED (works, but wasteful, and logged as
  BLOCKED_CONFIDENCE_INTEGRITY, not WATCHLIST_ONLY)
```

**After (Phase 2):**

```
crawler/event_pipeline.py::process_latest_events()  ──┐
  classify_event(headline) → CausalEvent(news_id=<real>) │ ONE canonical
                                                          │ classification per
news_discovery_engine.py::_build_evidence()              │ real event when
  _find_canonical_event(headline) — checks event_pipeline │ the pipelines
  .py's recent CausalEvent rows (news_id-linked) first    │ overlap
  → match:    reuse canonical row, NO second classify_event() call
  → no match: classify_event(headline, summary) → CausalEvent(news_id=None)

_verify_canonical_event(): event_id required + DB lookup, PLUS:
  - materiality floor for DIRECT candidates (LOW/NONE → blocked outright)
  - direction check requires AFFIRMATIVE presence in the matching list
  - thesis-vs-canonical check via validate_evidence_consistency(), universal
    to every EVENT_DRIVEN intent regardless of caller

process_ticker() second-order cascade:
  sector_graph.get_second_order_trades() → TradeIntent carrying explicit
  relationship_type/relationship_strength/company_exposure/market_confirmation
  (currently all None — sector_graph.py doesn't compute them yet)
  → authorize_trade_intent() checks these FIRST, before confidence_source →
    WATCHLIST_ONLY immediately, correctly labeled, no wasted price/SL-TP work
    saved once execution is known to be impossible*
```//
\* price/SL-TP computation still runs today (see §10, Remaining Gaps) —
`_execute_news_trade()` wasn't restructured to skip it, only the gate outcome
was fixed.

## 3. Canonical Event Flow

```
NewsItem (real headline, source, published_at)
   │
   ▼
classify_event(headline[, summary])           ── the ONE canonical LLM call
   │  EventClassification{category, impact, bullish, confidence, entities}
   ▼
CausalEvent (news_id, event_title=category, country=impact/materiality,
             confidence, bullish_stocks, bearish_stocks, ...)
   │
   ├─ DIRECT path: DecisionEvidence built directly from the SAME
   │  classification → TradeIntent(event_id=CausalEvent.id, evidence=snapshot)
   │
   └─ SECOND_ORDER path: sector_graph.get_second_order_trades() proposes a
      related symbol, citing the SAME event_id, but its own
      relationship_type/strength/exposure/confirmation factors are not yet
      computed → WATCHLIST_ONLY, never a tradeable TradeIntent
   │
   ▼
authorize_trade_intent() / execute_trade_intent()
   │  _verify_canonical_event(): event_id → real CausalEvent row (DB is
   │  authority) → materiality floor → direction-affirmation → thesis-vs-
   │  canonical consistency
   ▼
route_decision() → open_paper_trade() / place_real_order()   (only path in)
   │
   ▼
_log_intent_audit(): SimulationLog row carrying event_id, evidence_ids,
event_directness, event_category, event_direction, event_materiality
```

## 4. Files Changed

| File | Function | Exact change | Why |
|---|---|---|---|
| `autotrade-backend/news_discovery_engine.py` | `_find_canonical_event()` (new) | Queries `CausalEvent` joined to `NewsItem` (last 6h, `news_id` not null), headline-similarity match via `difflib.SequenceMatcher` at the same 0.5 threshold `DuplicateEventEngine` uses. | Rule 1 (no parallel architecture) — reuses the codebase's one existing clustering pattern instead of inventing a new one. Objective 2 (remove duplicate classification). |
| `autotrade-backend/news_discovery_engine.py` | `_build_evidence()` | Calls `_find_canonical_event()` first; on a match, builds `DecisionEvidence` from the existing `CausalEvent` row's own fields (no second `classify_event()` call); on no match, falls through to the original fresh-classification path. | Objective 1/2 — one canonical classification feeds both the trade and the persisted event. |
| `autotrade-backend/news_discovery_engine.py` | `_execute_news_trade()` | Added `extra_factors: dict \| None` param, merged into the built `TradeIntent.extra`. | Lets the second-order caller attach `relationship_type`/`relationship_strength`/`company_exposure`/`market_confirmation` without a parallel data path. |
| `autotrade-backend/news_discovery_engine.py` | `process_ticker()` (second-order cascade block) | Replaced the hardcoded `confidence=80` mock with `confidence=0` (explicitly not a real evaluation, not a plausible-looking fake number), and now passes `extra_factors` from `trade.get(...)` (currently always `None` since `sector_graph.py` doesn't compute them — explicit `None`, not omitted, so the gate's factor check fires). | Objective 3 ("no fake confidence"); Phase 2.3. |
| `autotrade-backend/engine/decision_router.py` | `_verify_canonical_event()` | (a) Added a materiality floor: `event_directness==DIRECT` + canonical materiality `LOW`/`NONE` → blocked outright. (b) Tightened the direction check from "blocked only if in the opposite list" to "blocked unless affirmatively in the matching list." (c) Added a thesis-vs-canonical check reusing `event_classifier.validate_evidence_consistency()` against `intent.extra["reasoning_points"]` + `intent.confidence`, run for every `EVENT_DRIVEN` intent regardless of caller. | Objective 2 (no silent contradiction — `BLOCKED_EVIDENCE_DRIFT`); DIRECT candidate requirement ("materiality must meet the minimum threshold"; "canonical event direction must agree with trade direction"). |
| `autotrade-backend/engine/decision_router.py` | `authorize_trade_intent()` | Added a `SECOND_ORDER`-specific check, positioned before the `confidence_source` check: if any of `relationship_type`/`relationship_strength`/`company_exposure`/`market_confirmation` is `None` in `intent.extra`, return `WATCHLIST_ONLY` immediately. | Objective 4 / second-order requirement — the contract's `second_order_confidence = event_strength × causal_relationship_strength × company_exposure × market_confirmation` formula; per the user's explicit fallback clause, an unimplementable factor blocks rather than defaults. |
| `autotrade-backend/engine/decision_router.py` | `_log_intent_audit()` | Added `event_category`/`event_direction`/`event_materiality` keys (pulled from `intent.evidence`) to the persisted `SimulationLog.data`. | Objective 3 — "every automatic candidate must carry event_category/event_direction/event_materiality"; these existed on the `TradeIntent` object but were not previously persisted to the audit trail. |

`autotrade-backend/engine/event_classifier.py` — **not modified**. Its
`validate_evidence_consistency()` is reused, not reimplemented, satisfying
Rule 1.

## 5. Evidence Integrity

Three independent checks now run inside `_verify_canonical_event()`, in this
order, any one of which fails closed:

1. **Existence** — `event_id` must resolve to a real `CausalEvent` row via
   `session.get()`. A dangling/fake id is rejected (`BLOCKED_NO_EVENT`), not
   silently treated as "no event."
2. **Materiality floor** — for `DIRECT` intents only, canonical materiality
   `LOW`/`NONE` blocks outright, independent of what the caller's own
   evidence snapshot claims.
3. **Direction affirmation** — the caller's claimed direction (from its
   `DecisionEvidence` snapshot) must be affirmatively present in the matching
   `bullish_stocks`/`bearish_stocks` list on the canonical row. Previously, a
   symbol absent from *both* lists passed silently; now it's blocked
   (`BLOCKED_EVIDENCE_DRIFT`).
4. **Thesis-vs-canonical** — `validate_evidence_consistency()` (existing,
   deterministic, keyword+confidence-threshold check) now runs inside the gate
   itself against `intent.extra["reasoning_points"]` and `intent.confidence`,
   using either the caller's evidence snapshot or a canonical-row-derived
   fallback when no snapshot was provided. This makes the protection universal
   — it no longer depends on the caller happening to invoke
   `news_discovery_engine.py`'s specific pre-gate call.

The canonical DB row remains the authority throughout; a caller-supplied
`DecisionEvidence` is only ever compared against it, never trusted in its
place.

## 6. Direct Candidate Flow

`news_discovery_engine.py::process_ticker()` → `llm_tooluse_candidate()`
produces a verdict → `_execute_news_trade()` builds a `TradeIntent` with
`strategy_family=EVENT_DRIVEN`, `event_directness=DIRECT`, the real
`event_id`, `evidence_ids=[str(event_id)]`, and an `evidence` snapshot built
from the same classification that created the `CausalEvent`. `confidence` is
`float(verdict.get("confidence"))` — the LLM's own calculated score, never a
hardcoded stand-in, with `confidence_source=CALCULATED`.

Verified in Test 6 (§9): the persisted `SimulationLog` audit row carries
`event_id`, `evidence_ids`, `event_directness`, `event_category`,
`event_direction`, and `event_materiality` — all six required fields, none
blank.

## 7. Second-Order Candidate Flow

Second-order candidates are **not a separate strategy** — same `TradeIntent`
shape, same gate, `event_directness=SECOND_ORDER` and `strategy="NEWS_CASCADE"`
distinguishing them. The contract's formula is
`second_order_confidence = event_strength × causal_relationship_strength ×
company_exposure × market_confirmation`.

`engine/sector_graph.py::get_second_order_trades()` does not currently
compute `causal_relationship_strength`, `company_exposure`, or
`market_confirmation` for a proposed candidate — confirmed by reading its
output shape (`{"ticker", "action", "reason"}` only). Implementing that
scoring model (real cross-sector causal-strength estimation, live exposure
data, market-confirmation signals) is out of Phase 2's scope per Rule 4 and
was not attempted.

Per the user's own explicit permission — *"if the full scoring system cannot
safely be implemented in Phase 2, keep second-order candidate generation
disabled for execution, preserve the candidate as WATCHLIST_ONLY, do not
create a tradeable TradeIntent"* — `authorize_trade_intent()` now checks for
all four required factors on any `SECOND_ORDER` intent, before any other
check (including the `confidence_source` check that previously did the
blocking incidentally). Missing factor(s) → `WATCHLIST_ONLY`, with the
missing-factor list in the audit metadata. `news_discovery_engine.py`'s
cascade caller passes these four factors through as explicit `None` (not a
fake number, not omitted) via `trade.get(...)`, so the moment
`sector_graph.py` is enhanced to actually compute them, they flow through
without any further gate change.

Today, every second-order candidate lands on `WATCHLIST_ONLY` — second-order
execution is effectively disabled, exactly as the fallback clause specifies,
while the candidate itself remains visible in the audit log rather than
silently discarded.

## 8. Technical Validation Boundary

Confirmed by direct code inspection (not just gate-logic reasoning):

- `_verify_canonical_event()` reads only `CausalEvent`/`DecisionEvidence`
  fields — no technical indicator, score, or signal is read anywhere in it.
- `authorize_trade_intent()`'s equity risk check (`validate_signal()`) runs
  **after** the event/confidence/directness checks, and only
  rejects/permits — it cannot manufacture an `event_id`, flip
  `event_directness`, or alter `intent.action`/`evidence`.
- The zero-bypass sweep (Test 9, §9) confirmed `open_paper_trade()` has
  exactly one caller in the entire codebase — `route_decision()` inside the
  gate itself. No technical-strategy code path (`tasks/india_tasks.py`,
  `engine/agent/agent_loop.py`) calls it directly; both route through
  `execute_trade_intent()`/`authorize_trade_intent()` with
  `strategy_family=TECHNICAL`, which never touches `_verify_canonical_event()`'s
  event checks (short-circuited to `True` for non-`EVENT_DRIVEN` intents) —
  correct, since a pure technical strategy has no event to check, and is not
  claiming one.
- `engine/agent/event_arbitrage.py` (the one place technical/LLM-driven
  "instant" trading historically touched news) remains hard-blocked at
  `evaluate_news_flash()`'s entry (`_NEWS_ONLY_BLOCKS_HUB_ENTRIES = True`,
  Phase 1) — confirmed still in place, not touched in Phase 2 per Rule 4. Its
  dead code path, if ever re-enabled by a future edit, would still be
  correctly blocked by `_verify_canonical_event()` since it constructs its
  `TradeIntent` with no `event_id`.

Self-check answer (§1): the News-Only pipeline's DIRECT path produces a valid
`CausalEvent`-backed candidate using only `classify_event()`'s own output —
no technical/options/macro/MasterIntelligenceScore input appears anywhere
between `NewsItem` and `authorize_trade_intent()`'s approval. Confirmed **yes**.

## 9. Tests Executed

All 9 required tests, run live against the local Postgres DB
(`autotrade_postgres`) with disposable `NewsItem`/`CausalEvent` rows created
and cleaned up per run:

| # | Test | Result |
|---|---|---|
| 1 | No `event_id`/`evidence_ids` on an `EVENT_DRIVEN` intent → blocked | **PASS** — `BLOCKED_NO_EVENT` |
| 2 | Dangling `event_id` (non-existent row) → blocked | **PASS** — `BLOCKED_NO_EVENT`, reason cites "does not reference an existing CausalEvent row" |
| 3 | Evidence snapshot direction not affirmed by canonical row → blocked | **PASS** — evidence-drift reason returned |
| 4 | Evidence snapshot matching canonical row (symbol affirmed, materiality matching, benign thesis) → passes | **PASS** |
| 5 | Downstream thesis ("Strong earnings beat...") contradicting a LOW-materiality canonical event → blocked | **PASS** — `BLOCKED_EVIDENCE_DRIFT` (reason: `"thesis-vs-canonical drift: ..."`, contains "drift" so the outcome-selection logic in `authorize_trade_intent()` correctly labels it `BLOCKED_EVIDENCE_DRIFT`, satisfying "`BLOCKED_EVIDENCE_DRIFT` or `WATCHLIST_ONLY`, never silent contradiction") |
| 6 | Direct candidate traceability — audit row carries `event_id`/`evidence_ids`/`event_directness`/`event_category`/`event_direction`/`event_materiality` | **PASS** — all six fields present and non-empty in the persisted `SimulationLog` row |
| 7 | Second-order candidate missing scoring factors → `WATCHLIST_ONLY`, not executed | **PASS** |
| 8 | Technical-only signal (no event, technical-sounding thesis text) on an `EVENT_DRIVEN` intent → blocked | **PASS** — `BLOCKED_NO_EVENT` |
| 9 | Zero-bypass sweep: every trade-creation function's call sites | **PASS** — see below |

**Test 9 detail** (static grep sweep, not a runtime test):

- `open_paper_trade()` — exactly 1 caller in the whole repo: `engine/decision_router.py:295` (inside the gate's own `route_decision()`).
- `place_real_order()` — 2 callers: `engine/decision_router.py` (gate's own LIVE path) and `engine/agent/execution.py::AgentExecutionManager.execute()`. Both callers of `AgentExecutionManager.execute()` (`agent_loop.py`, `event_arbitrage.py`) call `authorize_trade_intent()` first and check `.approved` before invoking it.
- `open_option_paper_trade()` — 4 callers, all `strategy_family=FNO` (`engine/fno/selection.py`, `engine/fno/strategies_vol.py`, `tasks/india_tasks.py`), out of Phase 2 scope (Rule 4) and correctly outside the `EVENT_DRIVEN`-only "NO EVENT → NO TRADE" invariant by design.
- No call site anywhere constructs a trade from an `EVENT_DRIVEN` intent without first passing through `authorize_trade_intent()`/`execute_trade_intent()`.

Test script retained at
`/tmp/claude-1000/.../scratchpad/phase2_test_suite.py` (session-scoped
scratchpad, not part of the repo) for reference; not committed since it's a
throwaway harness, not a permanent test-suite addition (Rule 4 — Phase 2
wasn't scoped to add new permanent test infrastructure). All assertions
passed on the run dated 2026-07-20.

## 10. Remaining Gaps

Being explicit, not claiming more than what's true:

1. **`news_discovery_engine.py`'s own `CausalEvent` writes still can't
   dedupe against each other.** `_find_canonical_event()` only matches
   against `event_pipeline.py`'s `news_id`-linked rows (the only ones with a
   recoverable headline via the `NewsItem` join). Two near-duplicate
   headlines both arriving through *this* file's own fresh-classification
   path (not `event_pipeline.py`'s) would still each get their own
   `CausalEvent` row. Fixing this needs a headline/title column directly on
   `CausalEvent` — judged not "genuinely necessary" for Phase 2 per Rule 1,
   but flagged here rather than silently assumed handled.
2. **Second-order scoring is disabled, not implemented.** Per the user's own
   fallback clause this is allowed, but it means the "candidate mode, not a
   separate strategy" second-order path is currently inert — every candidate
   lands on `WATCHLIST_ONLY` regardless of how strong the actual cross-sector
   relationship might be, because `sector_graph.py` doesn't yet compute
   `relationship_strength`/`company_exposure`/`market_confirmation`. This is a
   scoring-model build-out, explicitly out of Phase 2 scope.
3. **`_execute_news_trade()` still does a live-price fetch and SL/TP
   computation for second-order candidates before the gate rejects them.**
   The `WATCHLIST_ONLY` outcome is now correct and clearly labeled, but the
   wasted work upstream of the gate wasn't removed — this is a minor
   efficiency gap, not a correctness one, and was left alone to avoid
   restructuring `_execute_news_trade()`'s control flow beyond what Phase 2
   required.
4. **`validate_evidence_consistency()`'s own documented gap persists
   unchanged**: it only checks the LOW/NONE-materiality-vs-high-conviction-
   language case, not HIGH/MEDIUM-materiality events with a mismatched
   bearish/bullish thesis language (direction mismatch there is still only
   caught by the separate, structural bullish_stocks/bearish_stocks check in
   `_verify_canonical_event()`, not by the thesis-text check). Not modified in
   Phase 2 — reused as-is per Rule 1.
5. **The pre-existing `swing_min_hold` timezone bug** (documented in the
   original audit as P0) remains unfixed — explicitly out of Phase 2 scope
   per Rule 4.

None of these gaps allow a trade to execute without a real, canonical
`event_id` — they are precision/completeness gaps in the *second-order*
scoring and *cross-file* dedup coverage, not violations of the core "NO EVENT
→ NO TRADE" invariant, which Test 1/2/8 confirm holds.

## 11. Git Commit

Committed locally as a single Phase 2 commit. **Not pushed to origin**, per
explicit instruction.
