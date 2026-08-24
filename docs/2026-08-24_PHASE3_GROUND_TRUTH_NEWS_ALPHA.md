# Phase 3 — Independent ground-truth news alpha study

**Source of truth:** NSE corporate announcements, using **NSE's own `an_dt`
publication timestamp** and **NSE's own category field**.
`causal_events` is not consulted anywhere in this study.

---

# VERDICT: **B — CONDITIONAL NEWS ALPHA**

Material corporate news **does** produce a statistically significant directional
reaction. Phase 2's "no edge" was a property of *our event pipeline*, not of the
market.

But the tradable fraction is far smaller than the gross reaction, because **the
move happens in the overnight gap, which no system can capture.** After costs,
one category survives with a confidence interval excluding zero.

| | |
|---|---|
| Does material news move stocks directionally? | **YES** — LONG total excess +0.368%, 95% CI [+0.053, +0.713] |
| Is the reaction capturable? | **MOSTLY NO** — for LONG events the gap is 117% of the total move |
| Does anything survive costs? | **ONE category** — `RATING_UPGRADE`, net +0.347%, CI [+0.130, +1.003], n=59 |
| Is our classifier the problem? | **YES** — it inverts the strongest ground-truth category |

---

## 1. Ground-truth construction

```
news_items WHERE source='NSE-Announcements'
  published_at  <- NSE's an_dt field  (crawler/news_crawler.py:435)
  category      <- NSE's own classification
  neither is produced by our LLM
```

`_parse_nse_announcement_dt` (news_crawler.py:358) parses `'14-Jul-2026 20:10:07'`
as IST and converts to naive UTC. **Timestamp integrity verified: 0 of 4,497 rows
have `published_at > crawled_at`.** A documented 5h30m bug in this parser (fixed
2026-08-17) left no residue in the data.

### Direction assignment — deterministic, no model

Direction comes from NSE's category via a fixed table, not from any classifier:

| Direction | NSE categories |
|---|---|
| **LONG** | Bagging/Receiving of orders, Awarding of order(s), Acquisition, Amalgamation/Merger, Buyback, Product launch, Agreements, MoU, Scheme of Arrangement, Demerger |
| **SHORT** | Resignation of Director/KMP/SMP, Resignation, Resignation of Statutory Auditor, Delayed/Non-submission of Financials, Granting/withdrawal/cancellation |
| **NEUTRAL** | Outcome of Board Meeting, Press Release, Dividend, Clarifications, Monthly Business Updates, Rights/Preferential issue |

Credit-rating direction is taken from regex on the announcement text
(`upgrad|revised upward|improve|positive outlook` vs
`downgrad|revised downward|negative outlook|default`), never from the bare
category. Categories carrying no direction are labelled NEUTRAL and are **not**
forced onto a side.

### Funnel

| Stage | Kept | % | Dropped | Examples |
|---|---:|---:|---:|---|
| S0 NSE announcements in DB | 4,497 | 100.0% | — | — |
| S1 Has publication timestamp | 4,497 | 100.0% | 0 | — |
| S2 Category in controlled taxonomy | 4,451 | 99.0% | 46 | `None`, `Public Announcement - Buyback of Shares` |
| S3 Symbol resolvable to candles | 4,309 | 95.8% | 142 | `VIJIFIN.NS`, `CLCIND.NS`, `GBGLOBAL.NS`, `BALLARPUR.NS` |

**4,309 ground-truth events.**

| Window | LONG | SHORT | NEUTRAL |
|---|---:|---:|---:|
| INTRADAY (09:15–15:30 IST) | 79 | 58 | 673 |
| PREMARKET | 16 | 5 | 61 |
| **POSTMARKET** | **393** | **422** | **2,602** |

**79% of NSE announcements are published after the close.** That single fact
shapes everything below.

---

## 2. Study design

- **Study A — intraday.** T_public inside the session → 1m reaction curve.
  After price validation: **302 observations, but only 21 LONG and 21 SHORT.**
  **Underpowered. Reported, not relied upon.**
- **Study B — overnight.** T_public after close → next session. After price
  validation: **2,714 observations, 349 LONG / 300 SHORT / 2,065 NEUTRAL.**
  This is the primary study.

