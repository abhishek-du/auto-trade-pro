# PHASE 2J.2 — CONTROLLED LEGACY 00:00 DELETION: RESULT

**Date:** 2026-09-08 · **Status: COMMITTED** · **All gates PASS**
**Rows deleted: 3,642,218** · **Rows preserved: 1,212,131**
**18:30, canonical 03:45 and all other conventions: unchanged**

---

## 1. Outcome

| | |
|---|---|
| transaction start | `2026-09-08T09:02:51Z` |
| COMMIT issued | `2026-09-08T09:14:00Z` |
| duration | ~11 minutes |
| rows deleted | **3,642,218** |
| rows preserved | **1,212,131** |
| gates evaluated | 18 |
| gates passed | **18 / 18** |
| post-commit checks | **9 / 9 PASS** |

Exactly one statement mutated data:
`DELETE FROM candles WHERE id IN (SELECT id FROM _cand)` — where `_cand` was
materialised inside the same transaction directly from the database.

---

## 2. Controlled snapshot

The two workers that execute 1d writes were stopped for the duration:

| unit | during the gate |
|---|---|
| `autotrade-celery-worker` (runs `india_price_scan`, `kite_sync_candles`, backfills) | **stopped** |
| `autotrade-celery-scan-worker` (runs `sync_regime_daily_candles`) | **stopped** |
| `autotrade-celery-beat` | left running |
| `autotrade-celery-exit-worker` | **left running — stop-loss protection maintained** |
| `autotrade-celery-trade-worker` | left running |
| `autotrade-uvicorn`, `autotrade-news-engine` | left running |

Verified before starting: only `exit@` and `trade@` responded to the broker,
**0 active tasks**, **0 1d rows written in the preceding 60 s**.

Both workers were restarted immediately after the post-commit verification;
all four workers respond and all seven units are active.

---

## 3. Candidate set — freshly derived, and identical to Phase 2J

Derived inside the transaction with no reference to the earlier CSV:

```sql
CREATE TEMP TABLE _cand AS
SELECT l.id, l.symbol, l.timestamp AS ts, l.timestamp::date AS session_date
FROM candles l
WHERE l.timeframe='1d' AND to_char(l.timestamp,'HH24:MI')='00:00'
  AND EXISTS (SELECT 1 FROM candles c
              WHERE c.symbol = l.symbol AND c.timeframe='1d'
                AND to_char(c.timestamp,'HH24:MI')='03:45'
                AND c.timestamp::date = l.timestamp::date);
```

Symbol + resolved trading-session date only. **No `open`, `high`, `low`,
`close` or `volume` appears anywhere in the predicate.**

| | Phase 2J manifest | fresh (authoritative) | delta |
|---|---|---|---|
| candidate rows | 3,642,218 | **3,642,218** | **+0** |
| distinct symbols | 2,299 | **2,299** | **+0** |
| global digest | `8ba1a75c2eced65651bfbdfb4f1067e3` | **`8ba1a75c2eced65651bfbdfb4f1067e3`** | **identical** |
| session range | 2016-09-06 .. 2026-07-06 | 2016-09-06 .. 2026-07-06 | — |

**There was no delta to reconcile.** The set was stable because canonical rows
added since Phase 2J land on sessions after 2026-07-06, where no legacy 00:00
row exists — the reason established in Phase 2J.1.

**Manifests written:**
* `docs/2026-09-08_FINAL_PREDELETE_MANIFEST.csv` — 2,299 symbols with per-symbol
  count, session range and checksum (162 KB).
* Row-level manifest — 3,642,218 rows of `symbol, legacy_id,
  trading_session_date, legacy_timestamp` — written to the session scratchpad at
  `FINAL_PREDELETE_MANIFEST_rowlevel.csv` rather than `docs/`, because at ~200 MB
  it does not belong in a shared git working tree. The per-symbol checksums plus
  the deterministic predicate above reconstruct and verify it exactly.

---

## 4. Pre / post counts and checksums

| metric | pre-delete | post-commit | delta |
|---|---|---|---|
| total candles | 40,279,918 | 36,637,700 | **−3,642,218** |
| **legacy 00:00** | 4,854,349 | **1,212,131** | **−3,642,218** |
| **canonical 03:45** | 4,076,785 | **4,076,785** | **0** |
| **legacy 18:30** | 1,002,584 | **1,002,584** | **0** |
| other conventions | 2,875 | 2,875 | **0** |
| `.BO` rows | 0 | 0 | 0 |

**Checksums**

```
canonical 03:45   pre 2681357103.46   post 2681357103.46   UNCHANGED
legacy   18:30    pre 1262947141.73   post 1262947141.73   UNCHANGED
legacy   00:00    pre 3660724845.45   (candidates removed by design)
```

The total fell by exactly the manifest count — nothing outside the manifest was
touched.

---

## 5. Gate results (printed before COMMIT)

