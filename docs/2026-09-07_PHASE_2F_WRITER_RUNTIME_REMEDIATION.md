# PHASE 2F — WRITER + RUNTIME REMEDIATION REPORT

**Date:** 2026-09-07 · **Type:** writer remediation + controlled runtime restart
**Historical rows migrated / rewritten / deleted / backfilled: 0** (verified, §12)

---

## 1. Executive Verdict

# YELLOW

The two CRITICAL blockers from Phase 2E are closed in code and in runtime:

* the index/ETF writer now produces canonical `03:45` bars and is held to a
  contract that can actually reject it (§2);
* all six stale workers plus uvicorn are restarted onto current code with
  current configuration, verified by PID, start time and loaded-module
  inspection (§7–§10).

The corrected writer then **executed naturally at 15:02 IST** and produced
canonical `03:45` bars for `^NSEI`, `^NSEBANK` and `NIFTYBEES.NS`, with **zero**
new legacy rows. Phase 2E's index-writer FAIL is closed (§11).

It is YELLOW rather than GREEN because of what that same run exposed: the
writer ran **once**, not every five minutes, so its current-session refresh never
re-fired and today's index bar is still a mid-session snapshot rather than the
settled close (§3a). The cause is a stuck default-queue backlog — 86 orphaned
messages, unchanged for two and a half hours — which is a new blocker (§14).

---

## 2. Index/ETF Writer Changes

### Source path (Part A)

`sync_regime_daily_candles_kite` called `get_kite_historical()` directly and then
re-anchored every bar to midnight IST by hand:

```python
ist_date = (ts + timedelta(hours=5, minutes=30)).date()
c["timestamp"] = datetime(ist_date.year, ist_date.month, ist_date.day)   # 00:00
```

That bypassed `fetch_nse_candles()`, where the Step 2D.0.1 index delegation
lives, so the routing fix never applied to it. It now calls
`get_upstox_candles_for_range()` — the same function that delegation uses — and
does **no** re-anchoring of its own. One implementation of "an Upstox daily
label becomes a timestamp", which is the invariant this whole line of work
protects.

Verified read-only, before any write:

| symbol | rows | timestamps | latest | close |
|---|---|---|---|---|
| `NIFTYBEES.NS` | 11 | `{03:45}` | 2026-09-07 | 271.52 |
| `^NSEI` | 11 | `{03:45}` | 2026-09-07 | 23783.05 |
| `^NSEBANK` | 11 | `{03:45}` | 2026-09-07 | 57144.95 |

The values are live: `^NSEI` 23783.05 against the frozen 00:00 row's 23754.10.

### Contract (Part B)

The guard governed `NSE_EQUITY` and returned `(True, None)` for everything else.
Each class now declares a policy, and a class with no entry raises rather than
defaulting to allow:

| Class | Policy | Model-eligible | Rationale |
|---|---|---|---|
| `NSE_EQUITY` | **CANONICAL** | yes | — |
| `NSE_INDEX` (`^NSEI`, `^NSEBANK`) | **CANONICAL** | yes | Upstox serves them under `NSE_INDEX` |
| `ETF_OR_INAV` (`NIFTYBEES.NS`) | **CANONICAL** | yes | Upstox key exists; already writes 03:45 elsewhere |
| `NON_EQUITY_SERIES` (`-BE`/`-SM`/`-BZ`/`-GS`) | **LEGACY_EXEMPT** | **no** | see below |
| `OTHER_INDEX` (`^BSESN`) | **EXCLUDED** | no | BSE index — rejected outright |
| `NON_NSE` (`.BO`) | **EXCLUDED** | no | BSE cash — rejected outright |
| `UNKNOWN` | **EXCLUDED** | no | unknown symbol → refuse |

An unknown timestamp is rejected for every CANONICAL class; `EXCLUDED` classes
are rejected even when the timestamp *is* canonical.

