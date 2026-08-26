# Four-session forensic investigation — 2026-08-21 → 2026-08-26

**Window:** 2026-08-21 (Fri) · 2026-08-24 (Mon) · 2026-08-25 (Tue) complete
· 2026-08-26 (Wed) **partial**, 09:15–15:15 IST
**Evidence:** production Postgres · production logs · source at working tree ·
git history · **live Kite API** · **live Upstox API** · **live NSE official
corporate-announcements API**
**Constraint honoured:** read-only. No production code, configuration, service
or database row was modified during this investigation. Broker and exchange
calls were quotes and filings only — **no order was placed**.

---

## Headline, including the part that contradicts it

Three findings, in the order they must be read:

1. **The book is capital-locked within minutes of the open.** On 2026-08-25 it
   crossed the 90% deployment limit at **09:27:15 IST** and then rejected
   1,715 further signals that session. Over four sessions, **1,931 signals were
   refused for lack of capital** at a median of **99.6% deployed**.
   → **CONFIRMED.**

2. **The refused signals scored higher than the executed ones** — median
   composite 80.75 versus 71.73; 131 signals scored above the best trade
   actually taken that day. → **CONFIRMED on score.**

3. **But that score gap does not translate into an outcome gap.** Over 2,628
   signals with 90-minute forward candle data, the executed population's mean
   forward MFE is **+0.736%** and the rejected population's is **+0.744%**.
   The 9-point median score advantage produced a **0.008 percentage-point**
   difference in realised opportunity. → **The "we execute our worse signals"
   finding is real as a statement about scores and NOT SUPPORTED as a
   statement about money.**

The consequence matters more than either finding alone: the system is both
**out of capital by 09:30** and **unable to tell its good signals from its bad
ones** (Pearson r between composite score and forward MFE = **+0.106**, 95%
bootstrap CI [+0.068, +0.142], n = 2,628, 2,000 resamples, seed 7 — non-zero
but very weak, and the quartile progression is not monotonic).

Fixing the allocator to prefer higher scores would therefore, on this evidence,
have changed almost nothing. That is a materially different conclusion from the
one the score gap alone suggests, and it is the reason this report does not
recommend score-based preemption as a profit fix.

---

## 0. Investigation window

Sessions determined from the `candles` table, not the calendar:

| Label | Date | Day | Session present in DB | Status |
|---|---|---|---|---|
| DAY -4 | 2026-08-21 | Fri | 09:15 → 15:29 | complete |
| DAY -3 | 2026-08-24 | Mon | 09:15 → 15:29 | complete |
| DAY -2 | 2026-08-25 | Tue | 09:15 → 15:29 | complete |
| DAY -1 | 2026-08-26 | Wed | 09:15 → 15:01 | **partial / current** |

08-22 and 08-23 are a weekend; no bars exist. Aggregates use the three
**complete** sessions unless the partial day is named explicitly.

---

## 1. Broker cross-validation (§5) — performed

Live three-source comparison at 15:06:39 IST on 2026-08-26. Kite via
`kiteconnect`, Upstox via the repo's own `crawler.upstox_market.get_ltp`, DB
via the newest 1m bar.

| Symbol | KITE | UPSTOX | our DB | Kite−Upstox | Kite−DB | DB bar | lag |
|---|---:|---:|---:|---:|---:|---|---:|
| APOLLOPIPE | 649.15 | 649.15 | 649.80 | 0.00% | −0.10% | 15:01 | 6m |
| GENESYS | 247.00 | 247.50 | 254.85 | −0.20% | **−3.08%** | 14:51 | 16m |
| FACT | 862.80 | 863.70 | 862.90 | −0.10% | −0.01% | 15:01 | 6m |
| GIPCL | 201.74 | 201.59 | 201.30 | +0.07% | +0.22% | 14:51 | 16m |
| NITINSPIN | 627.45 | 627.45 | 628.00 | 0.00% | −0.09% | 14:51 | 16m |
| RPTECH | 758.00 | 758.00 | 761.75 | 0.00% | −0.49% | 15:01 | 6m |

**Conclusion: the two brokers agree with each other (0.00% to −0.20%). Our
database is not wrong — it is LATE**, and the divergence tracks the bar lag
exactly (6m lag → ≤0.5%; 16m lag → up to 3.08%).

### The finding this unlocked: median 1m candle lag is 16 minutes

Measured across the whole live universe at 15:06:59 IST:

| Metric | Value |
|---|---|
| Symbols with 1m bars in the session | 1,743 |
| **Median lag** | **16.0 min** |
| p75 / p95 | 16.0 min / 17.0 min |
| Max | 52.0 min |
| **> 10 min stale** | **1,046 / 1,743 (60%)** |
| > 30 min stale | 12 / 1,743 (1%) |

This is invisible from inside the database — the bars are internally
consistent, evenly spaced and complete. **It only becomes visible against an
external reference.** Indicators and technical validation consume these bars
while entry prices come from the live `PRICE_CACHE`, so the two halves of a
decision are drawn 16 minutes apart.

→ **CONFIRMED.**

---

## 2. Official NSE feed (§9, §11, §16) — retrieved

Called `https://www.nseindia.com/api/corporate-announcements` directly, both
the market-wide form the engine uses and the date-ranged form.

### The market-wide endpoint returns exactly 20 filings, with no pagination

A live fetch returned **20 items spanning 15:02:06 → 15:06:50** — a ~5-minute
window. The engine polls this **every ~62 seconds (682 polls today)**.

### The true denominator, per session

| Session | NSE published | filter-eligible | we ingested | capture of eligible |
|---|---:|---:|---:|---:|
| 2026-08-21 | 598 | 85 | 5 | **5.9%** |
| 2026-08-24 | 566 | 92 | 9 | **9.8%** |
| 2026-08-25 | 676 | 94 | 89 | **94.7%** |
| 2026-08-26 *(to 15:11)* | 203 | 27 | 3 | **11.1%** |

The 08-25 spike to 94.7% follows the commits of the night of 08-24
(`53fd0fa` "a duplicate RSS headline was starving the NSE announcement feed",
`1e7be02`, `6b1234e`). It then collapsed again.

**08-25 proves the 20-item window is not inherently fatal** — 94.7% was
achieved through the same endpoint. Today's collapse is a different failure,
and its cause is **NOT PROVEN**. Two hypotheses were tested and rejected:

- *DB outage starved the writer* — **NOT SUPPORTED.** Zero
  `too many clients` / `OperationalError` / `IntegrityError` lines in
  `news-engine.log` today.
- *The engine stopped polling* — **RULED OUT.** 682 polls today, still running
  at 15:09:49.

### Every eligible filing today, traced

27 filter-eligible announcements on 2026-08-26. **22 were filed during the
trading session. The NSE-Announcements source captured ZERO of those 22.** The
only three captured were filed at 08:51, 09:00 and 09:06 — all before the bell.

Missed intraday filings include exactly the tradable category:

| Filed | Symbol | Category |
|---|---|---|
| 10:09:24 | SUBEXLTD | Awarding of order(s)/contract(s) |
| 11:10:45 | HAL | Press Release |
| 12:39:50 | JSLL | Product launch |
| 13:20:43 | HIKAL | Outcome of Board Meeting |
| **13:31:03** | **CEIGALL** | **Bagging/Receiving of orders/contracts** |
| 13:33:42 | MOLDTKPAC | Bonus |
| 14:09:29 | MOLDTKPAC | Dividend |
| 15:08:29 | MOLDTECH | Bonus |
| 15:11:16 | GODREJPROP | Credit Rating |

**Correction to a stronger claim I would otherwise have made:** this is a
blackout of the *NSE official source*, not of the pipeline. Cross-checking all
sources, several of these companies still arrived via secondary media:

| Symbol | news_items | causal_events | agent_decisions | tactical_signals | trades |
|---|---:|---:|---:|---:|---:|
| HAL | 2 | 5 | 5 | 0 | 0 |
| CEIGALL | 1 | 9 | 9 | 0 | 0 |
| JSLL | 0 | 2 | 2 | 0 | 0 |
| SUBEXLTD | 2 | 0 | 0 | 0 | 0 |
| SEPC | 2 | 0 | 0 | 0 | 0 |
| **MOLDTKPAC** | **0** | **0** | **0** | **0** | **0** |
| **HIKAL** | **0** | **0** | **0** | **0** | **0** |
| **GABRIEL** | **0** | **0** | **0** | **0** | **0** |

MOLDTKPAC's bonus *and* dividend, HIKAL's board outcome and GABRIEL's press
release were missed by **every source**. And **none of the eight produced a
tactical signal or a trade.**

→ 20-item window: **CONFIRMED**. Today's collapse: **NOT PROVEN**.

---

## 3. LLM audit (§19–20) — performed

