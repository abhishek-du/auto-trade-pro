# Symbol / Trigger Selection Alpha Test — Matched Controls

**Date:** 2026-08-25 · **Scope:** 1,002 tactical signals, sessions 2026-08-20 / 08-21 / 08-24
**Production code changed:** none. Read-only replay against `candles` and `tactical_signals`.

---

## The question

> After controlling for what was already true about the stock at the signal time, does the
> tactical trigger identify a stock that has unusually strong subsequent movement?

Every prior investigation in this series ruled something out — infrastructure, look-ahead,
stop geometry, direction, entry timing. Symbol selection was the last untested link.

---

## Verdict

**Split, because the sample splits.**

| Half of the sample | n | Verdict |
|---|---|---|
| A stock comparable to the signal existed at that minute | 506 (50.5%) | **NO EVIDENCE OF SELECTION EDGE** |
| No comparable stock existed at that minute | 496 (49.5%) | **INCONCLUSIVE — EVIDENCE NOT AVAILABLE** |
| Rule-level exception: `GAP_AND_GO` | 106 | **TRIGGER ADDS INFORMATION** (see §5) |

The headline number the naive test produces — signals beat controls by **+0.351%** to end of
day — **collapses to +0.019% [−0.065, +0.103], inconclusive**, the moment the signal set is
held fixed and only the control definition varies. The apparent edge was never about the
trigger. It was about *which signals had a control at all*.

---

## 1. Method

For each signal on symbol *S* at timestamp *T*, controls were drawn from 1,046 symbols with
liquid 1m data that session. Every matching variable is computed from bars with timestamp
`<= T`. This is enforced structurally, not by convention: a per-symbol state series is built
once, and each signal indexes it by `bisect_right(times, T) - 1`. A candidate whose most
recent bar is more than 5 minutes stale at *T* is dropped as untradable.

Forward returns are measured in the **signal's own direction** (a SELL signal's control is
also evaluated short), at +5/15/30/60/120 minutes and to session close. All intervals are
2.5–97.5% bootstrap, 6,000 resamples, paired on the signal.

Six matching arms, deliberately ordered from loose to tight:

| Arm | Matched on | Band |
|---|---|---|
| `MOM` | trailing 15m return | ±0.25pp |
| `VOL` | trailing 15m realised vol | ±25% |
| `LIQ` | session-to-date traded value | 0.5×–2.0× |
| `CMP` | all three | loose |
| `CMP2` | all three | ±0.15pp / ±20% / 0.5–2.0× |
| `CMP3` | all three + price decade | ±0.08pp / ±12% / 0.67–1.5× |

---

## 2. Match quality — read this before the results

Standardised mean difference between signal and its controls. `|SMD| < 0.10` is the
conventional balance threshold.

| Arm | n | SMD momentum | SMD volatility | SMD liquidity | Balanced? |
|---|---:|---:|---:|---:|---|
| `MOM` | 978 | +0.046 | +0.129 | **+0.869** | no — liquidity wildly off |
| `VOL` | 1001 | **+0.360** | +0.073 | **+0.881** | no |
| `LIQ` | 995 | **+0.386** | **+0.356** | +0.155 | no |
| `CMP` | 824 | +0.111 | +0.152 | +0.265 | marginal |
| `CMP2` | 507 | **+0.020** | **+0.056** | +0.115 | **yes** |
| `CMP3` | 54 | +0.018 | +0.025 | +0.023 | yes, but n=54 |

**A single-dimension match is not a control.** Matching on liquidity alone leaves the signal
stock with +0.386 SMD more momentum than its "control" — the comparison is then measuring
momentum, not selection. `CMP2` is the only arm that is balanced on all three axes at usable n.

---

## 3. The result, and why the naive version is wrong

### Naive (full sample per arm) — the misleading version

| Arm | n | signal EOD% | control EOD% | diff% | 95% CI |
|---|---:|---:|---:|---:|---|
| `MOM` | 970 | +0.089 | −0.051 | +0.140 | [+0.068, +0.212] |
| `VOL` | 993 | +0.090 | −0.127 | +0.217 | [+0.142, +0.293] |
| `LIQ` | 986 | +0.089 | −0.262 | **+0.351** | [+0.268, +0.436] |
| `CMP` | 815 | +0.003 | −0.183 | +0.186 | [+0.110, +0.262] |
| `CMP2` | 499 | −0.091 | −0.109 | +0.018 | [−0.069, +0.104] |

Two things are visible here that give the game away:

