# PHASE 2H — SHADOW BACKFILL: PILOT EVIDENCE REPORT

**Date:** 2026-09-07 · **Pilot: PASSED** · **Full backfill: NOT RUN**
**Legacy 00:00 / 18:30 data: untouched** · **Migration gate: NO-GO**

---

## 1. What ran

| | |
|---|---|
| Pilot symbols | 10 — **8 NSE_EQUITY + 2 ETF_OR_INAV**, validated and reported separately |
| Runs | dry-run, then **two live runs** (idempotency) |
| Rows inserted | **23,989** (run 1) · **0** (run 2) |
| Legacy rows modified | **0** |
| New 00:00 rows | **0** |
| New 18:30 rows | **0** |
| Full universe | **not run**, as instructed |

---

## 2. The blocker from the previous report is resolved

NSE trades on **ten weekend dates** in the last ten years. The write-path
contract rejected all of them, and because validation is all-or-nothing per
symbol, every one of the 10 pilot symbols failed with 0 rows.

**The deciding evidence:** the legacy series carries all ten — up to **3,008
rows on 2026-02-01** in the 00:00 series, and the 18:30 series holds them too.
A canonical series unable to represent them could not replace the legacy one, so
the exception in the original brief ("unless absolutely required") was met.

**The change made — deliberately minimal and purely additive:**

```
validate_canonical_daily_equity_candle(candle, *, holidays=None,
                                       extra_open=None,      # ← added
                                       now_utc=None)
filter_canonical_candles(candles, *, holidays=None, extra_open=None, source=...)
save_candles_to_db(candles, session, *, source, enforce_contract=True,
                   refresh_current_session=False, extra_open=None)
```

`is_nse_trading_session(d, holidays, extra_open)` already supported this; the
parameter was simply never plumbed through. It **defaults to `None`, which is
exactly the previous behaviour**, so no existing caller changes:

```
Muhurat 2019-10-27, default        -> (False, 'session date 2019-10-27 is a weekend')
Muhurat 2019-10-27, extra_open set -> (True, None)
ordinary Saturday 2026-09-05       -> (False, 'session date 2026-09-05 is a weekend')
```

The ten dates are a **curated constant in the backfill script**, not derived
from the API — letting the source assert its own calendar would make the check
circular. Each is annotated with its occasion (Diwali Muhurat ×4, Union Budget
×3, disaster-recovery ×3).

---

## 3. Universe — derived dynamically, classes separate

| class | count | in pilot | in full mode |
|---|---|---|---|
| `NSE_EQUITY` | **2,468** | 8 | default target |
| `ETF_OR_INAV` | **232** | 2 | opt-in via `--include-etf` |
| **combined** | **2,700** | 10 | |

Nothing is hardcoded — the count comes from `kite_instruments` filtered through
`classify_instrument`. The master contains no `-BE`/`-SM`/`-ST` symbols, so
there are no unsupported segments to exclude; exclusions are printed rather than
applied silently.

---

## 4. Price basis

This writes the **Upstox historical canonical series, using Upstox's historical
adjustment semantics**. It is not described as raw or unadjusted — Upstox
applies its own corporate-action adjustment (RELIANCE's 2016-09-07 close returns
242.50 against a nominal ~₹1,050, reflecting the 2017 and 2024 bonuses).

The property that matters is **reproducibility**, and it is now a first-class
validation step in the script rather than a one-off measurement.

**Existing Upstox-written 03:45 rows vs a fresh Upstox fetch:**

| scope | compared | identical | differing |
|---|---|---|---|
| before backfill (784 pre-existing rows) | 784 | 767 | 17 (97.83%) |
| **after backfill (whole 10-year set)** | **24,773** | **24,756** | **17 (99.93%)** |

The 17 differences are confined to **exactly two sessions** — 2026-09-04 and
2026-09-07 — with **zero** differing sessions older than 7 days. Of those, 4 are
price differences and 13 volume-only: post-settlement revision of the newest
bars, not retro-adjustment.

