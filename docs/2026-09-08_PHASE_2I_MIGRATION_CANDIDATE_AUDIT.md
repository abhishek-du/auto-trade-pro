# PHASE 2I — MIGRATION CANDIDATE AUDIT (READ-ONLY)

**Date:** 2026-09-08 · **Legacy rows deleted / modified / normalized: 0**
**Only writes: the authorised additive ETF backfill (+263,240 canonical rows)**
**Controlled deletion phase: NOT READY — see §9**

Companion data: `docs/2026-09-08_legacy_migration_candidates.csv` (3,357 symbols,
16 columns — sufficient to reconstruct the deletion set independently).

---

## 1. The 2025-04-26 exception — RESOLVED: it was NOT a trading session

Three independent lines of evidence, none of which relies on Upstox asserting
its own calendar:

**a. A different vendor recorded nothing.** The legacy 00:00 series is
yfinance-derived and spans 3,357 symbols. Rows dated 2025-04-26: **0**. The
18:30 convention, which labels a session under the previous calendar day, also
has **0** rows at 2025-04-25.

**b. It does not behave like a real special session.** Same query across
controls:

| date | day | legacy 00:00 | 18:30 (D−1) | canonical 03:45 | |
|---|---|---|---|---|---|
| **2025-04-26** | Sat | **0** | **0** | **0** | the date under test |
| 2025-02-01 | Sat | 2,658 | 714 | 1,870 | known special session |
| 2024-05-18 | Sat | 2,362 | 661 | 1,748 | known special session |
| 2025-04-19 | Sat | **0** | **0** | **0** | ordinary Saturday control |
| 2025-04-25 | Fri | 2,754 | 727 | 1,903 | ordinary trading day |

2025-04-26 is indistinguishable from an ordinary Saturday and nothing like a
genuine special session.

**c. The Upstox bars themselves are degenerate.** Every sampled symbol returns
`open = high = low = close` with **volume = 0**:

```
AKI.NS         2025-04-25 Fri  o=7.72  h=7.72  l=7.61  c=7.61  v=9,352
               2025-04-26 Sat  o=7.50  h=7.50  l=7.50  c=7.50  v=0     <-- synthetic
ATLASCYCLE.NS  2025-04-26 Sat  o=84.14 h=84.14 l=84.14 c=84.14 v=0     <-- synthetic
AVONMORE.NS    2025-04-26 Sat  o=21.26 h=21.26 l=21.26 c=21.26 v=0     <-- synthetic
```

A session with no volume and no intraday range is not a session. These are
placeholder bars.

*(The NSE `holiday-master` API responds but publishes only the current year, so
it could neither confirm nor deny a 2025 date — recorded for completeness.)*

**Action taken: none.** The calendar was **not** modified, the 57 symbols were
**not** resumed, and validation was **not** weakened. The validator was right to
refuse them.

**Cost of that correctness:** because validation is all-or-nothing per symbol,
one synthetic bar costs each of those 57 symbols its entire ten-year history —
**78,978 legacy rows remain unreplaced** for this reason. Changing the policy to
drop only the offending bar would recover them, but that is a validation-policy
change and is **not** made here.

---

## 2. The negative-volume symbol — RESOLVED: upstream 32-bit overflow

**Symbol: `IDEA.NS` (Vodafone Idea)**, `NSE_EQ|INE669E01016`, class `nse_equity`.

```
2024-08-28 Wed  c=15.97  v=     401,712,642
2024-08-29 Thu  c=16.30  v=     827,342,035
2024-08-30 Fri  c=15.64  v=     -81,259,413   <-- rejected
2024-09-02 Mon  c=15.05  v=     685,959,065
```

**Root cause: an upstream Upstox defect — signed 32-bit integer overflow.**

```
-81,259,413 + 2^32 = 4,213,707,883
```

IDEA is a sub-₹20 stock whose daily turnover runs to hundreds of millions of
shares; a 4.21-billion-share day exceeds `INT32_MAX` (2,147,483,647) and wraps
negative. The bar's OHLC is otherwise internally valid, which rules out a
corrupt record. Not a parser issue (`float(c[5] or 0)` does no truncation), not
an instrument-mapping issue (the key resolves correctly and neighbouring days
are right), not a corporate action.

**Action taken: none.** The validator was not modified, the candle was not
written, and the recovered value was not substituted — `+2^32` is an inference,
and manufacturing a volume from an assumption is exactly what this programme
has refused throughout. `IDEA.NS` remains **ERROR** and is documented here.

