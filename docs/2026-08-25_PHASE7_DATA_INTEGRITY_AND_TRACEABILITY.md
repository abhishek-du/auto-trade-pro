# PHASE 7 — PRODUCTION DATA INTEGRITY + NEWS EVENT TRACEABILITY

**Mode:** read-only forensics + design. **Zero production changes, zero DB mutations during this
phase.** One historical mutation is disclosed in §12 — it was caused by me in earlier phases.

---

## 1. Executive verdict

| objective | outcome |
|---|---|
| **A — BUG-2 real-market verification** | **EVIDENCE NOT AVAILABLE.** No market session has occurred since the Phase 5 deployment. |
| **B — TESTCO origin** | **CONFIRMED.** Test traffic. The exact mechanism, file and line are identified — and I contributed 10 of the 144 rows during Phases 1B and 5. |
| **B — `causal_events.news_id`** | **CONFIRMED, and it is not a bug.** It is a documented deliberate choice, plus a dated origination shift on 2026-07-22. Historical linkage is **IMPOSSIBLE** to recover. |

**Two corrections to Phase 6 are required:**

1. Phase 6 said *"`causal_events.news_id` is NULL for all 6,303 events"* and implied the column
   was never populated. **It was 100% populated from 2026-07-16 to 2026-07-21 — 2,912 events —
   and dropped to 0% on 2026-07-22.** A dated regression, not an absence.
2. Phase 6 reported TESTCO timestamps as IST. **`simulation_logs.timestamp` is naive UTC**
   (verified: delta 0 s against `tactical_signals.created_at`). Every TESTCO time in Phase 6 was
   5:30 early.

---

## 2. BUG-2 real-session verification — **EVIDENCE NOT AVAILABLE**

The system clock reads **2026-08-25 22:27 IST**. The Phase 5 fix deployed at 20:01 IST the same
day, after the 15:30 close.

```
cycles logged by the trade worker : 144
distinct dates                    : 2026-08-25 only
cycles inside 09:15-15:30 IST     : 0
```

**The measurement the brief requires cannot be taken.** No session has run since deployment.
Reporting a market-hours cycle count would mean fabricating one.

What *is* measurable, and is **not** the required test:

| metric | observed (out of session) |
|---|---|
| cycles | 144 over 143 min |
| coverage | 101.0% |
| median / p75 / p95 interval | 60 s / 60 s / 60 s |
| max interval | 61 s |
| gaps > 90 s / 2 min / 5 min / 15 min | 0 / 0 / 0 / 0 |
| exceptions | 0 |
| worker restarts | 0 |
| expired tasks | **not observable** — Celery logs nothing on expiry |

Cycles reaching BUG-1 at `:610`: **0**. Every cycle returned at the market-status check on
`:524`, which is why there are zero `UnboundLocalError` traces. In-session the crash reappears —
that is the designed behaviour of this experiment and it is unverified.

Whether the trade worker stays isolated from the Hub worker is also **unverified**: the Hub's
heavy cycle runs during market hours, and no market hours have elapsed.

**Classification: EVIDENCE NOT AVAILABLE.** Scheduling is sound out of session; the actual test
is pending.

---

## 3. TESTCO origin — **CONFIRMED: test code writing to the production database**

### The search

`TESTCO` appears in **21 files**. Twenty are under `tests/`. The two outside are **comments**,
not code:

| file:line | content | classification |
|---|---|---|
| `utils/logger.py:43` | `#     CRITICAL │ REAL ORDER PLACED — BUY 1×TESTCO @ ₹100.00` | comment |
| `integrations/telegram_service.py:34` | `# ... Fixture data like the "TESTCO ...` | comment |

**No production code references TESTCO.** RULED OUT as production code, fixture leak into
production config, or a real listed symbol (it has zero rows in `candles`).

### The mechanism — identified from the row payload itself

The `ERROR` rows carry the writer's own stack text:

```json
"reason": "paper execution error: TestFullApprovedFlow
           .test_second_order_cascade_skipped_when_no_graph_trades
           .<locals>._fake_open_paper_trade() got an unexpec…"
```

That names `tests/test_integration_pipeline.py:82 class TestFullApprovedFlow`.

