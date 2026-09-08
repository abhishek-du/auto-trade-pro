# PHASE 2M.1 — V1 DATASET INTEGRITY GATE

**Date:** 2026-09-08 · **Final gate: PASS, conditional on a mandatory quarantine**

No model trained. No 18:30 migration. No 00:00 deletion. No backfill. No
fundamentals, sector or pre-open work. Nothing deleted, normalised or
retimestamped. One production code path was changed — the training-target
constructor in `engine/ml_predictor.py` — because it was found defective.

Three concerns were raised independently. All three were verified against the
code and the database. **One was confirmed and fixed. One was confirmed as a
problem but its stated cause was contradicted by the evidence. One is now
settled with direct measurement.**

| § | Area | Verdict |
|---|---|---|
| 1 | Target NaN handling | **FAIL → FIXED** |
| 2 | Corporate-action investigation | **CONTRADICTED** (they are not corporate actions) |
| 3 | Price basis | **PASS** — adjusted, consistently; recommendation **B** |
| 4 | Invalid bars | **PASS** |
| 5 | Universe / survivorship | **PASS** — SURVIVORSHIP BIAS = PRESENT |
| 6 | High/low target semantics | **PASS** |
| 7 | 10,000-row validation sample | **PASS** |
| 8 | Final gate | **PASS (conditional)** |

**All row counts in this report were measured at 2026-09-08 18:28 IST.** The
database is live — the regime-daily writer runs every five minutes and the daily
syncs keep adding today's bars — so a count taken an hour later differs. Between
17:5x and 18:28 the usable-row count moved 4,074,107 → 4,075,079 as 972 symbols
gained their 2026-09-08 bar. Counts are a measurement, not a constant; the
dataset build must record its own `as_of` and row count.

**Artefacts**
- `docs/2026-09-08_V1_ANOMALY_MANIFEST.csv` — all 293 anomalies, one row each, fully attributed
- `docs/2026-09-08_V1_VALIDATION_SAMPLE.csv` — the 10,000-row sample (100 symbols × 100 sessions)
- `autotrade-backend/scripts/build_v1_validation_sample.py` — regenerates it deterministically
- `autotrade-backend/tests/test_step2m1_target_integrity.py` — 21 regression tests

---

## 1. Target NaN audit — **FAIL, now FIXED**

### The defect, confirmed

`engine/ml_predictor.py::_onehot_labels` ended in `.fillna(0)`:

```python
fut = df["close"].pct_change(1).shift(-1).fillna(0).values
cls = np.where(fut > _LABEL_THRESHOLD, 2, np.where(fut < -_LABEL_THRESHOLD, 0, 1))
```

`shift(-1)` leaves the **last** row's target undefined — that session's outcome
has not happened yet. `0.0` sits inside the ±0.5% band, so it classified as
**FLAT**. The exact path raised: `NaN → fillna(0) → falsely labelled FLAT`.

Demonstrated on a constructed series before the fix — final bar +22.6%, its own
next session unknown:

```
raw next-session return : [0.0100, 0.0099, 0.0098, 0.0097, 0.0096, 0.0095, 0.2264, nan]
labels (0=DOWN 1=FLAT 2=UP): [2, 2, 2, 2, 2, 2, 2, 1]
                                                    ^ NaN → FLAT
```

A **second** hole shared the same fill: `pct_change` off a `0.00` close returns
`inf`, which `fillna` does not touch, and `inf > 0.005` classified as a
confident **UP**:

```
zero-close raw returns : [inf, 0.1000, 0.0909, nan]
zero-close labels      : [ 2 ,    2  ,    2  ,  1 ]
                           ^ a division by zero taught as a strong UP
```

### Rows affected

