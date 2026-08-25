# PHASE 2 — HISTORICAL EDGE DISCOVERY

**Date:** 2026-08-25 · **Question:** does any existing information path contain repeatable,
cost-adjusted predictive information that can be monetized?

**NO PRODUCTION STRATEGY CHANGES IN PHASE 2.** Read-only replay throughout.

---

## Headline

> **NO REPEATABLE MONETIZABLE EDGE FOUND.**

Three information paths were tested independently against matched controls, at two defensible
cost bases, with symbol-clustered confidence intervals and session-level robustness.

| path | observations | sessions | verdict |
|---|---:|---:|---|
| **Master Intelligence** | 151,263 | 17 | **NO EVIDENCE** — top-decile minus bottom-decile spread is ~0 at every horizon |
| **Tactical** | 2,956 | 4 | **NO EVIDENCE** overall; one rule is **FRAGILE**, see §16 |
| **News (causal events)** | 3,516 | 12 | **NO EVIDENCE** — direction separation ~0 at every horizon |

One finding came close enough to require a deliberate attempt to break it —
`VOLUME_BREAKOUT` — and it broke. It is reported in full in §16 and §17 because a near-miss
that fails for identifiable reasons is more useful than a list of nulls.

---

## 1. Actual data coverage

Sample sizes come from the data, not from the width of a query window.

| date | 1m candles (syms) | complete¹ | tactical signals | tactical exec | news items | causal events | agent decisions | master scores | trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-07-13 → 07-31 | 1,676–1,785 | **no** | 0 | 0 | 350–2,259 | 73–1,227 | 2–663 | 32,760 (07-31) | 0 |
| 2026-08-03 | 3,351 | partial 43% | 0 | 0 | 843 | 448 | 447 | 45,730 | 0 |
| 2026-08-04 | 4,054 | partial 47% | 0 | 0 | 1,119 | 384 | 376 | 35,701 | 0 |
| 2026-08-05 | 3,877 | partial 38% | 0 | 0 | 887 | 348 | 299 | 38,988 | 0 |
| 2026-08-06 | 4,612 | partial 29% | 0 | 0 | 1,047 | 353 | 346 | 41,036 | 0 |
| 2026-08-07 | 4,363 | partial 29% | 0 | 0 | 715 | 889 | 888 | 36,203 | 0 |
| 2026-08-10 → 08-14 | 4,284–4,944 | partial 23–64% | 0 | 0 | 643–720 | **0** | **0** | 36k–54k | 0 |
| 2026-08-17 | 4,150 | partial 6% | 0 | 0 | 2,891 | 86 | 84 | 32,735 | 0 |
| 2026-08-18 | 4,230 | partial 11% | 0 | 0 | 2,290 | 542 | 515 | 11,475 | 0 |
| 2026-08-19 | 4,329 | partial 15% | 0 | 0 | 1,661 | 510 | 472 | 14,753 | 1 |
| **2026-08-20** | 4,464 | partial 46% | **530** | 0 | 3,121 | 487 | 420 | 13,032 | 2 |
| **2026-08-21** | 4,156 | partial 53% | **268** | 9 | 366 | 544 | 524 | 19,530 | 15 |
| **2026-08-24** | 4,342 | **95%** | **207** | 9 | 490 | 683 | 679 | 42,835 | 11 |
| **2026-08-25** | 2,748 | partial 36% | **1,998** | 16 | 402 | 623 | 619 | 42,739 | 16 |

¹ "complete" = share of symbols with 1m data reaching 09:50 UTC (15:20 IST).

**The binding constraints:**

- **`tactical_signals` exists for exactly 4 sessions.** 2026-08-20, 21, 24, 25. That is the
  entire tactical sample. It is not 30 days, and no amount of signal count changes that.
- **1m coverage before 2026-08-03 is truncated intraday** (last bar 05:40–06:17 UTC ≈ 11:10–11:47
  IST). Those sessions cannot support forward-return measurement and are excluded.
- **`causal_events` is empty for 2026-08-10 → 08-14** — five consecutive sessions with news items
  but no events. **EVIDENCE NOT AVAILABLE** for the news path on those dates.
- **`master_intelligence_scores` is the largest asset in the database**: 1,467,420 rows,
  2026-06-12 → 2026-08-25, 2,319 symbols. It has never been tested against outcomes.
- **`agent_decisions` has 8,062 rows and 33 non-SKIP decisions in its entire history**
  (27 BUY, 6 SELL, 8,029 SKIP). Part 12's `TAKE` arm is therefore untestable.
- **`master_score` on `agent_decisions`: 0 of 8,062.** Confirmed dead path (Phase 1).

### Sampling rules, fixed before any result was seen

- **Master:** the first score per (symbol, hour). Scores are rewritten ~26× per symbol per
  session; using all would inflate `n` and weight symbols by how often the Hub happened to
  rescore them. → 151,263 usable observations, 17 sessions, 1,935 symbols.
- **Tactical:** every signal with an entry price and a usable forward bar. → 2,956 of 3,003.
- **News:** every ticker named by a `causal_event`, split by arrival time. → 3,516.

---

## 2. Tactical overall edge

2,956 signals · 4 sessions · 382 symbols. Forward returns from **1m candles only**, in the
signal's own direction, from the bar at the signal timestamp.

