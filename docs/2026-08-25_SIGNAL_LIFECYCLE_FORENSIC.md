# Signal lifecycle & counterfactual entry-time forensic report

**No production code was changed.** Sample: the 1,002 forward-resolved signals
from the three completed sessions (2026-08-20, 08-21, 08-24). Today's 1,800
signals are excluded from every completed-window result.

---

# 1. Executive verdict

> **Why is the current signal timestamp harmful?**

**It is harmful only beyond about thirty minutes, and the mechanism is the
rule's own trigger condition — not latency, not confirmation delay, not
execution.**

The system fires at the **83rd percentile of the day's range so far** (median;
p75 = 92%, p90 = 96%). It enters near the favourable extreme of the session and
the stock reverts from there over the following hour, relative to a random
minute of the same session.

| Claim | Status |
|---|---|
| Infrastructure latency causes the lateness | **DISPROVEN** — bar→row median 0.0 min, p95 1.2 min, max 2.4 min |
| The tactical path has a news/AI/Hub lifecycle to blame | **DISPROVEN** — no such stage exists in this code path |
| Signal timing is worse than random at short horizons (≤30m) | **DISPROVEN** — indistinguishable, window-matched |
| Signal timing is worse than random at 60–120m | **PROVEN** — −0.204% and −0.294%, CIs exclude zero |
| The system enters near the day's favourable extreme | **PROVEN** — median 83rd percentile of range |
| Direction carries information | **DISPROVEN** — indistinguishable from a coin flip |
| The previous −0.297% full-window figure | **WITHDRAWN — confounded** (see §2.2) |

---

# 2. Reproduction, and a confound found in my own previous experiment

## 2.1 Reproduced

The forward-resolution dataset reproduces exactly: **1,002 resolved** of 2,790;
TP 44.8% / SL 36.8% / TIME_EXIT 18.4%; median MFE +0.776%, median MAE −0.739%;
time-to-MFE 38 min, time-to-MAE 56 min; direction null +0.032%, CI
[−0.071, +0.139]; symbol null +0.113%, CI [+0.000, +0.229].

## 2.2 ⚠️ The −0.297% timestamp result was CONFOUNDED — withdrawn

The previous null drew a uniform random minute and ran both arms **to the next
session's close**. Real signals do not fire uniformly:

| minutes into session | real signals | share |
|---|---:|---:|
| 0–30 | **0** | 0.0% |
| 30–60 | **0** | 0.0% |
| 60–120 | 217 | 21.7% |
| 120–180 | 112 | 11.2% |
| 180–240 | 41 | 4.1% |
| 240–300 | 225 | 22.5% |
| 300–400 | 407 | 40.6% |

Median real signal: **286 minutes** into the session. Uniform expectation:
**~187**. **The null therefore received ~99 minutes more forward window** — a
mechanical advantage with nothing to do with information.

**The −0.297% figure measured window length, not signal quality. It is
withdrawn.**

## 2.3 Corrected: window-matched null

Both arms get an identical fixed horizon; the random timestamp is drawn only
from minutes leaving that same horizon inside the same session, so neither arm
crosses an overnight gap.

| horizon | pairs | real | null | **diff** | 95% paired CI | Verdict |
|---|---:|---:|---:|---:|---|---|
| +5m | 985 | +0.032 | +0.024 | +0.008 | [−0.020, +0.038] | **INDISTINGUISHABLE** |
| +15m | 954 | +0.052 | +0.055 | −0.003 | [−0.043, +0.037] | **INDISTINGUISHABLE** |
| +30m | 884 | +0.071 | +0.120 | −0.048 | [−0.105, +0.008] | **INDISTINGUISHABLE** |
| **+60m** | 651 | +0.001 | +0.205 | **−0.204** | **[−0.279, −0.128]** | **SIGNAL SUBTRACTS** |
| **+120m** | 395 | +0.025 | +0.319 | **−0.294** | **[−0.389, −0.203]** | **SIGNAL SUBTRACTS** |

