# PHASE 21 — OPPORTUNITY CONVERSION + EDGE RETENTION BASELINE

**Date:** 2026-08-26 · **Safety gate honoured:** no strategy, threshold, R:R,
capital, AI-routing, exit, BUG-1 or execution change. Instrumentation only,
proven behaviour-neutral below.

---

## Headline

Two numbers, from opposite ends of the pipeline:

**Conversion — 2.5%.** Under a pre-specified, outcome-blind opportunity rule,
2026-08-26 produced **1,105 opportunities**. **80 (7.2%)** produced a tactical
signal. **28 (2.5%)** ended up in a traded symbol.

**Retention — 48 of 68.** Across every closed trade on record, **48 never
reached +0.5% maximum favourable excursion**. Median MFE is **+0.06%**. Of the
39 that did show a positive peak, **17 closed negative**.

These are not the same problem and they do not have the same fix. The first is
about finding; the second is about keeping.

---

## Workstream A — instrumentation (deployed)

### What the nine states now resolve to

| State | How it is now answerable |
|---|---|
| not in universe | `HUB_UNIVERSE_SNAPSHOT` row, per rebuild, symbol → rank |
| in universe but not scanned | `no_price` / `no_candles` counters **+ the symbol lists** |
| scanned but no rule fired | `scanned − raw_signals` |
| rule fired but signal rejected | `raw_signals − kept` (score threshold) |
| persisted but ranked out | `kept − persisted` (`TACTICAL_TOP_N`) |
| risk rejected | `tactical_signals.reason` (R:R, sector, concurrency) |
| capital rejected | `tactical_signals.reason` (cash buffer) |
| execution rejected / executed | `tactical_signals.executed` |

Before today, **four of these nine were not distinguishable at all**.

### Changes

| File | Change |
|---|---|
| `engine/tactical_executor.py` | `ScanResult` gains `universe / no_price / no_candles` and the two dropped-symbol lists; both `continue`s in `_collect()` now increment; per-scan summary logs all four; one `TACTICAL_SCAN_FUNNEL` row per scan |
| `engine/hub_universe.py` | one `HUB_UNIVERSE_SNAPSHOT` row per rebuild carrying symbol → rank |

**Bounded:** dropped-symbol lists cap at `_FUNNEL_SYMBOL_CAP = 250`, enforced at
the append site so memory cannot grow, with the truncation flag computed from
the **uncapped** counters. **No per-symbol log lines** — 1,476 symbols against a
3-minute scan is ~700k lines a day, and a test forbids adding any.

**No schema change, no migration.** Both rows ride `simulation_logs`, which
already exists and already accepts JSON.

**Timestamped, no sensitive payloads:** counts and symbol names only. No prices,
no request bodies, no credentials.

### Behaviour-neutrality — proven, not asserted

Diff of `tactical_executor.py` against `main`, all comment lines excluded, shows
**exactly one removed line**:

```
-from dataclasses import dataclass          (replaced by "..., field")
```

Nothing else was removed or altered. Every other change is an addition. Any edit
to a trading-logic line would appear in that list and does not.

Two further guarantees, both enforced by tests:

- **The funnel row uses its own session.** The scan's session must contain
  nothing but `TacticalSignal` rows, and `test_tactical_executor.py` asserts
  that. My first attempt added the telemetry row to the scan's session and
  **broke two existing tests** — the suite caught a genuine behaviour change
  before deploy. Corrected to a separate `AsyncSessionLocal`, which also makes
  the failure isolation real rather than nominal.
- **Both telemetry writes are failure-isolated.** The universe snapshot is
  written after the universe itself commits and rolls back only itself; the
  funnel row never touches the scan's session at all.

### Tests and deployment

| | |
|---|---|
| New tests | **13** (`test_opportunity_funnel_telemetry.py`) |
| Full suite | **1,777 passed · 27 failed · 7 skipped · 5 errors** |
| Phase-19 baseline | 1,764 · 27 · 7 · 5 |
| **Delta** | **+13 passed, zero new failures** ✅ |

Deployed to `autotrade-celery-scan-worker` (PID 3439377) and
`autotrade-celery-worker` (PID 3439376). All 7 services active, `NRestarts=0`,
**0** import/syntax/name errors.

**Rollback:** `git checkout -- autotrade-backend/engine/tactical_executor.py
autotrade-backend/engine/hub_universe.py` then restart those two workers.

**First data:** tomorrow's session. The scan does not run after close, so none
of this has produced a row yet.

---

## Workstream B — edge retention, all 68 closed trades

*(73 rows exist; 5 lack a candle on their close date and are excluded.)*

### Entry quality is the binding constraint

| Metric | Value |
|---|---|
| **Trades whose MFE never reached +0.5%** | **48 / 68 (71%)** |
| Median MFE | **+0.06%** |
| p75 MFE / max MFE | +0.61% / +3.27% |
| Median MAE | −0.12% |
| p25 MAE / min MAE | −0.36% / −2.97% |

