# PHASE 2F — INDEX PIPELINE REMEDIATION REPORT

**Date:** 2026-09-07 · **Migration gate: NO-GO** (unchanged)
**Historical rows migrated / normalized / deleted / backfilled: 0** (verified, §8)
**`.BO` rows deleted: 0** · **00:00 rows deleted: 0** — forensic evidence preserved

---

## 0. First: three of the briefed premises did not hold

The brief carried eleven "CONFIRMED" findings from two Gemini audits. Nine
reproduce. Three do not, and acting on them would have caused the exact harm
section B warns against — a duplicate production writer.

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 8 | `^NSEI` / `^NSEBANK` have **no active daily writer** | **REFUTED** | They received canonical `03:45` rows at 09:32:56 UTC today from `sync_regime_daily_candles_kite` |
| 10 | The `sync_regime_daily_candles_kite` path **was removed from the Celery schedule** | **REFUTED** | Still called at `india_price_feed.py:1281`; `india_price_scan` still scheduled every 300s (`celery_app.py:158`) |
| 11 | Therefore index data **will become stale** | **DOES NOT FOLLOW** | The premise is false; the real defect was cadence, not absence |
| 9 | Indices are excluded from the NSE equity sync | **CONFIRMED** | `settings.nse_symbols + nse_mid_symbols` = **33** symbols, no index. `NIFTYBEES.NS` *is* in it |
| 1,2 | No look-ahead in `_aligned_closes` / resolver | **CONFIRMED** | Re-proved on live data, §4 |
| 3,4,5,6,7 | Restart time, no new 00:00 writes, `.BO` residual, NIFTYBEES writer | **CONFIRMED** | §8, §2 |

**So I did not create a new index writer.** The writer existed, was already
canonical, and was correct. Its defect was *when* it ran: it executed as step 1b
of `run_india_price_crawl`, behind that task's ~1,400-symbol crawl, on a default
queue holding a static 86-message backlog. It fired **once** in a full trading
session. The consequence was measurable — today's `^NSEI` bar was frozen at its
15:02 IST snapshot, close 23751.75 against a settled 23779.15, and the regime
gate reads that series.

The remediation is therefore scheduling, not construction: **the same writer,
given its own beat entry on an unstarved queue, and removed from the crawl so
exactly one scheduled path exists.**

---

## 1. Code Changes

| File | Change |
|---|---|
| `tasks/india_tasks.py` | **new** `tasks.sync_regime_daily_candles` — a thin task that calls the existing `sync_regime_daily_candles_kite()`. No writer logic duplicated. |
| `tasks/celery_app.py` | beat entry `regime-daily-candles-every-5min` (300s) + route to `scan_queue` |
| `crawler/india_price_feed.py` | step 1b call removed → one scheduled path, not two; corrected a log line still claiming "via Kite" |
| `engine/portfolio_analytics.py` | `get_nifty_return` moved onto the shared session reader + a minimum-history guard (§9) |
| `tests/test_step2f_index_pipeline.py` | **new**, 35 tests in three tiers |

No change to news/fundamentals/corporate-action PIT logic, per the brief.

---

## 2. Writer Inventory — Before / After

| Symbol | Before | After |
|---|---|---|
| `^NSEI` | regime writer, buried in `india_price_scan` step 1b, default queue (backlogged) | **`tasks.sync_regime_daily_candles`**, 300s, `scan_queue` |
| `^NSEBANK` | same | same |
| `NIFTYBEES.NS` | same, **plus** `sync_all_nse_candles` (10:00 UTC, equity universe) | same — both canonical, same timestamp, same row |
| `^BSESN` | in `_REGIME_DAILY_SYMBOLS` | **removed** (Phase 2F earlier); `OTHER_INDEX` is `EXCLUDED` at the contract, so it cannot be written even if re-added |
| all NSE equities | `sync_all_nse_candles` + `india_price_scan` | unchanged |

**Section B — NIFTYBEES was not given a second writer.** It already had two
schedules (regime + equity sync). Both now emit the identical canonical
`03:45` bar for the same session, so they address the *same row* under
`uq_candle_bar (symbol, timeframe, timestamp)` rather than creating a second.
Verified: source Upstox, timestamp 03:45, contract enforced, refresh enabled,
and **no competing 00:00 writer** — 0 new 00:00 rows since the fix.

