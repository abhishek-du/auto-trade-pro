# PHASE 1B — DATA & EXECUTION CORRECTNESS

**Date:** 2026-08-25 · **Scope:** Phase 1B tasks A–E.
**Authorised behavioural changes made:** 2 of the 3 permitted. The third
(same-bar exhaustion) was **not** made, because the evidence did not confirm it.

No threshold, score, prompt, stop geometry, news weight, confirmation gate,
position size, cash buffer, sector limit, tactical rule, Master Intelligence
score or `PAPER_MODE` was touched.

---

## 1. NSE starvation

### Root cause — **CONFIRMED**

`news_discovery_engine.py` ran one cycle body containing, in order:

```
section 0  pre-market queue
section 1  RSS fetch  →  for each new article: await process_ticker(...)
                                                    └── full LLM ReAct loop
section 2  NSE corporate-announcement fetch          ← never reached
section 2b anomaly scan
section 3  re-entry watches
```

Section 2 was never gated on market hours and sat at the same indentation as
section 1 — structurally it should have run. It did not, because section 1
awaits an LLM ReAct loop **per article**, inline.

Evidence, from the poller's own tally line in `logs/news-engine.log`:

```
2026-08-25   last poll 09:14:50 IST → next poll 16:05:29 IST     411-minute gap
2026-08-20   last poll 09:14    IST → next poll 15:47    IST     393-minute gap
market-hours polls, every day measured:  0   (expected ~346 at the 65s cadence)
```

`agent_decisions` for 2026-08-25 run continuously from 03:45:56 to 10:30:58 UTC
— **619 decisions exactly spanning the gap**, ≈40 s each, the expected profile
of a Bedrock ReAct loop.

The consequence is not merely delay. NSE's market-wide feed is a **20-item
sliding window** (`crawler/news_crawler.py:446-450`, confirmed across 2,892
cycles in the 2026-07-22 audit). Not polling for 6.8 hours means every filing
made during the session scrolls out before it is ever seen — and filings made
during the session are the only ones tradeable intraday.

**Ruled out:** the fetch layer itself. 377 polls today, **zero** non-200
responses, **zero** exceptions, 2–7 high-impact items returned per poll out of
a constant denominator of 20.

### Architecture — before

```
while True:
    section 1 (RSS) ── await process_ticker() × N ──┐
    section 2 (NSE fetch + PDF/LLM + persist + dispatch)  ← blocked behind N LLM calls
    ...
    await asyncio.sleep(15)
```

### Architecture — after

```
run_news_discovery_loop()
├── creates asyncio.Queue(maxsize=200)
├── starts _nse_announcement_poller()  ── independent task ──┐
│      while True:                                            │
│          fetch (HTTP only)                                  │  never awaits
│          new = seq_ids not seen                             │  LLM/PDF/OCR
│          queue.put_nowait(new); mark seq_ids                │
│          sleep(_NSE_ANNOUNCEMENT_POLL_SEC)                  │
├── awaits _news_discovery_cycles()   ── the old loop body ───┘
│      section 1 (RSS) ── await process_ticker() × N
│      section 2 → new_announcements = _drain_nse_queue()
│                  ...then PDF/OCR/LLM, persist, direction, dispatch — UNCHANGED
└── finally: cancel the poller, await it, log the stop
```

**Only the fetch moved.** Enrichment, persistence, direction resolution and
dispatch remain in the main loop, in the same order, in the same code. The
reasoning: a queued item can wait, a missed poll cannot be recovered.

### Design decisions against the brief's constraints

