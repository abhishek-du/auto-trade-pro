# PHASE 20 — OPPORTUNITY DISCOVERY + EXIT FAILURE ROOT CAUSE

**Date:** 2026-08-26 · **Mode:** READ-ONLY. No production change, no order, no
strategy change.

---

## Executive summary

Two findings, and the second **materially corrects Phase 19B**.

**1. The AI never sees a momentum candidate. By design.**
`agent_decisions` = **0** on all 14 of today's biggest movers — including
GENESYS, which produced 13 tactical signals scoring 90–98.8. The tactical
pipeline and the LLM pipeline are **disjoint**: tactical signals go
`scan → score → rank → capital → execute` and never touch the AI. The AI is
reached only from the news path. "11 movers never evaluated by the AI" is
therefore not a defect in the AI — **no momentum candidate has ever been routed
to it.** → **CONFIRMED**

**2. EXHAUSTION did not cost today's money.**
Measured against the honest benchmark — the same trade simply held to close, no
look-ahead — EXHAUSTION is **net −₹107 across 14 exits**. It saved ₹1,896 and
cost ₹2,003. The exits that actually cost money were **T1_REVERSAL_EXIT (−₹1,079
on 3 trades)** and **MIS_SQUAREOFF (−₹781 on 7)**.

Phase 19B measured the giveback from the 12:00 high-water mark, which conflates
the exit decision with subsequent price drift. Both numbers are real, but the
hold-to-close benchmark is the one that isolates the decision. **Phase 19B's
emphasis on EXHAUSTION as the cause is withdrawn.**

---

## PART 1 & 5 — The candidate funnel, as actually implemented

```
hub_universe                                    2,560 symbols
        │  engine/tactical_data_fetcher.py:425  get_f1_universe()
        │  turnover_cr >= 5.0  AND  price >= 20  AND  rank <= 1500
        ▼
F1 universe                                     ~1,476 symbols
        │  engine/tactical_executor.py:156,245  get_prices_batch(universe)
        ▼
scanned                                         *** "scanned 0 of 1476" x16 today ***
        │  rules fire
        ▼
raw signals                                     ~300-345 per scan
        │  engine/tactical_scoring.py  score_and_filter
        ▼
score > 50.0                                    ~334 per scan
        │  "keeping 40"
        ▼
top 40                                          40
        │  engine/tactical_executor.py:200  rank_signals(scored, top_n=TACTICAL_TOP_N)
        │  utils/config.py:421  TACTICAL_TOP_N = 15
        ▼
top 15 persisted                                <= 15 per scan
        │                                       325 signals / 129 symbols, whole day
        ▼
engine/risk_manager.py  validate_signal
        │  R:R gate, sector cap, concurrency cap, capital
        ▼
executed                                        20 positions opened today
        │
        ▼
  *** the AI is NOT in this path at all ***
```

**Answer to PART 5:** the architecture is **B (ranked universe scan)** with a
hard **top-N=15 persistence cut**. It is not news-triggered, not
momentum-triggered into the AI, and not hybrid. A stock can rise 19% and receive
zero AI evaluations because **the momentum path has no route to the AI**.

Of ~334 signals that clear the score threshold each scan, **15 survive** — a
95.5% discard rate at a stage that is a ranking convenience, not a risk control.

---

## PART 2 — The 14 movers, first failing stage

Deterministic (`.NS` only), against the **actual** config
(`TACTICAL_F1_MIN_TURNOVER_CR=5.0`, `TACTICAL_F1_MAX_SYMBOLS=1500` — note the
docstring at `tactical_data_fetcher.py:439` still says 500):