Most positions never move. No exit rule can recover a trade that does not go
anywhere, and this bounds everything in the next table.

### MFE capture — of the peak favourable move, how much survived?

| Metric | Value |
|---|---|
| n (trades with a measurable positive peak) | 39 |
| **Median capture** | **26.7%** |
| Kept > 50% of peak | 16 / 39 |
| **Ended negative after a positive peak** | **17 / 39** |

The mean is meaningless here (small denominators produce huge ratios); the
median is the usable statistic.

### By exit family — actual versus holding the same trade to close

Honest benchmark, no look-ahead:

| Exit reason | n | realised | if held | **net** | saved | cost |
|---|---:|---:|---:|---:|---:|---:|
| STOP_LOSS | 22 | 125 | 5,692 | **−5,567** | 1,281 | 6,848 |
| MIS_SQUAREOFF | 18 | −2,215 | 326 | **−2,541** | 258 | 2,799 |
| T1_REVERSAL_EXIT | 4 | 3,299 | 4,765 | **−1,466** | 0 | 1,466 |
| CONFIRMATION_LOST | 1 | −88 | −12 | −76 | 0 | 76 |
| **EXHAUSTION** | 18 | −3,747 | −3,943 | **+196** | 2,726 | 2,530 |
| REALLOCATED | 1 | −589 | −1,102 | +513 | 513 | 0 |
| TAKE_PROFIT | 4 | 5,037 | 4,172 | +865 | 865 | 0 |
| **ALL** | **68** | **1,823** | **9,898** | **−8,076** | 5,643 | 13,719 |

### The two families the brief named explicitly

**T1_REVERSAL_EXIT — n = 4. Do not act on this.** Net −₹1,466, worst per-trade
figure of any family at −₹366, and it cost on **all four**. But four trades is
not evidence. Today contributed 3 of the 4; the brief's instruction not to
change it on today's sample is correct and extends to the full history, which is
barely larger. **INSUFFICIENT EVIDENCE.**

**MIS_SQUAREOFF — n = 18. Net −₹2,541, and this one is suspicious.** The
positions were worth **+₹326 held to 15:29** and realised **−₹2,215**. Squareoff
executes around 15:31, two minutes later. A two-minute window should not produce
a ₹2,541 gap across 18 trades. Candidate explanations — squareoff pricing,
applied slippage, or a defect — were **not tested**. This is the single most
concrete thing to investigate next, and it is a measurement question, not a
policy one. **PARTIALLY SUPPORTED: the gap is confirmed, its cause is not.**

**EXHAUSTION is net positive (+₹196) across the full history.** Phase 19B blamed
it for the day's giveback. That attribution is **withdrawn** — it measured from
the 12:00 high-water mark, which conflates the exit decision with price drift.

### Concentration and the intraday cut

STOP_LOSS's −₹5,567 looks like the largest leak, but **75% of it comes from 3
trades**, all held **68–70 hours** — multi-day swings, for which "hold to that
day's close" is not the alternative that existed. Splitting properly:

| | n | realised | if held | net |
|---|---:|---:|---:|---:|
| **Intraday** (benchmark valid) | 50 | −5,070 | +1,237 | **−6,307** |
| Multi-day (benchmark invalid) | 18 | 6,892 | 8,661 | −1,769 |

Intraday STOP_LOSS is **−₹2,132 over 10 trades**, not −5,567. And hold-to-close
ignores tail protection entirely: a stop that costs on most trades can still be
correct if it prevents one catastrophic loss, and n=10 cannot show that tail.

### By day and by family

| Date | n | realised | if held | net |
|---|---:|---:|---:|---:|
| 2026-08-19 | 1 | 146 | 227 | −82 |
| 2026-08-21 | 4 | −485 | 19 | −504 |
| **2026-08-24** | 13 | 3,037 | 7,768 | **−4,731** |
| 2026-08-25 | 16 | −3,668 | −2,371 | −1,297 |
| 2026-08-26 | 34 | 2,793 | 4,255 | −1,462 |

| Family | n | realised | if held | net | wins |
|---|---:|---:|---:|---:|---:|
| TACTICAL | 61 | 1,848 | 9,921 | −8,073 | 21 |
| DIRECT_NEWS | 3 | −43 | −258 | **+215** | 1 |
| EVENT_DRIVEN | 3 | −128 | 7 | −136 | 2 |

The −₹8,076 total is **dominated by one session** (08-24, −₹4,731). News
families remain unusable at n=3.

---

## Workstream C — opportunity definition, specified before measurement

### The rule, fixed before any forward return was computed

A symbol becomes an opportunity at bar `t0` when all hold, using only
information available at or before `t0`:

```
E1  TRADABLE       in hub_universe with turnover_cr >= 5.0   (the system's own bar)
E2  PRICED         session open > Rs 20                       (the system's own bar)
E3  DATA           >= 30 one-minute bars exist before t0
E4  BREAKOUT       close(t0) > max(high) of the 30 bars before t0
E5  PARTICIPATION  volume(t0) >= 2x mean volume of those 30 bars
E6  WINDOW         09:45 <= t0 <= 15:00 IST
```

First qualifying bar per symbol per session only. **Nothing references any price
after t0**, so this does not select on outcome — the failure mode the brief
warned about, and the one my own Phase 19B/20 "14 biggest movers" analysis
suffered from.

*Recorded honestly:* E5 was written as "median" and changed to "mean" because
Postgres cannot window `percentile_cont`. The change was made **before** any
forward return was measured.

### Result — 2026-08-26

| | |
|---|---|
| **Opportunity count** | **1,105** |
| Distinct symbols | 1,105 (one event each) |

Forward 60-minute excursion, measured **only after** the rule was fixed:

| | value |
|---|---|
| MFE median | **+0.30%** |
| MFE p75 / p90 / max | +0.80% / +1.59% / +8.08% |
| MAE median | −0.33% |
| MAE p25 / min | −0.62% / −6.47% |
| reached +0.5% within 60m | 410 / 1,105 (**37%**) |
| reached +1.0% | 217 / 1,105 (20%) |
| reached +2.0% | 71 / 1,105 (6%) |

### The conversion funnel

```
t0 opportunities                 1,105
        │
        ▼  7.2%
tactical signal after t0            80
        │
        ▼  2.5%
symbol traded that day              28
```

**Two readings, and the honest one matters.** 1,105 events a day is a lot, and
median forward MFE of +0.30% against a round-trip cost floor of roughly
0.21–0.39% means **the median opportunity under this rule is not tradable**.
The rule is deliberately loose; it is a *baseline*, not a recommendation. What
it establishes is the denominator that has been missing — and that the system
converts 2.5% of it.

Whether the 80 it did signal are drawn from the profitable tail or at random is
**NOT MEASURED** here.

---

## Data gaps

| Gap | State |
|---|---|
| Per-symbol scan outcome | **Closed today** — no data until tomorrow |
| Historical universe membership | **Closed today** for future sessions; **permanently unrecoverable** for past ones |
| `tactical_signals` depth | 5 sessions only. A 20–30 session funnel needs ~4 more weeks |
| MIS_SQUAREOFF pricing | **UNPROVEN** — the ₹2,541 gap has no established cause |
| Are the 80 signalled opportunities better than the 1,025 unsignalled? | **NOT MEASURED** |
| `paper_trades.holding_hours` reads 22–43h for intraday trades | Known defect, uninvestigated |
| T1_REVERSAL_EXIT | n = 4. **INSUFFICIENT EVIDENCE** |

---

## Recommended fixes — ranked by impact × evidence × production risk

**Not implemented in this phase, per the brief.**

| # | Item | Impact | Evidence | Prod risk | Type |
|---|---|---|---|---|---|
| 1 | **Investigate the MIS_SQUAREOFF ₹2,541 gap** | high — applies to every intraday position | **CONFIRMED** gap, unknown cause | none (read-only) | investigation |
| 2 | **Measure whether the 80 signalled opportunities beat the 1,025 unsignalled** | decides whether selection has any edge at all | data already exists | none | investigation |
| 3 | **Wait for the new telemetry, then rebuild this funnel** | resolves 4 previously invisible stages | deployed today | none | measurement |
| 4 | Entry quality — 71% of trades never reach +0.5% MFE | highest ceiling of anything here | **CONFIRMED**, n=68 | high | strategy |
| 5 | `TACTICAL_TOP_N=15` discarding ~95% of qualifying signals | large but unquantified | CONFIRMED mechanism, unmeasured effect | medium | strategy |
| 6 | T1_REVERSAL_EXIT | −₹366/trade | **INSUFFICIENT** (n=4) | medium | wait for data |
| 7 | STOP_LOSS geometry | −₹2,132 intraday | PARTIALLY SUPPORTED; tail unmeasurable at n=10 | high | wait for data |
| 8 | EXHAUSTION | net **+₹196** | **RULED OUT** as a problem | — | no action |

Items 1–3 are read-only and cost nothing. **Items 4–7 are all strategy changes**
and none of them has enough evidence yet to justify the risk. That ranking is
the honest output of this phase: the highest-impact item (entry quality) is also
the one with the least safe path to a fix.

---

*All figures from the production database, our own 1m candles, and production
logs on 2026-08-26. Instrumentation deployed and proven behaviour-neutral; no
strategy, threshold, routing, capital, exit or execution behaviour was changed,
and no order was placed.*