1. **The signal column is constant** (+0.089 / +0.090 / +0.089) across `MOM`/`VOL`/`LIQ` — it is
   the same 1,002 signals. Everything that moves is the *control* column, from −0.051 to −0.262.
   The "edge" is a property of the control pool, not of the signal.
2. The effect shrinks monotonically as match quality improves: +0.351 → +0.217 → +0.140 →
   +0.186 → +0.018. That is the textbook signature of confounding being progressively removed.

### Isolated — signal set held fixed at 505 signals, only the control definition varies

| Arm | H+5 | H+30 | H+120 | H→close |
|---|---:|---:|---:|---:|
| `MOM` | +0.014 | +0.034 | +0.039 | +0.022 |
| `VOL` | +0.014 | +0.030 | +0.042 | +0.045 |
| `LIQ` | +0.016 | +0.031 | +0.039 | +0.019 |
| `CMP` | +0.012 | +0.031 | +0.050 | +0.053 |
| `CMP2` | +0.018 | **+0.044\*** | +0.023 | +0.018 |

`*` = 95% CI excludes zero. **1 of 20 cells.** Expected by chance at α=0.05 on 20 tests: 1.0.

Once the signal set is fixed, *no control definition produces a significant effect* — including
the deliberately badly-matched ones. The +0.351% headline is entirely an artefact of comparing
different subsets of signals, not of comparing signals to controls.

**Placebo check:** control-vs-control across two arms, no signal involved, returns
+0.002% [−0.063, +0.067] — the measurement pipeline does not manufacture an effect on its own.

---

## 4. Where the effect actually lives

Splitting the 1,002 signals by whether a closely-comparable stock existed at that minute:

| Group | n | mean \|15m mom\| | 15m vol | session traded value | signal EOD% | measured "edge" (LIQ arm) |
|---|---:|---:|---:|---:|---:|---|
| Close match exists | 506 | 0.174% | 0.071% | ₹151 cr | −0.091% | +0.019% [−0.07, +0.10] |
| **No close match exists** | 496 | **0.642%** | **0.162%** | **₹525 cr** | **+0.265%** | **+0.692% [+0.55, +0.83]** |

This is the whole finding in one table.

The signals where the system appears to win are exactly the ones where **no comparable stock
existed** — 3.7× the momentum, 2.3× the volatility, 3.5× the traded value of the matchable half.
For those, the +0.692% is a comparison against stocks that were not comparable. It is not
evidence of selection; it is a restatement of the fact that the trigger fires on extremes.

**And it cannot be tested.** A matched control for the unmatchable half does not exist by
construction. For that half the honest answer is **EVIDENCE NOT AVAILABLE** — this experiment
can neither confirm nor refute a selection edge there.

What *can* be said about that half is its absolute economics:

```
n=495   absolute EOD  +0.265% [+0.165, +0.368]   median +0.067%   win 53.1%
        net of 0.222% round-trip cost:  +0.043%   CI net [-0.057, +0.146]
```

Positive point estimate, **CI straddling zero after costs**, and the median is +0.067% — the
mean is carried by a tail. Even on the half where selection cannot be ruled out, the net
expectancy is not distinguishable from zero.

---

## 5. Rule-by-rule — one genuine exception

On the balanced `CMP2` arm, 7 rules had n≥15. Permutation p-values (sign-flip on paired
differences, 6,000 draws), Holm-corrected for 7 tests:

| Rule | n | diff% | raw p | Holm α | survives |
|---|---:|---:|---:|---:|---|
| **`GAP_AND_GO`** | 106 | **+0.319** | 0.0015 | 0.0071 | **YES** |
| `OVERBOUGHT_FADE` | 34 | −0.538 | 0.0188 | 0.0083 | no |
| `VWAP` | 70 | −0.263 | 0.0522 | 0.0100 | no |
| `VWAP_CROSSOVER` | 77 | +0.085 | 0.4604 | 0.0125 | no |
| `VOLUME_BREAKOUT` | 35 | +0.107 | 0.5709 | 0.0167 | no |
| `PIVOT_BREAKOUT` | 91 | +0.022 | 0.6901 | 0.0250 | no |
| `OVERSOLD_REBOUND` | 74 | −0.016 | 0.8930 | 0.0500 | no |

`GAP_AND_GO` is the one result that survives correction, and its horizon profile is
monotone — the mark of a real effect rather than a fluke at one arbitrary horizon:

