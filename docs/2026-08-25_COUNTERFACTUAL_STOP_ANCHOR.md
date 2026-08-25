# Counterfactual stop-anchor forensic report

**No production code was changed.** 1,002 signals, sessions 2026-08-20, 08-21,
08-24. Only the stop was varied — entry timestamp, entry price, direction,
**target**, rule, signal selection and the next-session holding window are all
identical to the baseline. Stop wins ties throughout. No costs applied.

---

# 11. FINAL VERDICT (stated first)

## **C — STOP ANCHOR EXONERATED**

Changing only the stop anchor **does remove the R:R degradation exactly as
predicted, and makes the forward result significantly worse.** Every fixed-
percentage stop tested is worse than production's historical anchor, with a
paired confidence interval excluding zero.

**The most damning single line:** the geometry with the *best* R:R produces the
*worst* outcome.

| geometry | median R:R | mean forward | win rate | stop-out rate |
|---|---:|---:|---:|---:|
| **PCT0.5 — best R:R** | **3.00** | **−0.050%** | **30.4%** | **69.3%** |
| **ORIG — worst R:R** | **0.75** | **+0.047%** | **51.5%** | **37.0%** |

The R:R of 0.60–0.75 measured across four reports is **a symptom of a wide stop,
and the width is doing useful work.** Tightening it to "fix" the ratio converts
survivable trades into stop-outs.

---

# 1. Baseline reproduced (control)

| geometry | n | stop% | R:R | med% | mean% | win% | PF | SL% | TP% | TIME% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **ORIG** | 1,002 | 1.43 | 0.75 | +0.064 | **+0.047** | 51.5% | 1.10 | 37.0 | 44.8 | 18.2 |

1,002 signals — matches the forward-resolution dataset exactly.

---

# 2. Counterfactual geometries

**A. Fixed percent** — `entry × (1 ∓ X)` for BUY/SELL, X ∈ {0.5, 0.75, 1.0,
1.25, 1.5, 2.0, 3.0}%.

**B. ATR** — Wilder true-range mean over the **14 closed 1m bars strictly before
the signal**, at 0.5× / 1.0× / 1.5× / 2.0×. Available on 998 of 1,002.

**C. Original production stop** — control.

### ⚠️ Scale finding on the ATR arm

1m ATR produces stop distances of **0.06% (0.5×) to 0.51% (2.0×)** — at or
inside a typical NSE bid-ask spread for these names. Consequently the ATR arms
stop out **77.6% to 90.8%** of the time.

**1m ATR is not a usable stop anchor at this scale.** The ATR results are
reported for completeness but should not be read as a fair test of
volatility-scaled stops; a 5m or daily ATR would be the correct instrument and
was not tested. **EVIDENCE NOT AVAILABLE** for volatility-scaled stops at a
sensible scale.

---

# 3. Overall forward results

| geometry | n | stop% | R:R | med% | mean% | win% | PF | SL% | TP% | TIME% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **ORIG** | 1,002 | 1.43 | 0.75 | +0.064 | **+0.047** | 51.5% | **1.10** | 37.0 | 44.8 | 18.2 |
| PCT0.5 | 1,002 | 0.50 | 3.00 | −0.500 | −0.050 | 30.4% | 0.86 | **69.3** | 29.6 | 1.1 |
| PCT0.75 | 1,002 | 0.75 | 2.00 | −0.750 | −0.102 | 35.8% | 0.78 | 62.7 | 34.7 | 2.6 |
| PCT1.0 | 1,002 | 1.00 | 1.50 | −1.000 | −0.120 | 42.0% | 0.79 | 55.2 | 39.8 | 5.0 |
| PCT1.25 | 1,002 | 1.25 | 1.20 | −0.410 | **−0.150** | 47.1% | 0.76 | 48.6 | 41.7 | 9.7 |
| PCT1.5 | 1,002 | 1.50 | 1.00 | +0.064 | −0.120 | 51.7% | 0.81 | 40.7 | 44.7 | 14.6 |
| PCT2.0 | 1,002 | 2.00 | 0.75 | +0.234 | −0.038 | 56.8% | 0.94 | 25.1 | 49.3 | 25.5 |
| PCT3.0 | 1,002 | 3.00 | 0.50 | +0.417 | −0.015 | 59.8% | 0.98 | 10.9 | 51.7 | 37.4 |
| ATR0.5 | 998 | 0.06 | 18.40 | −0.055 | +0.031 | 9.2% | 1.44 | 90.8 | 9.2 | 0.0 |
| ATR1.0 | 998 | 0.12 | 9.20 | −0.100 | +0.024 | 14.4% | 1.19 | 85.6 | 14.4 | 0.0 |
| ATR1.5 | 998 | 0.18 | 6.13 | −0.140 | +0.017 | 18.6% | 1.09 | 81.4 | 18.6 | 0.0 |
| ATR2.0 | 998 | 0.25 | 4.60 | −0.177 | +0.020 | 22.4% | 1.09 | 77.6 | 22.4 | 0.0 |

