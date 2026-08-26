# PHASE 12A — LIVE 5-MINUTE GAP FORENSICS

**2026-08-26, market open.** Measured 09:15:00 → 12:00 IST. Read-only: no edits, no
restarts, no task consumed/revoked/purged, no database mutation.

---

## Executive verdict

**The cause is proven, and it is not what the interim reading suggested.**

> The gaps are the trade loop **running to its Celery time limit**.
> `tasks/celery_app.py:109-110` sets `task_soft_time_limit=300` and
> `task_time_limit=600`. Every gap is exactly 300 s or 600 s.
> `SoftTimeLimitExceeded` appears in the worker log at the gap boundaries.

**This corrects my own interim statement.** I reported *"worker is visibly idle
during the gaps."* **That was wrong.** The worker is not idle — it is occupied by
a task that produces no log output while it hangs, then is killed by the time
limit. This is option **G** from the brief's list: *worker is actually
blocked/occupied despite appearing idle.*

| | |
|---|---|
| BUG-2A — Hub-contention starvation | **LIKELY RESOLVED** |
| Cadence defect (the 5/10-min gaps) | **CONFIRMED, and it is a different mechanism** |
| Overall BUG-2 | **NOT YET CONFIRMED FIXED** — full session ends 15:30 |

---

## A. The transition — 10:18:05 IST, and it is a hard switch

| period | cycles | median gap | max gap | gaps that are exact 5-min multiples |
|---|---:|---:|---:|---|
| 09:15:05 – 10:18:05 | 64 | **60 s** | 61 s | **0 / 63** |
| 10:18:05 – 11:53:17 | 16 | **300 s** | 605 s | **15 / 15** |

Not a degradation — a switch. Every gap after the transition:

```
10:18:05 -> 10:28:05   600s  mod300=0        11:08:07 -> 11:13:07   300s  mod300=0
10:28:05 -> 10:33:06   301s  mod300=1        11:13:07 -> 11:23:11   604s  mod300=4
10:33:06 -> 10:38:06   300s  mod300=0        11:23:11 -> 11:28:11   300s  mod300=0
10:38:06 -> 10:43:06   300s  mod300=0        11:28:11 -> 11:38:16   605s  mod300=5
10:43:06 -> 10:53:06   600s  mod300=0        11:38:16 -> 11:43:16   300s  mod300=0
10:53:06 -> 10:58:06   300s  mod300=0        11:43:16 -> 11:48:16   300s  mod300=0
10:58:06 -> 11:03:06   300s  mod300=0        11:48:16 -> 11:53:17   301s  mod300=1
11:03:06 -> 11:08:07   301s  mod300=1
```

The 1–5 s residuals are the task's own start latency, which drifts upward across
the session (:05 → :06 → :07 → :11 → :16) — the signature of a timer started at
task start, not of a fixed wall-clock schedule.

## B. Cycle statistics (09:15 – 12:00 IST, partial session)

```
cycles                79          elapsed 165 min        coverage ~48%
median interval       60 s        p75 60 s   p95 600 s   max 605 s
gaps >90s  14         >2min 14    >5min 6    >15min 0
worker restarts       0           queue depth 10, unacked 4
```

Pre-fix baseline (2026-08-25): **11 cycles for the whole session, largest gap 329 min.**

## C. Producer trace

```
celery_app.py:365  "india-trade-loop-every-60s"
                     task     tasks.india_trade_loop
                     schedule 60
                     options  {countdown: 15, queue: trade_queue, expires: 55}
       ↓
celery_app.py:99   route  tasks.india_trade_loop -> trade_queue
       ↓
redis broker       trade_queue
       ↓
celery-trade-worker  --queues=trade_queue --concurrency=1 --prefetch-multiplier=1
       ↓
india_tasks.py:1366  @celery_app.task(name="tasks.india_trade_loop")
india_tasks.py:1367  def india_trade_loop():  -> _run_async(_india_trade_loop())
```

**Beat is the only producer** — no `.delay()`, `.apply_async()` or `send_task`
for this task anywhere outside the beat schedule. The task does **not** schedule
its own successor. Beat is alive: the default and scan workers logged 121k and
250k lines respectively in the 10:00 hour alone.

## D. Exact cause — CONFIRMED

```python
# tasks/celery_app.py:109-110
task_soft_time_limit=300,
task_time_limit=600,
```

Log evidence at a gap boundary:

