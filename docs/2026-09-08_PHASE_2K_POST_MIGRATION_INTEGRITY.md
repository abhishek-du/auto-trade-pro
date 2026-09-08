# PHASE 2K — POST-MIGRATION INTEGRITY & PRODUCTION READINESS

**Date:** 2026-09-08 · **READ-ONLY** — no row inserted, updated, deleted or
normalized; no code, model or production behaviour changed.

---

## Executive Verdict

# YELLOW

The migration itself is clean and verified: **PASS** on database integrity, the
00:00 residual, the reader audit, look-ahead resolution, and Upstox
reproducibility. Zero reader errors across 214 sampled symbols.

It is **not GREEN** because two **HIGH** findings stand, both structural rather
than cosmetic, and neither is merely "probably safe":

* **H-1** — five production readers still query daily candles directly and see
  duplicated sessions. `morning_regime` is a near-exact twin of the bug fixed in
  `market_regime` and was never fixed: its 210-row NIFTYBEES window contains
  **75 duplicated sessions**.
* **H-2** — a point-in-time asymmetry. For the three regime symbols the reader
  exposes **today's in-progress partial bar** as the newest completed session,
  while equities correctly show only the last closed session. Features built
  intraday mix two different "as of" points.

---

## Database Integrity — **PASS**

| metric | 2J baseline | now | delta |
|---|---|---|---|
| canonical 03:45 | 4,076,785 | 4,076,785 | **0** |
| legacy 00:00 | 1,212,131 | 1,212,131 | **0** |
| legacy 18:30 | 1,002,584 | 1,002,584 | **0** |
| other conventions | 2,875 | 2,875 | **0** |
| `.BO` rows | 0 | 0 | **0** |
| total candles | — | 36,637,700 | — |
| 1d total | — | 6,294,375 | — |

No production writes landed between the migration and this audit, so no delta
needed explaining.

| check | result |
|---|---|
| duplicate `(symbol, timeframe, timestamp)` | **PASS** — 0 |
| future 1d timestamps | **PASS** — 0 |
| negative volume | **PASS** — 0 |
| NaN / infinite 1d closes | **PASS** — 0 |
| NULL 1d timestamps | **PASS** — 0 |
| `.BO` rows | **PASS** — 0 |
| 1d OHLC violations | **FAIL (LOW)** — 14 |

**The 14 OHLC violations are all in preserved legacy 00:00 data, and none in
canonical.** Every one is an **INAV** instrument (indicative NAV, not a
tradeable security) — `ABGSECINAV.NS`, `AONELIINAV.NS`, `ABSLLQINAV.NS` and
eleven siblings — each with `open = 0.00` and `volume = 0`, all written
2026-07-06 by the retired legacy writer. **Canonical 03:45 OHLC violations: 0.**
Pre-existing legacy data quality in preserved history, not introduced by this
pipeline.

---

## 00:00 Residual Audit — **PASS**

**00:00 rows still having a canonical row for the same symbol + session: 0.**
The migration was complete; nothing eligible was left behind.

Preserved: **1,212,131 rows across 2,578 symbols**, 2016-01-01 .. 2026-08-28.

| category | symbols | rows |
|---|---|---|
| NO_REPLACEMENT identity | 1,055 | 1,023,399 |
| pre-Upstox / unreplaced tail (symbol *is* otherwise replaced) | 1,520 | 186,157 |
| index / non-equity (`^NSEI`, `^NSEBANK`, `^BSESN`) | 3 | 2,575 |
| **TOTAL** | **2,578** | **1,212,131** |

---

## Daily Reader Audit — **PASS**

214 symbols through the production `daily_series` reader:

| group | n | ok | errors | empty | dup sessions | unordered | conventions |
|---|---|---|---|---|---|---|---|
| random 100 | 100 | 100 | **0** | 0 | **0** | **0** | 96,552 canonical · 2,907 `0000` · 1,277 `1830` |
| liquid 50 | 50 | 50 | **0** | 0 | **0** | **0** | 118,815 canonical · 1 `1830` |
| ETF 25 | 25 | 25 | **0** | 0 | **0** | **0** | 10,658 canonical · 14 `0000` |
| partial / pre-Upstox 25 | 25 | 25 | **0** | 0 | **0** | **0** | 25,904 canonical · 579 `1830` |
| NO_REPLACEMENT 10 | 10 | 10 | **0** | 0 | **0** | **0** | 2,906 `1830` · 2,598 `0000` |
| indices + NIFTYBEES | 4 | 4 | **0** | 0 | **0** | **0** | 2,502 canonical · 35 `0000` |

**The reader raised no errors on any sampled symbol.** Behaviour matches intent:
replaceable equity history reads canonical; preserved-only history reads its
legacy convention and is still served.

