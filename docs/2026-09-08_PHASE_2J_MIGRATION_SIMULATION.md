# PHASE 2J — MIGRATION SIMULATION (ROLLBACK-ONLY)

**Date:** 2026-09-08 · **Verdict: GREEN** · **Nothing was deleted or committed**
**Legacy 00:00 and 18:30 data: unchanged** · **Actual deletion: still unauthorised**

Artefacts:
* `docs/2026-09-08_FINAL_LEGACY_DELETE_MANIFEST.csv` — 2,299 symbols, per-symbol
  count, session range and checksum
* `docs/2026-09-08_legacy_migration_candidates.csv` — full per-symbol mapping

---

## 1. Verdict

# GREEN

Both conditions the brief sets for GREEN are met:

* **The manifest is independently verified.** Reconstructed from the database by
  a second, structurally different query plan; count, symbol set and every
  per-symbol checksum match exactly — zero manifest-only rows, zero DB-only rows.
* **The rollback simulation passes.** All nine in-transaction checks passed, the
  reader returned clean single-convention series on the simulated post-delete
  state, and the database was verified byte-identical after `ROLLBACK`.

GREEN describes the *manifest and the mechanism*. It is not a recommendation to
delete — §7 sets out what deletion still costs.

---

## 2. Recovery of the 46 in-master symbols

### Was the all-or-nothing policy safe to change?

**Yes, and the investigation showed it was never production behaviour.**

`crawler/price_feed.py::save_candles_to_db` → `filter_canonical_candles()`
already validates **per row**, keeping the good and logging the dropped. Every
production writer has always behaved that way. All-or-nothing existed **only**
in the one-off script's `validate_batch`, whose sole callers are the script and
its own tests:

```
callers of validate_batch : scripts/oneoff_upstox_backfill.py, tests/…  (nothing else)
callers of filter_canonical_candles : crawler/price_feed.py             (production)
```

So the change could not affect any production writer, and anything the script
keeps is still re-validated at the choke point. It was added as **opt-in**
(`--skip-invalid-candles`); the default remains all-or-nothing.

### Result

| | |
|---|---|
| symbols processed | 58 (all equity-run errors, not just the 46) |
| SUCCESS | **58 / 58** |
| rows received | 99,674 |
| **rows inserted** | **99,049** |
| invalid candles dropped | **58 — exactly one per symbol** |
| timestamp conventions written | `{03:45}` only |

The two invalid bars stayed refused, which was the whole point:

```
rows stored for the synthetic 2025-04-26 session : 0    (independently confirmed a non-session)
rows with negative volume anywhere               : 0
IDEA.NS canonical row for 2024-08-30             : 0    (NOT fabricated)
IDEA.NS canonical rows recovered                 : 2,476  (2016-09-07 .. 2026-09-08)
```

**In-master legacy symbols with no canonical rows: 46 → 0.**

The OHLCV contract was not weakened globally, negative volume is still refused,
and the 2025-04-26 placeholder is still rejected for all 45 symbols.

---

## 3. Reclassification (post-recovery)

| category | symbols | legacy rows |
|---|---|---|
| `SAFE_REPLACEMENT` | 779 | 629,977 |
| `PRE_UPSTOX_UNREPLACED` | 1,123 | 2,792,249 |
| `PARTIAL_REPLACEMENT` | 397 | 406,149 |
| `NO_REPLACEMENT` | 1,058 | 1,025,974 |
| `OTHER_EXCEPTION` | **0** | **0** |
| **TOTAL** | **3,357** | **4,854,349** |

`PRE_UPSTOX_UNREPLACED` is the largest class because most symbols have legacy
history reaching back to 2016-01-01 while the Upstox window starts 2016-09-06.
Those symbols are otherwise fully replaced; only the pre-window tail survives.

Deletion eligibility is per **row**, not per symbol — a `PRE_UPSTOX_UNREPLACED`
symbol still contributes every row whose session *is* covered.

---

## 4. Candidate criterion and independent reconstruction

Criterion, unchanged: a legacy 00:00 row is a candidate when a canonical 03:45
row exists for the **same `symbol` and same `trading_session_date`**, and the
production `daily_series` reader resolves that session. **Prices are never
compared** — see §8.

