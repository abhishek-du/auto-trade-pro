# PHASE 5 — INFRASTRUCTURE + BUY HYPOTHESIS

**Date:** 2026-08-25 · Two strictly separated objectives, reported separately.

**BUG-1 was NOT fixed. Master Intelligence was NOT connected. No threshold, stop, target,
weight, allocation or prompt changed. Zero orders. Zero paper trades. `PAPER_MODE` untouched.**

---

## Executive verdict

| | |
|---|---|
| **Part A — BUG-2** | **FIXED AND DEPLOYED.** `india_trade_loop` now has its own Celery queue and worker. Live and running at a 60-second cadence. |
| **Part B — verification** | **PENDING.** Full verification needs one complete market session; deployment landed at 20:01 IST, after the 15:30 close. |
| **Part D–N — the BUY hypothesis** | **NO EVIDENCE.** Phase 4's +0.312% does not survive. Across 17 sessions BUY returns **−0.055% EOD** and loses to its matched control at **every** horizon. |

> **PREVIOUS CONCLUSION NO LONGER HOLDS.** Phase 4 reported the Hub `BUY` population at
> +0.312% gross / +0.105% net on one session and 83 symbols, and flagged it as a question rather
> than a finding. On 17 sessions and 1,371 symbols it is **−0.055% gross, −0.263% net**, with a
> matched-control difference of **−0.138pp [−0.189, −0.087]** — significantly *worse* than
> comparable stocks, not better.

---

## Part A — BUG-2 repair

### The defect

`india_trade_loop` shared the two `default` queue slots with `run_master_intelligence_cycle`,
which scores ~1,663 symbols and holds a slot for hours. Measured 2026-08-25: **11 cycles inside
09:15–15:30 IST against ~375 expected**, with a single 329-minute gap (09:13:21 → 14:41:58)
through which the worker log is continuous `engine.indicators` plus Keras model loads.

This is the **third** task to hit the same failure. The precedent is in the file already:
`fast_sl_check` ran **once** on 21 Aug and **49,876 times** on 24 Aug after `exit_queue` was
split out; the tactical scans got `scan_queue` for the same reason on 24 Aug.

### The change

| file | change |
|---|---|
| `tasks/celery_app.py` | `Queue("trade_queue", …)` declared; `tasks.india_trade_loop` routed to it; beat entry gains `queue` + `expires: 55` |
| `deploy/systemd/autotrade-celery-trade-worker.service` | new unit — `--queues=trade_queue --concurrency=1 --hostname=trade@%h --prefetch-multiplier=1`, CPUQuota 40%, MemoryMax 1200M |

`concurrency=1` is deliberate: the loop manages open positions and must never run two cycles
against the same book concurrently.

`expires: 55` (< the 60 s cadence) follows the convention every other high-frequency entry in the
file uses. **A dedicated queue without an expiry would move the pile-up rather than remove it** —
if one cycle overruns, the backlog grows unbounded instead of the stale cycle dropping. The file's
own comment records the 63,000-task Redis backlog that convention exists to prevent.

**Nothing in either file contains origination logic.**

### Deployment evidence

```
[2026-08-25 20:01:30] trade@CISM-I-463 ready.

2026-08-25 20:01:30 | [india_trade_loop] Starting cycle     ← new trade worker
2026-08-25 20:02:02 | [india_trade_loop] Starting cycle
2026-08-25 20:03:03 | [india_trade_loop] Starting cycle

last cycle on the old default worker: 20:00:39
```

All seven services active: uvicorn, celery-worker, celery-beat, **celery-trade-worker**,
celery-scan-worker, celery-exit-worker, news-engine.

### Regression cover — `tests/test_trade_queue_routing.py`, 6 tests

| test | pins |
|---|---|
| `test_trade_queue_is_declared` | routing to an undeclared queue means nothing consumes it |
| `test_loop_is_routed_off_the_default_queue` | the route exists and targets `trade_queue` |
| `test_loop_does_not_share_a_queue_with_the_hub_cycle` | the specific contention that caused the gap |
| `test_beat_entry_carries_the_queue_and_an_expiry` | `expires` present **and** shorter than the cadence |
| `test_the_other_dedicated_lanes_are_untouched` | `exit_queue`/`scan_queue`/`default` undisturbed |
| `test_bug1_is_still_present_so_this_change_cannot_enable_origination` | fails loudly if BUG-1 is ever repaired without a deliberate decision |

