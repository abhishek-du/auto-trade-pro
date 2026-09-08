# PHASE 2M — V1 PREDICTION DATASET FOUNDATION

**Date:** 2026-09-08 · **Verdict: YELLOW**

Dataset foundation only. No model was trained, no prediction logic deployed, no
strategy changed. Nothing was deleted, migrated, backfilled, or fabricated. The
18:30 and 00:00 legacy series were not touched.

**YELLOW, not GREEN.** The database is clean — every structural, PIT, numerical
and leakage gate passes. But V1 carries four limitations that a model built on
it will inherit: it has **no fundamentals, no sector, no pre-open**; its market
regime is an **ETF proxy**, not the index; its **news history is five months**
against ten years of price; and its universe is drawn from the **current**
instrument master, so pre-2026 delistings are structurally invisible. A clean
database is not a complete dataset.

**Companion documents**
- `docs/2026-09-08_V1_DATASET_SCHEMA.md` — row contract, keys, targets
- `docs/2026-09-08_V1_FEATURE_CATALOG.md` — every feature, with PIT rule and earliest usable date

---

## 1. V1 scope

V1 is a **daily next-session** dataset. One row per (symbol, prediction
session). Features are everything knowable at **session D close (15:30 IST)**;
targets are session **D+1**, where D+1 is *the next resolved NSE session for
that symbol*, never `D + 1 calendar day`.

Every feature reads canonical 03:45 daily bars through
`engine/daily_series.py` with an explicit `as_of = prediction_session`. No
feature builder queries `candles` directly.

| tier | contents | rows | first prediction session |
|---|---|---:|---|
| **V1-A** | daily price + volume | **4,074,107** | 2016-01-01 (4 symbols) / **2016-09-06** (population) |
| **V1-B** | V1-A + market regime (NIFTYBEES proxy) | **4,074,107** | same |
| **V1-C** | V1-B + news | **263,605** | **2026-04-07** |

V1-B does not shrink relative to V1-A: the proxy covers 2,478 sessions,
2016-09-06 → 2026-09-08, the same span as the equity population.

---

## 2. Exactly what V1 contains

- **Price structure** — OHLC, 1/5/21/63-session returns, SMA and EMA 20/50/200
  and their ratios, EMA stack state, ATR-14, realised volatility 21/63,
  range %, body %, upper/lower wick %, gap %, 20-session and 52-week high/low,
  range position, breakout/breakdown state, consecutive-up run.
- **Volume / liquidity** — volume, 20-session average, volume ratio, volume
  acceleration, turnover **proxy** (`close × volume`), 20-session average
  turnover, zero-volume flag.
- **Market regime (proxy)** — market 1/5/21-session return, 21-session market
  volatility, above-SMA50/SMA200 state, 21-session relative strength,
  63-session beta.
- **News (V1-C only)** — `news_count_1d`, `news_count_5d`, `has_news_flag`,
  `hours_since_last_news`, all cut at `published_at <= D 15:30 IST`.
- **Corporate events (optional)** — `days_to_next_event`,
  `event_within_5d_flag`, from `market_events.event_date`, 2026-01-26 onward.
- **Targets** — three raw continuous outcomes, no thresholds:
  `next_session_return`, `next_session_high_return`, `next_session_low_return`.

---

## 3. Exactly what V1 excludes

| excluded | measured reason |
|---|---|
| **Fundamentals** | `fundamental_data` holds **1 current row per symbol** (1,217 of 1,217), with no period-end and no publication date. Joining it applies 2026 values to 2016 rows. |
| **Sector / industry** | No sector column and no sector table anywhere in the schema; sector is resolved at runtime and never persisted with a date. |
| **Pre-open (IEP, imbalance)** | No table exists. `preOpenMarket` is read live and discarded. |
| **Intraday features** | 1m/5m/15m candles begin **2026-06-18** — under 3 months. |
| **MFE-before-reversal, time-to-peak** | Require intraday paths; same 3-month limit. |
| **India VIX** | `^INDIAVIX` has **zero** 1d rows. |
| **Bid/ask, depth, order-book imbalance** | Never stored at any horizon. |
| **News sentiment / classification scores** | Produced by an LLM at crawl time with no stored as-of; availability at D close is unprovable. UNPROVEN is treated as unusable, not as safe. |
| **`causal_events`** | Stores only `created_at`; publication and ingestion cannot be separated. |
| **`^NSEI` / `^NSEBANK` index levels** | 12 canonical sessions (from 2026-08-24). |