The harm is real but it is **horizon-dependent**. Up to thirty minutes the
signal timestamp is worth neither more nor less than a coin toss on the clock.
Beyond an hour it is measurably worse. Sample shrinks with horizon because both
arms must fit the window in-session; that is stated, not hidden.

---

# 3. Signal lifecycle — most stages do not exist

`engine/tactical_executor.py` docstring, line 5:

> *"Path F originates trades from technical conditions with **no news event**."*

The module imports no news, event, classifier, agent, decision-engine or
intelligence-hub symbol. Its only intelligence import is `check_veto`
(`engine/tactical_llm_veto.py`), which is a documented stub.

| Lifecycle stage | Status |
|---|---|
| `T_event_public` | **EVIDENCE NOT AVAILABLE** — no event exists on this path |
| `T_news_detected` | **EVIDENCE NOT AVAILABLE** |
| `T_news_db_insert` | **EVIDENCE NOT AVAILABLE** |
| `T_entity_resolved` | **EVIDENCE NOT AVAILABLE** |
| `T_AI_start` / `T_AI_finish` | **EVIDENCE NOT AVAILABLE** |
| `T_master_score` | **EVIDENCE NOT AVAILABLE** |
| `T_candidate_generated` | **EVIDENCE NOT AVAILABLE** — no separate candidate row |
| `T_strategy_evaluation` | **EVIDENCE NOT AVAILABLE** — synchronous inside the scan |
| `T_entry_gate_evaluation` | **EVIDENCE NOT AVAILABLE** — same |
| **`T_signal_created`** | `tactical_signals.created_at` ✓ |
| **`T_bar`** | `tactical_signals.timestamp` ✓ |
| `T_risk_approval` | not separately stamped; `routing_outcome` only |
| `T_execution` | `executed_at` — present on **18 of 1,005** |

**Parts 2, 3A, 4, 7, 13, 14 and 15 of the brief are largely unanswerable for
this path**, because the pipeline they describe belongs to the news engine, not
to Path F. Reporting reconstructed timestamps here would be fabrication.

## 3.1 The one latency that IS measurable — and it is not the problem

`created_at − timestamp`, corrected for a timezone defect (below):

| | minutes |
|---|---:|
| median | **0.0** |
| p75 | +0.32 |
| p90 | +0.80 |
| p95 | +1.22 |
| max | **+2.4** |

**The signal row exists within 0–2.4 minutes of the bar its rule fired on.**
Infrastructure latency is ruled out as the cause of the timing harm.

## 3.2 🐛 Data defect found: `tactical_signals.timestamp` is IST, `created_at` is UTC

The raw difference is a constant **−330.0 minutes** — exactly the IST offset.
`timestamp` is stored as IST-naive while `created_at` and `candles.timestamp`
are UTC-naive.

This did not affect any experiment in this series (all replays keyed on
`created_at`), but any code comparing `tactical_signals.timestamp` against
candle timestamps would be 5½ hours out. **Reported, not fixed.**

---

# 4. Price movement already consumed at the signal

In the signal's own direction, from bars strictly **before** the signal.

| reference | n | p25 | median | p75 | p90 | p95 |
|---|---:|---:|---:|---:|---:|---:|
| session open → signal | 1,002 | −0.034 | **+1.191** | +2.694 | +5.163 | +6.403 |
| last 60 min | 1,002 | −0.054 | +0.437 | +1.018 | +2.147 | +3.101 |
| last 30 min | 1,002 | −0.095 | +0.268 | +0.712 | +1.385 | +1.840 |
| last 15 min | 1,002 | −0.107 | +0.101 | +0.365 | +0.750 | +1.154 |
| **BUY** open→signal | 862 | +0.161 | **+1.308** | +3.001 | +5.163 | +6.381 |
| **SELL** open→signal | 140 | −1.768 | +0.087 | +1.946 | +4.548 | +6.424 |

### Position in the day's range at entry

| p25 | median | p75 | p90 |
|---:|---:|---:|---:|
| 53% | **83%** | 92% | 96% |

**The median entry is at the 83rd percentile of the session's range so far**, in
the direction the signal wants. A quarter of entries are above the 92nd
percentile. This is the single clearest structural fact in the report.