| Requirement | How it is met |
|---|---|
| polling independent of RSS/LLM | own `asyncio.Task`, own cadence |
| an LLM job must never block the next poll | poller does HTTP + set/queue ops only; AST test forbids `process_ticker`, `process_nse_announcement`, `call_llm_chat`, `call_mantle_chat` inside it |
| preserve dedup / persistence / extraction / decisions / rate limiting | all unchanged; `_processed_seq_ids` and `ON CONFLICT DO NOTHING` both retained |
| no blind thread increase | no threads added; one task |
| no unbounded queue | `maxsize=200`; overflow is counted and logged at ERROR, never silent |
| no duplicate announcements | seq_ids marked **at enqueue**, so a slow consumer cannot cause re-enqueue; DB `ON CONFLICT` independently protects persistence |
| no concurrent processing of the same event | single consumer — the main loop, exactly as before |
| smallest change that guarantees independent scheduling | one extracted function + one queue; the 106-line processing block was dedented, not rewritten |
| cadence not hardcoded anew | reuses the existing `_NSE_ANNOUNCEMENT_POLL_SEC = 60` |
| preserve post-close behaviour | the `if market_open:` / `else: PreMarketNewsQueue` branch is untouched |
| preserve market-open filtering semantics | `is_nse_market_open()` still gates dispatch, not fetch |

**Deliberate trade-off, stated:** marking seq_ids at enqueue means an item
in-flight is lost if the process dies. That is the same exposure the previous
in-process set already carried (a restart cleared it), and it is the price of
guaranteeing a slow consumer cannot cause duplicates.

### Instrumentation

`get_nse_poll_stats()` returns: `nse_poll_started_at`, `nse_poll_completed_at`,
`nse_poll_duration`, `nse_items_seen`, `nse_items_new`, `nse_items_duplicate`,
`nse_items_enqueued`, `nse_items_dropped`, `nse_items_inserted`, `nse_errors`,
`queue_depth`, `polls_total`.

`nse_items_inserted` is incremented by the consumer after a successful commit,
so fetch-side and persist-side counts can be reconciled.

### Files changed

| file | change |
|---|---|
| `news_discovery_engine.py` | added `_NSE_QUEUE`/`_NSE_QUEUE_MAX`/`_NSE_POLL_STATS`, `get_nse_poll_stats()`, `_nse_announcement_poller()`, `_drain_nse_queue()`; split the cycle body into `_news_discovery_cycles()`; section 2 now drains instead of fetching |

### Tests — `tests/test_nse_poller_decoupling.py`, 12 tests

Deterministic: `asyncio.sleep` is replaced with a yield, the network is mocked,
no test takes wall-clock time.

| brief item | test |
|---|---|
| 1, 2 — a long LLM job does not block polling | `test_polling_continues_while_a_long_llm_job_runs` (asserts the LLM job is *still pending* — otherwise the test proves nothing) |
| 3 — no duplicates | `test_two_polls_of_the_same_feed_enqueue_each_item_once` |
| 4 — a failed LLM job does not stop polling | `test_failing_llm_job_does_not_stop_polling` |
| 5 — a failed poll does not stop processing | `test_failed_poll_is_recorded_and_polling_continues`, `test_consumer_drain_is_unaffected_by_poll_failure` |
| 6 — clean shutdown | `test_loop_shutdown_cancels_the_poller` |
| 7 — clean cancellation | `test_cancellation_is_clean` |
| bounded queue | `test_queue_is_bounded_and_overflow_is_counted_not_swallowed`, `test_the_module_level_queue_bound_is_a_real_bound`, `test_startup_creates_a_bounded_queue` |
| structural | `test_main_loop_body_does_not_fetch_announcements`, `test_poller_does_no_llm_or_pdf_work` |

### Mutation testing

| mutation | outcome |
|---|---|
| `_NSE_QUEUE_MAX = 0` (asyncio.Queue reads ≤0 as **infinite**) | **initially SURVIVED** → two tests added → now **2 fail** |
| mark seq_ids even when the queue drops the item | **1 fails** |
| let a poll failure propagate and kill the poller | **2 fail** |
| move the fetch back into the main cycle body | **1 fails** |

The first mutation is worth recording: `test_queue_is_bounded_...` substitutes
its own small queue, so it exercised the overflow *path* while saying nothing
about the shipped constant. It passed with the production queue unbounded.
That is precisely the class of vacuous test mutation testing exists to find.