**No excluded family is imputed, defaulted, or filled with a current value.**

---

## 4. Prediction timestamp and targets

```
prediction_cutoff = SESSION D CLOSE   (15:30 IST)
information_available_at <= D close   for every feature in row D
target_session            = next resolved NSE session for that symbol
```

```
next_session_return      = close[D+1] / close[D] - 1
next_session_high_return = high [D+1] / close[D] - 1
next_session_low_return  = low  [D+1] / close[D] - 1
```

No BUY/SELL/HOLD mapping, no profitability label, no threshold. Section 13
below is descriptive so that the label design that follows is informed rather
than assumed.

---

## 5. Dataset key and structural gates

Key: `(symbol, prediction_session)` → `target_session`.

| gate | result |
|---|---|
| duplicate `(symbol, prediction_session)` | **0** — PASS |
| `target_session <= prediction_session` | **0** — PASS |
| `target_session` is the next *resolved* session, not D+1 calendar | PASS |
| legacy/canonical duplicate rows creating two observations for one session | **0** — the reader collapses to one bar per session on one basis |

The last gate is the reason Phase 2D.2 → 2K existed: before the canonical
migration, 3,611 of 4,378 daily symbols carried two conventions, and every
rolling window spanned half the calendar it claimed.

---

## 6. Point-in-time contract

Enforced at the reader, not at each call site:

| family | PIT rule | enforced by |
|---|---|---|
| daily price/volume | `resolved_session <= D` | `session_bars(..., as_of=D)` |
| market proxy | `resolved_session <= D` | same reader, same `as_of` |
| news | `published_at <= D 15:30 IST` | publication time, never `crawled_at` |
| corporate events | `event_date` known at D | `market_events` |
| fundamentals, sector, pre-open | **excluded** | — |

`as_of` is applied by `_apply_as_of()` inside `engine/daily_series.py`, so a
caller cannot forget it by taking a default: the default is the *live* state and
is only correct for live inference. Historical construction must pass the
cutoff explicitly, and §7 makes the training path do so.

---

## 7. `ml_predictor` — training cutoff

Phase 2L found `train_all_models` reading daily bars with no `as_of`. Fixed:

```python
async def train_all_models(session, *, as_of: date | None = None) -> None:
    train_as_of = as_of
    if train_as_of is None:
        today = date.today()
        train_as_of = (today - timedelta(days=1)
                       if current_open_session() is not None else today)
    ...
    bars = await session_bars(symbol, cutoff.date(), train_as_of, session,
                              as_of=train_as_of)
```

The default is the last **completed** session — today's forming bar is not an
observation. Live inference is untouched: `predict_direction()` is handed a
DataFrame by its caller, owns no reader, and takes no `as_of`.

**Tests — `tests/test_step2m_training_pit.py`, 15 passing:**

- reader stops at `as_of`; D+1 and D+2 are never visible (§12 rules 1 and 2)
- a weekend cutoff resolves **backwards** to Friday, never forward (§12 rule 3)
- the cutoff's own session is included
- rebuilding the same row at a later cutoff extends the tail and leaves the
  prefix byte-identical
- without `as_of` the reader returns the whole series — the Phase 2L failure,
  kept as a regression witness
- `session_bars` is called with an explicit `as_of` (asserted against the
  **parsed AST**, not a substring — the docstring above it quotes the query it
  replaced)
- no direct `candles` SQL in the training path
- default cutoff steps back when a session is open, and is today when closed
- `predict_direction` calls no reader and takes no `as_of`

