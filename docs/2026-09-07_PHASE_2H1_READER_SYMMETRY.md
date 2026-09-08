# PHASE 2H.1 — READER/WRITER SYMMETRY FOR NSE SPECIAL SESSIONS

**Date:** 2026-09-07 · **Reader fix: DONE** · **Full backfill: STILL BLOCKED**
**Database rows written or modified by this task: 0**

---

## 1. Files changed

| File | Change |
|---|---|
| `utils/candle_contract.py` | `NSE_SPECIAL_SESSIONS` added — the single curated calendar |
| `engine/daily_series.py` | `_collapse_to_sessions` defaults `extra_open` to that calendar; module docstring corrected |
| `scripts/oneoff_upstox_backfill.py` | imports the calendar instead of holding a second copy |
| `tests/test_step2h1_reader_symmetry.py` | **new**, 47 tests |

Three files, one behavioural change. No consumer was edited, no trading logic
touched, no historical row modified.

---

## 2. Exact reader functions changed

Only one: **`engine/daily_series._collapse_to_sessions`**.

```python
if extra_open is None:
    extra_open = NSE_SPECIAL_SESSIONS
```

That single point covers the whole read path, because `session_closes`,
`close_for_session` and `session_bars` all resolve through it, and
`session_close_series` and `_aligned_closes` delegate to those. Every function
in the module **already accepted `extra_open`** — the plumbing was complete and
no caller had ever supplied a value, so it was permanently `None`.

Nine production consumers are fixed without being touched:

`market_regime` · `intelligence_hub` · `performance_engine._aligned_closes` ·
`replay._close_near` · `replay._candles_between` · `ml_predictor` ·
`portfolio_analytics.get_nifty_return` · `india_specific` · `corporate_actions`

`resolve_daily_session_date` and `validate_canonical_daily_equity_candle` were
**not** changed — their defaults remain `extra_open=None`, so no writer's
behaviour moved as a side effect of this fix.

---

## 3. Calendar source

The same curated list established for the writer in Step 2H, **moved into
`utils/candle_contract.py` so there is exactly one copy**. A test asserts the
reader and the backfill hold the *same object*, not merely equal contents —
two copies is precisely what produced this asymmetry.

Ten dates, each a documented exchange event: Diwali Muhurat ×4 (2016-10-30,
2019-10-27, 2020-11-14, 2023-11-12), Union Budget ×3 (2020-02-01, 2025-02-01,
2026-02-01), disaster-recovery ×3 (2024-01-20, 2024-03-02, 2024-05-18).

It is a `frozenset`, curated — **not** derived from candle data and **not**
inferred from what the API returns. Letting a price source assert its own
trading calendar would make the validation circular.

**It is not a "weekends are valid" rule.** Ordinary Saturdays and Sundays are
still refused, by both halves, and tests pin that.

---

## 4. Tests added

`tests/test_step2h1_reader_symmetry.py` — **47 tests**, mapped to the brief:

| req | coverage |
|---|---|
| **A** ordinary Saturday | 2026-09-05, 2026-09-06, 2025-07-12, 2024-06-30 rejected by writer *and* reader |
| **B** known special session | 2019-10-27 — the date the failing pilot first reported — accepted by both |
| **C** all ten agree | parametrised over the full calendar: writer accepts each, reader resolves each to itself as `canonical_0345` |
| **D** canonical timestamp | for every special session, `00:00` and `18:30` still rejected, `03:45` accepted |
| **E** reader returns them | real-DB test: every stored special-session row comes back with its exact close |
| **F** look-ahead | 09-02 → 1313.1, 09-03 → 1302.5, 09-04 → 1322.0 |
| **G** no regression | default (`extra_open=None`) still refuses at the contract level; series stay unique and ordered |
| **H** other readers | index/ETF/equity suites run separately (below) |

Plus: the calendar is immutable, is a single shared object, and every declared
date really is a weekend.

---

## 5. Tests passed / failed

| suite | result |
|---|---|
| `test_step2h1_reader_symmetry.py` | **43 passed, 4 skipped** |
| `test_oneoff_upstox_backfill.py` | **45 passed** |
| daily-series / regime / index / contract / trading-engine / pipeline (10 files) | **428 passed** |
| **full suite** | **2,709 passed · 15 skipped** |

**New failures: 0. New errors: 0.** The 32 pre-existing failures are unchanged
and unrelated: `test_upstox_isin` (7), `test_entry_confirmation` (6),
`test_pre_event_gap_phase3` (5), `test_trade_simulator_confirmation_lost`
(5 errors), `test_alert_router` (4), `test_alert_reports` (2),
`test_pre_event_gap_phase6 / 5_5 / foundation` (3).

The 4 skips are the real-DB tests: under pytest `DATABASE_URL` points at the
empty `autotrade_test`, and conftest aborts if it ever resolves to production.
They are therefore proven against the live database separately, in §6.

---