The test mocks its sessions (`AsyncMock()`) and patches
`patch("news_discovery_engine.AsyncSessionLocal", find_ctx)` — but the execution path it
exercises opens its **own** session from a **different import site**:

```
engine/direct_news_strategy.py:214    from db.database import AsyncSessionLocal   ← not the patched name
engine/direct_news_strategy.py:281    async with AsyncSessionLocal() as session:  ← REAL production session
```

`decision_router.py`, `agent/execution.py` and `trade_simulator.py` open **no** sessions of their
own (verified: zero `AsyncSessionLocal` references each), so `direct_news_strategy.py` is the
only unpatched door — and the session it opens is handed straight to
`execute_trade_intent(intent, session)` → `_log_intent_audit(…, session)` →
`session.add(SimulationLog(...))` → `await session.commit()`.

**The patch targets the wrong module.** Patching `news_discovery_engine.AsyncSessionLocal` does
nothing to a function-local `from db.database import AsyncSessionLocal` inside
`direct_news_strategy`.

| property | finding |
|---|---|
| can execute in production | **no** — only under pytest |
| writes to `simulation_logs` | **yes** — 144 rows |
| writes to `paper_trades` | **no** — 0 rows (the fake `open_paper_trade` intercepts it) |
| triggerable by Celery | **no** |
| triggerable by HTTP | **no** |
| currently scheduled | **no** |
| imported by production code | **no** |

**Classification: test code. CONFIRMED.**

---

## 4. Event 2848 lineage

`causal_events.id = 2848` is **a real production event**, not synthetic:

```
id 2848 · created 2026-07-21 09:05:21 · event_title "EARNINGS"
importance 85 · confidence 0.92 · bullish_stocks ["TVS Motor"] · news_id 8967 (NOT NULL)
```

The fixture reuses this real id to satisfy the `NO EVENT → NO TRADE` gate in
`decision_router._verify_canonical_event`. All 144 TESTCO rows carry `event_id 2848`, and **no
non-TESTCO row references it**.

```
SOURCE      news_items id 8967                                   [EXACT — FK present]
   ↓
EVENT       causal_events id 2848, TVS Motor EARNINGS, 07-21      [EXACT]
   ↓
DECISION    — no agent_decisions row for TESTCO.NS               [EVIDENCE NOT AVAILABLE]
   ↓
ROUTER      execute_trade_intent, evidence_ids ["2848"]          [INFERRED from the payload]
   ↓
GATE        simulation_logs × 144, EXECUTED_PAPER 142 / ERROR 2  [EXACT]
   ↓
TRADE       none — 0 paper_trades rows                           [RULED OUT]
```

The synthetic intent is fully visible in the payload: `entry 100.0`, `stop_loss 95.0`,
`take_profit 110.0`, `confidence 85.0` — round fixture numbers, identical on every row.

**All TESTCO rows derive from this one real event id. CONFIRMED.**

---

## 5. Can TESTCO still write? — **Yes, whenever the test suite is run**

Not scheduled, not routable, not imported by production. But `pytest tests/` is the documented
way to run this project's tests (CLAUDE.md §7), and the path is live in the current tree.

**Nothing was disabled, killed or rescheduled.**

---

## 6. Database integrity reconciliation

| field | cardinality |
|---|---|
| total TESTCO rows | **144** |
| unique `event_id` | **1** (2848) |
| unique timestamps | 144 |
| unique gate outcomes | 2 — `EXECUTED_PAPER` 142, `ERROR` 2 |
| unique confidence | 1 — `85.0` |
| unique `strategy_family` | 1 — `DIRECT_NEWS` |
| unique `event_type` | 1 — `EXECUTION_GATE` |
| unique symbols | 1 |

Per day (UTC): 08-19 ×36, 08-20 ×52, 08-21 ×22, 08-24 ×24, 08-25 ×10.

Rows arrive in **pairs 3–4 seconds apart** — one test invocation produces two gate rows.
144 rows ≈ 72 invocations.

**Verdict: A — synthetic test traffic. CONFIRMED by code lineage**, not assumed:
the ERROR payload names the test function, no production code references the symbol, and the
symbol has zero rows in `news_items`, `causal_events`, `agent_decisions`, `paper_trades` or
`candles`.