| horizon | n | gross | net (delivery) | net (MIS) | median | win% | 95% CI (symbol-clustered) |
|---|---:|---:|---:|---:|---:|---:|---|
| +1m | 2,956 | −0.001 | −0.395 | −0.208 | +0.000 | 46.6 | [−0.005, +0.004] |
| +5m | 2,956 | +0.007 | −0.388 | −0.200 | +0.000 | 47.0 | [−0.007, +0.021] |
| +15m | 2,956 | −0.007 | −0.401 | −0.214 | −0.013 | 46.5 | [−0.038, +0.024] |
| +30m | 2,956 | +0.005 | −0.390 | −0.202 | −0.023 | 46.2 | [−0.041, +0.053] |
| +60m | 2,956 | −0.008 | −0.402 | −0.215 | −0.026 | 47.1 | [−0.080, +0.067] |
| +120m | 2,956 | +0.038 | −0.356 | −0.169 | −0.005 | 48.6 | [−0.054, +0.128] |
| EOD | 2,956 | +0.106 | −0.288 | −0.101 | +0.035 | 51.8 | [−0.032, +0.240] |

**Every horizon's gross return is statistically indistinguishable from zero, and every net
return is negative under both cost bases.**

Excursions (60m window): MFE mean +0.534% (median +0.299%), MAE mean −0.496% (median −0.321%),
time-to-MFE median 22m, time-to-MAE median 25m. Favourable and adverse excursions are
symmetric — the signals do not lead their adverse move.

**Missing-data handling:** a signal is dropped if its symbol has no bar within 300 s before the
signal timestamp, or no bar after it. 47 of 3,003 (1.6%) were dropped. EOD uses each symbol's
own last available bar, not an assumed session close, because 1m coverage to 15:20 IST ranges
from 6% to 95% of symbols by session.

---

## 3. Tactical rule-by-rule edge

EOD horizon. `ctl-diff` is paired against the balanced matched control (§4).

| rule | n | sessions | syms | gross | net (MIS) | median | win% | PF | 95% CI | ctl-diff |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `VOLUME_BREAKOUT` | 275 | 3 | 93 | **+0.605** | **+0.397** | +0.394 | 67.3 | 4.02 | [+0.279, +0.922] | **+0.298 [+0.073, +0.529]** |
| `VWAP_CROSSOVER` | 454 | 3 | 106 | +0.259 | +0.052 | +0.116 | 57.9 | 1.79 | [−0.116, +0.592] | −0.102 [−0.470, +0.217] |
| `PIVOT_BREAKOUT` | 807 | 4 | 144 | +0.235 | +0.028 | +0.120 | 58.6 | 1.96 | [+0.009, +0.483] | +0.098 [−0.162, +0.442] |
| `VWAP` | 225 | 3 | 42 | +0.193 | −0.014 | −0.055 | 47.1 | 1.52 | [−0.314, +0.656] | −0.133 [−0.815, +0.508] |
| `OVERSOLD_REBOUND` | 163 | 4 | 41 | −0.067 | −0.274 | −0.095 | 46.6 | 0.80 | [−0.341, +0.215] | −0.109 [−0.422, +0.234] |
| `GAP_AND_GO` | 805 | 4 | 157 | −0.092 | −0.300 | −0.130 | 43.7 | 0.84 | [−0.368, +0.190] | −0.031 [−0.393, +0.314] |
| **`OVERBOUGHT_FADE`** | 179 | 4 | 53 | **−0.633** | −0.840 | −0.517 | 30.7 | 0.22 | [−0.986, −0.267] | **−0.291 [−0.550, −0.034]** |
| `DAY_MOMENTUM` | 9 | — | — | — | — | — | — | — | INSUFFICIENT SAMPLE | |
| `ORB` | 26 | — | — | — | — | — | — | — | INSUFFICIENT SAMPLE | |
| `PIVOT_BOUNCE` | 9 | — | — | — | — | — | — | — | INSUFFICIENT SAMPLE | |
| `SCALP` | 4 | — | — | — | — | — | — | — | INSUFFICIENT SAMPLE | |

Two rules are statistically distinguishable from their matched control. One is positive
(`VOLUME_BREAKOUT`, §16) and one is **negative**: `OVERBOUGHT_FADE` underperforms its control
by −0.291pp with a 30.7% win rate and a profit factor of 0.22. That is the most reliable
directional result in the tactical sample, and it points the wrong way.

### `GAP_AND_GO` — a previous finding that does not replicate

The 2026-08-25 matched-control study reported `GAP_AND_GO` beating matched controls by
+0.319% [+0.131, +0.513] to session close, surviving Holm correction across 7 rules. With the
fourth session added and the full 805-signal sample:

```
gross -0.092    ctl-diff -0.031 [-0.393, +0.314]    INCONCLUSIVE
per session:  08-20 +0.422 (n=101)   08-21 -0.293 (n=108)   08-25 -0.167 (n=567)
```

**PREVIOUS CONCLUSION NO LONGER HOLDS.** The sign flips across sessions and the effect
disappears in the larger sample. The earlier result rested on a subset in which 08-20 was
over-represented.

---

## 4. Tactical baselines and match quality

