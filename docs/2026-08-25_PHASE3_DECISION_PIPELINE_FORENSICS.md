# PHASE 3 — DECISION PIPELINE FORENSICS

**Date:** 2026-08-25 · **Scope:** forensic only. **NO PRODUCTION CHANGES.**
No threshold, weight, stop, target or prompt was touched. Master Intelligence was **not**
connected to execution. Nothing was deployed.

---

## 1. Executive verdict

The 42,835 → `decisions_made = 0` discrepancy is **not one defect. It is two, and only one of
them is a bug.**

1. **Master Intelligence has had no trade-origination path since 2026-07-21.** The ~200-line
   inline loop that turned Hub scores into entries was deliberately deleted (Phase 3C Phase B,
   documented in the code and in `docs/PHASE_3C_PHASE_B_DEPENDENCY_AUDIT.md`).
   `decisions_made` is the counter that loop incremented. With the loop gone it is initialised
   to `0` and never touched. **`decisions_made = 0` is correct behaviour, correctly reporting a
   path that no longer exists.** It is, however, **badly named** — it reads as "decisions the Hub
   made" and means "trades the Hub's removed inline loop opened".

2. **The one surviving consumer of Hub scores crashes before it can read them.**
   `tasks/india_tasks.py::_india_trade_loop` raises `UnboundLocalError` at line 610 on **every**
   invocation that gets that far, because `settings` is read at 610 and locally imported at 632.
   The Hub query at line 697 is unreachable. This **is** a production bug — the code contract
   (`.env: NEWS_ONLY_BLOCKS_HUB_ENTRIES=false`, and the comment *"Owner decision 2026-08-20
   (contract SS10b): hub entries re-enabled"*) says this path should be live.

A third, independent finding: **the loop barely runs during market hours anyway.** On 2026-08-25
it executed **11 cycles inside 09:15–15:30 IST against ~375 expected**, with a single
**329-minute gap** from 09:13:21 to 14:41:58.

| question | answer |
|---|---|
| Is Master Intelligence connected to execution? | **No** — by design since 2026-07-21 |
| Can STRONG_BUY create an `AgentDecision`? | **No** — no code path exists |
| Where do candidates disappear? | They are never created. The Hub writes scores; nothing reads them for origination. |
| Intentional or accidental? | **Intentional** (removal) **plus accidental** (the crash blocks the one remaining reader) |
| Any unaccounted loss? | **Yes — 3 observability gaps**, §14 |

---

## 2. The 42,835 reconciliation

**2026-08-24.** Labels reconcile exactly.

| # | stage | count | source |
|---|---|---:|---|
| 1 | Master score evaluations | **42,835** | `master_intelligence_scores` |
| 2 | Unique symbols scored | **1,663** | ~26 rescores per symbol per session |
| 3 | `STRONG_BUY` | **17,106** (743 distinct symbols) | |
| 4 | `BUY` | **4,891** | |
| 5 | `SELL` | **3,511** | |
| 6a | `STRONG_SELL` | **8** | |
| 6b | `NEUTRAL` | **17,319** | |
| — | `is_blocked` | **0** | |

`17,106 + 4,891 + 3,511 + 8 + 17,319 = 42,835` ✓ **exact.**

| # | stage | count | explanation |
|---|---|---:|---|
| 7 | Candidates emitted by the Hub | **0** | the origination loop was removed 2026-07-21 |
| 8 | Candidates received by `decision_router` from a Hub path | **12** | all `TECHNICAL` / `HUB_TECHNICAL`, **all RELIANCE.NS** |
| 9 | Rejected before the agent | **12** | `BLOCKED_TECHNICAL_ORIGIN` |
| 10 | Agent decisions | **679** | from the **news** path, not the Hub |
| 11 | `BUY` | **1** | |
| 12 | `SELL` | **0** | |
| 13 | `SKIP` | **678** | |
| 14 | Trade-eligible candidates | 25 `EXECUTED_PAPER` at the gate | |
| 15 | Rejected at the gate | 249 of 274 | §6 |
| 16 | Trades opened | **11** | 0 with `strategy_family='TECHNICAL'` |
| 17 | Trades closed | **14** | |

**The 22,005 `STRONG_BUY`+`BUY` labels produce exactly 0 candidates.** This is not attrition
through filters — there is no dispatch step to attrite through.

*Note on 29 overlapping symbols:* 29 of the agent's 94 symbols that day also carry a
`STRONG_BUY` label. That is coincidence — both sets are drawn from the liquid NSE universe.
There is no data path between them (§4).

---

## 3. STRONG_BUY lifecycle

**Every one of the 17,106 STRONG_BUY labels on 2026-08-24 terminates in the same state.**

```
MASTER_SCORE_CREATED  (intelligence_hub.py:1552, session.add(MasterIntelligenceScore(...)))
        ↓
STRONG_BUY label assigned
        ↓
NO_DISPATCH  ← terminal, for 17,106 of 17,106
```

**How many STRONG_BUY signals reached the decision engine? ZERO.**

The exact code path responsible, in order:

1. **`run_master_intelligence_cycle()` does not originate.** `tasks/india_tasks.py:3161` sets
   `decisions_made = 0`; the loop that incremented it was removed. The in-file comment
   (`:3188–3210`) records why, and states the removal also deleted `selector`, `de`, `rm`,
   `flow`, `_hub_short_strat`, `_intraday_on` and `_short_enabled` because the loop body was the
   only thing they existed for.
2. **`india_trade_loop` is the only other reader** — `tasks/india_tasks.py:697`,
   `hub_rows = (await session.execute(select(hub_subq))).all()`, filtered to
   `signal IN ('BUY','STRONG_BUY','SELL','STRONG_SELL')` and `is_blocked = False`.
3. **It never gets there.** The function raises at line 610, 87 lines earlier (§13, BUG-1).

The 12 `HUB_TECHNICAL` intents that *did* reach the gate cannot have come from
`_india_trade_loop` — it crashes before line 1307. The remaining `StrategyFamily.TECHNICAL`
emitter is `engine/agent/agent_loop.py:878`, which is **not in the beat schedule** and is
reachable only via `POST /agent/trigger_cycle`. Their timestamps (08:21, 11:53–12:16,
17:19–18:03 IST) straddle market hours in a pattern consistent with manual triggering.
**Emitter: STRONGLY SUPPORTED = `agent_loop` via API trigger. Not proven** — the audit row does
not record the originating process (§14, GAP-3).

Every one of those 12 is **RELIANCE.NS at confidence 72.0**. Across all four days the Hub path
has produced intents for exactly **one symbol**: 11 on 08-20, 11 on 08-21, 12 on 08-24, 4 on
08-25 — RELIANCE.NS every time.

---

## 4. AgentDecision lifecycle — is Master Intelligence an input?

**No.** `AgentDecision` has four writers:

| writer | path |
|---|---|
| `news_discovery_engine.py:1292` | news / RSS / announcements |
| `engine/agent/decision_engine.py:2325` | the LLM ReAct loop, called from the news path |
| `engine/agent/execution.py:136` | execution outcome record |
| `engine/agent/agent_loop.py:1155` | agent cycle (not beat-scheduled) |

None of the first three reads `MasterIntelligenceScore`. `agent_loop.py` does
(`:453`, `:462` — `features.hub_composite_score`), but it is not scheduled.

**What actually determines an agent decision:** a headline → `_extract_ticker_from_news()` →
`process_ticker()` → `_build_evidence()` (requires a canonical `CausalEvent`) →
`llm_tooluse_candidate()`. The Master score is not read at any step.

Consistent with Phase 1's finding: **`master_score` is NULL on 0 of 8,062 `AgentDecision` rows**
because no writer on that path has the value to write.

> **Is Master Intelligence merely producing an unused analytical record?**
> For trade origination: **yes**. It is consumed by three read-only paths —
> `engine/tactical_scoring.py` (sector sub-score only, feeding the tactical composite),
> `engine/portfolio_analytics.py`, and `api/india.py` (UI). It is not an unused record; it is a
> **scoring and display record with one narrow feed into tactical scoring**.

*(Phase 2 remains in force: the score has no demonstrated predictive edge. Nothing here
recommends connecting it. §11.)*

---

## 5. `decisions_made` — definition and writer

| property | finding |
|---|---|
| Declared | `db/models.py:1205`, `Integer`, default `0` |
| Initialised | `tasks/india_tasks.py:3161`, `decisions_made = 0`, inside `run_master_intelligence_cycle()` |
| Incremented by | **nothing** — the inline origination loop that incremented it was removed 2026-07-21 |
| Persisted | `:3239` `cycle_log.decisions_made = decisions_made` |
| Also emitted to | `:3260` alert payload, `:3268` log line `scored={n} trades={decisions_made}` |
| Read by | `api/intelligence.py:521` → the dashboard |
| Scope | **per Hub cycle**, not per symbol, per agent call or per trade |
| Resets | yes — a fresh local each cycle |
| Same variable as the dashboard shows | yes |

**Reconciliation:**

```
STRONG_BUY labels (08-24)      17,106
decisions_made                      0     <- counter of a removed loop
AgentDecision rows (08-24)        679     <- news path, unrelated to the Hub
```

These three numbers are **not connected by any code path** and were never meant to reconcile
with one another. The `:3268` log line reads `scored=N trades=0`, which invites exactly the
inference that prompted this investigation.

**Classification: CORRECT BEHAVIOUR, MISLEADING NAME.** Not a bug.

---

## 6. Complete rejection histogram

Source: `simulation_logs` where `event_type = 'EXECUTION_GATE'`, written by
`engine/decision_router.py::_log_intent_audit`. Reasons are `RoutingOutcome` enum values —
none invented.

**2026-08-24 — 274 gate events**

| outcome | family | count | % | syms | first | last |
|---|---|---:|---:|---:|---|---|
| `BLOCKED_SAFETY_GATE` | TACTICAL | 176 | 64.2% | 69 | 08:21 | 18:03 |
| `EXECUTED_PAPER` | DIRECT_NEWS | 25 | 9.1% | 2 | | |
| `BLOCKED_SHORT` | DIRECT_NEWS | 24 | 8.8% | 1 | | |
| `BLOCKED_MARKET_CLOSED` | DIRECT_NEWS | 14 | 5.1% | 6 | | |
| `BLOCKED_TECHNICAL_ORIGIN` | TECHNICAL | 12 | 4.4% | **1** | 08:21 | 18:03 |
| `EXECUTED_PAPER` | TACTICAL | 9 | 3.3% | 9 | | |
| `BLOCKED_SAFETY_GATE` | EVENT_DRIVEN | 8 | 2.9% | 3 | | |
| `BLOCKED_MARKET_CLOSED` | EVENT_DRIVEN | 3 | 1.1% | 1 | | |
| `BLOCKED_SECOND_ORDER_CONFIDENCE` | EVENT_DRIVEN | 1 | 0.4% | 1 | | |
| `EXECUTED_PAPER` | EVENT_DRIVEN | 1 | 0.4% | 1 | | |
| `BLOCKED_SAFETY_GATE` | DIRECT_NEWS | 1 | 0.4% | 1 | | |

**2026-08-25 — 1,862 gate events**

| outcome | family | count | % |
|---|---|---:|---:|
| `BLOCKED_SAFETY_GATE` | TACTICAL | 1,820 | 97.7% |
| `EXECUTED_PAPER` | TACTICAL | 16 | 0.9% |
| `BLOCKED_SHORT` | DIRECT_NEWS | 11 | 0.6% |
| `EXECUTED_PAPER` | DIRECT_NEWS | 8 | 0.4% |
| `BLOCKED_TECHNICAL_ORIGIN` | TECHNICAL | 4 | 0.2% |
| `BLOCKED_SAFETY_GATE` | EVENT_DRIVEN | 2 | 0.1% |
| `BLOCKED_MARKET_CLOSED` | EVENT_DRIVEN | 1 | 0.1% |

`BLOCKED_SAFETY_GATE` sub-reasons (from `tactical_signals.reason`, 2026-08-25): cash buffer
1,715 · sector cap 56 · R:R below 1.2 26 · strategy allocation cap 1.

**Note:** the `AgentDecision.skip_reason` field is free-text LLM prose, not an enum — 619 rows
on 08-25 produced ~600 distinct strings. It cannot be histogrammed as a taxonomy (§14, GAP-2).

---

## 7. Code-path audit

Writer: `engine/intelligence_hub.py:1552`.

| consumer | file:line | what it reads | originates trades? |
|---|---|---|---|
| tactical composite | `engine/tactical_scoring.py:37–42` | `sector_score` only | no — scoring input |
| portfolio analytics | `engine/portfolio_analytics.py:405` | latest per symbol | no |
| UI / API | `api/india.py:2395` | latest per symbol | no |
| **`india_trade_loop`** | `tasks/india_tasks.py:633–697` | full row, `signal IN (...)`, `is_blocked=False` | **would — unreachable (BUG-1)** |
| `agent_loop` | `engine/agent/agent_loop.py:453,462` | composite/master score | not beat-scheduled |
| `t1_reanalysis` | `engine/agent/t1_reanalysis.py:61` | score text for the prompt | no |

**Gates on the surviving path**, each with source, condition and persistence:

| gate | file:line | condition | rejection reason | persisted? |
|---|---|---|---|---|
| trading halted | `india_tasks.py:576` | `RuntimeConfig.halted` | log only | **no** |
| shock cooldown | `india_tasks.py:582` | `shock_cooldown_active` | log only | **no** |
| news-only hub block | `india_tasks.py:609–626` | `NEWS_ONLY_BLOCKS_HUB_ENTRIES` (**.env=false**) | log only | **no** |
| entry window | `india_tasks.py:531` | `now_ist < 15:20` | log only | **no** |
| strategy toggle | `utils/runtime_config.py::strategy_enabled` | `india_trade_loop` (**absent → fail-open → enabled**) | log only | **no** |
| confidence | `india_tasks.py:~755` | `conf < PAPER_CONFIDENCE_THRESHOLD (30.0)` | `continue` | **no** |
| SELL confidence | `~765` | `action=='SELL' and conf < 50` | `continue` | **no** |
| stale/absent price | `~810` | `entry_price <= 0` after 3 fallbacks | `continue` | **no** |
| stock weight cap | `~828` | `sym_w >= _max_stock_w` | `continue` | **no** |
| sector weight cap | `~830` | `sector_w >= _max_sector_w` | `continue` | **no** |
| candidate cap | `~888` | `actionable[: min(len, max(max_new*3,12), 24)]` — **≤15** with `max_new_entries_per_cycle=5` | silent truncation | **no** |
| pre-trade research veto | `~1015` | `res.get("veto")` | log | **no** |
| **central execution gate** | `engine/decision_router.py::authorize_trade_intent` | market hours · TECHNICAL block · canonical event · `validate_signal` 12 checks | `RoutingOutcome` enum | **YES** — `simulation_logs` |

**Every gate before the central router is log-only.** That is the observability boundary: once an
intent reaches `authorize_trade_intent` it is fully auditable; before that it is not.

**Effective flag values, resolved live:**

| flag | `.env` | `runtime_settings` | effective |
|---|---|---|---|
| `TECHNICAL_ORIGINATION_BLOCKED` | `false` (:194) | absent | **False** |
| `NEWS_ONLY_BLOCKS_HUB_ENTRIES` | `false` (:195) | absent | **False** |
| `strategy_enabled("india_trade_loop")` | — | absent | **True** (fail-open) |

So **no configured flag is blocking the Hub path today.** The `BLOCKED_TECHNICAL_ORIGIN` events
were produced when the effective value was still `True` — the worker started
**2026-08-24 23:00:41 IST** and `.env` was last modified **2026-08-25 08:15:32 IST**, so the
running worker carries settings from before that edit. This is the cached-settings failure mode
CLAUDE.md warns about; **CONFIRMED by timestamps, and it changes nothing** — the path is dead at
line 610 regardless.

---

## 8. Twenty-case forensic sample

Every STRONG_BUY on 2026-08-24 has the identical lifecycle, so a stratified sample is
representative rather than selective. Range: score 40.0 – 80.8, 743 symbols, 03:45:47 – 10:45:15
UTC (09:15 – 16:15 IST).

| # | stratum | master score | candidate created? | dispatch? | router? | agent? | decision? | trade? | **first stop** |
|---|---|---|---|---|---|---|---|---|---|
| 1 | highest score | 80.8 | no | no | no | no | no | no | **NO_DISPATCH** |
| 2–4 | top decile | 68–79 | no | no | no | no | no | no | **NO_DISPATCH** |
| 5–8 | early session (03:45–05:00 UTC) | 40–72 | no | no | no | no | no | no | **NO_DISPATCH** |
| 9–12 | mid session (05:00–08:00 UTC) | 41–74 | no | no | no | no | no | no | **NO_DISPATCH** |
| 13–16 | late session (08:00–10:00 UTC) | 40–70 | no | no | no | no | no | no | **NO_DISPATCH** |
| 17–19 | lowest STRONG_BUY | 40.0–40.4 | no | no | no | no | no | no | **NO_DISPATCH** |
| 20 | RELIANCE.NS | (72.0 at the gate) | — | **yes** | **yes** | no | no | no | **BLOCKED_TECHNICAL_ORIGIN** |

**The first point at which the candidate stops is the same for 17,105 of 17,106: the Hub writes
the row and no consumer reads it for origination.** Case 20 is the sole exception and did not
originate from the Hub loop (§3).

Presenting individual symbol rows would imply per-case variation that does not exist. The
terminal state is a property of the architecture, not of any score.

---

## 9. Silent-drop audit

| location | mechanism | affects Master | affects news | affects tactical |
|---|---|---|---|---|
| `india_tasks.py:576,582,626` | `return`, log only | **yes** | no | no |
| `india_tasks.py` candidate loop (`conf`, SELL-conf, price, weight, sector) | `continue`, log only | **yes** | no | no |
| `india_tasks.py` `level_pool[:≤15]` | **silent truncation, no log at all** | **yes** | no | no |
| `news_discovery_engine.py::_extract_ticker_from_news` | 7 × `return None` | no | **yes** | no |
| `news_discovery_engine.py:1523` | `except Exception as _rss_exc` | no | **yes** | no |
| `decision_router.py:330` | broad `except` → generic `ERROR` | yes | yes | yes |
| `tactical_executor.py:~372` | `except Exception` → `intent build failed` | no | no | **yes** |
| `india_tasks.py:1607–1613` | `sl_hit = False` (swing hold) | no | no | **yes** (exits) |

**Phase 1B already instrumented the news extraction path** — all 7 exits now emit a terminal
reason via `_drop_candidate()`.

**The Master path has no equivalent instrumentation at all.** Not one of its drops writes a row.

*Not changed — reported only, per the brief.*

---

## 10. NSE live-path verification

The Phase 1B decoupling **is live** — `watchmedo` hot-reloaded it at 17:44:56 IST:

```
📡 NSE announcement poller started as an independent task (every 60s, queue max 200)
📋 [nse_poller] 3 new of 3 fetched — queued (depth 3)
📋 [nse_poller] 1 new of 3 fetched — queued (depth 4)
📋 [nse_poller] 1 new of 3 fetched — queued (depth 1)
```

Measurable, as required: poll cadence 60–62 s · items seen 20/poll · new items and queue depth
both logged per poll · `get_nse_poll_stats()` exposes the full counter set.

> **Is the fixed NSE pipeline producing actionable candidates?**
> **EVIDENCE NOT AVAILABLE.** The fix went live at 17:44 IST, after the 15:30 close. Whether the
> poller runs *during* market hours — the entire point of the change — cannot be observed until
> the next session. The queue depth returning to 1 shows the consumer is draining, so the
> handoff works; the in-session claim is unproven and must not be asserted.

---

## 11. Master Intelligence economic result

**Phase 2 is not reversed.** 151,263 observations, 17 sessions, 1,935 symbols: top-decile minus
bottom-decile spread of −0.003 to +0.016pp at every horizon; top decile positive on 6 of 17
sessions.

**Nothing in this phase is a reason to connect Master Intelligence to execution.** 17,106
STRONG_BUY labels a day is a statement about label frequency, not about information. The finding
here is only that the architecture is *not faithfully processing* an output — which is worth
knowing regardless of whether that output is worth processing.

---

## 12. Final reconciliation table — 2026-08-24

| Stage | Count | Explanation of the difference from the row above |
|---|---:|---|
| Master evaluations | 42,835 | |
| Unique symbol-cycles | 1,663 | ~26 rescores per symbol per session |
| `STRONG_BUY` | 17,106 | |
| `BUY` | 4,891 | |
| `SELL` | 3,511 | (+ `STRONG_SELL` 8, `NEUTRAL` 17,319 — sums to 42,835 ✓) |
| **Candidate emitted** | **0** | origination loop removed 2026-07-21 (intentional) |
| **Candidate received** | **12** | not from the Hub loop — `agent_loop` via API (STRONGLY SUPPORTED) |
| Decision gate | 12 | all `BLOCKED_TECHNICAL_ORIGIN`, all RELIANCE.NS |
| Agent called | 679 | **news path only** — no Hub input |
| Agent `SKIP` | 678 | LLM's own verdict |
| Agent `BUY` | 1 | |
| Agent `SELL` | 0 | |
| Execution eligible | 25 | `EXECUTED_PAPER` at the gate (DIRECT_NEWS 25) |
| Execution rejected | 249 | of 274 gate events |
| Order submitted | 11 | paper only — `PAPER_MODE=true` |
| Order filled | 11 | simulated fill |
| Trade opened | 11 | 0 with `strategy_family='TECHNICAL'` |
| Trade closed | 14 | includes positions carried in from earlier sessions |

**UNACCOUNTED FOR: none at the row level.** Every count is explained. The unaccounted loss is
*within* the "candidate emitted = 0" row — 17,105 STRONG_BUY labels stop with no record of
having stopped (§14, GAP-1).

---

## 13. Confirmed bugs

### BUG-1 — `india_trade_loop` crashes before it can read Hub scores

**BUG:** `UnboundLocalError: cannot access local variable 'settings' where it is not associated
with a value`

**EXPECTED:** with `NEWS_ONLY_BLOCKS_HUB_ENTRIES=false` and the in-code note *"Owner decision
2026-08-20 (contract SS10b): hub entries re-enabled"*, `_india_trade_loop` should read
`hub_rows` at line 697 and evaluate Hub candidates.

**ACTUAL:** the function raises at line 610 and returns nothing. Line 697 is unreachable.
Because line 610 sits outside the two `try` blocks at 559/570, the exception propagates to the
Celery task wrapper and is logged as `Task tasks.india_trade_loop[...] raised unexpected`.

**EVIDENCE:**
- `tasks/india_tasks.py:520` — `from utils.config import settings as _cfg` (binds `_cfg`, **not** `settings`)
- `tasks/india_tasks.py:610` — `getattr(settings, "NEWS_ONLY_BLOCKS_HUB_ENTRIES", True)`
- `tasks/india_tasks.py:632` — `from utils.config import settings` ← the local binding
- No module-level `from utils.config import settings` exists in the file
- Python scoping: any assignment to a name anywhere in a function makes it local for the entire body
- `logs/celery-worker.err`: 38 occurrences today, 34 on 08-24, 130 in the 08-21 rotation — **not new**

**FILE / FUNCTION:** `tasks/india_tasks.py` · `_india_trade_loop`

**SAFE FIX PROPOSAL** (not implemented): add a module-level `from utils.config import settings`,
or reuse the already-bound `_cfg` at line 610. The second is the smaller change and needs no
new import:
```python
getattr(_cfg, "NEWS_ONLY_BLOCKS_HUB_ENTRIES", True)
```
**Risk:** fixing this makes the Hub path live for the first time since 2026-08-20. Given Phase 2
found no predictive edge in the Hub score, the fix should land **with the path still gated off**
(set `NEWS_ONLY_BLOCKS_HUB_ENTRIES=true`), so the crash is repaired without silently enabling an
unvalidated origination path. **That decision is the owner's, not this report's.**

**Classification: PRODUCTION BUG.** The intended behaviour is documented in the code and in
`.env`; the system does not do it.

### BUG-2 — the loop cannot run during market hours

**BUG:** `india_trade_loop` executed **11 cycles inside 09:15–15:30 IST** on 2026-08-25, against
~375 expected at its 60-second cadence.

**EXPECTED:** a 60 s beat task runs ~375 times in a 6.25 h session.

**ACTUAL:** 557 cycles total across 03:20–19:23 IST, with a **329-minute gap from 09:13:21 to
14:41:58** — 88% of the session. Further gaps of 24 and 30 minutes followed.

**EVIDENCE:** `logs/celery-worker.log`, `[india_trade_loop] Starting cycle` timestamps.
During the gap the worker log shows continuous `engine.indicators` activity and repeated Keras
`model.compile_metrics` warnings from `ForkPoolWorker-3` — the Master Intelligence Hub cycle
scoring ~1,663 symbols with the ML predictor attached. Beat entries carry `expires`, so a task
that cannot be picked up **drops silently: no log, no exception, no worker**.

**FILE / FUNCTION:** `tasks/celery_app.py` (beat + `expires`) · `run_master_intelligence_cycle`

**SAFE FIX PROPOSAL** (not implemented): route `india_trade_loop` to a dedicated queue with its
own worker, exactly as `scan_queue` was carved out for the F1 scan in commit `737dd4e`. That
isolates it from Hub saturation without changing any task's logic.
**Risk:** an additional worker process (~1 CPU, ~500 MB) on a 4-core host.

**Classification: PRODUCTION BUG** — but note it is currently **masked** by BUG-1: even in the
11 cycles that did run, the function crashed at line 610.

---

## 14. Observability gaps

**GAP-1 — the Master path emits no lifecycle record.**
17,105 STRONG_BUY labels reach a terminal state with nothing written anywhere. Their
disappearance is inferable only by reading the code. Every gate before
`authorize_trade_intent` is log-only, and `level_pool[:≤15]` truncates with no log at all.
**Classification: OBSERVABILITY GAP.**

**GAP-2 — `AgentDecision.skip_reason` is free-text prose.**
619 rows on 08-25 produced ~600 distinct strings, so skips cannot be histogrammed as a taxonomy.
The `EXECUTION_GATE` path does this correctly with a `RoutingOutcome` enum; the agent path does
not. **Classification: OBSERVABILITY GAP.**

**GAP-3 — `simulation_logs` does not record the originating process.**
The 12 `HUB_TECHNICAL` intents cannot be attributed to a process with certainty because the
audit row carries `strategy` but not the emitter. **Classification: OBSERVABILITY GAP.**

---

## 15. Unknowns

- **Exact emitter of the 16 `HUB_TECHNICAL` intents.** `agent_loop` via API trigger is
  STRONGLY SUPPORTED; not proven (GAP-3).
- **Why only RELIANCE.NS, ever.** Four sessions, one symbol. The filter producing that is not
  identified. **INCONCLUSIVE.**
- **Whether the NSE fix works in-session.** Deployed after the close. **EVIDENCE NOT AVAILABLE.**
- **When BUG-1 was introduced.** `git log -S` on the local import returns five candidate
  commits; none isolates it. Crashes are present in the earliest retained log (08-21).
- **Whether BUG-2 predates the ML predictor wiring** (2026-08-06). Logs do not reach back far
  enough.

---

## 16. Safe fixes proposed — NOT IMPLEMENTED

| # | fix | file | risk | verification |
|---|---|---|---|---|
| 1 | `settings` → `_cfg` at line 610 | `tasks/india_tasks.py:610` | **enables a path Phase 2 found no edge in** — land it with the gate flag ON | AST test that no name is read before its local import in the function |
| 2 | dedicated queue for `india_trade_loop` | `tasks/celery_app.py` + a new unit | +1 worker process | count cycles inside 09:15–15:30 next session |
| 3 | rename `decisions_made` → `hub_inline_entries_opened`, or delete it | `db/models.py:1205`, `india_tasks.py:3161/3239/3260/3268`, `api/intelligence.py:521` | schema touch; dashboard field | field reads 0 with an unambiguous name |
| 4 | `_drop_candidate()`-style terminal reasons on the Master path | `tasks/india_tasks.py` candidate loop | none — logging only | every Hub candidate gets exactly one reason |
| 5 | enum for `AgentDecision.skip_reason`, prose moved to a separate column | `db/models.py`, agent writers | migration | skips histogrammable |
| 6 | record the originating process in `_log_intent_audit` | `engine/decision_router.py:975` | none — one extra json field | intents attributable |

**None of these were implemented.**

---

## 17. Recommended next experiment

**Before any fix**, one measurement — cheap, read-only, and it decides whether fix 1 or fix 2
matters at all:

> Instrument `_india_trade_loop` **temporarily and in a branch** to emit one counter line per
> cycle (`hub_rows`, `candidates`, `actionable`, `level_pool`, `intents`, `gate outcomes`), fix
> only BUG-1 in that branch, and run it for one session **with `NEWS_ONLY_BLOCKS_HUB_ENTRIES=true`
> so nothing can execute**. That yields the true Hub funnel — which is currently unmeasurable
> because the function dies before producing a single number.

Only then is it possible to say whether the Hub path would produce 3 candidates a day or 300,
and therefore whether BUG-2's starvation matters.

**Not recommended:** enabling the Hub path. Phase 2's evidence stands.

---

## Classification summary

| # | finding | classification |
|---|---|---|
| 1 | `decisions_made = 0` is a defect | **RULED OUT** — correct counter for a loop removed by design 2026-07-21 |
| 2 | `decisions_made` is misleadingly named | **CONFIRMED** |
| 3 | Master Intelligence is connected to execution | **RULED OUT** |
| 4 | STRONG_BUY can create an `AgentDecision` | **RULED OUT** — no code path |
| 5 | `_india_trade_loop` crashes at line 610 | **PRODUCTION BUG** (BUG-1) |
| 6 | The loop is starved during market hours | **PRODUCTION BUG** (BUG-2) |
| 7 | A config flag is blocking the Hub path today | **RULED OUT** — both flags resolve False |
| 8 | The worker carries pre-`.env`-edit settings | **CONFIRMED** — worker 08-24 23:00, `.env` 08-25 08:15 |
| 9 | Hub-path intents come from `_india_trade_loop` | **RULED OUT** — it crashes before line 1307 |
| 10 | They come from `agent_loop` via API | **STRONGLY SUPPORTED** |
| 11 | Only RELIANCE.NS ever reaches the gate | **CONFIRMED** — 38 intents, 4 sessions, 1 symbol |
| 12 | Master path lifecycle is unobservable | **OBSERVABILITY GAP** |
| 13 | `skip_reason` is not a taxonomy | **OBSERVABILITY GAP** |
| 14 | `simulation_logs` lacks the emitter | **OBSERVABILITY GAP** |
| 15 | The NSE fix produces in-session candidates | **EVIDENCE NOT AVAILABLE** |
| 16 | Any unaccounted candidate loss | **CONFIRMED** — 17,105 labels, no record (GAP-1) |

---

**NO PRODUCTION CHANGES WERE MADE. No defect was fixed. Master Intelligence was not connected.
Nothing was deployed. STOP.**
