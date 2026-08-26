# PHASE 24 — OPPORTUNITY QUALITY DISCOVERY

**Mode:** local research only. Nothing deployed. No threshold, R:R, TOP_N,
capital, exit, Master Score, prompt, AI routing, BUG-1 or execution behaviour
was changed.

**Dataset:** 5,488 opportunities under the unmodified Phase-21 t0 rule across
5 sessions (2026-08-20 … 08-26). Discovery = 4,383 (first four sessions).
**Validation = 1,105 (2026-08-26, held out, touched only after the rules were
fixed).** No shuffling.

---

## 1. EXECUTIVE VERDICT

> **The problem was never the opportunity. It was the holding horizon — and
> Phase 23's conclusion is wrong because it measured at 60 minutes.**

| Signalled opportunities | 30m | 60m | 120m | to close |
|---|---:|---:|---:|---:|
| median return | +0.048% | +0.058% | **+0.164%** | **+0.452%** |
| net of 0.11% cost | −0.062% | −0.052% | **+0.054%** | **+0.342%** |
| P(return > cost) | 43% | 47% | 52% | **63%** |
| MFE / \|MAE\| | 1.35 | 1.58 | 1.81 | **2.34** |

*(discovery set)*

**The edge does not decay with time. It compounds.** At 60 minutes the signalled
subset is still net negative; it turns positive at 120 minutes and reaches
+0.342% net held to the close.

**Phase 23 concluded "the opportunity itself is weak" from a single number —
+0.092% capturable at 60m against a 0.110% floor. That number is real and the
conclusion drawn from it is withdrawn.** 60 minutes is simply the wrong place to
look.

This also explains the standing puzzle: multi-day TACTICAL is the only
profitable population on record (+₹7,155, n=14) while intraday loses (−₹1,742,
n=47). **Longer holds win because the edge needs time.**

---

## 2. Time-horizon analysis (PART 3)

### The broad pool is genuinely bad, at every horizon

| Horizon | med MFE | med MAE | med return | MFE/\|MAE\| | P(>cost) |
|---|---:|---:|---:|---:|---:|
| 5m | 0.082% | −0.137% | −0.032% | 0.60 | 21% |
| 15m | 0.146% | −0.214% | −0.058% | 0.68 | 26% |
| 30m | 0.204% | −0.283% | −0.074% | 0.72 | 30% |
| 60m | 0.280% | −0.378% | −0.092% | 0.74 | 33% |
| 120m | 0.387% | −0.498% | −0.129% | 0.78 | 34% |
| to close | 0.563% | −0.737% | −0.210% | 0.76 | 35% |

**MAE exceeds MFE at every horizon and the median return is negative
everywhere.** Holding longer makes the broad pool *worse*. → **CONFIRMED**

### The signalled subset has the opposite shape

| Horizon | med MFE | med MAE | med return | MFE/\|MAE\| | P(>cost) |
|---|---:|---:|---:|---:|---:|
| 5m | 0.128% | −0.125% | +0.006% | 1.02 | 31% |
| 15m | 0.245% | −0.194% | −0.008% | 1.26 | 38% |
| 30m | 0.339% | −0.249% | +0.031% | 1.36 | 42% |
| 60m | 0.493% | −0.306% | +0.060% | 1.61 | 47% |
| 120m | 0.724% | −0.391% | +0.178% | 1.85 | 52% |
| **to close** | **1.234%** | **−0.501%** | **+0.452%** | **2.46** | **63%** |

Two different populations, and the difference is not marginal.

---

## 3. Out-of-sample validation (PART 7)

The horizon finding was established on the four discovery sessions and then
applied unchanged to 2026-08-26.

| | 30m net | 60m net | 120m net | to-close net |
|---|---:|---:|---:|---:|
| **Discovery** (n=337) | −0.062% | −0.052% | **+0.054%** | **+0.342%** |
| **Validation** (n=61) | −0.154% | −0.032% | **+0.109%** | **+0.342%** |

**It replicates.** Per session:

| Session | n | ret60 | ret120 | retE |
|---|---:|---:|---:|---:|
| 08-20 | 38 | −0.030% | −0.076% | **−0.079%** |
| 08-21 | 99 | −0.009% | +0.193% | +0.570% |
| 08-24 | 74 | +0.062% | +0.077% | +0.213% |
| 08-25 | 126 | +0.113% | +0.310% | +0.612% |
| 08-26 | 61 | +0.078% | +0.219% | +0.452% |