Four controls were built. Every matching variable is computed from bars at or before the
signal timestamp; nothing reads a future bar, MFE, MAE or future volatility.

**Match quality** (standardised mean difference, |SMD| < 0.10 = balanced):

| control | n | SMD 15m return | SMD realised vol | SMD liquidity | balanced? |
|---|---:|---:|---:|---:|---|
| A — market (40 liquid symbols) | 2,897 | +0.387 | +0.473 | +0.559 | **no** |
| B — matched (±0.15pp ret, ±20% vol, 0.5–2.0× liquidity) | 1,473 | **+0.025** | **+0.052** | **+0.070** | **yes** |

**Paired results, signal minus its own control at the same instant:**

| horizon | A: vs market | verdict | B: vs matched control | verdict |
|---|---|---|---|---|
| +1m | −0.000 [−0.005, +0.004] | inconclusive | +0.001 [−0.004, +0.007] | inconclusive |
| +3m | +0.009 [+0.000, +0.018] | SIG>CTL | +0.007 [−0.001, +0.017] | inconclusive |
| +5m | +0.013 [−0.001, +0.027] | inconclusive | +0.007 [−0.006, +0.019] | inconclusive |
| +15m | +0.006 [−0.023, +0.037] | inconclusive | −0.001 [−0.025, +0.023] | inconclusive |
| +30m | +0.024 [−0.022, +0.073] | inconclusive | +0.023 [−0.014, +0.061] | inconclusive |
| +60m | +0.011 [−0.060, +0.084] | inconclusive | +0.023 [−0.037, +0.086] | inconclusive |
| +120m | +0.034 [−0.058, +0.129] | inconclusive | +0.026 [−0.065, +0.125] | inconclusive |
| EOD | +0.040 [−0.097, +0.176] | inconclusive | +0.002 [−0.148, +0.173] | inconclusive |

**Against the only properly balanced control, every horizon is inconclusive.** The single
"significant" cell is +3m against the badly-matched market control, at +0.009% with a lower
bound of exactly +0.000 — economically meaningless and produced by the control arm whose SMDs
are 4–8× the balance threshold.

This reproduces the 2026-08-25 finding on a larger sample: the apparent market-relative edge is
a property of the control pool, not of the signal.

---

## 5. Tactical direction edge

| horizon | real | opposite | random | real − random | 95% CI |
|---|---:|---:|---:|---:|---|
| +5m | +0.007 | −0.007 | −0.001 | +0.008 | [−0.013, +0.030] |
| +15m | −0.007 | +0.007 | +0.009 | −0.016 | [−0.048, +0.017] |
| +30m | +0.005 | −0.005 | +0.007 | −0.002 | [−0.053, +0.049] |
| +60m | −0.008 | +0.008 | −0.013 | +0.005 | [−0.070, +0.084] |
| EOD | +0.106 | −0.106 | −0.009 | +0.116 | [−0.030, +0.259] |

**Every horizon inconclusive.** The direction the rule assigns is not distinguishable from a
coin flip on the same signal set. This confirms the earlier three-session result on four
sessions.

---

## 6. Tactical timing edge

Control: a random timestamp on the **same symbol, same session, within ±60 bars** of the real
signal — so symbol, day and time-of-day bucket are all held.

| horizon | real | random-t | diff | 95% CI | verdict |
|---|---:|---:|---:|---|---|
| +1m | −0.001 | +0.001 | −0.002 | [−0.009, +0.004] | inconclusive |
| +5m | +0.007 | +0.013 | −0.006 | [−0.025, +0.013] | inconclusive |
| +10m | +0.000 | +0.041 | −0.041 | [−0.068, −0.011] | **REAL < RANDOM** |
| +15m | −0.007 | +0.057 | −0.065 | [−0.099, −0.027] | **REAL < RANDOM** |
| +30m | +0.005 | +0.121 | −0.116 | [−0.164, −0.068] | **REAL < RANDOM** |
| +60m | −0.008 | +0.189 | −0.198 | [−0.262, −0.139] | **REAL < RANDOM** |
| +120m | +0.038 | +0.208 | −0.170 | [−0.223, −0.120] | **REAL < RANDOM** |
| EOD | +0.106 | +0.270 | −0.164 | [−0.218, −0.116] | **REAL < RANDOM** |

**The real signal timestamp is significantly WORSE than a random timestamp on the same symbol
and session**, from +10m onward. The rules fire at moments that are systematically worse than
average moments in the same stock on the same day.

**Confound, stated:** the random timestamp may fall up to 60 bars earlier, which lengthens the
window to session close. That invalidates the **EOD** row only. Every fixed horizon (+10m
through +120m) holds window length constant and is unaffected — and those are all significant.

Interpretation consistent with §5 and §4: the rules are momentum-chasing. A random earlier
moment in the same stock captures the move the rule waits to confirm.

---

## 7. Tactical feature decomposition

Pre-signal features only, split into terciles fixed by the data's own quantiles (not chosen
after seeing results). EOD horizon. Session column shows the sign of the mean per session.