Full suite after the change: **2,730 passed, 27 failed, 5 errors** — identical
to the pre-change baseline. No model was trained.

---

## 8. Replay revalidation manifest

Phase 2L fixed replay entry look-ahead (13.9% of RELIANCE calendar cutoffs
changed when bounded; largest entry-price difference −3.41%).

**Manifest: empty — there are no stored pre-fix results to classify.** Replay
and backtest results in this repository are computed on demand and returned;
no table persists them, so no `PRE_PIT_FIX` / `POST_PIT_FIX` / `UNKNOWN`
partition can be drawn. Nothing was deleted, and nothing was rerun.

The practical consequence: **every replay number quoted in any document written
before Phase 2L is unreproducible and must be regenerated before it is
trusted**, because the artefact that produced it does not exist.

---

## 9. Historical coverage

| boundary | value |
|---|---|
| earliest prediction session (population) | **2016-09-06** (1,075 symbols) |
| earliest prediction session (any symbol) | 2016-01-01 — **4 symbols only**, from an earlier ingest |
| latest prediction session | **2026-09-07** (its target is 2026-09-08, the last stored session) |
| latest data session | 2026-09-08 |
| V1-A / V1-B rows | **4,074,107** |
| V1-C rows (news floor 2026-04-07) | **263,605** — 6.5% of V1-A |
| symbols with at least one usable row | **2,701** |

**Market regime is the substitution to watch.** `^NSEI` and `^NSEBANK` have
**12 canonical sessions** (2026-08-24 onward). They were not extended,
manufactured, or backfilled. `NIFTYBEES.NS` — a Nifty-50 ETF — has **2,478
canonical sessions, 2016-09-06 → 2026-09-08, single price basis**, and is used
as the market proxy through the same reader with the same `as_of`.
`BANKBEES.NS` gives the same span for a banking proxy.

An ETF is not its index: it carries tracking error, its own liquidity, and its
own corporate actions. Any V1-B result sensitive to that distinction must say
so. This is a documented substitution, not an index reconstruction.

---

## 10. Feature catalog

`docs/2026-09-08_V1_FEATURE_CATALOG.md` — PRICE / VOLUME / MARKET / NEWS /
CORPORATE EVENTS, each feature with source, cutoff, window, PIT rule, earliest
usable date and status. Nothing was added because it sounded useful; every
listed feature's historical availability is proven, and everything else sits in
the EXCLUDED table with its measured reason.

---

## 11. Symbol universe and survivorship

**Canonical universe: 2,702 symbols** — 2,468 NSE equities, 232 ETF/InvIT,
2 indices. 2,700 of them are present in the current NSE instrument master; the
two exceptions are `^NSEI` and `^NSEBANK`, which are index symbols, not
instruments.

Depth is uneven and matters for any windowed feature:

| canonical sessions | symbols |
|---|---:|
| ≥ 2,400 (full 10y) | **1,091** |
| 1,250 – 2,399 (5–10y) | 474 |
| 500 – 1,249 (2–5y) | 455 |
| 220 – 499 (1–2y) | 254 |
| < 220 (<1y) | **428** |

A 200-session moving average is undefined for the 428 short-history symbols and
thin for the 254 above them. Roughly **40% of the universe carries the full
decade**; the rest must either be filtered per feature window or accept nulls.

### Survivorship — three distinct limitations, none of them fixed here

1. **The universe is the *current* master.** The backfill enumerated the
   instrument master as it stands in September 2026. A company delisted in,
   say, 2019 has no instrument key today and therefore contributed **zero
   rows**. The dataset cannot see it, and **the size of that hole is not
   measurable from inside this database** — there is no historical master
   snapshot to compare against. This is the classic survivorship bias and V1
   has it, undiminished.

