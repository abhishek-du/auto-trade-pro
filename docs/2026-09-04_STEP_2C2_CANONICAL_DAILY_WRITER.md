# STEP 2C.2 — Eliminate Competing Daily Candle Writers

**Date:** 2026-09-04 · **Verdict:** **PASS**
**Scope:** make the NSE-equity daily candle pipeline deterministic — exactly one
canonical writer. No historical rows migrated, deleted or rewritten.

> Every number below is a measurement taken during this step. Nothing is
> inferred from a previous report.

---

## 1. Executive verdict

**PASS** — one canonical NSE-equity daily writer, enforced at the persistence
choke point, with no historical data touched.

Step 2C.1 found **three** daily timestamp conventions writing to one table from
**eleven** possible persistence paths. Fixing writers one at a time would leave
the next new writer free to invent a fourth, so the rule now lives in one module
and is enforced immediately before persistence.

| Acceptance item | Status |
|---|---|
| All active 1D equity writers identified | ✅ |
| Exactly one canonical NSE equity daily writer | ✅ |
| `fetch_nse_candles` delegates correctly | ✅ |
| yfinance cannot write NSE equity 1D | ✅ |
| API cannot create an alternate equity daily series | ✅ |
| Manual backfill cannot reintroduce bad timestamps | ✅ |
| Application persistence guard exists | ✅ |
| Golden basket passes | ✅ 56 symbols / 280 bars / 0 failures |
| Raw → canonical → DB equality | ✅ 33 bars / 0 mismatches |
| Repeated ingestion idempotent | ✅ 56/56 |
| No new 00:00 / 18:30 / arbitrary-time equity daily rows | ✅ 0 / 0 / 0 |
| No new future / weekend daily rows | ✅ 0 / 0 |
| NSE-only invariant | ✅ |
| Historical legacy rows untouched | ✅ |
| Step 2B / 2C / 2C.1 regressions reported | ✅ |
| No newly introduced test failures | ✅ |

---

## 2. Complete daily-writer inventory (Phase A)

| Path / function | Caller | Data source | TF | Convention | Writes? | Active | Class | Risk (after fix) |
|---|---|---|---|---|---|---|---|---|
| `upstox_candles.get_upstox_candles_for_range` | 4 scheduled tasks | Upstox V3 | 1d + intraday | **03:45** | via helper | ✅ | equity | **canonical** |
| `zerodha_historical.get_kite_candles_for_range` | `kite_sync_candles`, `_backfill_hub_1d`, `_refresh_priority_1d`, `sync_full_nse_universe` | Upstox | 1d | **03:45** | ✅ | ✅ | equity | none |
| `price_feed.fetch_candles` | `run_price_crawl` | Upstox → yf → AlphaVantage | 1h (1d capable) | **03:45** | ✅ | ✅ | equity | none |
| `india_price_feed.fetch_nse_candles` | `india_price_scan`, API route | **delegates** (equity 1d) | 1d, 5m, 1h | **03:45** | ✅ | ✅ | equity | **FIXED** |
| ↳ same fn, non-equity | `^NSEI`, `^NSEBANK`, `NIFTYBEES` | yfinance | 1d | 00:00 | ✅ | ✅ | index/ETF | outside contract |
| `zerodha_market.get_kite_historical` | agent tools, news engine, simulator | Upstox | 1d + intraday | **03:45** | ✅ | ✅ | equity | none |
| `upstox_historical.sync_long_tail_intraday` | 30-min task | Upstox | **1m only** | n/a | ✅ | ✅ | equity | none |
| `candle_resampler` | 2-min task | derived from 1m | 5m/15m/1h — **no 1d** | n/a | ✅ | ✅ | equity | none |
| `api/india.py:356` | `GET /india/candles/{sym}` | via `fetch_nse_candles` | on-demand | **03:45** (equity) | ✅ | ✅ | mixed | **FIXED** |
| `scripts/backfill_1d_candles.py` | manual | yfinance, **direct INSERT** | 1d | 00:00 | ✅ | ❌ **DISABLED** | equity | **CLOSED** |
| `scripts/backfill_candles.py` | manual | via guarded helper | 1d | guarded | ✅ | manual | equity | none |
| `scripts/backfill_all_candles_zerodha.py` | manual | via guarded helper | 1d | guarded | ✅ | manual | equity | none |

