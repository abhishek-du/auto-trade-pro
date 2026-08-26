# Stage 3 — is the negative result real, or an artefact of my own method?

**Objective:** distinguish BUG / BAD STRATEGY / BAD EXIT GEOMETRY / BAD COST
ASSUMPTION / NOISE. This audit attacks the previous audit.

**Outcome:** I found **two real defects in my own methodology**. Correcting the
first strengthened the negative result. The second means **all per-strategy
numbers in the previous audit — including the "VOLUME_BREAKOUT is breakeven"
claim — are not trustworthy and are withdrawn.**

The aggregate conclusion survives, but it now rests on a different and stronger
piece of evidence than before.

---

# The ten answers

| # | Question | Answer |
|---|---|---|
| 1 | **Replay validity** | **PARTIALLY.** Entry/SL/target are production-faithful (proven). Two method defects found: R:R gate ordering (corrected, conclusion strengthened) and unresolved-trade marking (**invalidates per-strategy numbers**). |
| 2 | **Entry edge** | **CONDITIONAL — and mostly NO.** Symbol selection beats random (+0.090%, CI [+0.050,+0.133]). **Direction is indistinguishable from a coin flip.** **Timing is worse than random by −0.201%.** |
| 3 | **Exit geometry** | **NOT the primary problem.** Nine exit models tested; all net-negative. Best (1.5R) improves on production by only +0.034% against a 0.222% cost. |
| 4 | **Cost sensitivity** | Crosses to negative at **≈0.02–0.05%**. Brokerage alone is ~0.03%. There is no realistic cost at which this is positive. |
| 5 | **Regime edge** | **UNPROVEN.** No segment's 95% CI clears the cost floor, and the segmentation rests on the same unstable marking. |
| 6 | **Random baseline** | **Direction: NO better than random. Timing: WORSE than random.** |
| 7 | **Capital gates** | **Protective** at signal level; portfolio-level simulation NOT RUN — see limitations. |
| 8 | **News causality** | **EVIDENCE NOT AVAILABLE** — not investigated at this stage. |
| 9 | **Strategy status** | See §7. Most verdicts are **INSUFFICIENT**, not DISABLE, because of defect #2. |
| 10 | **Next experiment** | See §9 — one experiment, defined to be marking-invariant. |

---

# 1. PHASE A — replay validity

## 1.1 Entry, stop and target are production-faithful — PROVEN

Joined all 23 executed tactical signals over 30 days to the trades they became
(`tactical_signals` → `paper_trades`, matched on symbol within ±2 min):

**23 of 23 have identical `stop_loss` and identical `take_profit`.** Entry
differs by 0.02–0.05%, always adverse — production's simulated 2–8 bps entry
slippage.

| Symbol | sig entry | trade entry | sig SL | trade SL | sig TP | trade TP |
|---|---:|---:|---:|---:|---:|---:|
| FINEORG.NS | 5284.50 | 5285.48 | 5170.77 | 5170.77 | 5499.67 | 5499.67 |
| DEVYANI.NS | 149.09 | 149.12 | 146.49 | 146.49 | 152.21 | 152.21 |
| ADANIENT.NS | 3031.90 | 3031.38 | 3047.06 | 3047.06 | 2980.90 | 2980.90 |
| … | | | | | | (23/23 match) |

`tactical_executor.py:354` sets `take_profit=signal.target`; nothing downstream
overrides it. **There is a single target — no T1/T2 ambiguity in this path.**
CONFIRMED: the replay used the levels production would have used.

## 1.2 DEFECT #1 — gate ordering. FOUND, CORRECTED, conclusion strengthened

`engine/risk_manager.py`: the **cash buffer check is at lines 270–295; the R:R
check is at line 467.** The cash gate fires first, so cash-blocked signals never
reach the R:R floor (`TACTICAL_MIN_RISK_REWARD = 1.2`, `utils/config.py:448`).

The previous audit replayed all 2,693 signals as though capital were the only
obstacle. **It was not.** Production would additionally have refused every
signal with R:R < 1.2, and the population median R:R is 0.60.

Corrected population:

| Population | n | med% | mean% | win% | net% |
|---|---:|---:|---:|---:|---:|
| All 2,693 (previous audit) | 2,693 | 0.000 | +0.022 | 49.0% | −0.200 |
| Would FAIL R:R < 1.2 | 1,974 | +0.047 | +0.024 | **52.7%** | −0.198 |
| **Would PASS R:R ≥ 1.2** | **719** | **−0.143** | **+0.018** | **38.8%** | **−0.204** |