### Expected polling behaviour after the change

Unchanged cadence (60 s), unchanged endpoint, unchanged filter — but the poll
now happens during market hours instead of not at all. **No claim is made that
this will increase the daily announcement count**; the purpose is that filings
made between 09:15 and 15:30 are seen at all.

---

## 2. Tactical cutoff

### Root cause — **CONFIRMED**

```
tactical entry window   09:15 – 15:20 IST   engine/tactical_data_fetcher.py:70
intraday squareoff      15:10 IST           tasks/celery_app.py:629-631 (crontab 09:40 UTC)
```

`tasks.intraday_squareoff` closes **every** `OpenPosition` with
`product == "MIS"`. After Phase 1A all tactical signals are MIS, so any entry
in the 15:10–15:20 overlap was structurally unholdable.

Measured 2026-08-25 — four entries at 15:08 IST, all closed by `MIS_SQUAREOFF`
at 15:11:

| symbol | held | P&L |
|---|---:|---:|
| RELIANCE.NS | 181 s | −₹188 |
| SHRIRAMFIN.NS | 175 s | −₹147 |
| WELCORP.NS | 175 s | −₹146 |
| INDOMIM.NS | 171 s | −₹147 |

Four full round-trips for three minutes of exposure, −₹628.

### Which cutoff is authoritative

Four candidates were traced:

| time | source | authoritative? |
|---|---|---|
| **15:10 IST** | `tasks.intraday_squareoff`, beat-scheduled, closes all MIS | **YES** — the earliest thing that force-closes a tactical position, and the one that actually fired |
| 15:15 IST | `AGENT_MIS_SQUAREOFF_TIME` (`utils/config.py:716`) | no — `agent_loop` is not in the beat schedule |
| 15:20 IST | `india_tasks.py:531,4440` entry window | no — it references Zerodha's auto-squareoff |
| 15:20 IST | Zerodha's own auto-squareoff | no — external, and later than ours |

The old comment said 15:20 "mirrors the 15:20 cutoff the India loop uses" —
15:20 is **Zerodha's** deadline, not ours.

### Change

`engine/tactical_data_fetcher.py`:
- `ENTRY_CUTOFF = (15, 20)` → `(15, 10)`
- `... <= t <= _at(t, ENTRY_CUTOFF)` → `... <= t < _at(t, ENTRY_CUTOFF)`

The bound is now **exclusive**, as the brief's cases require: at exactly
15:10:00 the squareoff is due, so 15:09:59 is the last admissible instant.

The squareoff time was **not** changed and trading was **not** extended.

### Tests — `tests/test_tactical_entry_cutoff.py`, 17 tests

Boundary to the second (09:14:59 / 09:15:00 / 15:09:59 / **15:10:00** /
15:10:01 / 15:15 / 15:19:59 / 15:20:00 / 15:30:00), the whole 15:10–15:20
overlap swept at :00/:30/:59, the four real churned timestamps, weekends, and
timezone handling asserted explicitly — 09:40 UTC converted to IST must be
rejected, 09:39:59 UTC accepted.

**Mutation testing:** reverting the constant to `(15, 20)` fails **8**;
restoring the inclusive `<=` fails **3**.

---

## 3. Same-bar exhaustion — **INCONCLUSIVE**

### What the reconstruction showed

`detect_exhaustion` (`engine/indicators.py:1203`) drops the forming bar
(`d = df.iloc[:-1]`) and reads the last **closed** bar. Replaying both BHEL
cases against the stored 5m series:

| case | entry (UTC) | exit | bar consumed | last closed bar at entry | same? | replay verdict |
|---|---|---|---|---|---|---|
| PIVOT_BREAKOUT | 09:38:25 | 09:38:30 | 09:30:00 | 09:35:00 | **no** | `True` — "RSI 85 with no new high" |
| ORB | 09:45:44 | 09:45:49 | 09:35:00 | 09:40:00 | **no** | **`False` — does not reproduce** |

