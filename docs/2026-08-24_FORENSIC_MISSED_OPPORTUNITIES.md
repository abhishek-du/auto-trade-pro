# Forensic post-mortem — why AutoTrade Pro converted 1 of 107 movers

**Session:** 2026-08-24 (Monday) · **Written:** 2026-08-24 evening IST
**Ground truth:** direct Kite Connect scan of the close, 15:33 IST
**System evidence:** production Postgres + source code. No code was changed producing this.

Artifact version: https://claude.ai/code/artifact/e5559e4b-2ed1-45db-a539-5be74107b53a

---

## Executive summary

The system was not blind. It scored **42,835 symbol-cycles** today and correctly
flagged six of the day's biggest winners as BUY or STRONG_BUY. It made **zero**
decisions from any of them.

The failure is not intelligence. It is that the layer which knows has no
authority, and the layer with authority waits for a confirmation that arrives
after the move is over.

| Metric | Value |
|---|---|
| Big movers (\|move\| ≥ 5%, ≥ ₹1cr turnover) | **107** |
| Trades opened | **11** (all BUY, zero short) |
| Movers actually traded | **1** of 107 |
| Capture ratio (realised ÷ available) | **−32.2%** |
| Hub decisions from 29 cycles | **0** |
| Agent BUY/SELL vs SKIP, last 7 days | **7** vs **2,687** |

Four failures compound. None is the AI being stupid.

1. **The scoring engine has no trigger.** Master Intelligence scored LTFOODS,
   KRBL, SHANTIGEAR, BALUFORGE, QUADFUTURE and RATNAMANI as BUY/STRONG_BUY. All
   six closed between +7.3% and +18.3%. Across 29 hub cycles, `decisions_made = 0`.
   By design — `CLAUDE.md` §5b: the Hub "does not originate trades".
2. **The entry gate is anti-alpha.** A long needs the stock already **+1.5%** on
   the day. Across 14 sessions and 7,688 entries that rule turns a median
   **+0.060%** into **−0.186%** and drops the win rate from 52.1% to 45.9%.
   Monotonic: higher threshold, worse result.
3. **News is 6% of the intraday score.** The stated philosophy is news-as-catalyst,
   technicals-as-timing-filter. The intraday weight vector is
   `tech 0.59 · volume 0.29 · news 0.06`.
4. **The tactical pipeline was dead for two-thirds of the session.** First
   tactical signal: **13:30 IST**. Market opened 09:15.

Consequence: a book that deployed **74% of capital between 13:42 and 14:30 IST**,
buying stocks whose moves were already finished, then blocked its own remaining
signals on a cash buffer for the rest of the day.

---

## 1. The opportunity funnel

Ground truth: 10,086 NSE EQ instruments scanned, 5,224 with valid data,
**1,796 liquid names** above ₹1cr turnover, of which **107 moved ≥5%**.

```
STAGE                          of 107 movers      all liquid (1,796)
market truth (|move| >= 5%)     107   100%
has 1m candles today             77    72%          1,573   88%
in hub_universe                  67    63%          1,521   85%
scored by Master Intelligence    63    59%          1,458   81%
in market_shortlist              27    25%            244   14%
named in today's news            20    19%            135    8%
tactical signal generated        10     9%             76    4%
causal_event created              5     5%             93    5%
agent_decision recorded           5     5%             78    4%
TRADE OPENED                      1     1%             11    1%
```

**The cliff is not at data and not at scoring.** 63 of 107 movers were scored —
the system looked directly at them. The collapse is between scoring and any
actionable output: 63 scored → 10 signals → 5 events → 1 trade.

### Stock-by-stock trace

