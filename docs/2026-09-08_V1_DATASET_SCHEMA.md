# V1 PREDICTION DATASET — SCHEMA SPECIFICATION

**Version:** 1.1 — CONTRACT FROZEN (Phase 2M.2) · **Date:** 2026-09-08
Specification only — no dataset built, no model trained, no database mutation.

Changes from 1.0, all from Phase 2M.2: the training universe is frozen to NSE
equities only; the next-session rule is defined against the session calendar
rather than a calendar-day threshold; a warm-up requirement is stated
explicitly; the market proxy is renamed; and news moves out of the baseline
into a separately versioned extension.

---

## 1. What V1 predicts

```
prediction_cutoff = SESSION D CLOSE
target            = SESSION D+1   (the next valid NSE trading session)
```

Every feature in row D must satisfy `information_available_at <= D close`.

`D+1` is always the **next session on the NSE session calendar**, never
`D + 1 calendar day` and never "the next database row". The full rule is §1b.

Verified: 10,606 transitions in the source data go Friday → Saturday, and every
one is a declared NSE special session (Budget/DR Saturdays). A calendar rule
would have mislabelled all of them.

---

## 1a. Training universe — FROZEN

The objective is **NSE stock prediction**. The universe is therefore frozen to
tradeable NSE equity instruments, and nothing else becomes a training
observation.

### Training observations

```
2,468 NSE equity symbols   (InstrumentClass.NSE_EQUITY, canonical 03:45 history)
3,807,105 canonical bars
```

Only these produce rows. The universe is not expanded without a version bump.

### Market context — features only, never observations

| symbol | class | canonical sessions | range |
|---|---|---:|---|
| `NIFTYBEES.NS` | ETF | 2,478 | 2016-09-06 → 2026-09-08 |
| `BANKBEES.NS` | ETF | 2,478 | 2016-09-06 → 2026-09-08 |
| `^NSEI` | index | **12** | 2026-08-24 → 2026-09-08 |
| `^NSEBANK` | index | **12** | 2026-08-24 → 2026-09-08 |
| 230 further ETF/INAV symbols | ETF | — | — |

These 234 symbols contribute **feature values** and never **rows**. Mixing them
into the observation set would train one target model across three different
instrument types: an ETF's next-session return is a fund's tracking behaviour,
an index level is not tradeable at all, and neither is the thing the model is
being asked to predict. It would also let a handful of heavily-traded ETFs
dominate a cross-sectional fit.

### InvITs and REITs — EXCLUDED, structurally

**Zero InvIT or REIT rows exist in the canonical data, and none can enter.**
NSE lists them under dedicated series suffixes — `-IV` for InvITs, `-RR` for
REITs — and `classify_instrument()` routes any suffixed base to
`NON_EQUITY_SERIES`, which the backfill never fetched:

```
IRBINVIT-IV    IRB INVIT FUND                    INDIGRID-IV   INDIGRID INFRA TRUST
PGINVIT-IV     POWERGRID INFRA INVESTMENT TRUST  ANZEN-IV      ANZEN IND ENE YLD PLU TRU
IRBIT-IV       IRB INFRASTRUCTURE TRUST          ROADSTAR-IV   ROADSTAR INFRA INVT TRUST
SHREMINVIT-IV  SHREM INVIT                       EMBASSY-RR    EMBASSY OFFICE PARKS REIT
MINDSPACE-RR   MINDSPACE BUSINESS PARKS REIT     BIRET-RR      BROOKFIELD INDIA REIT
NXST-RR        NEXUS SELECT TRUST - REIT
```

The exclusion is **correct on its merits and not merely incidental**: an InvIT
or REIT distributes cash flow under a trust structure, its price responds to
yield and distribution announcements rather than to earnings, and its
next-session return is not the quantity this model predicts.

`POWERINDIA.NS` is **Hitachi Energy India Ltd**, an ordinary equity — it is
included, and it is named here because a naive `INDIA`/`TRUST` name filter
flags it by mistake.

---

## 1b. Next-session definition — FROZEN

```
target_session = the next session on the NSE session calendar
                 on which THIS SYMBOL has a canonical bar,
                 AND that session must be the calendar-adjacent one
```

Formally, with `market[]` the ordered NSE session calendar and `idx()` its
position function:

```
valid(D, T)  ⟺  idx(T) - idx(D) == 1
calendar_gap_days = (T - D).days          # recorded, never used as the test
```

**`calendar_gap_days` is a diagnostic column, not the rule.** It is computed and
stored on every row so that closures remain visible, and it decides nothing.

### Why a calendar-day threshold is wrong

A `target - prediction <= 4 calendar days` rule was measured against the
session-calendar rule across all 3,804,629 equity transitions:

| | rows |
|---|---:|
| flagged by the 4-day rule | 13,197 |
| genuinely wrong (target is not the symbol's next session) | **19,149** |
| in both sets | 9,275 |
| **wrong but ≤4 calendar days — the 4-day rule MISSES these** | **9,874** |
| **>4 calendar days but perfectly correct — the 4-day rule WRONGLY DROPS these** | **3,922** |

The 4-day rule is wrong in both directions at once. A symbol that skips a single
Tuesday produces a two-calendar-day gap and a defective row; a long weekend plus
a holiday produces a five-calendar-day gap and a perfectly correct one.

### The session calendar

Derived from the data and cross-validated: a date is an NSE session when at
least 10% of that year's peak equity-symbol count carries a canonical bar.

```
2,478 sessions, 2016-09-06 .. 2026-09-08
```

That is **exactly** `NIFTYBEES.NS`'s canonical session count — an independent
confirmation, since the ETF trades every session the market is open.

The threshold is safe: of 170 rejected dates, **167 carry 1–5 symbols** (the
early four-symbol ingest that predates the universe). Nine weekend sessions are
recognised and all nine are declared in `NSE_SPECIAL_SESSIONS`
(2019-10-27, 2020-02-01, 2020-11-14, 2023-11-12, 2024-01-20, 2024-03-02,
2024-05-18, 2025-02-01, 2026-02-01).

**Known coverage holes — three Muhurat sessions.** They are real NSE sessions
that the canonical data almost entirely lacks, so the derived calendar excludes
them:

| date | equity symbols with a bar | that year's peak |
|---|---:|---:|
| 2016-10-30 (Sun) | **21** | 1,114 |
| 2017-10-19 (Thu) | **18** | 1,210 |
| 2018-11-07 (Wed) | **29** | 1,267 |

Excluding them is the right call for the ~99% of symbols that have no bar, and
it mislabels transitions for the ~20 that do. This is a **data gap, not symbol
inactivity**, and it is recorded here rather than silently absorbed.

---

## 1c. Long-gap handling — FROZEN

Every transition that is not calendar-adjacent on the session calendar is
classified by **how many market sessions the symbol missed**:

| classification | rows | share | treatment |
|---|---:|---:|---|
| `ADJACENT` | 3,781,558 | 99.394% | **KEEP** |
| `VALID_LONG_CLOSURE` — >4 calendar days, **0 missed sessions** | 3,922 | 0.103% | **KEEP** |
| `SHORT_INACTIVITY` — 1–5 missed sessions | 15,475 | 0.407% | **EXCLUDE** |
| `SUSPENSION` — 6–60 missed | 2,248 | 0.059% | **EXCLUDE** |
| `LONG_SUSPENSION` — >60 missed | 145 | 0.004% | **EXCLUDE** |
| `LISTING_BOUNDARY` — inside the first 10 sessions | 477 | 0.013% | **EXCLUDE** |
| `UNRESOLVED` — an endpoint is not on the calendar | 804 | 0.021% | **EXCLUDE** |

Worked examples:

```
20MICRONS.NS  2022-04-13 -> 2022-04-18   5 calendar days, 0 missed  -> KEEP
GOYALALUM.NS  2016-09-12 -> 2022-11-04   2,244 days, 1,519 missed   -> EXCLUDE (listing)
AARTIPHARM.NS 2017-07-18 -> 2023-01-30   2,022 days, 1,369 missed   -> EXCLUDE (suspension)
```

Excluded rows are recorded in `docs/2026-09-08_V1_EXCLUSION_MANIFEST.csv` with
their reason. They are **not deleted** from `candles`.

---

## 1d. Warm-up requirement — FROZEN

Not a vague "depth filter". The number comes from the longest rolling window in
the baseline feature set:

| feature | lookback (sessions) |
|---|---:|
| `high_52w`, `low_52w`, `range_position_52w`, `dist_from_52w_high` | **252** |
| `sma_200`, `ema_200`, `close_vs_sma200`, `ema_stack_state` | 200 |
| `beta_63d`, `return_63d`, `realised_vol_63d` | 64 |
| `breakout_20d`, `breakdown_20d` | 21 |
| everything else | ≤ 22 |

```
maximum rolling lookback     = 252 sessions
required warm-up             = 251 sessions strictly before D
first valid prediction row   = the symbol's 252nd canonical session
minimum sessions per symbol  = 253  (252 for the features + 1 for the target)
```

**No warm-up feature is ever filled, imputed, defaulted or back-filled.** A
symbol emits no row at all until its complete feature vector is defined.

`ema_200` is seeded with a 200-session SMA at bar 200 and updated from there,
so at bar 252 it carries 52 updates. That is a stated convention, not a hidden
approximation. A stricter `ema_200_converged` variant (warm-up 400) is
available and costs 94 symbols and ~292,000 rows; the frozen contract uses 252.

Cost of the requirement, measured:

| warm-up | equity symbols emitting ≥1 row | approx. rows |
|---:|---:|---:|
| 0 | 2,468 | 3,804,637 |
| 200 | 2,083 | 3,362,615 |
| **252 (frozen)** | **2,024** | **3,255,790** |
| 400 | 1,930 | 2,963,802 |

---

## 2. Row key

```
PRIMARY KEY (symbol, prediction_session)
UNIQUE      (symbol, prediction_session, target_session)
```

| column | type | notes |
|---|---|---|
| `symbol` | text | `.NS` form, as stored in `candles`; **must classify as `NSE_EQUITY`** |
| `prediction_session` | date | resolved NSE session D |
| `target_session` | date | the **next session on the NSE session calendar**, strictly greater than D |
| `calendar_gap_days` | int | `(target - prediction).days` — **diagnostic only**, never a validity test |
| `dataset_version` | text | `V1_BASELINE` \| `V1_NEWS_EXTENSION` |
| `built_at` | timestamptz | provenance |
| `as_of` | date | the reader cutoff the row was built at |

Frozen constraints:

```
EXACTLY ONE row per (symbol, prediction_session)
target_session > prediction_session
idx(target_session) - idx(prediction_session) == 1   on the session calendar
```

The third constraint is the one that matters and the one a naive build gets
wrong: "strictly greater" alone admits a target 2,244 days later (§1b, §1c).

Verified on the source: duplicate `(symbol, session)` = **0**;
`target_session <= prediction_session` = **0**; future-dated sessions = **0**;
`max_feature_session > prediction_session` = **0** across the 10,000-row
validation sample.

---

## 3. Targets — raw continuous, no thresholds

| column | definition |
|---|---|
| `next_session_return` | `close[D+1] / close[D] - 1` |
| `next_session_high_return` | `high[D+1] / close[D] - 1` |
| `next_session_low_return` | `low[D+1] / close[D] - 1` |

Deliberately **not** converted to BUY/SELL/HOLD or profitable/not. That decision
follows the distribution work in §13 of the phase report.

### RAW OUTCOME ≠ EXECUTABLE OUTCOME

**RAW OUTCOME** — what price the next session reached. That is all three targets
measure, and all a daily bar can support.

**EXECUTABLE OUTCOME** — whether a strategy could have captured it: whether the
high came before the reversal, whether a stop was hit first, how long the move
took to peak. **The daily bar cannot answer any of it.** A bar that opened at the
low, ran to the high and closed at the low is indistinguishable from its exact
opposite.

The base rates make this concrete: **91.52%** of sessions trade above the prior
close at some point and **84.47%** trade below it, while the *close* is positive
only **46.69%** of the time. Intraday touch is nearly free — a "did it touch +2%"
label would score impressively and mean almost nothing.

MFE, time-to-peak and TP-before-SL are **future intraday targets**. Intraday
history begins 2026-06-18 (under three months), so they are not available for V1
and no daily proxy is substituted.

---

## 4. Row admission rules

A row is emitted only when **all** hold:

1. `classify_instrument(symbol) == NSE_EQUITY` (§1a).
2. Session D has a canonical `03:45` bar for the symbol.
3. D is the symbol's **252nd or later** canonical session (§1d).
4. `idx(target) - idx(D) == 1` on the NSE session calendar (§1b).
5. `close[D] > 0`, and `close/high/low[D+1]` are positive, non-null and finite.
6. OHLC invariants hold (verified: 0 violations in the canonical source).

Rule 3 subsumes the old `MAZDOCK.NS` carve-out: all 8 zero-OHLC pre-listing bars
sit inside that symbol's first 8 sessions and never reach the warm-up boundary.

### Measured effect of the frozen rules

| stage | rows |
|---|---:|
| candidate transitions (equity only) | 3,804,637 |
| − warm-up (< 252 sessions) | −546,823 |
| − target is not the next session | −8,797 |
| − session absent from the calendar | −52 |
| − invalid bar | −0 |
| − `extreme_return_flag` | −138 |
| **= V1 BASELINE training rows** | **3,248,827** |

```
symbols contributing rows : 2,024 of 2,468
prediction sessions       : 2017-01-09 .. 2026-09-07
measured at               : 2026-09-08 (live database — counts drift)
```

Only **138** of the 293 extreme rows survive to the flag stage; the other 155 are
already removed by warm-up or the next-session rule. The rules compose rather
than overlap.

### Extreme-return quarantine

Measured on 4,074,107 candidate rows (all instrument classes, pre-contract):

| band | rows | share |
|---|---|---|
| `abs(return) > 20%` | 1,712 | 0.042% |
| `abs(return) > 50%` | 293 | 0.0072% (135 symbols) |
| `abs(return) > 100%` | 93 | 0.0023% |

The extremes are **not** corporate-action artefacts — Phase 2M.1 §3 proved the
series is retroactively adjusted, so a corporate action leaves no jump at all.
They are breaks in the vendor's adjustment, spliced price regimes, frozen
placeholder feeds and isolated bad bars: `PRIVISCL.NS` 2020-08-20 goes
0.15 → 553.35 (+3688×) after seven sessions frozen at 0.15 on 200-600M shares;
`RAINBOW.NS` 0.75 → 450.20. All 293 exceed every NSE price band and 93.2% are
already gapped at the D+1 open, so none is economically capturable.
Per-row attribution: `docs/2026-09-08_V1_ANOMALY_MANIFEST.csv`.
They inflate the raw standard deviation from **0.029 to 1.90**.

**Quarantine — FROZEN**

| column | type | notes |
|---|---|---|
| `extreme_return_flag` | bool | `abs(next_session_return) > 0.50` — **the frozen rule** |
| `anomaly_class` | text | from the manifest: `SERIES_CHANGE`, `SYNTHETIC/BAD_BAR`, `LISTING/RELISTING`, `DATA_ERROR`, `UNPROVEN` |

Frozen guarantees:

1. The flag is **written into the table**, not applied at read time.
2. Flagged rows **stay in the table** and stay auditable — deleting them would
   make the exclusion unverifiable.
3. Flagged rows are **excluded from training by default**.
4. Prices are **never repaired or adjusted**. The bars are faithful to the
   vendor (proven by fresh re-fetch); a "repair" would be a fabrication.
5. The manifest stays linked: `docs/2026-09-08_V1_ANOMALY_MANIFEST.csv`,
   293 rows, 135 symbols, one row each with its evidence.

`corporate_action_suspect` from v1.0 is **removed**: Phase 2M.1 proved none of
these is a corporate action, so the name asserted something false.

The 0.50 threshold is a quarantine marker, not a label threshold.

---

## 5. Point-in-time contract per feature family

| family | source | cutoff rule | enforced by |
|---|---|---|---|
| daily price/volume | `candles` 03:45 via `daily_series` | `resolved_session <= D` | `as_of=prediction_session` |
| market regime | `NIFTYBEES.NS` daily | `resolved_session <= D` | same reader, same `as_of` |
| news *(extension only, not baseline)* | `news_items` | `published_at <= D close (15:30 IST)` | publication time, **never** `crawled_at` |

**Excluded from the baseline entirely** — fundamentals, sector/industry, FII/DII,
pre-open, intraday-derived labels, news. Not filled with today's values, not
imputed, not present as columns.

`market_events` is an **events calendar** (EARNINGS, FNO_EXPIRY, HOLIDAY,
RBI_MPC — all 2026), *not* a corporate-action reference. No splits, bonuses or
ex-dates are stored anywhere in this database.

All feature construction must call the reader with an explicit
`as_of=prediction_session`. Direct `candles` queries are forbidden: they bypass
session resolution and would reintroduce the duplicate-session defect fixed in
Phase 2K.

---

## 6. Survivorship bias — PRESENT

```
SURVIVORSHIP BIAS = PRESENT
```

The training universe (§1a) is drawn from the **current** Upstox/NSE instrument
master, so:

* **173 model-eligible symbols** (132 NSE equities + 41 ETFs, **204,993 legacy
  rows**, 2016-01-01 → 2026-08-27) hold legacy history but no canonical
  identity. All were still trading in 2026, so these are **symbol-mapping
  failures**, not delistings — names the backfill could not resolve to an
  Upstox instrument key. A further 1,524 non-equity series forms
  (`-BE`/`-SM`/`-BZ` and similar) are outside the V1 instrument classes.
* Their history is preserved in the legacy 00:00/18:30 rows but is **not**
  mapped into V1 — no speculative identity mapping was performed, because a
  wrong mapping splices two companies' price histories together silently.
* Symbols **delisted before the 2026 master snapshot** contributed zero rows and
  cannot be counted from inside this database — there is no historical master
  snapshot to compare against. That hole is acknowledged, not measured.

This limitation is a property of V1 and must be stated wherever V1 results are
reported. It is not corrected by anything in this schema.

---

## 6a. Market proxy — `NIFTYBEES_MARKET_PROXY`

Every market feature is named with the `mkt_` prefix and documented as deriving
from **`NIFTYBEES_MARKET_PROXY`**. It is **never** labelled "NIFTY 50 return".

| | |
|---|---|
| **source** | `NIFTYBEES.NS` — Nippon India ETF Nifty 50 BeES, canonical 03:45 daily bars, read through `daily_series` with the same `as_of` as every other feature |
| **historical range** | 2,478 canonical sessions, 2016-09-06 → 2026-09-08, single price basis |
| **why it is used** | `^NSEI` has **12** canonical sessions (2026-08-24 →). It was not extended, manufactured or backfilled. The ETF is the only instrument in this database with a full-decade Nifty-50-tracking series. |
| **tracking-error limitation** | An ETF is not its index. It carries tracking error against the NAV, its own bid/ask and liquidity, its own expense drag, and its own corporate actions. Its returns are close to but not equal to the index's. |

Any result sensitive to the difference between an index and a fund tracking it
must say so. This is a **documented substitution, not an index reconstruction**.

---

## 7. Dataset variants — V1 BASELINE vs V1-NEWS EXTENSION

News is **not** in the baseline. Its PIT history is ~5 months against ten years
of price, so folding it in would silently shorten the whole dataset or, worse,
make the news features null for 93% of rows and let the model learn "news
column populated" as a proxy for "recent".

| variant | features | prediction sessions | rows |
|---|---|---|---:|
| **`V1_BASELINE`** *(frozen)* | price + volume + `NIFTYBEES_MARKET_PROXY` | 2017-01-09 → 2026-09-07 | **3,248,827** |
| `V1_NEWS_EXTENSION` | baseline + news, same rows restricted to the news era | 2026-04-07 → 2026-09-07 | ~264,000 before contract filters |

Versioned separately and evaluated separately, so the comparison

```
V1_BASELINE            vs            V1_BASELINE + NEWS
```

is measured over the **same rows and the same period** rather than across two
different history lengths. A news feature that appears to help because it only
exists in 2026 is measuring 2026, not news.

Earlier `V1-A` / `V1-B` / `V1-C` labels are superseded: `V1-A` and `V1-B` merge
into `V1_BASELINE` (the market proxy costs no rows), and `V1-C` becomes
`V1_NEWS_EXTENSION`.

The latest *prediction* session is 2026-09-07 because its target is 2026-09-08,
the last stored session. The earliest is **2017-01-09** — the warm-up rule, not
the data: canonical bars begin 2016-09-06 (1,075 symbols) and the first symbols
reach their 252nd session in January 2017.

The market proxy costs no rows, because its 2,478 sessions span the whole
equity range. News costs ~92% of them, which is exactly why it is a separate
version rather than a baseline column.
