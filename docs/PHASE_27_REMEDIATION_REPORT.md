# PHASE 27 — PRODUCTION REMEDIATION REPORT

**2026-08-27, changes applied 12:24–12:40 IST, market OPEN throughout.**
Mode: PAPER. V2/120 untouched. No real order path enabled.

---

## 1. EXECUTIVE VERDICT

I implemented **5 of the 9 findings** and left 4 untouched. That is a deliberate
scope call, not an omission — see §5.

| Finding | Verdict |
|---|---|
| F1 NSE announcement recall | **PARTIALLY FIXED** — fetch confirmed live (2 → 26 high-impact); downstream queue is not draining |
| F2 BSE→NSE normalisation | **PARTIALLY FIXED** — one normaliser built and tested; wired into 2 of ~5 call paths |
| F3 DIRECT_NEWS double-suffix | **CONFIRMED FIXED** in code + 34 tests; **NOT YET EXERCISED LIVE** |
| F4 Same-bar exit protection | **CONFIRMED FIXED** — 37 tests; **NOT YET EXERCISED LIVE** |
| F7 Candles index | **PARTIALLY FIXED** — index built and valid, but it does **not** help the query it was requested for |
| Capital rejection telemetry | **CONFIRMED FIXED** (code); awaiting first scan-cycle rows |
| F5 Sector data | **NOT FIXED** |
| F6 Premarket backlog | **NOT FIXED** |
| F8 LLM transport taxonomy | **NOT FIXED** |
| F9 Event provenance | **NOT FIXED** |
| Queue starvation | **NOT CHANGED** — measured only |

**Two things I got wrong and corrected mid-phase are recorded in §17.**

---

## 2. GIT STATE BEFORE CHANGES

```
HEAD            28ee7e6
staged          0
checkpoint tag  phase27-checkpoint  -> 28ee7e6
colleagues' in-flight files  4, untouched throughout
open positions at start      10, Rs457,005 deployed
```

## 3. FILES CHANGED

| File | Class |
|---|---|
| `utils/symbols.py` **(new)** | A — correctness |
| `crawler/news_crawler.py` | A — correctness |
| `engine/direct_news_strategy.py` | A — correctness |
| `engine/exit_policy.py` | **D — safety protection** (F4, the one approved behaviour change) |
| `utils/config.py` | D — 2 new settings |
| `engine/tactical_executor.py` | B — observability |
| 3 new test files | — |
| Postgres: `ix_candles_tf_ts` | C — infrastructure |

**No file outside this list was modified. `.env` was NOT touched.**

---

## 4. EXACT CHANGES, WITH BEFORE/AFTER EVIDENCE

### F1 — NSE announcement recall

**Before:**
```python
url = ".../corporate-announcements?index=equities"       # no dates
for item in (data or [])[:limit]:                        # slice BEFORE filter
    if not any(kw in category.lower() for kw in _HIGH_IMPACT...): continue
```
Two independent recall bugs: an unscoped endpoint returning a rolling ~20-item
window, and a slice that discarded high-impact filings *without examining them*.

**After:** date-scoped to a single IST trading date (`DD-MM-YYYY`, `from_date ==
to_date`, so yesterday cannot leak in); filter runs on the whole payload, limit
applied last; `seq_id` dedup preserved; symbols go through the normaliser.

**Live verification, 12:53:53 IST:**
```
NSE_ANNOUNCEMENT_POLL date_scope=27-08-2026 date_scoped=True
  nse_total=116 nse_high_impact=26 returned=26 limit=50
  duplicates_in_payload=0 truncated_by_limit=0
```
**Visibility went from ~2 high-impact to 26.**

**Residual risk — and why this is only PARTIAL:** the announcements reach the
in-process queue and stop there.
```
[nse_poller] poll seen=26 new=0 dup=26 enqueued=26 dropped=0 depth=26/200
   ... identical across polls 11, 12, 13, 14
```
`news_items` holds **3** NSE-Announcement rows today. **The queue depth is
static at 26 while the consumer runs.** The fetch is fixed; the conversion is
not. Root cause NOT established.

