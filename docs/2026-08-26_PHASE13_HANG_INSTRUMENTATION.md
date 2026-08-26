# PHASE 13 — TRADE-LOOP HANG INSTRUMENTATION

**Implementation + test report. NOT DEPLOYED to the executing worker — see §D.**
Logging only: 44 insertions, 0 deletions, across 2 production files.

---

## A. Implementation summary

### Pre-change call graph

```
tasks/india_tasks.py::_india_trade_loop
  :535  await fetch_live_snapshot()                       HTTPS   logs
  :538  async with celery_session() as session:           Postgres
  :542  await update_positions_with_current_prices()      Postgres  logs conditionally
  :549  await publish(AlertEvent(...))                    Telegram  no log
  :561  await llm_dynamic_sl_tp(session)                  →         NO LOG AT ALL
  :571  await VirtualWallet.check_drawdown_breakers()     Postgres  on failure only
  :610  BUG-1 raises here
        │
        └─ engine/agent/dynamic_management.py::llm_dynamic_sl_tp
             :148  throttle — return if within _MANAGE_INTERVAL_SEC
             :154  positions = select(OpenPosition)  → return if empty
             :309  await asyncio.gather(*[_manage_pos(p) for p in positions])   ← CONCURRENT
                     └─ _manage_pos :166
                          :242  resp = await call_llm_chat(...)
                                  │
                                  └─ utils/llm.py
                                       _acquire_llm_rate_slot  — while-loop, sleeps 1s,
                                                                  gives up after 90s
                                       Config(read_timeout=90, connect_timeout=10,
                                              retries={"max_attempts": 0})
                                       await asyncio.to_thread(client.converse)
```

**Two facts from this graph shaped the design:**

1. `:309` runs every position **concurrently** through `asyncio.gather`, so total
   duration is bounded by the slowest call, not their sum. A single boundary
   around `llm_dynamic_sl_tp` would give the total but could not distinguish
   *"one call stalled"* from *"every call queued behind the shared rate limiter"*.
2. Per position the worst case is **up to 90 s waiting for a rate slot plus up to
   90 s read timeout**. That makes the second boundary necessary rather than
   optional, and it is the brief's permitted maximum.

### The two boundaries

**1. `tasks/india_tasks.py` :559 — the dynamic-management boundary**

```python
import time as _t13
_dyn_t0 = _t13.monotonic()
logger.info("[india_trade_loop] LLM_DYNAMIC_SL_TP_START")
try:
    from engine.agent.dynamic_management import llm_dynamic_sl_tp
    await llm_dynamic_sl_tp(session)
    logger.info(f"...LLM_DYNAMIC_SL_TP_END ok=True elapsed_ms={...}")
except Exception as e:
    logger.error(f"[india_trade_loop] Dynamic management failed: {e}")   # UNCHANGED
    logger.info(f"...LLM_DYNAMIC_SL_TP_ERROR exc={type(e).__name__} elapsed_ms={...}")
```

**2. `engine/agent/dynamic_management.py` :242 — the per-position call**

```python
_llm_t0 = _time.monotonic()
logger.info(f"[dynamic_mgmt] BEDROCK_CALL_START {pos.symbol}")
try:
    resp = await call_llm_chat(...)                                      # UNCHANGED
    logger.info(f"...BEDROCK_CALL_END {pos.symbol} ok=True "
                f"elapsed_ms={...} chars={len(resp or '')}")
```

### Why each line is necessary

| line | why |
|---|---|
| `_dyn_t0 = monotonic()` / `_llm_t0 = monotonic()` | monotonic per the brief; a wall clock would misreport across NTP steps |
| `LLM_DYNAMIC_SL_TP_START` | **a START with no END is the signal** — that is exactly what a 600 s `SIGKILL` looks like from the log |
| `LLM_DYNAMIC_SL_TP_END … elapsed_ms` | total duration of the suspect block |
| `LLM_DYNAMIC_SL_TP_ERROR … exc=` | records the exception class on the path that already caught it |
| `BEDROCK_CALL_START {symbol}` | per-position, so concurrent calls are countable and attributable |
| `BEDROCK_CALL_END … elapsed_ms chars=` | per-call latency; `chars` is a **length**, deliberately not the response |

### Exception semantics — unchanged