`agent_decisions.confidence_factors` stores the model's own structured context
(`news`, `bull`, `bear`, `thesis`, `verdict`, `key_risk`), which makes
reconstruction possible without re-running anything.

### Evidence pack — CEIGALL.NS, 2026-08-26

| Field | Value |
|---|---|
| Event | ₹274 crore road project win, Arunachal Pradesh |
| Official NSE filing | 13:31:03 (category *Bagging/Receiving of orders/contracts*) |
| Our detection | 09:41 via secondary media — **ahead of the NSE filing** |
| Classification | `HIGH materiality ORDER_WIN`, directionally bullish — **correct** |
| Symbol mapping | `CEIGALL.NS` — correct |
| Universe | present |
| **AI evaluations** | **9, between 09:41 and 15:11** |
| **AI verdicts** | **SKIP × 9** (confidence 55, 55, 55, 60, 60, 60, 65, 65, **85**) |
| Master Score | `NULL` on all 9 rows |
| Tactical signals | 0 |
| Trades | 0 |
| **Actual move (Kite)** | prev close 324.35 → **high 346.00 = +6.67%**; LTP 337.00 = +3.90% |

The nine stated reasons:

| Time | Conf | Reason |
|---|---:|---|
| 09:41 | 60 | "genuine bullish catalyst for CEIGALL's EPC business, but …" |
| 09:55 | 55 | "Buying on news without technical confirmation in neutral regime"; context adds **"no intraday candles for timing"** |
| 10:14 | 55 | "price only **+0.80%** on the day — not enough follow-through" |
| 10:51 | 65 | "price only **+1.06%** on the day — not enough follow-through" |
| 11:27 | **85** | "genuine HIGH materiality catalyst … but absent **volume** confirmation" |
| 12:18 | 60 | "Extended runner … without **volume** confirmation" |
| 13:23 | 60 | "Profit-booking after news rally without fresh breakout or **volume** confirmation" |
| 14:53 | 55 | "Buying an extended runner prone to profit-booking without **volume** confirmation" |
| 15:11 | 65 | "order win is factual and material but not generating buying pressure" |

**"volume confirmation" appears in four of the nine refusals — and 55% of our
1-minute bars carry `volume = 0`.** One refusal explicitly cites the absence of
intraday candles, and the median candle lag is 16 minutes.

This is the closest thing in the investigation to a proven data→decision
causal link: the model was given a correctly-identified, correctly-classified,
high-materiality catalyst, and refused it nine times citing evidence the data
layer could not supply.

