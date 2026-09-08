# PHASE 2H — UPSTOX SHADOW BACKFILL: IMPLEMENTATION + BLOCKER

**Date:** 2026-09-07 · **Status: IMPLEMENTED, PILOT BLOCKED**
**Rows written: 0** · **Database unchanged** · **Migration gate: NO-GO**

---

## 0. Executive summary

The script and its tests are complete and pass. The dry-run pilot then found a
**contract conflict that blocks the backfill entirely**, and per the brief's
instruction I stopped rather than adapting around it.

> *"Do NOT change the canonical daily candle contract unless absolutely
> required; if a contract conflict is discovered, STOP and report it."*

**The conflict:** NSE trades on ten weekend dates in the last ten years — Diwali
Muhurat, Budget Saturdays, and three disaster-recovery sessions. Upstox returns
them correctly. The write-path contract rejects them as weekends, and because
validation is all-or-nothing per symbol, **all 10 pilot symbols failed and 0 of
2,700 symbols can be backfilled**.

Nothing was written. The dry-run existed precisely to find this before it
touched the database, and it did.

---

## 1. Files Changed

| File | Status | Purpose |
|---|---|---|
| `scripts/oneoff_upstox_backfill.py` | **new**, 470 lines | the standalone one-off utility |
| `tests/test_oneoff_upstox_backfill.py` | **new**, 34 tests | its regression suite |

No production module was modified. No schedule, worker, contract or PAPER_MODE
setting was touched. `grep` across `engine/ crawler/ tasks/ api/ utils/
paper_trading/` confirms nothing imports the script — asserted by a test.

---

## 2. Exact Functions Reused (§15 — verified, not assumed)

| Function | Signature as it actually is | Use |
|---|---|---|
| `crawler.upstox_candles.get_upstox_candles_for_range` | `(symbol, from_date, to_date, interval='1d', oi=False)` | the only fetch; returns save-ready dicts |
| `crawler.upstox_candles._to_naive_utc` | `(raw, *, daily=False)` | **not called directly** — reached through the fetch, so there is exactly one normalization |
| `utils.candle_contract.validate_canonical_daily_equity_candle` | `(candle, *, holidays=None, now_utc=None)` | pre-save validation |
| `crawler.price_feed.save_candles_to_db` | `(candles, session, *, source, enforce_contract=True, refresh_current_session=False)` | the only writer; `ON CONFLICT DO NOTHING` |
| `crawler.upstox_limiter.acquire` | `(*, bucket=None, exit_bucket=False)` | **already called inside the fetch** — no second limiter added |
| `crawler.upstox_quotes.ensure_key_map` / `_to_key` | `(force=False)` / `(symbol) -> str \| None` | instrument keys, never manufactured |
| `utils.candle_contract.classify_instrument` | `(symbol) -> InstrumentClass` | universe filtering |
| DB uniqueness | `uq_candle_bar UNIQUE (symbol, timeframe, timestamp)` | idempotency |

The script never parses a candle, never builds a timestamp, and never issues SQL
of its own.

---

## 3. Three Conflicts With The Brief

### 3a. `_KITE_TO_UPSTOX` does not exist — **not a blocker**

The brief specifies reusing `_KITE_TO_UPSTOX`. It is not in production code:

```
>>> from crawler.upstox_quotes import _KITE_TO_UPSTOX
ImportError: cannot import name '_KITE_TO_UPSTOX' from 'crawler.upstox_quotes'
```

Its only appearance is `test_universe.py`, a root-level scratch file whose
import is already broken. The real mapping is `ensure_key_map()` / `_to_key()`
over the `kite_instruments` table. The script uses the real one.

### 3b. Universe is 2,700, not ~2,301 — **not a blocker**

Derived dynamically as instructed:

| class | count | policy |
|---|---|---|
| `NSE_EQUITY` | **2,468** | canonical — the backfill target |
| `ETF_OR_INAV` | 232 | canonical — opt-in via `--include-etf` |
| everything else | 0 | — |

The master contains no `-BE` / `-SM` / `-ST` symbols at all, so there are no
unsupported segments to exclude. Default run is equity-only: 2,468.

### 3c. Price basis — measured, and the audited label needs correcting

The brief and my own Phase 2E report call Upstox "raw / unadjusted". **That is
imprecise.** Upstox history *is* corporate-action adjusted in absolute terms —
RELIANCE's 2016-09-07 close returns as **242.50** against a nominal ~₹1,050 at
the time, reflecting the 2017 and 2024 bonuses.

