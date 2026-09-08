# PHASE 2I — FULL NSE_EQUITY BACKFILL: REPLACEMENT-QUALITY AUDIT

**Date:** 2026-09-07 · **Run: COMPLETE** · **Verdict: YELLOW**
**Rows inserted: 3,658,184** · **Legacy rows deleted or modified: 0**
**ETF backfill: not run** · **Migration/deletion decision: not taken**

---

## 1. Executive verdict

# YELLOW

The backfill itself is flawless: **zero** timestamp violations, **zero**
duplicates, **zero** OHLCV violations, **zero** future sessions, **zero** legacy
mutations, and 97.65% symbol success. Every quality gate in sections B, C and D
passed outright.

It is YELLOW, not GREEN, because of what the replacement-coverage analysis
found: **the canonical dataset replaces 68.50% of legacy 00:00 history, not
~100%.** The shortfall is not a backfill defect — most of it is structurally
unreplaceable — but it means the legacy series cannot simply be deleted, and the
cleanup decision needs the breakdown in §8 rather than a single number.

---

## 2. Baseline and universe

| baseline metric | value |
|---|---|
| total candles | 36,149,406 |
| total 1d | 5,915,920 |
| 03:45 | 56,112 |
| 00:00 | 4,854,349 |
| 18:30 | 1,002,584 |
| other conventions | 2,875 |
| distinct 1d symbols | 4,378 |

Immutability evidence captured before the run:
`00:00 n=4,854,347 sum=3,660,724,845.45` · `18:30 n=1,002,584 sum=1,262,947,141.73`

**Universe, derived at runtime** — instrument master, NSE only, non-empty key:

| class | count | in this run |
|---|---|---|
| `NSE_EQUITY` | **2,468** | ✅ target |
| `ETF_OR_INAV` | 232 | ❌ excluded by instruction |
| unexpected classes | **0** | — |
| non-NSE symbols | **0** | — |

`sha256(sorted universe) = f374c231d49442bbb8720da0e559c397e9f9ad335959046961437db7cf5f4848`

No unexpected instrument class appeared, so the pre-run STOP condition did not
trigger.

---

## 3. Run summary

| | |
|---|---|
| elapsed | **1,820.6 s (30.3 min)** |
| requests | 2,468 (one per symbol — a single request covers ten years) |
| retries | **0** |
| effective req/s | 1.36 |
| per-symbol elapsed | min 1.96s · p50 13.61s · max 53.97s |
| rows received | 3,805,459 |
| passed the contract | 3,705,817 |
| **inserted** | **3,658,184** |
| already existing | 47,633 |
| rejected | 99,642 |

Wall-clock was dominated by inserting ~1,500 rows per symbol, not by the API.
The existing limiter was used unchanged — `acquire()` lives inside
`get_upstox_candles_for_range`; the script added only a bounded semaphore at 20.

No STOP condition fired: no systemic timestamp violation, no constraint failure,
no unexpected production write, no rate-limit distress, no non-NSE instrument.

---

## 4. A — Universe outcome

| status | count |
|---|---|
| attempted | 2,468 |
| **SUCCESS** | **2,410 (97.65%)** |
| NO_DATA | **0** |
| NO_KEY | **0** |
| ERROR | 58 |

Classes attempted: `{nse_equity: 2468}` — ETF correctly absent.

**All 58 errors reduce to two causes:**

| n | cause |
|---|---|
| 57 | `session date 2025-04-26 is a weekend` |
| 1 | `invalid volume -81259413.0 on 2024-08-30` |

**2025-04-26 is a Saturday that Upstox reports as a session for 57 smaller
symbols, and it is not in the curated calendar.** The pilot's five large caps
did not carry it, which is why it surfaced only at full scale — NSE special
sessions cover a limited set of securities.

I did **not** add it. The brief forbids inferring special sessions from candle
data, and the live `/v2/market/holidays` endpoint only covers the current year,
so it cannot corroborate a 2025 date. Those 57 symbols are recorded ERROR with
an exact reason and are **resumable** the moment the date is independently
confirmed.

