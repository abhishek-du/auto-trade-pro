# Where the Alpha Dies — Pipeline Forensic, 2026-08-25

**Session:** NSE 09:15–15:30 IST (03:45–10:00 UTC) · **Report written:** 16:0x IST, post-close
**Production code changed:** none. Read-only queries against the live database.
**Scope note:** this report covers the stages in the submitted pipeline diagram. Sections
explicitly NOT covered are listed in §9 rather than left implied.

---

## Executive summary

Today the system closed 18 trades for **−₹4,322** (2 winners of 18) and holds 7 positions at
+₹2,372 unrealised. It evaluated 619 news events and took **zero** news trades. It generated
1,998 tactical signals and executed **16**.

The pipeline diagram asks where the alpha dies between "stock reacts to news" and "TRADE / SKIP".
The answer, on today's evidence, is that **it is not clear the alpha ever entered the pipeline.**

Two independent measurements, both properly controlled, point the same way:

1. **The event classifier's direction tags carry no usable information today.** Bullish-tagged
   stocks finished **−0.327% below market** (symbol-clustered CI [−1.285, −0.120]); the market
   itself rose +0.160%. Bullish-vs-bearish separation is **−0.213pp, permutation p = 0.2071**.
2. **Every LLM skip avoided money it would otherwise have lost.** Trading the 432 scorable
   skipped events in their event-implied direction returns **−0.351% to close**, −0.573% after
   costs. This holds in every confidence bucket, including confidence ≥70.

Meanwhile the loudest symptom — 1,715 tactical signals blocked on the cash buffer — turns out to
be **economically harmless**. Blocked and executed signals are statistically indistinguishable,
and both lose money after costs.

So the honest headline is not "our filters destroyed alpha". It is:

> **The system is skipping trades that would have lost money, using reasoning that is not
> demonstrably better than chance, on events whose direction labels are not demonstrably
> better than chance. The gates are not the bottleneck. The signal is.**

One real infrastructure defect was found (§6) and one real data failure (§7). Neither explains
today's P&L.

---

## 1. The two funnels

The system runs two independent origination paths. They must be measured separately — pooling
them is what makes previous reports read as contradictory.

### Funnel A — the news path (the one in the diagram)

```
       325   news_items ingested                      (15 sources)
         ↓
       623   causal_events created                    entity resolution: 0 failures
         ↓
       230   distinct tickers named across events
         ↓
       126   symbols actually reached the agent        54.8% of named tickers evaluated
         ↓
       619   agent_decisions written
         ↓
         0   TAKE                                      ← the path produced nothing
         ↓
         5   reached TAKE internally, then blocked      "TAKE verdict but execution gate blocked"
         ↓
         0   orders
```

**Entity resolution is not the failure.** 623 of 623 events carried at least one ticker; zero
events resolved to an empty stock list. The `causal_events` tables store bare NSE symbols
(`TCS`, `RUBICON`) while `agent_decisions` stores yfinance form (`TCS.NS`); the system resolves
this correctly. (My first join did not, and reported a false 100% mapping failure — corrected
before it reached any conclusion here.)

### Funnel B — the tactical path

```
     1,998   tactical_signals generated
         ↓
     1,715   BLOCKED — cash buffer                    85.8%
        56   BLOCKED — sector cap (2/2 per sector)
        26   BLOCKED — R:R below 1.2 minimum
       184   skipped — position already open in symbol
        16   EXECUTED_PAPER                            0.8%
```

---

## 2. Why the news path took zero trades

`agent_decisions` for today: **619 rows, all SKIP, every one carrying a skip reason.**
`master_score` is **NULL in all 619** — the Master Intelligence Score played no part in any of
today's news decisions and cannot be blamed for suppressing them.

The skips are not gate rejections. **614 of 619 are the LLM's own verdict.** Only 5 reached a
TAKE and were then blocked downstream.

Skip-reason themes (regex over the free text, one reason may hit several themes):

| Theme | count | share |
|---|---:|---:|
| technical follow-through / breakout absent | 278 | 44.9% |
| volume confirmation absent | 178 | 28.8% |
| price reaction insufficient | 115 | 18.6% |
| liquidity / market-depth concern | 50 | 8.1% |
| event materiality too low | 48 | 7.8% |
| LLM returned unparseable output | 5 | 0.8% |