| Stock | Day % | Turnover | News | Events | Hub scores | Signals | Decisions | Traded |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| LALITHAA | +23.60 | ₹2,459cr | 7 | 0 | 0 | 0 | 0 | no |
| SHANTIGEAR | +18.26 | ₹654cr | 2 | 0 | 26 | 0 | 0 | no |
| RATNAMANI | +14.55 | ₹244cr | 2 | 0 | 26 | 0 | 0 | no |
| BALUFORGE | +13.83 | ₹982cr | 0 | 0 | 26 | 6 | 0 | no |
| QUADFUTURE | +10.35 | ₹1,067cr | 1 | 0 | 26 | 4 | 0 | no |
| VMM | +9.74 | ₹1,754cr | 6 | 0 | 26 | 0 | 0 | no |
| TVSSCS | +9.51 | ₹537cr | 4 | 0 | 26 | 0 | 0 | no |
| LTFOODS | +7.34 | ₹1,923cr | 2 | 0 | 26 | 0 | 0 | no |
| KRBL | +7.58 | ₹232cr | 1 | 0 | 26 | 0 | 0 | no |
| BLS | −10.98 | ₹717cr | 4 | 0 | 26 | 0 | 0 | no |
| AEGISLOG | −5.39 | ₹125cr | 0 | 0 | 26 | 6 | 0 | **BOUGHT** |

BLS carried 4 news items on the day of its visa-fraud disclosure and 26 hub
scores, and produced no event, no signal, no decision. AEGISLOG is the one that
was traded — and it was *bought* on the day it fell 5.39%.

---

## 2. Root cause 1 — the Hub knows and cannot act · CONFIRMED

**Evidence:** `hub_cycle_logs`, 24 Aug: 29 cycles, 42,835 symbols scored,
avg 566s per cycle, spanning 08:44–16:30 IST, `SUM(decisions_made) = 0`.

**What it threw away:**

| Stock | Master | Tech | News | Sector | Signal | Actual close | Verdict |
|---|---:|---:|---:|---:|---|---:|---|
| QUADFUTURE | 80.8 | 100.0 | 92.0 | 40.0 | STRONG_BUY | +10.35% | right |
| SHANTIGEAR | 70.3 | 100.0 | 31.7 | 40.0 | STRONG_BUY | +18.26% | right |
| KRBL | 65.8 | 100.0 | 90.6 | −25.0 | STRONG_BUY | +7.58% | right |
| LTFOODS | 63.5 | 100.0 | 90.6 | −25.0 | STRONG_BUY | +7.34% | right |
| BALUFORGE | 57.2 | 100.0 | 20.0 | 40.0 | STRONG_BUY | +13.83% | right |
| RATNAMANI | 27.5 | −43.0 | 90.4 | 50.0 | BUY | +14.55% | right |
| BALRAMCHIN | 62.1 | 100.0 | 11.2 | −25.0 | STRONG_BUY | −4.64% | **wrong** |
| AEGISLOG | 55.5 | 100.0 | 10.0 | 50.0 | STRONG_BUY | −5.39% | **wrong** |
| BLS | 52.4 | 100.0 | 19.4 | 40.0 | STRONG_BUY | −10.98% | **wrong** |
| VMM | 7.6 | −100.0 | 80.7 | 0.0 | NEUTRAL | +9.74% | missed |
| TVSSCS | −14.3 | −74.0 | 14.3 | 40.0 | NEUTRAL | +9.51% | missed |

### Honest reading

The Hub is **not** an oracle: right on six, wrong on three, neutral on two big
winners — roughly 55% on the extremes.

Critically, **STRONG_BUY fired on 17,106 of 42,835 scores (40%)**. A label that
applies to two names in five carries almost no information. Wiring the Hub
directly to execution as it stands would not have made money — it would have
bought BLS on the day it fell 11%.

The finding is narrower and more useful than "connect the Hub": the system's
best-informed layer is discarded wholesale, and the layer that does trade cannot
see what it saw.

---

## 3. Root cause 2 — the confirmation gate destroys the edge it guards · CONFIRMED

**Where:** `engine/entry_confirmation.py:29` → `MIN_DAY_CHANGE_PCT = 1.5`

A BUY is refused unless the stock is already up 1.5% on the day. Added
2026-07-28 after seven consecutive stopped-out trades all showed near-zero MFE —
a real problem, correctly diagnosed. The fix inverts the cure.

