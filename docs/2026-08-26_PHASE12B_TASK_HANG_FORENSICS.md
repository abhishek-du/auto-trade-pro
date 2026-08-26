# PHASE 12B — LIVE TASK-HANG FORENSICS

**2026-08-26, live session.** Read-only: no edits, no restarts, no task
consumed/revoked/purged/acked, no Redis or database mutation, no `sudo`.

---

## Executive verdict

| question | answer |
|---|---|
| Are the gaps the Celery time limits? | **CONFIRMED** |
| Is the hard limit destructive? | **CONFIRMED** — it `SIGKILL`s the pool worker, 5 times today |
| Is the soft limit being swallowed by application `except` blocks? | **RULED OUT** |
| Is the defect permanent? | **RULED OUT** — it ran 10:18 → ~12:03, then 60 s cadence resumed |
| Does it correlate with the position book emptying? | **RULED OUT** — 10 positions still open when it recovered |
| **The exact blocking operation** | **INCONCLUSIVE** — and §M explains why the usual evidence cannot resolve it |

**BUG-2A (Hub-contention starvation) and this hang are kept separate. Nothing
found here merges them.**

---

## A. Call graph before line 610

`tasks/india_tasks.py::_india_trade_loop`, from :498:

| line | operation | external dependency | logs? |
|---|---|---|---|
| :535 | `await fetch_live_snapshot()` | Kite REST / yfinance (HTTPS) | yes — `live_snapshot:202` |
| :538 | `async with celery_session() as session:` | Postgres, own engine per call | no |
| :542 | `await update_positions_with_current_prices(session)` | Postgres, price cache | **only conditionally** — `:1151` on T1-partial, `:1329` on auto-close |
| :549 | `await publish(AlertEvent(...))` | Telegram HTTPS | no |
| :561 | `await llm_dynamic_sl_tp(session)` | **AWS Bedrock (HTTPS)** | **never — 0 log lines** |
| :571 | `await VirtualWallet.check_drawdown_breakers(session)` | Postgres | on failure only |
| :574, :581 | `await RuntimeConfig.load(session)` ×2 | Postgres | no |
| :610 | `getattr(settings, …)` | — | **BUG-1 raises here** |

Guarded by `try/except Exception` at :559-562 (`llm_dynamic_sl_tp`) and
:570-574 (breakers). Neither is annotated with a timeout.

## B/C. Representative traces — the difference is decisive

**Fast cycle, 12:09:05, ~1 s:**

```
12:09:05  india_trade_loop:1373      Starting cycle
12:09:05  _india_trade_loop:524      NSE market status: OPEN
12:09:05  live_snapshot:202
          (ends — next cycle 12:10:05)
```

**Slow cycle, 11:43:16, killed at 300 s:**

```
11:43:16  india_trade_loop:1373      Starting cycle
11:43:16  _india_trade_loop:524      NSE market status: OPEN
11:43:16  live_snapshot:202
11:43:19  utils.llm:_acquire_llm_rate_slot:262     ← LLM rate slot taken
11:43:20  trade_simulator:1151       [T1 partial]  ← partial booked
          ── silence ──
11:48:16  next cycle (300 s later)
```

## D. Common operation preceding the timeouts

**Slow cycles do two things fast cycles never do:** they acquire an **LLM rate
slot** and they log a **T1-partial booking**. The hang always begins *after* the
last of those.

That narrows the window to: everything after the T1-partial branch inside
`update_positions_with_current_prices` (:542) through `llm_dynamic_sl_tp` (:561)
and the two `RuntimeConfig.load` calls — i.e. **the exit-management block**, all
of it before BUG-1's line 610. It does not narrow it to a single call.

## E. Auto-close analysis

Phase 12A noted 600 s gaps following `1 position(s) auto-closed`. Across the
whole session auto-close fired at **10:44, 11:15, 11:28, 11:56, 12:04** — five
events against **16 slow cycles**. Most slow cycles have no auto-close at all.

**Auto-close is not a necessary condition for the hang.** It appears in the
longest ones, which is consistent with it adding work, but it does not explain
the 300 s cases.

## F. T1-partial analysis

`trade_simulator.py:1151` logs after a partial booking; the surrounding code sets
`pos.trade.stop_loss`, flags `trailed`, then evaluates the trailing-stop ratchet.
The log line is the **last thing emitted** before most 300 s hangs.

It is a *marker*, not necessarily the blocking call — the function continues past
it with no further logging, so the hang could be anywhere from there to the end
of exit management. **INCONCLUSIVE** as to whether the T1 branch itself blocks.

## G. LLM analysis

`llm_dynamic_sl_tp` at :561 calls into AWS Bedrock. Two facts matter:

1. **It emits no log line at all** — `dynamic_management`, `llm_dynamic_sl_tp`,
   `Dynamic SL` all return **0** matches in the worker log. If the hang is here,
   the logs are structurally incapable of showing it.