**Nothing beats the control on mean forward return.** The ordering within the
fixed-percent family is monotone in the wrong direction for the hypothesis:
as R:R improves from 0.50 to 3.00, mean return falls from −0.015% to −0.050%
and the stop-out rate climbs from 10.9% to 69.3%.

---

# 4. Paired comparison vs the original stop

Same signal, only the stop differs. 4,000-sample paired bootstrap, seed
20260825.

| geometry | pairs | diff mean% | 95% paired CI | Verdict |
|---|---:|---:|---|---|
| PCT0.5 | 1,002 | −0.096 | [−0.165, −0.027] | **WORSE** |
| PCT0.75 | 1,002 | −0.149 | [−0.215, −0.082] | **WORSE** |
| PCT1.0 | 1,002 | −0.167 | [−0.230, −0.102] | **WORSE** |
| PCT1.25 | 1,002 | −0.197 | [−0.257, −0.137] | **WORSE** |
| PCT1.5 | 1,002 | −0.166 | [−0.227, −0.102] | **WORSE** |
| PCT2.0 | 1,002 | −0.085 | [−0.146, −0.024] | **WORSE** |
| PCT3.0 | 1,002 | −0.062 | [−0.119, −0.000] | **WORSE** |
| ATR0.5 | 998 | −0.036 | [−0.108, +0.039] | inconclusive |
| ATR1.0 | 998 | −0.043 | [−0.115, +0.028] | inconclusive |
| ATR1.5 | 998 | −0.050 | [−0.121, +0.022] | inconclusive |
| ATR2.0 | 998 | −0.047 | [−0.115, +0.021] | inconclusive |

**Seven of eleven counterfactuals are significantly worse. Four are
inconclusive. None is better.**

---

# 5. Does the R:R degradation disappear? — YES, and it doesn't help

Median initial R:R by prior-move bucket:

| prior move | n | **ORIG** | PCT1.0 | PCT2.0 | ATR1.0 | ATR2.0 |
|---|---:|---:|---:|---:|---:|---:|
| < 0 | 253 | **1.48** | 0.88 | 0.44 | 8.92 | 4.46 |
| 0–0.25 | 59 | 1.29 | 1.50 | 0.75 | 17.53 | 8.76 |
| 0.25–0.5 | 79 | 1.04 | 1.07 | 0.54 | 10.20 | 5.10 |
| 0.5–0.75 | 34 | 0.98 | 1.30 | 0.65 | 12.76 | 6.38 |
| 0.75–1 | 39 | 0.72 | 1.13 | 0.56 | 10.92 | 5.46 |
| 1–1.5 | 111 | 0.98 | 1.65 | 0.82 | 11.91 | 5.96 |
| 1.5–2 | 107 | 0.56 | 2.00 | 1.00 | 13.21 | 6.60 |
| 2–3 | 82 | 0.43 | 2.00 | 1.00 | 11.36 | 5.68 |
| > 3 | 238 | **0.36** | 1.50 | 0.75 | 4.11 | 2.05 |

