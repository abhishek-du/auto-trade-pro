# PHASE 2L — POINT-IN-TIME / REPLAY / PREDICTION READINESS

**Date:** 2026-09-08 · Read-only except the authorised replay `as_of` fix.
No 18:30 migration, no model trained, no production prediction logic built.

---

## 1. Executive Verdict

# YELLOW

The one **confirmed** look-ahead is fixed and quantified (§2), and every daily
caller is now classifiable with evidence (§3). No known leak remains in the
price path.

It is not GREEN because three required feature families cannot be proven
point-in-time, and one of them is provably **incapable** rather than merely
undocumented:

* **Fundamentals — FAIL.** `fundamental_data` holds exactly **one current row
  per symbol** (1,217 rows / 1,217 symbols), overwritten in place, with no
  period-end date, no publication date and no historical snapshots.
* **Sector/industry — FAIL.** No sector column and no sector table exist
  anywhere; classification is resolved at runtime, so any historical use applies
  today's sector retroactively.
* **Pre-open — FAIL (absent).** No IEP/indicative table exists at all.

It is not RED because no historical prediction dataset exists yet to be
contaminated — the leak that did exist (replay entry pricing) has been corrected.

---

## 2. Replay PIT Remediation

### The hazard, reproduced

```
target = 2026-09-06 (Sunday)
  unbounded -> 1309.5   (the Sep 7 close — a session AFTER the target)
  as_of     -> 1322.0   (Sep 4 — the last session actually known by then)
```

### Lookups classified

| line | call | role | change |
|---|---|---|---|
| 177 | `_close_near(symbol, as_of.date())` | **ENTRY** price at the cutoff | **bounded** `as_of=` |
| 127 | `_close_near(NIFTY_SYMBOL, as_of.date())` | **ENTRY** benchmark | **bounded** |
| 128 | `_close_near(sector_index, as_of.date())` | **ENTRY** sector benchmark | **bounded** |
| 133 | `_close_near(symbol, exit_date)` | exit/outcome | unchanged — deliberately forward-looking |
| 140 / 144 | nifty / sector exits | outcome | unchanged |
| 150 | `_candles_between(as_of, exit_date)` | MFE/MAE over the hold | unchanged, already bounded by `exit_date` |

Strategy logic, exit logic, labels and prices were not touched. `_close_near`
gained a keyword-only `as_of`; only the three entry sites pass it.

### Regression

| target | case | unbounded | bounded | |
|---|---|---|---|---|
| 2026-09-06 | Sunday (the hazard) | 1309.5 | **1322.0** | corrected |
| 2026-09-03 | ordinary weekday | 1302.5 | 1302.5 | unchanged |
| 2026-09-05 | Saturday | 1322.0 | 1322.0 | unchanged |
| 2026-04-03 | Good Friday (holiday) | 1350.5 | 1350.5 | unchanged |
| 2026-02-01 | Budget Sunday (special session) | 1347.0 | 1347.0 | unchanged |

### Do historical outputs change? Yes — quantified, not hidden

Every calendar date over a year, RELIANCE:

| | |
|---|---|
| dates tested | 366 |
| identical | 315 |
| **changed by the bound** | **51 (13.9%)** |
| by weekday | **50 Sundays + 1 Monday** (a holiday) |
| largest entry-price delta | **−3.41%** (2025-10-19) |

The leak fires only when the target falls on a non-session day whose *next*
session is nearer than its previous one — every Sunday, and holidays followed
sooner than preceded. Weekdays are unaffected.

**Any replay run whose cutoff landed on a Sunday priced its entry from the
following Monday.** Those runs' returns were computed against a price the engine
could not have known; they should be re-run before being used as evidence.

Tests: `tests/test_pre_event_gap_phase5.py` — 11 passed. One stub there needed
its signature widened to accept the new keyword (a seam change, not a behaviour
change).

---

## 3. Caller-Level PIT Audit

| file / function | data source | cutoff used | class | evidence |
|---|---|---|---|---|
| `market_regime.get_market_regime` | daily NIFTYBEES | `include_current=True` | **C** live current-session, explicit | code + 2K tests |
| `morning_regime._fetch_regime_inputs` | daily NIFTYBEES + hub 200 | closed-only default | **B** | 210 sessions, 0 dupes |
| `momentum_filter._compute_ranks` | daily, bulk | closed-only default | **B** | 64 sessions/symbol |
| `breakout_screener` | daily OHLCV, bulk | closed-only default | **B** | 25 sessions |
| `momentum_screener` | daily OHLCV, bulk | closed-only default | **B** | 37 sessions |
| `intelligence_hub` | daily NIFTYBEES | closed-only default | **B** | EMA200 label |
| `performance_engine._aligned_closes` | daily | closed-only; `as_of` available | **B** | Sep 2/3/4 = 1313.1/1302.5/1322.0 |
| `portfolio_analytics.get_nifty_return` | daily ^NSEI | closed-only; ≥60-session floor | **B** | refuses rather than annualising a short window |
| `india_specific._fetch_candle_prices` | daily | closed-only default | **B** | |
| `corporate_actions` | daily + 1m | `basis="raw"`, live by design | **C** | compares D close to D+1 open |
| **`replay`** | daily | **`as_of` on all entry lookups** | **A** | §2 |
| `ml_predictor.train_all_models` | daily bars | **none passed** | **D → must pass `as_of`** | parameter exists; unused |
| Fundamentals consumers | `fundamental_data` | **no date semantics exist** | **E** | §4 |
| Sector consumers | runtime lookup | **no historical membership** | **E** | §5 |