| Symbol | turnover cr | rank | signals | AI | **first failing stage** |
|---|---:|---:|---:|---:|---|
| RAMBHAJO | 4.99 | 1505 | 0 | 0 | **UNIVERSE** — turnover floor |
| VOEPL | 4.80 | 1527 | 0 | 0 | **UNIVERSE** — turnover floor |
| EEPL-SM | 0.00 | 2493 | 0 | 0 | **UNIVERSE** — turnover floor |
| ARIES | 3.14 | 1742 | 0 | 0 | **UNIVERSE** — turnover floor |
| WEL | 24.68 | 693 | 0 | 0 | **SCAN/SCORING** |
| RGL | 12.52 | 1009 | 0 | 0 | **SCAN/SCORING** |
| INDSWFTLAB | 11.74 | 1052 | 0 | 0 | **SCAN/SCORING** |
| TVSSRICHAK | 33.41 | 558 | 0 | 0 | **SCAN/SCORING** |
| OMAXE | 16.19 | 885 | 0 | 0 | **SCAN/SCORING** |
| AASTHA | 16.40 | 876 | 0 | 0 | **SCAN/SCORING** |
| SBFC | 14.62 | 934 | 0 | 0 | **SCAN/SCORING** |
| IDBI | 62.76 | 379 | 0 | 0 | **SCAN/SCORING** |
| GENESYS | 17.49 | 847 | 13 | 0 | **RISK — R:R gate**, then capital |
| MILKYMIST | 508.91 | 26 | 1 | 0 | signalled, not executed |

**4 of 14** die at the universe filter. **8 of 14 are in the universe and
scanned, and produced no signal at all.** Only 2 produced signals.

**Correction to my own working:** an intermediate table in this session used the
docstring's `cap=500` and a non-deterministic `symbol IN ('X.NS','X.BO') LIMIT 1`
lookup, which returned `.BO` rows for some symbols and mislabelled seven names.
Both errors are corrected above; the table here is the one to use.

---

## PART 6 — GENESYS, and a correction to Phase 19B

Phase 19B classified GENESYS as **CAPITAL BLOCKED (CONFIRMED)**. That is only
partly right. All 13 signals:

| # | entry | score | rejection |
|---|---:|---:|---|
| 1–6 | 236.50 → 246.60 | 92.5–96.6 | **R:R ratio 0.08–0.10 below minimum 1.2** |
| 7–11 | 246.25 → 249.10 | 90.0–98.8 | Cash buffer (₹1,242–1,478) |
| 12–13 | 249.10 → 250.40 | 90.2 | **R:R ratio 0.08** |

**8 of 13 were refused by the R:R gate, not capital — and the R:R refusals came
first.** Capital only became the binding constraint after the book filled. So
the **first failure for GENESYS is the R:R gate**, at scores of 92.5–96.6.

An R:R of 0.08–0.10 means the stop sat ~12× further from entry than the target.
On a stock that ran +10.4%, that is the stop-and-target geometry rejecting a
correctly-identified move. This is the same **"R:R gate ordering defect"** the
prior Stage 3 audit raised. → **PARTIALLY SUPPORTED** — the arithmetic is
confirmed; whether the ordering is a defect or intended was not re-derived here.

**PART 6 verdict:** for the other 13 movers, **capital is RULED OUT** as first
failure — there was no signal to block.

---

## PART 7 & 8 — Exit forensic

### The honest benchmark: actual exit vs holding the same trade to close

No look-ahead. Marks from our own 1m candles at 15:29.

| Exit reason | n | realised | if held to close | **net** | saved | cost |
|---|---:|---:|---:|---:|---:|---:|
| EXHAUSTION | 14 | −3,463 | −3,356 | **−107** | 1,896 | 2,003 |
| STOP_LOSS | 7 | 1,819 | 1,907 | **−88** | 996 | 1,084 |
| MIS_SQUAREOFF | 7 | −847 | −67 | **−781** | 99 | 880 |
| TAKE_PROFIT | 3 | 4,002 | 3,409 | **+593** | 593 | 0 |
| T1_REVERSAL_EXIT | 3 | 1,282 | 2,361 | **−1,079** | 0 | 1,079 |
| **ALL** | **34** | **2,793** | **4,255** | **−1,462** | **3,584** | **5,046** |

**EXHAUSTION is approximately neutral.** Its −₹3,463 realised looks damning, but
those positions were worth −₹3,356 held to close: they were losers either way.
EXHAUSTION saved money on 5 (COSMOFIRST alone +₹1,227 avoided) and cost money on
9.

**The two exits that actually destroyed value:**
- **T1_REVERSAL_EXIT: −₹1,079 on 3 trades.** Worst per-trade cost of any family,
  and it cost on **all three**. Highest-leverage exit finding of the day.
- **MIS_SQUAREOFF: −₹781 on 7 trades.** Forced end-of-day closure of positions
  that were near flat (−₹67 held) and were closed at −₹847.

**TAKE_PROFIT is the only family that beat holding (+₹593).**

### Per-trade classification (vs a best-hold oracle — upper bound, not achievable)