```
CHECK                                               RESULT    EXPECTED
--------------------------------------------------------------------------
deleted == fresh manifest count                     PASS      PASS
zero candidate ids remain                           PASS      PASS
E preserved 00:00 == pre - manifest                 PASS      PASS
D canonical 03:45 count unchanged                   PASS      PASS
D canonical 03:45 checksum unchanged                PASS      PASS
I 18:30 count unchanged                             PASS      PASS
I 18:30 checksum unchanged                          PASS      PASS
other conventions unchanged                         PASS      PASS
F total == pre - manifest                           PASS      PASS
.BO rows still zero                                 PASS      PASS
G key uniqueness holds                              PASS      PASS
A every candidate is a legacy 00:00 row             PASS      PASS
B every candidate has canonical same symbol+session PASS      PASS
C zero candidates are 18:30/03:45 rows              PASS      PASS
C zero candidates lack a canonical replacement      PASS      PASS
G primary-key uniqueness before delete              PASS      PASS
J canonical 03:45 count recorded pre-delete         PASS      PASS
H reader checks pass                                PASS      PASS
==========================================================================
ALL CHECKS PASS: True
COMMIT ISSUED at 2026-09-08T09:14:00.783210Z
```

The COMMIT was gated in code: `if allpass: commit() else: rollback()`. No check
was interpreted, softened or waived.

---

## 6. Reader validation

In-transaction, and again post-commit on a fresh connection:

| symbol | sessions | unique | ordered | conventions |
|---|---|---|---|---|
| RELIANCE.NS | 2,480 | ✅ | ✅ | `canonical_0345` |
| TCS.NS / INFY.NS / HDFCBANK.NS | 2,477 | ✅ | ✅ | `canonical_0345` |
| ICICIBANK.NS / SBIN.NS / ITC.NS / LT.NS | 2,477 | ✅ | ✅ | `canonical_0345` |
| NIFTYBEES.NS | 2,478 | ✅ | ✅ | `canonical_0345` |
| **PRE_UPSTOX** 20MICRONS.NS | 2,477 | ✅ | ✅ | `canonical_0345` |
| **PRE_UPSTOX** 21STCENMGM.NS | 2,118 | ✅ | ✅ | `canonical_0345` |
| **PRE_UPSTOX** 360ONE.NS | 1,729 | ✅ | ✅ | `canonical_0345` |
| **NO_REPLACEMENT** 3IINFOLTD.NS | 1,192 | ✅ | ✅ | `legacy_1830` |
| **NO_REPLACEMENT** AAKAAR-SM.NS | 46 | ✅ | ✅ | `legacy_1830` |
| **NO_REPLACEMENT** AARADHYA-SM.NS | 40 | ✅ | ✅ | `legacy_1830` |

Every replaced symbol now reads as a **pure canonical single-convention series**.
The `NO_REPLACEMENT` symbols still read — their history was preserved and is
served from the 18:30 convention, exactly as intended.

---

## 7. Post-commit verification (fresh connection)

| # | check | result |
|---|---|---|
| 1 | deleted candidate ids gone (92 sampled ids across the file, plus a full predicate re-scan) | **PASS** — 0 remain |
| 2 | preserved 00:00 rows remain (1,212,131) | **PASS** |
| 3 | canonical 03:45 count and checksum unchanged | **PASS** |
| 4 | 18:30 count and checksum unchanged | **PASS** |
| 5 | other conventions unchanged | **PASS** |
| 6 | no duplicate `(symbol, timeframe, timestamp)` | **PASS** |
| 7 | no `.BO` candle rows | **PASS** |
| 8 | representative `daily_series` reads | **PASS** |
| 9 | manifest rows no longer exist | **PASS** — predicate returns 0 |

---

## 8. What was preserved

**1,212,131 legacy 00:00 rows remain**, deliberately:

* **~1,026,000 rows / 1,058 symbols** with no canonical identity — delisted,
  renamed, `-BE`/`-SM`/`-BZ` series with no Upstox instrument key, and 214
  unknown identities. These have no replacement and were never candidates.
* **2,540 rows** — `^NSEI` and `^NSEBANK` (1,270 each), whose canonical window
  begins 2026-08-24 and does not intersect their legacy window ending
  2026-08-21.
* the remaining pre-September-2016 tails, which predate Upstox coverage.

**All 1,002,584 legacy 18:30 rows are untouched.**

---

## 9. Remaining state and open items

* **704,696 legacy 18:30 rows satisfy the same replacement predicate**, and
  ~2,650 symbols still carry 18:30 alongside canonical. This deletion covered
  00:00 only, per instruction — a single-convention database would need a
  separate 18:30 migration with its own manifest and gate.
* **Price basis:** the canonical series uses Upstox's historical adjustment
  semantics; the removed legacy series used yfinance's. Backtests, trained
  models and performance statistics computed on the old series are **not
  numerically comparable** to results computed now (INFY differed by ≈2.16%) and
  should be recomputed rather than compared across the boundary.
* Still open from earlier phases: production writers pass `extra_open=None`, so
  a live NSE special session is dropped — the next is **2026-11-08** (Diwali
  Muhurat).

---

**Exactly one DELETE ran, inside one transaction, against a manifest derived
from the database and verified by checksum. No 18:30 row, no canonical 03:45
row, and no other-convention row was changed. No candle was normalized,
re-timestamped or updated.**
