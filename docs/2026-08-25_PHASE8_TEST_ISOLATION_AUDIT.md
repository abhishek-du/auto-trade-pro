# PHASE 8 — TEST ISOLATION + LIVE QUEUE VERIFICATION

**Mode:** read-only static forensics + design. **No tests were executed.** No production file,
`.env`, runtime setting or database row was touched.

---

## 1. Executive verdict

> **`pytest` run from this checkout CAN write to the production database. CONFIRMED.**
> There is no test-database, no `DATABASE_URL` override, no session-factory fixture and no
> rollback fixture anywhere in the project. `tests/conftest.py` provides **zero** database
> isolation — and one of its two autouse fixtures actively removes the market-hours gate that
> would otherwise have blocked the writes that produced the TESTCO rows.

| objective | outcome |
|---|---|
| **A — BUG-2 market-hours verification** | **EVIDENCE NOT AVAILABLE.** Still no session since deployment. |
| **B/C/D — test execution inventory** | Complete. The risk surface is **narrow and specific**, not systemic. |
| **E — global isolation** | **None exists.** This is the root cause. |
| **G — TESTCO reconciliation** | 10 of 144 rows attributable to this session with certainty; the rest **STRONGLY SUPPORTED**. |

**Two Phase 7 claims are corrected below** — one of them was my own miscount, and it inverted
the priority this phase was given.

---

## 2. BUG-2 market-hours verification — **EVIDENCE NOT AVAILABLE**

System clock: **2026-08-25 22:32 IST**. The Phase 5 fix deployed at 20:01 IST the same day.

```
trade-worker cycles by date        : 2026-08-25 only (151)
cycles inside 09:15-15:30 IST      : 0
last cycle                         : 2026-08-25 22:31:03
```

Cycle classification, per the brief's four categories:

| class | count |
|---|---:|
| A — returned before `:610` because the market was closed | **151 (100%)** |
| B — reached `:610` and hit `UnboundLocalError` | **0** |
| C — completed past `:610` | **0** |
| D — unknown | 0 |

Every cycle returned at the market-status check on `:524`. Hub-worker concurrency, queue
isolation under load, expired-task evidence and CPU/memory contention are all **unmeasured**,
because the condition that produces them — an open market — has not occurred.

**This is the third consecutive phase in which this measurement has been requested and could not
be taken.** It is one query; it simply requires a trading day to elapse. Extrapolating from
after-hours cycles is explicitly refused.

---

## 3. Complete test execution inventory

Static AST parse of every `tests/test_*.py`. Nothing was imported or run.

| file | exec calls (AST) | `AsyncSessionLocal` refs | session patch target |
|---|---:|---:|---|
| `test_decision_router.py` | 26 | 0 | — none needed — |
| `test_short_guards.py` | 7 | 14 | — none — |
| `test_pre_event_gap_foundation.py` | 5 | 0 | — none needed — |
| `test_pre_event_gap_phase6.py` | 3 | 0 | — none needed — |
| `test_technical_origination_unblocked.py` | 2 | 4 | — none — |
| `test_kite_limiter.py` | 2 | 0 | — none needed — |
| `test_live_order_path.py` | 1 | 0 | — none needed — |
| `test_execution.py` | **0** | 18 | `news_discovery_engine.AsyncSessionLocal` |
| `test_integration_pipeline.py` | **0** | 9 | `news_discovery_engine.AsyncSessionLocal` |
| `test_event_pipeline.py` | 0 | 8 | `news_discovery_engine.AsyncSessionLocal` |
| `test_reentry_watch.py` | 0 | 8 | `news_discovery_engine.AsyncSessionLocal` |
| `test_anomaly_catalyst_investigation.py` | 0 | 7 | `news_discovery_engine.AsyncSessionLocal` |
| **`test_upstox_isin.py`** | 0 | 7 | **`db.database.AsyncSessionLocal`** ← correct |
| **`test_ticker_subscription_sync.py`** | 0 | 6 | **`db.database.AsyncSessionLocal`** ← correct |
| `test_alert_threading.py` | 0 | 5 | — none — |

Breakdown of direct execution calls:

```
test_decision_router.py                 authorize_trade_intent 24 · execute_trade_intent 2
test_short_guards.py                    authorize_trade_intent 7
test_pre_event_gap_foundation.py        authorize_trade_intent 5
test_pre_event_gap_phase6.py            authorize_trade_intent 3
test_technical_origination_unblocked.py authorize_trade_intent 2
test_kite_limiter.py                    place_real_order 2
test_live_order_path.py                 route_decision 1
```

**The correct patch idiom already exists in this codebase** — `test_upstox_isin.py` and
`test_ticker_subscription_sync.py` patch `db.database.AsyncSessionLocal` at source. The fix is a
known local pattern, not a novel design.

---

## 4. `tests/test_execution.py` — **Phase 7's priority was based on my miscount**

Phase 7 flagged this file as "the priority: 13 `execute_trade_intent` calls". That number came
from `grep -c "execute_trade_intent\|authorize_trade_intent"`, which counts **lines mentioning
the name** — including patch strings.

The AST parse returns **0 execution calls**. All 13 lines are patch targets:

```python
tests/test_execution.py:175   patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute))
tests/test_execution.py:207   patch("engine.decision_router.execute_trade_intent", AsyncMock(side_effect=_fake_execute))
…  (13 in total, all of this form)
```

**The file patches the execution function correctly and never calls the real one.**
**Classification: SAFE. Phase 7's "highest priority" designation is withdrawn.**

*(Correcting this matters beyond bookkeeping: it moves the priority to where the writes actually
came from.)*

---

## 5. Production DB writer matrix

### The mechanism, stated precisely

`authorize_trade_intent` uses the **passed** session for everything —
`_log_intent_audit(..., session)`, `RuntimeConfig.load(session)`,
`_verify_canonical_event(intent, session)`. Verified across its whole body: **no self-opened
session**.

So a test that passes a mock session **directly** to `authorize_trade_intent` /
`execute_trade_intent` cannot write. That is why `test_decision_router.py` — 26 execution calls,
zero session patches — produces nothing.

The danger is one level up: a test calling an **orchestrator** that opens its own session before
delegating.

```
engine/direct_news_strategy.py:214    from db.database import AsyncSessionLocal   ← function-local import
engine/direct_news_strategy.py:281    async with AsyncSessionLocal() as session:   ← REAL production session
                                        → execute_trade_intent(intent, session)
                                          → _log_intent_audit(..., session)
                                            → session.add(SimulationLog(...)) ; await session.commit()
```

`patch("news_discovery_engine.AsyncSessionLocal", …)` rebinds the name **only in
`news_discovery_engine`'s namespace**. `direct_news_strategy` re-imports from `db.database` at
call time and receives the original. The patch cannot reach it.

### The matrix

| test | orchestrator called | DB dependency source | patched at source? | production write possible | tables | risk |
|---|---|---|---|---|---|---|
| **`test_integration_pipeline.py`** | `process_ticker` ×8 | `direct_news_strategy` :214/:281 | **no** — patches `news_discovery_engine` | **YES — observed** | `simulation_logs` | **UNSAFE** |
| `test_direct_news_strategy.py` | `maybe_direct_trade` ×7 | same | no | **reads yes, writes no** — it patches `engine.decision_router.execute_trade_intent`, so the audit write is never reached | — | **CONDITIONALLY SAFE** |
| `test_news_side_from_classifier.py` | `maybe_direct_trade` ×1 | same | no | **UNKNOWN** — no session patch and no `execute_trade_intent` patch found | — | **UNKNOWN** |
| `test_anomaly_catalyst_investigation.py` | `_run_anomaly_scan` / `process_ticker` | `news_discovery_engine` | patches `news_discovery_engine.AsyncSessionLocal` | **INCONCLUSIVE** — protected only where the engine's own name is used | — | **CONDITIONALLY SAFE** |
| `test_news_engine_loop_reachability.py`, `test_nse_poller_decoupling.py` | `run_news_discovery_loop` | AST-only / mocked fetch | n/a | no | — | SAFE |
| `test_decision_router.py`, `test_short_guards.py`, `test_pre_event_gap_*`, `test_technical_origination_unblocked.py`, `test_live_order_path.py` | none — direct calls with mock sessions | passed session | n/a | no | — | SAFE |
| `test_execution.py` | none — patches the executor | n/a | n/a | no | — | SAFE |
| `test_kite_limiter.py` | `place_real_order` ×2 | broker client, mocked | n/a | **order path: RULED OUT** — `PAPER_MODE=true` and the client is mocked | — | SAFE |
| `test_upstox_isin.py`, `test_ticker_subscription_sync.py` | — | `db.database` | **yes** | no | — | SAFE |