EXHAUSTION: 9 PREMATURE / 5 marginal, ₹2,691 forgone.
STOP_LOSS: 4 PREMATURE / 3 marginal, ₹1,894 forgone.

That oracle benchmark is **not** what a rule could capture; it is reported only
to bound the opportunity. The hold-to-close table above is the decision-relevant
one.

### PART 9 — Stop losses

Against hold-to-close, STOP_LOSS is **net −₹88 — appropriate (E)**. It prevented
larger losses on DEVYANI (+₹963 vs holding) and CANBK, and cut retracements on
RATNAVEER (−₹559) and WELCORP (−₹342). No evidence of stops caused by stale data
or late entries was found. → **RULED OUT as a material cause today.**

---

## PART 11 — BUG-1

**Verdict: STRATEGY CHANGE. Not a safe bug fix. CONFIRMED.**

`tasks/india_tasks.py:684` reads `settings` while `:706` imports it bare inside
the same function, making it function-local for all of `_india_trade_loop`
(498–1437). 442 failed cycles today; exits run, entries do not.

What a fix would activate:

```
:683-684  _NEWS_ONLY_BLOCKS_HUB_ENTRIES = bool(getattr(settings, "NEWS_ONLY_BLOCKS_HUB_ENTRIES", True))
:695      if _NEWS_ONLY_BLOCKS_HUB_ENTRIES or not is_entry_window or not _strategy_on:  -> skip entries
```

- `utils/config.py:308` default: **True** (blocks)
- **`.env:195`: `NEWS_ONLY_BLOCKS_HUB_ENTRIES=false`** ← what would actually load

So repairing the crash makes the flag resolve to **False**, and the entry gate
**opens**.

**Historical evidence that this path has never traded:** every
`strategy_family` ever recorded in `paper_trades` is `TACTICAL` (61),
`DIRECT_NEWS` (6), `EVENT_DRIVEN` (3), `NULL` (3). There is no hub /
india_trade_loop-originated family at all.

Fixing BUG-1 would therefore switch on an entry path with **zero recorded trades
and no measured expectancy**, on a flag already set to "off the brakes". That is
a strategy decision. **No deployment. Not bundled.**

---

## PART 12 & 13 — Architecture (design only, not implemented)

### What the evidence says is wrong with the current shape

1. **Top-N=15 discards ~95% of qualifying signals** at a ranking step
   (`tactical_executor.py:200`), not a risk step.
2. **The AI is unreachable from momentum.** Two disjoint pipelines.
3. **"scanned 0 of 1476" occurred 16 times** — whole scans producing nothing.
4. **The turnover floor uses the average, not today's** — the code documents this
   at `tactical_data_fetcher.py:442-447`. Four of today's movers died there.

### Proposed shape

```
FAST MARKET DATA LAYER        <- Phase 18 delta window already deployed
        ↓
MOMENTUM / BREAKOUT DETECTOR  <- cheap, arithmetic, whole universe
        ↓
NEWS / EVENT DETECTOR         <- existing news path
        ↓
CANDIDATE QUEUE               <- the missing component: one queue, both sources
        ↓
TECHNICAL VALIDATOR
        ↓
MASTER SCORE
        ↓
AI                            <- reached by BOTH sources, bounded per cycle
        ↓
CAPITAL → EXECUTION
```

The single structural change is the **candidate queue**: momentum and news both
feed it, and the AI consumes from it under a fixed per-cycle budget. That
satisfies the constraint that the AI must never evaluate the whole universe.

### Tiers — measured facts vs proposed numbers, kept separate

**Measured today:** open positions 9–11 · F1 universe ~1,476 · full-universe
candle sync ~2.1 min projected post-Phase-18 · scan cadence 3 min · persisted
signals 325/day across 129 symbols.

**Proposed (not derived from an experiment — these are starting points to be
validated):**

| Tier | Membership | Scan freq | Max staleness | Candidate trigger |
|---|---|---|---|---|
| 1 | open positions + fresh news (<60 min) + top-200 rank | 60s | 90s | any rule fire |
| 2 | rest of F1 universe (turnover ≥ 5cr) | 180s | 5 min | rule fire + score ≥ 50 |
| 3 | long tail below the floor | 600s | 15 min | rule fire + score ≥ 70 |

---

## PART 14 — Root-cause matrix