```
[2026-08-26 11:53:16,976: ERROR/ForkPoolWorker-5] Task tasks.india_trade_loop[f6037b14…]
    raised unexpected: SoftTimeLimitExceeded()
billiard.exceptions.SoftTimeLimitExceeded: SoftTimeLimitExceeded()
```

Counts today: **SoftTimeLimitExceeded 33 · TimeLimitExceeded 49.**

The last log line before each gap, and how far into the cycle it appeared:

| gap | last activity | where |
|---:|---|---|
| 600 s | +20 s | `_india_trade_loop:544 — 1 position(s) auto-closed` |
| 301 s | +44 s | `trade_simulator:update_positions_with_current_prices:1151 — [T1 partial]` |
| 300 s | +3 s | same |
| 600 s | +63 s | `:544 — 1 position(s) auto-closed` |
| 604 s | +144 s | `:544 — 1 position(s) auto-closed` |
| 605 s | +45 s | `:544 — 1 position(s) auto-closed` |
| 300 s | +4 s | `[T1 partial]` |

**The task logs for 3–144 seconds, then goes silent until the limit kills it.**
Every 600 s gap follows `auto-closed`; most 300 s gaps follow `[T1 partial]` in
`update_positions_with_current_prices`.

**Where it hangs is not proven.** The last line locates the hang *after* those
points, not *at* them. Candidate: the exit-management block runs before BUG-1's
line 610 and includes dynamic SL/TP, which makes LLM calls — and
`utils.llm:_acquire_llm_rate_slot:262 — Redis coordination unavailable,
proceeding without` appears in the same window. **INCONCLUSIVE** as to the exact
blocking call; establishing it needs instrumentation this phase may not add.

## E. Role of `expires=55` — RULED OUT as the cause

`expires` is measured from publish. Beat publishes at T with `countdown=15`
(eligible T+15) and `expires=55` (dead at T+55) — a 40-second window.

It cannot produce this pattern:

- an expiry-driven pattern would give **irregular** gaps, not 15/15 exact 300 s
  and 600 s multiples
- expired tasks are discarded silently, so they would leave **no**
  `SoftTimeLimitExceeded` — and 33 of those are logged
- the gaps match the **time-limit** constants exactly, not the expiry constant

`expires=55` is, however, doing its job downstream: while one task occupies the
worker for 300–600 s, five to ten beat ticks publish and expire rather than
accumulating. Queue depth is **10, unacked 4** — bounded, not a 63k-style
backlog. Without the expiry those would have piled up.

## F. Hub contention — RULED OUT for these gaps

The default worker logged 121,263 lines and the scan worker 249,912 in the 10:00
hour, so both were heavily loaded during the gap period. But the trade worker was
**executing its own task**, not waiting for a slot — it is on a dedicated queue
with its own process, and it produced `SoftTimeLimitExceeded` for tasks it had
already started.

This is evidence **against** the original contention mechanism being responsible
here, and evidence **for** the Phase 5 fix having worked: 63 consecutive 60-second
cycles from 09:15 to 10:18, under exactly the Hub load that used to produce a
329-minute gap.

**Correlation is not causation, and none is claimed:** the Hub was busy in both
periods, and only one of them has gaps.

## G. BUG-1 — implicated, but in the opposite direction

```
BUG-1 (UnboundLocalError at :610) today   : 62
fast cycles (09:15–10:18)                 : 63
```

Near-exact correspondence. **BUG-1 is what makes the fast cycles fast** — it
terminates the task at line 610, immediately after exit management, so the cycle
ends in ~1 second and the next beat tick runs on time.

The slow cycles are the ones that **never reach** line 610: they hang in exit
management before it and run to the time limit. BUG-1 occurrences are recorded in
the 09: and 10: hours and stop thereafter, matching the transition.

**BUG-1 is not causing the 5-minute pattern.** Its absence from a cycle is a
*symptom* of the same hang, not its cause. Per the brief, BUG-1 remains unfixed.

## H. Queue state

```
trade_queue depth : 10
unacked index     : 4
```

Inspected read-only via `LLEN`/`ZCARD`. Nothing was consumed, acknowledged,
revoked, requeued or purged.

**Whether the specific queued tasks are expired, future-ETA or reserved was not
determined** — establishing it would require `celery inspect`, which contacts
workers, or reading task bodies. **EVIDENCE NOT AVAILABLE**, deliberately.