The existing `except Exception as e:` and its message
`"[india_trade_loop] Dynamic management failed: {e}"` are **byte-for-byte
identical**. No new catch was added, nothing is swallowed that was not, and no
`raise` was introduced. The added `logger.info` sits *inside* the existing
handler. A test asserts the catch type is still `Exception` and that no `Raise`
node exists in it.

Nothing branches on the timing — a test walks every `If`/`While` in both
functions and fails if any test expression references `elapsed`, `_dyn_t0` or
`_llm_t0`.

### Not logged

Prompts, model responses, messages, credentials, API keys, Authorization
headers, request bodies, account or order payloads. Only a symbol, an elapsed
time, an exception class, and a response **length**.

---

## B. Test results

`tests/test_trade_loop_hang_instrumentation.py` — **10 passed**.

| brief item | test |
|---|---|
| A start log emitted | `test_bedrock_boundary_logs_start_and_end_with_elapsed` |
| B end log emitted | same |
| C elapsed_ms present, non-negative | same — also bounds it below 60 s for a mocked call |
| D exception path records the class | `test_bedrock_boundary_does_not_swallow_an_llm_failure` |
| E exception semantics unchanged | `test_call_site_exception_semantics_are_byte_identical` (AST) |
| F no sensitive payload | `test_no_prompt_response_or_credential_is_logged` |
| G behaviour unchanged but for logging | `test_instrumentation_does_not_branch_on_its_own_timing` |
| static: Celery/queue untouched | `test_celery_time_limits_and_queue_config_untouched` |
| static: BUG-1 untouched | `test_bug1_is_still_present` |
| static: strategy params untouched | `test_strategy_parameters_untouched` |
| monotonic clock | `test_timing_uses_a_monotonic_clock` ×2 |

**Mutation-tested — five mutations, all caught:**

| mutation | result |
|---|---|
| remove the `BEDROCK_CALL_END` log | **3 fail** |
| time with `time.time()` instead of `monotonic()` | **2 fail** |
| log the raw model response instead of its length | **1 fail** |
| change `task_soft_time_limit` 300 → 900 | **1 fail** |
| add a `raise` to the existing handler | **1 fail** |

**Full suite against `autotrade_test`: 1,739 passed · 27 failed · 7 skipped ·
5 errors.**
Phase 10 baseline: 1,729 / 27 / 7 / 5. **+10 passing, exactly the new tests.
ZERO new failures** — failure sets diffed line by line.

### Two test corrections worth recording

1. My first monotonic check scanned the whole function for any wall-clock
   arithmetic and failed on `datetime.utcnow() - timedelta(minutes=60)` — a
   **pre-existing news-lookback window** unrelated to timing. **The check was
   wrong, not the code.** It now inspects only the f-strings that compute
   `elapsed_ms`.
2. The runtime tests first INSERTed a real `open_positions` row and hit a moving
   target — that table has 21 NOT NULL columns including `trade_id`. Rewritten
   on a stub session: the boundary under test is *logging*, not persistence, and
   the fail-closed guard still points every session at `autotrade_test`.

### Database audit

| table | before | after |
|---|---:|---:|
| `simulation_logs` | 18,488 | 18,488 (**+0**) |
| `paper_trades` | 57 | 57 (**+0**) |
| `news_items` | 37,650 | 37,650 (**+0**) |
| TESTCO rows | 144 | 144 (**+0**) |
| `agent_decisions` | 8,306 | 8,311 (+5) |
| `causal_events` | 11,918 | 11,924 (+6) |

The +5/+6 are the **live news engine** — the market is open, and the newest rows
are `strategy=NEWS` at 12:31–12:33 IST during the test run. **Production rows
marked `emitter.pytest=true`: 0.**

---

## C. Diff summary

```
autotrade-backend/engine/agent/dynamic_management.py  | 17 +++
autotrade-backend/tasks/india_tasks.py                | 27 +++
2 files changed, 44 insertions(+), 0 deletions(-)
```

Every added non-comment line is a `logger.info` or a `monotonic()` assignment —
listed in full in §A. **No deletions. No modified lines.**

| confirmation | |
|---|---|
| production strategy logic changed | **NO** |
| timeout / queue / expires settings changed | **NO** |
| database schema changed | **NO** — no migration |
| `.env` changed | **NO** — mtime still 2026-08-25 08:15 |
| BUG-1 changed | **NO** |
| Master Intelligence connected | **NO** |

