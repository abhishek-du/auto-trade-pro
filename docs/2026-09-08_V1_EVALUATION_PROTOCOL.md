# V1 EVALUATION PROTOCOL

**Phase 2O** · 2026-09-08 · Design and implementation only.
**No model trained. No predictions generated. No trading logic changed.**

This document fixes how the frozen V1 baseline will be split, what it will be
compared against, and how it will be scored — *before* anything is fitted, so
that the answer cannot be chosen after seeing it.

Companions: `2026-09-08_V1_DATASET_SCHEMA.md` (v1.1, frozen),
`2026-09-08_V1_FEATURE_CATALOG.md` (v1.1, frozen),
`2026-09-08_PHASE_2M1_DATASET_INTEGRITY_GATE.md`.
Implementation: `autotrade-backend/scripts/v1_protocol.py`,
tests in `tests/test_step2o_protocol.py`.

---

## 1. Muhurat calendar reconciliation

Three sparse sessions were raised as a possible calendar defect. All three were
checked against the frozen 2,478-session calendar and against the
`NIFTYBEES_MARKET_PROXY` series, read-only.

**The decisive check is set equality, not count equality.** Two independent
derivations — the equity-coverage threshold, and one ETF's bar presence —
produce the *same set*:

```
frozen equity calendar sessions : 2478
NIFTYBEES canonical sessions    : 2478
SET EQUALITY (calendar == proxy): True
  in calendar, not in proxy     : 0
  in proxy, not in calendar     : 0
```

| date | day | in calendar | in proxy | declared special | equity symbols | verdict |
|---|---|:--:|:--:|:--:|---|---|
| 2016-10-30 | Sun | No | No | **Yes** | 21 of 1,114 (1.89%) | **B — absent from the session index** |
| 2017-10-19 | Thu | No | No | No | 18 of 1,210 (1.49%) | **B — absent from the session index** |
| 2018-11-07 | Wed | No | No | No | 29 of 1,267 (2.29%) | **B — absent from the session index** |

All three fall far below the 10% coverage threshold. **No verdict-C date
exists**: nothing is "present but correctly sparse".

### Does this contradict the frozen contract?

**No.** Schema §1b defines the calendar as data-derived on the coverage rule and
already names these three as known coverage holes. The implementation matches
the contract exactly, so there is nothing to stop for.

`NSE_SPECIAL_SESSIONS` — which does contain 2016-10-30 — serves a different
purpose: it is the `extra_open` calendar the *reader* uses to resolve an 18:30
bar to its session. Membership there is not a claim about dataset coverage.

### What it costs, and why the handling is conservative

These are almost certainly real NSE Muhurat sessions that the Upstox source
barely covers, not sessions that did not happen. Forcing them into the calendar
would be worse, not better:

| date | symbols that would gain a transition | symbols that would be reclassified as having MISSED a session |
|---|---:|---:|
| 2016-10-30 | 21 | **1,093** |
| 2017-10-19 | 18 | **1,192** |
| 2018-11-07 | 29 | **1,238** |

Excluding them is right for the ~98% with no bar. For the ~20 per date that do
have one, both adjacent transitions are dropped as `SESSION_OFF_CALENDAR` — so
those symbols **lose two rows rather than gaining a wrong one**. The failure
mode is conservative in the direction that matters.

---

## 2. Survivorship bias — quantified, not solved

```
SURVIVORSHIP BIAS = PRESENT
```

Not addressed in this phase, and no speculative historical identity was
introduced.

| | |
|---|---|
| training universe | **2,468 NSE equities**, drawn from the *current* instrument master |
| delisted / bankrupt names before the 2026 snapshot | **absent, and unmeasurable from inside this database** — no historical master snapshot exists to diff against |
| unresolved identities | **173** symbols (132 equities + 41 ETFs, 204,993 legacy rows, 2016-01-01 → 2026-08-27), all still trading in 2026 — mapping failures, not delistings |
| non-equity series forms | 1,524 (`-BE` / `-SM` / `-BZ`, and the `-IV` / `-RR` InvIT/REIT forms) |

### Effect on historical performance estimates

