# PHASE 23 — ENTRY QUALITY + OPPORTUNITY-TO-EXECUTION ROOT CAUSE

**Mode:** local development. Cost model corrected locally; **nothing deployed**.
No strategy threshold, R:R, TOP_N, capital limit, exit, prompt, Master Score or
BUG-1 was touched.

---

## 1. EXECUTIVE VERDICT

> **The first material loss of edge occurs at the opportunity itself, not
> anywhere in the conversion chain. The chain is not where the money goes.**

Measured across 5 sessions, 576 matched opportunity→signal pairs:

| Stage | n | median 60m MFE |
|---|---:|---:|
| at opportunity `t0` | 576 | **0.394%** |
| at tactical signal | 574 | **0.299%** |
| at actual entry | 48 | **0.338%** |

**Alpha decay t0 → entry: 0.056pp.** The pipeline delivers essentially the whole
excursion it started with. Signal→entry is **negative** decay — entries land
marginally better than the signal price.

Execution is excellent, not deficient:

| | |
|---|---|
| signal → entry latency | **median 0.6 min** (p75 1.6 min) |
| signal → entry price slippage | **median +0.019%** (p75 +0.022%) |

**And a defect that invalidates Phase 22's headline: `paper_trades.mfe_pct` is
broken.** Phase 22 concluded "entry quality is the dominant constraint, median
intraday TACTICAL MFE = 0.00%". That number came from a field that
systematically under-records.

---

## 2. The `mfe_pct` defect — CONFIRMED

Validated 38 intraday TACTICAL trades against the candles over each trade's own
holding window:

| | stored `mfe_pct` | candle-derived |
|---|---:|---:|
| median | **0.010%** | **0.353%** |
| mean | 0.364% | 0.956% |

**17 of 38 store exactly `0.00`. Eleven of those 17 had more than 0.1%
favourable movement.** Worked examples:

| Symbol | stored | candle | window |
|---|---:|---:|---|
| PAYTM.NS | 0.00% | **5.31%** | 09:20–15:07 |
| BALRAMCHIN.NS | 0.00% | **3.00%** | 09:20–15:06 |
| DHAMPURSUG.NS | 0.00% | 1.62% | 14:44–15:21 |

### Root cause

`trade_simulator.py:616-620` reads `peak_upnl` from a running tracker written at
`:1290-1305`, which lives **inside `update_positions_with_current_prices()`**,
called from `india_trade_loop` at `india_tasks.py:542`.

MFE is therefore **sampled**, not measured — bounded by two things both already
measured as degraded:

- **trade-loop cadence** — Phase 15 measured 53.7% coverage with gaps to 605s
- **price freshness** — Phase 16 measured p50 16-minute candle lag

A peak that occurs between samples is never recorded. → **STRONGLY SUPPORTED**
(the mechanism is proven; the split between the two causes is not).

**This is the answer to PART 9's question — candle freshness affects more than
the news/AI path. It corrupts the MFE metric the entire strategy is judged on.**

---

## 3. PART 3 — which of A–H is happening

| | Verdict | Evidence |
|---|---|---|
| **A. Opportunity itself is weak** | **CONFIRMED — this is it** | Median capturable 60m return for *signalled* opportunities is **+0.092%** against a corrected cost floor of **0.11%**. Under water at entry, arithmetically. |
| B. Signal arrives late | **PARTIALLY SUPPORTED** | Median t0→signal 31 min, price already +0.241%. But p25 is **−49 min** — many signals *precede* t0. |
| C. Ranking delays the trade | **RULED OUT** | signal→entry median 0.6 min end to end. |
| D. Risk validation delays | **RULED OUT** | Same 0.6 min covers risk + capital + fill. |
| E. Capital validation delays | **RULED OUT** | Same. |
| F. Entry price much worse than opportunity | **RULED OUT** | signal→entry slippage +0.019%. |
| G. Signal selects poor setups | **RULED OUT** | Signalled beat unsignalled at every horizon; MFE bootstrap CI excludes zero. |
| **H. Multiple** | **partially** | A dominates; B contributes in the tail. |

---

## 4. Entry latency

n=576 (t0→signal), n=48 (signal→entry).

| Leg | p25 | median | p75 | p90 |
|---|---:|---:|---:|---:|
| t0 → signal (min) | **−49** | **31** | 198 | 252 |
| signal → entry (min) | 0.2 | **0.6** | 1.6 | — |