---

## 3. Index Pipeline

```
Upstox  get_upstox_candles_for_range()          ← single shared fetch
   ↓    _to_naive_utc(daily=True)               ← single normalization
03:45 UTC canonical timestamp
   ↓    validate_canonical_daily_equity_candle  ← NSE_INDEX / ETF_OR_INAV = CANONICAL
save_candles_to_db(refresh_current_session=True)
   ↓    history insert-only · current session ON CONFLICT DO UPDATE
PostgreSQL candles
   ↓    engine/daily_series.py                  ← session resolution, single basis
market_regime · intelligence_hub · prediction features
```

No yfinance, no Kite: `get_kite_historical`, `yf` and `download` are all absent
from the writer's call graph (AST-asserted).

---

## 4. Timestamp Proof

**Natural scheduled run, 17:19:27 IST** — dispatched by beat, executed on
`scan_queue`, not triggered by hand:

```
Task tasks.sync_regime_daily_candles[f7c0eb1f…] received      17:19:27
[india_price_feed] regime daily candles refreshed — 3 rows    17:19:46
Task … succeeded in 8.94s: 3
```

Every regime symbol's current session now carries a canonical bar:

| symbol | timestamp | open | high | low | close |
|---|---|---|---|---|---|
| `^NSEI` | **03:45** | 23883.15 | 23890.00 | 23737.90 | 23779.15 |
| `^NSEBANK` | **03:45** | 57343.30 | 57426.85 | 57002.95 | 57088.30 |
| `NIFTYBEES.NS` | **03:45** | 273.19 | 273.78 | 270.90 | 271.21 |

**No new `00:00`, `18:30` or arbitrary-time rows** were created for these
symbols: the 00:00 counts are byte-identical (`^NSEI` 1,281, `^NSEBANK` 1,281,
`NIFTYBEES` 2,644) and their newest `created_at` is still 07:18 — the pre-fix
write.

Session-date correctness, on live rows (§L):

```
RELIANCE 2026-09-01 18:30  close 1313.10  -> session 2026-09-02   (legacy_1830)
RELIANCE 2026-09-02 03:45  close 1313.10  -> session 2026-09-02   (canonical)
RELIANCE 2026-09-02 18:30  close 1302.50  -> session 2026-09-03   (legacy_1830)
RELIANCE 2026-09-03 03:45  close 1302.50  -> session 2026-09-03   (canonical)
```

`_aligned_closes` returns session D's own close for 09-02 (1313.1), 09-03
(1302.5) and 09-04 (1322.0); sessions returning the *next* session's close:
**none**. Across 191 sessions and four symbols, adjacent-duplicate closes: **0**.

`^NSEI`, `^NSEBANK`, `NIFTYBEES` and `RELIANCE` all read back through
`daily_series` as unique, ordered, single-basis series.

---

## 5. Refresh Proof

The 17:19 run **updated the existing rows in place** rather than inserting:

| symbol | close before | close after | settled value (independently fetched) |
|---|---|---|---|
| `^NSEI` | 23751.75 | **23779.15** | 23779.15 ✓ |
| `^NSEBANK` | 57066.40 | **57088.30** | 57088.30 ✓ |
| `NIFTYBEES.NS` | 271.38 | **271.21** | 271.21 ✓ |

`NIFTYBEES` volume 5,305,627 → 5,921,896; `^NSEI` low 23738.70 → 23737.90.

**`created_at` did not change** — still `09:32:56` for all three. A new row would
carry a new `created_at`; an unchanged one proves `ON CONFLICT DO UPDATE` on the
existing record. The 03:45 row count also did not move (32,050 before and
after), confirming an update rather than an insert.

Historical immutability is preserved by construction: rows are partitioned by
session date *before* the statement is built, and only today's group is given a
`DO UPDATE`. Tested directly — a prior session written twice keeps its first
value; another symbol's row is untouched; 10 historical rows survive a
current-session write (→ 11).

---

## 6. Runtime Proof

