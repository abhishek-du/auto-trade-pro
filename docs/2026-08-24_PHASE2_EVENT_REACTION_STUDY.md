# Phase 2 — Event reaction study

**Question:** does the event stream this system produces have a measurable
directional reaction that could be traded?

**Answer:** No. Not at any horizon from 1 minute to end of day, not at any
importance tier, not in any major event category, and not in any
high-confidence subset. Measured before costs.

**Status of this document:** this establishes *raw and benchmark-adjusted event
reaction*. It is deliberately **not** called alpha — costs, entry policies and
slippage have not been applied. They are not needed: there is no gross reaction
for costs to erode.

---

## 1. Eligibility funnel

`T_event = causal_events.created_at` — the moment **our system** created the
event row. Every measurement therefore starts from information we demonstrably
held, so no stage can smuggle in look-ahead. (`news_items.published_at` would
give the theoretical ceiling but is populated for only 2,912 of 11,049 events.)

Nothing was discarded silently. Every exclusion is counted and sampled.

| Stage | Kept | % of raw | Dropped | Examples of what was dropped |
|---|---:|---:|---:|---|
| S0 Raw event-symbol mentions | 19,241 | 100.0% | — | — |
| S1 Mapped to instrument **with** 1m data | 14,293 | 74.3% | 4,948 | `TATAMOTORS`, `HDFC`, `AIRINDIA`, `PCJEWEL`, `ADANIEXPO` |
| S2 T_event inside NSE session | 6,777 | 35.2% | 7,516 | `ICICIBANK 2026-07-16 14:05` (=19:35 IST), `POWERGRID 14:05`, `NTPC 14:05` |
| S3 ≥30 min of session left after event | 6,240 | 32.4% | 537 | `TCS 09:36 UTC`, `INFY 09:36`, `WIPRO 09:36` |
| S4 De-duplicated (instrument, side, minute) | 5,652 | 29.4% | 588 | `SUNPHARMA.NS 03:49` twice, `ICICIBANK.NS 03:49` |
| S5 Non-confounded (>60 min apart) | 2,867 | 14.9% | 2,785 | `HDFCAMC.NS 03:50` (prev 03:49), `03:50`, `04:30`, `07:09` (prev 07:02) |
| S6 Valid pre-event price | 2,582 | 13.4% | 285 | `VARDMNPOLY.NS`, `INFRA.NS`, `SBIFUNDS.NS` (no 1m that session) |
| S7 ≥5 post-event bars | 2,167 | 11.3% | 415 | `HDFCAMC.NS 07:02`, `HAL.NS 07:59`, `LT.NS 07:02` |
| S8 Benchmark available | 2,167 | 11.3% | 0 | — (NIFTYBEES present on all 22 sessions) |

**FINAL: 2,167 independent reaction observations · 503 instruments · 22 sessions ·
LONG 1,469 / SHORT 698.**

### The two largest losses are not data problems

- **S2 (−7,516, 39% of raw)** — the events were created *outside market hours*.
  The crawler produces most events in the evening. Those are not tradable
  intraday, which is a scheduling fact, not a defect.
- **S5 (−2,785, 14% of raw)** — the same instrument receives repeated events
  within 60 minutes. `HDFCAMC.NS` alone fired at 03:49, 03:50, 04:30, 07:02,
  07:09 and 07:47 in one session. These are the same story re-emitted, so they
  are not independent observations. This is a **news-deduplication defect** and
  it inflates every raw event count this system reports.

---

## 2. Raw event reaction

Every return is signed **by the event's own direction** — a SHORT-tagged event
whose stock falls produces a positive number. This measures "did the market move
the way the event said", not "did the price go up".

| Horizon | n | median | mean | win% | p10 | p25 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| +1m | 2,152 | −0.000 | −0.003 | 46.6% | −0.11 | −0.04 | 0.04 | 0.10 |
| +3m | 2,159 | −0.004 | −0.004 | 47.8% | −0.20 | −0.08 | 0.06 | 0.17 |
| +5m | 2,161 | −0.001 | +0.006 | 48.9% | −0.23 | −0.09 | 0.08 | 0.23 |
| +10m | 2,161 | −0.013 | −0.004 | 47.0% | −0.35 | −0.14 | 0.10 | 0.29 |
| +15m | 2,161 | −0.017 | −0.016 | 46.3% | −0.39 | −0.17 | 0.14 | 0.37 |
| +30m | 2,161 | −0.015 | −0.014 | 47.5% | −0.54 | −0.22 | 0.20 | 0.52 |
| +60m | 2,161 | −0.012 | −0.016 | 48.9% | −0.72 | −0.30 | 0.28 | 0.70 |
| EOD | 2,161 | −0.077 | −0.058 | 45.0% | −1.25 | −0.52 | 0.39 | 1.14 |

