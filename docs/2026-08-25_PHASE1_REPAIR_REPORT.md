# Phase 1 — Data & Pipeline Repair

**Date:** 2026-08-25 · **Scope:** Phase 1 items A–E only.
**Production behaviour changed:** one line, the authorised F4 stop-suspension defect.
No strategy parameter, threshold, prompt, gate or stop geometry was touched.

Every finding below carries a classification: **CONFIRMED**, **STRONGLY SUPPORTED**,
**INCONCLUSIVE**, **RULED OUT**, or **EVIDENCE NOT AVAILABLE**.

---

## A — F4 → CNC → SWING → stop suspension · **FIXED**

### The defect

```
engine/tactical_executor.py:369    product = "MIS" if signal.sub_pipeline == "F1" else "CNC"
      ↓
paper_trading/trade_simulator.py:403,418-419
                                   is_swing       = product == "CNC"
                                   trade_style    = "SWING"
                                   swing_min_hold = now + 48h
      ↓
tasks/india_tasks.py:1607-1613     if sl_hit and trade_style == "SWING" and swing_min_hold:
                                       if now_ist < swing_min_hold:
                                           sl_hit = False
```

`F4_RULES = OVERBOUGHT_FADE, OVERSOLD_REBOUND, VOLUME_BREAKOUT, VWAP_CROSSOVER`.

### Was a swing hold intended? — No. **CONFIRMED**

Three independent lines of evidence:

1. **Both pipelines are intraday.** `run_intraday_scan` → F1 and `run_mean_reversion_scan` → F4
   enter through the same `_run()`, which gates on `in_entry_window()` —
   09:15–15:20 IST (`engine/tactical_data_fetcher.py:98-107`). There is no swing tactical scan.
2. **The commit that introduced it never mentions it.** `a590e44` carries a long design-notes
   section covering the enum, the router branch, the runtime flag and the retired shadow mode.
   The `product=` line appears in the diff with no rationale anywhere in the message.
3. **It produced orders that cannot legally exist.** A CNC SELL is a delivery short, which the
   cash segment does not permit. `engine/agent/execution.py:183-198` blocks exactly this on the
   live path — *"SEBI/NSE rule: delivery short selling not allowed. Use MIS product for intraday
   shorts."* Paper mode has no such check, so **3 CNC shorts were opened between 2026-08-21 and
   2026-08-25**. Had `PAPER_MODE` been off, every one would have been rejected at the broker.

Nobody designs a pipeline whose SELL rules emit unplaceable orders. The CNC branch was an
accident.

### The fix

`engine/tactical_executor.py` — `product="MIS"` unconditionally, with the three consequences
recorded in a comment at the site so the next reader does not have to re-derive them.

**Before:** F4 signal → delivery trade → 48h hold → stop suppressed → capital pinned.
**After:** every tactical signal → MIS → `trade_style="MIS"` → `swing_min_hold=None` → stop live,
squared off same day.

**Rollback:** `git revert` the commit, or restore the conditional at that single line. No schema
change, no migration, no config.

### Regression cover

`tests/test_tactical_intraday_product.py` — 8 tests pinning each link of the chain separately:

| test | pins |
|---|---|
| `test_tactical_product_is_intraday_for_every_pipeline` | `product=` is the AST constant `"MIS"` |
| `test_no_tactical_pipeline_is_excluded_from_intraday` | no `sub_pipeline` comparison reappears |
| `test_simulator_only_makes_swing_positions_from_cnc` | the CNC→SWING mapping still means what this test assumes |
| `test_mis_product_yields_live_stop_no_min_hold` | MIS ⇒ `trade_style="MIS"`, `swing_min_hold=None` |
| `test_stop_suspension_cannot_apply_to_a_tactical_position` | replays the india_tasks guard for both styles |
| `test_f4_sell_rules_exist_and_would_have_been_delivery_shorts` | the delivery-short consequence |

**Mutation-tested**, because a test that cannot fail is not cover:

| mutation | result |
|---|---|
| restore `"MIS" if sub_pipeline == "F1" else "CNC"` | **3 tests fail** |
| set `product="CNC"` with a comment claiming MIS | **2 tests fail** |

The assertions read the AST, not the source text, so a comment mentioning `"MIS"` cannot satisfy
them — that second mutation exists specifically to prove it.

### Test-suite impact

```
tests matching "tactical"   120 passed
full suite                  1676 passed · 28 failed · 5 errors
```

All 28 failures and 5 errors were verified **pre-existing** by re-running them against the
stashed HEAD version of the file: identical counts with and without the change
(`test_pre_event_gap_*`, `test_upstox_isin`, `test_trade_simulator_confirmation_lost`,
`test_alert_router`).