No transformation is applied by the script. What the production parser returns
is what is validated and stored.

---

## 5. Per-symbol pilot results

| symbol | class | status | API rows | passed contract | inserted | already existing | convention | dups | wknd viol | OHLC viol | first | last | refetch |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| RELIANCE.NS | nse_equity | SUCCESS | 2,480 | 2,480 | 2,394 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| HDFCBANK.NS | nse_equity | SUCCESS | 2,477 | 2,477 | 2,391 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| ICICIBANK.NS | nse_equity | SUCCESS | 2,477 | 2,477 | 2,391 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| TCS.NS | nse_equity | SUCCESS | 2,477 | 2,477 | 2,391 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| INFY.NS | nse_equity | SUCCESS | 2,477 | 2,477 | 2,391 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| LT.NS | nse_equity | SUCCESS | 2,477 | 2,477 | 2,391 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| ITC.NS | nse_equity | SUCCESS | 2,477 | 2,477 | 2,391 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| SBIN.NS | nse_equity | SUCCESS | 2,477 | 2,477 | 2,391 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| **NIFTYBEES.NS** | **etf_or_inav** | SUCCESS | 2,477 | 2,477 | 2,391 | 86 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 84/86 |
| **BANKBEES.NS** | **etf_or_inav** | SUCCESS | 2,477 | 2,477 | 2,393 | 84 | 03:45 | 0 | 0 | 0 | 2016-09-06 | 2026-09-07 | 82/84 |

Every symbol: one timestamp convention (`03:45`), zero duplicates, zero weekend
violations, zero OHLC violations, ten full years.

**By class:** `nse_equity` 8/8 SUCCESS · `etf_or_inav` 2/2 SUCCESS · NO_DATA 0 ·
NO_KEY 0 · ERROR 0.

---

## 6. Idempotency — run twice

| | run 1 | run 2 |
|---|---|---|
| API rows received | 24,773 | 24,773 |
| passed contract | 24,773 | 24,773 |
| **inserted (new)** | **23,989** | **0** |
| already existing | 784 | 24,773 |
| rejected | 0 | 0 |

Database, identical after both runs:

```
run 1 -> total=36,149,406  1d=5,915,920  00:00=4,854,349  03:45=56,112  18:30=1,002,584
run 2 -> total=36,149,406  1d=5,915,920  00:00=4,854,349  03:45=56,112  18:30=1,002,584
```

`ON CONFLICT DO NOTHING` on `uq_candle_bar (symbol, timeframe, timestamp)`.
Run 2 was executed **without `--resume`**, so it genuinely refetched and
re-attempted every symbol.

---

## 7. Post-backfill validation

**A. Timestamp integrity** — 23,989 new rows, all `03:45`. New `00:00`: **0**.
New `18:30`: **0**.

**B. Session integrity** — 91 weekend-dated canonical rows across exactly 10
distinct dates, **all of them declared NSE special sessions, none unexpected**.
Duplicate `(symbol, session)`: **0**. Future-dated: **0**.

**C. OHLCV integrity** — invariant violations: **0**.

**D. Coverage** — 10/10 symbols, 2,477–2,480 rows each, 2016-09-06 → 2026-09-07.
No symbol with unexpectedly short history.

**E. Uniqueness** — duplicate `(symbol, timeframe, timestamp)`: **0**.

**Legacy preserved** — pilot symbols still hold **25,906** legacy 00:00 rows and
**12,884** legacy 18:30 rows. Nothing deleted, updated or normalized.

**Reader impact** — `_aligned_closes` still returns session D's own close
(09-02 → 1313.1, 09-03 → 1302.5, 09-04 → 1322.0). RELIANCE now reads back
**2,472 sessions** instead of ~200, essentially all canonical
(`canonical_0345` 2,470, `legacy_1830` 2).

---

## 8. Database before / after