2. **173 symbols with legacy history have no canonical replacement** — 132 NSE
   equities and 41 ETFs, **204,993 legacy rows**, spanning 2016-01-01 →
   2026-08-27. Every one of them was still trading in 2026, so these are not
   delistings: they are **symbol-mapping failures**, names the backfill could
   not resolve to an Upstox instrument key (renames, series variants, SME
   listings). They are excluded from V1 rather than guessed at — §11 of the
   brief forbids speculative identity mapping, and a wrong mapping silently
   splices two companies' price histories together.

3. **7,511 master EQ instruments have no canonical daily history.** The master's
   "NSE EQ" bucket (10,211 rows) is broader than the equity universe the
   backfill derived (2,468) — it includes SME, trusts, and non-equity forms.
   This is a scope boundary, not a gap.

**Label to carry forward with any V1 result:** *universe drawn from the current
Upstox/NSE master; delisted-before-2026 names absent; 173 further symbols
excluded for unresolved identity.*

---

## 12. Data-quality gates

### Structural
| check | result |
|---|---|
| unique row key | **PASS** — 0 duplicates |
| target session > prediction session | **PASS** — 0 violations |
| chronological ordering | **PASS** |
| legacy duplicates creating two observations | **PASS** — 0 |

### Point-in-time
| check | result |
|---|---|
| feature session > prediction session | **PASS** — 0 |
| news published after D close | **PASS** — cutoff is `published_at`, not `crawled_at` |
| current fundamentals used | **PASS** — family excluded |
| retroactive sector | **PASS** — family excluded |
| event availability > D | **PASS** — `market_events.event_date` only |

### Numerical
| check | result |
|---|---|
| NaN / inf | **PASS** — 0 |
| invalid OHLC (high < low, close outside range) | **PASS** — 0 |
| negative volume | **PASS** — 0 after the int32-overflow repair in Phase 2I |
| future-dated rows | **PASS** — 0 |
| zero / negative close | **8 rows, 1 symbol** — `MAZDOCK.NS`, 2017-12-04 → 2018-07-02, OHLC all zero with volume: **pre-listing filler**. Excluded by the `close > 0` predicate; not deleted from `candles`. |
| divide-by-zero artifacts | guarded — `range_pct`, `body_pct` and both wick features emit `NULL` on a `high == low` locked-circuit bar, never 0 |

### Leakage
| rule | result |
|---|---|
| 1. row for D cannot see D+1 | **PASS** — tested |
| 2. row for D cannot see D+2 | **PASS** — tested |
| 3. weekend D resolves to the previous valid session | **PASS** — tested |
| 4. special session resolves correctly | **PASS** — see below |
| 5. duplicate legacy/canonical rows cannot create two observations | **PASS** |

**Two apparent failures were investigated and both were mine, not the data's.**

- **10,606 "D+1 lands on a weekend" transitions.** Every one is a
  Friday → declared-special-Saturday step: 2020-02-01, 2020-11-14, 2024-01-20,
  2024-03-02, 2024-05-18, 2025-02-01. These are real NSE sessions (Budget
  Saturdays, Muhurat, DR sessions), already in `NSE_SPECIAL_SESSIONS`. My gate
  asserted a weekend target was invalid; the gate was wrong and the data was
  right. Corrected — and this is exactly §12 rule 4 passing.
- **8 zero/negative closes** — the `MAZDOCK.NS` pre-listing rows above.

### Extreme returns — quarantine, not deletion

| threshold | rows | share | symbols |
|---|---:|---:|---:|
| \|r\| > 20% | 1,712 | 0.042% | — |
| \|r\| > 50% | **293** | **0.0072%** | **135** |
| \|r\| > 100% | 93 | 0.0023% | — |

Worst cases are unhandled corporate actions in the source series, not market
moves: `PRIVISCL.NS` 2020-08-20 closes 0.15 → 553.35 (**+3688×**),
`RAINBOW.NS` 0.75 → 450.20. These rows are **flagged, not deleted** — deleting
them would be a silent edit to shared history, and the flag is a feature of the
dataset build, not of `candles`.

---

