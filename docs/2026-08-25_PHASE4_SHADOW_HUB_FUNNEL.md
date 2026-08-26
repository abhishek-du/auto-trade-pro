# PHASE 4 — SHADOW HUB FUNNEL RECONSTRUCTION

**Date:** 2026-08-25 · **Orders placed: 0. Trades opened: 0. Production changes: 0.**
Master Intelligence was **not** connected to execution. Nothing was deployed.

---

## 1. Executive verdict

> **If the Hub origination path were alive, it would produce ~15 candidates per minute drawn
> from only 36 distinct symbols per session — and the filtering would make the population
> *worse*, not better, than the unfiltered Hub output.**

| question | answer |
|---|---|
| How many candidates would it produce? | **5,160 candidate-cycles** on 2026-08-25 — but only **36 unique symbols** |
| What kills the rest? | confidence gate **79.9%**, candidate cap **18.3%**, price **0.3%** |
| Is the cap binding? | **344 of 344 cycles (100%)** — median 192 actionable against a cap of 15 |
| Does the funnel diversify? | **No.** Top 10 symbols = 63.0% of candidate-cycles; top 20 = 92.5% |
| Does it prefer high scores? | **Yes, strongly** — candidate median score 66.7 vs 28.2 for all Hub rows |
| Is it active all session? | **Yes** — ~15/cycle from 09:15 to 15:00, tailing to 3.0 in the final 20 minutes |
| Does filtering improve forward return? | **No — it degrades it by −0.159pp** (all Hub rows +0.198% → candidates +0.040%) |

**Nothing here is a reason to connect Master Intelligence.** Phase 2's finding stands, and this
phase adds a second, independent reason for caution: the funnel's output is so concentrated
(34 symbol clusters) that its forward return **cannot be evaluated at all** — the 95% CI on the
shadow candidates is [−0.538, +0.803].

---

## 2. Shadow safety proof

### The literal instruction could not be followed safely — and that is a finding

Part 1 asked for a branch with BUG-1 fixed, not deployed. **In this environment, editing
`tasks/india_tasks.py` in the working tree *is* a deployment:**

```
autotrade-celery-worker.service
  ExecStart=.../watchmedo auto-restart --directory=. --pattern="*.py" --recursive
```

