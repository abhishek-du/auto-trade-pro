# PHASE 2K REMEDIATION — H-1 / H-2 / M-1

**Date:** 2026-09-08 · **Final gate: PASS**
No 18:30 or 00:00 deletion, no backfill, no training, no strategy redesign, no
validator weakened. Database untouched.

---

## H-1 — Unsafe direct daily readers

### Old behaviour

Four modules queried `candles` directly for daily windows and took the newest N
**rows**. While 18:30 coexists with canonical, N rows is not N sessions.

| module | window | old query |
|---|---|---|
| `morning_regime._fetch_regime_inputs` | 210 | `ORDER BY timestamp DESC LIMIT 210` — the exact query removed from `market_regime` in 2D.2 |
| `momentum_filter._compute_ranks` | 64 (63-day HPR) | one `ORDER BY symbol, timestamp DESC`, newest 64 rows per symbol |
| `breakout_screener` | 25 | `timestamp >= now() - (25+5) calendar days` |
| `momentum_screener` | 37 | `timestamp >= now() - (37+5) calendar days` |

`morning_regime` also compared "close now vs close ~50 **trading** days ago"
while the SQL used a 48–56 **calendar**-day window, on raw rows.

### Duplicate sessions — before / after

The required `morning_regime` reproduction, measured on production:

```
OLD  210 raw rows  = 110 canonical_0345 + 97 legacy_1830 + 3 legacy_0000
     duplicated sessions: 75

NEW  210 resolved sessions
     duplicated sessions: 0
     ordered: True   conventions: ['canonical_0345']
     newest session: 2026-09-07  (2026-09-08 excluded while in progress)
```

Exposure across the other windows, before the fix:

| window | duplicated (symbol, session) pairs | symbols |
|---|---|---|
| 30 days (screeners) | 31,191 | 2,643 |
| 90 days (`momentum_filter`) | 110,246 | 3,520 |
| ~210 sessions (`morning_regime`) | 161,584 | 3,534 |

### New behaviour

All four route through the production reader. The unit of uniqueness is
**symbol + resolved trading session**, decided by the same
`_collapse_to_sessions` / `resolve_daily_session_date` that `daily_series` uses.

**Deliberately not `DISTINCT timestamp`** — that deduplicates rows, not
sessions, and would still keep an 18:30 bar beside the canonical bar for the
same session. A test asserts `DISTINCT timestamp` appears nowhere.

Two new bulk readers were added because the screeners and the momentum filter
scan hundreds of symbols per cycle and a per-symbol round trip would turn one
query into hundreds:

```python
session_closes_bulk(symbols, session, *, sessions, as_of=None, include_current=False)
session_bars_bulk  (symbols, session, *, sessions, as_of=None, include_current=False)
```

Both fetch once and then run the **same** resolver per symbol — not a second
implementation. Verified equal to the single-symbol reader row for row on
RELIANCE, TCS, NIFTYBEES and 3IINFOLTD.

`morning_regime`'s breadth check now compares 51 resolved sessions ("now vs 50
sessions ago"), which is what its own comment always claimed. End-to-end it
returns `breadth_pct=62.4`, `5d_ret=-0.75`, `above_ema50=False`.

### Affected callers

| module | caller | classification |
|---|---|---|
| `morning_regime` | `agent_loop:269` (`get_morning_regime`) — AGGRESSIVE/SELECTIVE/WAIT day gate | CLOSED/HISTORICAL |
| `momentum_filter` | `agent_loop:22` (`refresh_if_needed`) — eligibility ranking | CLOSED/HISTORICAL |
| `breakout_screener` | `india_tasks:226` (`tasks.breakout_discovery`, 300 s) | CLOSED/HISTORICAL |
| `momentum_screener` | `india_tasks:270` (`tasks.momentum_discovery`, 1800 s) | CLOSED/HISTORICAL |

All four are closed-only: they run against equities, whose daily bar is written
once after the close, so closed-only **preserves their existing effective
semantics** while fixing the duplicate-session defect.

**Result: PASS.**

---

## H-2 — The daily as-of contract

### Default semantics

```python
session_closes(symbol, session, *, sessions=220,
               as_of=None, include_current=False, ...)
```

`include_current=False` is the **default**: completed sessions only. An
in-progress bar never appears in output that presents itself as finished
sessions.

**Completion comes from the calendar and the clock, never from row presence.**
`current_open_session()` returns the in-progress session when today is an NSE
trading day and the clock is before 15:30 IST, else `None`. Row presence would
have been wrong precisely because the regime writer refreshes today's bar every
five minutes — a half-finished session would have looked complete.

### Explicit live semantics

`include_current=True` is the opt-in for a live intraday gate that genuinely
wants the forming bar.

### Regime-symbol behaviour

Measured at **15:14 IST (pre-close)**:

| symbol | default (closed-only) | `include_current=True` |
|---|---|---|
| `^NSEI` | 2026-09-07 | **2026-09-08** |
| `^NSEBANK` | 2026-09-07 | **2026-09-08** |
| `NIFTYBEES.NS` | 2026-09-07 | **2026-09-08** |

Measured again at **15:32 IST (post-close)** both return 2026-09-08 — correct,
because the session is now genuinely complete. The distinction is temporal, as
it should be, not a permanent exclusion.

The original H-2 symptom is gone: previously `NIFTYBEES` reported today's
partial bar (270.05 stored against 270.22 live) as its newest *session* while
`RELIANCE` reported the last closed session — two as-of points in one row.

### Caller classifications