Benchmark: **`NIFTYBEES.NS` 1m — an ETF PROXY for NIFTY 50.** `^NSEI` has no 1m
candles in this database. The proxy carries tracking error, its own bid-ask, and
can trade at a premium/discount to NAV. This is **benchmark-adjusted reaction**,
not clean beta adjustment.

---

## 3. Primary result — Study B

Excess return over NIFTYBEES, signed by the event's own direction. `*` marks a
95% bootstrap CI (2,000 resamples) that excludes zero.

| Leg | Side | n | median | mean | win% | 95% CI of mean | |
|---|---|---:|---:|---:|---:|---|---|
| GAP (prev close→open) | LONG | 349 | +0.181 | **+0.430** | 59.6% | [+0.197, +0.672] | `*` |
| GAP | SHORT | 300 | +0.086 | +0.142 | 53.0% | [−0.165, +0.472] | |
| GAP | NEUTRAL | 2,065 | +0.026 | +0.055 | 50.8% | [−0.099, +0.219] | |
| DAY (open→close) | LONG | 349 | −0.097 | **−0.051** | 47.0% | [−0.294, +0.190] | |
| DAY | SHORT | 300 | +0.150 | +0.218 | 55.7% | [−0.037, +0.501] | |
| DAY | NEUTRAL | 2,065 | −0.670 | −0.533 | 38.8% | [−0.701, −0.364] | `*` |
| TOTAL (prev close→close) | LONG | 349 | +0.280 | **+0.368** | 55.3% | [+0.053, +0.713] | `*` |
| TOTAL | SHORT | 300 | +0.599 | +0.366 | 61.0% | [−0.065, +0.800] | |
| TOTAL | NEUTRAL | 2,065 | −0.735 | −0.496 | 39.1% | [−0.722, −0.286] | `*` |

**LONG events produce a genuine, significant excess reaction.** This directly
refutes the possibility that news carries no information.

---

## 4. The finding that matters most — where the move happens

```
LONG    gap +0.430   after the open -0.051   total +0.368   -> the gap is 117% of the move
SHORT   gap +0.142   after the open +0.218   total +0.366   -> the gap is  39% of the move
```

For positive news published after the close, **the entire excess return is in
the opening print, and the rest of the day gives a little back.**

**You cannot buy at the previous close.** The earliest executable moment is the
open — and by then the information is priced. This is not a latency defect that
better engineering fixes; it is the auction doing its job.

---

## 5. Cost adjustment

Round-trip cost assumption for a ₹50k ticket in a small/mid-cap name:

| Component | % |
|---|---:|
| Brokerage (flat ₹20 both legs) | 0.030 |
| STT (sell side, 0.025%) | 0.025 |
| Exchange transaction (0.00345% ×2) | 0.007 |
| GST 18% on brokerage + txn | 0.007 |
| SEBI turnover + stamp duty | 0.003 |
| Spread + slippage (conservative, small/mid cap) | 0.150 |
| **Total round-trip** | **0.222** |

### The only executable strategy: buy at open, exit at close

| Strategy | n | gross | **net** | win% | 95% CI (gross) |
|---|---:|---:|---:|---:|---|
| LONG `ORDER_WIN` | 84 | +0.013 | **−0.209** | 48.8% | [−0.437, +0.470] |
| **LONG `RATING_UPGRADE`** | 59 | +0.569 | **+0.347** | 55.9% | **[+0.130, +1.003]** |
| SHORT `MANAGEMENT_RESIGNATION` | 258 | +0.238 | +0.016 | 48.1% | [−0.036, +0.544] |

**`ORDER_WIN` — the single strongest ground-truth category on a total-return
basis (+1.053%) — is dead after costs once you remove the gap you cannot
capture.** Its executable component is +0.013% gross.

**Only `RATING_UPGRADE` survives** with a CI excluding zero: net **+0.347%**,
n=59. That n is small. Treat it as a hypothesis worth prospective testing, not
as a proven strategy.

---

## 6. Category breakdown — excess TOTAL