Class **E** is not treated as safe anywhere in this report.

---

## 4. Fundamentals — **FAIL**

`fundamental_data`: **1,217 rows across 1,217 distinct symbols — exactly 1.00
row per symbol.** A single current record per company, overwritten in place.

| column present | pe_ratio, pb_ratio, roe, roce, debt_to_equity, current_ratio, revenue_growth_3yr, profit_growth_3yr, promoter_holding, fii_holding, pledged_pct, market_cap_cr, dividend_yield, fundamental_score |
|---|---|
| **date columns** | **`last_updated` only** (2026-06-12 .. 2026-09-06) — an *ingestion* timestamp |
| period-end date | **absent** |
| publication / filing date | **absent** |
| historical snapshots | **absent** |

Per the requested field list:

| field | status |
|---|---|
| P/E, P/B, ROE, ROCE, debt/equity, market cap, dividend yield | present as a **current value only**, no as-of date |
| revenue, EBITDA, EBIT, PAT, EPS, margins, cash, absolute debt | **not stored at all** |
| guidance, earnings surprise, revenue surprise, EBITDA surprise | **not stored** (7 rows of free-text `earnings_call_summaries` only) |

**Consequence:** joining any of these to a historical session applies a value
captured in mid-2026 to a 2016 row. That is both look-ahead and survivorship
bias. The brief's rule — a period ending March 31 was not known on March 31 —
cannot even be evaluated here, because no period-end or publication date is
stored. **UNPROVEN would be generous; this family is FAIL for historical use.**

---

## 5. Sector / Industry — **FAIL**

No sector or industry column exists in `hub_universe` (1,775 rows) or
`kite_instruments` (25,214 rows); `stock_info` and `sector_performance` do not
exist. Sector is resolved at runtime (yfinance / an in-process `SECTOR_CACHE`)
and never persisted with a date.

There is therefore **no historical sector membership**, and any historical use
would retroactively apply today's classification — exactly what the brief
forbids. **FAIL.**

---

## 6. News / Events — **PASS (short history)**

| table | rows | published/effective | captured | PIT |
|---|---|---|---|---|
| `news_items` | 42,603 | `published_at` 2026-04-07 → 2026-09-08 | `crawled_at` | **PASS** |
| `sse_announcements` | 96 | `ann_date`, `ann_tstamp` 2026-06-19 → | `crawled_at` | **PASS** |
| `market_events` | 2,996 | `event_date` 2026-01-26 → 2027-02-03 | `created_at` | **PASS** |
| `premarket_news_queue` | 9,557 | — | `captured_at` 2026-07-13 → | **PASS** |
| `causal_events` | 14,456 | **`created_at` only** | `created_at` | **UNPROVEN** — no separate publication time |
| `earnings_call_summaries` | 7 | `call_date` | `created_at` | PASS but negligible volume |

Both a publication and a capture timestamp exist for the main news paths, so a
cutoff can be enforced. **But the usable history is ~5 months** (`published_at`
from 2026-04-07), which bounds any news-derived feature.

`causal_events` carries only `created_at`; a feature built from it cannot
distinguish publication from ingestion — **UNPROVEN**.

---

## 7. FII / DII — **PASS, but negligible history**

`india_signal_generator` applies `FIIDIIFlow.date <= bar_date` consistently, and
threads `bar_date` into news sentiment and VIX as well. The `date` column is a
plain date, `created_at` records ingestion — no timezone ambiguity.

**No leak found; no change made.** However `fii_dii_flows` holds **43 rows,
2026-06-12 → 2026-09-07** — roughly three months. Correct, but too short to
support a multi-year training set.

---

## 8. Pre-Open Readiness — **FAIL (absent)**

No table matching `preopen`, `pre_open`, `iep` or `indicative` exists. The only
reference in code is `engine/nse_crawler.py:108` reading `preOpenMarket` from a
live NSE response for immediate use — **nothing is persisted.**

| field | stored |
|---|---|
| IEP, indicative qty, buy/sell qty, imbalance | **no** |
| pre-open price, previous close, gap | **no** |
| index pre-open context | **no** |
| collection timestamp | **no** |

The target architecture's "PRE-OPEN D+1 information → optional updated
prediction" stage has **no data foundation whatsoever**, historical or live.
Recorded as a future data-foundation requirement; not implemented here.

---

## 9. Target / Label Feasibility