This was the largest single LLM rejection reason today: **324 of 678 skips**.

### 14-session counterfactual

| Rule (hold to session close) | Sessions | Entries | Median % | Mean % | Win rate | Days +ve |
|---|---:|---:|---:|---:|---:|---:|
| LONG, no gate | 14 | 14,629 | **+0.060** | +0.513 | 52.1% | 14/14 |
| LONG after +1.0% | 14 | 9,790 | −0.157 | +0.266 | 46.0% | 12/14 |
| **LONG after +1.5% ← LIVE** | 14 | **7,688** | **−0.186** | +0.254 | **45.9%** | 13/14 |
| LONG after +3.0% | 14 | 3,768 | −0.048 | +0.328 | 48.8% | 13/14 |

**Method:** 14 consecutive sessions; all NSE names above ₹5cr traded value in
that session; prior close reconstructed from each symbol's own last 1m bar of
the previous session; entry fires on the first 1m bar whose extreme crosses the
threshold; exit is the session's last 1m close. No selection on outcome —
winners, losers and flat names all included.

### What this proves, and what it does not

The gate is **monotonically harmful** on median and win rate. Removing it beats
every gated variant and is positive on 14/14 sessions. Mean return roughly halves
with the gate on (+0.513% → +0.254%).

It does **not** prove "buy everything" is a strategy — the no-gate row is
essentially market beta over a rising fortnight. The correct conclusion is
narrower: **this gate adds no selectivity, only lateness.** It rejects 6,941 of
14,629 candidates and the ones it keeps are worse than the ones it rejects.

Today: median crossing time **6 minutes** after the open (09:21 IST); median
move remaining after that point **−0.22%** to close; on **55%** of names that
closed up, buying at the confirmation moment was a losing entry.

---

## 4. Root cause 3 — news is 6% of the intraday score · CONFIRMED

**Where:** `engine/intelligence_hub.py:1052-1077`

```
es_w  Event Swing        news 0.40 · tech 0.30 · sector 0.10 · macro 0.10 · volume 0.10
ts_w  Technical Swing    tech 0.45 · news 0.20 · volume 0.15 · sector 0.10 · macro 0.10
id_w  Intraday Momentum  tech 0.59 · volume 0.29 · news 0.06 · macro 0.06   <-- intraday default
pos_w Positional         fundamentals 0.40 · earnings 0.20 · tech 0.20 · macro 0.10 · sector 0.10
```

Reaching the news-led profile requires (line 1096):

```python
if news_score >= 85 and technical_score >= 60:
    strategy_selected = "Event Swing"
```

Fail either and, outside swing mode, the score falls through to Intraday
Momentum where **news carries six percent**.

### The mechanism, in three of today's stocks

- **RATNAMANI** — news 90.4 (clears 85) but tech −43.0 (fails 60). Dropped out of
  Event Swing. Master 27.5. Closed **+14.55%**.
- **VMM** — news 80.7, tech −100.0. Master 7.6, NEUTRAL. Closed **+9.74%**.
- **TVSSCS** — tech −74.0 dragged master to −14.3, NEUTRAL. Closed **+9.51%**.

**The `AND` is the defect.** A stock that has just received strong news very
often has a *weak* technical profile — that is what a breakout out of a base
looks like from behind. Requiring both strong simultaneously selects for stocks
that have already moved: the same trap as the confirmation gate, expressed in
the scoring layer.

---

## 5. Root cause 4 — late entries and a book that filled in 48 minutes · CONFIRMED

Tactical signals were produced in exactly two hours of the session:

```
08:00 UTC = 13:30 IST   114 signals    8 executed
09:00 UTC = 14:30 IST    93 signals    1 executed
03:45-08:00 UTC (09:15-13:30 IST)  ZERO signals - 4h15m of a 6h15m session
```

Ten of eleven entries landed after 13:27 IST. Capital consequence:

```
13:42 IST  deployed  Rs 124k   FINEORG + SURYODAY
13:49 IST  deployed  Rs 272k   DEVYANI + REDINGTON + GRAPHITE + DIVISLAB
13:59 IST  deployed  Rs 463k   AEGISLOG      <-- past the 90% line
14:05 IST  deployed  Rs 493k   ENRIN
14:30 IST  deployed  Rs 493k   CGCL
           -> every later signal blocked: "Cash buffer would breach the 10% reserve"
```

**121 of 207 tactical signals (58%)** died on that cash buffer. It is **not a
bug** — the arithmetic is correct and the book genuinely was 98% deployed. It is
the downstream symptom of an entry burst crammed into the last two hours.

### Trade geometry is broken at entry, not at exit

| Trade | Entry IST | MFE after entry | MAE | Realised | Stock's full day | Status |
|---|---:|---:|---:|---:|---:|---|
| REDINGTON | 13:49 | **−0.09%** | −1.73% | −1.40% | **+4.15%** | STOPPED |
| DEVYANI | 13:49 | +0.09% | −0.23% | 0.00% | +3.27% | OPEN |
| CGCL | 14:30 | +2.62% | −0.58% | +1.37% | +4.59% | STOPPED |
| FINEORG | 13:42 | +0.44% | −0.82% | −0.53% | +2.28% | CLOSED |
| AEGISLOG | 13:59 | +0.35% | −0.47% | 0.00% | **−5.39%** | OPEN |
| ENRIN | 14:05 | +0.14% | −0.68% | −0.30% | +1.46% | CLOSED |

REDINGTON closed **+4.15%** on the day. After the 13:49 entry its maximum
favourable excursion was **−0.09%** — it never traded above our fill. The entire
move happened before we bought.

Across today's book: sum of best-available move **+6.75%**, sum realised
**−2.17%** → **capture ratio −32.2%**.

Median stop distance **1.58%** from entry against a median favourable excursion
after entry of roughly **+0.35%**. A stop four times wider than the available
move is not a stop — it is a coin flip with a fee.

---

## 6. What is NOT broken

Hypotheses investigated and rejected. These matter as much as the confirmed
causes, because each is a place not to spend effort.

| Hypothesis | Evidence | Verdict |
|---|---|---|
| Stops cut winners | 20 closed trades / 14 sessions. Median realised −0.28%; median if held to session close −0.06%. Only 2 of 8 losers would have closed green. | **RULED OUT** |
| Price data is wrong | 1,551 liquid symbols vs Kite close: median error 0.144%, 97.9% within 1%, no directional bias. | **RULED OUT** |
| Daily candles missing | Series-lag test vs Kite across three writers: all at lag +1 (0.000% median error for the full-universe path). Bars exist; the date label is one day early because 1d timestamps are IST midnight stored as naive UTC. | **RULED OUT** |
| Execution / broker layer | 9 of 9 offered signals returned EXECUTED_PAPER. Zero rejections, zero partial fills. | **RULED OUT** |
| Scoring never ran | 29 cycles, 42,835 scores, spanning the whole session. | **RULED OUT** |
| News never arrived | 385 items crawled; median publish→crawl 6.7 min. BLS had 4 items, LALITHAA 7. | **RULED OUT** |
| News latency tail | Mean 98.2 min vs 6.7 min median; p90 = 76 min; 213 of 386 items over 5 min. Real, but not what killed today. | CONTRIBUTING |
| Universe too small | 67 of 107 movers in universe, 63 scored. Secondary loss (40 names), not primary. | CONTRIBUTING |

---

## 7. The event classifier — worse than a coin flip · STRONGLY SUPPORTED

Today's `causal_events` tagged 132 symbols bullish and 55 bearish. Against the
actual close:

| Measure | Value |
|---|---|
| Bullish-tagged that ROSE | **41.9%** (31 rose · 43 fell) |
| Bearish-tagged that FELL | **39.3%** (11 fell · 17 rose) |
| Events touching a big mover | 5 of 107 |
| Agent verdicts, 7 days | 4 BUY / 3 SELL vs **2,687 SKIP** |