### Residual risk, stated

F4 positions will now be squared off the same day instead of held 48h. The 5 currently open
SWING positions are **not** retroactively converted — this changes new trades only. Whether
same-day exit is better or worse for F4 expectancy is a **Phase 2** question and was deliberately
not pre-judged here.

---

## B — NSE announcement collapse · **PARTIALLY WITHDRAWN, and a bigger defect found**

### Correction to the previous report

The earlier report stated NSE announcements had collapsed from 92/day to 6/day. **That
comparison was invalid.** It compared a completed day against a day still in progress:
announcements arrive overwhelmingly *after* the close, and the count was taken at ~17:00 IST.

Restricting both days to the same clock window (up to 17:10 IST):

| date | announcements by 17:10 IST | full day |
|---|---:|---:|
| 2026-08-19 | 23 | 62 |
| 2026-08-20 | 20 | 92 |
| 2026-08-21 | 5 | 5 |
| 2026-08-24 | 2 | 11 |
| **2026-08-25** | **21** | *(in progress)* |

**Today is normal.** The count rose from 6 to 21 during the investigation itself.

**PREVIOUS CONCLUSION NO LONGER HOLDS** for 08-25. What survives: **2026-08-21 (5) and
2026-08-24 (11) are genuinely low.** 08-24 is explained — only 33 poll cycles ran all day
against ~1,300 on a normal day, and journald shows the service restarting at 23:13 and 23:33.
**08-21 remains INCONCLUSIVE**: 510 polls ran, all 5 announcements arrived pre-open, none after.

### The defect that is real — **CONFIRMED**

**No NSE corporate announcement has been ingested during market hours since 2026-08-17.**

| date | total | pre-open | **in-session 09:15–15:30** | post-close |
|---|---:|---:|---:|---:|
| 2026-08-13 | 399 | 10 | **33** | 356 |
| 2026-08-14 | 357 | 7 | **4** | 346 |
| 2026-08-17 | 86 | 0 | **0** | 86 |
| 2026-08-18 | 95 | 0 | **0** | 95 |
| 2026-08-19 | 62 | 2 | **0** | 60 |
| 2026-08-20 | 92 | 2 | **0** | 90 |
| 2026-08-21 | 5 | 5 | **0** | 0 |
| 2026-08-24 | 11 | 2 | **0** | 9 |
| 2026-08-25 | 21 | 3 | **0** | 18 |

Six consecutive sessions, zero in-session announcements.

### Root cause — **CONFIRMED**

The announcement fetch simply stops when the market opens. From the poll log
(`logs/news-engine.log`, the tally line `[news] NSE corporate-announcements: N/20 high-impact`):

```
2026-08-25   09:14:50  ->  16:05:29     411-minute gap
2026-08-20   09:14:xx  ->  15:47:xx     393-minute gap
```

Market-hours poll count, every day measured: **0** (expected ~346 at a 65s cadence).

The cause is in `news_discovery_engine.py`. Section 2 (the NSE fetch) sits *after* section 1
(RSS), at the same indentation — it is not gated on market hours. But section 1 contains:

```python
# news_discovery_engine.py:1506-1507
if market_open:
    await process_ticker(ticker, side, headline, summary)
```

`process_ticker` runs the full LLM ReAct loop (≤20 rounds, force-decide at 12) **inline, one
article at a time**. Section 2 cannot start until every article in the batch has been processed.

The arithmetic closes it: today produced **619 agent decisions** whose timestamps run
continuously from 03:45:56 to 10:30:58 UTC — precisely the 411-minute gap. That is ~40 s per
decision, the expected profile of a Bedrock ReAct loop.

**Consequence:** NSE's market-wide feed is a 20-item sliding window (documented in
`crawler/news_crawler.py:446-450`, confirmed across 2,892 cycles in the 2026-07-22 audit).
Not polling it for 6.8 hours means every announcement filed during the session scrolls out
before it is ever seen. Announcements filed during market hours are the only ones tradeable
intraday, and the system is structurally blind to all of them.

**Not fixed in Phase 1.** The fix is architectural (decouple section 2, or move `process_ticker`
off the loop thread) and is not the pure-infrastructure change this phase authorises. Flagged
for a scoped Phase 1 follow-up.

**Explicitly not done:** no further `ON CONFLICT` workaround was added. The instruction not to
paper over this was followed — the fetch layer is healthy (377 polls today, **zero** non-200s,
**zero** exceptions); the problem is that it is never called.