### Why this is INCONCLUSIVE, not RULED OUT

The replay is not evidence about what the live check saw. The stored 5m series
is **rebuilt from 1m by `crawler/candle_resampler.py`** (commit `0d20813`),
which overwrites every column — so the bars queried today are not the bars
`fast_sl_check` held at 09:38:30, when the 09:35 bar was still forming.

Case 2 failing to reproduce is direct proof of that divergence.

What remains solid and unexplained: both positions were closed **5 seconds**
after opening — exactly one `fast_sl_check` tick — with `MFE = MAE = 0.000`,
meaning no price movement was ever recorded. Consistent with same-bar
consumption; not proof of it.

### Action taken — instrumentation only

`tasks/india_tasks.py` now emits, on every exhaustion exit:

```
[fast_sl] EXHAUSTION_AUDIT {symbol} entry_at=… entry_px=… check_at=…
          consumed_bar=… forming_bar=… bars=… atr=… reason=…
```

`consumed_bar` is `_c5["timestamp"].iloc[-2]` — the bar `detect_exhaustion`
actually reads after dropping the forming one.

**No exhaustion logic was changed.** Per the brief: evidence did not confirm
same-bar consumption, therefore strategy behaviour stays as-is and no
regression test was written for a root cause that is not yet established.

The audit block is wrapped in its own `try/except` and sits inside the existing
`except Exception as _adv_exc` guard, so a logging failure cannot abort the 5 s
tick or disable the fixed stop-loss. Verified: the guard is present and the
audit sits inside it.

---

## 4. Ticker attrition — the premise was wrong

### Finding — the "104 missing tickers" is not a pipeline loss

**Nothing dispatches from `causal_events` to the agent.** `bullish_stocks` /
`bearish_stocks` are only ever **read**, as verification:

- `engine/decision_router.py:491` — `_verify_canonical_event`, the
  NO EVENT → NO TRADE gate: confirms a ticker the agent *already chose* is
  listed in the event.
- `news_discovery_engine.py:852` — the same check in evidence validation.

They are **written** by `crawler/event_pipeline.py:61,165` and
`news_discovery_engine.py:904`.

The actual candidate path is the opposite direction:

```
headline → _extract_ticker_from_news() → ONE ticker → process_ticker() → agent_decision
                                                          └── _build_evidence() then looks
                                                              up a CausalEvent and checks
                                                              the ticker is listed in it
```

`_extract_ticker_from_news` returns `str | None` — **one ticker per headline,
by design**. An event listing five companies has never been a five-item work
queue, so the 14 "unexplained" tickers were not lost; they were never scheduled.

**My own Phase 1 framing of this is withdrawn**, along with the earlier 45%
figure it replaced.

### Instrumentation delivered

Building the requested `causal_event → … → decision` lifecycle would instrument
a pipeline that does not exist. Instead the instrumentation was applied to the
stage where candidates genuinely disappear: `_extract_ticker_from_news` had
**seven** exits, of which four returned `None` silently.

Every exit now records exactly one terminal reason via `_drop_candidate()`,
tallied in `get_candidate_drop_reasons()`:

| reason | condition |
|---|---|
| `LLM_ERROR` | the extraction call raised |
| `LLM_EMPTY` | empty response |
| `NO_LISTED_COMPANY` | model replied `NONE` |
| `MALFORMED_EXTRACTION` | repetition/garbage guard tripped |
| `EMPTY_AFTER_SUFFIX_STRIP` | nothing left after suffix stripping |
| `INSTRUMENT_LOOKUP_ERROR` | instrument search raised |
| `UNKNOWN_SYMBOL` | no NSE instrument match (fail-closed) |

Verified: 7 drop paths, 7 reasons — no silent exit remains.

### Final reason for each of the 14 — **EVIDENCE NOT AVAILABLE**

`BDL, ICICIPRULI, SHIPROCKET, CSBBANK, KALPATARU, NESTLEIND, KRONOX, ATGL,
JSWENERGY, BEL, RITES, SIGMA, MGL, GRINFRA`