**The `NON_EQUITY_SERIES` exemption is a measured constraint, not an oversight —
and correcting a mistake I made mid-phase.** I first set that class to carry the
contract. Live logs then showed `fetch_nse_candles` returning
`SIMBHALS-BZ.NS ... latest=2026-09-03 18:30`, which the new contract would have
rejected. Checking the instrument master explained why:

```
SIMBHALS-BZ.NS    key=None        NIFTYBEES.NS   key=NSE_EQ|INF204KB14I2
AAREYDRUGS-BE.NS  key=None        ABSLBANETF.NS  key=NSE_EQ|INF209KB17D5
AAKAAR-SM.NS      key=None        RELIANCE.NS    key=NSE_EQ|INE002A01018
719GS2060-GS.NS   key=None        ^NSEI          key=NSE_INDEX|Nifty 50
```

Upstox publishes no key for any series form, so the canonical fetch returns
nothing for them. Enforcing the contract would not have moved them to 03:45 — it
would have silently stopped daily bars for ~1,163 symbols. They stay on the
legacy path by **explicit declaration** and are kept out of the model dataset
instead, which is where the modelling risk actually sits.

### A classifier defect found along the way

`base[:1].isdigit()` classified nine real NSE equities — `3MINDIA.NS`,
`5PAISA.NS`, `63MOONS.NS`, `360ONE.NS`, `20MICRONS.NS`, `21STCENMGM.NS`,
`3BBLACKBIO.NS`, `3IINFOLTD.NS`, `3PLAND.NS` — as `NON_EQUITY_SERIES`, exempting
them from the daily contract entirely. A leading digit is not a series marker;
the `-XX` suffix is, and it still separates `3IINFOLTD.NS` from
`3IINFOLTD-BE.NS`. Rule removed.

---

## 3. Regime Writer Refresh Semantics (Part C/D)

`save_candles_to_db` used `ON CONFLICT DO NOTHING` — insert-if-absent, never
correcting an existing row. Combined with the Step 2D.2 delete removal, that
left the 5-minute regime writer unable to update its own current-session bar.

`save_candles_to_db(..., refresh_current_session=True)` now upgrades **only**
rows whose session date is today to `ON CONFLICT DO UPDATE` on
`uq_candle_bar (symbol, timeframe, timestamp)`, setting `open/high/low/close/
volume`. The two groups are split *before* the statement is built, so the
historical group never sees a `DO UPDATE`.

Rows the writer may update: **its own symbols, its own canonical timestamp, the
current NSE session only.** Everything else is insert-only. The old broad 15-day
DELETE is not restored — that is what destroyed other writers' canonical rows.

Pinned by tests: in-place refresh (one row, latest values), default stays
insert-only, a prior session is never rewritten, another symbol's row is
untouched, and history survives a current-session write (10 rows → 11).

### 3a. The refresh mechanism is correct; the schedule does not exercise it

Measured after market close (17:00 IST) against the source's settled bar:

| symbol | stored close | settled close | stored low | settled low |
|---|---|---|---|---|
| `^NSEI` | 23751.75 | **23779.15** | 23738.70 | 23737.90 |
| `^NSEBANK` | 57066.40 | **57088.30** | 57002.95 | 57002.95 |
| `NIFTYBEES.NS` | 271.38 | **271.21** | 270.90 | 270.90 |

The stored bars are the 15:02 IST snapshot. The writer never ran again — not
before the 15:30 close, not after — so the refresh had nothing to fire on, and
today's regime bar carries a mid-session close roughly 0.12% off the settled
one.

The mechanism itself is proven by test (`test_current_session_row_is_refreshed_
in_place`): a second write to the same session updates the row in place rather
than adding one. What is missing is cadence, and the cause is §14 blocker 1 —
`sync_regime_daily_candles_kite` runs as step 1b of `tasks.india_price_scan`,
which is starved behind a stuck queue. It executed once in a full session
instead of roughly seventy times.

---

## 4. BSE Regime Path (Part F)

