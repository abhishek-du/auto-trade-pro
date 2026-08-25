# PHASE 11 — LIVE MARKET VERIFICATION

**Mode:** read-only. No edits, no tests, no migrations, no restarts, no fixes.
**Clock at time of report:** 2026-08-25 23:15 IST.

---

## Executive verdict

**The measurement this phase exists for cannot be taken, and I am not going to
manufacture it.** But the read-only checks that *are* possible turned up a
significant correction to two earlier phases.

| objective | outcome |
|---|---|
| 1–3 BUG-2 market-hours verification | **EVIDENCE NOT AVAILABLE** — no NSE session has elapsed since deployment |
| 4 news_id live verification | **EVIDENCE NOT AVAILABLE** — no qualifying event created yet |
| 5 emitter verification | **EVIDENCE NOT AVAILABLE** — no qualifying gate row created yet |
| 6 test isolation | **CONFIRMED** |
| 7 production safety | **CONFIRMED** |
| 8 service health | **CONFIRMED**, with one item to note |
| — | **NEW: test contamination is broader than established** — §E |

---

## A. BUG-2 market-hours measurement — EVIDENCE NOT AVAILABLE

```
clock now                      2026-08-25 23:15 IST
market closed                  2026-08-25 15:30 IST
trade-worker deployed          2026-08-25 20:01 IST   ← 4h31m AFTER the close
Phase 10 code deployed         2026-08-25 23:03 IST

trade-worker cycles logged     195
distinct dates                 2026-08-25 only
cycles inside 09:15–15:30 IST  0
first cycle                    20:01:30
last cycle                     23:15:04
```

**Every metric the brief asks for — coverage, p75/p95 interval, gaps at four
thresholds, exceptions, restarts, queue backlog, expired tasks, concurrent Hub
activity — requires cycles inside 09:15–15:30 IST. There are none.**

This is the **fifth consecutive phase** in which this measurement has been
requested. The obstacle is not technical and not a defect: the fix landed after
the close on the same day the pre-fix baseline was measured. It needs one
trading day to elapse, and the measurement is then a single query.

Per the brief's instruction, nothing is extrapolated from the 195 after-hours
cycles. They demonstrate scheduling under no load; they say nothing about the
Hub-contention scenario BUG-2 was about.

**Classification: EVIDENCE NOT AVAILABLE.**

## B. Before vs after — not comparable

| | pre-fix (2026-08-25) | post-fix |
|---|---|---|
| cycles in 09:15–15:30 IST | 11 | **not yet measurable** |
| largest gap | 329 min | — |

**Classification: EVIDENCE NOT AVAILABLE.** BUG-2 remains
**DEPLOYED / STATICALLY VERIFIED / LIVE VERIFICATION PENDING**.

## C. Cycle classification

| class | count | |
|---|---:|---|
| A — returned at the market-status check before BUG-1 | **195 (100%)** | market closed for all of them |
| B — reached `:610`, raised `UnboundLocalError` | **0** | expected only during market hours |
| C — completed past `:610` | **0** | |
| D — unknown | 0 | |

Zero class-B cycles is the *expected* result outside market hours, not evidence
that BUG-1 has stopped firing. `_india_trade_loop` returns at `:524` when the
market is closed and never reaches `:610`.

## D. Queue starvation conclusion

**INCONCLUSIVE.** Every sub-question — did the dedicated queue eliminate
starvation, did the worker hold ~1/min, did the Hub worker interfere, did task
expiry cost anything — depends on the missing session.

---

## E. ⚠ NEW FINDING — test contamination is broader than TESTCO, and it corrects two earlier phases

While checking service health I looked at the five `simulation_logs` rows written
since the trade-worker deployment. Two are `TESTCO.NS`. **Three are
`RELIANCE.NS`** — and that led somewhere.

### The pattern

`RELIANCE.NS` `EXECUTION_GATE` rows, per IST date:

| date | `BLOCKED_SHORT` | `BLOCKED_TECHNICAL_ORIGIN` | window |
|---|---:|---:|---|
| 2026-08-20 | 14 | 11 | 17:43 – 20:38 |
| 2026-08-21 | 22 | 11 | 14:13 – 20:51 |
| 2026-08-24 | 24 | 12 | 13:51 – 23:33 |
| 2026-08-25 | 13 | 5 | 17:00 – 20:35 |

They arrive in **tight 3-row bursts** — 2 × `BLOCKED_SHORT` followed by
1 × `BLOCKED_TECHNICAL_ORIGIN`, with inter-row gaps of **2–15 seconds** and
hundreds of seconds between bursts:

```
17:37:43  gap=464s   ← burst starts
17:37:47  gap=4s
17:37:55  gap=8s
17:44:38  gap=403s   ← next burst
17:44:41  gap=3s
17:44:47  gap=6s
…
20:34:55  gap=9730s
20:34:57  gap=2s
20:35:05  gap=8s
```

Those burst times on 2026-08-25 — **17:00, 17:27, 17:37, 17:44, 17:52, 20:34** —
are the times I ran `pytest tests/` during Phases 1B and 5.

### The writers

| test file | gate calls (AST) | patches `db.database.AsyncSessionLocal`? |
|---|---:|---|
| `tests/test_short_guards.py` | 8 | **no** |
| `tests/test_technical_origination_unblocked.py` | 4 | **no** |

An 8:4 ratio of gate calls, producing a 2:1 ratio of `BLOCKED_SHORT` to
`BLOCKED_TECHNICAL_ORIGIN` rows, on every date. Neither file patches a session
at source, so both reach production exactly the way
`test_integration_pipeline.py` did.

### What this corrects

**Phase 3** reported: *"Across four sessions the Hub path produced intents for
exactly one symbol — RELIANCE.NS, 38 times… `agent_loop` via API trigger is
STRONGLY SUPPORTED, not proven."*

**That attribution is now RULED OUT.** Those rows are test traffic from
`test_technical_origination_unblocked.py`. The "one symbol, four sessions,
identical confidence 72.0" pattern that puzzled me was a fixture constant.

**Phase 6** reported in its histogram: *"`BLOCKED_SHORT` is 73 events on a single
symbol — one name repeatedly proposed short and repeatedly refused."* Also test
traffic, from `test_short_guards.py`. It was not a production behaviour.

**Phase 7** concluded the contamination was `TESTCO.NS`, 144 rows, one test file.
**Understated.** At least two more test files write production rows under a
different symbol, and because they use a *real* symbol they were
indistinguishable from production traffic — which is precisely why they survived
four phases of forensics.

**Classification: CONFIRMED** — burst timing matches known pytest runs, the
call-count ratio matches the row ratio, and neither writer patches a session.

### Why it is already fixed, and why the rows remain

The Phase 10 guard is not per-test: it repoints `settings.DATABASE_URL` before
collection, so **all three files now land on `autotrade_test`** regardless of
what they patch. That was the design intent — *"requires no cooperation from
individual tests"* — and this finding is the strongest evidence for it. The full
suite run in Phase 10 confirmed it: production counts all `+0`.

**Nothing was deleted.** Per the brief, no historical row was touched. The rows
are evidence.

**One consequence worth stating:** any future analysis of `simulation_logs`
should exclude test bursts, and the `emitter` field added in Phase 10 makes that
a query rather than a forensic exercise — but only for rows written *after* the
deployment. The historical ones carry `emitter=None` and remain
indistinguishable except by the timing pattern documented here.

---

## F. news_id live verification — EVIDENCE NOT AVAILABLE

```
causal_events created after the 23:03 IST deployment : 0
newest causal_event                                  : id=11690, 2026-08-25 10:31 UTC (16:01 IST)
```

The engine creates a `CausalEvent` only when it classifies tradeable news, and it
has produced none in the ~12 minutes since deployment. The change is deployed and
statically tested (8 tests against `autotrade_test`), but **its live effect is
unobserved.** No RSS-path, NSE-path or no-NewsItem event exists to inspect.

## G. Emitter verification — EVIDENCE NOT AVAILABLE

```
simulation_logs rows after the deployment            : 0
rows carrying an emitter field at all                : 0
production rows marked emitter.pytest = true         : 0
newest simulation_logs row                           : 2026-08-25 15:05 UTC (20:35 IST)
```

That newest row **predates** the emitter deployment, so it correctly has
`emitter=None`. No production gate row has been written since. Per the brief, no
synthetic traffic was generated to force one.

The negative half is verifiable and holds: **0 production rows marked
`pytest=true`.**

---

## H. Test isolation — CONFIRMED

```
DATABASE_URL       -> host=localhost port=5432 db=autotrade_pro
TEST_DATABASE_URL  -> host=localhost port=5432 db=autotrade_test
identical on host+port+database : False
fail-closed guard active in conftest.py : True
```

Compared on identity, not raw string. `.env` untouched (mtime 2026-08-25 08:15,
predating this session). No pytest was run in this phase.

## I. Production safety — CONFIRMED