What matters is that it is **stable**, and it is. Re-fetching sessions this
codebase already wrote from Upstox reproduces them exactly:

| symbol | rows compared | identical | differing |
|---|---|---|---|
| RELIANCE.NS | 86 | 84 | 2 |
| TCS.NS | 86 | 84 | 2 |
| INFY.NS | 86 | 84 | 2 |
| HDFCBANK.NS | 86 | 84 | 2 |
| ICICIBANK.NS | 86 | 84 | 2 |

The two that move per symbol are the newest sessions, at `price×1.00000` with
volume revised 0.01–6% — post-settlement volume corrections, not adjustments.

**I initially misread this as a STOP condition.** Comparing a fresh fetch against
stored *18:30* rows showed INFY differing on 651 of 664 sessions at a constant
×1.0216 with identical volume — the price-only rescale signature. But those
18:30 rows were never Upstox-sourced. Note that 0.9788 × 1.0216 = 1.0000: the
legacy 00:00 and 18:30 series share one basis, and Upstox differs from *both* by
that factor. The discrepancy lives in the legacy data, which is the reason this
backfill exists. No transformation is applied by the script.

---

## 4. THE BLOCKER — contract cannot express an NSE weekend session

### What happened

```
UNIVERSE   attempted 10 · SUCCESS 0 · NO_DATA 0 · NO_KEY 0 · ERROR 10
ROWS       received 24,773 · saved 0 · rejected 24,773
QUALITY    weekend_or_holiday 10 · every other violation 0
ERRORS     RELIANCE.NS  ValidationError: session date 2016-10-30 is a weekend
           INFY.NS      ValidationError: session date 2019-10-27 is a weekend
           … all 10 symbols
```

### The dates are real trading sessions

Ten distinct weekend dates appear in ten years of Upstox data:

| date | day | occasion |
|---|---|---|
| 2016-10-30 | Sunday | Diwali Muhurat |
| 2019-10-27 | Sunday | Diwali Muhurat |
| 2020-02-01 | Saturday | Union Budget |
| 2020-11-14 | Saturday | Diwali Muhurat |
| 2023-11-12 | Sunday | Diwali Muhurat |
| 2024-01-20 | Saturday | special live / DR session |
| 2024-03-02 | Saturday | special live / DR session |
| 2024-05-18 | Saturday | special live / DR session |
| 2025-02-01 | Saturday | Union Budget |
| 2026-02-01 | Sunday | Union Budget |

46 of 12,383 rows across five symbols — **0.371%**. Small in volume, total in
effect: all-or-nothing validation means one such bar rejects a symbol's entire
ten-year history.

### Why the contract cannot accept them

`is_nse_trading_session` already supports this exactly:

```
is_nse_trading_session(d, holidays=None, extra_open=None) -> bool
    2019-10-27 with extra_open=None  -> False
    2019-10-27 with extra_open={...} -> True
```

But the write-path validator does not forward it:

```
validate_canonical_daily_equity_candle(candle, *, holidays=None, now_utc=None)
```

There is **no `extra_open` parameter**. The capability exists one function down
and is simply not plumbed through. This is the same limitation recorded in Phase
2D.2 §2, where `daily_series` refuses 6 NIFTYBEES rows for the identical reason —
there it costs 0.7% of one symbol's history; here it costs 100% of everything.

### What I did NOT do

I did not add `extra_open` to the validator, did not hardcode a Muhurat list,
did not relax the weekend rule, and did not drop the offending bars to let the
rest through. Each would be a silent change to the canonical contract, and the
brief forbids exactly that.

### The minimal fix, for approval — not implemented

Thread `extra_open` through `validate_canonical_daily_equity_candle` and
`filter_canonical_candles` to the `is_nse_trading_session` call that already
accepts it, and have the backfill pass a calendar built by the existing
`nse_extra_open_dates()`. That is a ~5-line change to a load-bearing contract,
touching every production write path — which is why it needs a decision rather
than my judgement.

Open question that decision must settle: production currently writes
`extra_open=None`, so **a live Budget-Day or Muhurat session is silently dropped
today**. The next one is 2026-11-08 (Diwali Muhurat, a Sunday).

---

## 5. Tests Added