| caller | classification | action |
|---|---|---|
| `market_regime` | **LIVE CURRENT-SESSION** | `include_current=True` **explicit** — behaviour preserved, not silently changed |
| `morning_regime`, `momentum_filter`, `breakout_screener`, `momentum_screener` | CLOSED/HISTORICAL | default |
| `intelligence_hub` (EMA200 `nifty_regime`) | CLOSED/HISTORICAL | default — an EMA200 regime label is a completed-session concept |
| `performance_engine._aligned_closes` | REQUIRES EXPLICIT AS_OF | parameter available and threaded; callers pass none today |
| `ml_predictor` (`session_bars`) | **REQUIRES EXPLICIT AS_OF** | must pass `as_of` when the training set is built |
| `portfolio_analytics.get_nifty_return` | CLOSED/HISTORICAL | default |
| `india_specific._fetch_candle_prices` | CLOSED/HISTORICAL | default |
| `corporate_actions` | LIVE (compares D close to D+1 open) | default; `basis="raw"` retained |
| `replay._close_near` / `_candles_between` | **REQUIRES EXPLICIT AS_OF** | see the finding below |

**No silent behaviour change:** `market_regime` was the only live caller relying
on the forming bar, and it now says so in code.

**Result: PASS.**

---

## M-1 — Point-in-time `as_of`

### Contract

`as_of=date(...)` bounds the result to sessions **on or before** that date,
applied to the **resolved session**, never to the stored timestamp — an 18:30 row
carries the calendar date of the day *before* its session, so filtering the raw
timestamp would let the next session through. Available on `session_closes`,
`session_close_series`, `session_bars`, both bulk readers, and
`close_for_session`.

### Test results — production database

| test | case | newest returned | expected | |
|---|---|---|---|---|
| **A** | `as_of = 2026-09-07` | 2026-09-07 | 2026-09-07 | **PASS** |
| **B** | `as_of = 2026-09-02` | 2026-09-02 | 2026-09-02 (no Sep 3/4) | **PASS** |
| **C** | `as_of = 2026-09-06` (Sunday) | 2026-09-04 | last completed ≤ Sunday | **PASS** |
| **D** | `as_of = 2026-02-01` (Budget Sunday) | 2026-02-01 | the special session itself | **PASS** |
| **E** | RELIANCE Sep 2/3/4 | 1313.1 / 1302.5 / 1322.0 | unchanged | **PASS** |

Repeated for cutoffs 2026-09-02, 2026-08-20 and 2025-01-15: no session past the
cutoff was ever returned.

### A real look-ahead this exposed

`close_for_session` picks the *nearest* session to a target and could reach
**forward**:

```
target = 2026-09-06 (Sunday)
  without as_of -> 1309.5   (Sep 7 — AFTER the target)
  with    as_of -> 1322.0   (Sep 4 — the last completed session)
```

`replay._close_near` calls it for the point-in-time **entry** price at `as_of`,
so a backtest entry could take the next session's close. The parameter now
exists and is tested; **`replay` was not changed in this phase** — doing so
would alter backtest outputs, and `replay` is not one of the three items
authorised here. Recorded as the top item for the next phase.

**Result: PASS** for the contract; the `replay` adoption is **UNPROVEN** and
carried forward.

---

## Regression

| | failures/errors | passed | skipped |
|---|---|---|---|
| baseline | 32 | 2,532 | 11 |
| after | **32** | **2,730** | 22 |
| **new failures** | **0** | | |
| **new errors** | **0** | | |

`tests/test_step2k_remediation.py` — **28 tests, 21 passed, 7 skipped** (the
skips are real-DB cases; under pytest `DATABASE_URL` points at the empty
`autotrade_test`, and they are proven against production above).

**Behaviour changes, all intentional and tested:**
1. Four modules now consume resolved sessions instead of raw rows.
2. `morning_regime` breadth uses 50 sessions instead of 48–56 calendar days.
3. `market_regime` keeps the forming bar via an explicit flag.
4. Every other daily consumer is now closed-only by default.

---

## Files changed

| file | change |
|---|---|
| `engine/daily_series.py` | `as_of` + `include_current` on all readers; `current_open_session()`; `_apply_as_of()`; `session_closes_bulk()`; `session_bars_bulk()`; `as_of` on `close_for_session` |
| `engine/agent/morning_regime.py` | 210-session EMA window + 51-session breadth via the shared reader |
| `engine/agent/momentum_filter.py` | 64-session HPR via `session_closes_bulk` |
| `engine/breakout_screener.py` | 25-session window via `session_bars_bulk` |
| `engine/momentum_screener.py` | 37-session window via `session_bars_bulk` |
| `engine/agent/market_regime.py` | explicit `include_current=True` |
| `tests/test_step2k_remediation.py` | **new**, 28 tests |

---

## Final Gate

| item | verdict |
|---|---|
| **H-1** — no unsafe direct daily readers among the four | **PASS** — 75 → 0 duplicated sessions; all four delegate; `DISTINCT timestamp` absent |
| **H-2** — closed-only default, explicit live opt-in | **PASS** — calendar-driven; regime symbols verified pre- and post-close |
| **M-1** — `as_of` point-in-time contract | **PASS** — tests A–E all pass on production |
| Regression | **PASS** — 0 new failures |
| `replay` adopting `as_of` for entry lookups | **UNPROVEN** — parameter exists and is tested; caller unchanged by scope |

# GREEN for H-1, H-2 and M-1

The three authorised items are resolved and evidenced. Two things remain open
and are **not** part of this phase: `replay`'s entry lookup should adopt `as_of`
(a backtest-output change), and M-2, the production special-session calendar,
whose deadline is **2026-11-08**.

**No 18:30 migration, no deletion, no backfill, no training, no strategy
redesign. Phase 3 untouched.**