| target | requires | available | status |
|---|---|---|---|
| **1 — next-session return** `close[D+1]/close[D]-1` | daily close | 1d from 2016-01-01, 4,400 symbols | **PASS** |
| **2 — next-session high upside** `high[D+1]/close[D]-1` | daily high | same | **PASS** |
| **3 — MFE before reversal** | intraday path within D+1 | **1m only from 2026-06-18** (~3 months) | **FAIL for history**, PASS for ~3 months |
| **4 — adverse excursion (MAE)** | daily low, or intraday | daily **PASS**; intraday-precision ~3 months | **PASS (daily) / limited (intraday)** |
| **5 — time-to-peak** | intraday timestamps | 1m ~3 months | **FAIL for history** |

Targets 1, 2 and a daily-resolution 4 are constructible over ten years today.
Targets 3 and 5 — and any intraday-precision reversal definition — are limited
to roughly three months. No thresholds were chosen.

---

## 10. Historical Coverage

| timeframe | rows | symbols | range | usable span |
|---|---|---|---|---|
| **1d** | 6,294,407 | 4,400 | 2016-01-01 → 2026-09-08 | **~10 years** |
| 1h | 842,793 | 3,045 | 2023-06-30 → 2026-09-08 | ~3 years |
| 5m | 3,373,574 | 3,038 | 2026-06-09 → 2026-09-08 | ~3 months |
| 15m | 238,443 | 2,720 | 2026-06-19 → 2026-09-07 | ~3 months |
| 1m | 25,900,274 | 3,739 | 2026-06-18 → 2026-09-07 | ~3 months |

**Earliest date a complete feature vector could exist:**

* daily-only price/volume/market features → **2016-09-06** (canonical start)
* adding intraday-derived features → **2026-06-18**
* adding news → **2026-04-07**
* adding FII/DII → **2026-06-12**
* adding fundamentals or sector → **never, with the current schema**

So a genuinely complete feature vector as specified is **not currently
constructible at any date.**

---

## 11. Data Availability Matrix

| Feature family | D-close | D+1 pre-open | D+1 intraday | Historical PIT | Status |
|---|---|---|---|---|---|
| Daily OHLCV | PASS | PASS | PASS | PASS (10y, session-resolved) | **PASS** |
| Volume / liquidity | PASS | PASS | PASS | PASS (same rows) | **PASS** |
| Market / index | PASS | PASS | PASS | PASS — but `^NSEI` canonical only from 2026-08-24 | **PASS (short index history)** |
| Sector | UNPROVEN | UNPROVEN | UNPROVEN | **FAIL** — no historical membership | **FAIL** |
| News | PASS | PASS | PASS | PASS from 2026-04-07 | **PASS (5 months)** |
| Corporate actions | PASS | PASS | PASS | PASS (`basis="raw"` guard) | **PASS** |
| Fundamentals | UNPROVEN | UNPROVEN | UNPROVEN | **FAIL** — one current row, no dates | **FAIL** |
| FII / DII | PASS | PASS | PASS | PASS (`date <= bar_date`) from 2026-06-12 | **PASS (3 months)** |
| Pre-open | **FAIL** | **FAIL** | **FAIL** | **FAIL** — nothing stored | **FAIL** |
| Intraday | PASS | PASS | PASS | PASS from 2026-06-18 | **PASS (3 months)** |

---

## 12. Critical Blockers

| id | severity | finding |
|---|---|---|
| **B-1** | **CRITICAL** | Fundamentals have no historical dimension — 1 current row per symbol, no period-end or publication date. Any historical use is look-ahead + survivorship. |
| **B-2** | **CRITICAL** | Sector/industry has no persisted historical membership; today's classification would be applied retroactively. |
| **B-3** | **HIGH** | Pre-open data does not exist in any form — the D+1 pre-open stage of the target architecture has no foundation. |
| **B-4** | **HIGH** | Intraday history is ~3 months, so MFE-before-reversal and time-to-peak labels cannot be built over a meaningful sample. |
| **B-5** | **MEDIUM** | `ml_predictor` reads daily bars without `as_of`; it must pass one when the dataset is built. |
| **B-6** | **MEDIUM** | `causal_events` stores only `created_at`, so publication and ingestion cannot be separated — UNPROVEN. |
| **B-7** | **LOW** | Replay runs whose cutoff fell on a Sunday (13.9% of calendar dates) used a look-ahead entry price and should be re-run before being cited. |

---

## 13. Recommended Next Phase

1. **Decide the honest scope of the first dataset.** What is provable *today* is
   a daily-resolution, price/volume/market/news model over ~10 years of daily
   bars (5 months with news). Targets 1, 2 and daily-MAE are constructible now.
   Fundamentals, sector and pre-open must be excluded from v1 rather than
   included on assumption.
2. **B-1/B-2 are data-foundation projects, not code fixes.** Fundamentals need a
   snapshot table keyed by `(symbol, period_end, published_at, captured_at)`;
   sector needs dated membership. Neither can be backfilled from what exists.
3. **B-3 pre-open collection** must start capturing forward before it can ever
   support training — every day not collected is permanently lost.
4. **B-5** — pass `as_of` in `ml_predictor` before any dataset is built.
5. Only then revisit Phase 3 (18:30), which remains untouched.

---

**Regression: 2,730 passed, 0 new failures, 0 new errors** (the same 32
pre-existing). No 18:30 migration, no model trained, no production prediction
logic created.