Confirmation the guard held: **rows with `volume < 0` anywhere in `candles`: 0.**

---

## 3. ETF replacement — COMPLETE

Run separately, with its own checkpoint, never merged into the equity audit.

| | run 1 | run 2 (idempotency) |
|---|---|---|
| attempted | 232 | 232 |
| **SUCCESS** | **232 (100%)** | 232 |
| NO_DATA / NO_KEY / ERROR | 0 / 0 / 0 | 0 / 0 / 0 |
| rows received | 270,658 | 270,658 |
| **inserted** | **263,240** | **0** |
| already existing | 7,418 | 270,658 |
| rejected | 0 | 0 |

Validation of the resulting ETF canonical set:

| check | result |
|---|---|
| canonical rows | 270,660 |
| non-`03:45` rows created | **0** |
| future sessions | **0** |
| duplicate (symbol, session) | **0** |
| OHLCV violations | **0** |
| weekend dates | 10 — **all declared** |
| ETF symbols with canonical history | 232 |
| reproducibility vs fresh fetch | 7,407/7,418 (**99.85%**) |
| legacy 00:00 / 18:30 after the run | **UNCHANGED** |

---

## 4/6. Migration candidates — counts

### Symbol-level classification

| category | symbols | legacy rows |
|---|---|---|
| `SAFE_REPLACEMENT` | 758 | 606,139 |
| `PARTIAL_REPLACEMENT` | 1,419 | 2,995,197 |
| `ETF_PENDING` | 76 | 148,061 |
| `NO_REPLACEMENT` | 1,104 | 1,104,952 |
| **TOTAL** | **3,357** | **4,854,349** |

### Row-level — this is the deletion set

Matched on **`symbol` + `trading_session_date` only**. Price equality was
deliberately *not* required: the legacy series carries a different historical
price basis, and replacing that basis is the entire purpose of the migration.

| | rows | share |
|---|---|---|
| total legacy 00:00 rows | 4,854,349 | 100% |
| **rows with a canonical session — deletion candidates** | **3,567,605** | **73.49%** |
| **rows without — MUST PRESERVE** | **1,286,744** | **26.51%** |

Coverage improved from 68.50% to 73.49% because of the ETF run.

**Criterion A (reader resolves the session) verified**, not assumed — sampled
`SAFE_REPLACEMENT` symbols read through the production `daily_series` path:

```
MARATHON.NS   2,404 legacy sessions -> 2,404 resolved, 0 missing
KINGFA.NS     2,376 -> 2,376, 0 missing      PODDARMENT.NS 2,370 -> 2,370, 0
HEADSUP.NS    2,355 -> 2,355, 0 missing      TCIEXP.NS     2,354 -> 2,354, 0
HISARMETAL.NS 2,336 -> 2,336, 0 missing
```

Per-symbol detail for all 3,357 symbols — legacy/canonical counts, intersection,
legacy-only, canonical-only, replacement %, and first/last sessions on both
sides — is in the companion CSV.

### Why `ETF_PENDING` persists after a 100%-success ETF run

Not a run failure. Those 76 symbols' legacy history simply **predates the Upstox
window**: legacy starts 2016-01-01, canonical starts 2016-09-07.

```
JUNIORBEES.NS  legacy 2,597 rows 2016-01-01..2026-06-26
               canon  2,477 rows 2016-09-07..2026-09-08   legacy-only 172
LIQUIDBEES.NS  same shape, legacy-only 172
```

Only **7,324 sessions** across all 76 are genuinely unreplaced — the
pre-September-2016 tail, which no Upstox run can recover.

---

## 7. Unreplaceable identities (no speculative mapping)

For the 1,104 `NO_REPLACEMENT` symbols:

| identity class | symbols | legacy rows |
|---|---|---|
| `series_trade_for_trade(-BE/-BZ)` | 266 | 449,664 |
| `unknown_no_master_entry` | 214 | 292,022 |
| `series_sme(-SM/-ST)` | 549 | 272,609 |
| **`in_upstox_master`** | **46** | **78,978** |
| `series_other(-IV)` | 19 | 8,064 |
| `index` | 3 | 2,575 |
| `series_other(-E1)` | 5 | 655 |
| `series_other(-P1)` | 2 | 385 |