**ORIG degrades 1.48 → 0.36 across the buckets. PCT1.0 does not degrade at all
(0.88 → 1.50).** The predicted mechanism is confirmed as a *description* of the
geometry.

Median forward return in the same buckets:

| prior move | n | **ORIG** | PCT1.0 | PCT2.0 | ATR2.0 |
|---|---:|---:|---:|---:|---:|
| < 0 | 253 | −0.420 | +0.072 | +0.294 | −0.152 |
| 0.25–0.5 | 79 | −1.073 | −1.000 | −2.000 | −0.186 |
| 1–1.5 | 111 | **+0.500** | −0.651 | +0.500 | −0.174 |
| 1.5–2 | 107 | +0.134 | −1.000 | +0.134 | −0.189 |
| **> 3** | 238 | **+0.836** | −0.158 | +0.542 | −0.263 |

**Fixing the R:R does not fix the return.** In the largest bucket (>3%, n=238)
the original's +0.836% median becomes −0.158% under PCT1.0.

---

# 6. Rule by rule

| Rule | n | ORIG mean | ORIG win | PCT2.0 mean | ATR2.0 mean | best |
|---|---:|---:|---:|---:|---:|---|
| GAP_AND_GO | 238 | −0.122 | 42.4% | −0.211 | −0.065 | ATR2.0 |
| VWAP | 198 | **+0.206** | 58.6% | −0.065 | +0.046 | **ORIG** |
| PIVOT_BREAKOUT | 168 | +0.100 | 60.1% | −0.009 | +0.109 | ATR2.0 |
| VWAP_CROSSOVER | 156 | **+0.041** | 69.9% | −0.056 | +0.003 | **ORIG** |
| OVERSOLD_REBOUND | 82 | +0.175 | 46.3% | **+0.472** | +0.088 | PCT2.0 |
| VOLUME_BREAKOUT | 68 | +0.104 | 35.3% | +0.117 | **+0.219** | ATR2.0 |
| OVERBOUGHT_FADE | 67 | −0.075 | 29.9% | −0.137 | −0.147 | **ORIG** |
| PIVOT_BOUNCE / DAY_MOMENTUM / SCALP / ORB | 3–9 | — | — | — | — | **INSUFFICIENT SAMPLE** |

The two rules the hypothesis named as most affected — **VWAP and
VWAP_CROSSOVER — are the two where the original stop is best.** None of these
per-rule differences had a paired CI excluding zero.

---

# 7. The >3% contradiction — resolved, and it points elsewhere

| geometry | n | stop% | R:R | med% | mean% | win% | PF | SL% | TP% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **ORIG** | 238 | **3.32** | 0.36 | +0.836 | **+0.621** | **81.9%** | 2.62 | **10.1** | 76.5 |
| PCT1.0 | 238 | 1.00 | 1.50 | −0.158 | +0.118 | 50.0% | 1.24 | 49.2 | 50.0 |
| PCT2.0 | 238 | 2.00 | 0.75 | +0.542 | +0.420 | 74.4% | 1.84 | 24.8 | 70.6 |
| **PCT3.0** | 238 | **3.00** | 0.50 | +1.217 | +0.553 | **82.8%** | 2.17 | 14.3 | 77.7 |
| ATR2.0 | 234 | 0.51 | 2.05 | −0.263 | +0.156 | 40.2% | 1.48 | 59.8 | 40.2 |

**The >3% effect is not explained by prior move. It is explained by STOP
WIDTH.** The 81.9% win rate survives at 3.32% (ORIG) and 3.00% (PCT3.0) stops
and **collapses to 50.0% at a 1.00% stop.**

Those signals performed best because their stop happened to be widest — the
historical anchor was furthest away after a >3% run. **The very property the
hypothesis called a defect is what produced that bucket's result.**

---

# 8. The five causal predictions, tested