Representative verbatim reasons at confidence ≥70:

- "Buying into distribution without volume confirmation or fresh breakout after news rally."
- "Buying into exhaustion move after 4-day rally and 85% IPO gain."
- "Acting on low-materiality event without volume or price confirmation."

**This is the news-vs-technicals philosophy inverted in practice.** The stated design is *news is
the catalyst, technicals are the timing filter*. The implementation has technicals **vetoing**
the catalyst in 44.9% of decisions. That is a real architectural divergence — but see §3 before
concluding it is costing money.

### The 5 that reached TAKE

| symbol | time IST | conf | to close | net of cost |
|---|---|---:|---:|---:|
| CEIGALL.NS | 09:41 | 65 | −2.162% | **−2.384%** |
| INDIAGLYCO.NS | 11:15 | 50 | +0.171% | −0.051% |
| HINDCOPPER.NS | 13:02 | 59 | −0.384% | −0.606% |
| SIGMAADV.NS | 14:31 | 63 | +0.000% | −0.222% |
| MRF.NS | 15:38 | 59 | *(post-close, no forward bars)* | — |

Four of five scorable: **all negative after costs.** The execution gate that blocked them
prevented ₹0 of profit.

---

## 3. Did the skips cost us money? — the decisive test

For every skipped decision, direction was taken from the `causal_event` that named the ticker
(bullish → long, bearish → short) — never from the outcome. Forward return runs from the 1m bar
at decision time to session close.

| bucket | n | to close | net of 0.222% | win% | verdict |
|---|---:|---:|---:|---:|---|
| **all scorable skips** | 432 | **−0.351%** [−0.457, −0.245] | −0.573% | 44.4% | **avoided losses** |
| confidence ≥70 | 70 | −0.427% [−0.707, −0.160] | −0.649% | 42.9% | avoided losses |
| confidence 60–69 | 187 | −0.366% [−0.530, −0.206] | −0.588% | 43.3% | avoided losses |
| confidence <60 | 175 | −0.305% [−0.466, −0.156] | −0.527% | 46.3% | avoided losses |
| long-implied | 307 | −0.477% [−0.622, −0.336] | −0.699% | 43.3% | avoided losses |
| short-implied | 125 | −0.044% [−0.136, +0.045] | −0.266% | 47.2% | no edge either way |
| cited "no volume confirmation" | 113 | −0.316% [−0.544, −0.104] | −0.538% | 50.4% | avoided losses |

**Every bucket is negative.** Taking these trades would have lost money. The LLM's caution was
not costing alpha today — it was the only thing preventing a larger loss.

Note the confidence monotonicity runs the **wrong way**: higher-confidence skips avoided *more*
loss (−0.427% at ≥70 vs −0.305% at <60). If the confidence score were tracking trade quality,
high-confidence skips should look like the most expensive mistakes. They do not.

---

## 4. Is that just because the market fell? — the control

No. **The market rose.**

Baseline: at each decision's own timestamp, the mean signed to-close return of 40 randomly
sampled liquid symbols (579 qualifying symbols, >200 bars and >₹20cr traded value).

```
market drift, measured at the 432 decision instants:  +0.160%  [+0.140, +0.181]
```

Against that baseline, signed and **not** direction-flipped:

| tag | n decisions | raw to close | vs market |
|---|---:|---:|---:|
| BULLISH-tagged | 307 | −0.477% [−0.621, −0.338] | **−0.639%** [−0.782, −0.497] |
| BEARISH-tagged | 125 | +0.044% [−0.045, +0.132] | −0.110% [−0.203, −0.017] |

A correct bullish tag needs *vs market* > 0. It is −0.639%.

### Clustering correction — and an honest downgrade of my own number

Those 432 decisions sit on only 91 distinct symbols, so per-decision intervals are overstated.
Re-run with one cluster per symbol, bootstrapping over symbols:

| tag | symbols | decisions | vs market | symbols beating market |
|---|---:|---:|---:|---:|
| BULLISH-tagged | 63 | 307 | **−0.327%** [−1.285, −0.120] | 38.1% |
| BEARISH-tagged | 28 | 125 | −0.114% [−0.303, +0.104] | 39.3% |