**News vs non-news split: not applicable.** Every signal on this path is
non-news by construction (§3).

---

# 5. Point-of-no-return — and it is NOT what I expected

Forward outcome bucketed by prior move (session open → signal):

| prior move | n | med fwd% | mean fwd% | win% | med MFE | med MAE | hit SL% |
|---|---:|---:|---:|---:|---:|---:|---:|
| < 0 | 253 | −0.420 | −0.007 | 44.7% | +0.595 | −0.505 | 52.2% |
| 0 – 0.25 | 59 | −0.685 | −0.418 | 23.7% | +0.435 | −0.742 | 66.1% |
| **0.25 – 0.5** | 79 | **−1.073** | **−0.760** | **15.2%** | +0.151 | −1.135 | **78.5%** |
| 0.5 – 0.75 | 34 | −0.694 | −0.324 | 32.4% | +0.308 | −0.921 | 50.0% |
| 0.75 – 1.0 | 39 | −0.312 | −0.109 | 38.5% | +0.389 | −0.771 | 38.5% |
| 1.0 – 1.5 | 111 | +0.500 | +0.388 | 61.3% | +1.323 | −0.507 | 25.2% |
| 1.5 – 2.0 | 107 | +0.122 | −0.075 | 57.9% | +1.017 | −0.861 | 25.2% |
| 2.0 – 3.0 | 82 | −0.377 | −0.411 | 30.5% | +0.596 | −1.528 | 30.5% |
| **> 3.0** | 238 | **+0.846** | **+0.642** | **82.8%** | +1.511 | −0.994 | **10.1%** |

**There is no monotonic decay and therefore no point of no return.** The curve
is U-shaped and erratic:

- The **worst** bucket is a *small* prior move of 0.25–0.5% (15.2% win rate,
  78.5% hit stop).
- The **best** bucket is the *largest* prior move, >3% (82.8% win, 10.1% hit
  stop).

**This contradicts the "we enter too late" narrative** that my earlier reports
advanced. Signals entering after the biggest prior move performed best on this
sample. I am reporting the contradiction rather than reconciling it — the two
findings (§4 range position, §5 bucket curve) answer different questions and
this sample cannot resolve the tension.

Reading it as an actionable threshold would be curve-fitting on three sessions.
**Not recommended, not proposed.**

---

# 6. The +1.5% gate — not applicable to this path

`engine/entry_confirmation.py::check_price_volume_confirmation` is called from
the **news** entry path. `engine/tactical_executor.py` does not import or call
it. The 1,002 signals in this study never passed through it.

**Part 6 of the brief: EVIDENCE NOT AVAILABLE for the tactical path.** The gate
remains a live concern for the news path, where Phase 1 measured it across 14
sessions, but it cannot explain the tactical timing result.

---

# 7. Counterfactual entry times — cannot be constructed

The brief asks for entries at candidate / AI / strategy-eligibility / gate
timestamps. **None of those timestamps exist** (§3). Reconstructing them would
require re-running the rules over history, which needs the exact indicator state
at each historical minute — and the rules read a live `df` assembled inside the
scan that is not persisted.

**EVIDENCE NOT AVAILABLE.** The only two real timestamps — bar and row-written —
are 0–2.4 minutes apart, so a counterfactual between them cannot move the result.

---

# 8. Root-cause attribution

| Rank | Candidate | Verdict | Evidence |
|---|---|---|---|
| 1 | **Entry condition (decision logic)** | **CONFIRMED as the locus** | Entries sit at the 83rd percentile of the day's range (median). Timing is indistinguishable from random ≤30m and significantly worse at 60–120m, window-matched |
| 2 | **Direction rule** | **CONFIRMED uninformative** | +0.032%, CI [−0.071, +0.139] |
| 3 | Infrastructure latency | **RULED OUT** | bar→row median 0.0 min, max 2.4 min |
| 4 | News / entity / AI / Master Score | **NOT APPLICABLE** | no such stage in this path (§3) |
| 5 | Confirmation gate (+1.5%) | **NOT APPLICABLE** | not called by the tactical path (§6) |
| 6 | Execution | **RULED OUT for timing** | `executed_at` on 18 of 1,005; not the population's problem |
| 7 | Symbol selection | **MARGINAL** | +0.113%, CI [+0.000, +0.229] — lower bound on zero |
| 8 | Timezone defect on `timestamp` | **CONFIRMED bug, no impact here** | constant −330 min offset |