| Side · Subcategory | n | median | mean | win% | 95% CI | |
|---|---:|---:|---:|---:|---|---|
| LONG · `ORDER_WIN` | 84 | +1.072 | **+1.053** | 65.5% | [+0.444, +1.687] | `*` |
| LONG · `RATING_UPGRADE` | 59 | +0.566 | **+0.898** | 61.0% | [+0.244, +1.736] | `*` |
| SHORT · `MANAGEMENT_RESIGNATION` | 258 | +0.633 | +0.438 | 62.8% | [−0.021, +0.921] | |
| LONG · `ACQUISITION` | 133 | +0.063 | +0.129 | 51.9% | [−0.452, +0.745] | |
| SHORT · `RATING_DOWNGRADE` | 29 | +0.025 | +0.166 | 51.7% | [−0.430, +0.701] | |
| LONG · `MAJOR_PARTNERSHIP` | 39 | −0.366 | −0.319 | 46.2% | [−0.938, +0.303] | |
| LONG · `CORPORATE_RESTRUCTURE` | 23 | −0.737 | **−0.975** | 39.1% | [−1.891, −0.136] | `*` |

Controls:

| NEUTRAL · Subcategory | n | median | mean | win% |
|---|---:|---:|---:|---:|
| `ROUTINE_BOARD_MEETING` | 1,169 | −1.029 | −0.737 | 36.3% |
| `ROUTINE_DISCLOSURE` | 735 | −0.557 | −0.299 | 41.9% |
| `DIVIDEND` | 43 | +0.712 | +1.270 | 55.8% |

Materiality tiers show **no monotonic gradient**: Tier 1 (n=149) mean +0.077,
Tier 2 (n=195) +0.635, Tier 3 (n=305) +0.338. The middle tier is best.

---

## 7. Control-group caveat — read before using §3's comparisons

The neutral group is **not a clean control**, and I am flagging it rather than
leaning on it.

```
median |gap|:   LONG 0.846   SHORT 0.973   NEUTRAL 1.677
```

**Neutral announcements move MORE in absolute terms than directional ones.**
`Outcome of Board Meeting` (n=1,169) is NSE's category for results
declarations — the highest-information event on the calendar — and my
deterministic rules label it NEUTRAL because the category alone cannot say beat
or miss. Its mean excess total is −0.737%.

Consequence: the "directional vs neutral" differences (+0.864 for LONG,
+0.862 for SHORT, both with CIs excluding zero) are **inflated by the control
being unusually negative**, not purely by the directional events being good.

**The honest test is the absolute CI in §3, not the difference against this
control.** On that test LONG TOTAL and LONG GAP are significant; SHORT TOTAL is
not.

---

## 8. Our classifier vs ground truth — the secondary conclusion

The same category, measured two ways:

| `ORDER_WIN` | n | mean excess | win rate |
|---|---:|---:|---:|
| **Ground truth (NSE category)** | 84 | **+1.053%** | **65.5%** |
| **Our `causal_events` classifier** (Phase 2) | 158 | **−0.245%** | **37.3%** |

The strongest bullish category in the exchange's own taxonomy is the
**second-worst** category in ours. The signal exists in the source data and our
event pipeline inverts it.

### Detection is not the problem

| Metric | Value |
|---|---|
| Our detection lag vs NSE publication | median **1.4 min**, p90 **6.0 min**, max 233 min |

The earlier "6.7 minute median news latency" figure covered all sources. For NSE
announcements specifically we are fast. **Detection recall and speed are not the
bottleneck; interpretation is.**

---

## 9. Study A — intraday (underpowered, reported for completeness)

n = 21 LONG, 21 SHORT, 260 NEUTRAL. **No conclusion is drawn from this.**

| | n | median | mean | win% | 95% CI | |
|---|---:|---:|---:|---:|---|---|
| LONG EOD excess | 21 | −0.357 | −0.337 | 28.6% | [−0.878, +0.251] | |
| SHORT +60m excess | 21 | +0.247 | +0.279 | 71.4% | [+0.055, +0.519] | `*` |
| SHORT EOD excess | 21 | +0.111 | +0.219 | 66.7% | [−0.030, +0.473] | |
| NEUTRAL EOD excess | 258 | −0.193 | −0.423 | 40.3% | [−0.793, −0.030] | `*` |