`^BSESN` removed from `_REGIME_DAILY_SYMBOLS`, now
`("NIFTYBEES.NS", "^NSEI", "^NSEBANK")`. It contributed nothing after
2026-08-28 — the NSE-only work of `401cb06` left it failing silently every five
minutes — but it was a BSE path inside an NSE-only pipeline.

Defence in depth: even if it returns to a symbol list, `OTHER_INDEX` is
`EXCLUDED`, so the choke point refuses it regardless of timestamp.

**Its 35 historical daily rows (5,560 across all timeframes) are untouched.**

---

## 5. Writer Collision Results (Part E)

| Symbol | Active writers | Timestamp | Source | Conflict behaviour |
|---|---|---|---|---|
| `NIFTYBEES.NS` | regime sync (5 min) + `sync_all_nse_candles` (10:00 UTC) | both **03:45** | both Upstox raw | same key → same row; current session updates in place, history insert-only |
| `^NSEI`, `^NSEBANK` | regime sync only | **03:45** | Upstox raw | as above |
| all NSE equities | canonical writers | **03:45** | Upstox raw | insert-only |
| `-BE`/`-SM`/`-BZ`/`-GS` | legacy path | 18:30 | Upstox | insert-only, declared exempt, not model-eligible |
| `^BSESN`, `.BO` | **none** | — | — | rejected at the choke point |

The NIFTYBEES double-write is now benign: both writers agree on convention and
basis, so they address the *same row* rather than creating a second one. No
active writer can produce a new `00:00`, `18:30` or arbitrary-time bar for any
canonical class.

---

## 6. Test Results (Part G/O)

New file `tests/test_step2f_regime_writer.py` — **36 tests**, covering all ten
required cases: ^NSEI/^NSEBANK/NIFTYBEES → 03:45; ^BSESN excluded; repeated
current-session writes update one row; history not deleted; another symbol's
rows untouched; no 15-day DELETE (AST-asserted, because the function's comments
legitimately name it); unknown timestamps rejected; mixed legacy history does
not cause the writer to recreate a legacy convention.

`tests/test_step2c2_canonical_daily_writer.py` extended to **57**; two tests
there previously asserted the *opposite* of the new policy and were updated
deliberately, with the reason recorded in the test.

| | failures/errors | passed |
|---|---|---|
| Baseline (2E close) | 32 | 2,532 |
| Current | **32** | **2,587** |
| **New failures** | **0** | |
| **New errors** | **0** | |

Targeted suites (2B, 2C, 2C.1, 2C.2, 2D.0, 2D.0.1, 2D.2, 2F, trading engine,
candle pipeline): **410 passed, 0 failed** before the restart; the full suite was
run again after the final code change with the same result.

The 32 are pre-existing and unrelated (`test_upstox_isin`,
`test_entry_confirmation`, `test_alert_router`, `test_alert_reports`,
`test_pre_event_gap_*`, `test_trade_simulator_confirmation_lost`).

---

## 7. Runtime Before Restart (Part H)

| Process | PID | Started | Age | Queue depth | Restart needed |
|---|---|---|---|---|---|
| exit worker | 3523760, 3523771 | Aug 26 21:22 | 28 commits | 0 | YES |
| trade worker | 3523764, 3523783 | Aug 26 21:22 | 28 commits | 0 | YES |
| scan worker | 3523763; 3974087 | Aug 26 / Aug 27 | 28 / 27 | 0 | YES |
| uvicorn | 1975541 | Sep 7 12:03 | pre-2D.2 | — | YES |

Prerequisites checked before touching anything:

* **Dedicated systemd units exist** for each queue, so graceful restart was
  available — no process was killed.
* **`exit_queue`, `scan_queue`, `trade_queue` depths were all 0** — nothing
  in flight to lose.
* **One open position**: `GKENERGY.NS`, entry ₹139.52, SL ₹122.75, opened
  2026-08-27; latest close ₹128.96 → **~4.8% above the stop**, in PAPER mode.