No database triggers. No Redis consumers write candles.

---

## 3. Files / functions changed

| File | Change |
|---|---|
| `utils/candle_contract.py` | **NEW** — the contract, defined once |
| `crawler/price_feed.py` | guard wired into `save_candles_to_db`; added `source=` (provenance) and `enforce_contract=` |
| `crawler/india_price_feed.py` | equity 1d delegates to canonical; yfinance fallback removed for equity 1d; new `_delegate_daily_equity_to_canonical()` |
| `scripts/backfill_1d_candles.py` | disabled at import time |
| `tests/test_step2c2_canonical_daily_writer.py` | **NEW** — 37 tests |
| `audit/step2c2_golden_basket.py` | **NEW** — evidence harness |

Deliberately **not** changed: `performance_engine._trading_date()` (pinned by 2C.1
tests; reporting-only blast radius), and every historical row.

---

## 4. Architecture after the fix

```
                Upstox Historical Candle V3
                            │
                            ▼
        upstox_candles._to_naive_utc(daily=True)     ← ONE conversion, one place
                            │
     ┌──────────────────────┼──────────────────────┐
     │                      │                      │
zerodha_historical     price_feed        india_price_feed
(4 scheduled tasks)   .fetch_candles     (equity 1d → DELEGATES)
     │                      │                      │
     └──────────────────────┼──────────────────────┘
                            ▼
          price_feed.save_candles_to_db(source=…)
                            │
              utils.candle_contract  ← FAIL-CLOSED GUARD
                            │
                            ▼
                      candles table
                 one 1d bar / equity / session
```

Non-equity instruments (indices, ETFs/INAV, debt series) bypass the equity
contract **by classification, not by exception** — `^NSEI` has no Upstox
instrument key, so forcing the equity contract on it would reject legitimate
index data.

---

## 5. Canonical timestamp contract

```
RAW        2026-08-31T00:00:00+05:30     ← a DATE LABEL at midnight IST
CANONICAL  2026-08-31 03:45:00 UTC       ← 09:15 IST session open, naive UTC

∴  candles.timestamp::date  ==  NSE trading-session date
```

Forbidden for a **newly written NSE equity daily** bar:

| Rejected | Why |
|---|---|
| `00:00` UTC | the dead, pre-split legacy series |
| `18:30` UTC | the one-day-offset legacy series |
| any other time-of-day | not the session open |
| future timestamp | — |
| weekend / NSE-holiday session date | not a trading session |
| tz-aware or non-datetime | the table is naive UTC |

Rejected rows are **dropped and logged with their source**, never rewritten.
Silent repair is how three conventions accumulated in the first place.

---

## 6. Golden basket

**56 NSE equities**, real Upstox instrument keys from the current master,
drawn from `hub_universe` by turnover rank:

| Tier | n | Examples |
|---|---|---|
| large-cap | 12 | HDFCBANK, BSE, RELIANCE, BHARTIARTL, ETERNAL, ICICIBANK, SBIN |
| mid-cap | 12 | RHETAN, TRITURBINE, WHIRLPOOL, ENDURANCE, RATNAMANI, TRIVENI |
| small-cap | 12 | IKIO, RITES, HONAUT, SUMEETINDS, JINDRILL, MAHSEAMLES |
| low-liquidity | 12 | RPPINFRA, IRMENERGY, SALASAR, DHANUKA, EKC, SWARAJENG |
| volatile / active | 8 | DIFFNKG, WEL, PAR, SSWL, OMAXE, DJML, MOLBIO, KABRAEXTRU |

Sessions 2026-08-28 → 2026-09-03.

