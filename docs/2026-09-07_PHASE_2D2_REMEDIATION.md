# PHASE 2D.2 REMEDIATION REPORT

**Date:** 2026-09-07 · **Scope:** daily-candle consumer remediation
**Absolute rule observed:** no historical database row was migrated, deleted,
updated, merged, backfilled or manually inserted. Verified in Part K.

---

## 0. Verdict

| | |
|---|---|
| Consumers remediated | 4 named (A–C) + 3 found in the Part D sweep |
| New test failures caused | **0** (32 before, 32 after, identical set) |
| Production rows changed by this phase | **0** |
| Migration gate | **STILL CLOSED** — see §12 |

Two findings emerged that were not in the brief and matter more than the
consumer fixes themselves: an **active writer that was deleting the canonical
candles every run** (§4), and **three production workers running 12-day-old
code** (§11).

---

## 1. The defect being remediated

`candles` holds three daily timestamp conventions simultaneously:

| stored at | meaning | price basis |
|---|---|---|
| `03:45` UTC | canonical — 09:15 IST session open | raw (Upstox) |
| `18:30` UTC | legacy — the session date **minus one** | raw |
| `00:00` UTC | legacy — the session date | **adjusted** (see §9) |

Any reader treating a row's calendar date as its session date is wrong twice:

1. **Session collision → look-ahead.** An 18:30 bar shares a calendar date with
   the 03:45 bar of that date but belongs to the *next* session. Measured:

   ```
   RELIANCE.NS  2026-09-02 03:45  close 1313.1  -> session 2026-09-02
   RELIANCE.NS  2026-09-02 18:30  close 1302.5  -> session 2026-09-03
   ```

   Both carry calendar date 2026-09-02; 18:30 sorts first under `timestamp
   DESC`, so 2026-09-02 was reported as **1302.5 — the next session's close**.

2. **Duplicate sessions.** `ORDER BY timestamp DESC LIMIT 220` on NIFTYBEES.NS
   returned 220 **rows** covering 80 **sessions**. The resulting returns series
   had 111 of 219 values exactly zero (51%) and volatility 39% too low. That
   fed a trading gate.

**3,611 of 4,378** daily symbols carry more than one convention, so this is the
normal case, not an edge case.

---

## 2. One implementation: `engine/daily_series.py`

Per the brief's "do not create a second competing session-date implementation",
all readers now delegate to one new module, which itself delegates session
resolution to the existing `utils/candle_contract.py`.

```
session_closes(symbol, session, sessions=220, basis=None)   -> [(date, close, convention)]
session_close_series(...)                                   -> [close]           oldest→newest
close_for_session(symbol, target, session, tol_days=4)      -> close | None      fails closed
session_bars(symbol, start, end, session)                   -> [(date,o,h,l,c,v)] session-bounded
```

Guarantees: one row per NSE session · oldest→newest · a single price basis ·
refuses rather than guesses.

**Price basis is chosen before the fetch, not after.** The first version
over-fetched `N×4` rows, resolved them, then discarded one basis; when the
discarded basis dominated recent rows NIFTYBEES.NS came back with **16** of 220
sessions — below `market_regime`'s 60-session floor, which would have silently
**blocked all new entries**. The basis is now decided per symbol from a cheap
aggregate, then only that basis is read. Pinned by
`test_regime_gate_clears_its_fail_closed_floor`.

**Deliberate refusals.** NSE runs a few weekend sessions (Budget Day 2026-02-01
was a Sunday; Diwali Muhurat; DR drills). Resolving those needs the exchange
calendar, passed as `extra_open`. Without it they are **excluded, never
mis-dated** — 6 rows of 880 on NIFTYBEES.NS (0.7%, over three years), which
moves neither an EMA200 nor a 20-day ROC. Left as a refusal rather than a
hardcoded date list, because a wrong calendar baked into code is harder to
notice than a visibly absent session.

**NaN guard.** `THELEELA.NS` and `LTFOODS.NS` each hold a 2026-06-22 daily bar
with NaN OHLC and a real volume (5 such rows across all timeframes). One NaN
propagates through every EMA, ROC and stdev downstream, so these are dropped at
the reader. Note PostgreSQL defines `NaN = NaN` as **TRUE**, so the intuitive
`close <> close` filter matches nothing — this cost a false "0 NaN rows" reading
before it was caught.

---

## 3. Parts A–C — the four named consumers