(The table above is the benchmark-adjusted series; raw and adjusted are within
noise of each other because the benchmark barely moved on most of these days.)

**Every horizon has a median indistinguishable from zero and a win rate below
50%.** There is no reaction to detect.

### Excursions — the decisive shape

| | n | median | mean | p10 | p25 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|---:|
| MFE % | 2,167 | +0.458 | +0.812 | 0.05 | 0.16 | 0.98 | 1.93 |
| MAE % | 2,167 | −0.496 | −0.830 | −1.86 | −1.06 | −0.19 | −0.06 |

```
time-to-MFE   median 47 min   p25 12   p75 127
time-to-MAE   median 47 min   p25 11   p75 126
```

**MFE and MAE are near-perfectly symmetric in magnitude and arrive at the same
median time.** That is the signature of a random walk. A directional edge would
show MFE exceeding MAE and arriving sooner.

---

## 3. Benchmark

Adjusted against **`NIFTYBEES.NS` 1m**, stock minus benchmark over the identical
window.

**Disclosure:** NIFTYBEES is a **proxy**, not the index. `^NSEI` has no 1m
candles in this database (5m only, from 09 Jun), so index-level 1m adjustment
was not possible. NIFTYBEES is an ETF and therefore carries tracking error, its
own bid-ask spread, and can trade at a premium or discount to NAV. Its own 1m
bars also appear *inside the event set* (see §7), which is a data-quality flag
in its own right. Benchmark was available on 22 of 22 sessions.

---

## 4. Direction

| | n | median | mean | win% |
|---|---:|---:|---:|---:|
| LONG +5m raw | 1,469 | +0.000 | +0.003 | 44.7% |
| LONG +15m raw | 1,469 | −0.018 | −0.022 | 43.4% |
| LONG +60m raw | 1,469 | −0.039 | −0.035 | 44.6% |
| **LONG EOD excess** | 1,467 | **−0.135** | −0.130 | **40.4%** |
| SHORT +5m raw | 698 | −0.000 | +0.022 | 48.3% |
| SHORT +15m raw | 698 | −0.000 | +0.002 | 46.0% |
| SHORT +60m raw | 698 | +0.041 | +0.027 | 55.2% |
| **SHORT EOD excess** | 694 | **+0.063** | +0.096 | **54.9%** |

The long side is actively wrong (40.4% hit rate). The short side is mildly right
(54.9%) but the magnitude (+0.063% median) is inside NSE round-trip costs.

---

## 5. Importance tiers — no gradient

| Tier | n | +15m median | +60m median | EOD excess median | EOD win% |
|---|---:|---:|---:|---:|---:|
| Tier 1 (imp ≥ 85) | 349 | −0.003 | 0.000 | −0.095 | 44.7% |
| Tier 2 (70–84) | 619 | −0.017 | −0.034 | −0.111 | 41.7% |
| Tier 3 (40–69) | 524 | −0.016 | −0.032 | −0.091 | 44.4% |
| Tier 4 (< 40) | 675 | −0.006 | +0.032 | −0.018 | 48.7% |

**The lowest-importance tier performs best.** The `importance` score carries no
information about reaction magnitude or direction — it is monotonically
*inverted*, weakly. This refutes the hypothesis that "the edge exists only for
high-impact events".

---

## 6. Event categories (≥25 observations, EOD excess)

| Category | n | median | mean | win% |
|---|---:|---:|---:|---:|
| EARNINGS | 730 | −0.092 | −0.021 | 45.2% |
| ORDER_WIN | 158 | −0.153 | **−0.245** | **37.3%** |
| FINANCIAL_RESULTS | 62 | −0.006 | +0.157 | 50.0% |
| PRODUCT_LAUNCH | 48 | −0.269 | −0.191 | 29.2% |
| PRICE_MOVEMENT | 44 | +0.224 | −0.068 | 61.4% |
| SECTOR_ROTATION | 43 | −0.346 | **−0.466** | **16.3%** |
| M&A | 43 | −0.488 | −0.352 | 30.2% |
| EARNINGS_SURPRISE | 42 | −0.112 | +0.219 | 45.2% |
| CREDIT_RATING | 41 | −0.309 | −0.187 | 39.0% |
| PRICE_MOMENTUM | 40 | −0.112 | +0.055 | 40.0% |
| REGULATORY_APPROVAL | 37 | −0.017 | −0.039 | 45.9% |
| GOVERNANCE_UPDATE | 32 | +0.125 | +0.025 | 59.4% |
| MARKET_MOVEMENT | 31 | −0.056 | −0.073 | 45.2% |
| ANALYST_DOWNGRADE | 30 | +0.208 | +0.158 | 56.7% |
| EARNINGS_BEAT | 29 | +0.032 | +0.078 | 51.7% |