| | Method A (JOIN) | Method B (EXISTS) |
|---|---|---|
| candidate rows | 3,642,218 | **3,642,218** |
| symbols | 2,299 | **2,299** |
| manifest-only symbols | — | **0** |
| database-only symbols | — | **0** |
| count / checksum mismatches | — | **0** |
| derived global checksum | `8ba1a75c2eced65651bfbdfb4f1067e3` | **identical** |

The manifest was not trusted as its own authority: Method B is a different query
plan over the same predicate, and the per-symbol checksums were compared
individually rather than only in aggregate.

---

## 5. Deletion manifest

```
candidate rows   : 3,642,218
preserved rows   : 1,212,131
session range    : 2016-09-06 .. 2026-07-06
symbols          : 2,299
row-level checksum (md5 over symbol|session_date|timestamp|id, ordered):
                   6b25aeb0b842448c13275ce925c74540
per-symbol digest (md5 over symbol:checksum pairs, ordered):
                   8ba1a75c2eced65651bfbdfb4f1067e3
```

Per-symbol counts reconcile exactly to the global count. The manifest carries a
per-symbol checksum so any future reconstruction can be verified symbol by
symbol rather than only in total — a single mismatched symbol cannot hide inside
a matching grand total.

---

## 6. Rollback-only simulation

`BEGIN` → materialise candidate ids → `DELETE` by primary key → verify → `ROLLBACK`.

| check | result |
|---|---|
| A candidate rows removed == manifest count | **PASS** (3,642,218) |
| A no candidate rows remain | **PASS** |
| B preserved legacy rows remain | **PASS** (4,854,349 → 1,212,131) |
| C/D canonical 03:45 unchanged | **PASS** (4,076,785 → 4,076,785) |
| E 18:30 unchanged | **PASS** (1,002,584) |
| F `NO_REPLACEMENT` rows untouched | **PASS** (1,023,434) |
| G nothing outside the manifest removed | **PASS** (total fell by exactly the manifest count) |
| G other conventions unchanged | **PASS** (2,875) |
| I key uniqueness holds | **PASS** (0 duplicates) |

**H/J — reader on the simulated post-delete state.** Every representative symbol
returned a **single-convention** series:

| symbol | sessions | unique | ordered | conventions |
|---|---|---|---|---|
| RELIANCE.NS | 2,480 | ✅ | ✅ | `['canonical_0345']` |
| TCS.NS / INFY.NS / HDFCBANK.NS | 2,477 | ✅ | ✅ | `['canonical_0345']` |
| ICICIBANK.NS / SBIN.NS / ITC.NS / LT.NS | 2,477 | ✅ | ✅ | `['canonical_0345']` |
| NIFTYBEES.NS | 2,478 | ✅ | ✅ | `['canonical_0345']` |

**After `ROLLBACK`, verified from a fresh connection:**

```
00:00     4,854,349 -> 4,854,349   RESTORED
03:45     4,076,785 -> 4,076,785   RESTORED
18:30     1,002,584 -> 1,002,584   RESTORED
total    40,263,301 -> 40,263,301  RESTORED
checksum 00:00  3660724845.45 -> 3660724845.45   RESTORED
checksum 18:30  1262947141.73 -> 1262947141.73   RESTORED
```

**No COMMIT was issued at any point.**

*Note:* a first attempt using a correlated `EXISTS` delete exceeded the tool's
10-minute limit and was killed. PostgreSQL rolled the transaction back on
connection loss; integrity was re-verified before proceeding (counts and both
checksums intact). The rerun materialises candidate ids first and deletes by
primary key.

---

## 7. Post-cleanup dataset

| convention | before | after | delta |
|---|---|---|---|
| canonical 03:45 | 4,076,785 | 4,076,785 | 0 |
| legacy 00:00 | 4,854,349 | **1,212,131** | −3,642,218 |
| legacy 18:30 | 1,002,584 | 1,002,584 | **0** |
| other conventions | 2,875 | 2,875 | 0 |

**Mixed conventions would NOT be eliminated:**

| situation | symbols |
|---|---|
| 00:00 + canonical still coexisting | **1,522** |
| **18:30 + canonical still coexisting** | **2,650** |
| legacy-only, no canonical (preserved) | 1,056 |