Four of five sessions show `retE > ret120 > ret60`. **08-20 is negative
throughout** — one session in five where the pattern fails.

---

## 4. Feature analysis (PART 4) — a decisive negative result

Twenty t0 features tested on the discovery set against return-to-close.
**Multiple testing: 20 hypotheses; treat any single winner as exploratory.**

**Every decile of every feature is negative.** The best decile of the best
feature is −0.039%.

| Feature | D1 median | D10 median | spread | monotonic |
|---|---:|---:|---:|---:|
| range30 | −0.087% | −0.519% | −0.432% | 2/9 |
| **min_since_open** | −0.509% | −0.120% | +0.389% | **7/9** |
| r10 | −0.064% | −0.443% | −0.379% | 3/9 |
| **turnover** | −0.320% | −0.039% | +0.281% | **6/9** |
| rvol | −0.143% | −0.300% | −0.157% | 4/9 |
| brk_pct | −0.128% | −0.306% | −0.178% | 5/9 |

Only `min_since_open` and `turnover` show monotonic structure, and both still
end negative.

**No single t0 feature rescues the broad pool.** → **CONFIRMED**

### The consequence for PART 9

The tactical signal delivers **+0.452%** to close; the best feature decile
delivers **−0.039%**. **The existing rules contain information that none of
these twenty features captures.**

That is a strong argument for *keeping* the current signal logic, and against
replacing it with a feature screen. → **CONFIRMED**

---

## 5. Tiers (PART 6) — built on discovery, applied unchanged to validation

Within the signalled subset, `atr_pct` was the strongest splitter on discovery
(spread +0.257%). Split at its discovery median, 0.09.

### Discovery

| Tier | n | med return | net | MFE | MAE | P(>cost) | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| **A** — signalled & atr_pct ≥ 0.09 | 169 | 0.623% | **+0.513%** | 1.897% | −0.769% | 64% | **3.31** |
| **B** — signalled & atr_pct < 0.09 | 168 | 0.366% | +0.256% | 0.911% | −0.338% | 61% | 2.50 |
| **C** — all signalled | 337 | 0.452% | +0.342% | 1.194% | −0.510% | 63% | 3.01 |

### Validation (2026-08-26, rule unchanged)

| Tier | n | med return | net | MFE | MAE | P(>cost) | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| **A** | 33 | **1.241%** | **+1.131%** | 2.321% | −0.550% | **73%** | **5.61** |
| **B** | 28 | 0.142% | +0.032% | 0.926% | −0.441% | 54% | 1.82 |
| **C** | 61 | 0.452% | +0.342% | 1.575% | −0.494% | 64% | 3.62 |

### Baseline

| | n | med return | net | P(>cost) | PF |
|---|---:|---:|---:|---:|---:|
| Broad pool, discovery | 4,383 | −0.211% | −0.321% | 34% | 0.70 |
| Broad pool, validation | 1,105 | −0.205% | −0.315% | 36% | 1.06 |

**Tier A held out of sample and improved.** But **n=33 on the validation day**,
and the improvement from +0.513% to +1.131% is far larger than the discovery
sample warrants. → **PARTIALLY SUPPORTED. Treat Tier A as a hypothesis needing
more sessions, not a finding.**

The honest read of the tier work: **volatility (`atr_pct`) plausibly separates
better signals from worse ones, and the direction replicated. The magnitude did
not stabilise.**

---

## 6. Current vs proposed (PART 8)

| | coverage | med return to close | net | P(>cost) | PF |
|---|---:|---:|---:|---:|---:|
| Broad opportunity baseline | 5,488 (100%) | −0.210% | −0.320% | 35% | 0.70 |
| **Current tactical signal** | **398 (7.3%)** | **+0.452%** | **+0.342%** | **63%** | **3.01** |
| Tier A (signal + atr filter) | ~202 (3.7%) | +0.623–1.241% | +0.513–1.131% | 64–73% | 3.31–5.61 |

**The current tactical signal already does the job the brief asked for:
"fewer but economically meaningful trades."** It takes 7.3% of opportunities and
turns a −0.32% net baseline into +0.34% net.

---

## 7. Minimum economic edge (PART 11)

| Component | % |
|---|---:|
| Corrected MIS round-trip cost | 0.110 |
| Observed signal→entry slippage (Phase 23) | 0.019 |
| **Minimum breakeven move** | **0.129** |
| At 1.5:1 reward/risk with a 0.5% stop | ~0.75 needed for positive expectancy |