This is exactly the failure mode the brief warned about — *"do not increase LLM
processing without validating downstream queue capacity."* Supply rose 13×;
the consumer did not. **I deliberately did not touch the consumer during market
hours.**

### F2 — canonical symbol normaliser

`utils/symbols.py`: `normalize()` (no DB, suffix-safe) and `resolve()` (twin
lookup). Dual-listed `.BO` resolves to `.NS`; BSE-only keeps `.BO`; a lookup
failure **keeps the original exchange** rather than guessing.

Verified against the instrument table: J&KBANK, HUDCO, BERGEPAINT, ADANIENSOL,
BALKRISIND all dual-listed; NHCFOODS is BSE-only.

**Why PARTIAL:** wired into `direct_news_strategy` (F3) and tactical signal
telemetry. **Not** wired into the news-engine candidate path — `MAXGROW.BO` was
still sent to a multi-agent LLM debate at 12:54, which is the 29-of-136 problem
unchanged. Wiring that is a change to the live candidate path and I judged it
unsafe mid-session.

### F3 — the double-suffix bug

**Before:** `fetch_nse_candles(f"{ticker}.NS", ...)` where `ticker` was already
`GKENERGY.NS` → `GKENERGY.NS.NS` → no candles → `hist_df is None` → **the whole
`if hist_df is not None` block skipped.** The 20-EMA trend filter and the
volume-confirmation filter were not failing. They were never running.

**After:** canonical symbol, plus a log line that says whether the filters could
run at all, plus a WARNING when they cannot. `technical_reject=1` /
`volume_reject=1` counters added.

**Thresholds unchanged** — `span=20`, `window=20`, `avg_vol * 0.5`, pinned by test.

**Codebase sweep:** 29 other `.NS`-append sites reviewed. The rest operate on
bare Kite `tradingsymbol` values and cannot double-suffix. **This was the only
live instance.**

### F4 — same-bar exit protection *(the one approved behaviour change)*

**Before, measured:**
```
BHEL 5s -95 | BHEL 5s -161 | SMLMAH 1s -226 | COSMOFIRST 4s -529
AUTOBEES 5s -150 | GODREJCP 8s -153 | ETERNAL 1s -158 | TVSMOTOR 3s -136
   ^ all EXHAUSTION, -Rs1,608
CANBK 9s -168  <- a REAL stop-loss, must still fire
```

**After:** a `PROFIT_MANAGEMENT` exit requires ≥1 **completed bar**. Bar
*boundaries* are counted, not elapsed minutes — a position opened 09:19:58 has
seen 09:15–09:20 complete three seconds later; one opened 09:20:01 has not, four
minutes later. Elapsed time cannot express that.

**Deliberately mode-independent.** V2's 120m gate covers this today, but a
rollback to CONTROL must not reopen the hole.

Verified matrix:

| Case | Result |
|---|---|
| EXHAUSTION +5s / +4m | **blocked** (same-bar) |
| EXHAUSTION +5m01s | passes same-bar, then meets the V2 gate |
| STOP_LOSS +9s | **allowed** |
| MARKET_SHOCK_FLATTEN +2s | **allowed** |
| CONFIRMATION_LOST +30s | **allowed** (untouched) |
| MIS_SQUAREOFF +3s | **allowed** |
| V2 at 119m / 120m | **defer / allow — unchanged** |
| Under CONTROL, EXHAUSTION +6m | **allowed** (only the bar delays it) |

### F7 — candles index

**Before:** no index leads with `timeframe`. 42,444,021 rows, 13 GB.

`CREATE INDEX CONCURRENTLY ix_candles_tf_ts ON candles (timeframe, timestamp)` —
built in 376s, **294 MB, valid, no table lock.**

**Honest result — it does NOT fix the query it was requested for.**