| | |
|---|---|
| training universe (`nse_symbols + nse_mid_symbols`) | **33 symbols** |
| poisoned rows **per training run** | **33** — exactly one per symbol, always the most recent session |
| canonical bars in the 730-day training window | 16,401 (~497/symbol) |
| training sequences per symbol (`_SEQUENCE_LENGTH` 60) | ~437 |
| share of one symbol's training rows | 0.23% |
| **share of its validation rows** (80/20 split puts the tail in validation) | **~1.1%** |

The row was not merely trained on — the 80/20 chronological split places the
newest rows in **validation**, so the fabricated FLAT was also *scored* as if it
were a real observation. Reported `val_accuracy` was measured partly against a
label that never existed.

The second target path, `train_random_forest`, already dropped its undefined
tail (`X_raw[:-_RF_LABEL_WINDOW]`), so its `.fillna(0)` never produced a
phantom tail row — but it left the same interior `inf`/`NaN` hole open.

### The fix

`_onehot_labels` now returns `(onehot, valid_mask)` and performs **no fill**.
A row is trainable only when its own close and the next close are positive
finite prices and the resulting return is finite. `_make_sequences` **skips**
invalid rows rather than filling them; `train_model` passes the mask and logs
what it dropped. `train_random_forest` uses the same rule, replacing its tail
slice with the mask so interior invalid bars go too.

**A genuine 0.0% return is a real outcome and still labels FLAT.** The two cases
carry the *same label* and are separated only by the mask — which is precisely
why a mask exists rather than a sentinel return value.

After the fix, on the same constructed inputs:

| case | label | valid |
|---|---|---|
| unknown tail (no D+1 yet) | FLAT | **False → dropped** |
| genuine 0.0% return | FLAT | **True → kept** |
| zero close (`inf` return) | — | **False → dropped** |
| a drop to a 0.00 close | — | **False → dropped** (a bad bar, not a −100% move) |

Sequences: a 70-bar series that previously yielded 10 training samples now
yields **9** — the one dropped is the unknown-target tail, and nothing else.

### Proof that no NaN target becomes zero

`tests/test_step2m1_target_integrity.py` — **21 tests, all passing**:

- the last row is marked invalid; every earlier row stays valid
- a +22.6% final bar is learned on its own row and is not relabelled FLAT
- `_make_sequences` skips exactly the invalid tail
- genuine zero returns stay FLAT **and valid**; the ±0.5% threshold boundaries
  are unchanged
- FLAT-from-unknown and FLAT-from-real carry the same label and different masks
- zero, negative and NaN closes are invalid on both sides of the transition
- **an exhaustive check**: for a series seeded with zero closes, every row whose
  raw forward return is non-finite is asserted invalid
- `_onehot_labels` contains no `fillna` (asserted against the **parsed AST**, not
  a substring — the docstring quotes the call it removed)
- the RF path has no `fillna` on a `shift`/`pct_change` expression
- `train_model` passes the mask to `_make_sequences`

**The V1 dataset predicate never had this defect.** A row exists only when the
next canonical session exists and both closes are positive, so an unknown
target produces *no row*, not a filled one. Two tests pin that contract.

Full suite after the change: **2,730 passed, 27 failed, 5 errors** — identical
to the pre-change baseline (all pre-existing).

---

## 2. Corporate-action investigation — **CONTRADICTED**

All 293 rows are in `docs/2026-09-08_V1_ANOMALY_MANIFEST.csv` with symbol,
prediction session, target session, `close[D]`, `close[D+1]`, `high[D+1]`,
`low[D+1]`, `open[D+1]`, both volumes, return, ratio, session index, both
20-session level means, provenance, and a per-row classification with its
evidence. Nothing was deleted.

### The premise is contradicted by the evidence

Phase 2M called these "corporate-action artefacts". **That was wrong, and §3
proves it: the canonical series is retroactively adjusted, so a corporate
action leaves no jump at all.** Six known NSE actions were checked and every
one is absorbed cleanly. Whatever these 293 rows are, they are **not corporate
actions**.

`REAL_CORPORATE_ACTION: 0 of 293.`