2. The hung worker holds **HTTPS sockets to AWS ranges**
   (`3.224.204.236:443`, `35.172.103.40:443`, `34.193.162.167:443` — all
   AWS us-east-1), alongside Postgres and Redis.

`utils.llm:_acquire_llm_rate_slot:262` logs *"Redis coordination unavailable,
proceeding without"* — the message says it **proceeds**, i.e. an immediate
fallback (option A), not a blocking wait. Treated as a clue, it points at an LLM
call being *attempted*; it does not establish a block.

**The socket evidence is not proof.** It was sampled at 12:11 while the worker
was between cycles (`wchan: anon_pipe_read` — waiting for the next task), not
mid-hang.

## H. Redis analysis — broker healthy, coordination present

```
broker PING            : PONG
connected_clients      : 81
blocked_clients        : 1
LLM rate-limiter keys  : llm:rpm:29795439, llm:rpm:29795440  ← present
```

**Broker availability and application coordination are separate, and both are
healthy.** The rate-limiter keys exist, so the "Redis coordination unavailable"
message is **not** a global Redis outage — it is a transient condition inside
`_acquire_llm_rate_slot`. **Redis is RULED OUT as a global cause.**

## I. Database analysis

`celery_session()` builds a **new engine per call** (`tasks/_db.py:32-39`) with
`NullPool` and disposes it in `finally`. There is no shared pool to exhaust.

Evidence of session damage **does** exist today:

```
PendingRollbackError : 3
StaleDataError       : 2
```

`PendingRollbackError: This Session's transaction has been rolled back due to a
previous exception during flush` — consistent with work continuing on a session
after a failure. Whether these are a *cause* of the hang or a *consequence* of
`SIGKILL`ed cycles is **INCONCLUSIVE**; the ordering was not established.

## J. Celery analysis

```
concurrency=1 · prefetch_multiplier=1 · dedicated trade_queue
queue depth 10 · unacked 4
SoftTimeLimitExceeded 33 · TimeLimitExceeded 53 · SIGKILL 5
```

Task-ID tracing proves one task runs continuously through each gap:

```
11:23:11  task 35b0e6c7 starts
11:28:11  35b0e6c7 → SoftTimeLimitExceeded   (exactly 300 s)
11:28:11  task afef4bc5 starts
11:28:56  logs update_positions (+45 s)
11:38:11  afef4bc5 → Hard time limit (600s) exceeded
11:38:16  Process 'ForkPoolWorker-4' pid 3223545 exited with 'signal 9 (SIGKILL)'
11:38:16  next task starts
```

Nothing was revoked, acked, requeued or purged.

## K. Timeout cleanup semantics — CONFIRMED, and the hard limit is unsafe

**Soft limit (300 s).** Raises `SoftTimeLimitExceeded` — which inherits from
`Exception`, so `except Exception` *would* catch it. The code has two such
handlers on this path. **They are not catching it:** `Dynamic management failed`
and `breaker check failed` each appear **0** times today. The exception
propagates out of the task, and `async with celery_session()` unwinds normally,
running its `finally: await engine.dispose()`.

**Hard limit (600 s).** `Process 'ForkPoolWorker-N' exited with 'signal 9
(SIGKILL)'` — **5 times today.** SIGKILL is uncatchable:

- no `finally` runs → `celery_session()`'s `engine.dispose()` is skipped
- the `async with` block never exits
- the DB connection is severed rather than closed

Postgres rolls back an open transaction when its connection drops, so a
transaction does not stay open indefinitely. But **anything committed before the
kill persists while everything after it is lost**, and with `task_acks_late=True`
an unacked task can be redelivered — meaning a subsequent cycle can operate on
partially-updated state.

## L. Can the timeouts create state inconsistency? — YES, and there is evidence

| risk | assessment |
|---|---|
| DB transaction left open indefinitely | **RULED OUT** — Postgres rolls back on connection loss |
| `engine.dispose()` skipped | **CONFIRMED** — SIGKILL bypasses `finally` |
| partially-updated position / trade state | **STRONGLY SUPPORTED** — auto-close commits mid-cycle; a kill after that loses the rest |
| duplicate work on redelivery | **STRONGLY SUPPORTED** — `task_acks_late=True` with an unacked killed task |
| session-state corruption | **CONFIRMED present** — 3 × `PendingRollbackError`, 2 × `StaleDataError` today |
| stale Redis locks | **EVIDENCE NOT AVAILABLE** — no application lock keys were identified |

**This is the finding that matters most operationally.** The gaps are a
scheduling symptom; the `SIGKILL` is a correctness hazard, and it fired five
times during a live session with real positions open.

## M. Root cause — CONFIRMED at one level, INCONCLUSIVE at the next