| Query | Uses index? | Buffers | Time |
|---|---|---:|---:|
| `count(DISTINCT symbol) WHERE timeframe AND timestamp` | **No** | 662,813 | 7.1s |
| `max(timestamp) WHERE timeframe='1m'` | **Yes** | **6** | **0.06s** |
| `count(*) WHERE timeframe AND timestamp >= today` | **Yes** | 29,602 | 0.15s |

The planner is right to refuse it for the first: `symbol` is not covered, so
every row would still need a heap fetch. **A covering index
`(timeframe, timestamp) INCLUDE (symbol)` would fix it — I did not create it,**
because the brief says not to create speculative indexes.

**I also could not reproduce the 108-second figure.** I measured **4.8s** before
the index. The plan *is* a parallel seq scan reading ~5 GB, so the concern is
real, but I am not confirming a number I did not observe.

The index is retained: it turns the staleness-watchdog query from a seq scan
into a 6-buffer lookup. Cost: one more index maintained on ~212k inserts/day.

### Capital-rejection telemetry

Every rejected tactical signal now persists `reason_class` (CAPITAL /
SECTOR_CAP / SECTOR_BREADTH / STRATEGY_CAP / DUPLICATE / RISK_RR / LLM_VETO /
OTHER), rank, score, side, reference price, stop, target, entry eligibility,
canonical symbol, sector, timestamp. **No limit was changed.**

---

## 5. CHANGES DELIBERATELY NOT MADE

| Finding | Why not |
|---|---|
| **F5 sector data** | Requires diagnosing a publisher during market hours. Not attempted. |
| **F6 premarket backlog (2,677 rows)** | Any staleness rule is a judgement about what may become a trade candidate. Wrong call = historical news trading live. Needs design review. |
| **F8 LLM transport taxonomy** | Touches the decision-engine outcome path. `HINDCOPPER.NS` retries were visible at 12:54 — the retry/backoff works; only the *labelling* is wrong. Safe to defer. |
| **F9 event provenance** | Largest of the nine, touches every write path into `causal_events`. Not safe to start mid-session. |
| **Queue starvation** | Measured only (193 → 90 → 133 during the phase). Re-routing tasks mid-session risks dropping in-flight work. |
| **News-engine `.BO` path** | Live candidate path. See F2. |

Also NOT changed: PAPER_MODE · capital · sizing · TOP_N · R:R · turnover ·
Master Score · prompts · AI model/routing · BUG-1 · V2 120m · hard stops ·
setup invalidation · risk limits · signal-selection logic.

---

## 6–7. TESTS

| | Baseline | After | Δ |
|---|---:|---:|---|
| passed | 2,054 | **2,151** | **+97** |
| failed | 27 | **27** | 0 |
| skipped | 7 | 7 | 0 |
| errors | 5 | **5** | 0 |

**Zero new failures.** Re-ran the nine known-failing files in isolation:
`27 failed, 112 passed, 5 errors` — identical set, identical counts.
No test was weakened or deleted.

New: `test_symbol_normalisation.py` (34) · `test_same_bar_exit_protection.py`
(37) · `test_nse_announcement_recall.py` (23).

---

## 8–10. RUNTIME AND LIVE VERIFICATION

Workers run `watchmedo --pattern="*.py"` and **auto-reloaded**; no manual
restart was performed, so no rolling-restart risk was taken and no task was
dropped. 27 reload events between 12:20 and 12:40, all clean.

```
TRADING_STRATEGY_MODE = V2          PAPER_MODE = True
V2_MIN_HOLD_MINUTES   = 120         MIN_COMPLETED_BARS_BEFORE_PROFIT_EXIT = 1
TACTICAL_TOP_N        = 15          PROFIT_EXIT_BAR_MINUTES = 5
```

Import / syntax / name errors after 12:24, all 7 services: **0.**
All endpoints 200 including the :5173 frontend proxy.
Queues: default 90–133, exit 0, scan 4, trade 0.
Open positions untouched throughout.