| Part | File | Was | Now |
|---|---|---|---|
| A | `engine/agent/market_regime.py:268` | `ORDER BY timestamp DESC LIMIT 220` → 80 sessions, 51% zero returns | `session_close_series` |
| B | `engine/intelligence_hub.py:246` | same query, feeding an EMA200 BEAR/BULL call | `session_close_series` |
| C | `engine/pre_event_expectation_gap/replay.py:73` | `min(|row.date − target|)` — nearest **calendar** row | `close_for_session`, fails closed |
| C | `replay.py::_candles_between` | calendar-bounded MFE/MAE window | `session_bars`, session-bounded |
| — | `engine/agent/performance_engine.py::_aligned_closes` | own resolver (fixed in 2D.0.1) | delegates — one implementation |

`_candles_between` was not named in the brief but carried the same class of
defect: an 18:30 row dated `end` belongs to the session *after* `end`, so its
high or low could set the MFE/MAE of a hold that had already exited.

`_trading_date()` was left unmodified per the 2C.1 instruction. It now has
**zero production callers** (tests still measure it).

Behaviour is unchanged where it should be: `get_market_regime` returns
`WEAK_BEAR / −60.4 / roc −3.23% / ema 0/4` both before and after the
reversal cleanup.

---

## 4. Part D headline: an active writer was deleting the canonical rows

`crawler/india_price_feed.py::sync_regime_daily_candles_kite` ran, before every
insert:

```sql
DELETE FROM candles WHERE symbol = :sym AND timeframe = '1d'
  AND timestamp >= now() - interval '15 days'
```

on the premise that it was authoritative and was clearing stale yfinance
duplicates. It is no longer either: it now sources from **Upstox**, and since
Step 2C.2 the canonical writer independently writes these same symbols at 03:45.
So it was **destroying the canonical rows every run**.

Measured 2026-09-07, with a clean control:

| symbol | raw (03:45/18:30) rows inside the 15-day window |
|---|---|
| NIFTYBEES.NS, ^NSEI, ^NSEBANK, ^BSESN | **0** |
| RELIANCE.NS, TCS.NS (same window, not in the list) | **19 each** |

NIFTYBEES.NS 03:45 rows carried a last-written time of 2026-09-04 10:01 while
the newest surviving session was 2026-08-21 — written daily, deleted hours
later. Independently corroborated in Part K: the only daily rows written between
the baseline stamp and now were **33 rows — exactly the three regime symbols at
00:00 over a rolling 15-day window**, with the table total unchanged.

**The consequence landed on a trading gate.** `market_regime` reads
NIFTYBEES.NS; with its canonical rows removed, the only basis reaching the
present was the adjusted 00:00 series this same task writes.

**Fix:** the delete is removed. `save_candles_to_db` upserts, so the refresh is
idempotent without it, and rows the task does not own are now left alone. No
existing row was touched. As raw rows survive and catch up, the reader's
recency rule will return these symbols to the raw basis on its own.

---

## 5. Part D — full daily-reader inventory

| # | Reader | Status | Action |
|---|---|---|---|
| 1 | `market_regime.py:268` | **was UNSAFE** | fixed (A) |
| 2 | `intelligence_hub.py:246` | **was UNSAFE** | fixed (B) |
| 3 | `replay.py::_close_near` | **was UNSAFE** | fixed (C) |
| 4 | `replay.py::_candles_between` | **was UNSAFE** | fixed (C) |
| 5 | `performance_engine::_aligned_closes` | fixed in 2D.0.1 | now delegates |
| 6 | `ml_predictor.py:601` `train_all_models` | **was UNSAFE** — 3,611 symbols trained on duplicated sessions; every rolling feature spanned half its claimed calendar | fixed → `session_bars` |
| 7 | `india_specific.py:103` `_fetch_candle_prices` | **was UNSAFE** — `LIMIT 30` over two conventions made a "30-day change" a ~15-day change | fixed → `session_closes` |
| 8 | `corporate_actions.py:213` | **was UNSAFE** — compares a daily close to the next morning's raw intraday open and calls a gap a split; an adjusted close on one side **is** the corporate action | fixed → `basis="raw"`, skips if no raw row |
| 9 | `tactical_data_fetcher.py:498` `get_f1_universe` | **SAFE** | latest close per symbol, `LIMIT 1`, no cross-session arithmetic |
| 10 | `market_scanner.py:104` | **SAFE** | `SELECT DISTINCT symbol` — no prices |
| 11 | `portfolio_analytics.py:200` `get_nifty_return` | **SAFE in practice** | `^NSEI` holds only the 00:00 series → one row per session. Latently fragile if a second convention ever appears |
| 12 | `india_specific.py:199` VIX backtest | **DEAD** | `^INDIAVIX` has **no 1d rows**; always falls through to the neutral 15.0 default |
| 13 | `zerodha_market.py:198` `refresh_instrument_tokens` | **DEAD / SAFE** | F&O OTM spot approximation; returns early without a Kite token, and is `LIMIT 1` regardless |
| 14 | `api/stock_chat.py:290` `predict_chart` | **UNSAFE, advisory only** | 30 rows → ~15 sessions for an LLM chart opinion. Not a trade gate. **Recommended, not done** — outside the money path |
| 15 | `api/zerodha.py:661` `_candles_from_db` | **UNSAFE, display only** | chart series may repeat sessions. **Recommended, not done** |