---

## C — the 104 missing event tickers · **RECONCILED, 90 of 104 explained**

230 distinct tickers were named by today's `causal_events`; 126 reached the agent.

| reason | count | assessment |
|---|---:|---|
| **company name, not a ticker** | 42 | `"Balrampur Chini"`, `"PTC Industries"`, `"Dhampur Sugar"`, `"Vipul Organics"`. The classifier emits both forms — `["RUBICONRESEARCH", "RUBICON"]`, `["SCHNEIDER ELECTRIC INFRASTRUCTURE", "SCHNEIDER"]` — so the resolvable half was evaluated. **Correct behaviour, cosmetic noise.** |
| **unknown symbol** | 47 | no candle history under `.NS` or `.BO`, ever. Unlisted, delisted, or hallucinated by the LLM extractor. **Correctly filtered.** |
| **known symbol, no candles today** | 2 | not trading today. **Correctly filtered.** |
| **has candles today, still not evaluated** | **14** | **the genuine gap** |

The 14: `BDL, ICICIPRULI, SHIPROCKET, CSBBANK, KALPATARU, NESTLEIND, KRONOX, ATGL, JSWENERGY,
BEL, RITES, SIGMA, MGL, GRINFRA`.

These are liquid, actively-traded names with 1m data today that a `causal_event` named and the
agent never evaluated. **Attrition is 14/230 = 6.1%, not 45%.** The 45% figure in the previous
report counted name-variants and unlisted entities as losses.

**Why those 14 specifically: EVIDENCE NOT AVAILABLE.** Establishing it needs the queue/eligibility
path instrumented per ticker; the tables do not record a rejection reason for a ticker that never
becomes a decision. Recommended as the one piece of logging worth adding.

---

## D — `master_score` NULL · **CONFIRMED: no writer exists**

| date | agent_decisions | with master_score |
|---|---:|---:|
| 2026-08-17 | 84 | **0** |
| 2026-08-18 | 515 | **0** |
| 2026-08-19 | 472 | **0** |
| 2026-08-20 | 420 | **0** |
| 2026-08-21 | 524 | **0** |
| 2026-08-24 | 679 | **0** |
| 2026-08-25 | 619 | **0** |

**3,313 decisions over 7 days, zero populated.** Not stale, not a bad join — never written.

`master_score` is computed in `engine/intelligence_hub.py` (lines 1098–1161) and persisted to
`MasterIntelligenceScore` (`db/models.py:1156`). Searching every production package —
`engine/ api/ tasks/ crawler/ db/ paper_trading/ utils/` — returns **no assignment of
`master_score` on an `AgentDecision`**. The only references are scratch scripts at the backend
root (`test_llm_gate.py`, `run_one.py`, `test_tooluse.py`, …), none of which run in production.

**Classification: feature not being called on this path.** The column on `agent_decisions` is
dead — the hub scores symbols, and the news decision path never reads or writes that score.

**Consequence for the forensic record:** the Master Intelligence Score provably did not suppress
any of today's 619 news decisions, because it was never consulted. That hypothesis is
**RULED OUT** for the news path.

**Not changed.** The instruction was to investigate, not to alter scoring.

---

## E — short-hold churn · two distinct causes, one confirmed

All seven trades were **already MIS** (`ORB` and `PIVOT_BREAKOUT` are F1 rules), so the A fix does
not touch them.

### E1 — the four MIS_SQUAREOFF exits · **CONFIRMED: a schedule/window conflict**

| symbol | opened IST | closed IST | held | P&L |
|---|---|---|---:|---:|
| RELIANCE.NS | 15:08:23 | 15:11:24 | 181s | −₹188 |
| SHRIRAMFIN.NS | 15:08:28 | 15:11:23 | 175s | −₹147 |
| WELCORP.NS | 15:08:31 | 15:11:26 | 175s | −₹146 |
| INDOMIM.NS | 15:08:35 | 15:11:26 | 171s | −₹147 |

```
tactical entry window   09:15 – 15:20 IST   engine/tactical_data_fetcher.py:98-107
intraday squareoff      15:10 IST           tasks/celery_app.py:629-631  (crontab 09:40 UTC)
```

**The entry window stays open for 10 minutes after the squareoff that will immediately close
anything it opens.** Every MIS entry between 15:10 and 15:20 is unholdable by construction, and
entries just before 15:10 get minutes. These four opened at 15:08 and died at 15:11 — the
strategy paid four full round-trips for 3 minutes of exposure and −₹628.

