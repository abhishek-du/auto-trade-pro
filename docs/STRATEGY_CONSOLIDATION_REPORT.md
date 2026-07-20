# Strategy Consolidation Report — Converging Toward ONE News-Driven Event Trading Strategy

**Date:** 2026-07-20
**Type:** Analysis only. No code changes made. Builds directly on `docs/COMPLETE_SYSTEM_DEEP_AUDIT_HINGLISH.md` (same-day deep audit) — every fact below was already verified there; this report re-classifies those verified facts against the new target architecture, it does not re-research them.
**Target architecture (as specified):**

```text
NEWS / OFFICIAL EVENT
   ↓
CANONICAL EVENT INTELLIGENCE
   ↓
EVENT MATERIALITY + DIRECTION + SURPRISE + RELIABILITY
   ↓
ENTITY / IMPACT GRAPH
   ↓
DIRECT + FIRST-ORDER + SECOND-ORDER CANDIDATES
   ↓
EVENT-SPECIFIC CANDIDATE VALIDATION
   ↓
TECHNICAL TIMING / INVALIDATION FILTER
   ↓
ONE EVENT-DRIVEN TRADE DECISION
   ↓
CENTRAL RISK
   ↓
CENTRAL EXECUTION
```

---

## Summary verdict before the detail

Of the 12 trade-producing paths found in the audit, **only 2 are actually news-catalyst-driven today** (News Direct and News Cascade, both in `news_discovery_engine.py`). Everything else that independently creates a trade does so from technical/options/volatility signals with no news requirement — these are exactly the paths the target architecture says should stop existing as independent strategies. One path (`event_arbitrage.py`) is news-driven but is a **duplicate, parallel implementation** of the same idea as the canonical path, not a distinct strategy — it should be merged, not kept alongside. A large amount of already-built infrastructure (technical indicators, risk checks, position sizing, options-chain plumbing, the 7-factor scoring, the event classifier) is reusable as **components** inside the single pipeline — very little needs to be deleted outright; most of the work is re-plumbing what exists, not rebuilding it.

---

## Classification Table