**Only 26.7% of signals were ever tradable.** And the tradable subset performs
*worse*: 38.8% win rate against 52.7% for the rejected ones.

**The R:R gate is not selecting quality — it is selecting wider targets, which
are less often reached.** A signal passes the gate by having a distant target,
and a distant target is exactly what does not get hit.

## 1.3 DEFECT #2 — unresolved-trade marking. FOUND, NOT correctable at this stage

**59.7% of replayed signals never touch stop or target within the data window.**
They are marked to the last available bar. That choice dominates the result.

Two of my own runs on the identical strategy disagree:

```
replay_hist.py  VOLUME_BREAKOUT  n=220  mean +0.268%
exits.py        VOLUME_BREAKOUT  n=229  mean +0.400%
```

Reconciliation: of 150 rows matchable across both runs, **87 differ — every one
with `outcome=OPEN`.** The two scripts pulled slightly different candle windows,
so "last bar" differs and unresolved trades mark differently. A further 76 rows
appear only in the second run, averaging **+0.800%**, which is what inflates it.

**Consequence:** every per-strategy gross/net figure in the previous audit is
unstable at the ±0.13% level — larger than the entire claimed edge. Specifically:

> **The previous audit's claim that `VOLUME_BREAKOUT` is "net +0.046%,
> breakeven" is WITHDRAWN.** The same data under a slightly different window
> gives +0.178% net. Neither number is trustworthy.

## 1.4 Cost model — decomposed and swept

Components, and whether each applies to NSE cash equity:

| Component | Rate | Applies? |
|---|---:|---|
| Brokerage (₹20 flat / leg, ₹50k ticket) | 0.030% | YES both legs |
| STT | 0.025% | YES delivery sell; 0.025% intraday sell |
| Exchange transaction | 0.007% | YES |
| GST 18% on (brokerage + txn) | 0.007% | YES |
| SEBI turnover + stamp | 0.003% | YES |
| **Statutory subtotal** | **0.072%** | — |
| Spread + slippage | 0.150% | **assumption, not a fee** |
| **Total assumed** | **0.222%** | — |

Sweep on the R:R-eligible population (n=719):

| assumed cost | net mean | verdict |
|---:|---:|---|
| **0.000%** | **+0.018%** | positive |
| 0.050% | −0.032% | negative |
| 0.100% | −0.082% | negative |
| 0.150% | −0.132% | negative |
| 0.200% | −0.182% | negative |
| 0.222% | −0.204% | negative |
| 0.300% | −0.282% | negative |

**Break-even cost is ≈0.018%.** Even at *zero* slippage the statutory floor
alone (0.072%) is four times the gross edge. **The negative result is not a
cost-assumption artefact.**

---

# 2. PHASE D — is it the entry or the exit?

Same entries, nine exit models. **Grid fixed before any result was seen; no
parameter was tuned.** n=2,730.

| Exit model | med% | mean% | win% | **net%** | PF |
|---|---:|---:|---:|---:|---:|
| production SL/target | +0.037 | +0.052 | 52.2% | −0.170 | 1.17 |
| fixed +0.25% TP | +0.250 | −0.011 | 70.4% | −0.233 | 0.94 |
| fixed +0.50% TP | +0.500 | +0.027 | 59.2% | −0.195 | 1.11 |
| fixed +1.00% TP | +0.005 | +0.049 | 50.0% | −0.173 | 1.16 |
| 1R target | 0.000 | +0.078 | 49.3% | −0.144 | 1.24 |
| **1.5R target (best)** | −0.018 | **+0.086** | 47.7% | **−0.136** | 1.27 |
| 2R target | −0.029 | +0.078 | 47.0% | −0.144 | 1.24 |
| time exit 15 min | −0.017 | −0.003 | 46.3% | −0.225 | 0.98 |
| time exit 60 min | −0.041 | −0.001 | 45.3% | −0.223 | 1.00 |

**Every one is net-negative.** The best available exit improves on production by
**+0.034%** — one seventh of the cost floor.

### Why: the excursions are symmetric

```
median MFE  +0.540%
median MAE  -0.529%

MFE >= 0.10% : 84.0%      MFE >= 1.00% : 29.3%
MFE >= 0.20% : 73.7%      MFE >= 1.50% : 17.4%
MFE >= 0.30% : 65.0%      MFE >= 2.00% : 10.7%
MFE >= 0.50% : 51.6%
```