Phase 6's "144 synthetic gate events" reconciles exactly.

---

## 7. `causal_events.news_id` — root cause

**Phase 6's framing was wrong.** The column was populated, then stopped:

| period | events | with `news_id` | |
|---|---:|---:|---|
| 2026-07-16 → 07-21 | 2,912 | **2,912** | **100%** |
| 2026-07-22 → 08-25 | 6,703+ | **0** | **0%** |

All 2,912 linked events still reference existing `news_items` rows — the FK was valid and never
broke.

### Why it stopped

There are four `CausalEvent(...)` construction sites:

| site | sets `news_id`? |
|---|---|
| `crawler/event_pipeline.py:53` | **yes** — `news_id=primary_article_id` (`:54`) |
| `crawler/event_pipeline.py:69` | **yes** — `news_id=duplicate["id"]` (`:70`) |
| `crawler/event_pipeline.py:155` | **yes** — `news_id=cluster["articles"][0]["id"]` (`:156`) |
| **`news_discovery_engine.py:932`** | **no** — `news_id=None` |

And the engine's line is explicit:

```python
news_id=None,  # this pipeline doesn't have a NewsItem row to link — see audit doc §3.6
```

The engine's own docstring at `:785–787` says the same: *"…so its CausalEvent rows are the only
ones … CausalEvent writes have news_id=None."*

**This is a deliberate, documented choice, not a defect.** What changed on 2026-07-22 is *which
writer dominates*: origination moved from the celery crawler (`event_pipeline`, which links) to
`news_discovery_engine` (which does not). That is the same two-path split established earlier —
NSE announcements only ever flow through the engine.

**Classification: OBSERVABILITY GAP, by design. RULED OUT as a bug.**

The gap is real regardless of intent: without the link, no analysis can ask *which kind of news*
carries information (Phase 6 §13).

---

## 8. Historical recoverability — **4: IMPOSSIBLE FROM STORED DATA**

Every candidate join key was tested against the 2026-08-03 → 08-25 window (6,703 events,
20,276 news items):

| key | result |
|---|---|
| foreign key | `news_id` NULL on all — nothing to join |
| **`event_title` ↔ `headline`** | **0 exact matches.** `event_title` is a **category label**, not a headline — only **180 distinct values** across 6,703 events (`ACQUISITION`, `ANALYST_UPGRADE`, `ASSET_SALE`, …) |
| **timestamp proximity** | **0 of 400 sampled events had *any* news item crawled in the 60 s before them.** Not "ambiguous" — *no candidates at all* |
| symbol/ticker | `causal_events` stores company names and bare tickers; `news_items.tickers_affected` stores `.NS` symbols. Even normalised, it cannot disambiguate among same-minute items — and there are none |
| URL / external id / content hash | **not stored on `causal_events`** |

**Coverage: EXACT 0% · DETERMINISTIC-BUT-INDIRECT 0% · PROBABILISTIC 0% · IMPOSSIBLE 100%.**

The timestamp result is the decisive one and it also explains the mechanism: the engine creates
events from content that has no `NewsItem` row at that instant, exactly as its comment says.
There is nothing to link *to*, so no backfill can be correct.

**A probabilistic join was not accepted, and none was available to accept.**

---

## 9. Minimum safe instrumentation design — **DESIGN ONLY, NOTHING IMPLEMENTED**

### D1 — `causal_events.news_id`, forward only

| | |
|---|---|
| file / function | `news_discovery_engine.py:932`, the `CausalEvent(...)` construction |
| field | `news_id` (exists on the model; the relationship is already defined) |
| value | the `NewsItem.id` of the row inserted for the same headline earlier in the same cycle |
| source of value | the `ON CONFLICT DO NOTHING` insert at `:1437` — currently discards the returned id; `RETURNING id` would surface it |
| nullable | **yes, must stay nullable** — the engine can legitimately create an event with no news row |
| backward-compatible | yes — additive, no schema change |
| migration | **none** |
| historical backfill | **impossible** (§8) |
| backfill deterministic | n/a |
| risk of incorrect attribution | **moderate** — one cycle can insert several headlines and create several events; naive pairing would mislink. Any implementation must carry the id through the same call, not re-derive it by lookup |