## 13. Target distributions — descriptive only

n = **4,074,107**. No thresholds chosen.

### `next_session_return`

| statistic | all rows | \|r\| ≤ 50% (n = 4,073,814) |
|---|---:|---:|
| mean | +0.00254 | **+0.00082** |
| median | −0.00051 | −0.00051 |
| std | **1.89943** | **0.02937** |
| p1 | −0.0662 | −0.0662 |
| p25 | −0.01367 | — |
| p75 | +0.01225 | — |
| p99 | +0.0966 | +0.0965 |
| min | −0.9961 | — |
| max | **+3688.0** | — |
| positive | 46.69% | 46.69% |
| negative | 50.91% | — |

**The single most important number in this table is the std moving from 1.899
to 0.029 when 0.0072% of rows are removed.** 293 corporate-action artefacts
dominate the second moment of a four-million-row dataset. Any model trained on
the unquarantined target is fitting seven splits, not the market.

The median is **negative** (−0.051%) while the mean is positive and only 46.7%
of sessions are up: the daily cross-section is right-skewed — most sessions
drift slightly down, a minority carry the average. A naive "predict up" label
would be wrong more often than not.

### `next_session_high_return` / `next_session_low_return`

| | mean | median | std | rate |
|---|---:|---:|---:|---|
| high vs prior close | +0.02598 | +0.01776 | 1.9134 | **91.52% positive** |
| low vs prior close | −0.01660 | −0.01391 | 1.7244 | **84.47% negative** |

Nine sessions in ten trade above the prior close at some point, and eight in
ten trade below it. **Intraday touch is nearly free; the close is where the
information is.** This has a direct consequence for any future MFE or
stop-placement label: a target defined on "did it touch +2%" will look
impressive and mean almost nothing, because touch is the base rate.

---

## 14. Verdict — YELLOW

**GREEN was available on the gates and is refused on the content.** Every
structural, PIT, numerical and leakage check passes; the price path is clean and
the training cutoff is now explicit and tested. That earns a foundation, not a
dataset.

What holds it at YELLOW:

1. **Three feature families are missing outright** — fundamentals, sector,
   pre-open. A next-session equity model without any cross-sectional or
   valuation context is a pure price-and-volume model, and should be evaluated
   as one.
2. **Market regime is an ETF proxy**, not the index, for the whole ten years.
3. **News covers 6.5% of rows.** V1-C is a five-month dataset with 263,605 rows
   — usable for a news-conditioned study, not for a decade-long one.
4. **Survivorship is real and unmeasured**, plus 173 symbols dropped for
   unresolved identity.
5. **Depth is uneven** — 428 symbols have under a year, so long-window features
   are null or thin across a sixth of the universe.
6. **Corporate-action outliers require an explicit quarantine flag** in the
   build, or the target's variance is meaningless.

None of these is a leakage or corruption finding, which is why it is not RED.

### Next phase — recommendation

1. **Build the V1-A extract** with the quarantine flag and the depth filter,
   and freeze it as a versioned artefact so results become reproducible. Persist
   it — §8's empty manifest exists because nothing is persisted.
2. **Choose labels from §13**, not from convention: the negative median and the
   91.5% touch rate both argue against the obvious thresholds.
3. **Baseline first.** Train nothing until a naive baseline (previous return,
   or always-flat) is scored on the same split. The 46.7% positive rate means a
   coin flip has a specific, measurable number to beat.
4. **Purged, embargoed, walk-forward splits only.** Adjacent rows share
   overlapping windows; a random split leaks by construction even with a
   perfect PIT contract.
5. **Start persisting a historical instrument master** — a weekly snapshot
   costs nothing and is the only thing that will ever let survivorship bias be
   measured rather than acknowledged.
6. **Leave 18:30 alone** until V1 has produced a result. Its 1,002,584 rows are
   not blocking anything.

---

*No model trained. No 18:30 migration. No 00:00 deletion. No pre-open
implementation. No fabricated fundamentals or sector history.*