---

## D. ⚠ Current deployment state — partially loaded, and NOT where it matters

The brief says do not deploy. **The behaviour-relevant deployment has not
happened, but the working-tree edit was partially picked up automatically** —
the same `watchmedo` property documented in Phases 4 and 10. Stated precisely:

| service | mechanism | has the new code? | executes this path? |
|---|---|---|---|
| `autotrade-celery-worker` (default) | watchmedo | **yes** — child restarted 12:28:54, after the 12:28:41 edit | **no** |
| `autotrade-news-engine` | watchmedo | **yes** — restarted | **no** |
| `autotrade-celery-exit-worker` | watchmedo | yes | **no** |
| `autotrade-celery-scan-worker` | direct | child from 12:32:50 | **no** |
| **`autotrade-celery-trade-worker`** | **direct** | **NO** — pid 3239565 from 12:03:17, before the edit | **YES — the only one** |

`llm_dynamic_sl_tp` is reached **only** from `india_trade_loop`, which runs
**only** on `trade_queue`. So the instrumentation is currently loaded into
services that never execute it, and absent from the one that does.

**Confirmed empirically:** `LLM_DYNAMIC_SL_TP_START` / `BEDROCK_CALL_START`
appear **0 times** in every log.

**Nothing is measuring yet, and nothing in production behaves differently.**

---

## E. Deployment plan — awaiting approval

One command:

```bash
systemctl --user restart autotrade-celery-trade-worker
```

- restarts **one** service, the only executor of this path
- no migration, no schema change, no config change
- the watchmedo services already carry the code and need nothing
- **timing:** the market closes 15:30 IST. Restarting mid-session interrupts an
  in-flight cycle — acceptable given BUG-1 terminates most cycles in ~1 s anyway,
  but restarting **after 15:30** costs nothing and risks nothing. **My
  recommendation: after the close**, unless the goal is to catch today's hang
  window, which has already ended (60 s cadence resumed at 12:07).

## F. Rollback plan

```bash
git revert <commit>                                   # or: git checkout -- the two files
systemctl --user restart autotrade-celery-trade-worker
# watchmedo reverts the other services on its own
```

No migration to reverse, no schema change, no data to restore. Reverting removes
six log lines; nothing else changes.

---

## G. Remaining uncertainty

| # | item | classification |
|---|---|---|
| 1 | Whether the LLM is the blocking operation | **INCONCLUSIVE** — the whole point of this phase; unanswerable until deployed and a hang recurs |
| 2 | Whether the hang recurs at all | **EVIDENCE NOT AVAILABLE** — it was intermittent (10:18–12:03), and 60 s cadence has resumed |
| 3 | Whether the rate limiter or the Bedrock call dominates | the two boundaries **separate** them by subtraction, but only once data exists |
| 4 | Whether some other exit-management call blocks | possible — the boundaries bracket the LLM path, so a hang with `LLM_DYNAMIC_SL_TP_START` **and** `END` both fast would **exonerate** the LLM and redirect the search |
| 5 | What triggered onset at 10:18 and recovery at ~12:03 | **INCONCLUSIVE**, carried forward from Phase 12B |

**Point 4 matters:** this instrumentation can *falsify* the hypothesis as well as
confirm it. That is why it was worth adding before any fix.

---

## Required classification

```
BUG-2A (Hub-contention starvation)      : LIKELY RESOLVED
                                          (63 consecutive 60s cycles under full
                                           Hub load; the 329-min gap is gone.
                                           Full-session verification still due.)

NEW TRADE-LOOP HANG                     : CONFIRMED as a distinct defect
                                          (Celery soft/hard time limits, 5 SIGKILLs
                                           on 2026-08-26)

LLM as exact blocking operation         : INCONCLUSIVE

Production strategy behaviour changed   : NO
Celery configuration changed            : NO
Database schema changed                 : NO
Tests run against production            : NO
Tests run against test DB               : YES  (autotrade_test)
Production deployment                   : NOT DEPLOYED
                                          (trade worker still on pre-change code;
                                           see §D for the partial watchmedo state)
```

**The hang was not fixed in this phase. That was deliberate: definitive evidence
identifying the blocking operation comes first.**

**STOPPING HERE. Awaiting explicit approval to restart the trade worker.**