These were named inside an event's stock list. No stage ever scheduled them, so
no stage can report why it did not. Assigning them a terminal reason
retrospectively would be fabrication.

Whether one headline should yield more than one candidate is a **design
question**, not a defect, and is out of scope here. Recorded for Phase 2.

**No eligibility rule was changed.**

---

## 5. Master Intelligence — not modified

Confirmed by inspection of the diff:

- `master_score` is **not** written to `AgentDecision` anywhere.
- `STRONG_BUY` untouched.
- `engine/intelligence_hub.py` **not modified** — not in the changed-file set.
- No Hub output connected to execution.

Standing finding from Phase 1 (unchanged): 0 of 3,313 agent decisions over 7
days carry a `master_score`; no production writer exists. **CONFIRMED.**

---

## 6. Full test results

| run | result |
|---|---|
| `tests/test_nse_poller_decoupling.py` | **12 passed** |
| `tests/test_tactical_entry_cutoff.py` | **17 passed** |
| `tests/test_news_engine_loop_reachability.py` | 6 passed (repointed, below) |
| `tests/test_exit_management.py` | 29 passed (de-brittled, below) |
| `-k tactical` | 120 passed |
| **full suite, after** | **1,705 passed** · 28 failed · 7 skipped · 5 errors |
| **full suite, baseline** (all Phase 1B changes stashed) | 1,676 passed · 28 failed · 7 skipped · 5 errors |

**Regressions introduced: zero.** Failure sets were diffed line-by-line against
the stashed baseline; the difference is empty once the two new suites are
excluded. Passing count rose by exactly the 29 new tests.

### Two existing tests required repair — both were brittle, neither guarantee changed

1. **`test_news_engine_loop_reachability.py` (6 tests)** — these assert on the
   AST of the cycle body and located it by the name `run_news_discovery_loop`.
   Splitting the body into `_news_discovery_cycles` moved it. Repointed via a
   single `_CYCLE_FN` constant with an explanatory failure message.
   `test_the_nse_fetch_is_not_nested_inside_the_rss_error_path` was rewritten as
   `..._is_not_reachable_from_the_rss_error_path`: its original guarantee — an
   RSS error cannot skip the NSE fetch — is now met *more strongly* by the fetch
   living in a separate task, so the assertion follows the fetch into the poller
   and additionally pins that the cycle body does **not** fetch and that the
   drain sits outside the RSS guard.

2. **`test_exit_management.py::test_advanced_block_is_fully_guarded`** — sliced
   a fixed 6,000-character window from a marker and asserted the handler was
   inside it. The audit logging added ~1.3 k characters, pushing the handler to
   offset 7,009. The guarantee still held (verified directly: guard present,
   audit *inside* the guarded region). Replaced the magic window with an indexed
   search. The window had already been "widened" once — a number that must grow
   whenever the block grows is not testing anything.
   **Mutation-checked:** renaming the handler still fails 3 tests.

### Pre-existing failures, unchanged (28 failed + 5 errors)

`test_pre_event_gap_phase3/5_5/phase6`, `test_upstox_isin` (10),
`test_alert_router` (4), `test_trade_simulator_confirmation_lost` (5 errors).
None are attributed to this work — each was present in the stashed baseline run.

---

## 7. Production changes

| file | behavioural change | classification |
|---|---|---|
| `news_discovery_engine.py` | NSE fetch runs as an independent task; cycle body drains a bounded queue instead of fetching. Enrichment/persist/dispatch unchanged. | authorised (1) |
| `engine/tactical_data_fetcher.py` | `ENTRY_CUTOFF` 15:20 → 15:10; upper bound exclusive | authorised (2) |
| `tasks/india_tasks.py` | **logging only** — `EXHAUSTION_AUDIT` line. No control flow, no condition, no threshold. | instrumentation |
| `news_discovery_engine.py` | **logging only** — `_drop_candidate()` on 7 extraction exits | instrumentation |