| # | Path / File | News catalyst? | Independently creates trade? | Part of intended architecture? | Verdict | Depends on it (code/schedulers) |
|---|---|---|---|---|---|---|
| 1 | **News Direct** — `news_discovery_engine.py::_execute_news_trade` (primary) | Yes — NSE announcement/RSS headline | Yes | **Yes — this IS the target path** | **KEEP** | `autotrade-news-engine.service`, `llm_tooluse_candidate`, `validate_evidence_consistency`, `execute_trade_intent` |
| 2 | **News Cascade / second-order** — `news_discovery_engine.py` + `sector_graph.py::get_second_order_trades` | Yes — derived from a primary event | Currently blocked by the gate (hardcoded confidence), but the *mechanism* is the target's "SECOND-ORDER CANDIDATES" stage | **Yes — this is literally the Entity/Impact Graph + candidate-generation stage**, just unscored | **KEEP**, but must gain real per-candidate scoring (see §Migration) | Same service; `sector_graph.py` |
| 3 | **Event Arbitrage / news-flash** — `event_arbitrage.py::evaluate_news_flash` | Yes — breaking headline | Structurally yes (currently disabled) | **No — duplicate parallel implementation of #1's idea**, own LLM prompt, own confidence logic, own executor (`AgentExecutionManager`, not the news pipeline's own path) | **MERGE** into the canonical pipeline | `crawler/news_crawler.py`, `tasks/india_tasks.py` (2 call sites), `AgentExecutionManager` |
| 4 | **Equity Hub scan** — `agent_loop.py::_process_symbol` | No — technical/multi-factor blend, news is one of 7 sub-scores, not a trigger | Yes, on pure technical score ≥30 even with zero news | No — this is the "Technical stock scanner" explicitly named for removal | **DISABLE** as independent generator | `MarketShortlist`/`MasterIntelligenceScore` (read); manual `POST /agent/cycle/trigger` only — no scheduler |
| 5 | **Master Intelligence Cycle** — `india_tasks.py::run_master_intelligence_cycle` | No — same as #4, own inline scan/score/execute | Yes — and this is the highest-volume production path today | No — "Independent Master Intelligence strategy," explicitly named for removal | **DISABLE** as independent generator (highest-priority disable — this is the biggest live source of non-news trades) | Celery beat `master-intelligence-every-15min`; own inline `AgentExecutionManager`/`DecisionEngine`/`RiskManagerAgent` |
| 6 | **Main equity/short loop (Path B)** — `india_tasks.py::_india_trade_loop` | No — Hub-score + LLM reasoning gate, technical-signal-initiated | Yes | No — technical-only BUY/SELL loop | **DISABLE** as independent generator | Celery beat `india-trade-loop-every-60s`; `apply_reasoning_gate`, `execute_trade_intent` |
| 7 | **Intraday MIS burst** — `india_tasks.py` Step 5 | No — top Hub `master_score` signals | Yes | No | **DISABLE** | Celery beat `intraday-morning-entry` |
| 8 | **NIFTY MIS option scalp** — `india_tasks.py::_open_index_option_mis` | No — market-wide avg Hub score direction | Yes | No — "Independent NIFTY option scalp," explicitly named | **DISABLE** | Called from Step 5's task |
| 9 | **F&O spreads** — `engine/fno/selection.py::evaluate_index_options` | Partial — `composite_index_signal` blends in a news factor alongside price/PCR/FII-DII/breadth/VIX, but is not news-triggered | Yes, gated on `ENABLE_FNO`+`ENABLE_OPTIONS` (off by default) | No — "Independent F&O spread strategy," explicitly named | **DISABLE** as independent generator | `agent_loop.py`'s F&O pass (also currently gated off by feature flag) |
| 10 | **F&O portfolio hedge** — `selection.py::evaluate_portfolio_hedge` | No — fires on a bearish technical signal against existing equity exposure | Yes, but *defensive* (protects existing positions, doesn't seek new alpha) | Ambiguous — this is closer to a **Central Risk** action than an alpha strategy | **REUSE AS COMPONENT** — fold into Central Risk as an automatic hedge action triggered by portfolio state, not a standalone "strategy" scanning for opportunities | Same F&O pass |
| 11 | **F&O futures** — `engine/fno/futures.py::evaluate_index_futures` | No — `_index_signal`, purely technical | Yes, feature-flagged off by default | No — "Independent futures strategy," explicitly named | **DISABLE** | Same F&O pass |
| 12 | **Long straddle / iron condor** — `engine/fno/strategies_vol.py` | No — IV-rank extremes | Yes, feature-flagged off by default | No — "Independent volatility strategy," explicitly named | **DISABLE** | Same F&O pass |
| 13 | **`unstructured_alpha_scan`** — `tasks/unstructured_alpha_scan.py` | Nominally yes (scans `NewsItem`), but hardcoded to the literal word "apple" | No — logs only, never executes | No, and not usable as-is even conceptually | **DELETE/DEPRECATE** | Celery beat, hourly `:15` — safe to remove, nothing downstream consumes its output |
| 14 | **`CausalEvent` write path** — `crawler/event_pipeline.py::process_latest_events` + `engine/event_classifier.py::classify_event` | Yes | No (write-only, feeds nothing) | **Yes — this is exactly "Canonical Event Intelligence,"** just disconnected from decisions today | **KEEP**, but must become the actual read source for the pipeline (currently `news_discovery_engine.py` does its own separate classification instead of reading what this already wrote) | `crawler/news_crawler.py` (Celery, 60s) |
| 15 | **`news_discovery_engine.py`'s own classification call** — `_build_evidence()` (added in this session) | Yes | Feeds #1/#2 | Yes, but duplicates #14's job with a second, separate LLM call for conceptually the same "classify this event" step | **MERGE** with #14 — one canonical classification call per event, not two independent ones | `news_discovery_engine.py::process_ticker` |
| 16 | **`sector_graph.py`** | Yes (derived) | Feeds #2 | **Yes — this is the "Entity/Impact Graph" stage**, currently with zero scoring | **KEEP**, needs real materiality-aware scoring added (this is the #1 functional gap standing between today's code and the target architecture) | `news_discovery_engine.py::process_ticker` |
| 17 | **`engine/news_discovery_engine.py`'s dead stub classes** — `DependencyGraph`, `EventIntelligenceEngine`, `SurpriseEngine`, `EventLifecycleTracker`, `SourceTrustMatrix` | Nominally yes by docstring intent | No — hardcoded fake outputs, zero callers | Conceptually yes (these are literally scaffolded attempts at the entity/impact graph + surprise/reliability scoring the target architecture calls for) but abandoned and diverged from what `sector_graph.py`/`event_classifier.py` actually became | **DELETE/DEPRECATE** — their intended purpose is already better served by consolidating #14/#16; keeping fake stubs around is pure architectural noise | None — zero callers confirmed |
| 18 | **Central Execution Gate** — `engine/decision_router.py` | N/A (gate, not a strategy) | N/A | **Yes — this is literally "Central Execution"** | **KEEP** unconditionally | Will become the single terminal stage once #4-12 are disabled |
| 19 | **Risk infrastructure** — `engine/risk_manager.py::validate_signal`, `engine/agent/risk_manager.py::RiskManagerAgent`, `paper_trading/virtual_wallet.py::check_drawdown_breakers`, `calculate_position_size` | N/A | N/A (checks, not generators) | **Yes — this is "Central Risk,"** but currently fragmented across two modules with inconsistent coverage (per the deep audit's P1-1) | **REUSE AS COMPONENT**, and unify into one module during migration | Called from most of #4-12; must be consolidated so the single surviving strategy gets ALL checks (sector cap, correlation, drawdown, cash buffer) uniformly |
| 20 | **Technical levels/indicators** — `engine/risk_manager.py::compute_trade_levels`, `engine/indicators.py`, `crawler/price_feed.py::get_latest_candles` | N/A | N/A | **Yes — this is "Technical Timing / Invalidation Filter,"** already correctly scoped as a filter, not a signal generator | **REUSE AS COMPONENT** | Already used this way by News Direct (fixed earlier today); needs to become the ONLY consumer pattern once #4-12 stop being independent generators |
| 21 | **`MasterIntelligenceScore` 7 sub-scores** — `engine/intelligence_hub.py` | Partial (news is 1 of 7 sub-scores) | N/A on its own (feeds #4-8) | **Yes, as contextual validation** — matches "Fundamentals, macro, sector, options... are contextual validation inputs, not independent strategies" exactly | **REUSE AS COMPONENT** — keep computing all 7 sub-scores, but repurpose them as confirmation signals for a news-triggered candidate (technical/sector/macro/fundamental/options alignment check), not as an independent `master_score > threshold` trigger | `intelligence_hub.py`'s 15-min scoring cycle — keep running, change who's allowed to *act* on it |
| 22 | **`market_scanner.py` / `MarketShortlist`** | No | No on its own | Ambiguous — could serve as a technical-strength cross-check during candidate validation | **REUSE AS COMPONENT** (conditional) — keep if the news pipeline wants "is this stock already showing independent technical strength" as a confirmation signal; otherwise safe to deprecate once #4's only consumer is disabled | `agent_loop.py::_fetch_hub_scores` (which is itself being disabled) |

---

## Detail on the 3 hardest classification calls

**F&O portfolio hedge (#10) — REUSE, not DISABLE.** Unlike every other F&O strategy, this one doesn't originate a speculative position — it reacts to *existing* equity exposure with a bearish technical signal by buying a protective put. That's a risk-management action wearing a strategy-shaped hat. Recommendation: keep the underlying mechanism, but trigger it from Central Risk (e.g., "if portfolio delta/equity-exposure crosses X and market regime turns bearish, hedge automatically") rather than from an independent F&O evaluation pass that runs alongside speculative strategies. This distinction matters for the migration plan below — it doesn't get "disabled," it gets relocated.

**Event Arbitrage (#3) — MERGE, not DISABLE.** This is philosophically aligned with the target (news is the catalyst), so simply disabling it would throw away a legitimate capability: reacting *fast* to breaking headlines outside the normal NSE-announcement/RSS polling cadence. But it currently exists as a second, parallel LLM-prompt-and-execution stack, which is exactly the "strategy proliferation" the user wants eliminated — just proliferation of *news* strategies rather than technical ones. The fast-reaction capability should become a mode or fast-path within the canonical pipeline (same event classifier, same evidence contract, same execution gate), not a separate file with its own decision logic.

**`sector_graph.py` (#16) and the dead stub classes (#17) — KEEP one, DELETE the other, even though they attempt the same thing.** Both are attempts at an entity/impact graph. `sector_graph.py` is live, connected, and just needs scoring added. The stub classes in `engine/news_discovery_engine.py` are more architecturally ambitious on paper (they even sketch a `DependencyGraph.resolve_ripple_effect` distinguishing supply-chain relationships) but are 100% fake/hardcoded and have zero callers. Resurrecting them instead of extending `sector_graph.py` would mean building two competing implementations of the same stage — recommend deleting them and porting any genuinely good ideas from their docstrings into `sector_graph.py`'s design, rather than keeping both around.

---

## Safest Migration Path (sequenced, not a single big-bang change)

This deliberately mirrors the "don't do all 5 areas in one giant change" caution from earlier in this session — same principle applies here, arguably more so, since disabling live trade generators is higher-stakes than adding a gate check.

**Stage 1 — Stop the bleeding on the highest-volume non-news path first.**
Disable `run_master_intelligence_cycle`'s ability to execute (#5) before anything else — it's simultaneously the biggest source of non-news trades AND the path most recently confirmed to bypass even the execution gate. Suggest converting it to *scoring-only* (keep writing `MasterIntelligenceScore`, stop calling `executor.execute`) as the very first change, since the scoring output is needed by Stage 3 regardless.

**Stage 2 — Disable the remaining independent technical/F&O generators (#4, #6, #7, #8, #9, #11, #12).**
Most of these are already feature-flagged off by default (`ENABLE_FNO`/`ENABLE_OPTIONS`/`ENABLE_FUTURES`) or have no scheduler (#4). The two with live schedulers and no flag — #6 (`_india_trade_loop`, 60s) and #7 (intraday burst, one-shot 09:30) — need an explicit kill switch, not just relying on a flag no one's flipped. Relocate #10 (hedge) into Central Risk during this stage rather than disabling it outright.

**Stage 3 — Merge duplicate news-classification and news-execution paths.**
Consolidate #14 and #15 into one classification call per event (read `CausalEvent` if it already exists for this `NewsItem` before calling `classify_event()` again). Merge #3 (event_arbitrage) into the canonical pipeline as a fast-path mode, retiring its separate LLM prompt and separate `AgentExecutionManager`-based execution route in favor of the same `TradeIntent`/gate flow #1 already uses.

**Stage 4 — Add real scoring to the entity/impact graph.**
Extend #16 (`sector_graph.py`) with the per-candidate materiality/confidence scoring it currently lacks — this is the change that lets second-order candidates (#2) actually pass the gate on merit instead of being unconditionally blocked as they are today.

**Stage 5 — Repurpose the retained infrastructure as components.**
Rewire #19 (risk), #20 (technical timing), #21 (7-factor scores), and #22 (shortlist, if kept) so the single surviving News Direct + Cascade pipeline consumes them as validation/filter/context inputs at the appropriate stage of the target flow, rather than leaving them wired to the paths disabled in Stages 1-2.

**Stage 6 — Delete confirmed-dead code.**
Remove #13 (`unstructured_alpha_scan`) and #17 (the dead stub classes) — both zero-caller, both superseded by better-integrated equivalents by this point.

**What NOT to do during migration:** don't disable Stage 2's paths before Stage 1 is verified stable — Stage 1 alone removes the largest volume of non-news trades and is independently valuable even if Stages 2-6 slip. Don't attempt Stage 4's scoring work before Stage 3's merge — scoring second-order candidates from a *duplicated* classification path would mean scoring against two different evidence sets depending on which code path produced the event, which defeats the point of "canonical" event intelligence.

No code has been changed to produce this report. Awaiting direction on which stage to begin with.