There is also **no corporate-action reference anywhere in the database** to
check against: `market_events` holds only `EARNINGS`, `FNO_EXPIRY`, `HOLIDAY`
and `RBI_MPC`, all dated 2026. No splits, bonuses, or ex-dates are stored.

### Provenance: the stored bars are faithful

Every stored bar in five anomaly windows was compared against a **fresh Upstox
v3 fetch today**. All matched to the paisa, volumes included:

```
BSHSL.NS    2021-02-01  stored C=13.55   fresh C=13.55    stored V=45,100   fresh V=45,100
BSHSL.NS    2021-02-02  stored C=139.75  fresh C=139.75   stored V=8,833    fresh V=8,833
SAKUMA.NS   2017-11-13  stored C=4.93    fresh C=4.93     stored V=15,061,395  fresh V=15,061,395
SHANTI.NS   2018-11-01  stored C=19.40   fresh C=19.40    stored V=3,000    fresh V=3,000
PRIVISCL.NS 2020-08-20  stored C=0.15    fresh C=0.15     stored V=452,355,000 fresh V=452,355,000
```

A control window (RELIANCE.NS, 2026-08-25 → 09-05) matched on 9 of 9 bars; the
only difference found anywhere was RELIANCE's *same-day* volume
(13,022,095 stored vs 13,031,534 fresh), a bar still being written.

**The ingest pipeline is exonerated. These values come from the vendor.**

### What they actually are

Three worked examples, each with the surrounding bars:

**BSHSL.NS — a level splice, volume moving inversely**
```
2021-02-01  C=  13.55  V=  45,100     <== D
2021-02-02  C= 139.75  V=   8,833     x10.3 price, ÷5 volume
2021-02-05  C= 135.20  V=   1,461     the new level persists
```
and it recurs on 2021-07-28 (32.00 → 326.40) and 2022-03-28 (40.45 → 397.95).
A company does not reverse-split three times in fourteen months, and in an
adjusted series a reverse split would be absorbed anyway. This is **one segment
of the history carrying the adjustment and another not**.

**SHANTI.NS — a single bar from a different price scale**
```
2018-10-30  C= 263.25  V= 26,006
2018-10-31  C= 271.95  V= 34,254
2018-11-01  C=  19.40  V=  3,000     <== D — inserted into a 260-level series
2018-11-02  C= 259.90  V=  8,434
```

**PRIVISCL.NS — a frozen placeholder feed, not a market**
```
2020-08-12 .. 2020-08-20   C = 0.15 every session, V = 227M–634M
2020-08-21                 C = 553.35, V = 201,697
```
Seven consecutive sessions at an identical price on hundreds of millions of
shares is not trading.

### Classification

| class | rows | share |
|---|---:|---:|
| **UNPROVEN** | 115 | 39.2% |
| SERIES_CHANGE | 87 | 29.7% |
| SYNTHETIC/BAD_BAR | 50 | 17.1% |
| LISTING/RELISTING | 21 | 7.2% |
| DATA_ERROR | 20 | 6.8% |
| **REAL_CORPORATE_ACTION** | **0** | 0.0% |
| OTHER | 0 | 0.0% |
| **total** | **293** (135 symbols) | |

Rules applied, in order, each from measured structure:

- **SYNTHETIC/BAD_BAR** — a zero-volume bar at the transition; or the 20
  sessions before D hold ≤3 distinct closes (29 rows are frozen like this);
  or ≥5 of them have zero volume (8 rows); or a sub-₹2 close on >10M shares.
- **DATA_ERROR** — an isolated bar >60% away from the mean of its 21-bar
  neighbourhood while the other side of the transition is within 40%.
- **LISTING/RELISTING** — within the first 10 sessions of the symbol's series.
- **SERIES_CHANGE** — both levels stable within 40% across the splice *and*
  volume moving inversely by ≥5×: the signature of an adjustment applied to one
  segment only.