The cross-section is not a fixed 2,468 names — it grows from **1,210 symbols in
2017 to 2,465 in 2026**, because a symbol only appears once it has listed *and*
survived to be in the 2026 master. Three consequences, all of which inflate
apparent performance:

1. **Every symbol in the 2017 cross-section is known to have survived to 2026.**
   A model selecting from that cross-section is choosing among winners, and its
   backtested returns are biased upward by an amount this dataset cannot measure.
2. **The bias is worst exactly where the history is longest.** The 2017–2019
   period has the smallest and most heavily filtered cross-section, so the
   earliest folds — the ones with the most out-of-sample sessions after them —
   are the most contaminated.
3. **Downside metrics are understated more than upside ones.** The names missing
   from the universe are disproportionately those that fell to zero or were
   suspended, so `worst_decile` and `worst_session_avg` are optimistic in a way
   that mean return is not.

**Required on every V1 result:** *universe drawn from the current
Upstox/NSE master; names delisted before the 2026 snapshot are absent and their
number is unknown; 173 further symbols excluded for unresolved identity.*

**Fix (a later phase, not this one):** start snapshotting the instrument master
weekly. It costs nothing and is the only path from *acknowledging* survivorship
to *measuring* it.

---

## 3. Temporal split — recommended design

### Requirements met

No random split, no shuffle, no sampling across time. Boundaries are expressed
on the **NSE session index**, never on row counts and never on raw calendar
dates — the same authority the dataset itself uses.

### Available history

| year | sessions | peak cross-section |
|---|---:|---:|
| 2017 | 247 | 1,210 |
| 2018 | 245 | 1,267 |
| 2019 | 245 | 1,304 |
| 2020 | 252 | 1,369 |
| 2021 | 248 | 1,518 |
| 2022 | 248 | 1,648 |
| 2023 | 246 | 1,755 |
| 2024 | 249 | 1,920 |
| 2025 | 249 | 2,115 |
| 2026 | 170 | 2,465 |

**2,399 prediction sessions from 2017-01-09.**

### Recommendation: expanding-window walk-forward, plus one untouched test block

**Expanding window, not fixed split, and not sliding.**

- A **single fixed split** would hand the entire out-of-sample verdict to one
  regime. With ~2,400 sessions there is enough history to test across several,
  and the regimes here are genuinely different: the 2020 crash and recovery, the
  2021–22 small-cap bull, the 2023–24 broadening, the 2026 tape. A model that
  works only in one of those is a model that will fail, and a fixed split can
  hide that.
- A **sliding window** would discard the only COVID crash the dataset contains.
  For a ten-year daily equity dataset that is the most informative stress in it.
- An **expanding window** keeps every past session and still evaluates forward
  only.

```
fold 1 : train 2017-01-09 .. 2021-12-30   validate 2022
fold 2 : train 2017-01-09 .. 2022-12-29   validate 2023
fold 3 : train 2017-01-09 .. 2023-12-28   validate 2024
fold 4 : train 2017-01-09 .. 2024-12-30   validate 2025
                              (each train_end is AFTER the 1-session purge)

HELD-OUT TEST : 2026-01-01 .. 2026-09-07   — not touched until the end
```

Four validation folds of ~248 sessions each, ~170 test sessions. The 2026 block
is opened **once**, after the model and every hyperparameter are fixed. Any
result that requires re-opening it is a new experiment and must be reported as
such.

`scripts/v1_protocol.py::expanding_folds` builds these; `split_violations`
checks them.

---

## 4. Purge and embargo

The mechanism was established before any rule was written.

### The exact leakage mechanism

A row at prediction session **D** carries features from sessions ≤ D and a
target realised at **D+1**.

Take a boundary with training ending at `T_end` and validation starting at
`V_start = T_end + 1`:

- The last training row (`prediction_session = T_end`) has its outcome at
  `T_end + 1` — **which is the first validation session**.
- Standing at the close of `T_end`, the moment the model would actually be
  fitted, that outcome **has not happened yet**.

So the training set contains an observation that could not have been known at
training time. That is the leak, and it is about **deployability**, not about
label overlap.