The negative p25 matters: **a quarter of signals fire before the breakout event
they would be "chasing"**. These are different setups, not late entries. The
long right tail (p90 = 252 min) is a genuinely different population and is where
B lives.

---

## 5. Price decay / chase

| Leg | median | p75 | p90 |
|---|---:|---:|---:|
| t0 → signal price | **+0.241%** | +0.935% | **+1.955%** |
| signal → entry price | **+0.019%** | +0.022% | — |

**Proposed breakpoints — labelled as proposed, derived from this distribution,
not invented:**

| Class | t0→entry move | share |
|---|---|---|
| EARLY | < 0% | ~25% (the negative-latency population) |
| ACCEPTABLE | 0 – 0.24% (to the median) | ~25% |
| LATE | 0.24 – 0.94% (median to p75) | ~25% |
| CHASE | > 0.94% | ~25% |

Roughly a quarter of entries are chasing, and the p90 chase of +1.955% consumes
five times the corrected cost floor before the position is even open.

---

## 6. Corrected simulator P&L reconciliation

Implemented locally: `estimate_trade_cost(qty, price, side, product="CNC")` in
**both** `trade_simulator.py:133` and the duplicate at
`engine/agent/backtester.py:22`. Call sites at `:533,538,619,624` now pass
`getattr(trade, "product", None) or "CNC"`.

| | ₹ |
|---|---:|
| Gross P&L (from stored prices) | 9,633 |
| As charged | 806 |
| **Corrected** | **4,393** |
| **Charging defect** | **+3,588** |

**Historical rows were NOT rewritten.** The corrected figure is derived from
`entry_price`, `exit_price`, `size_units` and `pnl`, so old and corrected P&L
remain simultaneously auditable.

**Tests:** 14 new (`test_product_aware_costs.py`) — MIS ≠ CNC for identical
notional; zero-move MIS lands in 0.08–0.16%; zero-move CNC preserved at
0.28–0.31% (production charged 0.294%); default and unknown product fall back to
delivery; MIS charges STT on the sell leg only; the backtester copy agrees
byte-for-byte; call sites actually pass the product.

**Suite: 1,791 passed / 27 failed / 7 skipped / 5 errors** against the Phase-21
baseline of 1,777/27/7/5 — **+14, zero new failures.**

**Not deployed.** The cost flows `:507 partial_pnl → VirtualWallet.return_margin
→ wallet_balance → risk_manager sizing`, so correcting it raises the wallet
~0.7% and permits marginally larger positions. Real, small, and a behaviour
change.

---

## 7. Edge retention funnel

Median 60-minute **capturable** return (not MFE — MFE cannot be captured):

| Stage | count | median return | retention |
|---|---:|---:|---:|
| t0 opportunities | 1,105 | **−0.062%** | — |
| → signalled | 80 | **+0.092%** | selection *adds* edge |
| → traded | 28 | — | — |
| corrected cost floor | — | **−0.110%** | **wipes it out** |
| → net | | **−0.018%** | **retention ≈ 0** |

MFE-based retention, separately: 0.394% → 0.299% → 0.338% = **86% retained from
t0 to entry**.

**The two retentions disagree, and that is the whole finding.** The pipeline
retains 86% of the *peak* excursion and 0% of the *economics*, because the peak
was never capturable at this holding horizon.

---

## 8. Intraday vs multi-day — revisited

Phase 22 explained this with median MFE 0.00% vs 0.48%. **That comparison used
the broken field.** With candle-derived MFE, intraday TACTICAL is **0.353%**, not
0.00%.

The corrected P&L split still holds: intraday **−₹1,742** (n=47), multi-day
**+₹7,155** (n=14). But the differentiating feature is **NOT MEASURED** — the
explanation Phase 22 gave is withdrawn, and no replacement is offered because
the analysis rested on the defective metric.

---

## 9. TOP-N discarded candidates

**NOT MEASURED and not implemented this phase.** Ranks beyond the cut are
discarded at `tactical_executor.py:218` before any write; max signals ever
persisted in one scan-minute is 30 across all history (two pipelines × 15).

PART 7's telemetry was **not built** — the phase's budget went to establishing
that the conversion chain is not the bottleneck, which changes whether that
telemetry is worth building at all.

---