- **UNPROVEN** — everything else: 41 persistent level splices with no volume or
  liveness signature, and 74 with no strong structural signature at all.

**UNPROVEN is 39% and stays 39%.** 41 of the 115 are persistent level splices
with no volume or liveness signature to attribute a cause; the other 74 carry no
strong structural signature at all. Attributing a vendor root cause needs a
corporate-action reference this database does not have. Guessing would be worse
than the gap.

### What *is* proven for all 293

| property | result |
|---|---|
| exceeds the widest NSE price band (20%) | **293 / 293 (100%)** |
| already gapped at the **D+1 open** — no intraday path to it | **273 / 293 (93.2%)** |
| both price levels persist across ±20 sessions | 200 / 293 (68.3%) |
| is a real corporate action | **0 / 293** |
| is economically capturable | **0 / 293** |

NSE applies a price band to every equity — a fixed 2%, 5%, 10% or 20% for most
securities, and a dynamic band (relaxed in steps during the session) for F&O
names. The exact ceiling varies; **no configuration of it permits a ×10 move,
let alone ×3688, in one session.** All 293 are non-economic artefacts of the
vendor series. That is the property the dataset needs, and it is proven for
every row — the open question is *which* vendor mechanism produced each one,
not whether any is a real move.

Corroboration from §7: across 10,000 clean sample rows the observed return range
is **−19.47% to +20.00%** — the band, exactly where it should be.

### Contamination is confined to the transitions

An independent sweep for *off-level* bars (close >3× or <⅓ the median of its
21-bar neighbourhood) across the 135 affected symbols found **98 off-level bars
in 234,285 bars (0.04%)**, in 22 symbols; only one symbol (WEWIN.NS) exceeds 20.
Across the whole canonical series, session-to-session moves of ×3 or more number
**155 in 4,075,079 (0.0038%)**.

The defect is a small number of splice points, not a pervasively corrupted
history.

---

## 3. Price basis — **PASS**, recommendation **B**

Determined from evidence, not documentation. Six NSE corporate actions with
unambiguous ratios were checked in the stored canonical series:

| symbol | ex-date | action | raw price would | **observed** |
|---|---|---|---|---|
| RELIANCE.NS | 2024-10-28 | 1:1 bonus | halve | ×1.005 — **no jump** |
| INFY.NS | 2018-09-11 | 1:1 bonus | halve | ×1.005 — **no jump** |
| IRCTC.NS | 2021-10-29 | 1:5 split | →⅕ | ×0.926 — **no jump** |
| TATASTEEL.NS | 2022-07-28 | 1:10 split | →1/10 | ×1.046 — **no jump** |
| BAJFINANCE.NS | 2025-06-16 | 1:2 split + 4:1 bonus | →~1/10 | ×1.005 — **no jump** |
| WIPRO.NS | 2019-03-06 | 1:3 bonus | →¾ | ×1.015 — **no jump** |

Level corroboration: Infosys traded near ₹1,400 before its 2018 bonus and the
series stores ~₹730; Tata Steel near ₹940 before its 2022 split and the series
stores ~₹94; Bajaj Finance near ₹9,400 and the series stores ~₹940.

**Volume is adjusted too** — Tata Steel's pre-split sessions store ~48M shares
where the unadjusted tape would show ~4.8M.

The same test on the legacy **18:30** family gives identical adjusted values
(offset by its known session-date-minus-one convention), so both members of the
reader's `_RAW_TIMES` family share one basis. No basis mixing.

### Verdict

```
The canonical 03:45 series is RETROACTIVELY ADJUSTED for corporate actions,
in price AND volume, consistently across six independent events and across
both timestamp families in the same basis group.
```

**Recommendation B — raw price return is valid only after corporate-action-aware
filtering.** Not A: the adjustment is demonstrably *incomplete* for some
symbols, and those breaks are the 293 rows of §2. Not C: an adjusted series
already exists, so no transformation is needed. Not D: the basis is proven, not
inferred.