### D2 — `simulation_logs` emitting-process identity

| | |
|---|---|
| file / function | `engine/decision_router.py:975` `_log_intent_audit`, the `SimulationLog(data={...})` payload |
| field | one new key inside the existing `data` JSON — e.g. `"emitter"` |
| value | process name + whether `PYTEST_CURRENT_TEST` is set in the environment |
| nullable | yes |
| backward-compatible | yes — JSON key, no schema change |
| migration | **none** |
| historical backfill | **partially deterministic**: TESTCO rows are attributable with certainty; others are not |
| risk | low — additive metadata only |

This single field would have made §3 a one-query answer instead of a code trace.

### D3 — structured replacement for `AgentDecision.skip_reason`

| | |
|---|---|
| file / function | the four `AgentDecision(...)` writers (`news_discovery_engine.py:1292`, `decision_engine.py:2325`, `execution.py:136`, `agent_loop.py:1155`) |
| field | a **new** `skip_code` column; `skip_reason` prose **retained unchanged** |
| value | an enum drawn from the categories Phase 6 derived — but only the ones the code can assert, not ones inferred from prose |
| nullable | yes — old rows stay NULL |
| backward-compatible | yes, if additive |
| migration | **required** — one nullable column |
| historical backfill | **no.** Phase 6 measured 92.0% unique strings, 38.7% unclassifiable and 13.9% ambiguous. Regex-backfilling that would manufacture a taxonomy, not recover one |
| risk of incorrect attribution | **high if backfilled, low if forward-only** |

**None of D1–D3 was implemented. No schema was modified. No migration was written. No code was
edited.**

---

## 10. Actual news data model

```
NewsItem                                         db/models.py
  │
  ├─[EXACT]──────────  crawler/event_pipeline.py:53/69/155   news_id set
  │                    → dominant 2026-07-16 → 07-21 only
  │
  └─[OBSERVABILITY GAP]  news_discovery_engine.py:932        news_id=None (documented)
                         → dominant 2026-07-22 → present
  ↓
CausalEvent                                      db/models.py:1708
  │
  └─[OBSERVABILITY GAP]  no FK on AgentDecision; the link is the ticker string only
  ↓
AgentDecision                                    news_discovery_engine.py:1292
  │                                              engine/agent/decision_engine.py:2325
  │                                              engine/agent/execution.py:136
  │                                              engine/agent/agent_loop.py:1155
  │
  └─[OBSERVABILITY GAP]  order_id NULL on all 11 non-SKIP rows; no FK to the gate
  ↓
DecisionRouter          engine/decision_router.py::execute_trade_intent
  │
  └─[EXACT]──────────  _log_intent_audit(...) writes the intent payload verbatim
  ↓
ExecutionGate           simulation_logs, event_type='EXECUTION_GATE'
  │                     data.event_id → causal_events.id       [EXACT]
  │
  └─[INFERRED]────────  symbol + timestamp only; no FK
  ↓
PaperTrade                                       paper_trading/trade_simulator.py
```

**Three gaps, one exact link in the middle.** The chain is auditable only from the router
onward — the same boundary Phase 6 identified.

---

## 11. Proven / unproven / unknown

### Proven

| # | finding | classification |
|---|---|---|
| 1 | TESTCO is test traffic from `tests/test_integration_pipeline.py::TestFullApprovedFlow` | **CONFIRMED** |
| 2 | Mechanism: the test patches `news_discovery_engine.AsyncSessionLocal`; `direct_news_strategy.py:214/281` imports and opens its own from `db.database` | **CONFIRMED** |
| 3 | 144 rows, 1 event_id, 1 confidence, 1 family, 0 paper trades | **CONFIRMED** |
| 4 | `causal_event 2848` is a real TVS Motor earnings event with a valid `news_id` | **CONFIRMED** |
| 5 | `news_id` was 100% populated 07-16 → 07-21, 0% from 07-22 | **CONFIRMED** |
| 6 | The NULL is deliberate and documented in code, not a defect | **RULED OUT as a bug** |
| 7 | Historical `news_id` is unrecoverable — 0 candidates on every key | **CONFIRMED** |
| 8 | `simulation_logs.timestamp` is naive UTC | **CONFIRMED** — corrects Phase 6 |
| 9 | No production code references TESTCO | **CONFIRMED** |
| 10 | TESTCO wrote 0 rows to `paper_trades` | **RULED OUT** |