**Production modules that import `AsyncSessionLocal` inside a function** — the general risk
surface, unreachable by any module-level patch of a *different* module:
`engine/agent/decision_engine.py` (7), `engine/direct_news_strategy.py` (1),
`utils/runtime_config.py` (1), `utils/llm.py` (1), `utils/sector_cache.py` (1).

---

## 6. Global pytest isolation audit — **the root cause**

`tests/conftest.py` is 59 lines with two autouse fixtures. **Neither concerns the database.**

| question | answer |
|---|---|
| Does pytest use a test database? | **NO** |
| Does pytest override `DATABASE_URL`? | **NO** |
| Does `conftest.py` replace `AsyncSessionLocal` globally? | **NO** |
| Does it patch `db.database.AsyncSessionLocal`? | **NO** |
| Does it patch every imported alias? | **NO** — it patches none |
| Is rollback guaranteed? | **NO** — no transaction fixture exists |
| Are external side effects blocked? | **PARTIALLY** — network snapshot calls are stubbed; the DB is not |
| **Can pytest write to the production DB?** | **YES — CONFIRMED** |

`db/database.py` builds `AsyncSessionLocal` at **import time** from `settings.DATABASE_URL`, with
**no pytest-aware branch anywhere** in `db/database.py` or `utils/config.py`. `pytest.ini`
contains no DB-related setting.

### The aggravating factor

`conftest.py`'s first autouse fixture:

```python
with patch("crawler.india_price_feed.is_nse_market_open", return_value=True):
```

This is well-reasoned for its stated purpose — without it, gate tests would pass or fail
depending on the time of day the suite runs. But its effect is that **during every test, the
router's market-hours block is disabled**. The 10 TESTCO rows I created were written at
16:59–20:34 IST — hours after the close — and reached `EXECUTED_PAPER` precisely because this
fixture had removed the gate that exists to prevent exactly that.

**The fixture is not the bug.** The bug is that a fixture designed to make gate tests
deterministic ends up removing a safety gate against a *real* database, because nothing else
stands between the test suite and production.

---

## 7. Test safety classification

| classification | tests | basis |
|---|---|---|
| **UNSAFE** | `test_integration_pipeline.py` | **CONFIRMED** — 144 rows in `simulation_logs` bearing its own stack text |
| **CONDITIONALLY SAFE** | `test_direct_news_strategy.py` | opens real sessions for reads; writes blocked only because it patches `execute_trade_intent`. Remove that patch and it becomes UNSAFE |
| **CONDITIONALLY SAFE** | `test_anomaly_catalyst_investigation.py` | protected only where the engine's own `AsyncSessionLocal` name is used |
| **UNKNOWN** | `test_news_side_from_classifier.py` | calls `maybe_direct_trade` with neither a source-level session patch nor an `execute_trade_intent` patch. **Static evidence insufficient — and it was not run to find out** |
| **SAFE** | all others in §5 | mock sessions passed directly, or the executor patched, or the source-level patch used |

**No test was executed to distinguish these.** The UNKNOWN stays UNKNOWN.

---

## 8. TESTCO reconciliation

| | count | basis |
|---|---:|---|
| total rows | **144** | `simulation_logs`, symbol `TESTCO.NS` |
| attributable to **this session** | **10** | timestamps inside my four Phase 1B suite runs and one Phase 5 run |
| — of which Phase 1B | **8** | 16:59, 17:36, 17:43, 17:51 IST (four pairs) |
| — of which Phase 5 | **2** | 20:34 IST, matching the Phase 5 full-suite run |
| historical (08-19 → 08-24) | **134** | predate this session |
| attributable to the same test | **all 144** | **STRONGLY SUPPORTED** — 1 `event_id`, 1 confidence, 1 family, 1 symbol, arriving in 3–4 s pairs ≈ 72 invocations |
| distinguishable by emitter metadata | **NO** | `simulation_logs` records no emitter — the only reason attribution was possible is the stack text that happened to land in an `ERROR` payload |