No adjusted-price transformation was implemented in this phase.

### A flagged contradiction — reported, not fixed

`engine/daily_series.py:77` declares:

```python
_RAW_TIMES = ("03:45", "18:30")           # Upstox, unadjusted
```

**The evidence above contradicts the word "unadjusted".** `crawler/corporate_actions.py:224`
depends on that belief: it requests `basis="raw"` to compare a daily close
against the next morning's intraday open, on the stated reasoning that "intraday
candles are unadjusted, so the daily side must be unadjusted too". The daily
side is **adjusted**.

Whether that guard misfires in practice depends on how quickly Upstox restates
history at an ex-date, which cannot be established from stored data — it needs
observation across a live ex-date. **UNPROVEN as to live impact; CONTRADICTED as
to the code's stated assumption.** It is a live-trading guard that rewrites
position quantities and is out of this phase's scope; it should be the subject
of its own investigation. The next scheduled opportunity to observe one is worth
scheduling against.

---

## 4. Invalid bars — **PASS**

The eight zero-close cases are confirmed:

```
MAZDOCK.NS 2017-12-04  O=H=L=C=0.00  V=7,154,666
MAZDOCK.NS 2018-01-01  O=H=L=C=0.00  V=  670,998
MAZDOCK.NS 2018-02-05  O=H=L=C=0.00  V=2,430,000
MAZDOCK.NS 2018-03-05  O=H=L=C=0.00  V=    2,012
MAZDOCK.NS 2018-04-02  O=H=L=C=0.00  V=  100,000
MAZDOCK.NS 2018-05-07  O=H=L=C=0.00  V=   20,000
MAZDOCK.NS 2018-06-04  O=H=L=C=0.00  V=  840,852
MAZDOCK.NS 2018-07-02  O=H=L=C=0.00  V=  166,400
```

Mazagon Dock's first real bar is **2020-10-12** (C=86.00, V=79.9M) — its IPO.
The eight bars are **pre-listing placeholders**, monthly-spaced, carrying
fabricated volume against a zero price. They are excluded by the V1 `close > 0`
predicate and were **not deleted** from `candles`.

Exhaustive sweep over all 4,076,817 canonical daily bars:

| check | rows | symbols |
|---|---:|---:|
| `close <= 0` | **8** | 1 |
| `close < 0` | 0 | 0 |
| `open`/`high`/`low` ≤ 0 | 8 | 1 |
| `high < low` | **0** | 0 |
| `close` outside `[low, high]` | **0** | 0 |
| `open` outside `[low, high]` | **0** | 0 |
| `volume < 0` | **0** | 0 |
| `volume IS NULL` | 0 | 0 |
| NULL in OHLC | **0** | 0 |
| NaN close | **0** | 0 |
| Infinite close | **0** | 0 |
| future-dated | **0** | 0 |

Return magnitudes over rows with a valid next session:

| band | rows |
|---|---:|
| \|r\| > 20% | 1,713 |
| \|r\| > 50% | **293** |
| \|r\| > 100% | 93 |
| divide-by-zero candidates (`close ≤ 0` on either side) | **8** — all MAZDOCK |

Under the full V1 predicate (which additionally requires `high[D+1] > 0` and
`low[D+1] > 0`) the >20% count is 1,712. The negative-volume defect repaired in
Phase 2I (int32 overflow) has not recurred.

---

## 5. Universe and survivorship — **SURVIVORSHIP BIAS = PRESENT**

```
SURVIVORSHIP BIAS = PRESENT
```

V1 is **not** a ten-year dataset. It is a ten-year *window* over a universe
drawn from the current instrument master, in which only 40% of symbols carry
the full decade.

### Depth