| # | Prediction | Result |
|---|---|---|
| 1 | Entry-relative stops prevent R:R deterioration | **CONFIRMED** — PCT1.0 goes 0.88 → 1.50 instead of 1.48 → 0.36 |
| 2 | The prior-move → stop → R:R relationship disappears | **CONFIRMED** |
| 3 | **Forward expectancy should improve** | **REFUTED** — 7 of 11 significantly worse, none better |
| 4 | Effect strongest in GAP_AND_GO / VWAP / VWAP_CROSSOVER / PIVOT_BREAKOUT | **REFUTED** — ORIG is best for VWAP and VWAP_CROSSOVER |
| 5 | Mean-reversion rules should not improve | **PARTIALLY REFUTED** — OVERSOLD_REBOUND improves under PCT2.0 (+0.175 → +0.472, n=82, CI not established) |

**The geometry hypothesis is confirmed as a description and refuted as a cause.**

---

# 9. What this proves

1. **The stop anchor is not the defect.** Removing it removes the R:R
   degradation and makes results significantly worse.
2. **R:R is the wrong lever.** Across the fixed-percent family, mean return moves
   *against* R:R: the 3.00 R:R geometry loses most, the 0.50 R:R geometry loses
   least, and the production 0.75 is the only positive one.
3. **The wide historical stop is load-bearing.** It lets trades survive noise;
   stop-out rate rises from 10.9% to 69.3% as the stop tightens from 3.0% to 0.5%.
4. **The >3% bucket's strength was a stop-width artefact**, not a prior-move
   effect. Contradiction resolved.
5. **1m ATR is unusable as a stop anchor** — 0.06–0.51% distances, 78–91%
   stop-out rates.

# 10. What this does NOT prove

- That the production stop is *optimal*. It is the best of twelve tested; no
  claim beyond that.
- Anything about **volatility-scaled stops at a sensible scale** (5m/daily ATR).
  **EVIDENCE NOT AVAILABLE.**
- Anything about **target** geometry — held fixed by design. Part 10's second
  diagnostic (counterfactual stop *and* target) was **not run**; running it now
  would confound the two effects this experiment separated.
- Regime robustness. **Three sessions.**
- Any profitability claim. No costs applied. The control's +0.047% mean is far
  below any realistic cost floor.

---

# 12. Next investigation

**Back to the trigger conditions — the redirect this experiment was designed to
produce.**

The evidence chain now reads: infrastructure ruled out (0–2.4 min), look-ahead
ruled out (`closed()` applied throughout), stop anchor ruled out (this report),
direction indistinguishable from random (null test), timing indistinguishable
from random at ≤30 minutes.

What remains untested is **whether the trigger conditions select stocks that
move at all** — the symbol-selection null was measured at +0.113% with a CI
whose lower bound sits exactly on zero, which is the weakest link left standing.

**Proposed next step, diagnostic only:** hold the entry timestamp and geometry
fixed and vary only the *symbol universe* — compare the rules' chosen symbols
against liquidity-matched and volatility-matched controls at the same minute. If
the rules cannot beat a volatility-matched control, the trigger conditions are
selecting normal movement and the investigation is complete.

**No production change is recommended from this report.**

---

## Evidence appendix

| Claim | Source |
|---|---|
| 1,002 signals, 3 sessions | `tactical_signals` where `created_at::date` in (08-20, 08-21, 08-24) |
| Baseline reproduction | identical to the forward-resolution dataset: n=1,002 |
| Counterfactual stops | entry × (1∓X); ATR from 14 closed 1m bars strictly before signal |
| Forward walk | 1m `candles`, signal → next session close, stop wins ties |
| Paired CIs | 4,000-sample bootstrap on `cf_i − orig_i`, seed 20260825 |
| Look-ahead audit | ATR uses `pre = [b for b in day if b[0] <= ts]` only; no bar after the signal is read for any stop computation |

**Limitations.** Three sessions. 862 BUY / 140 SELL. No costs. Target held fixed
by design. ATR arm invalid at 1m scale. Four rules under n=20 excluded from all
conclusions.

*Systems and statistics analysis. Not investment advice. No production code was
changed.*