**Could another test have created them? RULED OUT for the two files that patch at source; not
formally excluded for `test_news_side_from_classifier.py`** (§7). The single `event_id 2848`
across all 144 rows makes a single fixture origin **STRONGLY SUPPORTED**.

Shell history and pytest invocation logs: **EVIDENCE NOT AVAILABLE** — neither is retained.
Attribution rests entirely on timestamps plus the embedded stack text.

**Nothing was deleted or modified.**

---

## 9. Root cause of test DB contamination

```
1. No test database, no DATABASE_URL override, no session fixture      ← the root cause
2. db/database.py builds AsyncSessionLocal at import from the prod URL
3. conftest.py autouse fixture disables the market-hours gate
4. A test patches news_discovery_engine.AsyncSessionLocal
5. direct_news_strategy.py re-imports from db.database at call time    ← the patch misses
6. execute_trade_intent receives a REAL session
7. _log_intent_audit writes and commits to production
```

**Steps 4–5 are the proximate defect. Step 1 is the cause.** Fixing the patch target in one test
closes one hole; only step 1 closes the class.

---

## 10. Recommended test isolation architecture — **DESIGN ONLY, NOT IMPLEMENTED**

| # | approach | safety | complexity | coverage | hidden-bypass risk | async-SQLA fit | prod code change? |
|---|---|---|---|---|---|---|---|
| 1 | patch `db.database.AsyncSessionLocal` in an autouse fixture | medium | low | high | **real** — misses `engine = create_async_engine(...)`, and any module holding a pre-bound reference | good | no |
| 2 | dependency injection (pass a factory everywhere) | high | **very high** | complete | low | good | **yes, extensive** |
| 3 | dedicated test database | high | medium | complete | low | good | no |
| 4 | transaction-rollback fixture | medium | medium | high | writes still reach the server; rollback can be defeated by code that commits | fair | no |
| 5 | explicit simulation DB | high | medium | complete | low | good | no |
| 6 | **block the production `DATABASE_URL` under pytest** | **highest** | **low** | **complete** | **very low** — fails at connect, before any code path matters | good | **no** |
| 7 | 6 + 3 combined | highest | medium | complete | very low | good | no |

### Recommendation: **6, optionally extended to 7**

The brief's standard is *"pytest must be incapable of writing to the production database"*, not
*"pytest normally doesn't"*. Only approach 6 meets that as stated, because it is the only one
that does not depend on every future test patching the right name.

Approaches 1 and 4 are the tempting ones and both fail the standard: option 1 is defeated by any
module that captures a reference before the fixture runs (and by `engine` itself, which the patch
does not touch); option 4 still opens a production connection and is defeated by any code that
commits — which is exactly what `_log_intent_audit` does.

Approach 6 is also the smallest change: it is **test-only**, requires no production edit, and
needs no migration.

---

## 11. Recommended fail-closed guard — **DESIGN ONLY**

An autouse, session-scoped fixture in `tests/conftest.py` that runs before any test and raises
if the configured database is not a test database:

| property | design |
|---|---|
| mechanism | inspect `settings.DATABASE_URL`; **allowlist** the test database name/host, do not denylist the production one |
| direction | **fail closed** — an unrecognised URL aborts the session; an unset URL aborts |
| timing | session-scoped, `autouse=True`, ordered before every other fixture |
| what it protects | every test, including ones not yet written |
| second layer | assert `settings.PAPER_MODE is True` and that no live broker token is configured, so the order path is unreachable even if the DB check were bypassed |
| failure mode | `pytest.exit()` with the offending URL redacted |

**Allowlist, not denylist**, is the load-bearing choice: a denylist of known production hosts
silently permits any host nobody thought to add.

**Not implemented. No fixture was written, no `conftest.py` edited.**

---

## 12. What must NOT be changed yet

- **BUG-1** — still deliberately unfixed; Hub origination remains unreachable.
- **BUG-2** — not modified in this phase; still awaiting its market-hours measurement.
- **TESTCO rows** — not deleted. They are the only evidence of the defect and they sit in an
  audit table.