* **The full test suite was run first** so the restart deployed validated code.

Judged acceptable: the exit worker was already running the pre-`basis="raw"`
corporate-action guard, which can rewrite a position's quantity on a false split
detection. Leaving it running was the larger risk.

---

## 8. Runtime Restart Actions (Part J)

`systemctl --user restart` — graceful, no `kill`. Order chosen lowest-risk first
so the mechanism was proven before touching the position-protecting worker:

1. `autotrade-celery-scan-worker`
2. `autotrade-celery-trade-worker`
3. `autotrade-celery-exit-worker`
4. `autotrade-uvicorn`

A second pass over all four followed the final code change at 14:33, because
these units are not `watchmedo`-wrapped and would otherwise have kept the 14:24
code.

---

## 9. Runtime After Restart (Part J verification)

| Process | Old PID | New PID | State |
|---|---|---|---|
| scan worker | 3523763 → 2084013 | **2091725** | active |
| trade worker | 3523764 → 2084100 | **2091767** | active |
| exit worker | 3523760 → 2084188 | **2091813** | active |
| uvicorn | 1975541 → 2084409 | **2091982** | active |

* All seven Aug-26/27 PIDs confirmed **gone**.
* Every source file predates every worker start (`candle_contract.py` and
  `india_price_feed.py` 14:33:31; workers 14:39–14:40).
* Broker: **4 workers responding** — `celery@`, `exit@`, `scan@`, `trade@`.
* Queue depths after restart: `exit=0 scan=0 trade=0` — no backlog corruption.
* uvicorn serving `/api/v1/settings/`.
* Exit loop confirmed alive on the new code:
  `[fast_sl] alive — watching 2 positions` at 14:40:01, 14:41:04, 14:42:08.

---

## 10. Current Configuration Verification (Part K)

```
PAPER_MODE                True
AGENT_PAPER_MODE          True
ZERODHA_ENABLED           False
LIVE_CONFIDENCE_THRESHOLD 70.0
TECHNICAL origination     getattr(settings,"TECHNICAL_ORIGINATION_BLOCKED", True)  → blocked by default
```

Loaded-module inspection on the restarted code:

| Path | Result |
|---|---|
| `market_regime` → `session_close_series` | ✅ |
| `intelligence_hub` → `session_close_series` | ✅ |
| `replay._close_near` → `close_for_session` | ✅ |
| `replay._candles_between` → `session_bars` | ✅ |
| corporate-action guard → `basis="raw"` | ✅ |
| regime writer → `get_upstox_candles_for_range` | ✅ |
| regime writer → no `delete` | ✅ |
| regime writer → `refresh_current_session` | ✅ |
| regime universe | `('NIFTYBEES.NS', '^NSEI', '^NSEBANK')` |

**Non-mutating trade_queue regime trace** (no order placed):

```
state=WEAK_BEAR  score=-59.9  can_buy=False  size_mult=0.0
roc_20d=-3.23%   ema_levels=0/4
regime series: 220 sessions, conventions={'legacy_0000': 220}, latest=2026-09-07
```

220 distinct sessions on a single basis — the reader is behaving. The basis is
still `legacy_0000` because NIFTYBEES's canonical rows only reach 2026-08-21;
they resume once the corrected writer runs (§11).

---

## 11. Index 03:45 Natural-Run Verification (Part M)

# VERIFIED — canonical rows produced, no new legacy writes

The corrected writer ran on its own at **09:32:56 UTC (15:02 IST)**, 38 minutes
after the final restart. Not triggered manually; no row inserted by hand.

| symbol | 03:45 before | 03:45 after | latest | written |
|---|---|---|---|---|
| `^NSEI` | **0** | **11** | 2026-09-07 | 09:32:56 UTC |
| `^NSEBANK` | **0** | **11** | 2026-09-07 | 09:32:56 UTC |
| `NIFTYBEES.NS` | 75 | **86** (+11) | 2026-09-07 | 09:32:56 UTC |