| feature | tercile | n | gross | net (MIS) | 95% CI | session signs |
|---|---|---:|---:|---:|---|---|
| recent 5m return | low / mid / high | 985/985/986 | +0.089 / +0.128 / +0.102 | −0.118 / −0.079 / −0.105 | all straddle 0 except mid [+0.003,+0.260] | `+-++` / `--++` / `+-++` |
| recent 15m return | low / mid / high | 965/966/966 | +0.031 / +0.137 / +0.187 | −0.176 / −0.070 / −0.020 | all straddle 0 | `+-++` all |
| volume surge | low / mid / high | 985/985/986 | +0.152 / +0.071 / +0.095 | −0.055 / −0.136 / −0.112 | all straddle 0 | `+-++` all |
| VWAP distance | low / mid / high | 985/985/986 | +0.081 / +0.079 / +0.159 | −0.126 / −0.128 / −0.048 | all straddle 0 | `+--+` / `--++` / `+-++` |
| range percentile | low / mid / high | 985/985/986 | +0.067 / +0.140 / +0.112 | −0.140 / −0.067 / −0.095 | all straddle 0 | `+--+` / `+-++` / `+-++` |
| realised vol | low / mid / high | 985/985/986 | +0.100 / +0.152 / +0.067 | −0.107 / −0.055 / −0.140 | all straddle 0 | `---+` / `+-++` / `+-+-` |
| time of day | early / mid / late | 985/985/986 | +0.092 / +0.184 / +0.043 | −0.115 / −0.023 / −0.164 | all straddle 0 | `++` / `+-+` / `+-++` |
| liquidity | low / mid / high | 985/985/986 | +0.077 / +0.001 / +0.241 | −0.130 / −0.206 / +0.034 | all straddle 0 | `---+` / `--++` / `+-++` |

**Not one of 24 tercile cells has a gross CI clearing zero and a positive net return with a
stable session sign.** The most favourable cell (high liquidity, gross +0.241, net MIS +0.034)
has a CI of [−0.003, +0.444] — the lower bound is below zero — and a `+-++` session pattern.

No feature family provides stable forward separation.

---

## 8. News data coverage limitation

**This is not a complete intraday-news test, and must not be described as one.**

- **NSE corporate announcements are absent in-session for the entire window.** Phase 1B
  established this as CONFIRMED: zero in-session announcements on every trading day from
  2026-08-17 to 2026-08-25, caused by the RSS/LLM loop starving the announcement fetch. What
  follows therefore covers **RSS-derived causal events only**, not exchange filings.
- **Bucket A (pre-open): 0 observations.** `causal_events` are created by the running engine, so
  their timestamps fall inside the session. **EVIDENCE NOT AVAILABLE.**
- **Bucket B (post-close): 595 event-tickers**, but scoring them requires the next session's
  open, which was not measured here. **EVIDENCE NOT AVAILABLE.**
- **Bucket C (in-session): 3,516 scored** across 12 sessions, 207 symbols. This is the only
  populated bucket, and the only one reported below.
- **`causal_events` is empty for 2026-08-10 → 08-14.** Five sessions absent.

---

## 9. News overall edge

Direction is taken from the `causal_event` that named the ticker — never from the outcome, and
never from the LLM's verdict.

| horizon | n | gross | net (delivery) | win% | vs market (paired) |
|---|---:|---:|---:|---:|---|
| +30m | 3,516 | −0.003 | −0.397 | 48.2 | −0.009 [−0.030, +0.015] |
| EOD | 3,516 | −0.071 | −0.465 | 44.9 | −0.075 [−0.194, +0.074] |

By tagged direction, EOD:

| tag | n | gross (in tag direction) | vs market |
|---|---:|---:|---|
| LONG | 2,428 | −0.181 | −0.137 [−0.281, +0.068] |
| SHORT | 1,088 | +0.173 | +0.063 [−0.102, +0.234] |

**Separation test** — long-tagged minus short-tagged, both measured as raw market move:

| horizon | long-tagged | short-tagged (raw) | separation |
|---|---:|---:|---:|
| +5m | +0.000 | −0.010 | **+0.010pp** |
| +15m | −0.017 | −0.013 | −0.004pp |
| +30m | −0.019 | −0.033 | +0.013pp |
| +60m | −0.049 | −0.048 | −0.001pp |
| EOD | −0.181 | −0.173 | −0.007pp |

**Separation is ~0 at every horizon and changes sign.** The event direction tag carries no
usable information. This confirms the 2026-08-25 single-session result (separation −0.213pp,
p = 0.21) on twelve sessions.

Session robustness: 5 of 11 measurable sessions positive.

---

## 10. News event-type edge

**EVIDENCE NOT AVAILABLE.**

The predefined families (earnings, order/contract, management, regulatory, corporate action,
rating, M&A, capacity, fundraising) matched **zero** of the 3,516 in-session observations. The
`category` field is populated only for NSE announcements, and those are exactly what is missing
in-session (§8). RSS-derived events carry a source name, not an exchange filing category.

All 3,516 fall into "other/unmatched": +30m −0.003 [−0.024, +0.019], EOD −0.071 [−0.191, +0.083].

A meaningful event-type decomposition requires the in-session announcement feed to be working,
which Phase 1B repaired but for which no history yet exists.

---

## 11. LLM incremental value