```
^NSEI          12 sessions  2026-08-24 .. 2026-09-08  canonical_0345
^NSEBANK       12 sessions  2026-08-24 .. 2026-09-08  canonical_0345
^BSESN         35 sessions  2026-07-13 .. 2026-08-28  legacy_0000   (preserved)
NIFTYBEES.NS 2,478 sessions 2016-09-06 .. 2026-09-08  canonical_0345
GUJGASLTD.NS 2,598 sessions 2016-01-01 .. 2026-06-30  legacy_0000   (no replacement)
```

---

## Look-Ahead Audit — **PASS**

**RELIANCE, the known hazard:** Sep 2 → **1313.1**, Sep 3 → **1302.5**,
Sep 4 → **1322.0**. Stored rows resolve correctly, and the 18:30 legacy row
cannot override canonical resolution.

**Large sample:** 117,173 adjacent session pairs across 180 symbols. 3,847
identical-close pairs (3.3%) were investigated rather than assumed — a 51,635-pair
re-check found 1.36%, and every sampled case is a **genuine flat close**:
distinct sessions, same convention (e.g. `APEX.NS` 2018-04-23 → 2018-04-24, both
`canonical_0345`, close 671.05). Illiquid small-caps printing an unchanged close
— not duplication.

**Calendar cases** (the contract default correctly refuses; the reader supplies
the calendar):

| date | case | with calendar | expected | |
|---|---|---|---|---|
| 2026-09-02 | ordinary Wednesday | usable | usable | PASS |
| 2026-09-05 / 06 | ordinary weekend | refused | refused | PASS |
| 2026-04-03 | Good Friday | refused | refused | PASS |
| 2026-02-01 | Budget Sunday | **usable** | usable | PASS |
| 2025-02-01 | Budget Saturday | **usable** | usable | PASS |

The reader returns exactly the ten declared special sessions and no undeclared
weekend.

---

## Daily Convention Code Audit — **1 FAIL (HIGH)**

**Zero hard-coded convention literals** (`'00:00'`, `'18:30'`, `'03:45'`,
`legacy_1830`, `canonical_0345`) exist anywhere outside `daily_series.py` and
`candle_contract.py`. No module assumes a timestamp convention.

yfinance is used **only for company metadata** (name, industry, description) in
`stock_enricher.py` — never for prices.

| module | classification |
|---|---|
| `market_regime`, `intelligence_hub`, `replay`, `performance_engine`, `ml_predictor`, `portfolio_analytics`, `india_specific`, `corporate_actions` | **1 — SAFE**, delegate through `daily_series` |
| `tactical_data_fetcher` (`LIMIT 1` spot price), `market_scanner` (`SELECT DISTINCT symbol`), `zerodha_market` (F&O spot, inactive) | **1 — SAFE**, no cross-session arithmetic |
| **`morning_regime.py:134`** | **4 — UNSAFE** |
| **`momentum_filter.py:86`** | **4 — UNSAFE** |
| **`breakout_screener.py:122`** | **4 — UNSAFE** |
| **`momentum_screener.py:120`** | **4 — UNSAFE** |
| `hub_universe.py:75,139` | **3 — REVIEW** |
| `api/stock_chat.py`, `api/zerodha.py` | **3 — REVIEW**, display/advisory |
| `india_specific.py:199` (VIX backtest) | **5 — DEAD**, `^INDIAVIX` has no 1d rows |

### H-1 — the unfixed twin

`morning_regime.py` runs the *identical* query that was removed from
`market_regime`:

```sql
SELECT close FROM candles
WHERE symbol = 'NIFTYBEES.NS' AND timeframe = '1d'
ORDER BY timestamp DESC LIMIT 210
```

Measured right now, those 210 rows are **110 canonical + 97 `legacy_1830` + 3
`legacy_0000`**, containing **75 duplicated sessions**. Its EMA50/EMA200 and
5-day return are therefore computed on a series where a third of the "days"
repeat.

Exposure of the direct readers post-migration:

| reader window | duplicated (symbol, session) pairs | symbols |
|---|---|---|
| 30 days (`hub_universe`, screeners) | 31,191 | 2,643 |
| 90 days (`momentum_filter`) | 110,246 | 3,520 |
| ~210 sessions (`morning_regime`) | 161,584 | 3,534 |

Overall **721,731 duplicated (symbol, session) pairs remain across 3,613 of
4,400 symbols** — because 18:30 still coexists with canonical everywhere. The
00:00 migration did not remove this exposure and was never intended to.

---

## Point-in-Time Audit — **1 FAIL (HIGH), 1 MEDIUM**

### H-2 — partial in-progress bar exposed as a completed session

Measured live at 15:14 IST, mid-session:

```
NIFTYBEES.NS  stored today  o=271.64 h=271.64 l=269.80 c=270.05  v=5,854,092
              live now                                  c=270.22  v=6,039,112
              reader's newest session = 2026-09-08  (today, in progress)

RELIANCE.NS   reader's newest session = 2026-09-07  (last CLOSED session)
```

The three regime symbols refresh every five minutes, so their current partial
bar is served as the newest session. Equities are written once after close, so
they correctly show the last completed session.

**Consequence:** any feature set built intraday combines an in-progress bar for
the regime input with a completed bar for equities — two different "as of"
points in one row. `market_regime`'s EMA stack and 20-day ROC currently include
a partial bar. For a live gate this is arguably current information; for a
training row labelled "session D" it is not.
**Severity: HIGH** for the prediction dataset being prepared.

### M-1 — no `as_of` in the daily reader

`session_closes(symbol, session, sessions=N)` returns the **newest** N sessions.
There is no `as_of` parameter anywhere in `daily_series`. For live inference
that is correct. For backtesting or training-set construction, a caller
evaluating session D receives a series that extends to today, so any
window-derived feature can see beyond D unless the caller trims it.

**Good PIT discipline already exists elsewhere** and should be the model:
`india_signal_generator` threads `bar_date` into FII/DII (`FIIDIIFlow.date <=
bar_date`), news sentiment and VIX; `replay.py` is explicitly point-in-time and
documented as forbidding any candle at or after the cutoff.

| source | PIT status |
|---|---|
| daily candles (live path) | **PASS** — equities show last closed session |
| daily candles (regime symbols, intraday) | **FAIL — HIGH** (H-2) |
| daily candles (backtest / training) | **MEDIUM** — no `as_of` (M-1) |
| intraday candles | PASS — timestamped, queried by range |
| news | PASS — `published_at` carried; `bar_date` threaded |
| FII/DII | PASS — `date <= bar_date` |
| corporate actions | PASS — guard uses `basis="raw"`, compares D close to D+1 open |
| fundamentals | **UNPROVEN** — no explicit as-of bound found |
| market regime | FAIL — inherits H-2 |
| sector data | **UNPROVEN** |
| pre-open data | **UNPROVEN** — `iep` feed still uncollected |

---

## Special Session Calendar — **MEDIUM, unfixed**

1. **Where it breaks:** `validate_canonical_daily_equity_candle` →
   `is_nse_trading_session(ts.date(), holidays, extra_open)` at
   `candle_contract.py:376`. With `extra_open=None` a real special session is
   rejected at the write path.
2. **Does the live API expose it?** **Yes.** `/v2/market/holidays` returns
   `2026-11-08 SPECIAL_TIMING open_exchanges=[… NSE …] "Diwali Laxmi Pujan"`.
3. **Can `nse_extra_open_dates(payload)` be reused?** **Yes** — it already
   derives `['2026-02-01', '2026-11-08']` correctly from that payload.
4. **Which writers need it:** **22 `save_candles_to_db` call sites** across
   `india_price_feed`, `upstox_historical`, `zerodha_historical`,
   `zerodha_market`, `india_tasks`, `api/india` — none passes `extra_open`.
5. **Can it be centralised?** **Yes, and it should be.** Rather than patching 22
   call sites, give `save_candles_to_db` a cached calendar accessor that
   defaults `extra_open` when the caller omits it — mirroring what
   `daily_series._collapse_to_sessions` already does on the read side. One
   change, symmetric with the reader.

**Recommendation (not implemented):**

| | |
|---|---|
| files | `utils/candle_contract.py` (cached accessor), `crawler/price_feed.py` (default the parameter) |
| current | `extra_open=None` → a live Muhurat/Budget bar is refused and lost |
| proposed | choke point defaults to a cached, TTL-refreshed calendar from `/v2/market/holidays`, overridable per call |
| tests | cache hit/miss; API failure path; ordinary weekend still refused; 2026-11-08 accepted; existing 22 call sites unchanged in behaviour on ordinary days |
| risk | **the failure mode must be decided first** — if the API is unreachable on a Muhurat morning, fail-open risks accepting a genuine weekend bar, fail-closed reproduces today's behaviour. This is why it is a recommendation, not a patch. |

**Deadline: 2026-11-08**, ~2 months out.

---

## Canonical Freshness — **PASS with a noted asymmetry**

| symbol | latest session | latest timestamp | created_at | today's bar |
|---|---|---|---|---|
| RELIANCE / TCS / INFY / HDFCBANK / ICICIBANK / SBIN | 2026-09-07 | 03:45 | 2026-09-07 14:09 | **absent** |
| NIFTYBEES.NS | **2026-09-08** | 03:45 | 2026-09-08 03:49 | present, c=270.05 |
| ^NSEI | **2026-09-08** | 03:45 | 2026-09-08 03:49 | present, c=23639.6 |
| ^NSEBANK | **2026-09-08** | 03:45 | 2026-09-08 03:49 | present, c=56767.75 |