### What is *not* the mechanism

- **Target windows do not overlap.** Horizon is exactly one session: training
  targets end at `T_end+1`, validation targets start at `V_start+1 = T_end+2`.
  There is no shared label window, which is what a classical embargo exists to
  remove.
- **Shared lookback is not leakage.** A validation row's 252-session window
  reaches back into the training period. Using past information is the whole
  point; it tells the model nothing about its own future target.

### Conclusion

```
PURGE   = 1 session   (mandatory)
EMBARGO = 0 sessions  (default; not required by any mechanism found)
```

Drop exactly one prediction session from the end of every training block, so
that `max(train.target_session) < min(validation.prediction_session)`.

**The residual issue is real but is not leakage.** Adjacent rows for one symbol
share 251 of their 252 lookback sessions and are near-duplicates. Pooling rows
would treat them as independent observations and understate the error bars.
That is handled in the metrics, not the split: **aggregate per session, not per
row** (§6). An embargo would not fix it — it would only delete data.

An embargo parameter exists and is tested, for the case where a future V2 uses a
multi-session horizon. It is 0 here because nothing found justifies more.

---

## 5. Cross-sectional vs time-series — recommendation

**Recommendation: B — cross-sectional ranking per prediction session**, with
per-stock regression retained only as a diagnostic.

The stated goal is *selecting stocks likely to outperform on the next session*.
That is a ranking problem, and the evidence says absolute-return regression is
the wrong shape for it:

1. **The signal-to-noise ratio defeats level prediction.** Daily return std is
   ~0.029 against a mean of +0.0008 — the mean is 3% of one standard deviation.
   A regression will spend its capacity predicting a level it cannot resolve,
   and will be scored mostly on noise.
2. **Ranking only needs the ordering.** Selecting the top-k requires the model
   to get relative order right, not magnitude. That is a strictly easier problem
   and it is the one the strategy actually poses.
3. **A per-session cross-section removes the market factor for free.** Every
   symbol on session D shares the same market move, so ranking within a session
   is automatically market-neutral — while a pooled regression must learn to
   subtract it, and will be dominated by market-wide days.
4. **The cross-section is deep enough**: 1,210 symbols per session in 2017 and
   2,465 by 2026.

Target definitions are unchanged. Ranking is scored on
`next_session_return`; the raw targets stay exactly as frozen.

Per-stock regression is kept as a **diagnostic only** — MAE/RMSE against the
constant baselines answer "is there any level information at all", which is
worth knowing even though it is not the deployment task.

---

## 6. Baselines — definitions only

No baseline was run. These are the bar a model must clear to be interesting.

| id | baseline | definition | why it is here |
|---|---|---|---|
| **B0** | zero | predict `0.0` for every row | the honest null; the median next-session return is **negative**, so "always zero" already beats "always up" |
| **B1** | historical mean | training-period mean return | constant → zero cross-sectional information; any ranking metric on it is exactly chance |
| **B2** | historical median | training-period median return | differs from B1 in sign — the cross-section is right-skewed (mean +0.0008, median −0.0005) |
| **B3** | momentum 21d | `return_21d` | the first baseline carrying cross-sectional information — and the one the intraday-reversal finding predicts should **fail** |
| **B4** | volume-gated momentum | `return_21d × 1[volume_ratio_20d ≥ 1]` | built only from frozen features; no new family |
| **B5** | majority class | always predict the majority direction | with 46.7% up, that class is **DOWN**; direction accuracy must be read against ~50.9%, never 50% |
| **B6** | momentum ranking | rank each session by `return_21d` | the cross-sectional counterpart of B3, and the direct competitor to any ranking model |

**A model that does not beat B3 and B6 on the ranking metrics is not a
result.** B0–B2 exist to make the regression numbers interpretable, not to be
competitive.

---

## 7. Metrics

Grouped, because they answer different questions and are not interchangeable.

### Return prediction (diagnostic)
`MAE` · `RMSE` · `Pearson correlation`

Pearson is reported but read with care: raw next-session returns have a fat
tail, so a handful of rows dominate it. Where the question is ordering, use
Spearman.

