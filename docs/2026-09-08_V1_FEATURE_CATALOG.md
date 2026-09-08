# V1 FEATURE CATALOG

**Version:** 1.1 — SCOPE FROZEN (Phase 2M.2) · **Date:** 2026-09-08
Specification only — no features computed, no model built.

## Scope of this catalog

```
V1_BASELINE          = PRICE + VOLUME + NIFTYBEES_MARKET_PROXY      <- frozen here
V1_NEWS_EXTENSION    = V1_BASELINE + NEWS                           <- separate version
```

**News is NOT in the baseline.** It has ~5 months of PIT history against ten
years of price; folding it in would either shorten the dataset to 2026 or leave
the news columns null for 93% of rows, where "news column populated" becomes a
proxy for "recent". Kept as a separately versioned extension so the comparison
`V1_BASELINE` vs `V1_BASELINE + NEWS` runs over the same rows and the same
period.

Training observations are **NSE equities only** (2,468 symbols). ETFs and
indices supply feature values and never rows — see
`docs/2026-09-08_V1_DATASET_SCHEMA.md` §1a.

**Warm-up: a symbol emits no row until its 252nd canonical session** — the
longest lookback in this catalog. Nothing is imputed or back-filled to get a
row out earlier. Schema §1d.

Every feature is derived from **canonical session-resolved daily bars** read
through `engine/daily_series.py` with an explicit `as_of = prediction_session`.
Nothing here reads `candles` directly.

Common contract unless stated otherwise:

```
source            = candles, timeframe='1d', 03:45 canonical, via daily_series
cutoff            = D close
resolved_session <= D
earliest usable   = 2016-09-06 + (window - 1) sessions
price basis       = RETROACTIVELY ADJUSTED for corporate actions, price and
                    volume (proven against 6 known NSE actions, Phase 2M.1 §3)
```

Because the series is adjusted, a corporate action produces **no** discontinuity.
The 293 quarantined rows are therefore not corporate actions — they are breaks
in the vendor's adjustment. See `docs/2026-09-08_V1_ANOMALY_MANIFEST.csv`.

A feature is listed only if its historical availability is proven. Nothing was
added because it "sounds useful".

**"Earliest usable" below is FEATURE-level availability** — the first session on
which that one feature is defined. It is not the first row: the warm-up rule
means no row is emitted before the symbol's 252nd session, so the earliest
`V1_BASELINE` prediction session is **2017-01-09**, later than every
feature-level date in these tables.

---

## PRICE

| feature | window | PIT rule | earliest usable | status |
|---|---|---|---|---|
| `return_1d` | 2 sessions | `session <= D` | 2016-09-07 | **PASS** |
| `return_5d` | 6 | `session <= D` | 2016-09-14 | **PASS** |
| `return_21d` | 22 | `session <= D` | 2016-10-10 | **PASS** |
| `return_63d` | 64 | `session <= D` | 2016-12-07 | **PASS** |
| `sma_20`, `sma_50`, `sma_200` | 20 / 50 / 200 | `session <= D` | 2016-10-06 / 2016-11-21 / 2017-06-27 | **PASS** |
| `ema_20`, `ema_50`, `ema_200` | as above | `session <= D` | as above | **PASS** |
| `close_vs_sma20/50/200` | as above | ratio at D | as above | **PASS** |
| `ema_stack_state` | 200 | ordering of EMAs at D | 2017-06-27 | **PASS** |
| `atr_14` | 15 | true range, `session <= D` | 2016-09-28 | **PASS** |
| `realised_vol_21d` | 22 | stdev of `return_1d` | 2016-10-10 | **PASS** |
| `realised_vol_63d` | 64 | stdev of `return_1d` | 2016-12-07 | **PASS** |
| `range_pct` | 1 | `(high-low)/close` at D | 2016-09-06 | **PASS** |
| `body_pct` | 1 | `(close-open)/(high-low)` | 2016-09-06 | **PASS** |
| `upper_wick_pct` | 1 | `(high-max(open,close))/(high-low)` | 2016-09-06 | **PASS** |
| `lower_wick_pct` | 1 | `(min(open,close)-low)/(high-low)` | 2016-09-06 | **PASS** |
| `gap_pct` | 2 | `open[D]/close[D-1]-1` | 2016-09-07 | **PASS** |
| `high_20d`, `low_20d` | 20 | rolling extremes ≤ D | 2016-10-06 | **PASS** |
| `high_52w`, `low_52w` | 252 | rolling extremes ≤ D | 2017-09-11 | **PASS** |
| `range_position_20d` | 20 | `(close-low20)/(high20-low20)` | 2016-10-06 | **PASS** |
| `range_position_52w` | 252 | same over 252 | 2017-09-11 | **PASS** |
| `dist_from_52w_high` | 252 | `close/high52w - 1` | 2017-09-11 | **PASS** |
| `breakout_20d` | 21 | `close[D] > high_20d[D-1]` | 2016-10-07 | **PASS** |
| `breakdown_20d` | 21 | `close[D] < low_20d[D-1]` | 2016-10-07 | **PASS** |
| `consecutive_up_days` | ≤10 | run length ending at D | 2016-09-20 | **PASS** |

