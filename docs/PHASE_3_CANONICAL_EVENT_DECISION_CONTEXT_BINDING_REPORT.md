# Phase 3 — Canonical Event → Decision Context Binding

Status: **Implemented and tested.**

Scope: Phase 3A only (structural restriction + context binding), per the
staged "disable → verify → delete" discipline carried over from Phase 2.
Phase 3B (verify all remaining callers of `_tool_news`/`_tool_expert_research`)
and Phase 3C (delete/relocate if nothing legitimate remains) are explicitly
**not** done here — deferred, not silently skipped.

## 1. Why this phase exists

Phase 2 made the central execution gate (`_verify_canonical_event()`) check a
trade's thesis against its canonical `CausalEvent` *after* the LLM produced a
verdict. That catches the specific failure mode already observed (a
LOW-materiality event paired with high-conviction language), but it left the
generation side untouched: `llm_tooluse_candidate()` still had a standing
`news` tool that performs a live, unclassified Google News RSS fetch, and an
`expert_research` tool that queries the raw `NewsItem` table directly — both
completely disconnected from `classify_event()`/`CausalEvent`. The model was
being shown the canonical evidence ("do not contradict") while simultaneously
holding a tool that could fetch a different set of facts and build a thesis
from those instead. The gate was the only thing standing between that and a
live trade — a backstop, not a structural guarantee.

Phase 3A's objective: **remove the LLM's ability to originate an alternate
news thesis for a candidate that already has a canonical event**, so the
gate becomes a defense-in-depth check rather than the sole line of defense.

## 2. What changed

### 2.1 `engine/agent/decision_engine.py::_candidate_context()`

The previous evidence block:

```
📰 VERIFIED NEWS EVIDENCE (structured classification — do not contradict):
Source: ... | Category: ... | Materiality: ... | Direction: ...
Title: ...
Summary: ...
Classifier confidence: ...
```

is replaced with an explicit `CANONICAL_EVENT` block carrying `event_id`,
`event_category`, `materiality`, `direction`, `source_type`, `published_at`,
`title`, `summary`, `classifier_confidence`, followed by instructions that
reframe the model's task from "find the truth" to "given the truth, is this
tradeable":

> The event above is canonical fact, not a lead to investigate further. You
> may NOT search for or substitute a different news event, a different
> cause, or a different affected company. Your job is only to determine: (1)
> does the candidate actually correspond to the company this event concerns,
> (2) is materiality/direction enough to act on now, (3) does market context
> support or argue against it, (4) what are the risks, (5) what confidence do
> you have in *executing* now.

`event_id` is rendered for traceability (so a logged prompt can be tied back
to the exact `CausalEvent` row), not so the model can verify the database —
it can't, and isn't asked to.

This block only renders when `candidate.evidence is not None`. Candidates
with no canonical event (e.g. technical/swing scans from `agent_loop.py`,
`tasks/india_tasks.py` — confirmed by audit that these never set
`.evidence`) see no `CANONICAL_EVENT` block and are unaffected.

### 2.2 `engine/agent/decision_engine.py::llm_tooluse_candidate()`

Structural (not prompt-based) tool removal:

```python
has_canonical_event = getattr(candidate, "evidence", None) is not None
available_tools = {k: v for k, v in _LLM_TOOLS.items()
                    if not (has_canonical_event and k in _NEWS_AUTHORITY_TOOLS)}
```

where `_NEWS_AUTHORITY_TOOLS = ("news", "expert_research")`. When a candidate
already has a canonical event:

- `news` (live Google News RSS fetch) and `expert_research` (raw `NewsItem`
  table query) are absent from the tool menu shown to the model, absent from
  the "core tools" requirement list (swapped for `options`), and absent from
  the markdown tool table in the prompt.
- If the model calls one anyway, the dispatch loop returns `TOOL[news] →
  BLOCKED: not available for this candidate — a canonical event already
  exists ... Independent news search is not permitted` — a clear, actionable
  observation, not a silent no-op or an unhandled fall-through (the previous
  code's `if tool in _LLM_TOOLS` check would have silently done nothing on an
  unrecognized tool name, leaving the model to guess why nothing happened).
- Candidates with no canonical event keep the full 12-tool menu, unchanged.

### 2.3 Output contract (additive)

The decide-output JSON schema gained two fields:

```json
{
  "thesis": "<if a CANONICAL_EVENT was given: state how it justifies this trade, WITHOUT contradicting its category/materiality/direction. If none was given, your general thesis.>",
  "market_confirmation": "POSITIVE" | "NEGATIVE" | "NEUTRAL"
}
```