Classification is by symbol form and instrument-master presence only. No symbol
was speculatively mapped to a renamed or merged identity.

**Potentially recoverable identities:**

* **46 symbols / 78,978 rows — `in_upstox_master`.** All 46 are explained by a
  backfill ERROR (45 by the 2025-04-26 synthetic bar, plus `IDEA.NS`). Zero are
  unexplained. Recoverable if the all-or-nothing validation policy is revisited.
* **214 symbols / 292,022 rows — `unknown_no_master_entry`.** Plain symbol
  forms absent from the master: likely delisted, renamed or merged. Determining
  which requires an external corporate-actions source this project does not
  have. **Genuinely unrecoverable today, but not proven permanently so.**
* **841 symbols / ~731,000 rows — series forms** (`-BE`, `-BZ`, `-SM`, `-ST`,
  `-IV`, `-E1`, `-P1`). Upstox publishes no instrument key for any series form
  (verified in Phase 2H), so these are unrecoverable from this source. They are
  also excluded from the model dataset by `MODEL_ELIGIBLE_CLASSES`.

---

## 8. Database safety

| metric | task start | now | delta |
|---|---|---|---|
| total candles | 39,896,619 | 40,162,349 | +265,730 |
| 1d total | 9,574,304 | 9,837,544 | +263,240 |
| **03:45** | 3,714,496 | 3,977,736 | **+263,240** (authorised ETF run) |
| **00:00** | 4,854,349 | 4,854,349 | **0** |
| **18:30** | 1,002,584 | 1,002,584 | **0** |

```
LEGACY 00:00  n=4,854,347  sum=3,660,724,845.45   UNCHANGED
LEGACY 18:30  n=1,002,584  sum=1,262,947,141.73   UNCHANGED
```

Legacy rows updated: **0**. Deleted: **0**. Normalized: **0**. Timestamps
altered: **0**. The only growth is additive canonical rows from the authorised
ETF backfill; the 2,490-row difference in the total is intraday rows written by
ordinary production tasks during the window.

---

## 9. Final answers

1. **Exact replacement candidate count: 3,567,605 legacy 00:00 rows** (73.49%)
   — matched on symbol + session date, reader-resolution verified.
2. **Exact preserved count: 1,286,744 rows** (26.51%) that must not be deleted.
3. **Remaining addressable gaps:**
   * 78,978 rows / 46 symbols — blocked by the all-on-nothing policy against the
     2025-04-26 synthetic bar and the IDEA overflow. Needs a policy decision.
   * 7,324 sessions / 76 ETF symbols — pre-Sept-2016 tail, unrecoverable from
     Upstox.
   * ~292,022 rows / 214 symbols — unknown identities, need an external
     corporate-actions source.
4. **ETF result: PASS** — 232/232, 263,240 rows, idempotent, zero violations.
5. **2025-04-26 result: NOT a trading session.** Calendar unchanged, 57 symbols
   left ERROR, validation not weakened.
6. **Negative-volume result: upstream Upstox int32 overflow on `IDEA.NS`.**
   True volume ≈ 4,213,707,883. Not written, not substituted, symbol left ERROR.
7. **Potentially recoverable historical identities: 260 symbols / 371,000 rows**
   — 46 in-master (recoverable by policy change) plus 214 unknown-identity
   (recoverable only with an external source). The remaining 841 series-form
   symbols are unrecoverable from Upstox.
8. **Controlled deletion phase: NOT READY.**

### Why not ready

The mechanics are sound — the candidate set is exact, reader-verified and
reconstructible from the CSV. Three things still block a deletion:

* **The 00:00 price basis question is still unanswered** (open since Phase 2E).
  Deletion does not merely swap timestamps: for some symbols the two series
  differ by a constant factor (INFY ~2.16%), so every backtest and trained model
  built on legacy prices changes its inputs. Nothing in this task addressed
  that.
* **26.51% must be preserved**, and any deletion must be executed per row on
  `symbol + session_date` — never by symbol, and never on an aggregate
  percentage.
* **The 46 recoverable symbols should be recovered first**, so the deletion set
  is computed once against a final canonical dataset rather than twice.

---

**No legacy 00:00 or 18:30 data was deleted, normalized, or altered. No
speculative symbol mapping was performed. The 1,286,744 unreplaced rows —
including all 1,025,974+ with no canonical identity — remain untouched.
Migration remains NO-GO.**