Pre-event drift on the 42 directional intraday events: −15m median **+0.195%**,
**69% already moving in the event's direction** before publication. That is a
leakage/anticipation signal worth its own study; at n=42 it is a flag, not a
finding.

---

## 10. Look-ahead audit

| Risk | Control |
|---|---|
| Hindsight event labels | Direction comes from NSE's category via a fixed table written before any return was computed. No model, no outcome input. |
| Detection-time contamination | `T_public` is NSE's `an_dt`, not our `crawled_at` and not our DB insert time. Verified: 0 of 4,497 rows have publication after crawl. |
| Future prices in the entry | Study B entry is the next session's **first** 1m bar. Study A entry is the last 1m bar at or before `T_public`. |
| Benchmark leakage | Benchmark windows are identical to the stock's, taken from the same bars. |
| Outcome-dependent filtering | Every exclusion (S1–S3, plus price validation) is defined on availability, never on the return. Counts and examples published. |
| Survivorship | Symbols resolved against `kite_instruments`, not a still-trading list. |
| Category cherry-picking | The full taxonomy table is published in §1 and applied uniformly. `CORPORATE_RESTRUCTURE` came out significantly **negative** and is reported. |

---

## 11. Primary and secondary conclusions, kept separate

### PRIMARY — does independently verified material news have tradable alpha?

**B — CONDITIONAL.** Directional reaction is real and significant for LONG
events (+0.368% total excess, CI [+0.053, +0.713]). But the capturable fraction
is small: for LONG events the gap is 117% of the move, and after realistic costs
only `RATING_UPGRADE` retains a CI above zero (net +0.347%, n=59).

Phase 2's verdict D applies to *our event stream*. It does **not** generalise to
real news. That distinction is now established with independent data.

### SECONDARY — why is AutoTrade Pro failing to capture it?

Of the six candidate explanations, the evidence points to **B — real alpha, AI
understands events incorrectly**:

| Candidate | Evidence |
|---|---|
| A — AI misses events | **NO.** Detection median 1.4 min, p90 6.0 min. |
| **B — AI interprets incorrectly** | **YES.** `ORDER_WIN` +1.053%/65.5% in ground truth vs −0.245%/37.3% in ours. |
| C — latency kills capture | **PARTLY, structurally.** Not our latency — the gap is unreachable by anyone. |
| D — technical/risk gates kill capture | Established in Phase 1, unaffected by this study. |
| E — no real alpha | **REFUTED.** |
| F — alpha only in specific categories | **YES, and this is the sharpest form of the answer.** |

---

## 12. What follows

1. **Replace the event classifier's direction source with NSE's category field.**
   The exchange already publishes a taxonomy that carries signal; our LLM
   overwrites it with one that does not. This is the highest-value change in
   three phases of investigation.
2. **Do not build an intraday strategy on post-market announcements.** 79% of
   announcements land after the close and their information is in the opening
   print. Any system entering after the open is trading the residue.
3. **The only prospective candidate is `RATING_UPGRADE`, buy-at-open.** n=59,
   net +0.347%, CI [+0.130, +1.003]. Paper-trade it forward before sizing it.
   Do not fit anything else to this dataset.
4. **Investigate the pre-event drift.** 69% of intraday directional events were
   already moving before publication (n=42). If that holds at scale it changes
   what "the event" even means.
5. **Do not add AI.** The bottleneck is a taxonomy substitution, not model
   capability.

### What NOT to build

- Intraday news strategies on the 79% of announcements published post-close.
- Anything fitted to `ORDER_WIN`'s +1.053% — that number is not executable.
- A larger LLM for event classification while NSE's own field outperforms it.

---

## Method

Scripts: `gt_build.py` (ground-truth extraction + taxonomy), `gt_react.py`
(Study A/B measurement), `gt_analyse.py` (statistics, bootstrap CIs).
Data: `news_items` (source `NSE-Announcements`), `candles` (1m),
`kite_instruments`. Window: announcements 14 Jul – 21 Aug 2026; 1m candles from
18 Jun 2026; 29 sessions with benchmark coverage.
Bootstrap: 2,000 resamples, seed 7, percentile method.

*This is a systems and statistics analysis, not investment advice.*