Guards: `range_pct`, `body_pct` and both wick features divide by `high-low`,
which is zero on a locked circuit bar — emit `NULL`, never 0.

---

## VOLUME / LIQUIDITY

| feature | window | PIT rule | earliest usable | status |
|---|---|---|---|---|
| `volume` | 1 | at D | 2016-09-06 | **PASS** |
| `avg_volume_20d` | 20 | `session <= D` | 2016-10-06 | **PASS** |
| `volume_ratio_20d` | 20 | `volume[D]/avg_volume_20d` | 2016-10-06 | **PASS** |
| `volume_accel` | 25 | `avg_vol_5d / avg_vol_20d` | 2016-10-06 | **PASS** |
| `turnover` | 1 | `close × volume` (proxy) | 2016-09-06 | **PASS** |
| `avg_turnover_20d` | 20 | rolling mean of `turnover` | 2016-10-06 | **PASS** |
| `zero_volume_flag` | 1 | `volume = 0` at D | 2016-09-06 | **PASS** |

**Not included:** bid/ask spread, depth, order-book imbalance. No historical
spread or depth data exists, so these cannot be claimed. `turnover` is labelled
a **proxy** (`close × volume`), not true traded value.

---

## MARKET — `NIFTYBEES_MARKET_PROXY`

Every feature below derives from **`NIFTYBEES_MARKET_PROXY`** and must be
documented under that name. **Never call these "NIFTY 50 returns".**

| | |
|---|---|
| source | `NIFTYBEES.NS`, Nippon India ETF Nifty 50 BeES, canonical 03:45 daily |
| historical range | 2,478 sessions, 2016-09-06 → 2026-09-08, single price basis |
| why | `^NSEI` has **12** canonical sessions (2026-08-24 →); it was not extended, manufactured or backfilled |
| tracking-error limitation | an ETF is not its index — tracking error vs NAV, its own liquidity and spread, expense drag, its own corporate actions |

| feature | window | PIT rule | earliest usable | status |
|---|---|---|---|---|
| `mkt_return_1d` | 2 | proxy, `session <= D` | 2016-09-07 | **PASS (proxy)** |
| `mkt_return_5d` / `_21d` | 6 / 22 | proxy | 2016-09-14 / 2016-10-10 | **PASS (proxy)** |
| `mkt_vol_21d` | 22 | stdev of proxy returns | 2016-10-10 | **PASS (proxy)** |
| `mkt_above_sma50` / `_sma200` | 50 / 200 | proxy | 2016-11-21 / 2017-06-27 | **PASS (proxy)** |
| `rel_strength_21d` | 22 | `return_21d - mkt_return_21d` | 2016-10-10 | **PASS (proxy)** |
| `beta_63d` | 64 | regression vs proxy, ≤ D | 2016-12-07 | **PASS (proxy)** |

Because warm-up is 252 sessions, no market feature is ever the binding
constraint on a row: the longest here is 200 and the symbol already needs 252.

**Not included:** `^NSEI`/`^NSEBANK` levels (12 sessions), India VIX
(`^INDIAVIX` has no 1d rows at all), market breadth (no dated historical store).

---

## NEWS — `V1_NEWS_EXTENSION` ONLY, not in the baseline

`news_items` carries both `published_at` (2026-04-07 → 2026-09-08, 42,615 items)
and `crawled_at`. **The cutoff uses `published_at`**, never `crawled_at`.

| feature | window | PIT rule | earliest usable | status |
|---|---|---|---|---|
| `news_count_1d` | 1 session | `published_at <= D 15:30 IST` | 2026-04-07 | **PASS** |
| `news_count_5d` | 5 sessions | same | 2026-04-14 | **PASS** |
| `has_news_flag` | 1 | same | 2026-04-07 | **PASS** |
| `hours_since_last_news` | unbounded back | same | 2026-04-07 | **PASS** |

Sentiment/classification features are **excluded from V1**: the scores are
produced by an LLM at crawl time with no stored as-of, so their availability at
D close cannot be proven.

These four features are **not** part of `V1_BASELINE`. They ship only in
`V1_NEWS_EXTENSION`, and any measured lift is reported against the baseline on
the news-era rows only.

---

## CORPORATE EVENTS — NOT in the baseline