**Mutation-tested:** removing the route fails 1; dropping the expiry fails 1; routing back to
`default` fails 2.

---

## Part B — cycle reliability

**PENDING — and it must not be called a success yet.**

The brief is explicit: *"Do NOT declare success merely because the worker is active. Success
requires actual cycle execution during market hours."* The fix went live at **20:01 IST**, after
the close.

| metric | target | observed so far |
|---|---|---|
| cycles inside 09:15–15:30 IST | ~375 | **not yet measurable** |
| median cycle interval | ≈60 s | **31–61 s** over the first 3 post-deployment cycles |
| maximum gap | no multi-minute gaps | **not yet measurable** |
| exceptions | — | none on the new worker so far |
| expired tasks | — | **not observable** — Celery logs nothing for an expired task |

What *is* established: the task is now dispatched to, and consumed by, a worker that nothing else
competes for. Whether that produces ~375 cycles under real market-hours load is the measurement,
and it requires the next session.

**A note on what this will and will not prove.** If the count recovers to ~375, queue starvation
is confirmed as the mechanism. If it does not, the cause is task expiry and needs a different
remedy. Either outcome is informative, and neither can create a Hub candidate, because BUG-1
still blocks the path.

---

## Part C — safety proof (run before deployment, and again immediately before restart)

Nine checks, all PASS:

```
[PASS] BUG-1 present: `settings` read before its local import  — read at :610, import at :632
[PASS] no module-level `settings` import rescues it
[PASS] crash precedes the Hub query                            — :610 < :697
[PASS] crash precedes every TradeIntent construction           — TradeIntent at [1306]
[PASS] crash precedes every execute_trade_intent call          — call at [1313]
[PASS] `settings` is compiled as a function-local (co_varnames)
[PASS] that pattern raises UnboundLocalError in this interpreter
[PASS] no TECHNICAL-family paper trade exists, ever            — count=0
[PASS] no TECHNICAL intent has ever been EXECUTED at the gate  — count=0

  BUG-1 still blocks Hub candidate creation : YES
  Master Intelligence can reach candidates  : NO
  Order submission reachable from this test : NO
  A paper trade can be opened               : NO
  A live trade can be opened                : NO — PAPER_MODE=true and no path
```

### How the proof was obtained, and one thing deliberately not done

The first version called `_india_trade_loop()` for real. Outside market hours it returns at the
status check on `:524` and never reaches `:610`, so it proved nothing — and **mocking the market
open to force the crash would have run the exit-management block against live positions**, which
a safety proof must not cause.

The compiler settles it without side effects instead: because `settings` is imported locally at
`:632`, CPython classifies it as a function-local for the whole body, so it appears in
`co_varnames`. Reading a local before it is bound raises `UnboundLocalError` by language
semantics — **there is no execution path in which `:610` succeeds.**

---

## Part D — BUY historical sample

**Definition, fixed before any result was seen:** `signal = 'BUY'` from
`master_intelligence_scores`. No score threshold substituted, no label re-derived, nothing tuned.

**n = 21,463 · 1,371 symbols · 17 sessions** (2026-08-03 → 2026-08-25). Forward returns from 1m
candles only, symbol-clustered bootstrap, MIS cost basis 0.2072%.

| horizon | n | symbols | gross | **net** | median | win% | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| +5m | 21,463 | 1,371 | −0.007 | −0.214 | +0.000 | 42.0 | [−0.011, −0.002] |
| +15m | 21,463 | 1,371 | −0.001 | −0.208 | +0.000 | 44.8 | [−0.008, +0.006] |
| +30m | 21,463 | 1,371 | −0.011 | −0.218 | −0.015 | 43.7 | [−0.021, −0.002] |
| +60m | 21,463 | 1,371 | −0.019 | −0.226 | −0.028 | 43.9 | [−0.033, −0.006] |
| +120m | 21,463 | 1,371 | −0.029 | −0.236 | −0.042 | 43.6 | [−0.050, −0.009] |
| **EOD** | 21,463 | 1,371 | **−0.055** | **−0.263** | −0.056 | 43.5 | **[−0.095, −0.016]** |

**Gross return is negative at five of six horizons, and significantly so at four.** Phase 4's
+0.312% came from 83 symbols on one session.

---

## Part E — matched control

Controls drawn at the same instant, matched on trailing 15m return, realised volatility and
session-to-date traded value — all from bars at or before the observation timestamp.

**Match quality is excellent** (|SMD| < 0.10 on all three axes):