Representative post-cleanup shapes:

```
SAFE_REPLACEMENT     3BBLACKBIO.NS   00:00 kept=0      canonical=98
PARTIAL/PRE_UPSTOX   20MICRONS.NS    00:00 kept=3      canonical=2,477
                     360ONE.NS       00:00 kept=3      canonical=1,729
NO_REPLACEMENT       3IINFOLTD.NS    00:00 kept=1,240  canonical=0
                     AAKAAR-SM.NS    00:00 kept=224    canonical=0
```

The readers already handle this correctly — `daily_series` picks one basis per
series — but **deleting the 00:00 candidates does not produce a single-convention
database**. 2,650 symbols would still carry 18:30 alongside canonical, because
18:30 is deliberately out of scope (§ brief item 8).

---

## 8. Price basis — documented, not a blocker

**Legacy (00:00):** a yfinance historical series, T+1-style date labelling
relative to the other legacy convention, carrying yfinance's own retroactive
price adjustments (price-only; volume left unadjusted).

**Replacement (canonical 03:45):** the Upstox historical canonical series, using
Upstox's own historical adjustment semantics, anchored to the session open
(09:15 IST). Reproducible: re-fetching stored rows reproduces them to 99.2–99.9%,
with differences confined to the current settlement window.

The migration **intentionally replaces the legacy price basis**, so price
inequality is not a disqualifier and was deliberately excluded from the
candidate criterion.

**Flagged consequence:** historical backtests, trained models and performance
statistics computed on the legacy series are **not numerically comparable** to
results computed on the canonical series. For some symbols the two differ by a
constant factor (INFY ≈ 2.16%, volume identical). Anything derived from legacy
prices — model weights, backtest P&L, Sharpe, beta — should be regarded as
invalidated and recomputed, not compared across the boundary.

---

## 9. Final answers

| item | value |
|---|---|
| 1. final candidate row count | **3,642,218** |
| 2. final preserved row count | **1,212,131** |
| 3. candidate checksum | row-level `6b25aeb0b842448c13275ce925c74540`; per-symbol digest `8ba1a75c2eced65651bfbdfb4f1067e3` |
| 4. CSV-vs-DB equality | **EXACT** — 0 CSV-only, 0 DB-only, 0 mismatches, identical digest |
| 5. rollback simulation | **PASS** — 9/9 checks, database fully restored, no COMMIT |
| 6. reader validation | **PASS** — 9/9 symbols single-convention, unique, ordered |
| 7. 46-symbol recovery | **COMPLETE** — 58 symbols, 99,049 rows, 46 → 0 gaps, both invalid bars still refused |
| 8. remaining unrecoverable identities | **1,058 symbols / 1,025,974 rows** — 266 `-BE/-BZ`, 549 `-SM/-ST`, 214 unknown, 29 other |
| 9. remaining 00:00 rows after cleanup | **1,212,131** |
| 10. remaining 18:30 rows after cleanup | **1,002,584** (untouched by design) |
| 11. remaining mixed-convention situations | 1,522 symbols (00:00 + canonical), **2,650 symbols (18:30 + canonical)** |

---

## 10. What a controlled deletion would still need

The manifest and mechanism are verified. Before authorising the real deletion:

1. **Accept the price-basis consequence** (§8). This is a business decision, not
   a data one: every legacy-derived backtest and model becomes non-comparable.
2. **Decide the 18:30 convention.** Deleting only 00:00 leaves 2,650 symbols
   mixed. If the goal is a single-convention database, 18:30 needs its own
   migration plan — explicitly out of scope here.
3. **Re-derive the manifest immediately before deleting.** Production writers add
   canonical rows continuously (03:45 grew from 3,977,736 to 4,076,785 during
   this task alone), so the candidate set grows. The manifest is a snapshot; its
   checksum is valid only against the state it was computed from.
4. **Execute per row on `symbol + session_date`**, never by symbol and never on
   an aggregate percentage.

---

**Nothing was deleted. Nothing was committed. No legacy 00:00 or 18:30 row was
modified, normalized, or re-timestamped. The 1,212,131 rows that would be
preserved — including all 1,025,974 with no canonical replacement — remain
untouched. Actual deletion remains unauthorised.**