`ORDER_WIN` — the category that should be the cleanest bullish catalyst in
Indian equities — is the **second-worst** performer at 37.3% and −0.245% mean.

The four categories with a positive tilt (`ANALYST_DOWNGRADE`,
`GOVERNANCE_UPDATE`, `EARNINGS_BEAT`, `PRICE_MOVEMENT`) all have n = 29–44 and
disagree between median, mean and win rate. None is evidence of anything at
this sample size.

---

## 7. Distribution shape — answering A/B/C/D/E/F

### Not driven by extremes

```
n=2,161   total excess = −125.0 pts   mean = −0.0578

top  1% ( 21 obs) contribute  +113.2    bottom  1% contribute  −124.4
top  5% (108 obs) contribute  +344.3    bottom  5% contribute  −345.9
top 10% (216 obs) contribute  +497.0    bottom 10% contribute  −508.1
top 25% (540 obs) contribute  +720.1    bottom 25% contribute  −775.3

mean after trimming the extreme 5% each side: −0.0644   (vs −0.0578 untrimmed)
share of observations with |excess| < 0.25%: 31.7%
```

Trimming the extremes **does not change the answer** (−0.058 → −0.064). The tails
are almost exactly symmetric. This is not a small number of movers dragging an
otherwise-positive result; it is broadly flat with symmetric noise.

### Not market beta

Everything above is already benchmark-adjusted. Raw and adjusted differ by less
than 0.02 pts at every horizon.

### Not consistent across sessions

| Sessions with positive mean excess | **10 / 21** |
|---|---|

A coin flip. Per-session means range from −0.621 (23 Jul) to +0.324 (29 Jul)
with no pattern.

**Verdict on shape:** none of A–F describes a positive result. The correct
description is *robustly nothing* — broad, symmetric, session-inconsistent noise
centred slightly below zero.

---

## 8. Classifier validation — 22 sessions

'Correct' = the stock's **excess** return moved the way the tag said. Base rate
= share of these same observations with positive excess, i.e. what an
always-LONG coin flip would score.

| Horizon | n | accuracy | balanced acc | LONG hit | SHORT hit | base rate | vs base |
|---|---:|---:|---:|---:|---:|---:|---:|
| +15m | 2,161 | 46.8% | 47.9% | 45.0% | 50.7% | 46.3% | **+0.5** |
| +60m | 2,161 | 45.1% | 44.8% | 45.6% | 44.1% | 48.9% | **−3.7** |
| EOD | 2,161 | 41.9% | 42.7% | 40.4% | 45.1% | 45.0% | **−3.1** |

Confusion matrix at EOD:

```
                 predicted
              LONG      SHORT
went up        592        381
went down      875        313
```

**The 24 Aug single-session figure (41.9% bullish accuracy) reproduces exactly
across 22 sessions (41.9% at EOD).** That one-day result was representative, not
noise. Phase 1 marked it STRONGLY SUPPORTED; it is now **CONFIRMED**.

### No high-confidence subset pays

| Subset | n | median | mean | win% |
|---|---:|---:|---:|---:|
| confidence ≥ 0.90 | 1,017 | −0.0692 | −0.0484 | 45.1% |
| confidence 0.80–0.89 | 983 | −0.0731 | −0.0483 | 46.0% |
| confidence < 0.80 | 161 | −0.1199 | −0.1755 | 38.5% |
| importance ≥ 85 **and** conf ≥ 0.85 | 345 | −0.0984 | −0.0301 | 44.3% |
| linked to a `news_item` | 393 | −0.0392 | −0.0443 | 46.3% |

The `confidence` field is uninformative: the ≥0.90 bucket performs the same as
the 0.80–0.89 bucket.

### Inverting the tag does not rescue it