- **`causal_events.news_id`** — untouched, per Objective J.
- **Nothing in `tests/`** — no patch target corrected, no fixture added.

---

## 13. Findings

### Proven

| # | finding | classification |
|---|---|---|
| 1 | pytest can write to the production database from this checkout | **CONFIRMED** |
| 2 | No test DB, no URL override, no session fixture, no rollback fixture exists | **CONFIRMED** |
| 3 | `conftest.py` disables the market-hours gate for every test | **CONFIRMED** |
| 4 | `test_integration_pipeline.py` is the writer of the 144 TESTCO rows | **CONFIRMED** |
| 5 | The patch misses because `direct_news_strategy` re-imports from `db.database` | **CONFIRMED** |
| 6 | `authorize_trade_intent` never self-opens a session — mock-session tests are safe | **CONFIRMED** |
| 7 | `test_execution.py` has 0 real execution calls; Phase 7's "13" were patch targets | **CONFIRMED — corrects Phase 7** |
| 8 | The correct source-level patch idiom already exists in 2 test files | **CONFIRMED** |
| 9 | 10 of 144 TESTCO rows are attributable to this session | **CONFIRMED** |
| 10 | All 144 share one `event_id`, confidence, family and symbol | **CONFIRMED** |
| 11 | TESTCO wrote to `paper_trades` | **RULED OUT** — 0 rows |
| 12 | An order could have been submitted by any test | **RULED OUT** — `PAPER_MODE=true`, broker clients mocked |

### Unproven

- **BUG-2 under market load** — §2, third phase running.
- **`test_news_side_from_classifier.py`'s safety** — **UNKNOWN**, and not resolved by running it.
- **Whether the 134 historical TESTCO rows came from the same test** — **STRONGLY SUPPORTED**
  (one `event_id`, one fixture shape), not CONFIRMED: only 2 of 144 rows carry the stack text.
- **Whether any test has ever written to `paper_trades`, `agent_decisions` or `causal_events`** —
  not audited; only the `simulation_logs` path was traced. **EVIDENCE NOT AVAILABLE.**

### Unknowns and observability gaps

| # | gap |
|---|---|
| G1 | `simulation_logs` records no emitter — attribution depended on an error string landing in a payload by luck |
| G2 | No pytest invocation log or shell history is retained |
| G3 | Only the `simulation_logs` write path was traced; other tables were not |
| G4 | `engine/agent/decision_engine.py` has 7 function-local `AsyncSessionLocal` imports, none traced against the tests that exercise it |
| G5 | Celery still logs nothing on task expiry, keeping §2's mechanism unprovable either way |

---

## 14. Recommended next experiment

**Two, in strict order.**

1. **Take the BUG-2 measurement.** One query after the next session. It has been outstanding since
   Phase 5 and blocks nothing else — but until it is taken, BUG-2 is *deployed*, not *fixed*.

2. **Then decide on the guard in §11 — before writing any more tests.** Every new test written
   against this checkout is written against a suite that can reach production. The guard is
   test-only, needs no migration and no production edit, and is the difference between "we fixed
   the one test we found" and "this cannot happen again".

**Not recommended:** correcting `test_integration_pipeline.py`'s patch target on its own. It
closes one hole and leaves the class open, and it would remove the evidence that motivates the
guard.

**Also not recommended:** deleting the TESTCO rows. One predicate excludes them from any
analysis, as Phase 6 did once it knew.

---

## Final safety statement

| | |
|---|---|
| production files modified | **NO** |
| `.env` modified | **NO** |
| runtime settings modified | **NO** |
| strategy parameters changed | **NO** |
| Master Intelligence connected | **NO** |
| BUG-1 fixed | **NO** |
| BUG-2 modified during this phase | **NO** |
| orders submitted | **NO** |
| paper trades opened | **NO** |
| database INSERT/UPDATE/DELETE during Phase 8 | **NO** |
| **tests executed** | **NO** |
| execution modules invoked | **NO** |

Working tree clean at HEAD `fec95f3` throughout. No suspected writer was run. No unexpected
mutation occurred.

**PHASE 8 WAS READ-ONLY FORENSICS AND DESIGN. NOTHING WAS FIXED. STOP.**
