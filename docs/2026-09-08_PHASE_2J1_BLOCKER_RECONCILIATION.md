# PHASE 2J.1 — BLOCKER RECONCILIATION (READ-ONLY FORENSIC)

**Date:** 2026-09-08 · **Verdict: GREEN — both blockers reconcile exactly**
**Nothing deleted, updated, normalized or committed. No timestamp changed.**

Both queries you flagged were correct observations. Neither is a defect in the
manifest: one is a property of the legacy data, the other a definitional
asymmetry between two different questions. Both are reconciled to the row below.

---

## BLOCKER 1 — Why candidates stop at 2026-07-06

### 1/2. Extent of each convention

| convention | rows | session range | max timestamp |
|---|---|---|---|
| legacy 00:00 | 4,854,349 | 2016-01-01 .. **2026-08-28** | 2026-08-28 00:00:00 |
| canonical 03:45 | 4,076,785 | 2016-01-01 .. 2026-09-08 | 2026-09-08 03:45:00 |
| legacy 18:30 | 1,002,584 | 2021-06-23 .. 2026-09-02 | 2026-09-02 18:30:00 |

So legacy 00:00 *does* reach 2026-08-28, exactly as the report said.

### 3. But the tail after 2026-07-06 is 95 rows across 3 symbols

| | |
|---|---|
| legacy 00:00 rows after 2026-07-06 | **95** |
| distinct symbols | **3** |
| **with a canonical replacement** | **0** |
| without | **95** |

| symbol | rows | range |
|---|---|---|
| `^BSESN` | 35 | 2026-07-13 .. 2026-08-28 |
| `^NSEBANK` | 30 | 2026-07-13 .. 2026-08-21 |
| `^NSEI` | 30 | 2026-07-13 .. 2026-08-21 |

The daily row counts collapse from ~2,754 symbols/day to 1–3:

```
2026-08-28  rows=1  symbols=1        2026-08-21  rows=3  symbols=3
2026-08-27  rows=1  symbols=1        2026-08-20  rows=3  symbols=3
```

**The bulk legacy 00:00 equity writer stopped after 2026-07-06.** Everything
after that date is three index symbols only.

### 4/5. The exact boundary rows

Latest 20 legacy rows **with** a canonical replacement — all on the same date:

```
2026-07-06  20MICRONS.NS   id=5283115795      2026-07-06  AARTIDRUGS.NS  id=5283914454
2026-07-06  21STCENMGM.NS  id=5283118922      2026-07-06  AARTIIND.NS    id=5283917531
2026-07-06  360ONE.NS      id=5283120159      2026-07-06  ABB.NS         id=5283920007
… (all 20 are 2026-07-06)
```

Latest 20 **without** a replacement — all three index symbols:

```
2026-08-28  ^BSESN    id=13552014143     2026-08-21  ^NSEBANK  id=14792756700
2026-08-27  ^BSESN    id=13552014142     2026-08-21  ^NSEI     id=14792756688
2026-08-26  ^BSESN    id=13552014141     2026-08-20  ^NSEBANK  id=14792756699
…
```

### 6. Independent reconstruction (no CSV involved)

```
SELECT count(*), max(l.timestamp::date) FROM candles l
WHERE l.timeframe='1d' AND to_char(l.timestamp,'HH24:MI')='00:00'
  AND EXISTS (SELECT 1 FROM candles c WHERE c.symbol=l.symbol AND c.timeframe='1d'
       AND to_char(c.timestamp,'HH24:MI')='03:45' AND c.timestamp::date=l.timestamp::date)

-> candidates = 3,642,218    MAX candidate session = 2026-07-06
```

Identical to the manifest, derived without touching the CSV.

### 7. Why the manifest stops at 2026-07-06

**Because 2026-07-06 is the last session for which any replaceable legacy 00:00
row exists.** Not a filter, not a cut-off, not a bug:

* legacy 00:00 equity coverage ends 2026-07-06;
* the only later legacy rows are `^BSESN`, `^NSEI`, `^NSEBANK`;
* `^BSESN` has **no canonical rows at all** — it is `OTHER_INDEX`, which the
  daily contract excludes outright (Phase 2F);
* `^NSEI` / `^NSEBANK` legacy stops **2026-08-21** while their canonical series
  starts **2026-08-24** (when the corrected regime writer began). **The two
  windows never overlap**, so the intersection is empty by construction.

Canonical growth after 2026-07-06 is real, but it lands on sessions where no
legacy 00:00 row exists — nothing to replace. **Blocker 1: RECONCILED.**

---

## BLOCKER 2 — the 2,540-row difference

Two different questions were being counted:

* **Definition A** (classification) — legacy rows belonging to symbols in
  category `NO_REPLACEMENT`, which my classifier assigns when
  `(canonical = 0 AND not in master)` **or** `(canonical > 0 AND intersection = 0)`.
* **Definition B** (rollback check F) — legacy rows whose **symbol has no
  canonical rows at all**.

Recomputed from the database:

```
Definition B                                        : 1,023,434
symbols with canonical rows but ZERO session overlap:         2
  their legacy rows                                 :     2,540
Definition A  =  1,023,434 + 2,540                  : 1,025,974
reported earlier as NO_REPLACEMENT                  : 1,025,974
RECONCILES EXACTLY: True
```

