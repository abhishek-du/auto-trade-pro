# PHASE 2E — DAILY PIPELINE INTEGRITY REPORT

**Date:** 2026-09-07 · **Type:** evidence + runtime audit (read-only)
**Database mutations by this phase:** 0 (verified, §14)
**Production code changed by this phase:** none
**Services restarted / processes killed:** none

---

## 1. Executive Verdict

# RED

The daily pipeline is **structurally safer than in 2D.2 but not yet safe**. Four
blockers stand, two of them new to this phase.

| # | Blocker | Severity |
|---|---|---|
| 1 | Index/ETF daily writer still emits `00:00` — the contract guard does not govern non-equity instruments | **CRITICAL** |
| 2 | Six production workers run 28-commit-old code; one executes trades, one runs the corporate-action guard | **CRITICAL — RUNTIME BLOCKER** |
| 3 | The 2D.2 delete removal left the regime writer unable to correct its own rows; today's bars are frozen mid-session | **HIGH — regression introduced by 2D.2** |
| 4 | `00:00` price basis is now identified but is unusable as-is: retroactive price-only adjustment plus contaminated dates | **HIGH** |

Confirmed good: **no DELETE or TRUNCATE of candles survives anywhere in
production code** (§3); the NSE-only gate holds with zero `.BO` daily rows (§10);
the equity canonical writer is correct and on schedule (§11); all readers remain
session-correct with 361 targeted tests and 0 new failures (§13).

---

## 2. Complete 1D Writer Inventory

Twelve write paths reach `candles`. Only the persistence choke point
(`crawler/price_feed.py::save_candles_to_db`) enforces the canonical contract,
and it enforces it **for NSE equity only**.