| # | First failure | Evidence | Impact | Confidence | Safe fix? | Strategy change? | Prod risk |
|---|---|---|---|---|---|---|---|
| 1 | **AI unreachable from momentum** | `agent_decisions`=0 on all 14 movers incl. GENESYS (13 signals) | all momentum candidates | **CONFIRMED** | no — new routing | **YES** | high |
| 2 | **top-N=15 cut** | `TACTICAL_TOP_N=15`; ~334 qualify, 15 persist | ~95% of signals | **CONFIRMED** | config-only | **YES** | medium |
| 3 | **Turnover floor (average, not today's)** | 4 of 14 movers below 5.0cr; documented at `:442-447` | 4 of 14 today | **CONFIRMED** | config-only | **YES** | medium |
| 4 | **Scan produced nothing** | "scanned 0 of 1476" ×16 | unknown | **CONFIRMED** (occurrence) / **UNPROVEN** (cause) | investigate first | no | — |
| 5 | **R:R gate on GENESYS** | 8 of 13 refused at R:R 0.08–0.10 vs min 1.2, scores 92–97 | 1 mover +10.4% | **CONFIRMED** | no | **YES** | medium |
| 6 | **T1_REVERSAL_EXIT** | −₹1,079 on 3 trades vs hold-to-close; cost on all three | largest exit cost | **CONFIRMED** | no | **YES** | medium |
| 7 | **MIS_SQUAREOFF** | −₹781 on 7 | second-largest | **CONFIRMED** | no — product/timing | **YES** | medium |
| 8 | EXHAUSTION | net −₹107 vs hold-to-close | ~neutral | **RULED OUT** as today's cause | — | — | — |
| 9 | STOP_LOSS | net −₹88 | ~neutral | **RULED OUT** | — | — | — |
| 10 | Capital | GENESYS 5 blocks, but R:R fired first | 0 clean cases | **PARTIALLY SUPPORTED** | — | — | — |
| 11 | BUG-1 | `.env` sets the flag false; no hub trade ever recorded | opens an untested path | **CONFIRMED** | **no** | **YES** | high |

**Note: every item that could increase trade count is a strategy change.** None
is a safe bug fix. That is the honest reading, and it is why nothing here is
proposed for deployment.

---

## Implementation, test, deployment, rollback (proposals only)

**Sequencing — investigate before any change:**

1. **Item 4 first, because it is the only pure defect.** Find why 16 scans
   returned "scanned 0 of 1476". Read-only; no strategy implication. This is the
   one item that could be a safe fix.
2. **Item 2 (top-N) as a measurement, not a change.** The data to test it already
   exists: replay today's discarded signals — every score and timestamp is in
   `tactical_signals` — and measure forward MFE of ranks 16–40 against ranks
   1–15. If the discarded band is indistinguishable, raising top-N buys nothing
   and the item closes.
3. **Item 6 (T1_REVERSAL_EXIT)** — 3 trades is too small to act on. Extend to the
   full history before proposing anything.
4. **Items 1, 3, 5, 11** — strategy decisions for the operator, each needing its
   own risk envelope. Not engineering calls.

**Test plan for any of the above:** replay against stored `tactical_signals` and
`candles`; define thresholds before looking at outcomes; full suite must stay at
the 1,764 / 27 / 7 / 5 baseline with zero new failures.

**Deployment:** one item per deployment, affected service only, live-verified in
the following session before the next.

**Rollback:** every item above is config or a single call site; `git checkout` of
the one file plus a restart of the one service. No schema change is implied by
any of them.

---

## What this phase did not establish

- **PART 3 (time-to-detection) and PART 4 (per-mover freshness at +3/+5/+7%):**
  **NOT PERFORMED.** For 12 of 14 movers there is no candidate timestamp to
  measure against — the pipeline never produced one, so the latency chain has no
  second point.
- **PART 10 (entry-quality distribution):** **NOT PERFORMED.**
- **Whether the 8 in-universe movers were scanned but scored below threshold, or
  never scanned at all:** **UNPROVEN.** Distinguishing them needs per-symbol scan
  logging that does not exist.
- **Whether the 4 sub-floor movers were tradeable in size:** **INSUFFICIENT
  EVIDENCE** — several are SME names that may be circuit-locked.

---

*All figures from the production database, our own 1m candles, and production
logs on 2026-08-26. Read-only throughout; nothing was modified and no order was
placed.*
