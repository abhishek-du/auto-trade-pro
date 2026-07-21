# Phase 3C — Phase B: Read-Only Dependency & Dead-Code Audit

Status: **Analysis only. No code modified.** Starting point: pushed HEAD
`9f19111` (Phase 3C/Phase A — `StrategyFamily.TECHNICAL` hard-blocked in
`authorize_trade_intent()` via `BLOCKED_TECHNICAL_ORIGIN`).

Every claim below is backed by an actual `grep -rn` or file read performed
during this audit, not inferred from the top-level architecture decision.
Per the governing rule for this phase: **a top-level strategy being blocked
does not make its supporting code dead** — a function is only classified
DELETE if grep shows zero remaining callers anywhere in the repository.

---

## 1. `_tool_news()` / `_tool_expert_research()` — full reachability trace

**Direct callers**: both defined in `engine/agent/decision_engine.py`
(lines 303, 449) and referenced **only** via the `_LLM_TOOLS` dispatch dict
(lines 486, 496). `grep -rn "_tool_news\b|_tool_expert_research\b"` across
the whole repo returns no other reference. Confirmed: these two functions
have exactly one path to being invoked — through `llm_tooluse_candidate()`'s
tool dispatch.

**Reachability of `llm_tooluse_candidate()`** (and its caller
`apply_reasoning_gate()`), traced per call site:

| Call site | Enclosing function | Scheduled/reachable? | Effect of Phase 3C block |
|---|---|---|---|
| `engine/agent/agent_loop.py:793` | `_process_symbol()` | Reachable via `run_agent_cycle()`, exposed at `api/agent.py:130-131` (`POST` endpoint, `force=True`). **Not** on Celery beat (its Celery wrapper `tasks.run_agent_cycle` was deleted in the Phase 2 dead-code pass) — on-demand only, not autonomous. | Trade always rejected downstream (`BLOCKED_TECHNICAL_ORIGIN` at `authorize_trade_intent()`), but `apply_reasoning_gate()` and its LLM/tool calls still run **before** that rejection. |
| `tasks/india_tasks.py:1221` | `_india_trade_loop()` | **Scheduled** — `tasks.india_trade_loop` is in `celery_app.py`'s `beat_schedule` (line 228). Runs autonomously. | Same as above — still runs, trade still gets rejected downstream. |
| `tasks/india_tasks.py:3208` | `run_master_intelligence_cycle()`, inside the `for stock in (scored if not _NEWS_ONLY_BLOCKS_HUB_ENTRIES else []):` loop | **Scheduled** (`tasks.run_master_intelligence_cycle`, beat line 395) — but the loop this call sits inside iterates an **empty list** (`_NEWS_ONLY_BLOCKS_HUB_ENTRIES = True`, hardcoded, added in **Phase 1**, well before this session's Phase 3C work). | **Already unreachable since Phase 1** — this specific `apply_reasoning_gate()` call has never fired since that hard block landed. Confirmed by reading the guard directly (`india_tasks.py:3061-3079`). |

**Conclusion for `_tool_news`/`_tool_expert_research`**: **KEEP — do not
delete.** Two of three `apply_reasoning_gate()` call sites (`agent_loop.py`,
`india_tasks.py:1221`) are still live-executed in production today — one on
a recurring Celery schedule, one via a live API endpoint. Phase 3C only
guarantees the *trade* they're evaluating gets rejected; it does not stop
the scan → score → reasoning-gate → LLM tool-use pipeline from running.
Deleting these two tools now would break live, still-executing code paths
that happen to now always end in a rejected trade — exactly the mistake
this audit was commissioned to prevent.

**Real finding (not proven-dead, but real)**: since the *trade* from these
two paths can never execute again, the LLM tool-use calls they still make
(including live Google RSS fetches and `_tool_news`/`_tool_expert_research`
specifically) are now **pure wasted work** — API calls, LLM tokens, DB
queries that can never produce a trade. This is an **INVESTIGATE** item, not
a DELETE one: the right fix is a short-circuit *before* `apply_reasoning_gate()`
is even called for a `TECHNICAL`-family candidate (check would need to
happen in `agent_loop.py`/`india_tasks.py`, not in `decision_engine.py`
itself, since `apply_reasoning_gate()` has no visibility into
`strategy_family`). Not implemented here — Phase B is read-only.

---

## 2. `StrategyFamily.TECHNICAL` construction sites and their supporting functions

Exactly 3 construction sites exist repo-wide (`grep -rn "StrategyFamily.TECHNICAL"`):

| Site | Enclosing function | Scheduled? |
|---|---|---|
| `engine/agent/agent_loop.py:929` | `_process_symbol()` | On-demand via API (see §1) |
| `tasks/india_tasks.py:1264` | `_india_trade_loop()` | Yes — Celery beat |
| `tasks/india_tasks.py:2054` | `_intraday_entry_task()` | Yes — Celery beat (`tasks.intraday_entry`, line 432) |

All three now unconditionally hit `BLOCKED_TECHNICAL_ORIGIN` inside
`authorize_trade_intent()`, **before** `_verify_canonical_event()` or any
risk/wallet check runs (confirmed by reading the block's placement — first
statement in the function body, before `resolve_mode()`'s result is used for
anything else).

**Functions that exist to feed these three entry points, and their actual
dependency status** (checked individually, not assumed dead as a group):

| Function | Role | Other callers found? | Classification |
|---|---|---|---|
| `engine/signal_generator.py::TradingSignal` (dataclass) | Carries a scored candidate into the gate | **Yes** — `news_discovery_engine.py::_intent_to_signal_for_alert()` builds a `TradingSignal` too, purely for the Telegram alert formatter on News Direct trades | **REUSE** — shared data carrier, not TECHNICAL-only |
| `engine/risk_manager.py::calculate_position_size()` | Position sizing | **Yes** — called generically inside `engine/decision_router.py::execute_trade_intent()` for *any* `EQUITY` intent, including EVENT_DRIVEN news trades that don't supply `position_size_hint` | **REUSE** — required by the News-Only pipeline itself |
| `engine/risk_manager.py::validate_signal()` | Equity risk gate | **Yes** — called unconditionally inside `authorize_trade_intent()` for any `instrument_type == "EQUITY"` intent, `EVENT_DRIVEN` included | **REUSE** |
| `engine/risk_manager.py::compute_trade_levels()` | SL/TP calc | **Yes** — `news_discovery_engine.py::_compute_news_trade_levels()` explicitly reuses this (see its own docstring: "Reuses the same compute_indicators -> compute_trade_levels hierarchy") | **REUSE** |
| `engine/indicators.py::compute_indicators()` | Technical indicator computation | **Yes** — same as above, News Direct's own SL/TP path depends on it | **REUSE** |
| Hub scoring (`engine/intelligence_hub.py`, `MasterIntelligenceScore`) | Master Intelligence Hub scores | **Yes** — referenced by `engine/agent/dynamic_management.py` (exit/stop-loss management), `engine/agent/execution.py::_fetch_hub_scores_for_exits()`, `engine/agent/decision_engine.py` (candidate context `hub_subscores`) | **REUSE** — feeds exits and LLM decision context, not just technical entries |
| `engine/momentum_screener.py` / `engine/breakout_screener.py` (feeding `momentum_discovery`/`breakout_discovery` Celery tasks) | Populate `hub_universe`/`user_watchlist` | Both tasks still scheduled (beat lines ~412, ~423); they inject candidates for Hub *scoring*, not trade origination directly | **INVESTIGATE** — not proven dead (Hub scoring/exit-management still consumes what they populate), but their *only remaining purpose* (surfacing candidates for now-blocked technical entries) needs a product decision before further action. Not classified DELETE without that decision. |
| `_process_symbol()`, `_india_trade_loop()`, `_intraday_entry_task()` themselves | The 3 entry-origination functions | Every trade they attempt to open is now rejected at the gate; the functions themselves are still invoked (scheduled/API-reachable) | **KEEP (as scaffolding), TRADE-ORIGINATION LOGIC INSIDE = now provably dead-end.** These functions do real, still-needed work upstream of the blocked call (scoring, chart_brief building for the reasoning gate, wallet balance checks) — deleting the *function* would also delete that upstream work. Only the specific "build `TradeIntent(strategy_family=TECHNICAL)` and try to execute it" tail of each function is a guaranteed-dead-end today. |

---

## 3. F&O speculative/entry paths vs. the hedge path

`grep -rn "StrategyFamily.FNO"` and `open_option_paper_trade(` callers, traced individually:

| Site | Strategy label | Category | Gated? |
|---|---|---|---|
| `tasks/india_tasks.py:2210` (`_select_and_open_nifty_option`-style scalp) | `NIFTY_MIS_OPTION` | **TRADE ORIGINATION (speculative)** | Yes — `authorize_trade_intent()`, `StrategyFamily.FNO` |
| `engine/fno/strategies_vol.py:104` (`open_long_straddle`) | `FNO_LONG_STRADDLE` | **TRADE ORIGINATION (speculative)** | Yes — `authorize_trade_intent()`, `StrategyFamily.FNO` |
| `engine/fno/selection.py:846` (hedge builder) | `FNO_HEDGE` | **PORTFOLIO HEDGE** — explicit `stop=0.0, target=0.0` ("hedge has no fixed SL/TP"), sized to `FNO_HEDGE_RATIO` (50%) of open equity notional, comment: "protecting ₹X of equity exposure" | Yes — `authorize_trade_intent()`, `StrategyFamily.FNO` |

**None of these were touched by the Phase 3C hard block** — it checks
`strategy_family == StrategyFamily.TECHNICAL` specifically, and all F&O
paths use `StrategyFamily.FNO`. All three remain reachable in code, but
`ENABLE_FNO = False` and `FNO_HEDGE_ENABLED = False` (both default-off in
`utils/config.py`) mean none of them execute in the default configuration
today, independent of anything Phase 3C did.

**Classification**: `FNO_HEDGE` = **KEEP** (explicitly a portfolio
protection mechanism, not a speculative originator — must survive any
future News-Only cleanup regardless of TECHNICAL's fate). `NIFTY_MIS_OPTION`
/ `FNO_LONG_STRADDLE` = **INVESTIGATE** — these are speculative FNO entries,
architecturally the same kind of "independent origination without a news
event" the News-Only contract objects to for equities, but the user's
Phase 3C directive named TECHNICAL specifically and did not extend to FNO;
whether FNO speculative entries should also eventually be blocked is a
product decision outside this audit's scope.

---

## 4. Code unreachable specifically because of `BLOCKED_TECHNICAL_ORIGIN`

Only the **tail** of the 3 TECHNICAL entry functions is unreachable in the
sense of "can never produce a different outcome than rejection":

- `agent_loop.py::_process_symbol()` — from the `TradeIntent(...)` construction
  (line 923) through `authorize_trade_intent()` (line 932) and whatever would
  have followed a `True` approval (the `_executor.execute()` call and its
  post-fill bookkeeping) is dead-end code: `authorize_trade_intent()` now
  always returns `approved=False` for this path. Everything **before** line
  923 (scoring, chart_brief, `apply_reasoning_gate()`) still executes and
  still has side effects (LLM calls, log writes, `_log_skipped_decision()`
  audit rows).
- `tasks/india_tasks.py::_india_trade_loop()` — same shape: `TradeIntent`
  construction (line 1259) through `execute_trade_intent()` (line 1267) is a
  dead end; everything above it still runs.
- `tasks/india_tasks.py::_intraday_entry_task()` — same shape: lines
  2049-2059 (`TradeIntent` + `execute_trade_intent()`) are a dead end;
  candidate scoring, veto filtering, and wallet checks above it still run.
- `tasks/india_tasks.py::run_master_intelligence_cycle()`'s hub-inline entry
  loop (`for stock in (scored if not _NEWS_ONLY_BLOCKS_HUB_ENTRIES else [])`,
  lines ~3072-3220+) — **this was already unreachable before Phase 3C**, via
  the Phase 1 hard block. Phase 3C's `authorize_trade_intent()` change is
  redundant for this specific loop body (it would also be blocked there if
  ever re-enabled, since it too builds no — wait, this path does NOT build a
  `TradeIntent` at all; it calls `executor.execute(decision, session)`
  **directly**, bypassing the gate entirely). See §6 below — this is
  flagged as a **historical bypass that is currently inert only because of
  the Phase 1 empty-list guard**, not because of anything gate-side.

None of these dead-end tails are deletable in isolation without either (a)
deleting the whole enclosing function (which would also remove still-useful
upstream scoring/logging), or (b) restructuring the function to stop before
building the now-always-rejected `TradeIntent` — both are Phase-B-follow-up
implementation work, not this audit's job to perform.

---

## 5. Shared infrastructure the News-Only pipeline itself still needs

Confirmed by direct reference-tracing (not assumption) — these are used by
News Direct / canonical event processing / technical validation / risk
management / exits / SL-TP / market data / the Hub, and must not be touched
by any future TECHNICAL-strategy cleanup:

- `engine/risk_manager.py` (`calculate_position_size`, `validate_signal`,
  `compute_trade_levels`) — **REUSE**, called from the central gate and from
  News Direct's own SL/TP computation.
- `engine/indicators.py::compute_indicators()` — **REUSE**, same reason.
- `paper_trading/virtual_wallet.py::VirtualWallet` — **REUSE**, used by both
  `authorize_trade_intent()`'s equity check and `AgentExecutionManager._paper_execute()`.
- `engine/intelligence_hub.py` / `MasterIntelligenceScore` — **REUSE**, feeds
  exit/stop-loss management (`dynamic_management.py`,
  `_fetch_hub_scores_for_exits()`) and the LLM decision context
  (`_candidate_context()`'s `hub_subscores`) for News candidates too.
- `tasks/india_tasks.py::_fast_sl_check()` and the slower exit path — **EXIT
  MANAGEMENT**, confirmed (per the Phase 3C report) to never construct a
  `TradeIntent` at all; entirely independent of the TECHNICAL-origination
  question.
- `crawler/live_prices.py`, `crawler/zerodha_market.py` (market data) —
  **REUSE**, used throughout News Direct (`_execute_news_trade()`'s live
  price fetch), exits, and Hub scoring alike.

---

## 6. Trade-creation bypass re-verification (zero-bypass sweep)

Re-ran the sweep after the Phase 3C change, by function name and by every
`AgentExecutionManager()` instantiation site (not by variable name — the
original Phase 2 sweep's grep pattern for `.execute(` calls used specific
variable names like `_executor.execute(`/`exec_mgr.execute(` and **missed**
one instantiation that uses a differently-named local variable):

| Function | Callers | Gated? |
|---|---|---|
| `open_paper_trade()` | 1 — `engine/decision_router.py:296` only | N/A (this *is* the gate's own execution) |
| `place_real_order()` | 2 — `engine/decision_router.py:261` (gate's own LIVE path), `engine/agent/execution.py:204` (`AgentExecutionManager._live_execute`) | `AgentExecutionManager.execute()` itself has no gate check inside it — gating is the **caller's** responsibility |
| `open_option_paper_trade()` | 4 — all `StrategyFamily.FNO`, all preceded by `authorize_trade_intent()` | Yes, at each call site |
| `AgentExecutionManager()` instantiated | **3 production sites**: `engine/agent/event_arbitrage.py:160`, `engine/agent/agent_loop.py:34` (module-level `_executor`), **`tasks/india_tasks.py:3034`** | See below |

**`AgentExecutionManager.execute()` does not call `authorize_trade_intent()`
internally** — it goes straight to `_paper_execute()`/`_live_execute()`,
which write `PaperTrade`/`OpenPosition`/`AgentTrade` rows directly or call
`place_real_order()` directly. Gating is entirely the caller's
responsibility. Each of the 3 production callers was checked individually:

1. `event_arbitrage.py:160` — caller (`_execute_instant_trade()`) calls
   `authorize_trade_intent()` first (confirmed in Phase 2 work) **and** the
   whole function is hard-blocked at `evaluate_news_flash()`'s entry
   (`_NEWS_ONLY_BLOCKS_HUB_ENTRIES = True`, Phase 1). Double-safe.
2. `agent_loop.py:34` (`_executor`) — used in `_process_symbol()`, which
   calls `authorize_trade_intent()` first and checks `.approved` (confirmed
   in this session's earlier Phase 3C work) before ever reaching
   `_executor.execute()`. Now always blocked at the gate (`BLOCKED_TECHNICAL_ORIGIN`).
3. **`tasks/india_tasks.py:3034` (`executor = AgentExecutionManager()`) —
   this IS a genuine gate bypass in the source text**: the code path from
   `apply_reasoning_gate()` (line 3208) through `rm.can_take_trade()` (risk-manager
   check, not the central gate) to `executor.execute(decision, session)`
   (line 3220) contains **no `TradeIntent` construction and no call to
   `authorize_trade_intent()`/`execute_trade_intent()` anywhere in between**.
   If this loop ever ran, it would open a trade without going through the
   central gate at all.

   **This bypass is currently inert, not fixed.** It sits inside
   `for stock in (scored if not _NEWS_ONLY_BLOCKS_HUB_ENTRIES else []):` —
   since `_NEWS_ONLY_BLOCKS_HUB_ENTRIES = True` (hardcoded, Phase 1), the
   loop body never executes. The bypass exists in the source but cannot fire
   today. **This is exactly the kind of latent risk Phase 3A's own reasoning
   warned about for `_tool_news`** ("someone can later re-enable a path...
   and silently reintroduce the exact problem") — except here the risk
   predates Phase 3C entirely (it's from Phase 1) and was not previously
   flagged as a bypass specifically, only as "blocked new-entry origination."
   **Flagging this explicitly now**: if `_NEWS_ONLY_BLOCKS_HUB_ENTRIES` is
   ever flipped back to `False` without also adding a gate call to this
   specific loop body, trades would originate with zero central-gate
   involvement — no confidence-provenance check, no event check, no risk
   validation beyond `rm.can_take_trade()`. Classified **INVESTIGATE /
   HIGH-PRIORITY** for Phase B: either delete this loop body outright (it
   has been fully inert since Phase 1, longer than anything else audited
   here) or, if kept for reference, add an explicit comment at the
   `executor.execute()` call site itself (not just the loop guard 150 lines
   above it) making the bypass impossible to miss.

**No new bypass was introduced by Phase 3C.** The one bypass found predates
this session's work and was already inert before Phase 3C; Phase 3C did not
create it and does not need to fix it to be correct, but Phase B should
address it since it's now the single largest latent risk in the codebase
relative to the News-Only invariant.

---

## 7. Classification Summary

| Component | Category | Classification |
|---|---|---|
| `_tool_news()`, `_tool_expert_research()` | Trade-decision LLM tools | **KEEP** (still live-invoked; wasted-work optimization is a separate, smaller INVESTIGATE item) |
| `agent_loop.py::_process_symbol()`, `india_tasks.py::_india_trade_loop()`, `india_tasks.py::_intraday_entry_task()` | Trade origination (scaffolding) | **KEEP** (upstream scoring/logging still needed); their `TradeIntent`-construction tails are a dead end — **not deletable without restructuring**, out of scope for Phase B audit |
| `TradingSignal`, `calculate_position_size()`, `validate_signal()`, `compute_trade_levels()`, `compute_indicators()`, Hub scoring, `VirtualWallet`, market-data fetchers, exit paths | Shared validation/risk/market-data/exits | **REUSE** |
| `run_master_intelligence_cycle()`'s hub-inline entry loop (`india_tasks.py` ~3061-3220) | Trade origination (already dead since Phase 1) + **an unguarded gate bypass in its source** | **INVESTIGATE / HIGH PRIORITY** — recommend deletion in Phase B (longest-inert code found, and the bypass risk is real if ever re-enabled) |
| `momentum_screener.py`/`breakout_screener.py` (feeding `momentum_discovery`/`breakout_discovery`) | Candidate surfacing (feeds Hub, not direct origination) | **INVESTIGATE** — needs a product decision, not proven dead |
| `FNO_HEDGE` path (`fno/selection.py`) | Portfolio hedge | **KEEP** |
| `NIFTY_MIS_OPTION`, `FNO_LONG_STRADDLE` | FNO speculative origination | **INVESTIGATE** — out of this audit's directive scope (TECHNICAL only), flagged for a future decision |

Counts: **KEEP: 8 items/groups, REUSE: 6 items/groups, INVESTIGATE: 4 items/groups, DELETE: 0, RELOCATE: 0.**

No component met the bar for DELETE or RELOCATE in this pass — every
candidate either has a live caller today, or its dependency status is
genuinely uncertain pending a product decision this audit is not authorized
to make.

---

## 8. Recommended deletion order (for when Phase B implementation begins)

Ordered to minimize risk — each step names the exact re-verification to run
immediately before it, since deleting one thing can change what's provably
dead for the next:

1. **`run_master_intelligence_cycle()`'s hub-inline entry loop**
   (`india_tasks.py`, the `for stock in (scored if not
   _NEWS_ONLY_BLOCKS_HUB_ENTRIES else []):` block, roughly lines 3072-3260,
   ending wherever the loop body closes). Lowest risk: fully inert since
   Phase 1, and removing it also closes the unguarded-bypass risk in §6.
   **Before deleting**: re-run `grep -n "AgentExecutionManager()"
   tasks/india_tasks.py` to confirm this is still the only instantiation in
   this file, and re-read the loop's full body one more time end-to-end
   (this audit read it in sections, not as one contiguous block) to confirm
   nothing after line ~3260 depends on state this loop sets that survives
   past it.
2. **The `TradeIntent`-construction tails** of `_process_symbol()`,
   `_india_trade_loop()`, `_intraday_entry_task()` — NOT a deletion of the
   whole function, only the now-dead-end final section (build
   `TradeIntent(TECHNICAL)` → call the gate → handle the impossible-approval
   branch). **Before touching**: confirm `BLOCKED_TECHNICAL_ORIGIN` is still
   the first check in `authorize_trade_intent()` (re-read
   `engine/decision_router.py`), and get an explicit product decision on
   whether to (a) delete the tail and stop the function right after
   scoring/logging, or (b) leave it as visible, self-documenting dead code
   for now. This audit does not recommend either — it's a product call.
3. **`_tool_news()`/`_tool_expert_research()`** — only after step 2 is
   resolved AND a decision is made to short-circuit `apply_reasoning_gate()`
   for `TECHNICAL`-family candidates before it's called at all (not
   currently the case). **Before deleting**: re-run the full reachability
   trace in §1 — if `apply_reasoning_gate()` genuinely can no longer be
   reached for a `TECHNICAL` candidate (because step 2's restructuring
   removed the call), re-confirm zero other callers exist (news candidates
   never reach these two tools by construction — Phase 3A already removes
   them from `available_tools` whenever `candidate.evidence is not None`).
4. **`momentum_screener.py`/`breakout_screener.py` and their Celery tasks**
   — lowest priority, requires an explicit product decision (is
   candidate-surfacing for a strategy family that can't trade still worth
   the Celery cycles?) before any dependency work is even worth doing.

**Do not delete FNO paths (`NIFTY_MIS_OPTION`, `FNO_LONG_STRADDLE`,
`FNO_HEDGE`) as part of this sequence** — they were not part of the user's
Phase 3C directive and require a separate decision.