---

# 9. What this experiment proves

1. The tactical path has **no news, AI, entity-resolution or Master-Score stage**
   to blame — proven from the module's imports and docstring.
2. **Infrastructure latency is 0–2.4 minutes** and cannot explain the result.
3. The system **enters at the 83rd percentile of the session's range** (median).
4. **Timing is indistinguishable from random at ≤30 minutes** and **significantly
   worse at 60 and 120 minutes**, with the window-length confound removed.
5. **Direction is indistinguishable from random.**
6. My own previous **−0.297% figure was confounded** by a ~99-minute window
   advantage and is withdrawn; the corrected figures are −0.204% (60m) and
   −0.294% (120m).
7. `tactical_signals.timestamp` carries a **timezone defect**.

# 10. What this experiment does NOT prove

- **Why** the rules fire where they do in the range. The trigger conditions were
  not traced to source in this study.
- Whether the pattern holds beyond **three sessions**. It does not establish a
  regime-robust result.
- Whether the >3% bucket's strength is real or an artefact of stop width
  scaling with volatility. **Untested.**
- Anything about the **news path**, which has a genuine lifecycle and was not
  examined here.
- Any profitability claim. No costs were applied anywhere in this document.

---

# 11. The two final answers

> **If the goal is to recover the information the current signal timing is
> destroying, what exact stage must we investigate first, and what proves it?**

**The rule trigger conditions in `engine/tactical_rules.py` — specifically what
each rule requires before it fires.** Proof: every other stage is either absent
from this code path (news, entity, AI, Master Score, the +1.5% gate — §3, §6) or
measured and ruled out (infrastructure latency 0–2.4 min — §3.1; execution —
§8). What remains is the condition itself, and its measurable signature is that
it fires at the 83rd percentile of the day's range with a direction that is
statistically a coin flip.

> **Is the problem primarily LATENCY, CONFIRMATION, DECISION LOGIC, EXECUTION,
> or UNKNOWN?**

## **DECISION LOGIC.**

Latency is measured at 0–2.4 minutes and ruled out. Confirmation does not apply
— the +1.5% gate is not on this path. Execution is not implicated. What is left,
and what the evidence positively supports, is the entry condition the rules
themselves apply.

**One caveat I will not bury:** the point-of-no-return curve in §5 is U-shaped,
not decaying, and its best bucket is the latest entries. That does not fit a
simple "the trigger fires too late" story. The locus is the decision logic; the
precise defect within it is **not yet established**.

---

## Evidence appendix

| Claim | Source |
|---|---|
| No news/AI/Hub in path | `engine/tactical_executor.py` imports, docstring line 5 |
| bar→row latency | `tactical_signals.created_at − timestamp`, +330 min TZ correction, n=1,005 |
| Timezone defect | constant −330.0 min across all 1,005 |
| Range position, prior move | 1m `candles` strictly before each signal, n=1,002 |
| Signal time-of-day distribution | `created_at` − first bar of session, n=1,002 |
| Window-matched null | fixed horizon both arms, random start constrained to leave the same horizon in-session, 4,000 paired bootstrap, seed 20260825 |
| Point-of-no-return buckets | forward-resolved outcomes from `fwd.json`, n=1,002 |
| +1.5% gate not called | `engine/tactical_executor.py` contains no reference to `entry_confirmation` |

**Limitations.** Three sessions. 862 BUY vs 140 SELL — the short side is thin
everywhere. Matched-null sample shrinks with horizon (985 → 395). No costs
applied. Today's session excluded from all completed-window results.

*Systems and statistics analysis. Not investment advice. No production code was
changed.*