The entries **do** move — half touch +0.50% at some point. But they move
against you by the same amount. A stop tight enough to protect is hit; a stop
wide enough to survive destroys the R:R. **That symmetry is the definition of no
directional edge, and no exit rule can manufacture one.**

**VERDICT: the problem is entry quality, not exit construction.**

---

# 3. PHASE E — does the signal beat random?

Three nulls, each holding trade **geometry** constant and destroying only the
information the signal claims to carry. 2,000-sample bootstrap CIs.

**This test is marking-invariant** — real and null are marked identically — so
it is unaffected by defect #2. It is the strongest evidence in this audit.

| | n | med% | mean% | win% | 95% CI of mean |
|---|---:|---:|---:|---:|---|
| **REAL tactical signals** | 2,730 | +0.037 | **+0.052** | 52.2% | [+0.019, +0.087] |
| NULL 1 — random symbol | 2,730 | −0.024 | −0.038 | 46.4% | [−0.066, −0.011] |
| NULL 2 — random direction | 2,730 | +0.039 | +0.027 | 52.6% | [−0.008, +0.061] |
| NULL 3 — random entry time | 2,730 | +0.152 | **+0.253** | 60.5% | [+0.221, +0.286] |

| Comparison | difference | 95% CI | Verdict |
|---|---:|---|---|
| real − random symbol | **+0.090%** | [+0.050, +0.133] | **BEATS the null** |
| real − random direction | +0.025% | [−0.023, +0.071] | **INDISTINGUISHABLE** |
| real − random time | **−0.201%** | [−0.250, −0.154] | **LOSES to the null** |

Three findings, in order of importance:

1. **The direction carries no information.** Replacing every long/short call
   with a coin flip produces statistically the same result. The rules identify
   stocks that are about to move; they do not identify which way.

2. **The timing is actively harmful.** Entering at a *random minute* of the same
   session, in the same stock, with the same stop and target, **beats the
   signal's chosen moment by 0.201%**. The signal systematically enters at a
   worse point than chance.

3. **Symbol selection is the one thing that works** (+0.090%) — but it is
   smaller than the statutory cost floor alone (0.072%) plus any spread.

Finding 2 corroborates the Phase 1 "confirmation trap" from an entirely
independent direction: these rules fire after a move has extended, and a random
moment is on average closer to the middle of the intraday range.

---

# 4. What is NOT proven

Stated explicitly rather than filled with assumption:

- **PHASE B (regime segmentation)** — only partially run, on two strategies, by
  stop distance and planned R:R. **No segment's 95% CI cleared the cost floor.**
  But the segmentation inherits defect #2, so no regime verdict is defensible.
  Market regime, VIX, time of day, sector, cap bucket, gap state: **NOT RUN.**
- **PHASE F (survivorship / selection bias)** — **NOT RUN.** Delisted symbols,
  indicator warm-up, universe-construction timing, overlapping signals on one
  move: unaudited. The 59.7% unresolved rate is itself a symptom that deserves
  this audit.
- **PHASE G (portfolio-level gate simulation)** — **NOT RUN.** The claim that
  the capital gate is "protective" rests on signal-level averages, not a
  chronological portfolio simulation with sizing, concurrency and sector caps.
- **PHASE H (news causality)** — **NOT RUN.**

---

# 5. BUG vs BAD STRATEGY vs BAD EXIT vs BAD COST vs NOISE

| Candidate | Verdict | Evidence |
|---|---|---|
| **BAD COST ASSUMPTION** | **RULED OUT** | Break-even is 0.018%; the statutory floor alone is 0.072%, with zero slippage |
| **BAD EXIT GEOMETRY** | **RULED OUT as primary** | 9 exit models, all net-negative; best adds +0.034% |
| **NOISE** | **RULED OUT for direction** | Direction indistinguishable from a coin flip across 2,730 signals with a bootstrap CI |
| **BAD STRATEGY** | **CONFIRMED for direction and timing** | Direction ≈ random; timing **worse** than random by 0.201% [CI −0.250, −0.154] |
| **METHOD DEFECT** | **CONFIRMED, two of them** | R:R gate ordering (corrected); unresolved-trade marking (per-strategy numbers withdrawn) |
| **BUG** | Confirmed separately | F4→CNC→48h stop suspension; unsafe reallocation. Neither explains the lack of edge |

---

# 6. Strategy status