Each covers 2026-08-24 .. 2026-09-07 — the writer's 15-day window, now landing
on the canonical timestamp.

**Success criterion — NO NEW LEGACY INDEX WRITES:**

```
new 00:00 or 18:30 daily rows since the fix landed:  0
00:00 total   4,854,453 -> 4,854,453   (unchanged)
18:30 total   1,002,584 -> 1,002,584   (unchanged)
03:45 total      31,985 ->    32,050   (+65, all canonical)
```

Existing 00:00 rows were left in place, exactly as instructed.

The canonical bar is also a better bar: today's `^NSEI` canonical row has a low
of 23738.70 against the legacy 00:00 row's 23751.10 — it captured more of the
session.

## 12. Database Before/After (Part L/P)

| metric | before restart | after | delta |
|---|---|---|---|
| 1d total | 5,891,897 | 5,891,962 | **+65** (all canonical) |
| 1d @ 00:00 | 4,854,453 | 4,854,453 | **0** |
| 1d @ 03:45 | 31,985 | **32,050** | **+65** |
| 1d @ 18:30 | 1,002,584 | 1,002,584 | **0** |
| 1d @ other (11 dead conventions) | 2,875 | 2,875 | **0** |
| frozen-window checksum | n=5,891,862 sum=4954460247.82 | n=5,891,862 sum=**4954460247.82** | **exact match** |

```
historical rows inserted = 0     historical rows updated  = 0
historical rows deleted  = 0     historical rows migrated = 0
```

**Distinguishing the two kinds of write, explicitly:**

* **HISTORICAL MUTATION — none.** The checksum over every row created before the
  phase began is byte-identical, and both legacy conventions hold exactly their
  prior counts. No row that existed at the start of this phase was altered.
* **INTENDED NEW WRITES — 65 canonical rows.** 33 from the corrected regime
  writer at 09:32 UTC (11 sessions × 3 symbols, dates 2026-08-24..09-07, none of
  which previously existed at 03:45), and 32 from the normal 10:00 UTC equity
  writer for today's session. These are inserts of absent bars, not rewrites.
* **INTENDED CURRENT-SESSION REFRESH — 0 fired.** The writer ran once, so no bar
  was updated in place (§3a).

Test writes went to `autotrade_test` only, under sentinel symbols
(`ZZTEST_*`), cleaned up by fixture. `^BSESN` history preserved: 5,560 rows.

## 13. 00:00 Historical Basis Status (Part N)

Recorded, **not modified**:

| property | value |
|---|---|
| rows | 4,854,453 across 3,357 symbols |
| range | 2016-01-01 .. 2026-09-07 |
| range excluding regime symbols | 2016-01-01 .. **2026-07-06** (4,849,212 rows) |
| source | yfinance (legacy) |
| adjustment | retroactive **price-only**; volume unadjusted — 100 of 108 divergent pairs carry byte-identical volume |
| worked example | INFY ×0.9788 constant to a 2026-06-10 ex-date, then ×1.0000 |
| contaminated dates | 2026-06-15 (2 symbols), 2026-06-16 (2), 2026-06-22 (4) — price *and* volume differ |
| corporate-action dependency | factors unrecoverable internally: no `corporate_actions` table, no provenance column on `candles` |

**Policy unchanged: 00:00 legacy → EXCLUDED FROM MODEL DATASET** until a
separate normalization project is approved. Enforced in code by
`MODEL_ELIGIBLE_CLASSES` / `is_model_eligible()` at the class level, and by the
reader's single-basis rule at the series level.

---

## 14. Remaining Blockers

### Blocker 1 — the default queue is not draining (NEW, discovered this phase)
- **Severity:** HIGH
- **Evidence:** `celery` queue depth static at **86** across 14:27 → 14:42
  (five measurements). Contents: **33 × `tasks.fast_sl_check`**,
  10 × `refresh_live_prices`, 5 × `price_cache.refresh_price_cache`,
  5 × `market_shock_guard`, **3 × `tasks.india_trade_loop`**,
  1 × `tactical_tasks.run_tactical_intraday`.