My earlier interim note — *"trade_queue has 1 task while the worker is idle"* —
was a correct reading of the depth but a **wrong inference about the worker**. It
was not idle (§D).

## I. news_id live verification — deployed, and a gap in my own implementation

```
causal_events created since deployment : 214
  ...with a non-NULL news_id           : 0
```

**Why**, from the news-engine log:

```
🌅 Market is OPEN! Processing 2611 queued night/pre-market database alerts...
```

Today's live dispatches were **9 RSS + 2 NSE announcements**. Essentially all 214
events came from the **pre-market queue flush** at market open —
`news_discovery_engine.py:1623`, which calls
`process_ticker(item.symbol, item.side, item.headline, item.summary)` **without
news_id**.

That is one of the two call sites I deliberately left as `None` in Phase 10, and
I justified it as *"genuinely creates events with no news row."*
**That justification was wrong for this path.** Those items *did* have a
`NewsItem` — inserted by the RSS block before being queued overnight — but
`PreMarketNewsQueue` stores symbol/side/headline/summary and **not** the news id,
so the link is lost across the overnight boundary.

The implementation is correct for the two paths it covers. On a normal day the
**dominant** path is the pre-market queue, so in practice news_id is still NULL.

**Classification: the mechanism is deployed; the coverage is incomplete —
CONFIRMED GAP.** Whether the 11 live dispatches produced events with a populated
news_id could not be isolated from the 214. **INCONCLUSIVE.**

**Not fixed here.** This phase is read-only.

## J. Emitter live verification — CONFIRMED WORKING

```
simulation_logs rows since deployment : 244
  ...carrying an emitter field        : 204
  ...marked pytest = true             : 0
```

```
RUBICON.NS      {'pytest': False, 'process': 'news_discovery_engine.py', 'pid': 2878957}
BRAHMINFRA.BO   {'pytest': False, 'process': 'news_discovery_engine.py', 'pid': 2878957}
CDSL.NS         {'pytest': False, 'process': '__main__.py',              'pid': 3183356}
```

Both halves hold: production rows are identifiable by process, and **zero**
production rows are marked as test traffic. The 40 rows without an emitter
predate the deployment.

`paper_trades` since deployment: 12 (normal trading). TESTCO rows: **144,
unchanged**.

## K. Classification

| item | classification |
|---|---|
| BUG-2A — Hub-contention starvation | **LIKELY RESOLVED** — 63 consecutive 60 s cycles under full Hub load; the 329-minute gap is gone |
| Cadence defect — the 5/10-min gaps | **CONFIRMED** — the loop runs to `task_soft_time_limit=300` / `task_time_limit=600` |
| Exact blocking call inside the loop | **INCONCLUSIVE** |
| `expires=55` as the cause | **RULED OUT** |
| Hub contention as the cause of these gaps | **RULED OUT** |
| BUG-1 as the cause | **RULED OUT** — it makes cycles fast, not slow |
| Worker idle during gaps | **RULED OUT** — corrects my interim claim |
| Overall BUG-2 | **NOT CONFIRMED FIXED** — full session required |
| news_id live | **CONFIRMED GAP** — pre-market queue path loses the link |
| emitter live | **CONFIRMED WORKING** |

## L. To measure again after 15:30

1. **Full-session BUG-2 numbers** — cycles in 09:15–15:30, coverage, p95, max gap,
   class A/B/C/D against the 11-cycle / 329-minute baseline.
2. **Whether the cadence defect persists all session** or the loop recovers when
   the position book empties — the transition at 10:18 coincided with
   `1 position(s) auto-closed`, which is suggestive but **not established**.
3. **Total `SoftTimeLimitExceeded` / `TimeLimitExceeded`** for the session.
4. **Whether any `causal_event` from a live RSS/NSE dispatch carries news_id** —
   needs the live paths to fire while the market is open.

---

## Safety

| | |
|---|---|
| files edited | **NO** |
| services restarted | **NO** |
| tasks consumed / revoked / purged / acked | **NO** |
| database INSERT / UPDATE / DELETE | **NO** — SELECT and Redis `LLEN`/`ZCARD` only |
| BUG-1 fixed | **NO** |
| Master Intelligence connected | **NO** |
| strategy parameters changed | **NO** |
| orders / paper trades caused by this phase | **NO** |

**PHASE 12A WAS READ-ONLY FORENSICS. Nothing was fixed.**