Per-strategy verdicts from the previous audit are **withdrawn** — defect #2
makes them unstable at ±0.13%, larger than any claimed edge.

| Strategy | n (30d) | Status | Reason |
|---|---:|---|---|
| GAP_AND_GO | 753 | **INVESTIGATE** | negative in both runs, but magnitude unstable |
| PIVOT_BREAKOUT | 750–757 | **INVESTIGATE** | 60–66% win rate, negative net in both runs |
| VWAP_CROSSOVER | 413 | **INVESTIGATE** | negative in both runs |
| VOLUME_BREAKOUT | 220–229 | **INVESTIGATE** | previous "breakeven" claim **WITHDRAWN**: +0.046% vs +0.178% net across two runs of the same data |
| VWAP | 218 | **INVESTIGATE** | breakeven-ish, unstable |
| OVERSOLD_REBOUND | 159 | **INVESTIGATE** | negative both runs, 47.8% hit stop |
| OVERBOUGHT_FADE | 155 | **INVESTIGATE** | negative both runs, 55.5% hit stop |
| DAY_MOMENTUM | 9 | already disabled | — |
| ORB / SCALP / PIVOT_BOUNCE | 3–9 | **INSUFFICIENT** | n too small |

**No strategy should be disabled on this evidence.** The aggregate finding
(direction ≈ random, timing worse than random) applies to the population, and
disabling individual strategies on unstable per-strategy numbers would be
exactly the curve-fitting this stage was meant to avoid.

---

# 7. Frozen — no production change

Confirmed safety bugs remain the only permitted fixes:

1. F4 → CNC → SWING → 48 h stop suspension
2. Unsafe portfolio reallocation
3. Stale position marking

**Everything else stays frozen**: thresholds, stops, targets, R:R minimum, cash
reserve, sector caps, strategy enable/disable, prompts, models.

🚨 Real money remains disabled.

---

# 8. The next experiment — one, and marking-invariant

Everything above is limited by the same weakness: **60% of signals never resolve
within the window, and how they are marked drives the answer.**

**Experiment: a forward-marked, resolution-complete replay.**

- Extend each signal's window to the **next session's close**, so every trade
  resolves to stop, target, or an explicit end-of-holding-period mark.
- Report the **resolved subset separately** from the unresolved.
- Re-run Phase E's three nulls on the resolved subset only.

**Why this and nothing else:** it is the smallest change that removes the one
defect capable of overturning any conclusion here, and it costs no new data —
1m candles already extend across sessions. Until it is run, no per-strategy
number is decision-grade.

**What it cannot change:** the Phase E direction and timing results, which are
already marking-invariant. If the forward-marked replay agrees, the case is
closed.

---

# 9. The bottom line

**The negative result is real, but the previous audit proved it with the wrong
evidence.**

Its per-strategy table was unstable and is withdrawn. Its aggregate claim
survives on much firmer ground: across 2,730 signals, the tactical rules'
**direction is statistically indistinguishable from a coin flip**, and their
**timing is measurably worse than entering at a random minute of the same
session**. The only component that works — symbol selection, +0.090% — is
smaller than the statutory cost floor before a single paisa of spread.

**No exit model rescues it. No cost assumption rescues it. It is not noise.**

The system is not selecting *when* or *which way*. It is selecting *what*, and
then giving that advantage away at entry.

---

## Evidence appendix

| Claim | Method |
|---|---|
| 23/23 SL and TP preserved | `tactical_signals` ⋈ `paper_trades` on symbol ±2 min, 30 d |
| Gate ordering | `engine/risk_manager.py` cash 270–295, R:R 467; `utils/config.py:448` |
| R:R-eligible subset | `abs(target−entry)/abs(entry−stop) ≥ 1.2` |
| Nine exit models | re-walk of 1m candles, stop wins ties, grid fixed in advance |
| Three nulls | seed 11, 2,000-sample bootstrap, geometry held constant |
| Cost sweep | 0.00 → 0.30% on the eligible subset |
| Run contradiction | 150 rows matched on (symbol, MFE); 87 differ, all `outcome=OPEN` |

**Limitations.** 59.7% of signals unresolved. Fills use each signal's own
`entry_price` (production-faithful, but real queue position unmodelled). Replay
ignores portfolio constraints by design — it measures the signal population.
Phases B (full), F, G and H were not run.

*Systems and statistics analysis. Not investment advice. No production code was
changed in producing this report.*
