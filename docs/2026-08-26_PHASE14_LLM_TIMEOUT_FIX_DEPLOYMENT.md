# PHASE 14 — Production Fix + Live Deployment: bounding the external LLM call

**Date:** 2026-08-26 · **Deployed:** 12:59:59 IST, during the live NSE session, under explicit user authorization
**Scope authorized:** observability + safe external-LLM timeout/fail-safe ONLY
**Service changed:** `autotrade-celery-trade-worker` (the only unit that executes the changed code)

---

## A. Root cause — CONFIRMED

`utils/llm.py::call_mantle_chat(..., timeout: float = 60.0)` accepts a `timeout`
parameter and **never references it in the function body**. It appears only in
the signature and the docstring. Every caller that passed `timeout=` — including
`call_llm_chat` at `utils/llm.py:517-540` — believed it had bounded the call.
It had not.

The only real bounds were incidental:

| Bound | Location | Worst case |
|---|---|---|
| Redis RPM slot acquisition | `utils/llm.py:253-262` — `while monotonic() < deadline: await sleep(1.0)` | 90s |
| botocore socket read | `utils/llm.py:136-139` — `Config(read_timeout=90, connect_timeout=10)` | 90s |
| **per attempt** | | **≤180s** |
| retry on transient error *or empty content* | `call_mantle_chat` | **×2 = ≤360s** |

`360s > task_soft_time_limit=300` (`tasks/celery_app.py:109`) and approaches
`task_time_limit=600`. A single position's LLM call could therefore consume the
entire `india_trade_loop` task lifetime and get the worker SIGKILLed.

Confirmed in production logs: `TimeLimitExceeded` / `Hard time limit` /
`signal 9` events between **10:28:05 and 12:43:08** today.

**Not fixed here (deliberate):** `utils/llm.py` was **not modified**. Its dead
`timeout` parameter is a latent trap for every other caller, but repairing it
touches the shared LLM client used by the news engine, the decision agent and
the Hub — outside this phase's authorized scope. Recorded as an open defect.

## B. The fix — two nested deadlines, both fail-safe

Bounded at the **call sites**, not inside the shared client, so blast radius is
one task.

1. **Per-position** — `engine/agent/dynamic_management.py:142, ~242`
   `_LLM_CALL_DEADLINE_SEC = 60.0`, applied via `asyncio.wait_for` around
   `call_llm_chat`. On `asyncio.TimeoutError` the handler logs
   `BEDROCK_CALL_TIMEOUT` and **returns without touching the position** — SL and
   TP are left exactly as they were. Positions are managed concurrently
   (`asyncio.gather`, `:309`), so one slow symbol cannot stall the others.

2. **Whole-stage** — `tasks/india_tasks.py:~576-620`
   `_DYN_MGMT_DEADLINE_SEC = 120.0` around the entire `llm_dynamic_sl_tp(session)`
   call. On timeout it logs `LLM_DYNAMIC_SL_TP_TIMEOUT` and **swallows** the
   exception so the cycle continues — dynamic management is skipped for that
   cycle, positions unchanged. It does **not** re-raise, so a stalled LLM can
   never fail the trade loop.

120s is well inside the 300s soft limit with room for the rest of the cycle, and
is not a "299s" cosmetic fix — it is derived from observed latency (see F).

### What was explicitly NOT changed
`PAPER_MODE`, Celery soft/hard limits, `expires`, `trade_queue`, concurrency,
prefetch, `.env`, broker credentials, strategy parameters, thresholds,
confidence, prompts, stops, targets, sizing, allocation, entry/exit logic, risk
limits, wallet logic, drawdown breakers, order routing, database schema,
migrations, historical rows. **BUG-1 remains unfixed** — verified still firing
every cycle post-deploy (210 `UnboundLocalError` today, latest 13:06:42).
`asyncio.to_thread` semantics mean a timed-out boto3 call's underlying thread is
not killed; it is abandoned and its result discarded. That is accepted — it
frees the coroutine, which is what bounds the task.

## C. Tests — 19/19 pass

`tests/test_trade_loop_hang_instrumentation.py` (10 Phase 13 + 9 Phase 14).
`test_llm_timeout_does_not_mutate_the_position` is self-bounding
(`asyncio.wait_for(..., timeout=10)` + `pytest.fail`) so a regression that
removes the production deadline fails fast instead of hanging the suite.

**Mutation testing — 5/5 caught** (each run under `timeout 120`):

| # | Mutation | Caught |
|---|---|---|
| M1 | remove per-position `wait_for` | ✅ |
| M2 | deadline set to 299s | ✅ |
| M3 | timeout handler mutates SL/TP | ✅ |
| M4 | whole-stage timeout re-raises | ✅ |
| M5 | drop whole-stage `wait_for` | ✅ |

**Full suite:** 1,748 passed / 27 failed / 7 skipped / 5 errors, vs the Phase 13
baseline of 1,739 / 27 / 7 / 5 — **+9 tests, zero new failures.** All pytest ran
against `autotrade_test` via the Phase 9 fail-closed guard.

## D. Deployment

`systemctl --user restart autotrade-celery-trade-worker` at **12:59:59 IST**.
MainPID **2883073 → 3267754**, `NRestarts=0`, `ActiveState=active`,
`trade@CISM-I-463 ready` at 13:00:07,693.