### Direction
`accuracy` · **`balanced_accuracy`** · `precision_up` · `recall_up`

Balanced accuracy is **not optional**: at 46.7/50.9 a constant DOWN prediction
scores 50.9% plain accuracy and exactly 50.0% balanced. Any headline accuracy
without its balanced counterpart is uninterpretable.

### Ranking — the primary group
`spearman_rank_ic` (per session, then averaged) · `topk_avg_return` ·
`topk_hit_rate` · `topk_worst` · `coverage`

Computed **per prediction session and then aggregated across sessions** — never
pooled over rows, because adjacent rows are near-duplicates (§4). `coverage`
is reported alongside every top-k number: a rule that only scores when it
happens to fire is not comparable to one that always does.

### Trading-relevant
`mean_of_session_avg_return` · `median_of_session_avg_return` ·
`worst_decile_of_session_avg` · `worst_session_avg`

### Deliberately excluded

| metric | why |
|---|---|
| `max_drawdown` | a portfolio path statistic; V1 has no position sizing, no costs, no capital constraint and no holding period beyond one session |
| `sharpe_ratio` | same — needs a return stream from a sized, costed strategy |
| `profit_factor` | implies executed trades; V1 targets are **RAW outcomes** |

**No profitability claim may be made from these targets.** The base rates are
the reason: **91.5%** of sessions trade above the prior close at some point and
**84.5%** below it, while the close is positive only **46.7%** of the time.
Touch is nearly free. A positive `topk_avg_return` is a statement about a
raw outcome and about nothing else until a portfolio simulation exists.

---

## 8. Feature freeze

The V1 feature list is **frozen at 47 features** and is not extended in this
phase. It lives in two places that cannot drift apart:

- `docs/2026-09-08_V1_FEATURE_CATALOG.md` — per feature: name, formula,
  lookback, source, as-of semantics, family and status
- `autotrade-backend/scripts/v1_contract.py` — the same list as executable
  constants, imported by the builder, the verifier and the protocol

```
PRICE  32   returns, moving averages, trend, ATR, volatility, candle
            structure, range position, breakout, high/low structure
VOLUME  7   volume, rolling average, ratio, acceleration, turnover proxy
MARKET  8   NIFTYBEES_MARKET_PROXY returns, volatility, trend state,
            relative strength, beta
```

Every feature: source `candles` 1d at canonical 03:45, read through
`engine.daily_series` with an explicit `as_of`, cutoff `D close`, resolved
session ≤ D. `turnover` is labelled a **proxy** (`close × volume`), not traded
value. Market features are labelled **`NIFTYBEES_MARKET_PROXY`**, never
"NIFTY 50 return".

**No new feature family may be added to V1.** Fundamentals, sector, FII/DII,
pre-open, intraday-derived features, news and earnings-calendar features remain
excluded, each for a measured reason recorded in the catalog. News stays a
separately versioned `V1_NEWS_EXTENSION` so that
`V1_BASELINE` vs `V1_BASELINE + NEWS` is measured over the same rows and the
same period.

---

## 9. Model card — limitations to carry forward

Any model trained on V1 inherits all of these, and they belong in its card:

1. **Survivorship bias is present and unquantified** (§2).
2. **The market factor is an ETF proxy**, not the index — `NIFTYBEES.NS` carries
   tracking error, its own liquidity and its own corporate actions.
3. **The price series is vendor-adjusted**, and the adjustment is demonstrably
   incomplete for 293 rows across 135 symbols. Those rows are flagged and
   excluded by default; 115 of them have no proven root cause.
4. **No fundamentals, sector, FII/DII, pre-open, intraday or news information.**
   A next-session equity model without valuation or cross-sectional context is a
   pure price-and-volume model and must be evaluated as one.
5. **Three Muhurat sessions are missing** from the source data (§1).
6. **Depth is uneven** — the cross-section doubles from 2017 to 2026, so early
   folds are not comparable to late ones.
7. **Targets are RAW outcomes, not executable outcomes.** No claim about
   capturable profit follows from them.