## 6. Real DB special-session read evidence

Not the calendar helper in isolation — the production path, on the rows the
Step 2H pilot actually wrote: **DB canonical row → `daily_series` → resolved
session.**

```
RELIANCE.NS canonical special-session rows stored:  10

  session      day   stored close      reader returns          result
  2016-10-30   Sun        250.50   250.50 (canonical_0345)      OK
  2019-10-27   Sun        683.60   683.60 (canonical_0345)      OK
  2020-02-01   Sat        659.30   659.30 (canonical_0345)      OK
  2020-11-14   Sat        954.30   954.30 (canonical_0345)      OK
  2023-11-12   Sun       1165.30  1165.30 (canonical_0345)      OK
  2024-01-20   Sat       1356.65  1356.65 (canonical_0345)      OK
  2024-03-02   Sat       1491.10  1491.10 (canonical_0345)      OK
  2024-05-18   Sat       1434.80  1434.80 (canonical_0345)      OK
  2025-02-01   Sat       1264.00  1264.00 (canonical_0345)      OK
  2026-02-01   Sun       1347.00  1347.00 (canonical_0345)      OK

  10/10 special sessions now readable
```

**Readable sessions: 2,472 → 2,480.** (Net +8, not +10: the ten special sessions
were added, and two sessions previously served from `legacy_1830` are now
covered by canonical rows the backfill supplied, so they dedupe into the
canonical convention.)

Boundaries held:

* weekend sessions returned: **10**, every one declared — **undeclared weekend
  sessions returned: none**
* look-ahead: 09-02 → 1313.1, 09-03 → 1302.5, 09-04 → 1322.0
* series unique and ordered: **True**
* `market_regime`'s NIFTYBEES input still clears its 60-session floor

**Database unchanged by this task:**
`total=36,149,406 · 1d=5,915,920 · 00:00=4,854,349 · 03:45=56,112 · 18:30=1,002,584`
— identical to the post-pilot state.

---

## 7. Is production live special-session writing now safe?

# NO — and it needs a different calendar than this one

**Not one** of the ~14 production `save_candles_to_db` call sites passes
`extra_open` (`india_price_feed`, `upstox_historical`, `zerodha_historical`,
`zerodha_market`, `india_tasks`, `api/india`). They all inherit `None`, so a
live special session is still refused at the write path:

```
2026-11-08 (Diwali Muhurat, Sunday), production default
  -> (False, 'session date 2026-11-08 is a weekend')
```

**And the curated calendar would not fix it:** `2026-11-08` is *not* in
`NSE_SPECIAL_SESSIONS`. That list is historical by construction — the ten dates
observed in ten years of data. A forward-looking session cannot be in it.

**The right source already exists and already works.** Live check just now:

```
GET /v2/market/holidays -> HTTP 200, 22 entries
nse_extra_open_dates(payload) -> ['2026-02-01', '2026-11-08']

  2026-11-08  SPECIAL_TIMING  open_exchanges=[MCX, NSCOM, NSE, BSE, NFO, CDS, BCD, BFO]
              "Diwali Laxmi Pujan"
```

So the exchange publishes it, and `utils.candle_contract.nse_extra_open_dates()`
already derives it correctly. What is missing is only the wiring.

### Reported as a distinct task, not done here

I did not wire it, for two reasons the brief anticipates:

1. It is a **different calendar source** — the live API payload, not the curated
   historical list — so it is not "supplying the same curated calendar".
2. It needs a failure-mode decision I should not make silently: the payload
   requires an HTTP call and a cache, and something must define what happens
   when that call fails on a Muhurat morning. Fail-open risks accepting a real
   weekend bar; fail-closed drops the session, which is today's behaviour.

**Deadline: 2026-11-08**, ~2 months away. Until then every production writer
will drop that session, exactly as it dropped the previous nine.

---

## 8. Is the full backfill ready?

# STILL BLOCKED — but the reader asymmetry is no longer the reason

| gate | state |
|---|---|
| Writer stores special sessions | ✅ Step 2H |
| **Reader returns them** | ✅ **this task — 10/10 proven on real rows** |
| Writer and reader share one calendar | ✅ same object, asserted |
| Ordinary weekends still refused | ✅ both halves |
| Look-ahead free | ✅ |
| No new test failures | ✅ 0 of 2,709 |
| No DB mutation this task | ✅ 0 |
| Full universe run | ⛔ **not run, as instructed** |
| Live production special-session writing | ⛔ **§7 — separate task** |
| Legacy 00:00 basis decision | ⛔ unchanged since Phase 2E |

The backfill is technically ready to run: the pilot passed, the reader now
returns everything the writer stores, and nothing regressed. It remains blocked
only because you have not authorised the full run, and because §7 is still open
— though §7 does not affect historical backfill correctness, only future live
sessions.

**No historical data was migrated, normalized or deleted. The full 2,468/2,700-symbol
backfill was NOT run.**
