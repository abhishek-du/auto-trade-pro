# Phase 3C (Phase A) — News-Only Strategic Pivot: TECHNICAL Trade-Origination Hard Block

Status: **Implemented and tested.** This is Phase A of the two-phase
migration the user specified for this pivot: hard-block trade authority and
verify zero bypasses now; Phase B (dependency-check and delete/relocate dead
code) is explicitly deferred, not done here.

## 1. Directive

> "Yes. Make the system pure News-Only. Hard-block all independent TECHNICAL
> strategy trade origination, including agent_loop.py equity scans and the
> technical/MIS/swing entry paths in india_tasks.py. Preserve their reusable
> infrastructure only where it serves the News-Only pipeline as context/
> technical validation, risk management, exits, indicators, or market data.
> Do not delete code yet."

This is a real strategic pivot, not a code-quality cleanup: `StrategyFamily.TECHNICAL`
paths were live, actively originating paper trades independent of any news
event throughout Phases 1–3. This change stops that.

## 2. What was verified before touching anything

Given the blast radius (stopping a currently-active trading strategy
family), three things were confirmed by reading the actual code — not
assumed — before implementing:

1. **Exits/stop-losses do not go through `TradeIntent`/`authorize_trade_intent()` at all.**
   `tasks/india_tasks.py::_fast_sl_check()` (the 5-second fast-exit loop) and
   the slower exit path both close positions directly via
   `close_paper_trade()` on `OpenPosition` rows. Confirmed by reading the
   function and its own docstring: *"Does NOT score, does NOT open new
   trades — pure exit."* The hard block below therefore cannot affect
   position exits, stop-loss enforcement, or take-profit execution.
2. **All three `StrategyFamily.TECHNICAL` construction sites are entry-only.**
   `engine/agent/agent_loop.py:929`, `tasks/india_tasks.py:1264`
   (`_india_trade_loop`), and `tasks/india_tasks.py:2054`
   (`_intraday_entry_task`, MIS entries) — each computes position sizing,
   deducts from wallet balance, and increments an `opened`/trade counter.
   None of them close an existing position.
3. **A fourth `authorize_trade_intent()` call site at `tasks/india_tasks.py:2207`
   (the NIFTY MIS option scalp) uses `strategy_family=StrategyFamily.FNO`,
   not `TECHNICAL`.** Confirmed unaffected — the user's directive did not
   name FNO, and it's already gated off by default separately.

## 3. Implementation

A single hard block, placed in `engine/agent/decision_router.py::authorize_trade_intent()`
— the one function every trade-creation call site in the codebase already
funnels through (re-confirmed by the same zero-bypass grep sweep used in
Phase 2: `open_paper_trade()` and `place_real_order()` still have exactly
the same caller shape as before, no new path introduced):

```python
_TECHNICAL_TRADE_ORIGINATION_BLOCKED = True
if _TECHNICAL_TRADE_ORIGINATION_BLOCKED and intent.strategy_family == StrategyFamily.TECHNICAL:
    reason = "TECHNICAL strategy_family trade origination is hard-blocked — the system is News-Only by design. ..."
    result = RoutingResult(outcome=RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN, mode=mode, reason=reason, ...)
    await _log_intent_audit(intent, mode, result, session)
    return AuthorizationResult(approved=False, ...)
```

Placed **before** the "NO EVENT → NO TRADE" check, so it's the very first
thing evaluated for a `TECHNICAL` intent — consistent with the same
principle `event_arbitrage.py`'s existing hard block already uses: a
hardcoded boolean, not a settings flag, because a flag "is reversible by
anyone who flips it without knowing this decision exists."

A new `RoutingOutcome.BLOCKED_TECHNICAL_ORIGIN` value was added for
auditability — distinguishable in `SimulationLog` from every other block
reason (`BLOCKED_NO_EVENT`, `BLOCKED_CONFIDENCE_INTEGRITY`, etc.).

**What was explicitly NOT touched**: no TECHNICAL-family code was deleted.
Technical indicators, the Master Intelligence Hub, `calculate_position_size()`,
`validate_signal()`, `compute_trade_levels()`, and every other piece of
reusable technical infrastructure remain fully alive — they're still used
by the News-Only pipeline itself (SL/TP computation, risk validation) and
by `_verify_canonical_event()`'s downstream-only role. Only the ability to
independently *originate and authorize a TECHNICAL-family trade* is blocked.

## 4. Tests Executed

| Test | Result |
|---|---|
| A `TECHNICAL`-family intent is rejected with `outcome=BLOCKED_TECHNICAL_ORIGIN` | **PASS** |
| An `FNO`-family intent does not trigger this block (reaches its own, separate checks) | **PASS** |
| Full Phase 2 suite (9 tests — EVENT_DRIVEN gate logic) re-run | **PASS, no regressions** |
| Full Phase 3 suite (8 tests — canonical event context binding, tool restriction) re-run | **PASS, no regressions** |
| Static re-verification: exactly 3 `StrategyFamily.TECHNICAL` construction sites exist codebase-wide, all pre-confirmed to route only through `authorize_trade_intent()`/`execute_trade_intent()` | **PASS** |

## 5. Remaining Gaps / Phase B (deferred, not done)

1. **Wasted upstream work.** `agent_loop.py`'s equity scan and
   `india_tasks.py`'s MIS/swing loops still run their full technical
   scoring + `apply_reasoning_gate()` (LLM tool-use, including live
   `news`/`expert_research` calls for these still-unrestricted
   non-canonical-event candidates) before reaching the now-always-blocking
   gate. The trade can never execute, but the LLM calls, live price fetches,
   and RSS/DB queries still happen. Not fixed here — Phase A was scoped to
   the block + verification only, per explicit instruction ("do not delete
   code yet").
2. **`_tool_news()`/`_tool_expert_research()` still exist and are still
   reachable** for these now-permanently-blocked TECHNICAL candidates (since
   `candidate.evidence` is never set for them, Phase 3A's restriction doesn't
   apply). The user's directive frames removing them as conditional — "once
   all production trade-originating paths are gone, unless a surviving
   non-trading consumer is identified" — which requires the Phase B
   dependency-check this report explicitly does not perform. Doing that
   check (is `apply_reasoning_gate()`/`llm_tooluse_candidate()` for TECHNICAL
   candidates now *definitionally* pointless, and is it safe/desirable to
   short-circuit it before the LLM call rather than after) is the next
   concrete piece of work, not done in this pass.
3. **No dead code deleted or relocated.** Per instruction, this stays for
   Phase B, following the same "dependency-verified, don't delete on
   assumption" discipline already used for the Phase 2 dead-code pass.

## 6. Git Commit

Committed to `main` and pushed to `origin/main`.