`tests/test_oneoff_upstox_backfill.py` — **34 tests, all passing**, covering
every item in §14 of the brief:

| area | tests |
|---|---|
| date-range construction | 10-year default, explicit override, conservative defaults |
| universe / instrument filtering | class filter, ETF opt-in, keys never manufactured, pilot is 10 distinct equities with no index or ETF |
| canonical timestamp enforcement | 00:00 rejected, 18:30 rejected, 03:45 accepted |
| weekend/holiday rejection | Saturday session rejected with reason |
| parser output validation | OHLC invariants (4 cases), NaN, negative volume, duplicate sessions, future dates, empty batch |
| checkpoint / resume | round-trip, only SUCCESS resumes, append-only, all 12 fields present, missing file |
| retry behaviour | bounded to `MAX_ATTEMPTS` then ERROR; recovery when a later attempt succeeds |
| idempotent persistence | second run saves 0 |
| dry-run | never calls `save_candles_to_db` |
| failure isolation | one symbol raising leaves a concurrent symbol SUCCESS |
| persistence contract | correct `source`, `enforce_contract` left True, `refresh_current_session` left False |
| script hygiene | no DELETE/TRUNCATE/UPDATE in the file; no production module imports it |

**Full suite: 2,655 passed, 32 pre-existing failures, 0 new failures, 0 new
errors, 11 skipped.**

---

## 6. Safety Properties Verified

* **Additive only** — `save_candles_to_db` with `refresh_current_session=False`;
  `ON CONFLICT DO NOTHING`. No DELETE, TRUNCATE or UPDATE exists in the file.
* **Contract enforced** — `enforce_contract` left at its default `True`.
* **No second normalizer** — timestamps come only from the production fetch.
* **No second limiter** — `acquire()` is already inside the fetch; the script
  adds bounded concurrency (`asyncio.Semaphore`, default 20) and nothing else.
* **Durable checkpoint** — every attempt appended and `fsync`ed before the next
  symbol; resume skips only `SUCCESS`.
* **Failure isolation** — per-symbol try/except; one symbol cannot abort a run.
* **Measured throughput** — 10 symbols in 2.1s, 4.67 req/s at concurrency 20,
  0 retries. One request covers the full 10 years (RELIANCE: 2,479 rows, 0.68s).

**Database after all work: 1d 5,891,931 · 00:00 4,854,349 · 03:45 32,123 ·
18:30 1,002,584 — identical to before. 0 rows created in the last 20 minutes.**

---

## 7. Pilot Readiness

**NOT READY — blocked by §4.**

Everything else is ready: the script runs, the universe derives, the fetch
works, throughput is measured, the checkpoint is durable, and 34 tests pass. The
single blocker is the contract's inability to accept a legitimate NSE weekend
session. Until that is decided, a pilot would either fail all 10 symbols (as it
just did) or require me to change the contract unilaterally.

### Exact command, once the blocker is resolved

```bash
cd /home/cis/windows/auto-trade-pro/autotrade-backend

# 1. dry run — fetch + validate, writes nothing
PYTHONPATH=$PWD .venv/bin/python scripts/oneoff_upstox_backfill.py \
    --pilot --dry-run --checkpoint /tmp/pilot_dryrun.csv

# 2. pilot — 10 equities, additive writes
PYTHONPATH=$PWD .venv/bin/python scripts/oneoff_upstox_backfill.py \
    --pilot --checkpoint /tmp/pilot.csv

# 3. idempotency — must report rows saved = 0
PYTHONPATH=$PWD .venv/bin/python scripts/oneoff_upstox_backfill.py \
    --pilot --checkpoint /tmp/pilot_run2.csv

# 4. full universe (2,468 equities) — only after the pilot is verified
PYTHONPATH=$PWD .venv/bin/python scripts/oneoff_upstox_backfill.py \
    --full --resume --checkpoint backfill_log.csv
```

---

## 8. Migration Gate

# NO-GO

Unchanged and untouched. No legacy 00:00 or 18:30 row was read for replacement,
modified, normalized or deleted. The backfill has not yet produced a single row,
so it has produced no evidence for a later delete decision. That evidence is
what §13 of the brief asks for and it cannot exist until the blocker is cleared.

---

**Decision required:** whether to thread `extra_open` through the daily contract
(§4), which is a change to a load-bearing production guard and therefore yours,
not mine.