| Check | Result |
|---|---|
| 1. instrument identity resolves | **56 / 56** |
| 2. raw daily API returns data | **56 / 56** |
| bars validated | **280** |
| 3. raw has `+05:30` offset and midnight wall-clock | **280** |
| 4. canonical mapping → 03:45 UTC | **280** |
| 5. OHLC validity (finite, `low ≤ o/c ≤ high`) | **280** |
| 6. volume non-negative | **280** |
| 7. valid NSE trading session | **280** |
| 8. no future session | **280** |
| 11. canonical OHLCV == raw exactly | **280** |
| 9. persistence succeeds | **56 / 56** |
| 12/13. repeat write inserted 0 rows (idempotent) | **56 / 56** |
| 10. persisted timestamp == canonical | **280** |
| **failures** | **0** |

---

## 7. Raw Upstox vs canonical vs DB (Phase L)

**11 symbols across all tiers · 33 bars · exact comparison, no tolerance · 0 mismatches.**

| Symbol | Session | ts | close raw / canon / db | volume raw / db |
|---|---|---|---|---|
| RELIANCE | 2026-09-01 | `03:45` | 1309.0 / 1309.0 / 1309.0 | 12,617,528 / 12,617,528 |
| HDFCBANK | 2026-09-02 | `03:45` | 700.8 / 700.8 / 700.8 | 36,950,338 / 36,950,338 |
| BSE | 2026-09-03 | `03:45` | 3306.1 / 3306.1 / 3306.1 | 6,036,100 / 6,036,100 |
| RHETAN (mid) | 2026-09-03 | `03:45` | 23.11 / 23.11 / 23.11 | 10,526,861 / 10,526,861 |
| IKIO (small) | 2026-09-01 | `03:45` | 207.53 / 207.53 / 207.53 | 215,560 / 215,560 |
| RPPINFRA (low-liq) | 2026-09-02 | `03:45` | 55.57 / 55.57 / 55.57 | 56,432 / 56,432 |
| WEL (volatile) | 2026-09-02 | `03:45` | 75.98 / 75.98 / 75.98 | 5,897,220 / 5,897,220 |

---

## 8. Before / after database counts (Phase K)

NSE **equity** `1d` rows only:

| Metric | Before | After | Δ |
|---|---|---|---|
| **@03:45 (canonical)** | 5,667 | 5,922 | **+255** ✅ |
| @00:00 (legacy dead series) | 4,014,217 | 4,014,217 | **0** |
| @18:30 (legacy offset series) | 929,444 | 929,444 | **0** |
| other times | 2,498 | 2,498 | **0** |
| future timestamps | 0 | 0 | **0** |
| weekend-dated | 204,312 | 204,312 | **0** |
| duplicate `(symbol, tf, timestamp)` | 0 | 0 | **0** |
| multiple bars per symbol/session | 617,050 | 617,189 | +139 |
| all `1d` rows | 5,865,576 | 5,865,831 | +255 |

**Every new row is canonical.** The +139 is expected and honest: newly written
canonical bars now coexist with legacy bars for the same sessions. That overlap
is exactly what Step 2D reconciles — it is not a new defect.

**Pre-existing legacy violations vs newly created:**

| | Pre-existing | Newly created |
|---|---|---|
| non-canonical equity daily rows | 4,946,159 | **0** |
| weekend-dated | 204,312 | **0** |
| future | 0 | **0** |

---

## 9. Non-canonical write attempts detected / rejected

Live, after a full service restart:

```
[candle_contract] REJECTED 1 of 1 daily equity bars from source='2c2-live-proof':
  {'non-canonical daily time 18:30 UTC — the one-day-offset legacy daily s': 1}
  → rows inserted = 0
```

Every rejection records the calling pipeline, so each dropped bar has a
deterministic provenance path.

**Phase I — all five active daily-equity callers executed live:**

| Caller | bars | times produced |
|---|---|---|
| `upstox_candles` (canonical) | 5 | `['03:45:00']` |
| `zerodha_historical` (4 scheduled tasks) | 5 | `['03:45:00']` |
| `price_feed.fetch_candles` | 1241 | `['03:45:00']` |
| `india_price_feed.fetch_nse_candles` | 9 | `['03:45:00']` |
| `zerodha_market.get_kite_historical` | 5 | `['03:45:00']` |
| **callers producing a non-canonical time** | — | **NONE** |
| `^NSEI` (index, legacy path — by design) | 10 | `['00:00:00']` |