→ **STRONGLY SUPPORTED** (not CONFIRMED: the raw prompt bytes were not
captured, only the model's structured context record).

---

## 4. The follow-through gate — counterfactual (§27)

`engine/entry_confirmation.py:30` sets `MIN_DAY_CHANGE_PCT = 1.5`. A BUY is
refused unless the stock is already up ≥1.5% on the day.

**Earlier in this investigation I wrote that the gate "rejects flat stocks."
CEIGALL disproves that as a general statement** — it was refused at +0.80% and
+1.06% and went to +6.67%. So the counterfactual was run properly.

For each of 217 follow-through rejections, the favourable excursion available
**after** the rejection, to that day's close (169 had forward candle data):

| Statistic | Value |
|---|---|
| Median | **+0.38%** |
| Mean | +0.78% |
| p75 / p90 | +0.91% / +1.93% |
| Max | +5.83% |
| reached +0.5% | 72/169 (43%) |
| reached +1.0% | 39/169 (23%) |
| reached +1.5% | 27/169 (16%) |
| reached +2.0% | 16/169 (9%) |
| reached +3.0% | 8/169 (5%) |

Against round-trip costs of roughly 0.21% (MIS) to 0.39% (delivery), the
**median rejected candidate offered about enough to cover costs and no more**.
The gate is not systematically discarding winners. It does forgo a thin tail —
5% of rejections offered ≥3%, and CEIGALL sits in that tail.

→ Gate destroying alpha: **NOT SUPPORTED at the median; PARTIALLY SUPPORTED in
the tail.**

---

## 5. Null / random baselines (§25) — performed

**Test:** does `composite_score` predict forward return? n = 2,798 BUY/SELL
tactical signals over four sessions; 2,628 had ≥10 forward 1m bars in a
90-minute window.

| Score quartile | n | mean forward MFE | median |
|---|---:|---:|---:|
| Q1 (lowest) | 657 | 0.618% | 0.394% |
| Q2 | 657 | **0.831%** | 0.477% |
| Q3 | 657 | 0.616% | 0.448% |
| Q4 (highest) | 657 | 0.909% | 0.461% |

Not monotonic — Q2 outperforms Q3.

- **Pearson r(score, forward MFE) = +0.1057**
- **95% bootstrap CI = [+0.0678, +0.1423]** (2,000 resamples, seed 7)

The relationship is positive and excludes zero, but it is weak.

**The selection null:**

| Population | n | mean forward MFE |
|---|---:|---:|
| Executed | 47 | **+0.736%** |
| Rejected (all reasons) | 2,581 | **+0.744%** |

Since the executed set is drawn from the signal pool, **random selection out of
that pool would return ≈ +0.744%. The executed subset is indistinguishable from
random selection.** The system's choice of *which* signal to take adds nothing
measurable.

→ Directional/selection edge: **NOT PROVEN.** Consistent with the prior Stage 3
audit's conclusion, now on an independent sample and a different metric.

---

## 6. The four-day funnel (§8)

| Stage | 08-21 | 08-24 | 08-25 | 08-26 *part.* | 4-day |
|---|---:|---:|---:|---:|---:|
| NSE announcements published (official) | 598 | 566 | 676 | 203 | 2,043 |
| └ filter-eligible | 85 | 92 | 94 | 27 | 298 |
| └ **ingested from NSE** | **5** | **9** | **89** | **3** | **106** |
| News items, all sources | 362 | 467 | 550 | 265 | 1,644 |
| Causal events classified | 544 | 683 | 623 | 373 | 2,223 |
| AI evaluations | 524 | 679 | 619 | 363 | 2,185 |
| └ verdict BUY or SELL | 1 | 1 | 0 | 1 | **3** |
| └ with an order id | 0 | 0 | 0 | 0 | **0** |
| Hub scores written | 19,530 | 42,835 | 42,739 | 25,997 | 131,101 |
| Tactical signals | 268 | 207 | 1,998 | 255 | 2,728 |
| └ executed | 9 | 9 | 16 | 20 | 54 |
| **Positions opened** | **15** | **11** | **16** | **24** | **66** |
| Positions closed | 5 | 14 | 18 | 20 | 57 |
| Net P&L, closed (₹) | −758 | +2,948 | −4,322 | +3,493 | **+1,361** |
| Win rate | 0% | 57.1% | 11.1% | 40.0% | 31.6% |

**+₹1,361 on ~₹5.02 lakh = +0.27%** across four sessions — inside the noise of
a single day's ±₹4,000 swing. **No claim in this report rests on these P&L
figures.**

### Independent opportunity set (§9)

Built from our own 1m candles, restricted to symbols already in `hub_universe`
(≥250 bars, open > ₹50), across the three complete sessions — 30 biggest
movers:

| Stage | Count | Share |
|---|---:|---:|
| In hub universe | 30 | 100% |
| Hub-scored that day | 27 | **90%** |
| Tactical signal fired | 10 | 33% |
| **LLM ever evaluated** | **0** | **0%** |
| **Traded** | **0** | **0%** |

### Rejection census

`agent_decisions.skip_reason` over 2,185 evaluations:

| Category | n | share |
|---|---:|---:|
| LLM's own prose rejection | 1,883 | 86.2% |
| Price/volume confirmation gate | 212 | 9.7% |
| LLM said TAKE, execution gate blocked | 39 | 1.8% |
| LLM returned empty / unparseable ×3 | 22 | 1.0% |
| Blank | 19 | 0.9% |
| No day-change data | 10 | 0.5% |

`tactical_signals.reason` over 2,728 signals — the top reason by an order of
magnitude:

| Reason | n |
|---|---:|
| **Cash buffer / capital exhausted** | **1,931** |
| Tactical execution disabled | 74 |
| Sector cap (Consumer 2/2) | 69 |
| **Executed** | **54** |
| Sector cap (Infra 2/2) | 49 |
| Sector cap (Banking 2/2) | 45 |
| Concurrency cap 10/10 | 38 |
| Sector breadth veto | 33+28 |

### Capital state at rejection

| | |
|---|---|
| Median `deployed` | **₹500,094** |
| Median `equity` | **₹502,039** |
| Median deployed/equity | **99.6%** (gate blocks above 90%) |
| `this_notional` = ₹0 | 1,024 / 1,931 |

| Session | First capital rejection (IST) | Capital rejections |
|---|---|---:|
| 2026-08-21 | 14:46:20 | 27 |
| 2026-08-24 | 13:59:35 | 121 |
| 2026-08-25 | **09:27:15** | **1,715** |
| 2026-08-26 | 09:46:28 | 68 |

Mechanism, `engine/risk_manager.py:280`:

```python
cash_buffer_fail = equity > 0 and (deployed_capital + this_notional) > (1 - min_cash_buffer) * equity
```

When `this_notional = 0` this reduces to *the book is already over the limit*.
The gate is **correct**; the message at `:291` — "deploying ₹0 would breach the
reserve" — is misleading, and blamed the candidate on 1,024 occasions.

`engine/portfolio_reallocation.py` is the only escape and, per its own
docstring, "never touches a position whose own strategy still endorses it."
There is **no score-based preemption** — which, given §5, is not obviously a
defect.

### Origination

Of 66 positions opened: `TACTICAL` **57 (86.4%)**, `DIRECT_NEWS` 6 (9.1%),
`EVENT_DRIVEN` 3 (4.5%). The architecture contract describes a news-only
system.

---

## 7. Entry quality and exits (§23–24)

Maximum favourable excursion, 57 closed trades — mean **+0.562%**:

| MFE reached | trades | share |
|---|---:|---:|
| ≥ 0.25% | 19 | 33% |
| ≥ 0.50% | 17 | 30% |
| ≥ 1.00% | 13 | 23% |
| ≥ 2.00% | 2 | 3.5% |

**Two-thirds never went a quarter-percent in favour.**

| Exit reason | n | P&L ₹ | mean MFE |
|---|---:|---:|---:|
| TAKE_PROFIT | 4 | +5,037 | 1.75% |
| T1_REVERSAL_EXIT | 2 | +2,839 | 0.86% |
| STOP_LOSS | 21 | +413 | 0.88% |
| CONFIRMATION_LOST | 2 | −361 | 0.00% |
| REALLOCATED | 2 | −1,132 | 0.66% |
| MIS_SQUAREOFF | 10 | −1,513 | 0.08% |
| **EXHAUSTION** | **16** | **−3,922** | **0.17%** |

EXHAUSTION is the largest loss bucket and is **not** cutting winners — mean MFE
of what it closed is +0.17%. It went live 2026-08-21 14:29–14:40 (`9c22907`,
`030a67c`).

Note the gap between forward opportunity and realised outcome: signals showed a
mean **+0.74%** forward MFE at 90 minutes, while closed `TACTICAL` trades
realised a mean **+0.110%**. Most of the available excursion is not captured —
consistent with entering late (16-minute-stale indicators) rather than with
exit geometry.

Per-family: `TACTICAL` mean MFE 0.421% (n=48), `DIRECT_NEWS` 0.425% (n=6),
`EVENT_DRIVEN` 0.885% (n=2). **News samples remain too small for any
per-strategy conclusion.**

---

## 8. What is *not* broken (§36)

| Hypothesis | Status | Evidence |
|---|---|---|
| Symbol / entity mapping is failing | **RULED OUT** | All tables use `.NS`; exact join succeeds for 1,886/2,560 universe symbols. The 674 gap is candle coverage, not mapping. |
| Broker market data is wrong or unavailable | **RULED OUT** | Kite and Upstox agree within 0.20% on every symbol tested; both served continuously. |
| Our price data is *wrong* | **RULED OUT** | Values match brokers once lag is accounted for. It is late, not incorrect. |
| The engine stopped polling NSE | **RULED OUT** | 682 polls today at ~62 s, still running at 15:09:49. |
| The DB outage starved news ingestion | **NOT SUPPORTED** | Zero DB error lines in `news-engine.log` today; zero NSE rows even after the 14:02 restart and DB recovery. |
| News ingestion has stopped entirely | **RULED OUT** | 265–550 `news_items` per session across 12 sources. |
| Execution / broker layer is failing | **RULED OUT** | 54 executions via the central gate, no failure rows; PAPER mode. |
| Exit geometry is destroying the edge | **RULED OUT** | Mean MFE 0.562%; 67% never reached +0.25%. |
| Master Score is blocking trades | **RULED OUT as bottleneck** | It does not originate; `master_score` is NULL on the news decisions examined. |
| The confirmation gate discards winners | **NOT SUPPORTED at median** | Median post-rejection excursion +0.38%, at the cost floor. Tail exception: 5% offered ≥3%. |
| Score-based preemption would recover alpha | **NOT SUPPORTED** | Executed +0.736% vs rejected +0.744% forward MFE; r = 0.106. |
| Infrastructure delayed analysis | **PARTIALLY SUPPORTED** | A 44-backend lock convoy exhausted `max_connections` on 08-26. It does not explain the four-day pattern, which predates it. |

---

## 9. Other confirmed defects

**Volume is zero on 55% of 1m bars.** `732,185 / 1,324,888` on 08-25 — not
null, zero. Directly implicated in the CEIGALL refusals. **CONFIRMED**;
mechanism of the zeros not diagnosed.

**One table stores IST, every other stores UTC.**
`tactical_signals.timestamp` is naive IST; `candles`, `agent_decisions`,
`simulation_logs`, `paper_trades`, `news_items`, `causal_events`,
`master_intelligence_scores` are UTC. *Proof:* a signal at raw `10:15:59`
quotes entry ₹629.55; APOLLOPIPE traded ₹629.55 in the 1m bar at IST 10:14
(raw UTC 04:44). Any cross-table comparison is 5h30m wrong. **CONFIRMED.**

**News latency tail.** Median publish→ingest 106–399 s; **p95 3.3 hours to 48
hours**; max 2.5–6.8 days. **CONFIRMED.**

**Event→news traceability is dead.** All 323 `causal_events` on 08-26 have
`news_id = NULL`; the last populated row was **2026-07-21 14:26**. The resolver
is in `news_discovery_engine.py` (modified 08-25 22:59); the process started
08-24 23:33 and predates it. **CONFIRMED.**

**26% of the universe has no intraday data.** 674 of 2,560 `hub_universe`
symbols have no 1m candle in 30 days. **CONFIRMED.**

---

## 10. Remaining limits

- **Raw LLM prompt bytes were not captured** — only the model's structured
  `confidence_factors` record. The §19 reconstruction is therefore STRONGLY
  SUPPORTED, not CONFIRMED.
- **Today's NSE intraday capture collapse has no proven cause.** Two
  hypotheses were tested and rejected; a third was not identified.
- **Per-strategy edge** remains unmeasurable at n = 6 and n = 2.
- **The 90-minute forward window** in §5 is one choice among many. It was fixed
  before the analysis was run and not varied afterwards, but a different
  horizon could give a different r.
- **`this_notional = ₹0`** on 1,024 rejections was traced to the capital gate's
  arithmetic, but *why position sizing returns ₹0* was not separately
  investigated.

---

## 11. Direct answers (§39)

| Question | Answer | Evidence |
|---|---|---|
| Is the strategy broken? | **PARTIALLY — selection edge NOT PROVEN** | Executed signals are indistinguishable from random selection out of the signal pool (+0.736% vs +0.744%). |
| Is the data pipeline broken? | **YES** | 16-minute median candle lag confirmed against two brokers; 55% zero-volume bars; 674 symbols with no intraday data. |
| Is live tracking broken? | **PARTIALLY** | Bars arrive and are correct, but 60% are >10 min stale during the session. |
| Is news discovery broken? | **YES** | 106 of 298 filter-eligible NSE filings captured over four sessions; 0 of 22 intraday filings today. |
| Is symbol mapping broken? | **NO** | Formats agree; joins succeed. |
| Is AI context broken? | **YES — STRONGLY SUPPORTED** | The model refused a correct HIGH-materiality catalyst nine times, four times citing volume confirmation that the data layer cannot supply. |
| Is Master Score broken? | **PARTIALLY** | NULL on the news decisions examined; non-discriminating elsewhere; does not originate. |
| Is risk management broken? | **YES — but not in the way it first appears** | 1,931 capital rejections at 99.6% deployed is confirmed. Preferring the higher-scored refusals would not have helped. |
| Is execution broken? | **NO** | 54 executions, no failures. |
| Is infrastructure contributing? | **PARTIALLY** | Lock convoy on 08-26 only. |

---

## 12. Where the largest alpha loss occurs (§40)

Walking the pipeline to the **first** point of significant loss:

| Stage | Loss | Status |
|---|---|---|
| Real market event | — | events exist: 2,043 NSE filings over four sessions |
| **Official source → our ingestion** | **298 eligible → 106 ingested (36%)**; 0 of 22 intraday today | **FIRST MAJOR LOSS** |
| Classification | works — CEIGALL classified correctly | ok |
| Symbol mapping | works | ok |
| Universe | 27 of 30 top movers present | ok |
| **Market data freshness** | **16-min median lag; 55% zero volume** | **SECOND MAJOR LOSS** |
| **AI decision** | **2,185 evaluations → 3 verdicts → 0 orders**, refusals citing the missing data above | **THIRD MAJOR LOSS** |
| Score/strategy | ranking exists but does not discriminate (r=0.106) | weak |
| **Capital allocation** | **1,931 refusals at 99.6% deployed by 09:27** | **FOURTH MAJOR LOSS** |
| Execution | works | ok |

The **first** point is news ingestion, and the second is data freshness — and
the two compound, because the AI's stated reasons for refusing are precisely
the freshness and volume it lacks. The capital lock is severe and confirmed,
but it sits *downstream* of three earlier losses and, on the §5 evidence,
resolving it alone would not convert into money.

---

## 13. THE REAL PROBLEM

The market gave this system plenty, and on the official record we can now say
how much: NSE published 2,043 corporate announcements across the four sessions,
of which the system's own high-impact filter deems 298 tradable. It ingested
106. On 26 August, 22 eligible filings landed during the trading session and
the official-feed path captured none of them — MOLDTKPAC's bonus and dividend,
HIKAL's board outcome and GABRIEL's press release were missed by every source
the system runs.

The price data, checked for the first time against something outside our own
database, turns out to be correct but sixteen minutes old. Kite and Upstox
agree with each other to within two-tenths of a percent, and both agree with
our stored values once the lag is subtracted. Sixty percent of the intraday
universe is more than ten minutes stale at any moment during the session. This
is not detectable from inside the database: the bars are complete, evenly
spaced and internally consistent. It required an external reference to see at
all, which is why it has survived several audits.

Those two facts meet in the decision layer, and CEIGALL is where you can watch
it happen. A ₹274 crore order win was detected at 09:41 — ahead of NSE's own
13:31 filing, via secondary media — classified correctly as a high-materiality
bullish order win, mapped to the right symbol, and put in front of the model
nine times over five and a half hours. The model refused all nine times. Four
of those refusals cite the absence of volume confirmation, and fifty-five
percent of our one-minute bars carry a volume of zero. One cites the absence of
intraday candles for timing, and the median candle is sixteen minutes behind.
The stock closed the day at +3.90% having touched +6.67%. The model was not
reasoning badly. It was reasoning correctly over evidence the data layer could
not give it.

Downstream of all this sits the capital problem, which is real and severe and
which I initially took to be the whole story. The book crosses its ninety
percent deployment limit twelve minutes after the opening bell on 25 August and
then refuses 1,715 signals. Across four sessions 1,931 signals are refused at a
median deployment of 99.6%. The refused signals score higher than the executed
ones — a median of 80.75 against 71.73, with 131 of them scoring above the best
trade actually taken that day.

That last sentence is where this investigation nearly went wrong, and the
correction is the most useful thing in this report. Testing whether the score
gap translates into a money gap: over 2,628 signals with ninety-minute forward
candle data, the executed population's mean favourable excursion is +0.736% and
the refused population's is +0.744%. A nine-point median score advantage
produced eight thousandths of a percentage point of difference. The
correlation between composite score and forward return is +0.106 with a
bootstrap confidence interval of [+0.068, +0.142] — non-zero, but far too weak
for a nine-point gap to mean anything. And because the executed set is drawn
from the signal pool, random selection from that pool would have returned
+0.744%: the system's choice of which signal to take is indistinguishable from
picking at random.

So the honest formulation is harsher than the one the score gap suggests. The
system is simultaneously out of capital by half past nine and unable to tell
its good ideas from its bad ones. Building score-based preemption — the obvious
fix, the one the score gap seems to demand — would reshuffle a set of signals
whose ordering carries almost no information. It would cost churn and buy
nothing measurable.

The entry-quality figures follow. Two-thirds of the fifty-seven closed
positions never moved a quarter of a percent in the intended direction. Signals
showed a mean forward excursion of +0.74% at ninety minutes, while closed
tactical trades realised a mean of +0.110% — most of the available move is not
captured, which is what entering on sixteen-minute-old indicators looks like.
The EXHAUSTION exit shows the largest loss at −₹3,922, but the mean favourable
excursion of the positions it closed is +0.17%; it is disposing of trades that
were never going anywhere, and blaming the exit would be a mistake.

Is this systemic? Yes. It reproduces across all four sessions, in every
subsystem examined, and on the official exchange record rather than only on our
own. Did recent changes help? Mixed, and demonstrably so: the news-engine
commits of 24 August lifted NSE capture from 9.8% to 94.7% in a single session
— the clearest win in the window — and it then fell back to 11.1%, for reasons
this investigation could not establish.

One thing deserves saying plainly. The four-day P&L of +₹1,361 on ₹5.02 lakh
is +0.27% across fifty-seven trades with daily swings of ±₹4,000. It is not
evidence of anything, in either direction, and nothing above rests on it.

---

## 14. THE PATH FORWARD

Ordered by evidence strength, not by how satisfying the fix feels.

### P0 — Fix candle freshness

- **Problem:** median 1m candle lag of 16 minutes during the live session; 60%
  of symbols >10 min stale.
- **Evidence:** three-source comparison at 15:06:39 IST; per-symbol lag
  distribution over 1,743 symbols; Kite−Upstox agreement of ≤0.20% proves the
  reference is sound.
- **Why first:** it is the input to technical validation, and the model cites
  its absence when refusing catalysts. Everything downstream inherits it.
- **Location:** the 1m ingestion path and its scheduling — begin at the
  `sync_long_tail_intraday` / candle-writer task family and the universe fast
  lane (`0d20813`).
- **Test:** re-run the three-source comparison hourly for a full session;
  target p95 lag < 2 min.
- **Success metric:** p50 and p95 per-symbol lag; share of universe >10 min
  stale.

### P0 — Fix intraday NSE announcement capture

- **Problem:** 0 of 22 eligible intraday filings captured on 08-26; 106 of 298
  over four sessions.
- **Evidence:** live NSE date-ranged API versus `news_items`, per filing.
- **Current behaviour:** poll `?index=equities` (newest 20, no pagination)
  every ~62 s.
- **Correct behaviour:** the date-ranged form —
  `?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY` — returns the full
  day (676 rows for 08-25) and the code already uses this shape in
  `fetch_nse_announcements_for_symbol`. Poll the day window and diff against
  `seq_id` rather than relying on a 20-item tail.
- **Risk:** low; larger payload, same endpoint, dedup already exists.
- **Test:** replay 08-26 against the day-ranged endpoint; expect 27 eligible
  rather than 3.
- **Success metric:** eligible-filing capture rate per session.
- **Note:** 08-25 achieved 94.7% through the *existing* path, so first
  establish why that regressed — the window may not be the only cause.

### P1 — Diagnose the 55% zero-volume bars

Directly implicated in four of nine CEIGALL refusals. Determine whether the
zeros are a writer defect, a source defect, or genuine no-trade minutes, and
whether tactical scoring and `check_price_volume_confirmation` treat zero as
"no data" or as "no volume". These are not the same and the difference changes
decisions.

### P1 — Restart the news engine, then re-verify `news_id`

The resolver is in the file (modified 08-25 22:59) and not in the running
process (started 08-24 23:33). Until it loads, no event→trade attribution is
possible — which also blocks proper evaluation of the news path.

### P1 — Normalise the timezone convention

`tactical_signals.timestamp` in naive IST against UTC everywhere else. No
incorrect behaviour was demonstrated — **UNPROVEN** — but the entry-cutoff
logic sits in exactly that path.

### P1 — Make the capital rejection message truthful

`engine/risk_manager.py:291` blames a ₹0 candidate on 1,024 of 1,931
rejections. The gate is correct; the message is not, and it misdirects anyone
reading the logs.

### P2 — Capital deployment policy

Deployment reaches 99.6% within 12–31 minutes of the open. An intraday ceiling
that rises through the session would reserve capital for later signals.
**Deliberately P2, not P0:** §5 shows the later signals are not measurably
better, so this is a risk-management and optionality decision for the operator,
not a demonstrated profit fix.

### P2 — Build a score that discriminates, or stop acting as if it does

r = 0.106 between composite score and forward return, and executed signals are
indistinguishable from random selection out of the pool. Either the ranking
becomes predictive, or the architecture should stop spending 131,101 scoring
operations per four sessions on an ordering it cannot use.

### Explicitly NOT recommended

**Score-based preemption of open positions.** It is the obvious reading of the
80.75-versus-71.73 gap and the evidence does not support it: +0.736% versus
+0.744% forward MFE. It would add churn and cost for no measurable gain. Revisit
only after the score is shown to discriminate.

---

*All figures drawn from the production database and logs, the live Kite and
Upstox APIs, and the live NSE corporate-announcements API on 2026-08-26 between
14:30 and 15:15 IST. No production code, configuration, service or database row
was modified, and no order was placed.*