| canonical sessions | symbols | share |
|---|---:|---:|
| < 1 year (< 250) | **474** | 17.5% |
| 1–3 years (250–739) | 364 | 13.5% |
| 3–5 years (740–1,239) | 297 | 11.0% |
| 5–10 years (1,240–2,399) | 475 | 17.6% |
| **full 10 years (≥ 2,400)** | **1,092** | **40.4%** |
| **total** | **2,702** | |

By class: 2,468 NSE equities, 232 ETF/InvIT, 2 indices.

A 200-session moving average is undefined for the 474 short-history symbols and
thin for the 364 above them.

### Historical-only and unresolved

| category | count | detail |
|---|---:|---|
| current NSE master EQ instruments | 10,211 | master's EQ bucket, broader than the equity universe |
| with canonical daily history | 2,700 | + `^NSEI`, `^NSEBANK` = 2,702 |
| without canonical history | 7,511 | SME, trusts, non-equity forms — a scope boundary, not a gap |
| **unresolved identities** | **173** | 132 NSE equities + 41 ETFs, **204,993 legacy rows**, 2016-01-01 → 2026-08-27 |
| non-equity series forms (`-BE`/`-SM`/`-BZ` etc.) | 1,524 | outside the V1 instrument classes |
| **delisted before the 2026 master snapshot** | **unmeasurable** | see below |

Every one of the 173 was **still trading in 2026**, so they are not delistings —
they are names the backfill could not resolve to an Upstox instrument key
(renames, series variants, SME listings). No speculative identity mapping was
performed: a wrong mapping splices two companies' histories together silently
and is worse than an absence.

**Delisted and renamed symbols are not detectable from inside this database.**
There is no historical instrument-master snapshot to compare against, so the
size of the survivorship hole cannot be stated — only that it exists. Any V1
result must carry that caveat, and a weekly master snapshot should start now so
that a future phase can measure what this one cannot.

### A gap defect found while building the manifest — **new finding**

`RAINBOW.NS` carries prediction session **2019-06-04** with target session
**2022-05-10**. That is the symbol's genuine next canonical session — its
pre-IPO bars sit three years before its listing — but a "next-session return"
spanning 1,071 days is not a next-session return.

| target lands | rows | share |
|---|---:|---:|
| within 4 calendar days (a true adjacent session) | 4,060,891 | **99.652%** |
| 5–7 days | 10,583 | 0.260% |
| 8–30 days | 2,875 | 0.071% |
| **> 30 days** | **730** | 0.018% — in 244 symbols |
| > 365 days | 53 | |
| largest gap | **2,244 days** | |

**14,188 rows (0.348%) have a target more than four calendar days after their
prediction session.** 73 of the 293 anomalies are gap rows — a symbol that stops
trading for years and resumes at a different price level produces exactly the
splice §2 describes.

The V1 row definition needs a **maximum-gap constraint** alongside the
next-session rule. It is not a substitute for the quarantine, though: restricting
to adjacent rows *alone* leaves the return std at **1.832**, because 220 of the
293 anomalies are between genuinely adjacent sessions. Measured combinations:

| filter | rows | std of `next_session_return` |
|---|---:|---:|
| none | 4,075,079 | **1.89920** |
| adjacent only (gap ≤ 4d) | 4,060,891 | 1.83173 |
| `extreme_return_flag` excluded | 4,074,786 | **0.02937** |
| both | 4,060,686 | **0.02924** |

The quarantine does the work; the gap constraint is a correctness fix, not a
variance fix.

### The exact proposed V1 universe

```
measured at             : 2026-09-08 18:28 IST
earliest usable session : 2016-09-06   (population; 4 symbols reach 2016-01-01)
latest usable session   : 2026-09-07   (its target is 2026-09-08, the last stored session)
symbols                 : 2,702        (symbols contributing at least one usable row)
potential rows          : 4,075,079    (V1-A / V1-B)
                          264,577      (V1-C, news floor 2026-04-07)
quarantined (extreme)   : 293          (0.0072%) — flagged, retained, excluded from training
excluded (target gap>4d): 14,188       (0.348%)  — see the gap defect above
```