Both hit rates sit below 50% on a day when 803 of 1,689 liquid names rose — a
blind coin flip scores near 48%. The classifier is not merely uninformative; on
this sample it is *anti-informative*.

**This is one session, so it is STRONGLY SUPPORTED, not CONFIRMED.** It needs the
same 14-session treatment the confirmation gate received before anyone inverts or
rewires it. But it is the highest-value thing left to measure, because the entire
news-only architecture rests on this label being right.

---

## 8. Root cause matrix

| # | Root cause | Evidence | Impact | Class | Confidence |
|---|---|---|---|---|---|
| 1 | Hub has no trigger | 29 cycles, `decisions_made=0`. Six of the day's biggest winners scored BUY/STRONG_BUY. | 63 of 107 movers seen, 0 acted on | POLICY | CONFIRMED |
| 2 | +1.5% confirmation gate | 14 sessions / 7,688 entries: median −0.186% vs +0.060% ungated; win 45.9% vs 52.1%. | 324 of 678 LLM skips | POLICY | CONFIRMED |
| 3 | News weighted 0.06 intraday | `intelligence_hub.py:1077`; Event-Swing needs news≥85 AND tech≥60. RATNAMANI news 90.4, tech −43 → 27.5, closed +14.55%. | contract inverted | INTELLIGENCE | CONFIRMED |
| 4 | Tactical dead 09:15–13:30 | First tactical signal 13:30 IST. | ~68% of session | INFRA | CONFIRMED |
| 5 | Late burst fills the book | ₹124k→₹493k deployed 13:42–14:30; 121 of 207 signals then blocked. | 58% of signals | POLICY | CONFIRMED |
| 6 | Stop ≫ available move | Median stop 1.58%; median MFE after entry ≈ +0.35%. Capture −32.2%. | all 11 trades | POLICY | CONFIRMED |
| 7 | Event direction anti-informative | Bullish 41.9% right, bearish 39.3%, base rate 48%. | 187 tagged symbols | INTELLIGENCE | STRONGLY SUPPORTED |
| 8 | STRONG_BUY not selective | 17,106 of 42,835 scores (40%). | label uninformative | INTELLIGENCE | CONFIRMED |
| 9 | No short capability in practice | 188 BUY vs 19 SELL signals; 11 of 11 trades long on a day with 14 names down >5%. | one side of the book | POLICY | CONFIRMED |

---

## 9. Final verdict

| Question | Answer | Why |
|---|---|---|
| Is the strategy broken? | **PARTIALLY** | The hypothesis is sound. The gates around it invert it. |
| Is the data broken? | **NO** | 97.9% within 1% of Kite. |
| Is live tracking broken? | **PARTIALLY** | 1m fine; 5m/1h were 40–75 min stale until today's fix. |
| Is the AI context broken? | **YES** | News carries 6% of the intraday score it is supposed to lead. |
| Is scoring broken? | **YES** | 40% STRONG_BUY, and no path from a score to a trade. |
| Is risk management broken? | **PARTIALLY** | Arithmetic correct. Stop sizing unrelated to available move. |
| Is execution broken? | **NO** | 9 of 9 offered signals executed cleanly. |
| Is infrastructure broken? | **YES** | Tactical produced nothing for the first 4h15m. |

### The real problem

**The bottleneck is not the model. It is the set of rules wrapped around the model.**

Category **B (intelligence) and C (policy)** — not A (data) or D (execution).

The information arrives, is stored accurately, and is scored correctly often
enough to matter. It then passes through three filters that each independently
select for *lateness*:

1. a scoring engine with no trigger,
2. an entry gate that demands the move already happened,
3. a weight vector that lets a weak chart veto strong news.

What survives is a stock that has finished moving, bought with a stop four times
wider than anything left on the table.

**A smarter LLM fixes none of this.** The LLM already said TAKE 18 times today
and an execution gate blocked it every time.