Following the tag at EOD: median −0.0772%. Inverting: **+0.0772%**. NSE
round-trip cost (brokerage + STT + exchange + GST + a conservative spread) is
comfortably above 0.10%. An inverted classifier is still not tradable.

---

## 9. A hypothesis this study RULES OUT

Phase 1 proposed that the system "tags stocks after they have already moved".
Tested directly:

| Window before T_event | n | median | mean | already moved in tagged direction |
|---|---:|---:|---:|---:|
| −5m → T_event | 2,144 | 0.000 | +0.001 | 45.9% |
| −15m → T_event | 2,152 | 0.000 | +0.006 | 47.3% |
| −30m → T_event | 2,152 | −0.005 | +0.017 | 47.0% |
| −60m → T_event | 2,152 | 0.000 | +0.005 | 48.7% |
| session open → T_event | 2,152 | +0.002 | +0.017 | 50.0% |

**Pre-event drift is zero.** The stock has not moved in the tagged direction
before we tag it. It is flat before and flat after.

### Why this matters more than anything else in the document

If we were merely *late* to a real move, pre-event drift would be strongly
positive. It is not. So:

> **Latency is not the bottleneck.** Detecting these events faster would change
> nothing, because there is no move to be early to.

That directly answers the Phase 2 brief's §13 question. A 6.7-minute median news
latency is not the problem for *this* event set. Fixing it would deliver zero.

---

## 10. Examples

### Top 8 by EOD excess (direction was right)

| Symbol | T_event UTC | Type | imp | side | p0 | +1m | +5m | +15m | +30m | +60m | MFE | MAE | excess |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ANTELOPUS.NS | 07-28 04:30 | CORPORATE_GOVERNANCE | 5 | SHORT | 919.30 | +1.01 | +0.73 | +1.13 | +3.90 | +6.76 | +9.52 | +0.00 | **+9.55** |
| XPROINDIA.NS | 08-04 04:50 | EARNINGS | 15 | SHORT | 1282.50 | −0.12 | +0.50 | −0.03 | +0.58 | +2.46 | +9.67 | −0.41 | +7.58 |
| THERMAX.NS | 07-31 04:24 | CORPORATE_RESTRUCT | 35 | LONG | 3786.50 | −0.20 | −0.77 | −0.63 | +0.17 | +0.36 | +7.61 | −1.04 | +7.12 |
| ZAGGLE.NS | 08-20 03:54 | INVESTMENT | 85 | LONG | 185.99 | +1.25 | +2.69 | +4.98 | +5.01 | +1.87 | +7.36 | −0.11 | +6.58 |
| LAURUSLABS.NS | 07-27 03:45 | EARNINGS_SURPRISE | 88 | LONG | 1620.80 | +1.07 | +1.92 | +2.60 | +1.75 | +2.83 | +6.43 | −0.01 | +5.95 |
| PARADEEP.NS | 07-29 04:06 | EARNINGS | 72 | LONG | 147.70 | +1.25 | +4.77 | +4.34 | +2.48 | +2.65 | +7.99 | −0.06 | +5.75 |
| CONTROLPR.NS | 07-23 06:39 | FINANCIAL_RESULTS | 5 | SHORT | 613.25 | +0.33 | +2.35 | +5.95 | +7.06 | +5.78 | +8.59 | −0.08 | +5.60 |
| ZAGGLE.NS | 08-20 05:12 | INVESTMENT | 85 | LONG | 188.60 | −0.13 | +1.10 | +0.95 | +0.63 | +1.11 | +5.87 | −0.15 | +5.19 |

**The two best events in the entire study had importance 5 and 15** — the bottom
tier. That is not what a working importance score looks like.

### Bottom 8 by EOD excess (direction was wrong)