| group | n | +30m gross | +30m vs market | EOD gross |
|---|---:|---:|---|---:|
| EVENT ONLY (all events) | 3,516 | −0.003 | −0.009 [−0.030, +0.015] | −0.071 |
| agent SKIPped it | 3,177 | −0.006 | −0.013 [−0.035, +0.013] | −0.076 |
| agent said BUY/SELL | **1** | — | **INSUFFICIENT SAMPLE** | — |
| agent never saw it | 338 | +0.021 | +0.032 [−0.025, +0.075] | −0.035 |
| SKIP, confidence 70–100 | 527 | −0.036 | **−0.050 [−0.090, −0.003]** | −0.134 |
| SKIP, confidence 60–69 | 1,643 | −0.009 | −0.019 [−0.048, +0.013] | −0.205 |
| SKIP, confidence 0–59 | 1,007 | +0.016 | +0.016 [−0.023, +0.061] | +0.164 |

**The `TAKE` arm cannot be tested: one non-SKIP decision exists in the entire matched sample**
(33 in the whole database, against 8,029 SKIPs). **EVIDENCE NOT AVAILABLE** for
"EVENT + LLM vs EVENT ONLY" as a positive test.

What can be said: events the agent skipped at high confidence went on to do slightly *worse*
than the market (−0.050pp, CI excludes zero at +30m), and events it skipped at low confidence
did slightly better. Confidence therefore orders outcomes in the direction consistent with the
skip being correct — but the magnitudes (0.05–0.17pp) are an order of magnitude below cost, so
this is not monetizable information even if it is real.

---

## 12. Master Intelligence independent edge

**Not connected to execution. Tested here as an independent historical signal only.**

151,263 observations · 17 sessions · 1,935 symbols. This is the best-powered test in the entire
investigation — roughly 50× the tactical sample.

**A. Unconditional baseline** (all scores, measured long):

| horizon | n | gross | net (delivery) | median | win% | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| +5m | 151,263 | −0.005 | −0.399 | +0.000 | 42.3 | [−0.007, −0.003] |
| +30m | 151,263 | −0.011 | −0.405 | −0.016 | 43.8 | [−0.014, −0.008] |
| +60m | 151,263 | −0.021 | −0.415 | −0.033 | 43.4 | [−0.025, −0.016] |
| EOD | 151,263 | −0.083 | −0.477 | −0.076 | 42.2 | [−0.096, −0.068] |

**B. Score percentile buckets** (edges fixed by percentile before results were seen), EOD:

| bucket | score range | n | gross | 95% CI |
|---|---|---:|---:|---|
| p0–20 | [−74.9, −15.1] | 30,253 | −0.100 | [−0.126, −0.074] |
| p20–40 | [−15.1, 11.0] | 30,272 | −0.078 | [−0.106, −0.051] |
| p40–60 | [11.0, 38.7] | 30,292 | −0.078 | [−0.112, −0.043] |
| p60–80 | [38.7, 46.9] | 30,290 | −0.062 | [−0.093, −0.034] |
| p80–90 | [46.9, 51.1] | 15,182 | −0.083 | [−0.122, −0.041] |
| p90–95 | [51.1, 55.5] | 7,575 | −0.104 | [−0.159, −0.050] |
| **p95–100** | [55.5, 83.5] | 7,574 | **−0.108** | [−0.187, −0.029] |

**The highest-scoring ventile performs worse than the lowest.** There is no monotonic
relationship between score and forward return in either direction.

**C. The Hub's own categorical label**, EOD:

| label | n | gross |
|---|---:|---:|
| `STRONG_BUY` | 57,608 | −0.079 |
| `BUY` | 21,691 | −0.056 |
| `NEUTRAL` | 59,066 | −0.089 |
| `SELL` | 12,884 | −0.114 |
| `STRONG_SELL` | 14 | INSUFFICIENT SAMPLE |

`STRONG_BUY` beats `NEUTRAL` by 0.010pp. `SELL` is the most negative, which is the correct
ordering — by 0.035pp, against a cost of 207–394pp basis points. Directionally sensible,
economically irrelevant.

**D. Rank**, EOD: rank 1–5 (n=453) −0.061 [−0.369, +0.247]. Being the Hub's single best-ranked
name carries no measurable advantage.

**E. Spread test** — top decile minus bottom decile, the cleanest statement of rank information:

| horizon | top decile | bottom decile | **spread** |
|---|---:|---:|---:|
| +5m | −0.006 | −0.003 | **−0.003pp** |
| +15m | +0.000 | −0.000 | **+0.000pp** |
| +30m | −0.012 | −0.016 | **+0.004pp** |
| +60m | −0.016 | −0.032 | **+0.016pp** |
| EOD | −0.106 | −0.104 | **−0.002pp** |

**The spread is indistinguishable from zero at every horizon.** A 151,263-observation test with
1,935 symbol clusters finds no separation between the Hub's best and worst names.

**Session robustness:** top decile positive on **6 of 17** sessions. Sign pattern `++--+-----++----+`.

**Classification: NO EVIDENCE.** Master Intelligence, as currently computed, does not contain
predictive information about forward price movement at any tested horizon.

---

## 13. Master component edge

Top-decile minus bottom-decile spread by component, EOD:

| component | top-decile n | top-decile return | bottom-decile | spread |
|---|---:|---:|---:|---:|
| technical | 57,497 | −0.083 [−0.108, −0.057] | −0.136 | +0.052pp |
| news | 27,235 | −0.099 [−0.132, −0.069] | −0.130 | +0.031pp |
| earnings | 36,537 | −0.066 [−0.093, −0.038] | −0.113 | +0.047pp |
| fundamental | 15,451 | −0.078 [−0.115, −0.040] | −0.120 | +0.042pp |
| macro | 27,204 | −0.058 [−0.085, −0.030] | −0.007 | −0.050pp |
| sector | 40,270 | −0.124 [−0.146, −0.103] | −0.034 | **−0.090pp** |

**Does the master score add information beyond its components?** No — but neither do the
components. The largest spread of any component is 0.090pp (sector, and it points the *wrong*
way: high sector score → worse outcome). The composite's spread (−0.002pp) is smaller than four
of its six inputs, which is what one expects when averaging uninformative signals.

Every top decile is negative in absolute terms. **No weight was changed.**

---

## 14. Session robustness

Mandatory for every promising finding.

| finding | sessions | positive | classification |
|---|---:|---:|---|
| Tactical overall (EOD) | 4 | 3 of 4 | inconclusive — 08-21 is **−0.197 [−0.379, −0.019]**, significantly negative |
| Master top decile (EOD) | 17 | **6 of 17** | NO EVIDENCE |
| News in-session (EOD) | 11 | 5 of 11 | NO EVIDENCE |
| `VOLUME_BREAKOUT` (EOD) | 3 | 2 of 3 | **FRAGILE** — see §16 |
| `GAP_AND_GO` (EOD) | 3 | 1 of 3 | sign flips; previous finding withdrawn |

Per-rule × per-session gross (EOD), blank = insufficient:

| rule | 08-20 | 08-21 | 08-24 | 08-25 |
|---|---:|---:|---:|---:|
| `GAP_AND_GO` | +0.422 (101) | −0.293 (108) | — | −0.167 (567) |
| `PIVOT_BREAKOUT` | +0.112 (137) | — | — | +0.278 (639) |
| `VOLUME_BREAKOUT` | — | — | — | +0.697 (208) |
| `VWAP_CROSSOVER` | — | −0.136 (81) | +0.373 (73) | +0.339 (300) |
| `OVERBOUGHT_FADE` | −0.297 (43) | — | — | −0.719 (115) |
| `OVERSOLD_REBOUND` | +0.030 (49) | — | — | −0.041 (83) |
| `VWAP` | +0.286 (181) | — | — | — |

---

## 15. Out-of-sample validation

Chronological, session-level split — never random across trades, because signals from the same
day are correlated.

**Tactical:** TRAIN 08-20 + 08-21 (n=793) → TEST 08-24 + 08-25 (n=2,163).

| split | n | gross | net (MIS) | 95% CI |
|---|---:|---:|---:|---|
| TRAIN | 793 | +0.061 | −0.146 | [−0.172, +0.297] |
| TEST | 2,163 | +0.123 | −0.084 | [−0.036, +0.295] |

Both inconclusive, both net-negative.

**Rule carried forward without re-selection:** the best TRAIN rule was `VWAP` — which has
**n = 27 in TEST**. **INSUFFICIENT SAMPLE.** The four-session sample cannot support rule-level
out-of-sample validation.

**Master Intelligence and News:** the top-decile and direction tests are flat in every session,
so a train/test split has nothing to carry forward. **EVIDENCE NOT AVAILABLE** as a positive
validation; the negative result is consistent across all 17 and 12 sessions respectively.

---

## 16. Cost-adjusted economics, and the one near-miss

### The cost model — Part 3

The project has a real cost model: `paper_trading/trade_simulator.py::estimate_trade_cost`
(brokerage capped ₹20 + STT + exchange + SEBI + stamp + 18% GST), plus an adverse slippage band
of 2–8 bps per leg in `TradeSimulator._apply_slippage`.

**It is a delivery model** — its own docstring says so, and it charges STT 0.1% on both legs.
After Phase 1A, tactical trades are MIS, where STT is 0.025% on the sell leg only. Both bases
are therefore reported; neither is invented.