`market_events` holds **2,996 rows in four types only**: `EARNINGS` (2,339,
2026-04-21 →), `FNO_EXPIRY` (265), `HOLIDAY` (216, 2026-01-26 →) and `RBI_MPC`
(176). It is an **events calendar, not a corporate-action reference** — it
records no splits, bonuses, or ex-dates. Nothing in the database does
(established in Phase 2M.1 §2).

| feature | source | PIT rule | earliest | status |
|---|---|---|---|---|
| `days_to_next_earnings` | `market_events.event_date` where type = EARNINGS | event known by D | **2026-04-21** | **PASS** |
| `earnings_within_5d_flag` | same | same | 2026-04-21 | **PASS** |
| `days_to_next_rbi_mpc` | same, type = RBI_MPC | same | 2026-04-06 | **PASS** |

Earnings coverage begins 2026-04-21, so these are **news-era features only** —
narrower even than news itself, and therefore **outside `V1_BASELINE`**. A `days_to_next_event` built across all four types
would mix a company-specific signal with an index-wide one and is not listed.

`causal_events` is **excluded**: it stores only `created_at`, so publication and
ingestion cannot be separated — **UNPROVEN**, and UNPROVEN is not treated as safe.

---

## EXCLUDED — and why

Excluded from the **baseline**, each for a measured reason:

| family | reason |
|---|---|
| **News** | ~5 months of PIT history vs ten years of price — moved to `V1_NEWS_EXTENSION`, not dropped |
| **FII/DII** | not in the baseline: flow data is index-wide, not per symbol, and its historical PIT availability has not been established |
| Fundamentals | `fundamental_data` = 1 current row per symbol (1,217/1,217), no period-end, no publication date, no snapshots. Using it applies 2026 values to 2016 rows. |
| Sector / industry | No sector column or table anywhere; resolved at runtime, never persisted with a date. |
| Pre-open (IEP, imbalance) | No table exists; `preOpenMarket` is read live and discarded. |
| Intraday features | 1m/5m/15m start 2026-06-18 — ~3 months. |
| MFE-before-reversal, time-to-peak | Require intraday paths; same 3-month limit. |
| India VIX | `^INDIAVIX` has zero 1d rows. |
| Bid/ask, depth | Never stored. |
| News sentiment scores | No provable as-of. |

None of these is imputed, defaulted, or filled with a current value.


---

## Frozen contract — the values this catalog is built against

Full definitions live in `docs/2026-09-08_V1_DATASET_SCHEMA.md`; the sections
below name the frozen value and where to read the reasoning.

### Training universe
**2,468 NSE equity symbols** (`InstrumentClass.NSE_EQUITY`). Not expanded
without a version bump. → schema §1a

### Market context vs training observations
`NIFTYBEES.NS`, `BANKBEES.NS`, `^NSEI`, `^NSEBANK` and 230 further ETF/INAV
symbols supply **feature values only** and never become rows. InvITs and REITs
carry `-IV` / `-RR` series suffixes, never entered the canonical data, and are
excluded on their merits — their price responds to distributions and yield, not
to the quantity this model predicts. → schema §1a

### Next-session definition
```
valid(D, T)  ⟺  idx(T) - idx(D) == 1     on the 2,478-session NSE calendar
calendar_gap_days                         diagnostic column, never the test
```
A 4-calendar-day rule was measured and rejected: it misses 9,874 genuinely
defective rows and wrongly drops 3,922 correct ones. → schema §1b

### Long-gap handling
0 missed sessions → KEEP however many calendar days it spans (3,922 rows).
Any missed session → EXCLUDE, classified as short inactivity, suspension, long
suspension, listing boundary or unresolved. → schema §1c and
`docs/2026-09-08_V1_EXCLUSION_MANIFEST.csv`

### Extreme-return quarantine
`extreme_return_flag = abs(next_session_return) > 0.50`, written into the table,
rows retained and auditable, excluded from training by default, prices never
repaired. 293 rows / 135 symbols. → schema §4 and
`docs/2026-09-08_V1_ANOMALY_MANIFEST.csv`

### Warm-up requirement
```
maximum rolling lookback   = 252 sessions   (52-week high/low)
first valid row            = the symbol's 252nd canonical session
minimum sessions/symbol    = 253
```
No warm-up feature is filled, imputed or back-filled. → schema §1d

### V1 baseline vs V1-news extension
`V1_BASELINE` = PRICE + VOLUME + `NIFTYBEES_MARKET_PROXY`, **3,248,827 rows**,
2,024 symbols, 2017-01-09 → 2026-09-07.
`V1_NEWS_EXTENSION` = baseline + news, news-era rows only. → schema §7

### Survivorship bias
```
SURVIVORSHIP BIAS = PRESENT
```
Universe drawn from the current instrument master; names delisted before the
2026 snapshot contributed zero rows and **cannot be counted from inside this
database**; 173 further symbols excluded for unresolved identity. → schema §6