| Symbol | T_event UTC | Type | imp | side | p0 | +1m | +5m | +15m | +30m | +60m | MFE | MAE | excess |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| DHAMPURSUG.NS | 08-24 04:00 | SECTOR_MOMENTUM | 40 | LONG | 199.24 | +0.08 | −0.36 | −0.04 | −1.02 | −0.15 | +0.34 | −11.53 | **−9.69** |
| DHAMPURSUG.NS | 08-24 05:44 | SECTOR_MOMENTUM | 55 | LONG | 194.56 | +0.02 | +0.02 | −0.53 | −0.12 | −0.85 | +0.07 | −9.40 | −7.93 |
| DHAMPURSUG.NS | 08-24 07:27 | PRICE_MOVEMENT | 15 | LONG | 193.15 | −0.17 | −0.12 | −0.84 | −1.20 | −1.68 | +0.01 | −8.74 | −7.32 |
| THERMAX.NS | 07-31 05:33 | EARNINGS | 88 | SHORT | 3796.50 | −0.09 | −0.09 | −1.78 | −4.54 | −5.52 | +0.03 | −7.32 | −6.80 |
| CARTRADE.NS | 07-29 03:59 | PARTNERSHIP | 65 | LONG | 3007.70 | +0.08 | −0.98 | +1.16 | +0.29 | −0.32 | +2.03 | −8.90 | −6.43 |
| XPROINDIA.NS | 08-05 05:39 | EARNINGS | 30 | LONG | 1180.00 | +0.42 | +0.75 | −0.64 | −3.39 | −5.08 | +1.12 | −6.66 | −6.26 |
| KABRAEXTRU.NS | 07-31 04:09 | EARNINGS | 30 | SHORT | 359.15 | −0.79 | −1.13 | −3.56 | −4.40 | −5.40 | −0.10 | −7.28 | −6.10 |
| PCBL.NS | 07-30 03:46 | EARNINGS | 85 | LONG | 358.40 | −0.32 | −2.01 | −2.51 | −3.50 | −4.60 | +0.33 | −6.36 | −5.86 |

Two things to notice:

1. **DHAMPURSUG was tagged LONG three times on 24 Aug** (04:00, 05:44, 07:27)
   while the stock fell all day. The system re-confirmed a wrong thesis as the
   position deteriorated.
2. **THERMAX appears in both tables on the same day, 69 minutes apart** — tagged
   LONG at 04:24 (which paid +7.12) and SHORT at 05:33 (which lost −6.80). The
   classifier issued contradictory directional calls on one stock in one session.

### The typical event

| Symbol | T_event UTC | Type | imp | side | p0 | +1m | +5m | +15m | +30m | +60m | MFE | MAE |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NIFTYBEES.NS | 07-17 05:38 | Analysts recommend | 4 | LONG | 275.41 | +0.00 | −0.00 | +0.03 | +0.03 | +0.03 | +0.06 | −0.03 |
| ACI.NS | 07-31 06:13 | EARNINGS | 72 | LONG | 553.25 | +0.00 | −0.05 | −0.49 | −0.48 | +0.32 | +0.45 | −1.07 |
| ULTRACEMCO.NS | 08-24 06:31 | CREDIT_RATING | 10 | SHORT | 11543.00 | +0.07 | +0.19 | +0.11 | +0.27 | +0.07 | +0.37 | −0.26 |
| THYROCARE.NS | 07-24 06:38 | FINANCIAL_RESULTS | 68 | LONG | 558.40 | −0.37 | −0.17 | −0.90 | −0.72 | −1.81 | +0.68 | −2.76 |
| ITC.NS | 08-07 03:52 | EARNINGS | 72 | SHORT | 285.40 | −0.04 | +0.04 | +0.14 | +0.05 | +0.21 | +0.35 | −0.09 |
| MARUTI.NS | 08-03 06:24 | EARNINGS | 5 | SHORT | 14160.00 | +0.01 | −0.04 | +0.02 | +0.02 | +0.02 | +0.13 | −0.11 |

This is what the median event looks like: **nothing happens.** `MARUTI` tagged
SHORT with importance 5 moved 0.02% in an hour.

Note that **`NIFTYBEES.NS` — the benchmark ETF itself — appears in the event
set** as a tradable LONG. The event pipeline is emitting index instruments as
stock-level trade candidates.

---

## 11. Look-ahead audit

| Risk | Control |
|---|---|
| Future timestamps | Entry uses the last 1m bar at or **before** `T_event`; all measurement bars are strictly after it. |
| Hindsight event classification | `T_event` is `causal_events.created_at` — the row's own creation time. The classification existed at that instant by construction. |
| End-of-day values in the entry decision | No entry rule is applied at all in this document. Only observation. |
| Future volume / news / index | Benchmark bars are windowed identically to the stock's, strictly after `T_event`. |
| Outcome-dependent filtering | Every exclusion (S1–S8) is defined on availability or independence, never on the return. Counts and examples published above. |
| Survivorship | Instruments are resolved against the full `kite_instruments` master, not against a "still-trading" list. Names that resolve but lack 1m data are counted (215 mentions), not silently dropped. |
| Universe look-ahead | The event set is whatever the production classifier emitted at the time. No symbols were added retrospectively. |
| Duplicate inflation | S4 removes exact duplicates; S5 removes non-independent repeats. Both are counted. |