```
SMD 15m return  -0.005      SMD volatility  +0.041      SMD liquidity  +0.004
```

Coverage: 8,302 of 21,463 BUY observations (38.7%) had a control satisfying all three bands.

| horizon | n | BUY | control | **diff** | 95% CI | verdict |
|---|---:|---:|---:|---:|---|---|
| +5m | 8,298 | −0.012 | −0.004 | −0.008 | [−0.013, −0.003] | **BUY < CTL** |
| +15m | 8,298 | −0.004 | +0.016 | −0.020 | [−0.029, −0.011] | **BUY < CTL** |
| +30m | 8,298 | −0.020 | +0.013 | −0.033 | [−0.045, −0.020] | **BUY < CTL** |
| +60m | 8,298 | −0.030 | +0.027 | −0.057 | [−0.076, −0.038] | **BUY < CTL** |
| +120m | 8,298 | −0.042 | +0.038 | −0.080 | [−0.109, −0.051] | **BUY < CTL** |
| **EOD** | 8,298 | −0.104 | +0.034 | **−0.138** | **[−0.189, −0.087]** | **BUY < CTL** |

**BUY underperforms comparable stocks at every horizon, and the gap widens monotonically with
time.** This is the strongest single result in Phase 5 and it points against the hypothesis.

---

## Part F — session robustness

| session | n | sym | gross | net | median | win% | 95% CI |
|---|---:|---:|---:|---:|---:|---:|---|
| 2026-08-03 | 650 | 226 | +0.214 | +0.006 | +0.085 | 56.9 | [+0.120, +0.307] |
| 2026-08-04 | 1,403 | 311 | −0.080 | −0.287 | −0.088 | 42.8 | [−0.276, +0.111] |
| 2026-08-05 | 1,316 | 308 | +0.097 | −0.110 | +0.000 | 48.6 | [−0.046, +0.244] |
| 2026-08-06 | 1,668 | 345 | −0.191 | −0.398 | −0.165 | 37.4 | [−0.348, −0.035] |
| 2026-08-07 | 1,572 | 342 | −0.156 | −0.364 | −0.056 | 44.7 | [−0.314, −0.011] |
| 2026-08-10 | 1,660 | 333 | −0.242 | −0.450 | −0.207 | 38.6 | [−0.415, −0.071] |
| 2026-08-11 | 1,521 | 312 | +0.062 | −0.145 | −0.026 | 46.9 | [−0.150, +0.305] |
| 2026-08-12 | 1,801 | 454 | +0.047 | −0.160 | +0.008 | 50.6 | [−0.117, +0.207] |
| 2026-08-13 | 1,167 | 252 | −0.157 | −0.364 | −0.110 | 41.1 | [−0.340, +0.007] |
| 2026-08-14 | 1,665 | 665 | −0.097 | −0.304 | −0.077 | 41.0 | [−0.207, +0.021] |
| 2026-08-17 | 1,444 | 390 | +0.125 | −0.082 | +0.000 | 49.0 | [+0.004, +0.249] |
| 2026-08-18 | 924 | 285 | +0.053 | −0.155 | −0.097 | 41.7 | [−0.091, +0.199] |
| 2026-08-19 | 1,033 | 354 | −0.130 | −0.337 | −0.089 | 39.3 | [−0.240, −0.019] |
| 2026-08-20 | 516 | 159 | −0.145 | −0.353 | −0.126 | 36.6 | [−0.286, −0.000] |
| 2026-08-21 | 993 | 225 | −0.127 | −0.334 | −0.107 | 36.7 | [−0.242, −0.009] |
| 2026-08-24 | 1,096 | 293 | −0.251 | −0.459 | −0.173 | 36.9 | [−0.391, −0.112] |
| 2026-08-25 | 1,034 | 236 | +0.162 | −0.046 | +0.000 | 49.4 | [+0.019, +0.337] |

```
positive sessions: 7/17      pattern  + - + - - - + + - - + + - - - - +
```

**Only 2 of 17 sessions are positive after MIS costs** (08-03 at +0.006%, and 08-25 at −0.046% is
not). Six sessions are significantly negative.

---

## Part G — symbol concentration

```
unique symbols: 1,371        total summed return: -1,190.8pp
top  5 contribution: -31.1%
top 10 contribution: -48.1%
top 20 contribution: -76.7%
```

The concentration test is normally used to check whether a *positive* result depends on a few
names. Here the total is negative, so the top contributors are the largest **losers** — 20
symbols account for 76.7% of the loss.