Writer confirmed: **Upstox**, **canonical 03:45**, **one scheduled path**
(`tasks.sync_regime_daily_candles`, 300 s, `scan_queue`), current-session
refresh working. Equity bars are written once daily by `kite_sync_candles`
(10:00 UTC), which is why today's equity bar is absent at 15:14 IST — expected,
and the basis of the H-2 asymmetry.

---

## Upstox Reproducibility — **PASS**

20 symbols across large-cap, mid/small-cap, ETF and index, last 12 months:

| | |
|---|---|
| rows compared | 4,823 |
| **exact matches** | **4,797 (99.46%)** |
| price differences | 9 |
| volume-only differences | 17 |
| sessions affected | 2026-09-04, 2026-09-07, 2026-09-08 |
| confined to the 7-day settlement window | **True** |

Nothing was overwritten.

---

## 18:30 Future Migration Assessment — audit only, **technically feasible**

| | |
|---|---|
| rows | 1,002,584 |
| symbols | 4,249 |
| sessions | 2021-06-23 .. 2026-09-02 |
| **rows whose resolved session (D+1) already has canonical** | **926,016 (92.36%)** |
| rows without | 76,568 |
| closes with >2 decimals | **0 of 1,002,584 (0.00%)** |

**The 18:30 series is raw/unadjusted** — the same price-basis family as
canonical, unlike the 00:00 series that was just removed (which was
price-only adjusted). Combined with 92.36% replacement coverage, a Phase 3
migration is technically feasible and materially *simpler* than the 00:00 one:
no adjustment-basis question to resolve.

**No 18:30 row was read for replacement, modified or deleted.**

---

## Historical Universe / Survivorship

Across all remaining legacy data (00:00 + 18:30), 2,214,715 rows:

| category | symbols | legacy rows |
|---|---|---|
| in current Upstox master | 2,650 | 1,112,754 |
| series forms (`-BE`/`-SM`/`-BZ`/`-GS`/…) | 1,471 | 791,227 |
| delisted / renamed / unknown identity | 226 | 308,159 |
| index | 3 | 2,575 |
| **TOTAL** | **4,350** | **2,214,715** |

No speculative identity mapping was attempted. The 226 delisted/unknown
identities and the 1,471 series forms cannot be replaced from the current Upstox
master; that history exists only in the preserved legacy rows.

---

## Open Blockers

| id | severity | finding | status |
|---|---|---|---|
| **H-1** | **HIGH** | `morning_regime`, `momentum_filter`, `breakout_screener`, `momentum_screener` read daily candles directly and see duplicated sessions (75 duplicates in `morning_regime`'s 210-row window) | **FAIL** |
| **H-2** | **HIGH** | Regime symbols expose today's partial in-progress bar as the newest completed session; equities do not — asymmetric as-of | **FAIL** |
| M-1 | MEDIUM | `daily_series` has no `as_of`; backtest/training callers can see beyond the evaluated session | **FAIL** |
| M-2 | MEDIUM | 22 production writers pass `extra_open=None`; 2026-11-08 Muhurat will be dropped | **FAIL** |
| M-3 | MEDIUM | Fundamentals, sector data and pre-open have no demonstrated as-of bound | **UNPROVEN** |
| L-1 | LOW | 14 OHLC violations in preserved legacy INAV rows (`open=0`, `volume=0`) | **FAIL**, contained — not in canonical |
| L-2 | LOW | 721,731 duplicated (symbol, session) pairs remain because 18:30 coexists with canonical | expected; Phase 3 |

---

## Recommended Next Phase

1. **Fix H-1 first** — route the four UNSAFE readers through `daily_series`, as
   `market_regime` already is. Small, mechanical, and it removes the largest
   remaining correctness gap. `morning_regime` is the priority: it feeds a
   trading gate on a series where a third of the days repeat.
2. **Resolve H-2** — decide explicitly whether a partial in-progress bar may
   appear as a session, then either exclude the current session from
   `session_closes` by default (with an opt-in for live gates) or label it. This
   must be settled **before** the prediction dataset is built, not after.
3. **Then M-1** — add an `as_of` parameter to the daily reader so training rows
   cannot see beyond their own session.
4. **Then M-2** — centralise the special-session calendar at the write choke
   point, after deciding the API-failure posture. Deadline 2026-11-08.
5. **Phase 3 (18:30)** is technically feasible and simpler than 00:00 was, but
   should follow the above, not precede it.

---

**This phase was read-only. No database mutation, no model change, no 18:30
deletion, no backfill, no retraining, no production behaviour change. No
production worker was restarted.**