---

## 6. High/low target semantics — **PASS**

Both raw targets are retained and are mathematically constructible from the
canonical series:

```
next_session_high_return = high[D+1] / close[D] - 1
next_session_low_return  = low [D+1] / close[D] - 1
```

Verified on all 10,000 sample rows: 0 nulls, 0 non-finite values.

### RAW OUTCOME is not EXECUTABLE OUTCOME

**RAW OUTCOME** — what price the next session reached. This is what the two
targets measure, and it is all the daily bar can support.

**EXECUTABLE OUTCOME** — whether a strategy could actually capture that price:
whether the high came before the reversal, whether a stop was hit first, how
long the move took to peak. **The daily bar cannot answer any of this.** A bar
that opened at the low, ran to the high, and closed at the low is
indistinguishable from its exact opposite.

The base rates make the distinction concrete. Across all 4,074,107 rows,
**91.52%** of sessions trade above the prior close at some point and **84.47%**
trade below it (sample: 91.99% and 85.63%). **Intraday touch is nearly free.** A
label defined as "did it touch +2%" would score impressively and mean almost
nothing, because touching is the base rate — while the *close* is positive only
46.69% of the time.

MFE-before-reversal, time-to-peak and TP-before-SL all require the intraday
path. **Intraday history begins 2026-06-18 — under three months.** Those labels
are not available for V1 and no proxy was substituted.

No thresholds were chosen.

---

## 7. 10,000-row validation sample — **PASS**

Built by `autotrade-backend/scripts/build_v1_validation_sample.py`
(seed 20260908, deterministic), reading **only** through `engine.daily_series`
with an explicit `as_of`. No table was written.

**100 symbols × 100 sessions = 10,000 rows.** Symbols drawn at random from the
1,000+ carrying ≥365 canonical sessions; each row needs 260 sessions of warm-up.
30 features spanning PRICE, VOLUME and MARKET (NIFTYBEES proxy), plus the three
raw targets and the quarantine flag.

### Row contract

| gate | result |
|---|---|
| rows / symbols | 10,000 / 100 |
| duplicate `(symbol, prediction_session)` | **0** |
| `target_session <= prediction_session` | **0** |
| **`max_feature_session > prediction_session`** | **0** |
| non-chronological ordering within a symbol | **0** |

Every feature is computed from the slice `bars[:i+1]`, so *features ≤ D* is
structural, not merely tested. It was then **re-verified independently**: 200
random rows were read back through the reader with `as_of = D`.

| independent re-verification (200 rows) | result |
|---|---|
| the `as_of` read returned a session after D | **0** |
| the stored target is not the next resolved session | **0** |

### Numerical

| check | result |
|---|---|
| NaN across all feature and target cells | **0** |
| infinite values | **0** |
| `close_D <= 0` | **0** |
| highest null rate | `body_pct`, `upper_wick_pct`, `lower_wick_pct` — **0.2%** |

Those three nulls are the designed guard: they divide by `high − low`, which is
zero on a locked-circuit bar, and emit `NULL` rather than `0`.

### Summary statistics (n = 10,000)

| target | mean | median | std | p1 | p99 | min | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| `next_session_return` | +0.00070 | −0.00069 | 0.02398 | −0.0524 | +0.0796 | **−0.1947** | **+0.2000** |
| `next_session_high_return` | +0.02090 | +0.01538 | 0.02343 | −0.0116 | +0.1130 | −0.1262 | +0.2000 |
| `next_session_low_return` | −0.01537 | −0.01222 | 0.01759 | −0.0710 | +0.0164 | −0.2000 | +0.0996 |

| rate | sample | full population |
|---|---:|---:|
| `next_session_return` positive | 47.38% | 46.69% |
| `high_return` positive | 91.99% | 91.52% |
| `low_return` negative | 85.63% | 84.47% |
| `extreme_return_flag` set | **0** | 293 |