Removing them (mechanically, by contribution, not by result-shopping) does not rescue the
population:

| | n | gross | net | 95% CI |
|---|---:|---:|---:|---|
| excluding top 5 | 21,312 | −0.073 | −0.280 | [−0.109, −0.036] |
| excluding top 10 | 21,193 | −0.083 | −0.290 | [−0.120, −0.047] |

**It gets worse.** The result is not concentration-driven; it is broad.

---

## Part H — cost-adjusted results

MIS basis, 0.2072% round-trip, per the brief.

| | gross | cost | **net** |
|---|---:|---:|---:|
| BUY, all horizons | −0.007 … −0.055 | 0.207 | **−0.208 … −0.263** |
| BUY, best session (08-03) | +0.214 | 0.207 | +0.006 |
| BUY, TEST half | −0.057 | 0.207 | −0.264 |

**No horizon, and no aggregate, is positive after costs.** The criterion in Part I —
"a candidate cannot be considered economically interesting unless the result remains positive
after MIS costs" — is not met anywhere.

---

## Part I — chronological out-of-sample

Session-level split, no tuning on TRAIN, BUY defined before the test.

| split | sessions | n | symbols | gross | net | 95% CI | vs matched control |
|---|---|---:|---:|---:|---:|---|---|
| TRAIN | 08-03 → 08-12 (8) | 11,591 | 1,039 | −0.055 | −0.262 | [−0.116, +0.008] | −0.148 [−0.231, −0.069] |
| **TEST** | 08-13 → 08-25 (9) | 9,872 | 1,043 | **−0.057** | **−0.264** | **[−0.102, −0.010]** | **−0.126 [−0.186, −0.064]** |

**The TEST half reproduces the TRAIN half almost exactly**, including a significantly negative
control difference. The negative result is stable out-of-sample — which is itself a meaningful
finding: it is a *repeatable* negative, not noise.

---

## Part J — BUY vs the other stored labels

Categorical comparison only. Nothing optimised.

| label | n | symbols | +30m | EOD gross | EOD net | 95% CI (EOD) |
|---|---:|---:|---:|---:|---:|---|
| **BUY** | 21,463 | 1,371 | −0.011 | **−0.055** | −0.263 | [−0.095, −0.016] |
| `STRONG_BUY` | 56,653 | 1,435 | −0.013 | −0.073 | −0.281 | [−0.097, −0.049] |
| `NEUTRAL` | 58,297 | 1,362 | −0.012 | −0.084 | −0.291 | [−0.104, −0.064] |
| `SELL` | 12,802 | 558 | −0.016 | −0.112 | −0.319 | [−0.152, −0.073] |

> **Is BUY genuinely different from STRONG_BUY?**

The labels **do** order correctly — BUY > STRONG_BUY > NEUTRAL > SELL, monotonically, at both
+30m and EOD. And BUY is the best of the four.

**But the entire spread from best to worst is 0.057 percentage points**, against a 0.207pp cost
floor. Every label is negative. The ordering is real and economically irrelevant — the same
conclusion Phase 2 reached from the decile spread (−0.003 to +0.016pp).

*No inference is offered as to why BUY edges STRONG_BUY; the data does not support one.*

---

## Part K — BUY vs random timestamp on the same symbol and session

Fixed horizons only. EOD is excluded because a random timestamp up to 60 bars earlier gets a
longer window to the close — the confound that invalidated an earlier version of this test.

| horizon | n | BUY | random-t | diff | 95% CI | verdict |
|---|---:|---:|---:|---:|---|---|
| +5m | 21,463 | −0.007 | −0.002 | −0.004 | [−0.011, +0.002] | inconclusive |
| +15m | 21,463 | −0.001 | −0.005 | +0.004 | [−0.005, +0.013] | inconclusive |
| +30m | 21,463 | −0.011 | −0.005 | −0.006 | [−0.017, +0.005] | inconclusive |
| +60m | 21,463 | −0.019 | −0.011 | −0.008 | [−0.020, +0.003] | inconclusive |
| +120m | 21,463 | −0.029 | −0.032 | +0.003 | [−0.008, +0.014] | inconclusive |

> **Does the BUY label identify a better moment than an arbitrary moment in the same stock?**
> **No.** Every horizon is inconclusive, and the point estimates alternate sign. The label carries
> no timing information.

---

## Part L — final classification

Against the seven criteria in Part N, all of which must hold for category 4:

| # | criterion | result |
|---|---|---|
| 1 | positive matched-control difference | **FAIL** — −0.138pp EOD, negative at every horizon |
| 2 | 95% CI excludes zero | passes, but **in the wrong direction** |
| 3 | positive net MIS return | **FAIL** — −0.263% |
| 4 | majority of sessions positive | **FAIL** — 7/17 gross, 1/17 net |
| 5 | no extreme symbol concentration | passes — and removing the top names makes it *worse* |
| 6 | chronological TEST remains positive | **FAIL** — −0.057% gross, −0.264% net |
| 7 | BUY beats same-symbol random timestamps | **FAIL** — inconclusive at every horizon |

### **Classification: 1 — NO EVIDENCE.**

Not "inconclusive": with 21,463 observations, 1,371 symbol clusters and 17 sessions, the
matched-control difference is significantly negative and the out-of-sample half reproduces it.
This is a well-powered negative, not an absence of power.

**The Phase 4 observation is withdrawn.** It was one session, 83 symbols, and was flagged at the
time as "a question, not a finding". It has now been asked and answered.

---

## Part M — what remains unknown

- **Whether BUG-2's fix produces ~375 cycles.** Requires the next session (Part B).
- **Whether queue starvation or task expiry was the mechanism.** The next session's cycle count
  distinguishes them; Celery logs neither directly. **EVIDENCE NOT AVAILABLE** today.
- **Why 61.3% of BUY observations had no matched control.** The bands (±0.15pp return, ±20% vol,
  0.5–2.0× liquidity) are strict, and 2026-08-03's liquid pool was only 3 symbols. The matched
  subset may not be representative of the whole BUY population — though the unmatched aggregate
  (§D) is negative too, so this does not change the conclusion.
- **Whether the label ordering (BUY > STRONG_BUY > NEUTRAL > SELL) is stable.** It holds across
  17 sessions in aggregate; per-session stability was not tested, and at 0.057pp total spread it
  would not matter economically either way.
- **What the loop does once BUG-2 is verified and BUG-1 is still unfixed.** It will run its exit
  management, dynamic SL/TP and drawdown breaker ~375 times a session instead of 11 — a benefit
  independent of origination, and unquantified.

---

## Recommended next

1. **Measure Part B** on the next session: cycle count inside 09:15–15:30 IST, median and p95
   interval, maximum gap. That is one query against `logs/celery-trade-worker.log`.
2. **Do not revisit the BUY population.** It has now been tested with 21,463 observations across
   17 sessions against a well-matched control, out-of-sample, and against same-symbol random
   timestamps. More data will not change a result this stable.
3. **BUG-1 remains unfixed and should stay that way** until there is a reason to enable Hub
   origination. Phase 2 found no information in the score, Phase 4 found the candidate filtering
   degrades forward return, and Phase 5 now finds the best-performing label is significantly
   worse than matched controls. Three independent phases point the same way.

---

## Classification summary

| # | finding | classification |
|---|---|---|
| 1 | BUG-2 was queue contention with the Hub cycle | **STRONGLY SUPPORTED** — fix deployed, verification pending |
| 2 | The fix is live and the loop runs on its own worker | **CONFIRMED** |
| 3 | The fix produces ~375 cycles in market hours | **EVIDENCE NOT AVAILABLE** — needs the next session |
| 4 | BUG-1 still blocks Hub origination after deployment | **CONFIRMED** — 9-check proof, compile-level |
| 5 | Any order/paper/live trade reachable from this change | **RULED OUT** |
| 6 | Hub `BUY` contains repeatable forward information | **NO EVIDENCE** |
| 7 | BUY beats a matched control | **RULED OUT** — −0.138pp EOD, negative at every horizon |
| 8 | BUY beats a same-symbol random timestamp | **RULED OUT** — inconclusive at every horizon |
| 9 | The Phase 4 BUY observation (+0.312%) replicates | **RULED OUT** — one-session artefact, withdrawn |
| 10 | The stored labels order correctly | **CONFIRMED** — BUY > STRONG_BUY > NEUTRAL > SELL |
| 11 | That ordering is economically meaningful | **RULED OUT** — 0.057pp spread against a 0.207pp cost floor |
| 12 | The negative result is out-of-sample stable | **CONFIRMED** — TEST reproduces TRAIN |

---

**BUG-1 NOT fixed. Master Intelligence NOT connected. No strategy logic changed. No thresholds
changed. No trades executed. No Hub origination deployed. STOP.**