The one construct that is *not* look-ahead but should be stated: measuring from
`created_at` means this study answers **"could WE have traded these events"**,
not "could a perfect-latency system have traded them". §9 shows the distinction
does not matter here, because there is no pre-event drift to be early to.

---

## 12. Verdict

### D — NO EDGE, for the event set as currently produced

| Claim | Confidence |
|---|---|
| The `causal_events` stream has no measurable directional reaction at any horizon 1m→EOD, benchmark-adjusted, across 22 sessions and 2,167 independent observations | **CONFIRMED** |
| `importance` and `confidence` carry no information about reaction | **CONFIRMED** |
| Directional accuracy (41.9% at EOD) is below the always-LONG base rate (45.0%) | **CONFIRMED** |
| The result is not driven by extremes, not market beta, not category-specific | **CONFIRMED** |
| "We tag after the move has happened" | **RULED OUT** — pre-event drift is zero |
| Latency is the binding constraint | **RULED OUT** — nothing to be early to |
| *News events in Indian equities have no alpha* | **NOT TESTED** — see below |

### What this does NOT prove

This study tests the events **this system produces**, not the news-event
hypothesis. Three reasons the distinction is load-bearing:

1. **The event set is dominated by non-events.** `EARNINGS` is 730 of 2,167
   observations — generic "company reported results" mentions, not surprises.
   A category that fires on every quarterly filing cannot carry directional
   information.
2. **The classifier's direction is wrong more often than a coin flip.** Even a
   real underlying reaction would be invisible through a label that is
   anti-correlated with the outcome.
3. **`T_event` is our detection time.** §9 argues this does not matter, but the
   argument rests on pre-event drift measured *from the same possibly-mislabelled
   events*.

A clean test of the underlying hypothesis needs a **ground-truth event set built
from exchange filings** (NSE/BSE corporate announcements with their official
publication timestamps), not from this classifier's output. That is the next
study, and it is the only one that can move the verdict off D.

---

## 13. What follows from this

**Do not build on the current event stream.** No entry policy, no cost model, no
technical filter and no LLM improvement can extract signal from a label whose
directional accuracy is 41.9% against a 45.0% base rate.

The Phase 1 remediation list is unaffected for its *technical* items — the
confirmation gate, stop sizing and scan coverage are all still real defects
worth fixing. But the Phase 1 item **"wire the Hub to execute"** and the whole
news-only architecture rest on this classifier, and this study says that
foundation does not hold.

### Ranked next steps

1. **Build a ground-truth event set from NSE/BSE filings** with official
   timestamps, independent of the LLM classifier. Re-run this exact study
   against it. Until that is done, the alpha question is genuinely open, not
   answered.
2. **Fix news deduplication.** 2,785 of 19,241 mentions (14%) were the same
   story re-emitted within 60 minutes on the same instrument. Every event count
   this system reports is inflated by roughly that much.
3. **Stop emitting index instruments as stock events.** `NIFTYBEES`, `NIFTY50`,
   `NIFTYBANK` and sector indices appear as tradable candidates.
4. **Add a symbol alias/rename layer.** `TATAMOTORS` (146 mentions), `HDFC`
   (41), `ADANITRANS`, `GMRINFRA` are real companies whose tickers changed.
   Company-name resolution already recovers +858 symbols; a rename map would
   recover most of the remaining 16.7%.

### What NOT to build

- Any latency optimisation aimed at this event stream (§9).
- Any entry-policy tuning, cost model or technical-filter study on top of these
  events — there is no gross reaction for those to refine.
- A larger or smarter LLM for classification, before there is a ground-truth set
  to measure it against.

---

## Method and reproducibility

Scripts used, in order: `entity.py` (resolution funnel), `halluc.py` (unresolved
bucket decomposition), `elig.py` (S0–S5), `react.py` (S6–S8 + measurements),
`curves.py` (distributions), `predrift.py` (pre-event drift, concentration,
per-session), `clf.py` (confusion matrices, subsets).

Data: production Postgres — `causal_events`, `candles` (1m), `kite_instruments`,
`symbol_isin_map`, `news_items`. Window: events from 2026-07-16, 1m candles from
2026-06-18. Overlap yields 22 usable sessions.

*This is a systems and statistics analysis, not investment advice.*