| basis | statutory round-trip @ ₹50k | + slippage (mid 0.100%) | total |
|---|---:|---:|---:|
| delivery (the project's own model as written) | 0.2942% | 0.100% | **0.3942%** |
| intraday / MIS (correct for tactical today) | 0.1072% | 0.100% | **0.2072%** |

### Part 18 — the friction gate

| group | n | gross | net (delivery) | net (MIS) | 95% CI gross | monetizable? |
|---|---:|---:|---:|---:|---|---|
| ALL tactical | 2,956 | +0.106 | −0.288 | −0.101 | [−0.031, +0.246] | **NO** |
| `VOLUME_BREAKOUT` | 275 | +0.605 | +0.210 | +0.397 | [+0.279, +0.922] | passes the gate |
| `VOLUME_BREAKOUT` **ex-top-6 symbols** | 222 | +0.227 | −0.167 | **+0.020** | [+0.005, +0.415] | marginal |
| `PIVOT_BREAKOUT` | 807 | +0.235 | −0.159 | +0.028 | [+0.009, +0.483] | marginal |
| `PIVOT_BREAKOUT` ex-top-6 | 723 | +0.027 | −0.368 | −0.181 | [−0.134, +0.175] | **NO** |
| `VWAP_CROSSOVER` | 454 | +0.259 | −0.135 | +0.052 | [−0.116, +0.592] | **NO** |
| `OVERBOUGHT_FADE` | 179 | −0.633 | −1.027 | −0.840 | [−0.986, −0.267] | **NO** |

### `VOLUME_BREAKOUT` — the near-miss, and why it fails

It is the only finding in the whole phase that clears its cost with a CI excluding zero and a
significant matched-control difference. It was then deliberately stressed:

**1. Per session** — a result on one day is fragile by definition:

| session | n | syms | gross | net (MIS) | win% | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| 2026-08-21 | 31 | 19 | **−0.192** | −0.399 | 32.3 | [−0.558, +0.171] |
| 2026-08-24 | 36 | 14 | +0.757 | +0.550 | 66.7 | [+0.120, +1.307] |
| 2026-08-25 | 208 | 69 | +0.697 | +0.490 | 72.6 | [+0.298, +1.089] |

Three sessions; one negative. 76% of the sample is a single day.

**2. Symbol concentration** — 93 symbols, total summed return +166.3pp:

| symbol | n | sum | share of total |
|---|---:|---:|---:|
| IDEA.NS | 8 | +32.58pp | 19.6% |
| PAYTM.NS | 9 | +21.34pp | 12.8% |
| DIXON.NS | 13 | +19.43pp | 11.7% |
| DHOOTTRANS.NS | 6 | +15.45pp | 9.3% |
| ADANIENT.NS | 10 | +14.34pp | 8.6% |
| PCJEWELLER.NS | 7 | +12.68pp | 7.6% |
| **top 6 combined** | **53** | **+115.8pp** | **69.6%** |

**Excluding those six: gross +0.227, net (MIS) +0.020, net (delivery) −0.167.** The entire
economic case rests on six high-volatility names, and 53 of 275 observations.

**3. Out-of-sample:** TRAIN (08-20/21) n = 31, gross **−0.192**. TEST (08-24/25) n = 244,
gross +0.706. The training period is negative and too small to constitute a training set. This
is not a passed out-of-sample test; it is a regime difference between two pairs of days.

**4. Horizon profile:** significant only at +120m and EOD. At +5m through +60m every CI
straddles zero and every net return is negative. It is not a breakout-continuation effect at
tradeable horizons — it is an all-day drift that only pays if held to the close, which after
Phase 1A is exactly when the MIS squareoff closes it.

**5. Not explained by a crude control variable:** other rules firing in the same liquidity,
volatility, volume-surge and recent-return ranges return +0.053 to +0.081, against
`VOLUME_BREAKOUT`'s +0.605. So the effect is not simply "these were volatile liquid stocks" —
but this is a range filter, not a pairing, and the matched-control result (+0.298 [+0.073,
+0.529]) is the defensible version.

**6. All 275 are BUY.** There is no short arm to check for symmetry.

### Against Part 19's seven criteria

| # | criterion | `VOLUME_BREAKOUT` |
|---|---|---|
| 1 | distinguishable from its baseline | **PASS** — ctl-diff +0.298 [+0.073, +0.529] |
| 2 | positive net expected return | **PASS** on MIS basis (+0.397) |
| 3 | survives multiple sessions | **FAIL** — 3 sessions, one negative, 76% from one day |
| 4 | not dependent on one symbol | **FAIL** — 6 of 93 symbols carry 69.6% |
| 5 | no obvious look-ahead | PASS |
| 6 | pre-signal information only | PASS |
| 7 | economically meaningful after costs | **FAIL** — +0.020% ex-concentration on the favourable basis |

Fails 3, 4 and 7. **Classification: FRAGILE.** It is **not** an EDGE CANDIDATE.

---

## 17. What is NOT an edge

Attractive-looking findings that failed validation:

| finding | why it fails |
|---|---|
| `VOLUME_BREAKOUT` net +0.397% | **FRAGILE** — 69.6% of return from 6 of 93 symbols; 1 of 3 sessions negative; collapses to +0.020% ex-concentration |
| `PIVOT_BREAKOUT` gross +0.235%, CI [+0.009, +0.483] | **NON-MONETIZABLE** — net −0.159% on the delivery basis, +0.028% on MIS; ex-top-6 it is −0.181% |
| `GAP_AND_GO` +0.319% vs control (previous report) | **DOES NOT REPLICATE** — with the 4th session and full sample, −0.031 [−0.393, +0.314]; sign flips across sessions |
| Tactical +3m vs market, +0.009% [+0.000, +0.018] | **EXPLAINED BY CONTROL VARIABLE** — produced only by the badly-matched market arm (SMD 0.39–0.56); the balanced arm is inconclusive |
| Master `STRONG_BUY` ordering above `NEUTRAL` | **NON-MONETIZABLE** — 0.010pp against a 20.7pp cost floor |
| Master `SELL` most negative label | **NON-MONETIZABLE** — correct direction, 0.035pp magnitude |
| LLM high-confidence skips avoid −0.050pp | **NON-MONETIZABLE** — real ordering, magnitude ~1/4 of one basis point of cost |
| News SHORT-tagged +0.173% EOD | **INCONCLUSIVE** — vs market +0.063 [−0.102, +0.234]; separation from LONG is −0.007pp |
| Tactical EOD win rate 51.8% | **NOT AN EDGE** — median +0.035%, net negative; win rate without magnitude is not expectancy |

And one finding that is real but points the wrong way:

| finding | status |
|---|---|
| `OVERBOUGHT_FADE` underperforms its matched control by −0.291pp [−0.550, −0.034], PF 0.22, win 30.7%, consistent across 4 sessions | **CONFIRMED NEGATIVE.** The most reliable directional result in the tactical sample. |

---

## 18. What remains unknown

- **In-session exchange filings.** The single largest gap. Phase 1B repaired the starvation, but
  no history exists. The most likely place for a real event edge is precisely the data the
  system was blind to. **EVIDENCE NOT AVAILABLE.**
- **The `TAKE` arm of the LLM.** One non-SKIP decision in the matched sample, 33 in the whole
  database. "Does the LLM add value when it acts?" cannot be answered.
- **Post-close news → next-session open.** 595 event-tickers exist; scoring needs the next
  session's opening bars, not measured here.
- **Pre-open news.** Zero observations in `causal_events`. Requires a different source table.
- **Tactical rule-level out-of-sample.** Four sessions is not enough. `VWAP` had n = 27 in TEST.
- **Whether `VOLUME_BREAKOUT` survives on more sessions.** It is the only thing worth the cost
  of finding out.
- **Longer holding periods.** Everything here is intraday-to-EOD. Multi-day behaviour is untested.
- **Whether the 2026-08-10 → 08-14 `causal_events` gap hides a different news regime.**

---

## 19. Recommended next experiment

**One experiment, not a programme.**

> Collect 15–20 further sessions of `tactical_signals` with the Phase 1A/1B fixes in place, then
> re-run §16's `VOLUME_BREAKOUT` stress test unchanged — same code, same thresholds, same
> stress criteria, no re-tuning.

Rationale: it is the only finding that passed control, cost and clustering, and it failed on
sample breadth alone — the one deficiency more data can actually fix. The pre-registered
success criterion, fixed now:

```
VOLUME_BREAKOUT is promoted to EDGE CANDIDATE if and only if, on >= 12 sessions:
  (a) the matched-control difference CI excludes zero,          AND
  (b) net return on the MIS cost basis is positive              AND
  (c) >= 70% of sessions are individually positive              AND
  (d) excluding the top 6 contributing symbols, net return stays positive
      with a CI excluding zero.
```

Fixing (a)–(d) in advance is what makes this a test rather than a search. If it fails any of
them, it is retired.

Secondary, cheaper and independent: **once in-session announcement history accumulates**
(Phase 1B), re-run §10's event-family decomposition. That is a genuinely untested information
path, not a re-examination of a null.

**Not recommended:** re-testing Master Intelligence on more data. A 151,263-observation,
17-session, 1,935-symbol test returning a 0.000–0.016pp decile spread is not underpowered. More
data will not rescue it; the scoring itself would have to change, and that is out of scope.

---

## 20. Production changes

**NO PRODUCTION STRATEGY CHANGES IN PHASE 2.**

No threshold, prompt, stop, target, weight, gate or allocation was modified. Master Intelligence
was **not** connected to execution. `PAPER_MODE` untouched. Nothing was deployed. The only
artefacts are the read-only analysis scripts and this report.

---

## Classification summary

| # | finding | classification |
|---|---|---|
| 1 | Tactical signals contain a cost-adjusted edge | **NO EVIDENCE** |
| 2 | Tactical direction carries information | **NO EVIDENCE** |
| 3 | Tactical timing carries information | **RULED OUT** — real is significantly *worse* than random at +10m→+120m |
| 4 | Any tactical pre-signal feature separates outcomes | **NO EVIDENCE** — 0 of 24 tercile cells |
| 5 | `VOLUME_BREAKOUT` is an edge | **FRAGILE** — fails Part 19 criteria 3, 4, 7 |
| 6 | `PIVOT_BREAKOUT` is an edge | **NON-MONETIZABLE** |
| 7 | `GAP_AND_GO` beats matched controls | **PREVIOUS CONCLUSION NO LONGER HOLDS** |
| 8 | `OVERBOUGHT_FADE` destroys value | **CONFIRMED** (negative) |
| 9 | Master Intelligence score is predictive | **NO EVIDENCE** — best-powered test in the investigation |
| 10 | Any Master component is predictive | **NO EVIDENCE** — max spread 0.090pp, wrong sign |
| 11 | News event direction is predictive | **NO EVIDENCE** — separation ~0 across 12 sessions |
| 12 | News event-type families differ | **EVIDENCE NOT AVAILABLE** — no in-session categories exist |
| 13 | The LLM adds value when it acts | **EVIDENCE NOT AVAILABLE** — n = 1 |
| 14 | LLM confidence orders outcomes | **CONFIRMED but NON-MONETIZABLE** |
| 15 | Tactical rule-level out-of-sample validation | **EVIDENCE NOT AVAILABLE** — 4 sessions |

---

**NO REPEATABLE MONETIZABLE EDGE FOUND.**

No shadow implementation is recommended, because nothing survived all five of control, cost,
session, symbol and out-of-sample. The one candidate that survived the first two is named in
§16 with a pre-registered test for when more data exists.

The word "alpha" is not used anywhere in this report.
