# PHASE 19 — PRODUCTION DEFECT CLOSURE

**Date:** 2026-08-26, 16:50–17:20 IST (NSE closed 15:30).
**Continues:** Phase 18. Phase-14 timeout protection and the Phase-18 candle
delta fix were left intact and re-verified.

---

## 1. FIXED + DEPLOYED TODAY

### 1.1 Pre-market queue replayed twelve-day-old news · **CONFIRMED → FIXED**

**Fact.** `premarket_news_queue` held 2,451 PENDING rows, oldest captured
**2026-08-14** — twelve days. The engine log announced
`Processing 2611 queued` on **24 separate occasions**: it re-read the same
backlog from the start every cycle. 65,270 queue items were drained against
only **570 `process_ticker` invocations**, so the loop never approached the end.
Each item costs a full LLM ReAct loop.

**Root cause.** The drain filtered on `status == 'PENDING'` only. Nothing
bounded how old a row could be, despite the model's own docstring scoping the
table to "news captured outside of trading hours for processing at market open".

**Fix.** `news_discovery_engine.py` — `_PREMARKET_MAX_AGE_DAYS = 3` and a
`captured_at >= now() - 3 days` predicate on the drain. Three days covers a
Friday-evening filing drained on Monday morning, including a long weekend.
**No row is mutated, deleted or re-labelled** — stale rows simply stay PENDING,
so a revert of this file fully restores previous behaviour.

**Measured effect on the live queue:** 2,451 PENDING → **369 drained, 2,082
skipped as stale**.

**Tests.** `tests/test_premarket_queue_age_cutoff.py` — 7 tests: constant bounds,
the Friday→Monday boundary, `captured_at` filtering, named-constant use, PENDING
still filtered, no delete/expire (AST-checked), per-item commit.

**Deployed** 16:58 IST, `autotrade-news-engine`. **Rollback:**
`git checkout -- autotrade-backend/news_discovery_engine.py && systemctl --user restart autotrade-news-engine`

Related and already visible: the per-item PROCESSED commit deployed earlier
today moved the backlog off its stuck 2,611 for the first time — PROCESSED
4,731 → **4,925**.

### 1.2 NSE poller/queue/consumer telemetry · **DEPLOYED (instrumentation only)**

**Fact.** The stdlib logger used `logging.basicConfig(level=INFO)`, whose
default format carries **no timestamp**. Its output lands in
`logs/news-engine.err`, which appends since **2026-08-19**. Seven days of lines
were mutually indistinguishable — this is what blocked P0-A attribution, and it
caused two errors in my own Phase 18 analysis (§6).

**Fix.** Timestamped format; per-poll telemetry on **every** poll (not only when
new items arrive): `seen / new / dup / enqueued / dropped / depth / polls /
errors`; and a `[premarket_drain] completed=N/M elapsed_s=… max_age_days=…`
line so a drain that announces 2,611 and completes 3 is distinguishable from
one that finishes. Monotonic timing. Counts only — no headlines, payloads or
credentials.

**Deployed** 17:02 IST. **Verified live within six minutes:**

```
2026-08-26 17:09:19 | INFO | news_engine | [nse_poller] poll seen=1 new=0 dup=1 enqueued=1 dropped=0 depth=0/200 polls=3 errors=0
2026-08-26 17:10:21 | INFO | news_engine | [nse_poller] poll seen=1 new=0 dup=1 enqueued=1 dropped=0 depth=0/200 polls=4 errors=0
2026-08-26 17:11:23 | INFO | news_engine | [nse_poller] poll seen=1 new=0 dup=1 enqueued=1 dropped=0 depth=0/200 polls=5 errors=0
```

**No speculative NSE fix was deployed**, as instructed. The instrumentation
immediately closed P0-A on its own — see §3.1.

### 1.3 Misleading capital rejection message · **CONFIRMED → FIXED (message only)**

**Fact.** 1,024 of 1,931 capital rejections read
`Cash buffer: deploying ₹0 would breach the 10% cash reserve`. Deploying ₹0
breaches nothing; the sentence blamed the candidate for a book already over the
line, and it misdirected this investigation earlier today.