Two things to note. The sample's return range is **−19.47% to +20.00%** — the
NSE price band, cleanly, with nothing outside it. And the std of 0.02398 sits
close to the population's quarantined std of 0.02937, while **zero** of the
10,000 rows carries the extreme flag: the contamination is genuinely rare and
the sample is clean.

The negative median against a positive mean reproduces in the sample, so the
right-skew is a property of the cross-section, not of the outliers.

---

## 8. Final gate — **PASS (conditional)**

### Can the V1 daily price/volume dataset safely be materialized?

**Yes — conditional on the quarantine below being part of the build, not an
afterthought.**

| required condition | status |
|---|---|
| no target NaN→0 issue | **MET** — fixed in both target paths, 21 regression tests; the dataset predicate never had it |
| no unresolved critical price-basis corruption | **MET** — basis proven adjusted and consistent across 6 events and both timestamp families |
| no future leakage | **MET** — 0 violations across 10,000 rows, plus independent reader re-verification on 200 |
| invalid bars handled explicitly | **MET** — 8 rows, 1 symbol, identified as pre-listing placeholders, excluded by predicate, not deleted |
| universe / survivorship documented | **MET** — SURVIVORSHIP BIAS = PRESENT, with depth buckets and the 173 unresolved identities named |

### Mandatory conditions on materialization

1. **`extreme_return_flag` must be written into the table**, not applied at read
   time. 293 rows move the population std from 0.029 to 1.899; a build that
   forgets the flag produces a target whose variance is meaningless.
2. **Excluded rows stay in the table, flagged.** Deleting them hides the
   problem and makes the exclusion unauditable.
3. **The `close > 0` and next-session predicates are part of the row
   definition**, not a downstream filter.
4. **A maximum target-session gap must be part of the row definition.** 14,188
   rows currently pair a prediction session with a target up to 2,244 days
   later; without the constraint those are labelled as next-session outcomes.
5. **Every V1 result carries the survivorship label** — universe from the
   current master, delisted-before-2026 names absent and unmeasured, 173 further
   symbols excluded for unresolved identity.

### On the 293

The instruction was not to call V1 ready while the anomaly set is unexplained.
It is explained where it counts and honestly incomplete where it is not:

- **Proven**: they are not corporate actions (the series is adjusted);
  they are faithfully stored (fresh re-fetch matches to the paisa); all 293
  exceed every NSE price band; 93.2% are already gapped at the D+1 open, so no
  intraday path to them exists. **None is an economically capturable move.**
- **Attributed**: 178 of 293 (60.8%) to a specific mechanism — series change,
  synthetic bar, listing boundary, isolated data error — each with its evidence
  in the manifest.
- **Open**: 115 of 293 (39.2%) remain **UNPROVEN** as to vendor root cause.
  Closing that needs a corporate-action reference the database does not have.

Root-cause attribution is not required to exclude a row that is proven
non-economic. It *is* required before anyone tries to repair rather than
quarantine — and that is a separate phase.

### Recommended next steps

1. Materialize **V1-A** with the quarantine flag and the depth filter; freeze it
   as a versioned artefact so results become reproducible.
2. Acquire a corporate-action reference (ex-dates and ratios). It closes the 115
   UNPROVEN rows, and it is the only way to validate the vendor's adjustment
   rather than trust it.
3. Start snapshotting the instrument master weekly. It costs nothing and it is
   the only path to ever *measuring* survivorship instead of declaring it.
4. Open a separate investigation into `corporate_actions.py`'s `basis="raw"`
   assumption, observed across a live ex-date. It guards real positions.
5. Choose labels from the §6 base rates, not by convention.

---

*No model trained. No 18:30 migration. No 00:00 deletion. No fundamentals,
sector or pre-open implementation. No rows deleted, normalised or
retimestamped.*