| Horizon | n | signal% | diff vs matched control | **net of 0.222% cost** |
|---|---:|---:|---|---:|
| +5m | 106 | +0.011 | +0.031 [−0.010, +0.075] | −0.211% |
| +15m | 106 | −0.026 | +0.033 [−0.034, +0.100] | −0.248% |
| +30m | 106 | +0.030 | +0.114 [+0.023, +0.204] | −0.192% |
| +60m | 106 | +0.065 | +0.165 [+0.037, +0.287] | −0.157% |
| +120m | 106 | +0.129 | +0.283 [+0.105, +0.467] | −0.093% |
| → close | 106 | +0.121 | **+0.319 [+0.131, +0.513]** | **−0.101%** |

Per the standing instruction, this is called **conditional predictive information**, not alpha:
`GAP_AND_GO` picks stocks that outperform properly-matched peers, by a margin that grows with
horizon and is statistically distinguishable from zero after multiplicity correction.

**It is not currently monetisable.** Its absolute return at every horizon is below the 0.222%
round-trip cost. The information is real; the margin is smaller than the friction. Turning it
into P&L would require either a materially lower cost basis or an exit design that captures
more of the +0.319% relative move than the current one does — neither of which this experiment
tested, and neither of which should be assumed to work.

### Other splits (balanced arm, EOD)

| Split | n | diff% | 95% CI | verdict |
|---|---:|---:|---|---|
| momentum family | 382 | +0.071 | [−0.024, +0.166] | inconclusive |
| mean-reversion family | 117 | −0.154 | [−0.359, +0.044] | inconclusive |
| BUY | 436 | +0.107 | [+0.024, +0.192] | SIG>CTL |
| **SELL** | 63 | **−0.593** | [−0.942, −0.264] | **SIG<CTL** |
| session 08-20 | 250 | −0.004 | [−0.125, +0.118] | inconclusive |
| session 08-21 | 145 | −0.109 | [−0.244, +0.030] | inconclusive |
| session 08-24 | 104 | +0.251 | [+0.029, +0.471] | SIG>CTL |
| 14:00–15:30 IST | 272 | +0.115 | [+0.007, +0.231] | SIG>CTL |

The SELL result is the strongest single number in the table and it points the wrong way:
short signals *underperform* matched controls by −0.593%. n=63 and it is uncorrected, so it is
a lead, not a finding — but it is consistent with the earlier finding that direction is
indistinguishable from random.

The session split is the fragility warning: the whole positive result rests on 2026-08-24.
Two of three sessions are flat or negative.

---

## 6. Limitations — stated, not buried

1. **Three sessions.** `tactical_signals` holds 2,805 rows across four dates; 1,002 usable
   signals on three. Every number here can move with more data. The session split shows it
   already does.
2. **Half the sample is untestable.** The 496 unmatchable signals are not a nuisance subgroup —
   they are where the system's apparent performance comes from, and this design cannot evaluate
   them. Any future work should target exactly this half, probably by relaxing "comparable" to
   a propensity score over a wider universe rather than hard bands.
3. **Controls are held passively.** Each control is entered at *T* and held to the horizon with
   no stop and no target. That is the correct null for a *selection* question but it is not the
   system's trade. This experiment answers "does the trigger pick a better stock", not "does the
   strategy make money" — the counterfactual stop-anchor test already answered the second.
4. **`CMP3` (n=54) is reported for completeness only.** Its intervals are too wide to carry
   weight and it is not used in any conclusion.
5. **Costs.** 0.222% round-trip = 0.072% statutory (brokerage 0.030 + STT 0.025 + exchange 0.007
   + GST 0.007 + stamp 0.003) + 0.150% spread/slippage. A different slippage assumption moves
   the tradability line but not the relative comparisons.

---

## 7. What this changes

Consistent with the prior reports in this series, not contradicting them:

- The **direction** finding stands (SELL −0.593% vs matched controls reinforces it).
- The **stop-anchor exoneration** stands — this test bypasses stops entirely and still finds
  no selection edge on the matchable half.
- The **timing** finding stands — the +5m and +15m columns are flat everywhere.

What is new: symbol selection is now also ruled out **for the half of signals that can be
tested**, and the mechanism producing every previously-observed positive number is identified —
the trigger fires on stocks so extreme that no comparable exists, and comparing them to
non-comparable stocks produces a number that looks like an edge and is not one.

The one thing worth keeping is `GAP_AND_GO`'s conditional predictive information. It is the
only component of the tactical system, across five investigations, that has produced a
statistically defensible positive result against a properly matched control. It is also,
today, smaller than the cost of trading it.

**No production code was changed. No thresholds were tuned. No profitable examples were
selected. Nothing was optimised against these three sessions.**