**Root cause of the ₹0 — investigated per P1-A.** `int(raw_units)` flooring at
`risk_manager.py:584` was **RULED OUT**: at the observed prices and stop
distances, units land in 62–10,040, never near zero. The real path is the
position-weight cap at `:592-594` —
`max_notional = free_cash × AGENT_MAX_POSITION_WEIGHT`, then
`int(max_notional // entry_price)`. With the book ~99.6% deployed, free cash was
**₹1,945**, so 5% of it is **₹97** against a median entry price of **₹1,186** —
zero whole shares.

Modelling that arithmetic against the production rejection strings reproduced
the zero flag on **1,027 / 1,027 (100%)** of zero-notional cases.

**Verdict: EXPECTED BEHAVIOUR, not a sizing bug.** Per the brief, the gate was
left unchanged and only the classification corrected: a distinct
`elif this_notional <= 0:` branch that states free cash, the per-position
budget, and the share price. The diff is **purely additive** — no existing line
removed, gate condition at `:280` untouched.

**Tests.** 57 risk/sizing/capital/reallocation tests pass.
**Deployed** 17:14 IST to the four Celery workers. **Rollback:**
`git checkout -- autotrade-backend/engine/risk_manager.py && systemctl --user restart autotrade-celery-worker autotrade-celery-scan-worker autotrade-celery-trade-worker autotrade-celery-exit-worker`

---

## 2. VERIFIED

| Item | Evidence |
|---|---|
| Phase-14 timeout protection intact | `test_trade_loop_hang_instrumentation.py` **19/19 pass**; deadlines and `utils/llm.py` untouched |
| Phase-18 candle delta fix intact | `test_live_1m_delta_window.py` **9/9 pass**; file unmodified since deploy |
| Full suite | **1,764 passed · 27 failed · 7 skipped · 5 errors** — vs Phase-18's 1,757/27/7/5. **+7, zero new failures** |
| All services healthy | 7/7 active, `NRestarts=0`, **0** import/syntax/name errors |
| Telemetry live | timestamped poller lines every 60s, `polls` counter monotonic |
| Engine restart loop | **RULED OUT** — 13 restarts clustered at 17:02/17:05 were `watchmedo` reacting to my own edits; 150s with no edits gave **delta 0** |

**Service state at 17:20 IST**

| Service | PID | Restarts |
|---|---|---|
| autotrade-celery-worker | 3397710 | 0 |
| autotrade-celery-scan-worker | 3397665 | 0 |
| autotrade-celery-trade-worker | 3397687 | 0 |
| autotrade-celery-exit-worker | 3397690 | 0 |
| autotrade-news-engine | 3391682 | 0 |
| autotrade-uvicorn | 3312032 | 0 |
| autotrade-celery-beat | 3300942 | 0 |

---

## 3. INVESTIGATED — NO FIX REQUIRED

### 3.1 P0-A: NSE announcement capture · **RULED OUT as a defect**

Five hypotheses tested and rejected before the answer appeared:

| Hypothesis | Verdict | Evidence |
|---|---|---|
| 20-item window loses filings | **RULED OUT** | Replayed a 62s poller over today's **324 real filings**: 42 eligible intraday filings, **41 should have been captured**. The window is not the constraint. |
| Market-wide ≠ date-ranged endpoint | **RULED OUT** | Identical. Market-wide is exactly the date-ranged newest-20, newest-first, **20/20 seq_id overlap**. |
| Burst overflow | **RULED OUT** | Max **5** filings in any 62s window. The newest-20 covers p50 **1,395s**, min **645s** — **0/304** times shorter than the poll interval. |
| `seq_id` missing → silent drop at `:97` | **RULED OUT** | Present and truthy, **6/6**. |
| `limit` truncation | **RULED OUT** | `limit=50`, payload 20. |

**The actual answer, surfaced by §1.2's telemetry within six minutes of deploy:**