**Honest note on prior partial exposure:** the first `BEDROCK_CALL_START` in
production is **12:43:51**, 43 seconds after the 12:43:08 hard-limit SIGKILL.
The Celery pool forked a fresh child which imported the working-tree code,
so Phase 13's *instrumentation* reached production before this deployment,
without an explicit restart. The Phase 14 *deadlines* could not have been in
that child — `dynamic_management.py` mtime is 12:54:47 and `india_tasks.py` is
12:55:03, both after the respawn. They went live only at the 12:59:59 restart.

## E. Post-deploy health — §13 checklist

| Check | Result |
|---|---|
| service active | ✅ `active` |
| new PID | ✅ 3267754 |
| no crash loop | ✅ `NRestarts=0` |
| **import errors** | ✅ **0** — `ImportError\|ModuleNotFoundError\|SyntaxError` matches **0** in both logs today |
| syntax errors | ✅ 0 |
| instrumentation loaded | ✅ firing every cycle |
| timeout configuration loaded | ✅ both deadlines present in the running code |
| Celery limits unchanged | ✅ 300 / 600 |
| queue unchanged | ✅ `trade_queue` |

The post-restart health check initially reported `import/syntax errors: 2`.
**That was a false positive in my own grep** — the pattern included `Traceback`,
and both matches (12:58:24 and 12:59:40) are pre-restart BUG-1
`UnboundLocalError: cannot access local variable 'settings'` tracebacks. Zero
import or syntax errors exist anywhere in either log today.

## F. The critical success condition — MET

> "NO trade-loop task should reach the 300s/600s Celery limits merely because
> dynamic LLM management is waiting on an external call."

| Event | Before deploy (10:28–12:43) | Since 13:00 |
|---|---|---|
| `SoftTimeLimitExceeded` | 36 | **0** |
| `TimeLimitExceeded` | 60 | **0** |
| `Hard time limit` / `signal 9` | 6 | **0** |

**Observed LLM latency, 43 completed calls across 11 symbols:**
min 1,994ms · median 3,354ms · p95 7,953ms · **max 12,697ms** — against a
60,000ms per-call deadline. Whole-stage: worst observed 22,901ms against the
120,000ms deadline. `BEDROCK_CALL_TIMEOUT` and `LLM_DYNAMIC_SL_TP_TIMEOUT` have
fired **0** times — the deadlines are headroom, not a routine path.

**Confidence: HIGH** that the deadlines are correctly wired and fail safe
(instrumentation proves the code path executes; mutations prove the tests bind).
**Confidence: MODERATE** that the hang cannot recur — 8 clean cycles is a short
window, and see H.

## G. BUG-2A — cadence, reported separately

Measured on `india_trade_loop` "Starting cycle", 681 cycles, 00:00:04–13:08:23.

| | n | median | p75 | p95 | max |
|---|---|---|---|---|---|
| **whole day so far** | 681 | 60s | 60s | 60s | **605s** |
| before deploy | 671 | 60s | — | — | 605s |
| after deploy | 8 | 60s | 60s | 60s | **60s** |

Gap buckets: 60–75s (healthy) **660** · 181–600s **17** · >600s **3**.
Restarts: 1 (mine, deliberate). The 20 degraded gaps all fall inside the
10:18–12:43 hang window.

**This is not yet the full-session measurement.** 09:15–15:30 coverage is due
after 15:30 IST today.

## H. Still INCONCLUSIVE — do not treat as closed

- **The exact blocking operation was never proven.** Phase 12B's traceback landed
  in `epoll.poll()` (event-loop stack, not the coroutine stack); py-spy needs
  sudo with `ptrace_scope=1`. The unused `timeout` parameter is a *confirmed
  sufficient* mechanism for a ≤360s stall — it is **not proven** to be what
  stalled the loop today.
- **Onset at 10:18 and recovery at ~12:03 are unexplained.** No code change,
  deploy or config change is known at either boundary.
- Because of both, this fix is correctly described as **bounding the damage**,
  not as a proven root-cause repair.

## I. Production side effects — none

Since 13:00: `paper_trades` opened **0**, closed **0**, `simulation_logs` **0**,
`agent_decisions` **0**, `causal_events` **0**. `open_positions` 9, unchanged.
No orders submitted, no paper trades opened, no rows modified or deleted, no
TESTCO/RELIANCE test evidence touched.

## J. Deferred by explicit instruction

- `skip_code` (requires a migration).
- **news_id loss on the pre-market queue path** — Phase 12A finding; §15 of the
  Phase 14 brief forbids fixing it in this phase.
- BUG-1 — must remain unfixed and blocking Hub origination.
- `utils/llm.py`'s dead `timeout` parameter (see A).

## K. Rollback — §17

Both changes are additive and confined to two files.

```bash
BE=/home/cis/windows/auto-trade-pro/autotrade-backend
cd $BE
git diff --stat engine/agent/dynamic_management.py tasks/india_tasks.py
git checkout -- engine/agent/dynamic_management.py tasks/india_tasks.py
systemctl --user restart autotrade-celery-trade-worker
systemctl --user show autotrade-celery-trade-worker -p ActiveState -p MainPID
```

Rolling back restores the *unbounded* LLM call and re-exposes the worker to the
300s/600s limits. Nothing else regresses: no schema, config, queue or strategy
state depends on these deadlines. To weaken rather than remove the fix, raise
`_LLM_CALL_DEADLINE_SEC` / `_DYN_MGMT_DEADLINE_SEC` instead of deleting the
`wait_for` wrappers — the fail-safe handlers are what protect the positions.