**Audited before restarting anything** (section H). No duplicates: what looked
like four beat and three uvicorn processes resolved, via `systemctl show
MainPID` and process trees, to **one** genuine beat (a watchmedo child, already
reloaded at 17:10) and **one** uvicorn; `scan_queue`'s second PID is its normal
worker child.

The audit then produced a concrete, falsifiable reason to restart exactly one
service — beat was already dispatching the new task and the 14:39 scan worker
was rejecting it:

```
[17:14:27 ERROR/MainProcess] Received unregistered task of type
  'tasks.sync_regime_daily_candles'.  KeyError: 'tasks.sync_regime_daily_candles'
```

`registered()` confirmed it: `scan@` False, `exit@` False, `trade@` False.

**Restarted: the scan worker only** (queues empty, 2 open paper positions
untouched, market closed). Not the trade, exit or uvicorn services — the task
routes to `scan_queue` and nothing else needed new code.

| unit | MainPID | state | started |
|---|---|---|---|
| beat | 1975611 (child 2165305) | active | reloaded 17:10 |
| default worker | 1975610 | active | reloaded 17:10 |
| **scan worker** | **2175152** | active | **17:27** |
| trade worker | 2091767 | active | 14:39 |
| exit worker | 2091813 | active | 14:39 |
| uvicorn | 2091982 | active | 14:40 |
| news engine | 1975546 | active | reloaded 17:10 |

4 workers responding; `sync_regime_daily_candles` registered on `celery@` and
`scan@`. Queues: `exit=0 scan=0 trade=0`.
`PAPER_MODE=True · AGENT_PAPER_MODE=True · ZERODHA_ENABLED=False`.
Exit loop confirmed operational (`[fast_sl] alive — watching 2 positions`); it
stops logging after 15:30 by its own market-hours guard, which is expected.

Restart success was **not** inferred from start time alone — it is evidenced by
task registration, a successful natural execution, and the resulting row values.

---

## 7. Test Results

`tests/test_step2f_index_pipeline.py` — **35 tests in three explicitly labelled
tiers**, because "the helper is correct" and "production is safe" are different
claims:

* **UNIT** — contract per class, resolver on all three conventions, current-session
  close maps to its own session (cases 13, 14).
* **INTEGRATION** — the real `save_candles_to_db` path against a real database:
  canonical bar persists at 03:45 for each of the three symbols (cases 1–3); a
  00:00 bar and an 18:30 bar cannot become production rows (cases 4, 5); refresh
  updates one row (6); history immutable (7); duplicate execution idempotent and
  single-convention (8).
* **SCHEDULE / PRODUCTION-PATH** — the task is registered, beat schedules exactly
  one entry at 300s, it is routed off the backlogged default queue, it calls the
  one writer, the crawl no longer calls it, it reaches the contract without
  `enforce_contract=False`, Upstox is the only source, `^BSESN` is absent
  (case 9), and the indices provably have no other writer (cases 10, 11).

| | failures/errors | passed | skipped |
|---|---|---|---|
| Baseline (2E/2F close) | 32 | 2,532 | 11 |
| Current | **32** | **2,621** | 11 |
| **New failures** | **0** | | |
| **New errors** | **0** | | |

Targeted suites (2B, 2C, 2C.1, 2C.2, 2D.0, 2D.0.1, 2D.2, 2F ×2, trading engine,
candle pipeline): **448 passed, 0 failed**.

The 32 pre-existing failures are unrelated and unhidden: `test_upstox_isin` (7,
signature drift), `test_entry_confirmation` (6), `test_pre_event_gap_phase3` (5),
`test_trade_simulator_confirmation_lost` (5 errors), `test_alert_router` (4),
`test_alert_reports` (2), `test_pre_event_gap_phase6/5_5/foundation` (3).

---

## 8. DB Mutation Audit

| metric | before phase | after | delta |
|---|---|---|---|
| 1d total | 5,891,962 | 5,891,962 | **0** |
| 1d @ 00:00 | 4,854,453 | 4,854,453 | **0** |
| 1d @ 03:45 | 32,050 | 32,050 | **0** (the run *updated*, not inserted) |
| 1d @ 18:30 | 1,002,584 | 1,002,584 | **0** |
| `.BO` rows (all timeframes) | 5,082 | **5,082** | **0** |
| frozen-window checksum | n=5,891,862 sum=4954460247.82 | n=5,891,862 sum=**4954460247.82** | **exact match** |