```
17:08:13 | news_engine | 📋 Found 1 new high-impact NSE corporate announcements.
17:08:16 | news_engine | ⏭️  NSE category 'Press Release' carries no direction — not a trade candidate: MOLDTKPAC.NS
```

The pipeline works end to end. Announcements are **deliberately suppressed** at
`news_discovery_engine.py:1975-1981` via
`engine/event_classifier.py::resolve_nse_direction`, which returns `NEUTRAL` for
`Outcome of Board Meeting`, `Press Release`, `Scheme of Arrangement`,
`Credit Rating- Others`.

That suppression is **evidence-backed**:
`docs/2026-08-24_PHASE3_GROUND_TRUTH_NEWS_ALPHA.md` records
`ROUTINE_BOARD_MEETING` at **n=1,169, mean −1.029%, 36.3% win rate**. The code
comment is explicit: *"Acting on them lost money; the fix is to not act."*

**This corrects my own earlier framing.** "22 eligible intraday filings, 0
captured" was true but misleading: they were not lost, they were **deliberately
declined**. **EXPECTED BEHAVIOUR. No fix.**

### 3.2 P1-A: `this_notional == 0` · **EXPECTED BEHAVIOUR**

See §1.3. Legitimate zero sizing at 99.6% deployment. Gate unchanged; only the
message corrected.

---

## 4. CONFIRMED DEFECTS REMAINING

### 4.1 BUG-1 · root cause CONFIRMED, **deliberately NOT deployed**

**Exact site:** `tasks/india_tasks.py:684` reads `settings`, but `:706` inside
the same function does `from utils.config import settings`. Python therefore
binds `settings` as function-local for the whole of `_india_trade_loop`
(spans 498–1437), and `:684` executes before `:706` → `UnboundLocalError`.

`:520` uses `as _cfg` and is safe; **`:706` is the shadowing import**.

**Blast radius, measured:** **442 occurrences today**. The exception is **not
swallowed** — the task fails. The cycle reaches `:647` and dies at `:684`.
Exits still run (`:544` auto-close, `:594-600` dynamic SL/TP, drawdown breaker
all sit above the failure point); **entries do not**.

**Why not fixed today.** `:684` computes `_NEWS_ONLY_BLOCKS_HUB_ENTRIES`, which
is the **entry-origination gate** at `:695`. Repairing the crash would open a
trade-origination path that has not run in months. That is a strategy change,
and the brief forbids one without independent evidence — Phase 15 additionally
found no information in the Hub-origination population. Recorded as an isolated
fix candidate for a dedicated deployment, exactly as instructed.

The one-line fix, when it is decided: alias the import at `:706`
(`from utils.config import settings as _s` plus its local uses), matching the
pattern `:520` already follows.

**Verified independent of today's work:** BUG-1 lives in `india_trade_loop`;
today's changes touch the candle fetcher, the candle task's lock-skip log, the
news engine and the risk-manager message. No interaction.

### 4.2 `causal_events.news_id` NULL · root cause **NOT PROVEN**

Progress, all from the correct log:

- Writer identified by column fingerprint: today's 544 events are all
  `country ∈ {MEDIUM, HIGH, LOW}` = `news_discovery_engine._build_evidence`
  (`:993 country=classification.impact`). `event_pipeline`'s fingerprints
  (`India`, `Global`, `DUPLICATE`, `IN`) are **absent today**, and all of those
  historical rows carry `news_id` 100%.
- Call-site mix over the 7-day log: **570** `process_ticker` invocations; only
  **66 (11%)** came from sites that pass `news_id` (RSS `:1784` = 62,
  NSE `:1920` = 4). The remaining **89%** came from the pre-market drain
  (`:1645`), which passes `news_id=None` by design.

**This is consistent with the observed 0%, but is not proof** — the RSS and NSE
paths should still have produced ~66 non-NULL rows, and they did not. Not
isolated. **No fix attempted.**

§1.1's cutoff should materially change this mix by freeing the engine to reach
live RSS/NSE items; tomorrow's data will show whether non-NULL rows appear.