**Not yet exercised live:** F3 (no DIRECT_NEWS candidate since the fix), F4 (no
profit-management exit attempted inside an entry bar since the fix). Both are
proven by test, not by production.

---

## 11–16. TARGETED VERIFICATION

- **V2:** V2/120 confirmed in the live runtime; 119m defers, 120m allows.
  Same-bar protection is layered *before* it and does not shorten it.
- **Capital telemetry:** code in place; first rows expected on the next scan.
- **News detection:** fetch fixed and verified; **conversion not verified.**
- **Symbol mapping:** normaliser verified against the real instrument table;
  two call paths wired, others not.
- **Exit safety:** hard stops and shock flattens proven immediate by test; the
  CANBK-style 9-second stop still fires.
- **Rank overflow:** still non-executable, ranks 16–40, unchanged.

---

## 17. TWO ERRORS I MADE DURING THIS PHASE

1. **I reported 1,623 errors in `celery-worker` after my changes.** They were
   `PostgresSyntaxError` strings matching my `SyntaxError` grep, all timestamped
   **12:20:15 — four minutes before my first edit.** Re-checked with word
   boundaries: **0 real errors.** Same substring-matching mistake I have now
   made several times in this programme.
2. **Two of my own tests failed against my own explanatory comments** (the
   comment describing the old `f"{ticker}.NS"` bug matched a search for it).
   Fixed by asserting against AST-stripped source.

**Separately discovered, pre-existing, NOT mine and NOT fixed:**
`intelligence_hub:persist_daily_history` fails its upsert with a Postgres syntax
error — **22,459 times today**, for every symbol. Worth its own investigation.

**Security note:** an EventRegistry API key is printed in plaintext in
`news_crawler` error logs on every 403. **That key should be rotated and the log
line redacted.**

---

## 18. ROLLBACK

```bash
cd /home/cis/windows/auto-trade-pro
git revert --no-commit 64dba9a && git commit -m "revert phase 27"
# or, to the pre-phase checkpoint:
git reset --hard phase27-checkpoint
```
Workers auto-reload; no restart needed. `.env` was never modified.

To drop the index (it is not required by any code):
```sql
DROP INDEX CONCURRENTLY IF EXISTS ix_candles_tf_ts;
```

**F4 alone**, without reverting the rest:
`MIN_COMPLETED_BARS_BEFORE_PROFIT_EXIT=0` in `.env` → guard disabled, tested.

---

## 19. WATCH FOR THE REST OF TODAY

1. `[nse_poller] depth=` — **if it climbs toward 200/200, the recall fix is
   outrunning the consumer.** This is the highest-priority live signal.
2. First `[direct_news] candle_lookup=` line — proves F3 end to end.
3. Any `same-bar protection` line — proves F4 end to end.
4. `rejection` blocks appearing in `tactical_signals.meta_json`.
5. Default queue depth — it should not resume climbing.
6. V2: still zero profit-management exits before 120m.

## 20. AFTER THE CLOSE

1. **The capital question:** did the 398 cash-rejected candidates outperform
   the 14 taken? The telemetry now exists to answer it from tomorrow.
2. Why the NSE queue is not draining. **This is now the top blocker** — the
   recall fix has no value until it converts.
3. Whether `(timeframe, timestamp) INCLUDE (symbol)` is worth its write cost.
4. `persist_daily_history` — 22,459 failures/day.
5. F5, F6, F8, F9, and the news-engine `.BO` path.

## 21. DO NOT CHANGE TOMORROW MORNING BEFORE EVIDENCE REVIEW

V2 mode or the 120-minute horizon · `MIN_COMPLETED_BARS_BEFORE_PROFIT_EXIT` ·
TOP_N · R:R · turnover · capital limits or the cash buffer · sizing · Master
Score · prompts · AI model or routing · BUG-1 · signal-selection logic ·
hard stops · setup invalidation.

**Today established that the plumbing was broken and that some of it is now
fixed. It established nothing about the strategy.**