Both components are behaving as written; the schedules contradict each other. **Not fixed here** —
changing `ENTRY_CUTOFF` alters when signals may originate, which is strategy behaviour and
outside this phase's authority. Recommended as a one-line Phase 1 follow-up once approved.

### E2 — the two 5-second EXHAUSTION exits · **STRONGLY SUPPORTED**

| symbol | opened | closed | held | MFE | MAE | P&L |
|---|---|---|---:|---:|---:|---:|
| BHEL.NS | 09:38:25.179 | 09:38:30.170 | **5s** | 0.000 | 0.000 | −₹95 |
| BHEL.NS | 09:45:44.569 | 09:45:49.662 | **5s** | 0.000 | 0.000 | −₹161 |

Five seconds is exactly one `fast_sl_check` tick. `MFE = MAE = 0.000` means **no price movement
was ever recorded** — the position was closed on the first evaluation after opening, before the
market moved at all.

`tasks/india_tasks.py:1569-1575` calls `detect_exhaustion(_c5, _atr)` on **5-minute** candles.
The entry rule (`PIVOT_BREAKOUT`, `ORB`) and the exit rule therefore read the *same 5m bar*: the
position was opened into a bar that already satisfied the exhaustion condition, and the very next
tick read that same bar and closed it.

This is not a race and not a duplicate signal — it is **two rules returning contradictory verdicts
on identical input**, with a full round-trip cost paid in between.

**Why STRONGLY SUPPORTED rather than CONFIRMED:** the bar timestamp `detect_exhaustion` consumed
is not logged, so "same bar" is inferred from the 5-second interval and the zero MFE/MAE rather
than read directly. Logging that timestamp would settle it, and is the recommended next step.

**No regression test yet** — per the brief, the test lands once the root cause is confirmed, not
while it is still inferred.

---

## Classification summary

| # | Finding | Classification |
|---|---|---|
| A1 | F4 → CNC → SWING → 48h stop suspension | **CONFIRMED** — fixed, mutation-tested |
| A2 | CNC delivery shorts, impossible in the cash segment | **CONFIRMED** — 3 opened 08-21→08-25 |
| A3 | A swing hold was intended for F4 | **RULED OUT** |
| B1 | "NSE announcements collapsed to 6/day" | **WITHDRAWN** — invalid clock-window comparison |
| B2 | Zero in-session announcements since 2026-08-17 | **CONFIRMED** |
| B3 | Cause: section 1's inline `process_ticker` starves section 2 | **CONFIRMED** — 411-min gap, 619 LLM decisions fill it |
| B4 | 2026-08-24's low count (service ran only 33 cycles) | **CONFIRMED** |
| B5 | 2026-08-21's low count | **INCONCLUSIVE** |
| B6 | NSE fetch layer failing / rate-limited | **RULED OUT** — 377 polls, 0 non-200, 0 exceptions |
| C1 | 45% ticker attrition | **WITHDRAWN** — actual 6.1% (14/230) |
| C2 | Why those 14 were skipped | **EVIDENCE NOT AVAILABLE** |
| D1 | `master_score` never written on `agent_decisions` | **CONFIRMED** — 0/3,313 over 7 days |
| D2 | Master Intelligence suppressed today's news decisions | **RULED OUT** |
| E1 | Entry window (15:20) outlives the squareoff (15:10) | **CONFIRMED** |
| E2 | Entry and exhaustion rules read the same 5m bar | **STRONGLY SUPPORTED** |

---

## Changes made

| file | change | rollback |
|---|---|---|
| `engine/tactical_executor.py` | one line: `product="MIS"` unconditionally, + explanatory comment | revert the line |
| `tests/test_tactical_intraday_product.py` | new, 8 regression tests | delete the file |

**Nothing else was modified.** No thresholds, no prompts, no gates, no stop geometry, no cash
buffer, no news weighting, no scoring. `PAPER_MODE` untouched.

---

## Recommended next, in order

1. **B3 follow-up** — decouple the NSE fetch from section 1's inline LLM loop. This is the single
   largest information loss in the system: every in-session filing, every day.
2. **E1 follow-up** — align `ENTRY_CUTOFF` with the 15:10 squareoff (needs approval; it touches
   origination timing).
3. **E2 confirmation** — log the bar timestamp `detect_exhaustion` consumes, then add the test.
4. **C2 instrumentation** — record a rejection reason for a named ticker that never becomes a
   decision.
5. **Phase 2** — the historical edge test. Nothing in Phase 1 changes the standing conclusion that
   no origination path has yet demonstrated a cost-adjusted edge.

Phase 1's fixes make the system *safe and observable*. They do not make it profitable, and none
of them was expected to.