```
BUG-1 still blocks Hub candidate creation : YES  (settings read :610, local import :632)
Master Intelligence can reach candidates  : NO
Order submission reachable                : NO
A paper trade can be opened               : NO
A live trade can be opened                : NO

PAPER_MODE                    : True        (unchanged)
NEWS_ONLY_BLOCKS_HUB_ENTRIES  : False       (unchanged)
TECHNICAL_ORIGINATION_BLOCKED : False       (unchanged)
PAPER_CONFIDENCE_THRESHOLD    : 50.0        (unchanged)
MAX_NEW_ENTRIES_PER_CYCLE     : 5           (unchanged)

paper trades opened since deployment : 0
TESTCO rows                          : 144  (unchanged)
uncommitted production files         : 0
```

**No unexpected production mutation detected.**

## J. Service health — CONFIRMED, one item noted

| service | state | restarts | since |
|---|---|---:|---|
| autotrade-uvicorn | active | 0 | 08-21 13:26 |
| autotrade-celery-worker | active | 0 | 08-24 23:00 |
| autotrade-celery-beat | active | 0 | 08-25 20:01 |
| autotrade-celery-trade-worker | active | 0 | 08-25 23:09 |
| autotrade-celery-scan-worker | active | 0 | 08-25 23:09 |
| autotrade-celery-exit-worker | active | 0 | 08-24 23:00 |
| autotrade-news-engine | active | 0 | 08-24 23:33 |

Queue consumers: `default` 4 · `exit_queue` 2 · `scan_queue` 2 · `trade_queue` 2.
Two per dedicated queue is celery's MainProcess plus one pool worker at
`concurrency=1` — not duplicate workers.

Error counts in the last 300 log lines: trade-worker 0, scan-worker 0,
exit-worker 0, uvicorn 0, news-engine 1, **celery-worker 8**.

**The 8 in `celery-worker.err` are import tracebacks from `watchmedo` reload
cycles** (`<frozen importlib._bootstrap>` frames, `Python runtime state:
initialized`) — the reload race that occurs when a file changes while the worker
is starting. The worker is `active`, has never restarted at the systemd level,
and processed the full Phase 10 verification afterwards. **Noted, not
investigated** — this phase is measurement only, and it is a pre-existing
property of the watchmedo arrangement, not a consequence of the deployment.

---

## Remaining unknowns

| # | item | classification |
|---|---|---|
| 1 | BUG-2 under real market load | **EVIDENCE NOT AVAILABLE** — 5th phase |
| 2 | news_id populating live | **EVIDENCE NOT AVAILABLE** |
| 3 | emitter on a real production row | **EVIDENCE NOT AVAILABLE** |
| 4 | Whether more test files write production rows under real symbols | **INCONCLUSIVE** — two found beyond TESTCO; only files referencing RELIANCE were checked, and the guard now covers all of them regardless |
| 5 | The 8 watchmedo reload tracebacks | **OBSERVABILITY GAP** — noted, not investigated |
| 6 | NSE API returning 403 (Phase 10) | **OBSERVABILITY GAP** — external, unchanged |

---

## Recommended next

**One query, after the next NSE session** — nothing else is blocked on it:

```bash
grep -a "Starting cycle" logs/celery-trade-worker.log \
  | grep -oE "^2026-[0-9-]+ [0-9:]{8}" \
  | awk '{split($2,t,":"); m=t[1]*60+t[2]; if(m>=555 && m<=930) print}' | wc -l
```

Expected ~375. At the same time, the first `causal_event` the engine creates will
answer §F, and the first gate row will answer §G.

**Not recommended:** deleting the RELIANCE.NS or TESTCO.NS rows. They are the
evidence for §E, they sit in an audit table, and one predicate excludes them.

---

## Final safety table

| | |
|---|---|
| production files modified | **NO** |
| `.env` / runtime settings modified | **NO** |
| strategy parameters changed | **NO** |
| Master Intelligence connected | **NO** |
| BUG-1 fixed | **NO** |
| BUG-2 modified | **NO** |
| **BUG-2 verified under market load** | **NO — EVIDENCE NOT AVAILABLE** |
| orders submitted | **NO** |
| paper trades opened | **NO** |
| database INSERT / UPDATE / DELETE | **NO** — SELECT only |
| historical rows modified | **NO** |
| tests executed | **NO** |
| services restarted | **NO** |
| unexpected mutation detected | **NO** |

**PHASE 11 WAS READ-ONLY. Nothing was fixed, optimised or changed.**