No **active UNKNOWN** remains.

---

## 6. Parts E & F — tests

`tests/test_step2d2_daily_series.py` — **17 tests, all passing**.

Under pytest `DATABASE_URL` points at `autotrade_test`, which is empty:
production data is deliberately out of reach, and conftest aborts the run if the
two ever resolve to the same database. The suite therefore **seeds the exact
rows that exhibited each defect**, which is stronger than reading production —
the 1313.1 / 1302.5 collision is reproduced on purpose rather than waited for.

Covered: the point-in-time acceptance case (E) · one row per session · the
zero-returns collapse · the 60-session fail-closed floor · single basis · the
raw-basis refusal · replay tolerance failing closed · session-bounded excursion
windows · row-order independence (F) · and AST-level assertions that each
consumer delegates and that the destructive delete is gone.

Assertions are made against the **parsed AST**, never source text: these modules'
docstrings legitimately name the unsafe thing they replaced, so a substring
search matches the explanation and fails a correct file.

---

## 7. Part J — regression sweep

Rigorous before/after, stashing **only** the files this phase touched (never a
broad stash — the tree carries other people's in-flight work):

| | failures/errors | passed |
|---|---|---|
| Before (changes absent) | 32 | 2,515 |
| After | **32** | **2,532** |

**New failures: 0.** The two sets are identical after normalising an interleaved
asyncio log line. All 32 are pre-existing and unrelated (`test_upstox_isin`
signature drift, `test_entry_confirmation`, `test_alert_router`,
`test_pre_event_gap_phase3`, `test_trade_simulator_confirmation_lost`).

Two tests were updated because the seam moved, not the contract:

- `test_trading_engine.py::TestNiftyTrendGate` mocked the old raw query; it now
  mocks `session_close_series`. The gate's contract (fail **closed** under 60
  sessions, buy on a sustained uptrend) is unchanged and still asserted.
- `test_step2d01_session_resolution.py` asserted `_aligned_closes` imports the
  resolver directly; it now asserts the delegation **and** that the shared
  module uses the resolver — the same guarantee at both levels.

---

## 8. Part K — no historical data was modified

| metric | baseline | after | delta |
|---|---|---|---|
| 1d total | 5,891,897 | 5,891,897 | **0** |
| 1d @ 00:00 | 4,854,453 | 4,854,453 | **0** |
| 1d @ 03:45 | 31,985 | 31,985 | **0** |
| 1d @ 18:30 | 1,002,584 | 1,002,584 | **0** |
| frozen-window checksum (rows created before the baseline stamp) | n=5,891,862 sum=4954460247.82 | n=5,891,862 sum=**4954460247.82** | **unchanged** |

Rows migrated / deleted / updated / manually inserted by this phase: **0**.
The 33 rows with a newer `created_at` are the scheduled regime sync described in
§4, not this work. Test fixtures were written to `autotrade_test` only.

---

## 9. Part G — the 00:00 adjustment basis (investigation only, no changes)

**Precision signature** (NSE quotes to 2 decimal places — sub-paise precision is
a computed price):

| series | rows | closes with >2dp |
|---|---|---|
| 00:00 | 4,854,451 | 234,337 (**4.8%**) |
| 03:45 | 31,985 | 0 (0.0%) |
| 18:30 | 1,002,584 | 0 (0.0%) |

*(The 4.8% is the whole-table figure and supersedes the 19.0% sampled earlier in
the programme.)*

**The 00:00 series has no single consistent basis.** Comparing same-session
pairs against the raw 03:45 series:

| symbol | date | 00:00 | 03:45 | price ratio | volume ratio |
|---|---|---|---|---|---|
| INFY.NS | 2026-06-04..09 | 1175.86 | 1201.30 | **0.9788** (constant, 4 sessions) | **1.0000** |
| BAJFINANCE.NS | 2026-06-16 | 1788.90 | 959.65 | 1.8641 | 0.2187 |
| LTTS.NS | 2026-06-16 | 4007.50 | 3462.90 | 1.1573 | 5.8979 |

Three different behaviours:

- **INFY** — a textbook price-only adjustment: a constant factor across
  sessions, volume untouched. Classic yfinance `Adj Close` semantics.