The single volume error is a genuine data defect — Upstox returned a **negative
volume** — and the validator refused the whole symbol rather than storing it.

---

## 5. B — Canonical timestamp (rows created by this run)

```
03:45            3,658,184
new 00:00 rows           0
new 18:30 rows           0
new rows at any other time  0
```

Every SUCCESS symbol reported a single convention: `{'03:45'}`.

---

## 6. C/D — Session validity and OHLCV

| check | result |
|---|---|
| weekend session dates in canonical data | 10 |
| all within `NSE_SPECIAL_SESSIONS` | **True** — undeclared: **NONE** |
| future sessions | **0** |
| duplicate (symbol, session) | **0** |
| duplicate DB keys | **0** |
| OHLC/volume invariant violations | **0** |
| NaN / infinite closes | **0** |

Per-symbol counters agree: duplicates 0, OHLC violations 0 across all 2,468.

---

## 7. E — Coverage by symbol

2,698 symbols now hold canonical history (the 2,410 from this run plus symbols
already canonical from earlier phases).

| sessions per symbol | symbols |
|---|---|
| ≥2,000 | **1,182** |
| 1,000–1,999 | 385 |
| 250–999 | 405 |
| 50–249 | 240 |
| <50 | 486 |

min 1 · p10 10 · **p50 1,439** · p90 2,477 · max 2,644.
Full ten-year history (≥2,400 sessions): **1,031 symbols (38.2%)**.

**726 symbols have <250 sessions — and all 726 have their first session in 2024
or later.** These are recent listings, not gaps.

Continuity: **218 of 2,697** symbols have a longest gap exceeding 30 days.
The widest are suspension/relisting histories — `GOYALALUM.NS` 2,244 days,
`MEDANTA.NS` 2,122, `RHL.NS` 2,057.

Latest session across all symbols: **2026-09-07**.

---

## 8. H — LEGACY REPLACEMENT COVERAGE (the decisive report)

| | |
|---|---|
| symbols with legacy 00:00 history | 3,357 |
| legacy 00:00 session-rows | 4,854,349 |
| **replaced by canonical** | **3,325,135 (68.50%)** |
| **legacy-only, unreplaced** | **1,529,214 (31.50%)** |

Per-symbol distribution:

| replacement | symbols |
|---|---|
| 100% | 620 |
| 95–99.9% | 454 |
| 50–95% | 958 |
| 1–50% | 9 |
| **0%** | **1,316** |

### Why the 31.5% is unreplaced

| cause | symbols | unreplaced rows |
|---|---|---|
| **Not in the Upstox universe** (delisted / renamed / `-BE`/`-SM` series) | 1,058 | **1,025,974** |
| Equity in universe, partial coverage | 1,465 | 253,446 |
| **ETF — excluded from this run by instruction** | 214 | 249,794 |
| | | **1,529,214** |

* **Addressable by further work: 503,240 rows** — an ETF run (249,794) plus the
  partial-coverage equities (253,446), which include the 57 symbols blocked by
  2025-04-26.
* **Structurally unreplaceable: 1,025,974 rows across 1,058 symbols** — these
  are absent from the current Upstox instrument master and can never be
  backfilled from it. Examples: `GUJGASLTD.NS` (2,598 legacy rows),
  `BLISSGVS.NS` (2,596), `JBCHEPHARM.NS` (2,596), plus `-BE`/`-SM` series forms
  for which Upstox publishes no instrument key.

**These 1,058 symbols are the crux of any deletion decision. Deleting their
legacy rows would destroy history with no replacement. They were not touched.**

---

## 9. F — Reader verification (production path, real database)

| symbol | sessions | unique | ordered | first | last | conventions |
|---|---|---|---|---|---|---|
| RELIANCE.NS | 2,480 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| TCS.NS | 2,477 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| INFY.NS | 2,477 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| HDFCBANK.NS | 2,477 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| ICICIBANK.NS | 2,477 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| SBIN.NS | 2,477 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| ITC.NS | 2,477 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| LT.NS | 2,477 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| NIFTYBEES.NS | 2,477 | ✅ | ✅ | 2016-09-06 | 2026-09-07 | 100% canonical |
| ^NSEI | 11 | ✅ | ✅ | 2026-08-24 | 2026-09-07 | 100% canonical |