---

## 10. Path forward

Nothing below is implemented. Each carries a measurement that decides whether it stays.

### P0 — actively destroying performance

**1. Replace the +1.5% gate with a directional-agreement test**
- *Where:* `engine/entry_confirmation.py:29`
- *Now:* refuse unless the day move already exceeds ±1.5%
- *Instead:* require only that price is not moving *against* the thesis — for a
  BUY, above VWAP or the opening range, with no minimum magnitude
- *Why:* the 14-session sweep shows every positive threshold is worse than none
- *Risk:* this gate exists because 7 trades stopped out with zero MFE. Removing
  it without a replacement re-opens that; the replacement must still reject
  "price falling while we buy"
- *Measure:* median MFE after entry, over 20 sessions, must rise above ≈+0.35%

**2. Size the stop from the available move, not ATR alone**
- *Where:* `engine/risk_manager.py::compute_trade_levels`
- *Now:* median stop 1.58% against median +0.35% of favourable movement
- *Instead:* refuse the trade when measured recent favourable excursion for that
  setup is smaller than the stop
- *Measure:* capture ratio, currently −32.2%, computed nightly

**3. Make the tactical scan cover the whole session**
- *Where:* the scan-queue worker deployed 24 Aug
- *Now:* first signal 13:30 IST · *Target:* first signal within 15 min of open
- *Measure:* signals per hour, flat across 09:15–15:30

### P1 — materially reducing alpha

**4. Change the Event-Swing gate from AND to OR-with-veto**
- *Where:* `engine/intelligence_hub.py:1096`
- *Now:* `news >= 85 AND tech >= 60`
- *Instead:* strong news alone selects news-led weights; technicals veto only on
  active deterioration, not mere weakness
- *Risk:* largest behavioural change here; widens the funnel. Pair with 1 and 2
  or it just produces more late trades

**5. Measure the event classifier over 14 sessions before trusting or rewiring it**
- Today's 41.9% / 39.3% is one session. Run the same multi-session method used
  for the confirmation gate. If it holds, the news-only architecture rests on a
  worse-than-random label and this becomes P0 ahead of everything else.

**6. Spread the capital budget across the session**
- 74% of equity committed in 48 minutes. Cap deployment per hour so a late burst
  cannot consume the day's budget.

### P2 — quality

**7. Recalibrate the STRONG_BUY threshold** — 40% of scores carry the strongest
label. Set the cut at top decile, not top two-fifths.

**8. Close the news-latency tail** — median 6.7 min is fine; p90 at 76 min is not.

### P3 — later

**9. Decide whether shorting is a real capability** — 188 BUY vs 19 SELL signals,
and no short taken on a day with 14 names down more than 5%. Either make the
short path work or stop counting it as coverage.

---

## Method

- **Market truth** — direct Kite Connect scan at 15:33 IST: 10,086 NSE EQ
  instruments, 5,224 with valid previous close and traded price. Nothing in the
  market-truth column comes from the system under investigation.
- **System behaviour** — production Postgres: `candles`, `news_items`,
  `causal_events`, `agent_decisions`, `tactical_signals`,
  `master_intelligence_scores`, `hub_cycle_logs`, `paper_trades`,
  `open_positions`, `virtual_wallet`. Every count is a query result, not an estimate.
- **14-session counterfactual** — each symbol's prior close reconstructed from its
  own last 1m bar of the previous session; liquidity judged from that session's
  own traded value. No figure used in the entry decision is knowable only in
  hindsight.

### Correction made during this investigation

The cash-buffer rejections were first read as an arithmetic fault, after
comparing a mid-day rejection string ("deployed ₹492,979") against the
end-of-day book (₹296,593). Reconstructing the deployed-capital timeline showed
the arithmetic was correct and the book genuinely was 98% deployed at that
moment. The finding changed from "a bug" to "a symptom of burst deployment",
which is what appears above.

---

*This is a systems analysis, not investment advice.*