---

## 5. UNPROVEN / NOT STARTED

| Item | State |
|---|---|
| P0-B `news_id` | Root cause **NOT PROVEN** (§4.2) |
| P0-C news latency by source | **NOT STARTED** |
| P0-D timezone normalisation | **NOT STARTED** — behavioural impact still **UNPROVEN** |
| P1-B `master_score` NULL | **NOT STARTED** |
| P1-C EXHAUSTION trigger | **NOT STARTED** |
| P1-D universe coverage gap | **NOT STARTED** |
| P2 capital policy | **NOT STARTED** — score-based preemption remains explicitly unsupported (+0.736% vs +0.744% forward MFE) |

---

## 6. Corrections to my own earlier work

**6.1 Phase 18 correction #1 is WITHDRAWN.** I reported that
`news_discovery_engine`'s event path "did not run at all today" based on
`Processing Ticker: = 0`. I was grepping `logs/news-engine.log`; this logger
writes to `logs/news-engine.err`, where the count is **570**. The path ran.

**6.2 Seven-day counts were mislabelled as daily.** Both engine log files were
created **2026-08-19** and append. "31 restarts today", "72 enqueued", "570
Processing Ticker" are **7-day** figures. The daily attribution I stated is
withdrawn; §1.2's timestamped format is what prevents a recurrence.

**6.3 The "22 of 22 missed" framing was misleading** — see §3.1. Correct
statement: deliberately declined by an evidence-backed suppression rule.

---

## 7. Files changed

| File | Change | Deployed to |
|---|---|---|
| `news_discovery_engine.py` | pre-market age cutoff; timestamped logging; poller + drain telemetry | news-engine |
| `engine/risk_manager.py` | zero-notional rejection message (additive only) | 4 Celery workers |
| `tests/test_premarket_queue_age_cutoff.py` | new, 7 tests | — |
| `tests/test_live_1m_delta_window.py` | Phase 18, 9 tests | — |

Untouched, as required: `utils/llm.py`, `dynamic_management.py`,
`crawler/zerodha_historical.py` (Phase 18), all strategy thresholds, prompts,
Master Score weights, capital limits, EXHAUSTION policy, BUG-1, BUG-2.

---

## 8. Tomorrow 09:15 live validation checklist

**Candle (Phase 18) — first beat execution 08:30, verify 09:15–10:00**

| Metric | Baseline 2026-08-26 | Expect |
|---|---|---|
| `saved / candles` | 2.5% | materially higher |
| run elapsed | 1,041–1,107s | materially lower |
| p50 per-symbol lag | 16.0 min | materially lower |
| >10 min stale | 60% of 1,743 | materially lower |
| symbols with ≥300 bars | — | must not fall |
| Kite `errors` | 0 | must stay 0 |
| `SKIPPED already_running` | unmeasurable | now countable |

**Pre-market drain (Phase 19)**
`[premarket_drain] completed=N/M` — expect M ≈ 369 not 2,611, and
`completed == M`.

**News**
Poller telemetry every 60s; queue `depth` must return to 0 between polls.
Watch whether non-NULL `news_id` rows appear once the engine reaches live items.

**Trade loop**
`SoftTimeLimitExceeded` / `TimeLimitExceeded` / SIGKILL must stay **0**.

---

## 9. Production risk assessment

**Low.** Every change deployed today is either additive telemetry or a
narrowing filter, none touches order placement, sizing arithmetic, or any
strategy parameter, and each is revertible by a single `git checkout` plus one
service restart. The riskiest is §1.1's cutoff, and its failure mode is
conservative: fewer stale headlines replayed, no row mutated.

**The open risk is BUG-1** — 442 failed trade-loop cycles a day, with entries
blocked and exits unaffected. It is now fully characterised and deliberately
left for its own deployment decision.

**Honest status:** three defects closed with measured before/after, two
investigated and correctly closed as expected behaviour, one fully diagnosed
and held back on purpose, and seven items untouched. The candle fix's headline
metric — freshness — remains a projection until tomorrow's open.