`bull`/`bear`/`key_risk` were kept, not replaced — `_execute_news_trade()`,
`_log_evidence_gate_audit()`, and `validate_evidence_consistency()` all
already consume `bull`, and rewriting that contract wholesale (as an earlier
sketch proposed) was explicitly scoped out for this phase per your own
guidance: *"Do not redesign the entire llm_tooluse_candidate() system yet."*

### 2.4 Wiring `thesis` into the existing consistency checks

- `news_discovery_engine.py::_execute_news_trade()` now appends
  `verdict.get("thesis")` to `intent.extra["reasoning_points"]` alongside the
  existing `bull` text — this is what Phase 2's gate-level thesis-vs-canonical
  check (`_verify_canonical_event()` → `validate_evidence_consistency()`)
  reads.
- `engine/event_classifier.py::validate_evidence_consistency()`'s keyword scan
  now joins `bull` **and** `thesis` before checking for unsupported
  high-conviction claims, so a contradiction placed in either field is
  caught. This was the one change to `event_classifier.py` in this phase —
  additive (`bull_text = " ".join(...)` instead of `bull_text =
  verdict.get("bull")`), backward compatible with every existing caller that
  never supplies `thesis`.

## 3. What was deliberately not done (Phase 3B/3C, deferred)

Per your own staged plan:

- `_tool_news`/`_tool_expert_research` are **not deleted**. Audit confirmed
  (`grep`) that both are only ever invoked through `_LLM_TOOLS` inside
  `llm_tooluse_candidate()` — no other caller in the codebase reaches them
  directly. That audit satisfies Phase 3B's question but the actual deletion
  (Phase 3C) is not part of this change, since the tools remain legitimately
  available to non-canonical-event (technical/swing) candidates.
- The decide-output contract was extended, not rewritten — `bull`/`bear` were
  not removed, and `llm_tooluse_candidate()`'s ReAct/tool-menu structure and
  multi-agent-debate framing were not otherwise redesigned.
- No change was made to `_verify_canonical_event()`, `authorize_trade_intent()`,
  or any other part of the Phase 2 gate — Phase 3 strengthens what feeds the
  gate, not the gate itself.

## 4. Tests Executed

All run live, no mocked business logic — only `utils.llm.call_llm_chat` was
monkeypatched (to script a deterministic multi-turn ReAct conversation
without a real, non-deterministic LLM call) for the tool-restriction tests:

| Test | Result |
|---|---|
| `CANONICAL_EVENT` block renders with all required fields (`event_id`, `event_category`, `materiality`, `direction`, `published_at`, `classifier_confidence`, reframing text) when `candidate.evidence` is set | **PASS** |
| No `CANONICAL_EVENT` block rendered when `candidate.evidence is None` | **PASS** |
| Model attempting `news` on a canonical-event candidate receives a `BLOCKED` observation naming the tool and the reason, and `news` never appears in the final `tools_used` list | **PASS** |
| Model can still reach a `decide` verdict (via legitimate tools) after being blocked on `news`, and the verdict carries the new `thesis` field | **PASS** |
| Model on a candidate with **no** canonical event can still call and use `news` normally (`tools_used` includes it) | **PASS** |
| `validate_evidence_consistency()` catches a contradiction placed **only** in `thesis` (empty/neutral `bull`, high-conviction claim in `thesis`, LOW canonical materiality) | **PASS** |
| Full Phase 2 test suite (9 tests) re-run after these changes | **PASS, no regressions** |

## 5. Remaining Gaps

1. **Phase 3B/3C not executed** — `_tool_news`/`_tool_expert_research` still
   exist as live code, available to non-canonical-event candidates. This is
   intentional staging, not an oversight, but it means a future technical
   scan path could still be extended to misuse them; that risk is unchanged
   by this phase.
2. **The model can still write a `thesis` that passes both the prompt
   instruction and the keyword-based consistency check** if it avoids the
   specific high-conviction keyword list and stays under the confidence
   threshold, while still subtly drifting from the canonical event's actual
   cause. Structural tool removal closes the *fetch-an-alternate-source* gap;
   it does not make the keyword-based consistency check itself semantically
   complete. That check's known scope limits (documented in the Phase 2
   report §10.4) are unchanged.
3. **No test exercises a real (non-mocked) LLM call** — the tool-restriction
   tests script `call_llm_chat`'s responses deterministically. This confirms
   the harness enforces the restriction correctly; it does not confirm a real
   model reliably avoids attempting the blocked tools in practice (though the
   BLOCKED-message feedback path means even repeated attempts fail closed,
   not open).

## 6. Git Commit

Committed to `main` and pushed to `origin/main`.