| metric | before | after | delta |
|---|---|---|---|
| candles total | 36,125,417 | 36,149,406 | +23,989 |
| 1d total | 5,891,931 | 5,915,920 | +23,989 |
| 1d @ **03:45** | 32,123 | **56,112** | **+23,989** |
| 1d @ 00:00 | 4,854,349 | 4,854,349 | **0** |
| 1d @ 18:30 | 1,002,584 | 1,002,584 | **0** |

Every added row is canonical. Nothing else moved.

---

## 9. Performance

| | dry-run | run 1 | run 2 |
|---|---|---|---|
| elapsed | 2.8s | 13.3s | 11.7s |
| requests | 10 | 10 | 10 |
| retries | 0 | 0 | 0 |
| effective req/s | 3.58 | 0.75 | 0.86 |

One request covers the full ten years per symbol (RELIANCE: 2,480 rows, 0.68s),
so the full universe is ~2,468 requests, not one per year. Wall-clock is
dominated by the ~24k-row insert per symbol, not the API. At the observed rate,
**2,468 equities extrapolate to roughly 55 minutes** at concurrency 20 — but
that is an extrapolation from 10 symbols, not a measurement.

The existing limiter is used unchanged: `acquire()` is already called inside
`get_upstox_candles_for_range`. The script adds only bounded concurrency
(`asyncio.Semaphore`, default 20). No second limiter, no bypass.

---

## 10. Tests

`tests/test_oneoff_upstox_backfill.py` — **45 tests, all passing**, now
including: the ten special sessions are all genuinely weekends; a Muhurat bar
validates while an ordinary Saturday still does not; the contract default is
unchanged by the new parameter; the pilot is 8 equities + 2 ETFs with no index;
the reproducibility check counts identical rows, flags changed ones, and can
never block a backfill; `extra_open` reaches the choke point.

**Full suite: 2,666 passed · 32 pre-existing failures · 0 new failures · 0 new
errors · 11 skipped.**

---

## 11. Remaining issue — the reader has the same gap the writer just lost

The writer can now store the ten special sessions. **`engine/daily_series.py`
still refuses them**, because it calls `resolve_daily_session_date()` without
`extra_open`:

```
reader refuses 10 of 2,480 canonical RELIANCE rows
refused: 2016-10-30, 2019-10-27, 2020-02-01, 2020-11-14, 2023-11-12,
         2024-01-20, 2024-03-02, 2024-05-18, 2025-02-01, 2026-02-01
```

0.4% of sessions — stored correctly, then dropped at read time. Symmetrical to
the writer bug just fixed, and it should be closed before the full backfill so
the dataset is readable in full. **Not changed here:** it is a second contract
touch-point and this phase's mandate was the backfill.

Related, and still open from Phase 2H: production writes `extra_open=None`, so a
**live** Muhurat or Budget session is still dropped today. The next one is
**2026-11-08** (Diwali Muhurat, Sunday).

---

## 12. Migration gate

# NO-GO

The pilot proves the mechanism on 10 symbols. It does not yet prove the
replacement, which needs the full universe plus a coverage comparison against
the legacy series. Specifically still outstanding:

* full-universe coverage (2,468 equity + 232 ETF) — **not run**
* symbols whose legacy history cannot be replaced — unknown until then
* the reader gap in §11
* the legacy 00:00 basis question itself, unchanged since Phase 2E

**No legacy data was deleted, modified or normalized at any point.**

---

## 13. Command for the full run, when authorised

```bash
cd /home/cis/windows/auto-trade-pro/autotrade-backend

# equities only (2,468), resumable, full audit log
PYTHONPATH=$PWD .venv/bin/python scripts/oneoff_upstox_backfill.py \
    --full --resume --concurrency 20 --checkpoint backfill_log.csv

# to include the 232 ETFs as well
PYTHONPATH=$PWD .venv/bin/python scripts/oneoff_upstox_backfill.py \
    --full --include-etf --resume --checkpoint backfill_log.csv
```