---

## 10. Negative-test results (Phase J)

| Rejected input | Result |
|---|---|
| 00:00 UTC equity daily | ✅ rejected, names the dead pre-split series |
| 18:30 UTC equity daily | ✅ rejected, names the offset series |
| arbitrary time-of-day | ✅ rejected |
| future timestamp | ✅ rejected |
| weekend session (Sat 2026-09-05) | ✅ rejected |
| NSE holiday (2026-08-26, Id-E-Milad) | ✅ rejected |
| timezone-aware timestamp | ✅ rejected |
| non-datetime timestamp | ✅ rejected |
| yfinance-derived equity daily | ✅ fails closed, returns `[]` |
| BSE / non-NSE instrument | ✅ classified `NON_NSE` |
| non-equity (index / ETF / debt) | ✅ passes through its own contract |

**No historical rows were deleted to make any test pass.**

---

## 11–15. Test results

| Suite | Result |
|---|---|
| **Step 2C.2 (new)** | **37 passed** |
| Step 2C.1 | 18 passed |
| Step 2C | 19 passed |
| Step 2B | 22 passed |
| candle / instrument suites (6 files) | 101 passed |
| **Full project suite** | **2,462 passed / 27 failed / 11 skipped / 5 errors** |

| | Passed | Failed | Errors |
|---|---|---|---|
| Before this step | 2,425 | 27 | 5 |
| After | **2,462** | **27** | 5 |

**Baseline failures 27 → 27. New failures: 0.** No existing test weakened or skipped.

---

## 16. Remaining blockers *(Step 2D scope, not this step)*

1. **Historical legacy series untouched** — 4,014,217 rows @00:00 and 929,444
   @18:30. Deliberate: Phase N forbids migration here.
2. **`^NSEI` / `^NSEBANK` still write 00:00.** They have no Upstox instrument
   key, so they fall to the yfinance path. Index and equity daily bars therefore
   sit in *different* series — and `performance_engine._aligned_closes` compares
   exactly those two.
3. **`_trading_date()` still wrong by −1..−4 days.** Pinned by 2C.1 tests;
   blast radius is reporting only (`/agent/performance`,
   `/portfolio/capital-model`), not trade sizing or execution.

---

## 17. Commands used to validate

```bash
cd autotrade-backend

# golden basket: raw → canonical → DB, 56 symbols
.venv/bin/python audit/step2c2_golden_basket.py

# step suites
.venv/bin/python -m pytest tests/test_step2c2_canonical_daily_writer.py -q
.venv/bin/python -m pytest tests/test_step2b_market_data_invariants.py \
    tests/test_step2c_daily_session_date.py \
    tests/test_step2c1_trading_date_contract.py -q

# candle / instrument regressions
.venv/bin/python -m pytest tests/test_candle_pipeline_integrity.py \
    tests/test_live_candle_builder.py tests/test_zerodha_market.py \
    tests/test_upstox_migration.py tests/test_live_1m_delta_window.py \
    tests/test_price_freshness.py -q

# full suite
.venv/bin/python -m pytest tests/ -q

# the disabled legacy backfill must refuse
.venv/bin/python scripts/backfill_1d_candles.py
```

---

## 18. Is Step 2D safe to begin?

**Yes for the writer architecture — with one caveat to settle first.**

Every acceptance-gate item is satisfied with direct evidence: exactly one
canonical equity daily writer, delegation proven by execution, yfinance blocked
for equity daily, API and manual backfill closed, the guard verified live,
golden basket and three-way equality clean, zero new non-canonical rows, and all
legacy rows intact.

**The caveat is blocker 2.** `^NSEI` still lands in the 00:00 series while
equities land in 03:45, and `performance_engine._aligned_closes` compares exactly
those two. A Step 2D reconciliation that normalises equities but leaves indices
behind would leave that comparison broken in a new way — the two series would
still disagree, just differently.

Recommendation: resolve the **index** daily convention as part of Step 2D's
scope, not after it.

---

*Compiled 2026-09-04 from live measurement against the production Upstox account
and database. No historical candle rows were migrated, deleted or rewritten.*