The live worker is `active` and watches the backend directory recursively for `*.py`. Any edit
restarts production with the patch. Part 1 ("Do NOT deploy it") and Part 15 ("prove order
submission is impossible") are therefore in direct conflict with a working-tree edit.

**Resolution:** the branch was created in a **git worktree outside the watched directory**
(`/tmp/.../shadow-wt`), so the fix exists and is reviewable while the live tree stays untouched:

```
$ git status --short -- autotrade-backend/tasks/india_tasks.py
(empty)                                   ← live worker unaffected
$ grep -c 'getattr(settings, "NEWS_ONLY_BLOCKS_HUB_ENTRIES"' .../tasks/india_tasks.py
2                                         ← live tree still carries the bug, deliberately
```

Branch: `research/phase4-shadow-hub`. The fix is one expression —
`getattr(settings, …)` → `getattr(_cfg, …)` at `:610`, reusing the module already bound at
`:520`. It adds no import and is not merged.

### Why the funnel was reconstructed offline rather than run live

Even with the fix, a live shadow run was impossible today: it is 19:43 IST, `is_nse_market_open()`
returns `False`, and `_india_trade_loop` returns at its own entry-window check long before the
Hub query. Part 5 anticipates this ("prefer the next full session"). The funnel was therefore
**replayed offline against the stored Hub rows for 2026-08-25**, reproducing the production
filter chain line by line.

### Part 15 — the four required proofs

An AST guard (`p4_safety.py`) runs before the funnel and refuses to proceed unless all four hold:

```
banned execution imports/calls : NONE
session.add/commit/flush calls : 0
mutating SQL literals          : 0
.execute() calls               : 3 — non-SELECT: NONE

order submission     : IMPOSSIBLE
trade opening        : IMPOSSIBLE
capital allocation   : IMPOSSIBLE
paper execution      : IMPOSSIBLE
```

The guard bans `engine.decision_router`, `engine.tactical_executor`, `engine.zerodha_executor`,
`paper_trading.*`, `engine.agent.execution`, `engine.agent.agent_loop`, and the calls
`execute_trade_intent`, `authorize_trade_intent`, `route_decision`, `open_paper_trade`,
`close_paper_trade`, `scale_out_paper_trade`, `place_real_order`, `place_order`, `_record_exit`.

**A note on how this guard was built:** its first version banned the bare name `execute`, which
flagged SQLAlchemy's `session.execute` and would have made the proof vacuous by failing on every
SELECT. It now checks each `.execute()` call individually and requires its SQL literal to be a
SELECT. That is a stronger guarantee than the blanket ban, not a weaker one.

### Fidelity — where the reconstruction diverges from production

Stated rather than buried, because a reconstruction that overstates its own accuracy is worse
than no reconstruction:

1. **Entry price.** Production tries `PRICE_CACHE`, then Kite REST LTP, then a ≤90-minute candle.
   Neither the cache nor a live REST call is reproducible after the fact, so this uses the third
   fallback only. Production would resolve a price at least as often, so **`BLOCKED_BY_PRICE` is
   an upper bound** (measured: 0.3%, so the effect is small either way).
2. **Position/sector weights** are rebuilt from `paper_trades` open at each cycle instant rather
   than from a live `get_position_weights()` call.
3. **Research veto and portfolio-brain stance** run *after* the cap and make live web calls. They
   are not reproduced, so **`SHADOW_ELIGIBLE` is also an upper bound**.

---

## 3. Hub funnel counts — 2026-08-25, 366 one-minute cycles (09:15–15:20 IST)

Every filter cites the production line it reproduces.

| stage | per cycle (avg) | source |
|---|---:|---|
| Hub rows inside the 45-minute window | 1,541 | `india_tasks.py:658–697` |
| …actionable label, `is_blocked=False` | 928 | `:693–695` |
|   `STRONG_BUY` | 610 | |
|   `BUY` | 182 | |
|   `SELL` | 136 | |
|   `STRONG_SELL` | 0 | |
| signals surviving every filter | 183.7 | `:746–836` |
| **level_pool after the cap** | **14.1** | `:888` |
| **session total shadow candidates** | **5,160** | |

**Comparison to 2026-08-24:** 366 cycles, 1,603 hub rows/cycle, **5,355** shadow candidates —
materially identical, so the shape is not a one-day artefact.

---

## 4. Terminal-state histogram

Every candidate-cycle gets exactly one terminal state. Reasons come from the code's own
conditions; none invented.

| terminal state | count | share | source line |
|---|---:|---:|---|
| `BLOCKED_BY_CONFIDENCE` | 271,328 | **79.9%** | `:755` `conf < 50.0 and not news_override` |
| `BLOCKED_BY_CANDIDATE_CAP` | 62,087 | **18.3%** | `:888` `actionable[:15]` |
| `SHADOW_ELIGIBLE` | 5,160 | 1.5% | survived to `level_pool` |
| `BLOCKED_BY_PRICE` | 1,054 | 0.3% | `:~805` `entry_price <= 0` |
| `BLOCKED_BY_SELL_CONFIDENCE` | 0 | 0.0% | `:765` |
| `BLOCKED_BY_STOCK_WEIGHT` | 0 | 0.0% | `:828` |
| `BLOCKED_BY_ADJUSTED_SCORE` | 0 | 0.0% | `:836` |
| **TOTAL** | **339,629** | | |

**Two gates do all the work.** The portfolio-weight gates never fired once — with only 7 open
positions against a 10% single-stock cap, no symbol was ever near its limit.

### `UNINSTRUMENTED_GATE`

Three conditions in the production path have **no reason code and write nothing**:

| line | condition | what it drops |
|---|---|---|
| `india_tasks.py:888` | `actionable[: min(len, max(max_new*3,12), 24)]` | **62,087 candidate-cycles, silently** |
| `india_tasks.py:1031` | `level_pool = [s for s in level_pool if s.symbol not in vetoed_syms]` | research vetoes |
| `india_tasks.py:1090–1092` | `max_new = 0` / `min(max_new, stance["max_new_entries"])` | portfolio-brain override |

The first is the largest single silent drop in the system: 92.3% of Hub candidates are discarded
by a slice with no log line.

---

## 5. Candidate-cap analysis

```
cap = min(len(actionable), max(MAX_NEW_ENTRIES_PER_CYCLE * 3, 12), 24)
    = min(len, max(5*3, 12), 24)
    = min(len, 15, 24)  ->  15
```

| | count |
|---|---:|
| candidates before cap | 67,247 |
| candidates after cap | 5,160 |
| **removed by cap** | **62,087 (92.3%)** |

- **Ordering: deterministic and score-descending.** `actionable.sort(key=lambda s: s.confidence,
  reverse=True)` at `:884`. Not timestamp, not insertion order.
- `confidence` at that point is `compute_adjusted_score(conf, sym_w, max_w)` — the raw
  `min(100, abs(master_score))` scaled by unused position-weight headroom. With no position in
  any candidate symbol, the scaling factor was 1.0 all session, so **the ordering is pure
  `abs(master_score)`**.
- **The cap bound in 344 of 344 cycles (100%).** Median actionable per cycle: **192** against a
  cap of 15.
- **What gets removed:** everything below roughly the 92nd percentile of that cycle's actionable
  scores. Since ordering is by `abs(score)`, the survivors are the highest-|score| names — which
  is why the same handful recurs every minute (§6).

*The cap was measured, not changed.*

---

## 6. Symbol concentration

**5,160 candidate-cycles across 36 unique symbols.**

| symbol | cycles | share | mean score |
|---|---:|---:|---:|
| EPL.NS | 344 | 6.7% | +65.8 |
| KAYNES.NS | 344 | 6.7% | +71.4 |
| APOLLOTYRE.NS | 344 | 6.7% | +69.0 |
| QUADFUTURE.NS | 344 | 6.7% | +68.8 |
| ENTERO.NS | 344 | 6.7% | +73.8 |
| BALUFORGE.NS | 314 | 6.1% | +67.3 |
| JSWINFRA.NS | 314 | 6.1% | +67.3 |
| JYOTICNC.NS | 314 | 6.1% | +67.3 |
| SIGMA.NS | 307 | 5.9% | +66.9 |
| LTFOODS.NS | 280 | 5.4% | +67.6 |

- **top 10 = 63.0%** of candidate-cycles
- **top 20 = 92.5%**
- a symbol can appear at most 366 times; **max observed 344 — 94% of all cycles**

> **Does the Hub funnel produce a diversified candidate set, or collapse onto a few names?**
> **It collapses.** Five symbols are proposed in 94% of every minute of the session. Because
> the ordering is by `abs(master_score)` and Hub scores are recomputed only every ~15 minutes,
> the top of the ranking barely moves, so the same names re-enter the pool ~344 times each.

This has a direct consequence for evaluation: **the entire session's Hub output is 34–36
independent symbol bets, not 5,160.** Any statistic computed on the 5,160 without symbol
clustering would be overstated by roughly √150.

---

## 7. Score distribution by stage

| stage | n | median | p75 | p90 | p95 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| all Hub rows | 42,739 | 28.2 | 45.8 | 52.5 | 57.7 | 65.6 |
| `STRONG_BUY` | 17,386 | 47.5 | 52.3 | 58.7 | 62.7 | 68.6 |
| `BUY` | 4,789 | 34.6 | 38.2 | 39.5 | 39.7 | 40.0 |
| **shadow candidates** | **5,160** | **66.7** | **68.7** | **71.7** | **74.7** | **77.2** |

> **Does the pipeline actually prefer high-score names?**
> **Yes, emphatically.** The candidate median (66.7) sits above the **p99 of all Hub rows** (65.6)
> and above the p95 of `STRONG_BUY` (62.7). The funnel is a severe high-score filter.

Two structural notes, neither changed:

- **`BUY` is capped at 40.0** (p99 = 40.0), below the 50.0 confidence threshold — so a plain
  `BUY` label can only become a candidate through the news override, never on score alone.
- The confidence gate tests `min(100, **abs**(master_score))`, so a score of −60 passes the same
  gate as +60. On 2026-08-25 no `STRONG_SELL` existed and every `SELL` was filtered out earlier,
  so all 5,160 candidates were BUY — but the asymmetry is in the code, not the data.

---

## 8. Time-of-day distribution

| band (IST) | cycles | shadow candidates | per cycle |
|---|---:|---:|---:|
| 09:15–10:00 | 45 | 660 | 14.7 |
| 10:00–11:00 | 60 | 900 | 15.0 |
| 11:00–12:00 | 60 | 900 | 15.0 |
| 12:00–13:00 | 60 | 900 | 15.0 |
| 13:00–14:00 | 60 | 900 | 15.0 |
| 14:00–15:00 | 60 | 825 | 13.8 |
| 15:00–15:20 | 20 | 60 | 3.0 |

**The Hub candidate path would be saturated at the cap for essentially the whole session.**

This settles the question Part 8 poses: **the scarcity is not in candidates, it is in cycles.**
The funnel would supply 15 candidates every minute; BUG-2 means the loop only ran 11 times all
session. **Candidate scarcity is RULED OUT as an explanation for zero Hub trades.**

---

## 9. BUG-2 impact — measured, not fixed

| | |
|---|---:|
| expected cycles inside 09:15–15:30 IST at 60 s | ~375 |
| observed (`logs/celery-worker.log`, "Starting cycle") | **11** |
| missing | **~364 (97%)** |

Cause attribution for the 329-minute gap (09:13:21 → 14:41:58 IST):

| hypothesis | verdict |
|---|---|
| beat task expired | **EVIDENCE NOT AVAILABLE** — expired Celery tasks log nothing, raise nothing, never reach a worker |
| worker unavailable | **RULED OUT** — systemd `active`, `NRestarts=0` |
| queued behind Master workload | **STRONGLY SUPPORTED** — the worker log through the entire gap is continuous `engine.indicators` plus repeated Keras `model.compile_metrics` warnings from `ForkPoolWorker-3`: the Hub scoring 1,663 symbols |
| process blocked | **RULED OUT** — other tasks logged throughout |
| task exception | **RULED OUT for the gap** — the `UnboundLocalError` traces begin 15:00:21, *after* the gap ended |
| unknown | the precise mechanism — expiry versus queue starvation — is **NOT PROVEN** |

**Not fixed, per instruction.**

---

## 10. Hub shadow vs tactical — same session, not merged

| | Hub shadow | tactical |
|---|---:|---:|
| candidates / signals | 5,160 | 1,998 |
| **unique symbols** | **36** | **280** |
| symbols in both | 15 | |
| BUY | 5,160 | 1,849 |
| SELL | **0** | 149 |
| top-10 concentration | **63.0%** | 15.5% |
| time distribution | flat, cap-saturated | bursty, rule-driven |

Overlap: `APOLLOPIPE, ASTRAMICRO, ENTERO, JMFINANCIL, JSWINFRA, JYOTICNC, KAYNES, KFINTECH,
LAURUSLABS, LENSKART, MUTHOOTFIN, NCC, NETWEB, PAYTM, URBANCO`.

> **Are these genuinely different populations?**
> **Partly, and less than expected.** 15 of the Hub's 36 symbols — **42%** — also produced a
> tactical signal that day. They are **overlapping**, not independent. What differs is shape, not
> membership: the Hub is long-only and 4× more concentrated; tactical is broader, bidirectional
> and event-timed.

The 42% overlap matters for any future combination: treating them as independent sources would
double-count nearly half the Hub's names.

---

## 11. Historical forward-return diagnostic

**Diagnostic only. No edge is claimed. No threshold was selected.** Long, 1m candles,
symbol-clustered bootstrap (Phase 2 framework).

| population | n | symbols | +5m | +15m | +30m | +60m | +120m | EOD | 95% CI on EOD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| all Hub rows | 11,485 | 557 | +0.012 | +0.001 | −0.002 | +0.019 | +0.079 | **+0.198** | [+0.108, +0.286] |
| `STRONG_BUY` | 5,674 | 295 | +0.012 | −0.002 | −0.005 | +0.010 | +0.054 | +0.122 | [−0.008, +0.252] |
| `BUY` | 1,105 | 83 | +0.011 | +0.005 | −0.004 | +0.020 | +0.085 | +0.312 | [+0.090, +0.561] |
| **shadow candidates** | 5,107 | **34** | −0.012 | −0.031 | −0.038 | −0.046 | −0.018 | **+0.040** | **[−0.538, +0.803]** |
| shadow, first entry only | 34 | 34 | +0.145 | −0.122 | −0.172 | −0.188 | −0.047 | −0.027 | [−0.789, +0.764] |

**Matched control** — 5 random liquid symbols per candidate at the same instant:

```
shadow candidate minus control, EOD:  -0.127pp  [-0.697, +0.638]   n=5,107  symbols=34
```

Inconclusive, and the interval is 1.3 percentage points wide because there are only 34 clusters.

---

## 12. Cost-adjusted diagnostic

MIS basis, 0.2072% round-trip (Phase 2).

| population | gross EOD | cost | **net EOD** |
|---|---:|---:|---:|
| all Hub rows | +0.198% | 0.207% | **−0.009%** |
| `STRONG_BUY` | +0.122% | 0.207% | **−0.085%** |
| `BUY` | +0.312% | 0.207% | **+0.105%** |
| shadow candidates | +0.040% | 0.207% | **−0.168%** |
| shadow, first entry only | −0.027% | 0.207% | **−0.235%** |

The one positive cell — plain `BUY`, +0.105% net — is a population the funnel **excludes**: `BUY`
scores are capped at 40.0, below the 50.0 confidence threshold, so they can only enter through
the news override. It is one session, 83 symbols, and it is **not** an edge claim.

> **Is the shadow candidate population economically interesting enough to justify a future
> controlled experiment?** On this evidence, **no.** It is the worst of the five populations
> after costs bar one, and it is the one the architecture would actually trade.

---

## 13. Master score vs candidate population

> **Does candidate filtering itself create separation?**

```
all Hub rows       EOD  +0.198%
shadow candidates  EOD  +0.040%
difference              -0.159pp
```

**The filtering makes the population worse.** Selecting the highest-|score| names, capping to 15
per cycle and requiring a live price produces a subset that underperforms the unfiltered Hub
output by 0.159 percentage points.

**Classification: CANDIDATE POPULATION DIFFERENCE — negative direction, INCONCLUSIVE in
magnitude.** With 34 symbol clusters and one session, the interval cannot exclude anything. The
honest statement is: *there is no evidence the filtering adds information, and the point estimate
runs the wrong way.*

Consistent with Phase 2, where the score's top-decile-minus-bottom-decile spread across 151,263
observations and 17 sessions was −0.003 to +0.016pp. Concentrating on the top of a flat ranking
cannot manufacture separation, and it did not.

---

## 14. What this proves

1. **Candidate scarcity is not why the Hub produces no trades.** It would produce ~15 per minute,
   cap-saturated, all session. **CONFIRMED.**
2. **The candidate cap is the dominant silent filter** — 92.3% of candidates, 62,087
   candidate-cycles, discarded by a slice with no log line. **CONFIRMED.**
3. **The funnel collapses onto ~36 symbols**, five of which appear in 94% of cycles.
   **CONFIRMED.**
4. **The pipeline is a severe high-score filter** — candidate median above the p99 of all Hub
   rows. **CONFIRMED.**
5. **The filtering degrades forward return** by −0.159pp against the unfiltered Hub population.
   **CANDIDATE POPULATION DIFFERENCE**, negative, magnitude INCONCLUSIVE.
6. **BUG-1's one-line fix is sufficient** to carry the loop past `:610` — verified by syntax and
   by the fact that the reconstruction, which reproduces everything downstream of it, runs.
   **CONFIRMED.**
7. **A working-tree edit would deploy to production** via `watchmedo`. **CONFIRMED** — and it is
   why the branch lives in a worktree.

---

## 15. What this does NOT prove

- **Not** that the Hub would be profitable. It would not, on this evidence.
- **Not** that `STRONG_BUY` works. Its net EOD is −0.085%.
- **Not** that Master Intelligence should be connected. Phase 2 stands; this phase adds a second
  reason against.
- **Not** that the 36-symbol concentration is stable — it is one session (08-24 produced the same
  *shape*, but its symbol list was not compared).
- **Not** that `SHADOW_ELIGIBLE` equals what production would trade. The research veto and the
  portfolio-brain stance sit after the cap and were not reproduced; the figure is an upper bound.
- **Not** that the offline replay is byte-identical to the live loop. Three divergences are listed
  in §2.

---

## 16. Remaining unknowns

- **What the research veto and portfolio-brain stance would remove.** Both make live calls; both
  sit after the cap. **EVIDENCE NOT AVAILABLE** offline.
- **Whether the 36-symbol collapse persists across sessions.** One session measured in detail.
- **Whether expiry or starvation drops the beat task** (§9). Celery logs neither.
- **What the Hub funnel looks like when the loop actually runs** — i.e. after BUG-2 is fixed.
  The reconstruction assumes a cycle every minute; production would deduplicate against open
  positions, which the offline replay cannot model faithfully because it never opens any.
- **Whether plain `BUY` (net +0.105%) survives a second session.** One session, 83 symbols, and
  it is the population the funnel excludes — interesting only as a question, not a finding.

---

## 17. Recommended next experiment

**One measurement, and it is not about the Hub's profitability.**

> Fix **BUG-2 only** — give `india_trade_loop` its own Celery queue and worker, exactly as
> `scan_queue` was carved out in `737dd4e` — leaving BUG-1 **unfixed** so the loop still cannot
> reach the Hub query. Then count its cycles inside 09:15–15:30 IST for one session.

This isolates the two bugs. If the cycle count recovers to ~375, BUG-2 was queue starvation and
the fix is proven. If it does not, the cause is task expiry and needs a different remedy. Either
way **no Hub candidate can be created**, because BUG-1 still blocks the path — so the experiment
carries no origination risk at all.

**Only after that** is it worth deciding whether to fix BUG-1, and if so it should land with
`NEWS_ONLY_BLOCKS_HUB_ENTRIES=true` so the crash is repaired without enabling a path that two
independent phases have now found no information in.

**Not recommended:** connecting Master Intelligence; changing the cap; changing the confidence
threshold; combining Hub and tactical candidates (42% of the Hub's symbols already appear in the
tactical stream).

---

## Classification summary

| # | finding | classification |
|---|---|---|
| 1 | Order/trade/capital/paper execution reachable from the shadow run | **RULED OUT** — AST-proven |
| 2 | A working-tree edit would deploy to the live worker | **CONFIRMED** |
| 3 | Candidate scarcity explains zero Hub trades | **RULED OUT** — ~15/cycle, cap-saturated |
| 4 | The cap is the dominant silent filter (92.3%) | **CONFIRMED** |
| 5 | The cap ordering is deterministic and score-descending | **CONFIRMED** |
| 6 | The funnel collapses onto ~36 symbols | **CONFIRMED** |
| 7 | The pipeline prefers high-score names | **CONFIRMED** |
| 8 | The Hub path would be active all session | **CONFIRMED** |
| 9 | BUG-2 removes ~97% of expected cycles | **CONFIRMED** |
| 10 | BUG-2's mechanism (expiry vs starvation) | **STRONGLY SUPPORTED** (starvation), not proven |
| 11 | Hub and tactical are independent populations | **RULED OUT** — 42% symbol overlap |
| 12 | Candidate filtering improves forward return | **RULED OUT** — −0.159pp, wrong direction |
| 13 | Shadow candidates are economically interesting | **RULED OUT** on this evidence — net −0.168% |
| 14 | Three gates drop candidates with no reason code | **OBSERVABILITY GAP** |
| 15 | The 36-symbol collapse is stable across sessions | **EVIDENCE NOT AVAILABLE** |

---

**ZERO orders. ZERO trades. ZERO production changes. Master Intelligence NOT connected.
Nothing deployed. STOP.**