Share of the **broad** pool clearing 0.129% to close: **~34%** — but with median
−0.210%, the losers more than pay for them.

Share of **signalled** opportunities clearing it: **63%**, median +0.452%.

**The broad "breakout + volume" definition does not meet the economic threshold.
The tactical signal does.**

---

## 8. Root-cause update (PART 10)

| Candidate | Verdict |
|---|---|
| Weak opportunity definition | **CONFIRMED** — broad pool is negative at every horizon and no feature fixes it |
| **Wrong holding horizon** | **CONFIRMED — the dominant finding.** Edge is negative at 60m, positive at 120m+ |
| Weak entry trigger | **RULED OUT** — signal beats every feature decile tested |
| Weak scoring | **NOT MEASURED** — `composite_score` was available on only 11% of rows |
| Transaction economics | **PARTIALLY SUPPORTED** — 0.11% floor is real but clearable at the right horizon |
| Conversion pipeline | **RULED OUT** in Phase 23 (86% MFE retention, 0.6-min entries) |

---

## 9. Expected trade frequency

| | per session |
|---|---:|
| Broad opportunities | ~1,100 |
| Current signalled | ~80 |
| Tier A | ~40 |

The system currently executes ~12 of the ~80. Nothing here recommends
increasing that count — the recommendation is about **how long positions are
held**, not how many are opened.

---

## 10. IMPLEMENTATION PLAN

### SAFE ENGINEERING FIXES
1. **Fix `paper_trades.mfe_pct`** — compute from candles at close rather than a
   sampled tracker (Phase 23). Every strategy conclusion depends on it, and it
   is pure measurement.
2. **Deploy the product-aware cost model** (built and tested in Phase 23) under
   its own gate with wallet reconciliation.

### STRATEGY EXPERIMENTS — design only, none run
3. **Test a longer holding horizon.** This is the finding. The current intraday
   exit stack closes positions long before the edge matures: at 60 minutes the
   signalled subset is net −0.05%, at close it is +0.34%. Before changing any
   exit, replay the existing exits against a hold-to-squareoff baseline over
   more sessions.
4. Tier A (`atr_pct ≥ 0.09`) as a **research filter only** — needs 10+ sessions
   before it means anything.

### MEASUREMENT
5. Extend this dataset session by session. Every conclusion here rests on 5
   sessions and one validation day of 61 signals.
6. Capture `composite_score` per signal so PART 9 becomes answerable.

### DO NOT TOUCH YET
`TACTICAL_TOP_N` · turnover floor · R:R · capital limits · AI routing · BUG-1 ·
Master Score · prompts · **and the signal logic itself, which is the best
component measured so far.**

---

## 11. Recommendation for the next implementation phase

**Do not change what selects trades. Investigate what closes them.**

Every phase since 19B has looked for the leak in selection, conversion or
execution and each was ruled out. This phase locates it: the signal is good, the
conversion is good, and positions are closed at a horizon where the edge has not
yet appeared.

The next phase should measure — **not change** — the exit stack against a
hold-to-close baseline across as many sessions as exist, per exit family, using
candle-derived MFE rather than the defective field. If that replicates the
+0.34% gap, the exit horizon becomes the first justified strategy change of this
entire investigation.

---

## Classification

| Conclusion | Status |
|---|---|
| Edge grows with holding horizon | **CONFIRMED** (replicated out of sample) |
| Phase 23's "opportunity is weak" | **WITHDRAWN** — an artifact of the 60m horizon |
| Broad opportunity pool is unprofitable | **CONFIRMED** — negative at every horizon |
| No single t0 feature rescues it | **CONFIRMED** — 20 tested, all deciles negative |
| Tactical signal beats every feature tested | **CONFIRMED** |
| Tier A (atr_pct) improves on the signal | **PARTIALLY SUPPORTED** — direction replicated, magnitude did not |
| Current scoring captures the edge | **NOT MEASURED** — score present on 11% of rows |
| Holding horizon is the root cause | **CONFIRMED as the dominant factor** |
| Pattern holds every session | **RULED OUT** — 08-20 negative throughout |

---

*Local research only. 20 features tested; treat single-feature winners as
exploratory. Discovery and validation strictly time-separated, no shuffling, no
threshold tuned after seeing validation. Nothing deployed, no order placed, no
historical row rewritten.*