## 10. ROOT-CAUSE MATRIX

| Issue | Evidence | Impact | Confidence | Safe fix? | Strategy change? | Expected benefit | Risk |
|---|---|---|---|---|---|---|---|
| **`mfe_pct` under-records** | 17/38 store 0.00; PAYTM 0.00 vs 5.31% | corrupts every MFE conclusion | **CONFIRMED** | **yes** | no | correct measurement | low |
| **Opportunity economics marginal** | +0.092% vs 0.110% floor | **the bottleneck** | **CONFIRMED** | no | **yes** | — | — |
| **Cost model ignores product** | MIS = CNC at 0.294% | ₹3,588 | **CONFIRMED** | fixed locally | mild (wallet) | truthful P&L | low |
| Backtester duplicate | same defect | disagreement | **CONFIRMED** | fixed locally | no | consistency | none |
| t0→signal chase tail | p90 +1.955% | ~25% of entries | **PARTIALLY** | no | yes | — | — |
| Execution quality | 0.019% slippage, 0.6 min | none | **RULED OUT** | — | — | — | — |
| Ranking/risk/capital latency | inside 0.6 min | none | **RULED OUT** | — | — | — | — |
| Signal selection | CI excludes zero on MFE | positive | **CONFIRMED good** | — | — | — | — |
| Ranks 16–40 | never persisted | unknown | **NOT MEASURED** | telemetry | no | — | none |
| Intraday vs multi-day cause | rested on broken field | unknown | **NOT MEASURED** | — | — | — | — |

---

## 11. FINAL RECOMMENDATION

### SAFE ENGINEERING FIXES
1. **Fix `mfe_pct`** — compute MFE from candles at close instead of from a
   sampled tracker. Pure measurement; changes no decision. **Highest value in
   this list**, because every strategy conclusion depends on it.
2. **Deploy the product-aware cost model** under its own gate, with wallet
   reconciliation. Done locally, tested, not deployed.

### MEASUREMENT / TELEMETRY
3. Re-run this audit on 2026-08-27 with Phase-21 funnel telemetry live.
4. Repeat the 80-vs-1,025 test over 10+ sessions — the *return* edge CI includes
   zero at n=80.
5. Persist discarded ranks 16–40 (research only) **only if** a later phase shows
   ranking matters. On this evidence it does not.

### STRATEGY EXPERIMENTS — design only, none run
6. The opportunity rule itself is the thing to change: median capturable return
   +0.092% against a 0.110% floor. Either the entry trigger must select a
   better sub-population, or the holding horizon must extend past 60 minutes —
   note multi-day is the only profitable population on record.

### DO NOT CHANGE YET
`TACTICAL_TOP_N` · turnover floor · R:R · capital limits · exits · AI routing ·
BUG-1 · Master Score · prompts. **None of them is where the edge is lost.**

---

## 12. V1 REDESIGN — not recommended

The proposed architecture (detector → queue → validator → score → AI → risk →
capital → execution) addresses **conversion**, and conversion is not the
problem: 86% MFE retention, 0.6-minute entries, 0.019% slippage.

**A redesign of the conversion pipeline would rebuild the part that works.**

What the evidence supports instead is narrower and cheaper: fix the two
measurement defects, then re-examine the opportunity trigger and holding horizon
against honest numbers.

---

## Classification

| Conclusion | Status |
|---|---|
| First material edge loss is at the opportunity | **CONFIRMED** |
| Conversion chain retains 86% of MFE | **CONFIRMED** |
| Execution / ranking / risk / capital latency material | **RULED OUT** |
| `paper_trades.mfe_pct` under-records | **CONFIRMED** |
| Cause is sampling cadence + price staleness | **STRONGLY SUPPORTED** |
| Phase 22's "median intraday MFE 0.00%" | **WITHDRAWN** — 0.353% from candles |
| Phase 22's intraday-vs-multi-day explanation | **WITHDRAWN** |
| Cost model ignores product | **CONFIRMED**, fixed locally |
| Signal selection has MFE edge | **CONFIRMED** |
| Signal selection has *return* edge | **INSUFFICIENT EVIDENCE** (n=80) |
| Ranks 16–40 value | **NOT MEASURED** |
| Architecture redesign warranted | **RULED OUT** on current evidence |

---

*Local development only. Nothing deployed, no order placed, no historical row
rewritten.*