```
separation (bullish − bearish):  -0.213 pp
permutation p, labels shuffled across symbols:  0.2071
```

**The correct conclusion is the weaker one:** there is **no evidence the direction tags carry
usable information**. The apparent inversion is not statistically significant once symbol
clustering is respected. Bullish-tagged stocks did underperform the market significantly
(CI excludes zero), but bullish and bearish are not separable from each other.

Do not read this as "the classifier is inverted — flip it". One session, 91 symbols, p=0.21.

---

## 5. The tactical path — the loud symptom that is not the problem

1,715 of 1,998 signals (85.8%) died on the cash buffer. The book was genuinely full:

| IST | deployed | free cash | positions |
|---|---:|---:|---:|
| 09:15 | ₹296,593 | ₹203,407 | 9 (all carried in from earlier sessions) |
| 09:30 | ₹476,518 | ₹23,482 | 12 |
| **11:15–15:00** | **₹500,094** | **−₹94** | 12 |
| 15:30 | ₹412,725 | ₹87,275 | 9 |

**At 09:15, before the session had produced a single signal, 9 carried-in positions already held
59.3% of capital.** Two of them (ZAGGLE ₹24,926, JUNIPER ₹23,751) had been open five days.

### The cash gate's arithmetic is correct

I verified this specifically, because I previously called this same gate an "arithmetic bug" by
comparing a mid-day figure against the end-of-day book. Reconstructing the book at each of the
1,715 block instants from `paper_trades`:

```
mean overstatement vs reconstructed book:  +Rs28
median:                                     Rs0
actual free cash at block time — median:   -Rs94
blocks that refused to deploy Rs0:          971  (56.6%)
```

The gate was telling the truth. The book really was at −₹94.

### And blocking cost nothing

| bucket | n | to close | net of cost | win% |
|---|---:|---:|---:|---:|
| BLOCKED_CASH | 1,697 | +0.147% [+0.081, +0.215] | **−0.075%** | 55.8% |
| EXECUTED | 14 | +0.151% [−0.657, +1.108] | **−0.071%** | 50.0% |
| BLOCKED_SECTOR | 54 | −0.367% | −0.589% | 46.3% |
| BLOCKED_RR | 23 | −0.502% | −0.724% | 34.8% |
| skipped, position already open | 174 | +0.045% | −0.177% | 50.6% |

Blocked and executed are **indistinguishable**, and both are negative after costs. Taking all
1,697 blocked signals would have required ~₹8.5 crore and lost roughly a further ₹63,600.

The sector cap and the R:R gate did better than that — the trades they refused were the worst in
the set. **These gates are working.**

---

## 6. The one real infrastructure defect — CONFIRMED

Intraday mean-reversion rules become 48-hour swing holds **with their stop loss disabled**.

```
engine/tactical_executor.py:369       product = "MIS" if signal.sub_pipeline == "F1" else "CNC"
   F4_RULES = OVERBOUGHT_FADE, OVERSOLD_REBOUND, VOLUME_BREAKOUT, VWAP_CROSSOVER   -> CNC
        ↓
paper_trading/trade_simulator.py:403  is_swing = product == "CNC"
                             :418-419 trade_style = "SWING"
                                      swing_min_hold = now + timedelta(hours=48)
        ↓
tasks/india_tasks.py:1607-1613        if sl_hit and trade_style == "SWING" and swing_min_hold:
                                          if now_ist < swing_min_hold:
                                              sl_hit = False        # stop suspended
```

**Impact today:** 5 of 7 currently open positions are `trade_style='SWING'` with a live
`swing_min_hold`. Their stops are advisory. HSCL, UNIONBANK and GLAND were opened *today* by
tactical rules and cannot stop out until 27 Aug. This is also the direct cause of the capital
lock in §5 — intraday signals holding capital for two days.

**Severity: high. Confidence: CONFIRMED by code path.**
It did not cost money today (the positions are net positive), which is luck, not design.

---

## 7. The one real data failure — CONFIRMED

NSE corporate announcements have collapsed and have not recovered.

| date | announcements ingested |
|---|---:|
| 2026-08-17 | 86 |
| 2026-08-18 | 95 |
| 2026-08-19 | 62 |
| 2026-08-20 | 92 |
| 2026-08-21 | **5** |
| 2026-08-24 | **11** |
| 2026-08-25 | **6** |