### 3. The exact two symbols

| symbol | legacy rows | legacy range | canonical rows | canonical range | overlap |
|---|---|---|---|---|---|
| `^NSEI` | **1,270** | 2021-06-21 .. 2026-08-21 | 12 | 2026-08-24 .. 2026-09-08 | **0** |
| `^NSEBANK` | **1,270** | 2021-06-21 .. 2026-08-21 | 12 | 2026-08-24 .. 2026-09-08 | **0** |

1,270 + 1,270 = **2,540** — the entire difference, to the row.

### 4. What they actually are

**Neither a counting error nor a mis-categorisation — a definitional
asymmetry, and both definitions are individually correct.**

These two indices have canonical rows, so Definition B excludes them. But their
canonical window (from 2026-08-24) does not intersect their legacy window (to
2026-08-21), so **not one of their 2,540 legacy rows can be replaced**.
Category-wise they are correctly `NO_REPLACEMENT`: the classifier is answering
"can this symbol's legacy history be replaced?", check F is answering "does this
symbol have any canonical rows?". For these two symbols the answers legitimately
differ.

They are the same three index symbols behind Blocker 1 — the two blockers share
one root cause: **the index canonical series only begins 2026-08-24.**

### 5. Are any of them in the deletion manifest?

```
candidate rows contributed by ^NSEI / ^NSEBANK              : 0
candidate rows contributed by symbols with no canonical rows: 0
```

**Zero rows classified `NO_REPLACEMENT` under either definition appear in the
manifest.** Both definitions agree those rows are preserved. **Blocker 2:
RECONCILED.**

---

## Additional integrity checks

### A. Manifest CSV

| | |
|---|---|
| rows (symbols) | 2,299 |
| unique symbols | 2,299 |
| `candidate_rows` sum | **3,642,218** |
| max candidate session | **2026-07-06** |
| global digest | `8ba1a75c2eced65651bfbdfb4f1067e3` |

### B/C. DB-derived, compared at ROW level via the primary key

Per-symbol checksums are `md5` over `session_date|timestamp|id` ordered by
`(date, id)` — so equality is row-level identity, not merely matching totals.

| | |
|---|---|
| DB symbols / rows | 2,299 / **3,642,218** |
| manifest-only symbols | **0** |
| DB-only symbols | **0** |
| per-symbol count or checksum mismatches | **0** |
| DB global digest | `8ba1a75c2eced65651bfbdfb4f1067e3` — **identical** |
| max candidate session | 2026-07-06 |

### D. Predicate

```
c.symbol = l.symbol  AND  c.timestamp::date = l.timestamp::date
```

Symbol + resolved trading-session date only. **No reference to `close`, `open`,
`high`, `low` or `volume` anywhere in the candidate predicate.** Price equality
is deliberately excluded — replacing the legacy price basis is the point of the
migration.

### E. No 18:30 rows are candidates

**0** in the manifest — the predicate hard-codes `00:00`.

Worth recording: **704,696 legacy 18:30 rows *would* satisfy the same predicate**
if it were widened. They are deliberately out of scope and remain untouched.
That is a separate migration with its own manifest, not an extension of this one.

### F. Nothing outside the manifest affected

Proven inside the rolled-back transaction (Phase 2J §6): total rows fell by
**exactly** the manifest count, 03:45 / 18:30 / other conventions unchanged,
key uniqueness held, and every count and checksum was restored after `ROLLBACK`.

### G. Simulation

Not repeated — the Phase 2J run already covered it and nothing about the
manifest changed. No `COMMIT`, no delete outside a transaction, no
normalization, no timestamp change.

*(One note on process: an earlier attempt at this forensic used correlated
subqueries and was killed at the 10-minute tool limit; PostgreSQL rolled back on
connection loss and integrity was re-verified. The queries above were rewritten
as single-pass aggregates.)*

---

## Verdict

# GREEN — both blockers reconcile exactly

| blocker | status | reconciliation |
|---|---|---|
| 1 — candidates stop at 2026-07-06 | **RECONCILED** | last replaceable legacy session; the 95 later rows are 3 index symbols with 0 canonical overlap; independent reconstruction returns the same max |
| 2 — 1,025,974 vs 1,023,434 | **RECONCILED** | exactly `^NSEI` + `^NSEBANK`, 1,270 rows each = 2,540; non-overlapping canonical window; **0** of them are candidates |

Manifest verified at row level against the database by primary key, with
identical global digests.

### What GREEN does and does not mean

It means the manifest is exact, independently reproducible, and the simulated
delete touches nothing outside it.

It does **not** mean deletion is advisable yet. Unchanged from Phase 2J:

* the price-basis consequence — legacy-derived backtests and models become
  non-comparable (INFY ≈ 2.16%);
* **2,650 symbols would still carry 18:30 alongside canonical**, and 704,696
  18:30 rows satisfy the same replacement predicate — deleting only 00:00 does
  not yield a single-convention database;
* the manifest is a snapshot: canonical rows grew from 3,977,736 to 4,076,785
  during this work, so it must be re-derived immediately before any deletion,
  and its checksum re-verified against that state.

---

**Nothing was deleted. Nothing was committed. Legacy 00:00 (4,854,349) and 18:30
(1,002,584) are unchanged. Deletion remains unauthorised.**