Test-only: `tests/test_nse_poller_decoupling.py` (new),
`tests/test_tactical_entry_cutoff.py` (new),
`tests/test_news_engine_loop_reachability.py` (repointed),
`tests/test_exit_management.py` (de-brittled).

Authorised change (3) — the exhaustion correction — was **not** made, because
the precondition (confirmation) was not met.

---

## 8. Rollback

Whole phase:

```bash
git revert <phase-1b-commit>          # no schema change, no migration, no config
systemctl --user restart autotrade-news-engine autotrade-celery-worker
```

Individually:

| change | rollback |
|---|---|
| NSE decoupling | restore `announcements = await fetch_nse_corporate_announcements()` in the cycle body, drop the poller/queue/stats block, re-merge `_news_discovery_cycles` into `run_news_discovery_loop`, repoint `_CYCLE_FN` |
| entry cutoff | `ENTRY_CUTOFF = (15, 20)` and `<` → `<=` (two edits, one file) |
| exhaustion audit | delete the `EXHAUSTION_AUDIT` block (self-contained, own try/except) |
| candidate lifecycle | delete `_drop_candidate` / `get_candidate_drop_reasons` and the 7 call sites |

`watchmedo` hot-reloads `*.py`, so a revert takes effect on the next cycle for
the news engine and Celery. The uvicorn unit does not auto-reload and is not
affected by any of these files.

---

## 9. Phase 2 readiness

> **Is the data/execution pipeline now trustworthy enough to begin the
> historical edge-discovery experiment?**

**Yes, for historical work — with two limits that must shape the methodology.**

Trustworthy:

- Tactical positions carry live stops and square off same-day (Phase 1A).
- No structurally unholdable entries remain.
- Candle data is current and the resampler is verified.
- Signals, decisions, trades and their rejection reasons are all persisted.
- Zero test regressions; the suite is a usable safety net.

Two limits Phase 2 must respect:

1. **In-session NSE announcements do not exist in the historical record before
   today.** Every session from 2026-08-17 to 2026-08-25 has zero. A news-path
   edge test over that window is testing pre-open and post-close filings only,
   and must say so rather than generalise to "news".
2. **Stored 5m/15m/1h candles are derived, not recorded.** The resampler
   overwrites them from 1m. Any replay that depends on what a bar looked like
   *while forming* is invalid — that is what made §3 inconclusive. 1m bars are
   the only safe basis.

Neither is a reason to delay. Both are reasons to scope the claims.

---

## Classification summary

| # | Finding | Classification |
|---|---|---|
| A1 | Section 1's inline LLM work starves the NSE fetch | **CONFIRMED** — fixed |
| A2 | NSE fetch layer failing / rate-limited | **RULED OUT** |
| A3 | Unbounded-queue mutation initially survived the new tests | **CONFIRMED** — two tests added |
| C1 | Entry window outlives the authoritative squareoff | **CONFIRMED** — fixed |
| C2 | 15:10 is the authoritative squareoff | **CONFIRMED** |
| D1 | Entry and exhaustion consume the same 5m bar | **INCONCLUSIVE** — instrumented, logic untouched |
| D2 | Both exits occurred one tick after entry with MFE = MAE = 0 | **CONFIRMED** |
| D3 | A stored-candle replay can settle D1 | **RULED OUT** — the resampler overwrites the series |
| E1 | `causal_events` tickers are a work queue feeding the agent | **RULED OUT** — verification input only |
| E2 | The 14 tickers were "lost" by a pipeline stage | **RULED OUT** — no such stage exists |
| E3 | Terminal reason for each of the 14 | **EVIDENCE NOT AVAILABLE** |
| F1 | Master Intelligence modified in this phase | **RULED OUT** |
| H1 | Phase 1B introduced a test regression | **RULED OUT** — diffed against a stashed baseline |

---

**STOP.** Phase 2 not started. No strategy optimised, no threshold proposed, no
Master Intelligence connected, no profitability claimed. `PAPER_MODE` unchanged.