Worse than the daily count: of today's 6, **zero arrived during the trading session.** They
landed at 00:56, 02:06, 03:34 UTC (pre-open) and 10:25, 10:28, 10:37 UTC (post-close). A healthy
day (08-18) delivered 12–41 per hour through 10:00–17:00.

The `ON CONFLICT DO NOTHING` fix committed on 08-24 restored the loop's ability to *survive* a
duplicate headline, but **did not restore announcement volume**. Root cause is not yet
established — see §9.

**Severity: high. Confidence: CONFIRMED by data. Root cause: NOT YET DETERMINED.**

---

## 8. Short-hold churn

| exit reason | n | avg hold | total |
|---|---:|---:|---:|
| EXHAUSTION | 3 | 190s | −₹537 |
| MIS_SQUAREOFF | 4 | 176s | −₹628 |

Seven positions opened and closed within ~3 minutes for −₹1,165. BHEL was opened 09:38:25 and
closed 09:38:30 — **5 seconds, −₹95**. This is pure cost churn: the strategy paid a full
round-trip to learn nothing. Six stops also fired within a 30-second window at 09:36 IST.

**Not investigated:** which loop triggered the 09:36 cluster, and whether EXHAUSTION is
evaluating on a bar that does not yet exist. Flagged, not concluded.

---

## 9. What this report does NOT cover

Stated plainly rather than left as an implied gap:

- **Latency audit.** No stage was timed. T_news → T_execution is unmeasured. **EVIDENCE NOT AVAILABLE.**
- **AI context reconstruction.** The exact prompt sent to Bedrock was not rebuilt. `reasons` was
  read, the input was not. **EVIDENCE NOT AVAILABLE.**
- **Master Score.** Confirmed NULL in all 619 news decisions, so it did not suppress them. Why it
  is NULL was not investigated.
- **Universe construction.** 230 tickers were named by events, 126 reached the agent. The other
  104 were not traced. This is a real 45% attrition and it is unexplained.
- **Celery / infrastructure.** Not examined today.
- **Silent-failure sweep** (`except: pass`). Not run.
- **NSE collapse root cause.** Symptom confirmed, cause not found.
- **False positives on the news path.** Zero news trades were taken, so there are none to study.

---

## 10. Root cause matrix

| # | Root cause | Evidence | Impact today | Severity | Confidence |
|---|---|---|---|---|---|
| 1 | Event direction tags carry no usable information | §4, clustered p=0.2071 | all 619 news decisions rest on labels with no demonstrated edge | **critical** | STRONGLY SUPPORTED (1 session) |
| 2 | F4 rules → CNC → 48h stop suspension | §6, 3 files | 5 of 7 open positions unprotected; causes the capital lock | **high** | CONFIRMED |
| 3 | NSE announcements dead in-session since 08-21 | §7 | 0 in-session announcements today vs 92 on 08-20 | **high** | CONFIRMED (cause unknown) |
| 4 | Technicals veto the news catalyst | §2, 44.9% of skips | contradicts the stated architecture | medium | CONFIRMED (but see §3 — not costing money) |
| 5 | Short-hold churn | §8 | −₹1,165 on 7 trades, one held 5s | medium | CONFIRMED |
| 6 | 104 of 230 event tickers never reached the agent | §1 | 45% attrition | unknown | UNPROVEN — not traced |

---

## 11. What is NOT broken — ruled out with evidence

- **Entity resolution.** 623/623 events carried tickers; 0 empty. RULED OUT.
- **The cash-buffer gate's arithmetic.** Median error ₹0 against the reconstructed book. RULED OUT.
- **The cash buffer as an alpha destroyer.** Blocked ≈ executed, both negative. RULED OUT.
- **Sector cap and R:R gate.** They refused the worst trades in the set. RULED OUT — these help.
- **Master Intelligence Score suppressing news trades.** NULL in all 619. RULED OUT.
- **Services being down.** All five units active through the session. RULED OUT.
- **Candle freshness as today's cause.** 2,242 symbols on 1m, last write 10:31 UTC vs 10:00 close.
  Data was current. RULED OUT for today.
- **"The LLM is too cautious."** Its skips avoided loss in every bucket. RULED OUT.

---

## 12. Final verdict