**CONFIRMED:** the gaps are `tasks.india_trade_loop` running to
`task_soft_time_limit=300` / `task_time_limit=600`
(`tasks/celery_app.py:109-110`), with matching exceptions logged at every gap
boundary.

**INCONCLUSIVE:** which operation blocks. Three independent evidence sources are
each structurally unable to resolve it:

1. **The traceback cannot name it.** The complete soft-limit traceback ends:

   ```
   run_until_complete → run_forever → _run_once
     → self._selector.select(timeout) → epoll.poll(timeout, max_ev)
     → billiard/pool.py:228  raise SoftTimeLimitExceeded()
   ```

   The signal fires while the **event loop** waits on I/O, so the traceback is
   the loop's stack, not the awaiting coroutine's. It proves the task was blocked
   on I/O; it can never say on *which* awaitable.

2. **The suspect logs nothing.** `llm_dynamic_sl_tp` — the only LLM call on the
   path — emits **zero** log lines, so its entry and exit are invisible.

3. **Live stack capture is blocked.** `py-spy` is present at
   `.venv/bin/py-spy`, but `/proc/sys/kernel/yama/ptrace_scope = 1` requires
   `sudo`. **I did not escalate** — attaching a profiler to a live trading worker
   during market hours is beyond read-only forensics, and the brief forbids it.

**What is STRONGLY SUPPORTED:** the block is an I/O wait inside exit management,
on the path that includes the T1-partial branch and an LLM call, most plausibly
the Bedrock call at :561 — the only unlogged, untimed network operation in the
window, and the worker holds AWS HTTPS sockets. **Stated as a hypothesis, not a
finding.**

## Intermittency — the constraint any explanation must satisfy

```
09:15 – 10:18   63 consecutive 60 s cycles
10:18 – 12:03   16 cycles at 300/600 s
12:07 – 12:13   60 s cadence resumed  (12:07:05, 12:08:05, … 12:13:05)
```

At recovery there were still **10 open positions**, so "the book emptied" is
**RULED OUT**. Three `SIGKILL`s occurred inside the hang window (11:23, 11:38,
12:03) and the first two did **not** end it, so "a fresh pool process fixes it"
is **not supported** either.

**What changed at 10:18 and again at ~12:03 is unexplained.** Last trade opened
10:16:01, two minutes before onset — noted as a temporal coincidence, **not**
offered as a cause.

## N. Confidence summary

| finding | classification |
|---|---|
| Gaps = Celery soft/hard time limits | **CONFIRMED** |
| Hard limit SIGKILLs the pool worker | **CONFIRMED** |
| Soft limit is not swallowed by application handlers | **RULED OUT** |
| Redis outage as cause | **RULED OUT** |
| `expires=55` as cause (Phase 12A) | **RULED OUT** |
| BUG-1 as cause | **RULED OUT** |
| Book emptying as the recovery trigger | **RULED OUT** |
| Hang is an I/O wait inside exit management | **STRONGLY SUPPORTED** |
| Hang is the Bedrock call at :561 | **INCONCLUSIVE** (hypothesis) |
| SIGKILL can leave partial state | **STRONGLY SUPPORTED** |
| Session-state damage occurred today | **CONFIRMED** (3 + 2 errors) |
| Why it started 10:18 / ended 12:03 | **INCONCLUSIVE** |
| Same defect as BUG-2A | **RULED OUT** — different mechanism |

## O. Safest fix — DESIGN ONLY, NOT IMPLEMENTED

Ordered by safety, **none applied**:

1. **Log entry/exit around `llm_dynamic_sl_tp`.** Two log lines. Resolves §M
   without changing behaviour, and is the only proposal that turns an
   INCONCLUSIVE into a decidable question. **Do this first.**
2. **Give the Bedrock call an explicit timeout.** It is currently untimed; a
   bounded client timeout would convert a 600 s SIGKILL into a caught exception
   that the existing `except Exception` at :562 already handles. Changes
   behaviour — needs approval.
3. **Make the loop's own work bounded** so it cannot reach 300 s. Larger change,
   deferred.
4. **Do not raise the time limits.** That would lengthen the hang and delay the
   SIGKILL without addressing either.

**Explicitly not recommended:** disabling exit management or the LLM path,
fixing BUG-1, or touching Celery configuration.

---

## Safety

| | |
|---|---|
| files edited · services restarted · tests run | **NO** |
| tasks revoked / acked / requeued / purged | **NO** |
| Redis or database mutation | **NO** — `PING`/`INFO`/`--scan`, `SELECT`, `/proc` reads |
| `sudo` used · py-spy attached | **NO** |
| BUG-1 fixed · Master Intelligence connected | **NO** |
| strategy parameters changed · orders · paper trades | **NO** |

**PHASE 12B WAS READ-ONLY FORENSICS. Nothing was fixed.**