- **Why it matters twice over:** (a) `fast_sl_check`, `india_trade_loop` and the
  tactical scans are *routed* to dedicated queues, so these 86 are orphaned
  messages enqueued under the old routing — they will only ever be drained by
  the default worker, competing with it; (b) `india_price_scan` is starved
  behind them, which is why §11 is PENDING. This is the same failure shape as
  the 2026-08-21 incident recorded in `india_tasks.py:1459` — "the dispatches
  expired in a saturated queue… all four services showed healthy".
- **Measured consequence:** the regime writer executed **once** in a full
  trading session instead of roughly seventy times, so today's `^NSEI` daily bar
  is stuck at its 15:02 snapshot — a close of 23751.75 against a settled
  23779.15 (§3a). The regime gate reads exactly this series.
- **Next action:** purge the orphaned messages (a Redis queue operation, not a
  database change) and confirm `india_price_scan` completes on cadence; then
  re-verify §3a. Not done here — out of this phase's remit.

### Blocker 2 — RESOLVED
Phase 2E's "index writer still unsafe" is closed: both indices produced
canonical `03:45` rows on a natural run, and no new legacy row was written (§11).
What remains of it is the cadence problem, tracked as blocker 1.

### Blocker 3 — 00:00 basis remains unusable for modelling
- **Severity:** HIGH (unchanged from 2E)
- **Next action:** a separate normalization decision — external corporate-action
  feed, or restrict the dataset to 03:45 + resolved 18:30. **Do not migrate.**

### Blocker 4 — queue workers still lack hot-reload
- **Severity:** LOW–MEDIUM
- **Evidence:** the exit/scan/trade units are not `watchmedo`-wrapped, which is
  how they drifted 28 commits. This phase needed two manual restart passes for
  the same reason.
- **Next action:** wrap them as the default worker is, or add `ExecReload`.

---

## 15. Historical Migration Readiness

# YELLOW

| # | Gate condition | State |
|---|---|---|
| 1 | Index writer produces canonical 03:45 | **YES** — verified on a natural run (§11) |
| 2 | No active writer produces new legacy index/ETF timestamps | **YES** — 0 new legacy rows (§11) |
| 3 | Regime writer refreshes current-session bars correctly | **PARTIAL** — mechanism correct and tested, but the schedule fires it once a session (§3a) |
| 4 | No destructive candle DELETE remains | **YES** |
| 5 | `trade_queue` runs current code | **YES** (PID 2091767) |
| 6 | `exit_queue` runs current code | **YES** (PID 2091813, loop alive) |
| 7 | Affected workers use current configuration | **YES** (§10) |
| 8 | `market_regime` safe | **YES** |
| 9 | `intelligence_hub` safe | **YES** |
| 10 | `replay` safe | **YES** |
| 11 | 00:00 excluded from model dataset | **YES** (enforced in code) |
| 12 | No future-session leakage in prediction/backtest paths | **YES** |
| 13 | Integration tests pass | **YES** — 0 new failures |
| 14 | No unintended historical DB mutations | **YES** — 0 (§12) |

Thirteen of fourteen met outright. Condition 3 is partial: the refresh is
correct in code and in test, but the queue backlog means it fires roughly once
per session, leaving the regime symbols' current-day bar mid-session stale.

Migration stays **BLOCKED**. Even once 1 and 2 clear, condition 11 is a *policy*
exclusion, not a resolution: the 00:00 basis question (§13) must be settled on
its own terms before any historical migration is designed.

---

# NO HISTORICAL DATA WAS MIGRATED.

No historical candle was migrated, rewritten, normalized, deleted, purged or
backfilled. No index row was manually inserted. No process was killed. The
`^BSESN`, 18:30 and 00:00 histories are all intact and byte-identical, confirmed
by an unchanged row count and checksum (§12).