| # | Writer | file:line | Schedule / queue | Timestamp | Guard | DELETE | Status |
|---|---|---|---|---|---|---|---|
| 1 | `sync_all_nse_candles` (`tasks.kite_sync_candles`) | `celery_app.py:529` | daily 10:00 UTC | **03:45** | enforced | none | **ACTIVE — PASS** |
| 2 | `backfill_hub_1d_candles` | `india_tasks.py:4219` | daily 03:10 UTC | 03:45 | enforced | none | ACTIVE — PASS |
| 3 | `refresh_priority_1d_candles` | `india_tasks.py:4479` | evening | 03:45 | enforced | none | ACTIVE — PASS |
| 4 | `run_india_price_crawl` (`tasks.india_price_scan`) | `india_price_feed.py:1235` | every 5 min, **default** queue | 03:45 | enforced | none | ACTIVE — PASS |
| 5 | **`sync_regime_daily_candles_kite`** | `india_price_feed.py:696` | inside #4, every 5 min | **00:00 (forced)** | **BYPASSED** | removed in 2D.2 | **ACTIVE — FAIL** |
| 6 | on-demand candle persist | `api/india.py:356` | request-driven (uvicorn) | 03:45 | enforced | none | ACTIVE — PASS |
| 7 | `candle_resampler` | `candle_resampler.py:60` | `scan_queue` | n/a — **5m/15m/1h only**, never 1d | bypasses (by design) | none (`DO UPDATE`) | ACTIVE — N/A |
| 8 | `upstox_historical` backfill | `upstox_historical.py:230` | on demand | 03:45 | enforced | none | ACTIVE — PASS |
| 9 | legacy 18:30 writer (#1 before `f49ebb9`) | — | — | 18:30 | now **rejected** by the guard | none | **STOPPED** — last write 2026-09-03 12:13 |
| 10 | `zerodha_historical` | `zerodha_historical.py:142+` | — | — | enforced | none | INACTIVE (Kite disabled) |
| 11 | `scripts/backfill_1d_candles.py` | `:132` | manual | raw INSERT, bypasses guard | none | none (`DO NOTHING`) | **DISABLED at import** (`ALLOW_LEGACY_1D_BACKFILL`) |
| 12 | `scripts/backfill_candles.py`, `backfill_all_candles_zerodha.py` | — | manual | via choke point | enforced | none | manual only |

**Writer #5 is the single unsafe active daily writer.** It forces every bar to
midnight IST:

```python
ist_date = (ts + timedelta(hours=5, minutes=30)).date()
c["timestamp"] = datetime(ist_date.year, ist_date.month, ist_date.day)   # 00:00
```

for `_REGIME_DAILY_SYMBOLS = ("NIFTYBEES.NS", "^NSEI", "^NSEBANK", "^BSESN")`.

**Conflict key** (all upserting writers): `uq_candle_bar UNIQUE (symbol,
timeframe, timestamp)` — correct, but note the consequence: two conventions for
the same session are *different rows*, so they never conflict and both persist.
That is the mechanism by which the conventions accumulated.

**Correction to earlier phases:** the table holds **14 distinct daily
time-of-day values**, not three. Beyond 00:00/03:45/18:30 there are rows at
04:21, 04:26, 04:30, 04:54, 04:56, 05:30, 06:30, 07:30, 08:30, 09:30 and 12:30 —
~2,875 rows, all last written 2026-06-18, no writes since. The remediated reader
refuses them (unrecognised convention), so they are inert, but "three
conventions" was an undercount.

---

## 3. Delete/Overwrite Audit

Repository-wide search for `DELETE FROM candles`, `delete(Candle)`,
`TRUNCATE candles`, `delete_recent`, `refresh daily`:

| Finding | Verdict |
|---|---|
| `india_price_feed.py:725` | **prose only** — the comment explaining the removal |
| `tests/test_step2d2_daily_series.py:97,107` | test fixture, `autotrade_test` DB only |
| `india_tasks.py:3044` | `_r.delete("kite_live_candles:running")` — a **Redis key**, not candles |

1. Blind DELETE completely removed — **confirmed**
2. No equivalent DELETE elsewhere — **confirmed**
3. No TRUNCATE anywhere — **confirmed**
4. No DELETE+INSERT pattern can remove canonical rows — **confirmed**
5. UPSERT conflict key correct — **confirmed** (`symbol, timeframe, timestamp`)
6. One writer cannot overwrite another's data — **confirmed**, but see the
   caveat below
7. No scheduled job recreates the defect — **confirmed**

### 3a. The caveat — a regression this audit found in the 2D.2 fix

`save_candles_to_db` uses **`ON CONFLICT DO NOTHING`**, not `DO UPDATE`. It is
insert-if-absent; it never corrects an existing row. The 2D.2 code comment
calling it an idempotent "upsert" was imprecise, and the consequence is
material:

with the DELETE removed, `sync_regime_daily_candles_kite` **can no longer update
a bar it has already written**. Evidence:

```
NIFTYBEES.NS 2026-09-07 00:00  o=273.19 h=273.78 l=270.90 c=271.40  created 07:18:14 UTC
^NSEI        2026-09-07 00:00  c=23754.10                            created 07:18:15 UTC
^NSEBANK     2026-09-07 00:00  c=57051.05                            created 07:18:15 UTC
```

07:18 UTC is **12:48 IST — mid-session** (NSE 09:15–15:30 IST). The task runs
every 5 minutes; at 08:25 UTC these rows were unchanged. Today's regime bars are
therefore frozen at a partial-day value and cannot be corrected to the true
close.

Previously the blind DELETE provided that refresh — destructively, by wiping
every convention in a 15-day window. **The fix removed the destruction and the
refresh together.** Both behaviours were wrong; the correct design is a refresh
scoped to the writer's own rows.

**Required next action:** give writer #5 either `ON CONFLICT DO UPDATE` for its
own bars, or a DELETE narrowed to its own convention
(`AND to_char(timestamp,'HH24:MI') = '00:00'`) — after blocker 1 is resolved, so
it is writing canonical timestamps in the first place. Not done here: this phase
forbids production code changes beyond instrumentation.

---

## 4. Database Control Evidence

Read-only, four control symbols. The result is the cleanest confirmation yet of
the session-offset contract — **the 18:30 close on date D equals the 03:45 close
on D+1**, in all four symbols, on every overlapping date:

| session | RELIANCE 03:45 | RELIANCE 18:30 | TCS 03:45 | TCS 18:30 |
|---|---|---|---|---|
| 2026-08-31 | 1277 | 1309 | 2399.3 | 2369 |
| 2026-09-01 | 1309 | 1313.1 | 2369 | 2348 |
| 2026-09-02 | **1313.1** | **1302.5** | 2348 | 2320.1 |
| 2026-09-03 | 1302.5 | — | 2320.1 | — |

Reading down the 03:45 column and the 18:30 column one row up gives the same
series. An 18:30 bar **is** the next session — mechanically, not approximately.
A Sunday 18:30 row (2026-08-30, close 1277) maps to Monday's session, which is
exactly what `resolve_daily_session_date` does.

Coverage, identical across all four symbols:

| series | span | last written |
|---|---|---|
| 00:00 | 2016-01-01 .. **2026-06-22** | 2026-06-22 |
| 18:30 | 2021-06-23 .. **2026-09-02** | 2026-09-03 |
| 03:45 | 2026-05-07 .. **2026-09-04** | 2026-09-04 |

No evidence of canonical rows disappearing and reappearing for equities — that
pattern was confined to the four regime symbols and is explained by writer #5.

---

## 5. Runtime Process Audit

Repository HEAD: `99cdf7c` (2026-09-07 12:08).

| Process | PID | Started | Code age | Uses daily candles | Affected by 2C/2D/2D.2 | Restart |
|---|---|---|---|---|---|---|
| celery `default` (4 procs) | 2037701, 2037838, 2037876, 2037877 | **13:27 today** | current | YES | — already current | no |
| news engine | 2037745 | **13:27 today** | current | YES | already current | no |
| uvicorn | 1975541 | 12:03 today | **pre-2D.2** | YES (`api/india.py` → regime) | YES | **YES** |
| celery `exit_queue` | 3523760, 3523771 | **Aug 26 21:22** | **28 commits** | YES | YES | **YES** |
| celery `trade_queue` | 3523764, 3523783 | **Aug 26 21:22** | **28 commits** | YES | YES | **YES** |
| celery `scan_queue` | 3523763 | **Aug 26 21:22** | **28 commits** | YES | YES | **YES** |
| celery `scan_queue` | 3974087 | **Aug 27 14:56** | 27 commits | YES | YES | **YES** |

The `default` worker and news engine are wrapped in `watchmedo` and restarted at
13:27 when the 2D.2 edits landed — the remediation **is** live there. The other
seven processes are not wrapped and did not.

The stale workers pre-date, among 28 commits: the entire Upstox migration
(`5b4ec52`, `447ddbb`), NSE-only enforcement (`401cb06`), the paper-only
real-order closure (`a25be59`), the canonical daily writer (`6ce80b8`), the
session-open anchor (`f49ebb9`) and the reader look-ahead fix (`0b7b642`).

**They also hold cached settings from a `.env` that has since been modified**
(`.env` mtime 2026-09-07 08:16). Current values are `PAPER_MODE=True`,
`AGENT_PAPER_MODE=True`, `ZERODHA_ENABLED=false`. What those workers loaded on
Aug 26 **cannot be determined without restarting them**, which this phase
forbids. Treat it as unverified, not as safe.

One mitigation confirmed: the D2 `place_real_order` signature defect was already
fixed in the Aug 26 code, so that specific path is not open.

---

## 6. Runtime Risk

| Queue | Task | Consumes | Classification |
|---|---|---|---|
| `trade_queue` | `tasks.india_trade_loop` → `_phase9_market_context` (`india_tasks.py:470`) → `get_market_regime` | market_regime **and** intelligence_hub, both pre-remediation | **CRITICAL — RUNTIME BLOCKER** |
| `exit_queue` | `tasks.fast_sl_check` → `check_and_handle_corporate_actions` (`india_tasks.py:1566`) | the corporate-action split guard, pre-`basis="raw"` | **CRITICAL — RUNTIME BLOCKER** |
| `scan_queue` | tactical scans, `resample_intraday_candles` | `get_f1_universe` (a SAFE reader); resampler is 1m→5m/15m/1h | **MEDIUM** |
| uvicorn | `api/india.py:2459` regime display | market_regime, pre-remediation | **MEDIUM** (display) |

**Why `trade_queue` is CRITICAL.** It computes the market regime with the old
`ORDER BY timestamp DESC LIMIT 220` — 220 rows over ~80 sessions, 51% of returns
exactly zero, volatility understated 39%, a 20-day ROC spanning ~7 real
sessions. That regime is a trading gate, and this worker originates trades.

**Why `exit_queue` is CRITICAL.** It compares a daily close against the next
morning's raw intraday open and treats a large gap as a split, then calls
`adjust_open_positions` to rewrite position quantities. Without the `basis="raw"`
lock it can be served an adjusted close, in which case the ratio it measures
*is* the corporate action — a false detection that silently rewrites a live
position. GKENERGY.NS, the current open position, carries all three conventions.

---

## 7. Index Writer Verification

# FAIL — INDEX WRITER STILL UNSAFE

| symbol | rows | 00:00 | 03:45 | latest session | last written |
|---|---|---|---|---|---|
| `^NSEI` | 1,281 | **1,281** | **0** | 2026-09-07 | **2026-09-07 07:18** |
| `^NSEBANK` | 1,281 | **1,281** | **0** | 2026-09-07 | **2026-09-07 07:18** |
| `^BSESN` | 35 | 35 | 0 | 2026-08-28 | 2026-08-28 |

The scheduled job **has run** — today, at 07:18 UTC — and **still produced
`00:00`**. By the brief's own criterion this is FAIL, not UNPROVEN.

**This supersedes the Phase 2D.2 verdict of UNPROVEN**, which was recorded
before evidence of a post-fix run existed. The 2D.2 report was wrong to leave it
open; the run had already happened.

**Root cause — and it is not a missing schedule.** Two independent gaps:

1. `sync_regime_daily_candles_kite` calls `get_kite_historical` **directly**,
   bypassing `fetch_nse_candles`, where the 2D.0.1 `NSE_INDEX` canonical
   delegation lives. It then re-anchors every bar to midnight IST.
2. Even if it reached the choke point, the contract would not stop it.
   `utils/candle_contract.py::validate_canonical_daily_equity_candle` reads:

   ```python
   klass = classify_instrument(candle.get("symbol"))
   if klass is not InstrumentClass.NSE_EQUITY:
       # Indices/ETFs/debt keep their own (legacy) handling
       return True, None
   ```

   Indices (`NSE_INDEX`) and NIFTYBEES.NS (`ETF_OR_INAV`) are **explicitly
   exempt**. The guard cannot reject what it does not govern.

Per the brief, the job was not run manually and no index row was inserted.

---

## 8. 00:00 Price-Basis Investigation

**Answer: (A) consistently adjusted — price-only — for the dominant mode, with a
small contaminated subset. Overall: not usable without normalization.**

Method: join `00:00` against `03:45` on the same session (both are session-date
conventions, so a same-date join is a same-session comparison) across the whole
overlap, 2026-05-07 .. 2026-06-22.

**Finding 1 — the dominant mode is a retroactive price-only adjustment.**
Of 108 pairs differing >0.5%, **100 (92.6%) have byte-identical volume**. That
signature — price scaled, volume untouched — is `yfinance auto_adjust`, not a
data error. INFY is the clean example:

| session | 00:00 close | 03:45 close | price × | volume × |
|---|---|---|---|---|
| 2026-06-02 | 1243.88 | 1270.80 | **0.9788** | 1.0000 |
| 2026-06-05 | 1172.14 | 1197.50 | **0.9788** | 1.0000 |
| 2026-06-09 | 1155.30 | 1180.30 | **0.9788** | 1.0000 |
| 2026-06-10 | 1145.30 | 1145.30 | 1.0000 | 1.0000 |

A constant factor applied to every session **before** 2026-06-10 and none after
— i.e. the ex-date is 2026-06-10 and history was rewritten backwards from it.
The per-date divergence count decays exactly as this predicts: 7 symbols
affected on 05-07, then 6, 4, 3, 2, and 0 from 06-10 — each symbol's adjustment
ending at its own ex-date.

**Finding 2 — a small contaminated subset that is neither adjusted nor raw.**
Eight pairs, on 2026-06-15, 06-16 and 06-22, differ in **both** price and
volume:

| symbol | session | 00:00 close / vol | 03:45 close / vol | price × | vol × |
|---|---|---|---|---|---|
| BAJFINANCE.NS | 2026-06-16 | 1788.90 / 2,041,613 | 959.65 / 9,335,988 | 1.864 | 0.219 |
| LTTS.NS | 2026-06-16 | 4007.50 / 513,049 | 3462.90 / 86,988 | 1.157 | 5.898 |

**This corrects the Phase 2D.2 report.** 2D.2 read BAJFINANCE as "stale
pre-split" and LTTS as "inconsistent". Neither holds: BAJFINANCE's 00:00 rows
match the raw series *exactly* on 06-08 through 06-12 and again from 06-17
onward, so the series is not pre-split — the divergence is confined to two
consecutive days, and it hits multiple unrelated symbols on those same two days.
That is a bad ingest window, not a corporate action.

**Finding 3 — attribution is impossible from stored data.** There is no
`corporate_actions` table (`to_regclass` → `None`) and the `candles` table has
no provenance column: `[id, symbol, timeframe, open, high, low, close, volume,
timestamp, created_at]`. Source, adjustment factor and action type are all
unrecorded. Any per-symbol factor must be re-derived from an external source.

---

## 9. Authoritative Historical Price-Basis Recommendation

| series | point-in-time | source consistency | corp-action consistency | volume consistency | continuity | recommendation |
|---|---|---|---|---|---|---|
| **03:45 Upstox raw** | correct (session open) | single source | unadjusted throughout | consistent with price | 2026-05-07 → present | **AUTHORITATIVE** |
| **18:30 legacy raw** | correct **after** resolution (session = date + 1) | same raw prices | unadjusted | consistent | 2021-06-23 → 2026-09-02 | **SECONDARY** — required for pre-May-2026 history |
| **00:00 legacy** | session date correct | yfinance | price-only adjusted, factors unrecorded | **inconsistent — price adjusted, volume not** | 2016-01-01 → 2026-06-22 | **REQUIRES NORMALIZATION**; **EXCLUDE FROM MODEL DATASET** as it stands |

**Reasoning.** The 00:00 series is the only one reaching back to 2016, so it is
tempting for training. But its price and volume are on different bases: any
feature combining them (VWAP, turnover, volume-weighted returns, price × volume
liquidity screens) is internally inconsistent by construction. The adjustment
factors are not stored and the actions are not recorded, so the series cannot be
inverted back to raw without an external corporate-action feed. Three dates are
additionally contaminated.

**A correct session date is not sufficient grounds for migration** — which is
exactly the caution the brief raised, and it is the right one.

---

## 10. NSE-Only Writer Audit

**PASS.**

| check | result |
|---|---|
| `.BO` daily (1d) rows | **0** |
| `.BO` rows any timeframe | 5,082 (5m: 3,829, 1h: 1,253) |
| last `.BO` write | 2026-08-27 08:50 |
| `.BO` rows written in the last 7 days | **0** |

The NSE-only enforcement (`401cb06`, 2026-08-28) is holding; the residual `.BO`
rows are intraday history from before it and are not being added to.

**One latent BSE path:** `^BSESN` (a BSE index) remains in
`_REGIME_DAILY_SYMBOLS`, so writer #5 still attempts it each run. It has not
produced a row since 2026-08-28, so it currently fails silently — but it is a
BSE symbol in an active daily writer's symbol list and should be removed
explicitly rather than left to fail.

---

## 11. Canonical Timestamp Audit

| Writer | Emits | Verdict |
|---|---|---|
| `sync_all_nse_candles` (`kite_sync_candles`) | 03:45 | **PASS** |
| `backfill_hub_1d_candles` | 03:45 | **PASS** |
| `refresh_priority_1d_candles` | 03:45 | **PASS** |
| `run_india_price_crawl` | 03:45 | **PASS** |
| `api/india.py` on-demand | 03:45 | **PASS** |
| `upstox_historical` | 03:45 | **PASS** |
| **`sync_regime_daily_candles_kite`** | **00:00** | **FAIL** |
| `candle_resampler` | 5m/15m/1h only | N/A |
| `scripts/backfill_1d_candles.py` | raw INSERT | disabled at import |

**Can `00:00` / `18:30` / arbitrary timestamps still be written?**

- For **NSE equities** — no. The guard rejects them at the choke point. Proven
  operationally: the legacy 18:30 writer's last successful write was
  **2026-09-03 12:13**, and it has written **0 rows in the last 3 days** despite
  the sessions on 09-04 and 09-07. The guard silently stopped it.
- For **indices and ETFs** — **yes**, freely. This is blocker 1.

Equity conventions in the last 7 days: 03:45 → 29,123 rows; 18:30 → 5,987 (all
before the guard landed); 00:00 → 41 (all four regime symbols only).

---

## 12. Writer Collision Analysis

Symbols currently written by more than one active daily writer:

| Symbol | Writer A | Writer B | Convention A | Convention B | Basis A | Basis B | Risk |
|---|---|---|---|---|---|---|---|
| **NIFTYBEES.NS** | `sync_regime_daily_candles_kite` (5 min) | `sync_all_nse_candles` (10:00 UTC) | **00:00** | **03:45** | Upstox raw | Upstox raw | **Two rows per session.** No last-writer-wins (different timestamps never conflict) and no delete risk since 2D.2. Reader-resolved. **MEDIUM** |
| `^NSEI`, `^NSEBANK` | `sync_regime_daily_candles_kite` only | — | 00:00 | — | Upstox raw | — | Single writer, **wrong convention**. **HIGH** |
| `^BSESN` | `sync_regime_daily_candles_kite` only | — | 00:00 | — | — | — | Inactive since 2026-08-28. **LOW** |
| all other NSE equities | canonical writers only | — | 03:45 | — | Upstox raw | — | **NONE** |

The collision is now benign in the sense that matters most — **no writer can
delete or overwrite another's rows** (§3), and the remediated readers collapse
duplicate sessions and pick a single basis. It remains a defect because it
doubles storage, keeps the ambiguity alive for any future reader written without
the shared helper, and — for the two indices — leaves a trading input on the
legacy convention.

---

## 13. Test Results

No tests were modified in this phase.

**Targeted suites** (Step 2B, 2C, 2C.1, 2C.2, 2D.0, 2D.0.1, 2D.2, trading
engine, candle-pipeline integrity):

```
361 passed, 2 warnings in 20.42s          0 failures, 0 errors
```

**Full suite:**

| | failures/errors | passed |
|---|---|---|
| Baseline (2D.2 close) | 32 | 2,532 |
| Current | **32** | **2,532** |
| **New failures** | **0** | |
| **New errors** | **0** | |

The 32 are pre-existing and unrelated (`test_upstox_isin` signature drift,
`test_entry_confirmation`, `test_alert_router`, `test_alert_reports`,
`test_pre_event_gap_phase3/5_5/6/foundation`,
`test_trade_simulator_confirmation_lost`).

---

## 14. Database Mutation Check

Verified without mutating, against the numbers recorded at the close of 2D.2:

| metric | 2D.2 close | now | delta |
|---|---|---|---|
| 1d total | 5,891,897 | 5,891,897 | **0** |
| 1d @ 00:00 | 4,854,453 | 4,854,453 | **0** |
| 1d @ 03:45 | 31,985 | 31,985 | **0** |
| 1d @ 18:30 | 1,002,584 | 1,002,584 | **0** |
| frozen-window checksum | n=5,891,862 sum=4954460247.82 | n=5,891,862 sum=**4954460247.82** | **exact match** |

```
rows inserted = 0    rows updated = 0
rows deleted  = 0    rows migrated = 0
```

---

## 15. Migration Readiness

# RED

| # | Condition | State |
|---|---|---|
| 1 | Active writer can delete canonical candles | **NO** — cleared (§3) |
| 2 | Competing writers use different price bases | **NO** — all active writers are Upstox raw (§12) |
| 3 | Active writer can create non-canonical NSE timestamps | **YES** — writer #5, indices/ETFs (§7, §11) |
| 4 | Stale runtime worker can execute affected logic | **YES** — `trade_queue`, `exit_queue` (§5, §6) |
| 5 | Index canonical writer unsafe | **YES** — FAIL (§7) |
| 6 | 00:00 price basis unidentified | **NO** — identified (§8), but unusable (§9) |
| 7 | Active reader has future-session leakage | **NO** — cleared in 2D.2, re-verified (§13) |
| 8 | Prediction/backtest path unsafe | **PARTIAL** — readers fixed; the 00:00 basis makes the pre-May-2026 dataset unusable without normalization (§9) |

Three conditions hold outright and one partially. **Migration remains blocked.**

---

## 16. Remaining Blockers

### Blocker 1 — Index/ETF daily writer emits `00:00`
- **Severity:** CRITICAL
- **Where:** `crawler/india_price_feed.py::sync_regime_daily_candles_kite:696`;
  exemption at `utils/candle_contract.py::validate_canonical_daily_equity_candle`
- **Evidence:** ran 2026-09-07 07:18, produced 00:00; `^NSEI`/`^NSEBANK` hold
  1,281 rows each, **0** canonical. The guard returns `(True, None)` for every
  non-`NSE_EQUITY` instrument.
- **Next action:** route these four symbols through `fetch_nse_candles` so the
  2D.0.1 `NSE_INDEX` delegation applies, and extend the contract to govern
  `NSE_INDEX` and `ETF_OR_INAV`. Drop `^BSESN`. **Do this before any migration**
  — migrating while a writer still emits the legacy convention re-creates the
  problem the next day.

### Blocker 2 — Six stale production workers (RUNTIME BLOCKER)
- **Severity:** CRITICAL
- **Where:** PIDs 3523760, 3523771 (`exit_queue`); 3523764, 3523783
  (`trade_queue`); 3523763, 3974087 (`scan_queue`); plus uvicorn 1975541 (MEDIUM)
- **Evidence:** started Aug 26/27, **28 commits** behind `99cdf7c`; not wrapped
  in `watchmedo`; `.env` modified 2026-09-07 08:16 after they cached settings.
  `trade_queue` runs the pre-remediation market regime (51% zero returns) and
  originates trades; `exit_queue` runs the pre-`basis="raw"` corporate-action
  guard, which rewrites open-position quantities.
- **Next action:** `systemctl --user restart autotrade-celery-worker` plus the
  queue units and uvicorn, in a maintenance window with open positions checked
  first. **Not performed — this phase forbids restarts.** Longer term, wrap the
  queue workers in `watchmedo` or add `ExecReload`, so runtime and HEAD cannot
  silently diverge by 12 days again.

### Blocker 3 — Regime writer cannot refresh its own bars (2D.2 regression)
- **Severity:** HIGH
- **Where:** `crawler/price_feed.py:441` (`on_conflict_do_nothing`) combined with
  the 2D.2 delete removal in `india_price_feed.py`
- **Evidence:** today's four regime bars all created 07:18 UTC (12:48 IST,
  mid-session) and unchanged at 08:25 UTC despite a 5-minute schedule.
- **Next action:** scope a refresh to the writer's own rows — `ON CONFLICT DO
  UPDATE` for this writer, or a DELETE narrowed to its own convention. Fix
  **after** blocker 1, so the refresh applies to canonical rows.

### Blocker 4 — `00:00` basis unusable as a training source
- **Severity:** HIGH
- **Where:** 4,854,453 rows, 2016-01-01 .. 2026-06-22
- **Evidence:** 100 of 108 divergent pairs carry identical volume (price-only
  adjustment); INFY ×0.9788 constant to a 2026-06-10 ex-date; 8 pairs on
  06-15/06-16/06-22 contaminated; no `corporate_actions` table and no provenance
  column, so factors cannot be recovered internally.
- **Next action:** decide the training dataset policy before migrating anything
  — either source an external corporate-action feed and normalize, or restrict
  the dataset to 03:45 + resolved 18:30 (2021-06-23 onward) and accept the
  shorter history. **Do not migrate 00:00 into the canonical series.**

---

**No data was migrated, deleted, updated, rewritten, backfilled or inserted. No
worker was restarted. No process was killed. No production code was changed.**