- **BAJFINANCE** — 00:00 is ~1.86× *higher* with *lower* volume. A split
  adjustment lowers price and raises volume together, so this is not an
  adjustment at all: the 00:00 row looks **stale / pre-split** while the raw
  row is current.
- **LTTS** — inconsistent with both.

1,096 same-session pairs overlap; **108 (9.9%) differ by more than 0.5%**.

**Conclusion.** The 00:00 series is *not* a clean adjusted series — it is a
mixture of price-only adjustments, stale pre-corporate-action rows, and rows
that agree. It cannot be treated as an authoritative adjusted basis, and it must
not be blessed as canonical. The authoritative raw source is Upstox
(03:45/18:30); no authoritative *adjusted* source is currently ingested.

**Recommendation:** keep the reader's single-basis rule; do not migrate 00:00
into the canonical series. Its correct session date is **not** sufficient
grounds for migration — the price basis is the unresolved problem, and the
brief's own caution applies exactly here.

---

## 10. Part H — index writer status: **UNPROVEN**

| symbol | conventions present | latest |
|---|---|---|
| `^NSEI` | 00:00 only (1,281 rows) | 2026-09-07 |
| `^NSEBANK` | 00:00 only (1,281 rows) | 2026-09-07 |
| `^BSESN` | 00:00 only (35 rows) | 2026-08-28 |

**No index has produced a single canonical 03:45 row.** Index routing to the
canonical path was added in 2D.0.1 (`fetch_nse_candles` delegates `NSE_INDEX`),
but `sync_regime_daily_candles_kite` calls `get_kite_historical` directly,
bypassing that delegation, and re-anchors its bars to midnight — so indices keep
receiving 00:00 rows from a path that never reaches the canonical writer.

Per the brief, the scheduled canonical run was **not** triggered manually and no
index row was inserted. Status is UNPROVEN, not FAIL. Resolving it is index
writer work, not consumer work, and belongs in the next step.

---

## 11. Part I — blast radius, and a deployment caveat

`get_market_regime` consumers:

| consumer | effect of `can_buy = False` |
|---|---|
| `engine/agent/agent_loop.py:226` | full scan skip, returns `regime_gate_blocked` (agent_loop's TECHNICAL origination is hard-blocked anyway) |
| `tasks/india_tasks.py:470` | sets `regime_allows_buy` for `india_trade_loop` (strategy B) |
| `api/india.py:2459` | dashboard display |
| `intelligence_hub` | `nifty_regime` BEAR/BULL feeds the 7-factor score |

`news_discovery_engine` — the live trade path — does **not** consume
`get_market_regime` directly.

**Caveat, and it is important.** There are two worker sets:

- the `watchmedo`-wrapped `default` worker (started 12:03 today) — hot-reloads,
  and runs `tasks.india_price_scan`, so the §4 writer fix **is live**;
- three dedicated-queue workers — `exit_queue`, `scan_queue`, `trade_queue` —
  started **Wed Aug 26 21:22**, *not* under `watchmedo`. They have been running
  **12-day-old code** and will not pick up this remediation, nor the earlier
  2C/2D fixes, until restarted.

`tasks.india_trade_loop` (trade_queue), `tasks.fast_sl_check` (exit_queue) and
the tactical scans (scan_queue) are all on the stale workers.

**Recommended:** `systemctl --user restart autotrade-celery-worker` — deferred,
because the brief ends this phase at the report.

---

## 12. Part L — migration gate

**CLOSED. Do not migrate.**

| precondition | state |
|---|---|
| Readers session-correct | **met** — §3, §5 |
| Single implementation | **met** — `engine/daily_series.py` |
| Tests pin the behaviour | **met** — 17 tests, 0 new failures |
| Competing daily writers eliminated | **not met** — §4 fixed the destructive one; §10 shows indices still bypass the canonical path |
| 00:00 price basis understood | **not met** — §9: mixed, not a clean adjusted series |
| Index canonical writer proven | **not met** — UNPROVEN, §10 |

Three of six preconditions are unmet. The correct next step is the **index
writer**, then the 00:00 basis question — not a migration.

---

## 13. Files changed

**New:** `engine/daily_series.py` · `tests/test_step2d2_daily_series.py`

**Modified:** `engine/agent/market_regime.py` · `engine/intelligence_hub.py` ·
`engine/pre_event_expectation_gap/replay.py` ·
`engine/agent/performance_engine.py` · `engine/ml_predictor.py` ·
`engine/india_specific.py` · `crawler/corporate_actions.py` ·
`crawler/india_price_feed.py` · `tests/test_trading_engine.py` ·
`tests/test_step2d01_session_resolution.py`

**Database:** unchanged.