### Unproven

- **BUG-2 under market load.** §2.
- **Whether other tests also write to production tables.** Only the TESTCO path was traced;
  `tests/test_execution.py` (13 `execute_trade_intent` calls, 18 `AsyncSessionLocal` references)
  was **not** traced and was **not run**. **EVIDENCE NOT AVAILABLE.**
- **Whether the 2026-07-22 shift was intentional at the time** or a side effect of the news-only
  pivot. The code comment explains the *design*; nothing records the *decision*.

### Unknowns and observability gaps

| # | gap |
|---|---|
| G1 | `causal_events` → `news_items` link, forward and historical |
| G2 | `simulation_logs` has no emitter identity — test and production traffic are indistinguishable at query time |
| G3 | `AgentDecision` has no FK to either the causal event or the gate row |
| G4 | `AgentDecision.order_id` NULL on all non-SKIP rows |
| G5 | Celery logs nothing on task expiry, so §2's mechanism stays unprovable either way |
| G6 | No other test file was audited for production writes |

---

## 12. ⚠ Disclosed database mutation — caused by me, in earlier phases

**This did not happen during Phase 7**, which was read-only. It happened earlier in this session
and Phase 7 is how I found it.

`simulation_logs` contains **10 TESTCO rows dated 2026-08-25**, at these IST times:

```
16:59:04  16:59:07      17:36:56  17:36:58      17:43:58  17:44:00
17:51:49  17:51:53      20:34:13  20:34:16
```

Those five pairs fall inside the windows in which **I ran `pytest tests/`** — four during Phase 1B
(including the stashed-baseline comparison runs) and one during Phase 5. The last pair, 20:34 IST,
matches the Phase 5 full-suite run exactly.

**I wrote 10 rows into the production `simulation_logs` table**, indirectly, by running the test
suite the way CLAUDE.md documents. The other 134 rows predate this session (08-19 → 08-24).

Impact: `simulation_logs` is an audit table, not a trading table. No trade, order, position or
balance was affected — `paper_trades` has zero TESTCO rows. The contamination distorted **my own
Phase 6 analysis**, inflating the DIRECT_NEWS execution count from 8 to 142 until I isolated it.

**Not fixed, per the brief.** Nothing was deleted or modified.

---

## 13. Recommended next experiment

**Two measurements, in order, both cheap.**

1. **Complete BUG-2 verification** after the next session — the same query, still outstanding
   since Phase 5. Until it runs, BUG-2 is *deployed*, not *fixed*.

2. **Audit the remaining test files for production writes.** §11 flags
   `tests/test_execution.py` as the leading candidate on static grounds (13 `execute_trade_intent`
   calls, 18 `AsyncSessionLocal` references). The audit is static — grep each test's patch targets
   against the import sites its code path actually uses. **Do not run them to find out.**

   The general defect is worth naming: *a test that patches a symbol in module A does not protect
   against module B importing the same symbol from its own source.* One `conftest.py` fixture
   patching `db.database.AsyncSessionLocal` at source would close the whole class — but that is a
   change, and this phase proposes nothing.

**Not recommended:** deleting the TESTCO rows. They are the only evidence of the defect, they
sit in an audit table, and any future analysis can exclude them with one predicate — as Phase 6
did once it knew.

---

## Final safety statement

| | |
|---|---|
| production files modified | **NO** |
| `.env` modified | **NO** |
| runtime settings modified | **NO** |
| strategy parameters changed | **NO** |
| Master Intelligence connected | **NO** |
| orders submitted | **NO** |
| paper trades opened | **NO** |
| database INSERT/UPDATE/DELETE **during Phase 7** | **NO** — SELECT only |
| database INSERT/UPDATE/DELETE **disclosed from earlier phases** | **YES — 10 rows, §12** |
| execution modules invoked | **NO** |

Working tree clean at HEAD `fec95f3` throughout. No suspected writer was executed.

**PHASE 7 WAS READ-ONLY FORENSICS AND DESIGN. NOTHING WAS FIXED. STOP.**