| Question | Answer | Basis |
|---|---|---|
| Is the strategy broken? | **YES** | §3, §4, §5 — no measurable edge on either path |
| Is the data broken? | **PARTIALLY** | §7 — NSE feed dead in-session; everything else current |
| Is live tracking broken? | **NO** | candles current to 10:31 UTC |
| Is the AI context broken? | **UNKNOWN** | not reconstructed — §9 |
| Is scoring broken? | **PARTIALLY** | master_score NULL throughout |
| Is risk management broken? | **NO** | §5 — the gates refused the worst trades |
| Is execution broken? | **PARTIALLY** | §6 — stop suspension is a live defect |
| Is infrastructure broken? | **NOT EXAMINED** | §9 |

### Choosing between the brief's five options

**A — STRATEGY IS FUNDAMENTALLY BROKEN**, with a component of C (the NSE feed) and D (the stop
suspension) layered on top.

This is the fourth consecutive investigation to reach a variant of this answer, and today's is
the first to test the **news** path with a market baseline rather than the tactical path.

---

# 🔥 THE REAL PROBLEM

Every gate in this system was built on the assumption that a good signal was arriving and needed
protecting. Today's evidence says the signal was never good.

- The events' direction labels do not separate winners from losers (p=0.21).
- The LLM's verdicts on those events avoided loss in every bucket — including the ones it was
  most confident about.
- The tactical signals the cash gate blocked would have lost money at the same rate as the ones
  it let through.

**More AI will not fix this.** The bottleneck is not the model, the prompt, the latency, the
scoring weights or the filters. It is that neither origination path has been shown to produce a
directional signal with an edge over a matched or market baseline — on today's data, on the
three sessions in the matched-control study, or in the forward-resolution experiment.

Everything downstream of that is well-engineered machinery operating on an input that carries no
information.

---

# 🔥 THE PATH FORWARD

### P0 — fix now, independent of the strategy question

**P0.1 — Stop the stop-suspension.**
*Current:* F4 tactical rules open as CNC → SWING → stop disabled 48h (§6).
*Correct:* a tactical signal is intraday; it should open MIS with a live stop, or the 48h hold
must not disable the stop.
*Where:* `engine/tactical_executor.py:369`, `paper_trading/trade_simulator.py:403,418-419`,
`tasks/india_tasks.py:1607-1613`.
*Risk of changing:* stops fire more often; some current winners would have been stopped out.
*Test:* replay today's 5 SWING positions with stops live and compare.
*Success:* zero open positions carrying `trade_style='SWING'` from an F4 rule.

**P0.2 — Find why NSE announcements stopped mid-session.**
*Current:* 0 in-session announcements for 3 sessions; 92 on 08-20 (§7).
*Where:* the NSE fetch block in `news_discovery_engine.py`.
*Test:* poll the endpoint manually during the next session and diff against what lands in `news_items`.
*Success:* in-session announcement count back above 50/day.

### P1 — answer the question that decides everything else

**P1.1 — Repeat §3/§4 across every session in the database, not one.**
Today is n=1 with 91 symbols. The claim "the direction tags carry no information" is
STRONGLY SUPPORTED, not CONFIRMED, and it deserves to be settled properly before anyone rewrites
a classifier or a prompt on the strength of it. If it holds across sessions, **no downstream fix
matters** and the news path should be halted rather than tuned.

**P1.2 — Trace the 104 missing tickers** (230 named → 126 evaluated, §1). A 45% attrition rate is
either a universe bug or correct filtering; right now nobody knows which.

### P2 — cheap, safe, and worth doing regardless

- Kill the short-hold churn (§8): a position closed 5s after opening should be impossible.
- Populate `master_score` on news decisions, or remove the column from the write path so it stops
  reading as a silently broken feature.

### P3 — only after P1.1 returns

Prompt work, scoring weights, latency optimisation, and the news-vs-technicals rebalance in §2.
**All of it is premature until P1.1 establishes whether there is an edge to capture.**

---

*No production code was changed. No thresholds were tuned. No profitable examples were selected.
Where a number was withdrawn or corrected mid-investigation — the false 100% mapping failure in
§1, the per-decision intervals in §4, my earlier "arithmetic bug" claim in §5 — the correction is
stated in place rather than removed.*