```
historical rows deleted  = 0      historical .BO rows deleted   = 0
historical rows updated  = 0      historical 00:00 rows deleted = 0
historical rows migrated = 0      historical rows normalized    = 0
```

The **only** mutation this phase caused is the intended current-session refresh:
3 rows (`^NSEI`, `^NSEBANK`, `NIFTYBEES.NS`), today's session, canonical
timestamp, by the writer that owns them. No unrelated equity was touched.

Legacy rows are deliberately intact — indices still hold 11 sessions carrying
both a 00:00 and an 03:45 row, and NIFTYBEES 999 such sessions. That duplication
is *expected* under a no-migration rule and is resolved at read time by
`daily_series`'s single-basis selection, not by deleting anything.

---

## 9. Remaining Risks

### Risk 1 — index canonical history is only 11 sessions (NEW, caused by this phase)
- **Severity:** MEDIUM
- `^NSEI` / `^NSEBANK` now have 11 canonical sessions against 1,281 legacy 00:00
  ones. Because the reader picks one basis and raw now reaches the present, the
  indices read back as 11 sessions, not 1,281.
- **This broke a reader that had been safe by accident.** Phase 2E classified
  `portfolio_analytics.get_nifty_return` as *"SAFE in practice — latently
  fragile if a second convention ever appears."* Adding 03:45 index bars is
  exactly that event: its `LIMIT days + 1` **row** window began returning the
  newest sessions twice and differencing adjusted closes against raw ones.
  Fixed here by routing it through `session_close_series`.
- It then annualized 11 sessions to **−48.9%**, which would have flowed into
  Treynor and Jensen as a market return. It now **refuses below 60 sessions** and
  returns `None` rather than a fabricated figure.
- **Next action:** a canonical index backfill would close this — but that is
  historical data work and stays behind the migration gate. Until then, beta-
  and market-relative metrics are unavailable rather than wrong.

### Risk 2 — the default queue still holds 86 undrained messages
- **Severity:** HIGH (unchanged from 2E; this phase routed *around* it)
- Static at 86 for over three hours, containing 33 × `fast_sl_check`,
  3 × `india_trade_loop` and tactical tasks — all of which are *routed* to
  dedicated queues, so only the default worker will ever drain them.
- The index writer no longer depends on it, but `india_price_scan` and every
  other default-queue task still do.
- **Next action:** purge the orphaned messages (a Redis operation) and confirm
  `india_price_scan` runs on cadence.

### Risk 3 — the 00:00 basis remains unresolved
- **Severity:** HIGH (unchanged). Retroactive price-only adjustment, volume
  unadjusted, three contaminated dates, factors unrecoverable internally.
  **EXCLUDED FROM MODEL DATASET** by policy and by `MODEL_ELIGIBLE_CLASSES`.

### Risk 4 — queue workers have no hot-reload
- **Severity:** LOW–MEDIUM. The scan worker needed two manual restarts this
  phase for the same reason it drifted 28 commits earlier.

---

## 10. Migration Gate

# NO-GO

| condition | state |
|---|---|
| Index writer produces canonical 03:45 on a natural run | **YES** (§4) |
| No new legacy timestamps for active NSE regime symbols | **YES** (§4, §8) |
| Current-session refresh correct and scoped | **YES** (§5) |
| Historical immutability preserved | **YES** (§8) |
| One writer, one schedule, no duplicate path | **YES** (§2) |
| `^BSESN` out of the NSE regime universe | **YES** |
| Readers free of look-ahead | **YES** (§4) |
| Legacy 00:00/18:30 series safe to normalize or exclude | **NO — unresolved** |

The daily *pipeline* is now sound end to end. The migration gate stays **NO-GO**
because it turns on a different question — whether the legacy 00:00/18:30 series
can be safely normalized or must be excluded — and that requires the separate
historical-data audit, not this phase's evidence.

---

**NO HISTORICAL DATA WAS MIGRATED.** No historical candle was normalized,
deleted, purged or backfilled. The 5,082 `.BO` rows and all 4,854,453 legacy
00:00 rows are intact. No index row was inserted by hand. Forensic evidence is
preserved.