Every one of these now reads back as a **single-basis, fully canonical**
ten-year series — previously they were mixed-convention and ~200 sessions deep.

* Special sessions readable: RELIANCE 10, others 9 — **undeclared weekend
  sessions: none** on any symbol.
* Look-ahead free: 09-02 → 1313.1, 09-03 → 1302.5, 09-04 → 1322.0.
* `market_regime` input: NIFTYBEES **220 sessions** (≥60 floor) — OK.
* `^NSEI` remains at 11 sessions: indices are not in the equity universe and
  were not backfilled. Unchanged from before this run.

---

## 10. G — Price reproducibility

Representative basket of 8 symbols, last 12 months, stored canonical rows vs a
fresh Upstox fetch:

| | |
|---|---|
| compared | 2,024 rows |
| **exact matches** | **2,008 (99.21%)** |
| price differences | 3 |
| volume-only differences | 13 |
| sessions affected | **2** — 2026-09-04, 2026-09-07 |
| confined to the 7-day settlement window | **True** |

Consistent with the pilot (99.93% across 24,773 rows). No stored row was
rewritten on the strength of this check — it is validation only.

---

## 11. Database safety check

| metric | baseline | after | delta |
|---|---|---|---|
| total candles | 36,149,406 | 39,807,594 | +3,658,188 |
| 1d total | 5,915,920 | 9,574,104 | +3,658,184 |
| **03:45** | 56,112 | **3,714,296** | **+3,658,184** |
| **00:00** | 4,854,349 | 4,854,349 | **0** |
| **18:30** | 1,002,584 | 1,002,584 | **0** |
| other conventions | 2,875 | 2,875 | **0** |

**Legacy immutability — count and checksum both identical:**

```
00:00  n=4,854,347  sum=3660724845.45   UNCHANGED
18:30  n=1,002,584  sum=1262947141.73   UNCHANGED
```

Legacy rows updated: **0**. Deleted: **0**. Normalized: **0**.
03:45 growth matches the inserted count **exactly**. The 4-row difference in
the total is intraday rows written by ordinary production tasks during the run.

**Test suite: 2,709 passed · 0 new failures · 0 new errors** (the same 32
pre-existing failures).

**Audit log preserved:** `docs/2026-09-07_backfill_audit_log.csv` — 2,468 rows,
every symbol with a terminal status, 21 fields each.

---

## 12. Evidence for the next decision — controlled legacy cleanup

Not taken here. What the next decision needs, and what this run establishes:

**Safe to consider for cleanup** — legacy 00:00 rows whose session is already
covered by a canonical row: **3,325,135 rows (68.50%)**. For these the canonical
series is verified present, single-basis, look-ahead free and reproducible.

**Must NOT be deleted** — **1,529,214 rows (31.50%)**, of which:

1. **1,025,974 rows / 1,058 symbols — no replacement possible.** Absent from the
   Upstox master; deleting them destroys the only record.
2. 249,794 rows / 214 symbols — ETFs, replaceable by a run you have not
   authorised.
3. 253,446 rows / 1,465 symbols — partial coverage, partly recoverable once
   2025-04-26 is confirmed.

**Open items before any cleanup:**

* **2025-04-26** — verify independently against the NSE calendar, then resume
  the 57 blocked symbols.
* **ETF backfill** — 232 instruments, not run.
* **Negative-volume symbol** — one instrument returns volume −81,259,413 on
  2024-08-30; source-side defect worth reporting upstream.
* **Live special-session writing** — still unfixed from Phase 2H.1; production
  writers pass `extra_open=None`, so **2026-11-08 (Diwali Muhurat)** will be
  dropped.
* **The 00:00 price basis itself** — unchanged since Phase 2E. Even for the
  68.50% that is replaced, the two series differ for some symbols by a constant
  factor (INFY ~2.16%), so replacement changes values, not just timestamps.

---

**No legacy 00:00 or 18:30 data was deleted or modified. The ETF backfill was
not run. No migration or deletion decision has been made.**
