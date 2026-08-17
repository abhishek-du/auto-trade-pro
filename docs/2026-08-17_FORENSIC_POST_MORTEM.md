# AutoTrade Pro — Forensic Post-Mortem (3–17 August 2026)

**Investigator:** Claude Sonnet 5 · **Date:** 2026-08-17 · **Mode:** PAPER_TRADING (no real capital at risk)
**Evidence base:** 223 closed trades (`paper_trades`), 43 open positions, engine source code, external market/earnings verification via web search.

> **Every claim below is traced to a DB query, a code line, or a cited URL. Where a hypothesis failed, it is recorded as failed.**

---

## 1. Executive Summary

The system is **not** losing money because of bad luck, adverse markets, or a broken model. It is losing money because **the strategy that constitutes 97% of the book has no edge to begin with, and no mechanism in the pipeline is capable of detecting that.**

Five findings, in order of severity:

1. **The "Pre-Event Expectation Gap" strategy has no market-expectation input.** Both anchor providers (`_fetch_consensus`, `_fetch_guidance`) are unimplemented stubs returning `None`. All 199 live trades silently fell back to a 3-year historical CAGR baseline that the source code itself labels `is_market_expectation = False`. The strategy therefore does not trade an expectation gap — it trades "this company's recent quarterly growth vs its own 3-year average," which contains **zero information about what the market has priced in**. Measured profit factor: **1.069** — statistically indistinguishable from noise.

2. **`nc.confidence` is a sector label, not a conviction score.** Only 8 distinct values exist across 199 trades, each mapping 1:1 to a sector. Any confidence-based filtering is therefore sector selection in disguise. **This invalidated a fix deployed earlier the same day, which was reverted during this investigation** (§7.1).

3. **Holding past ~2 days after the event is where the capital dies.** Exited ≤2 days post-event: **+₹30,431**. Held 3–5 days: **−₹25,412**. Held >5 days: **−₹2,473** (0% win rate). The `POST_EVENT_REVERSAL` exit is **0-for-10, −₹16,275** — structurally guaranteed to lose (§5.3).

4. **99% long-only, heavily sector-concentrated, no hedge.** IT (−18,653) + Infra (−15,223) + Energy (−5,446) = **−₹39,322**, against Pharma (+19,310) + Metals (+15,162). Peak **101 concurrent positions**, ~69% of equity deployed, all betting the same direction on the same thesis.

5. **30% of stop-outs gave back a real gain** (ran >3% favourable first, median MFE 4.9%, **+₹8,173 surrendered**) because trailing protection only activates after Target 1.

**Ruled out as causes** (tested, negative): position sizing/risk caps, adverse market conditions, LLM hallucination, and LLM infrastructure. See §6.

---

## 2. Performance Overview

### 2.1 Headline metrics (since 21 July pivot, n=223)

| Metric | Value |
|---|---|
| Trades | 223 |
| Win rate | **38.6%** (86W / 137L) |
| Total realised PnL | **+₹5,677** |
| Average win | +₹1,023 |
| Average loss | −₹601 |
| Payoff ratio | 1.70 |
| **Profit factor** | **1.069** |
| Max realised drawdown | **−₹26,922** |

A profit factor of 1.069 means for every ₹1.00 lost, ₹1.07 was gained. After real-world costs (brokerage, STT, slippage — this is paper mode) this is **negative expectancy**. The system has no demonstrated edge.

### 2.2 Equity curve — 82.6% of all profit returned in 3 sessions

| Date | n | Day PnL | Cumulative | Drawdown |
|---|---|---|---|---|
| 07-31 | 18 | +4,376 | +4,376 | — |
| 08-03 | 18 | +6,623 | +10,999 | — |
| 08-04 | 18 | +1,233 | +12,232 | — |
| 08-05 | 32 | +15,995 | +28,227 | — |
| 08-06 | 33 | +1,922 | +30,149 | — |
| 08-07 | 22 | −4,867 | +25,282 | −4,867 |
| 08-10 | 29 | −3,908 | +21,374 | −8,775 |
| 08-11 | 16 | +9,568 | +30,942 | — |
| 08-12 | 20 | +1,656 | **+32,599 (peak)** | — |
| 08-13 | 3 | −5,532 | +27,066 | −5,532 |
| 08-14 | 6 | −2,889 | +24,177 | −8,422 |
| **08-17** | **8** | **−18,500** | **+5,677** | **−26,922** |

**Bleeding period: 13–17 August.** Peak equity +32,599 (12 Aug) → +5,677 (17 Aug). **82.6% of every rupee ever earned was returned in 3 sessions, on just 17 trades.**

### 2.3 Rolling win rate — decisive deterioration

| Window | First N | Last N |
|---|---|---|
| 10 trades | 40.0% | **10.0%** |
| 20 trades | 30.0% | **15.0%** |
| 50 trades | 50.0% | **26.0%** |

This is not noise around a stable mean — it is monotonic decay. The system worked meaningfully better at the start of the sample than at the end.

---

## 3. Root Cause #1 — The strategy has no market-expectation anchor (CRITICAL)

### 3.1 The finding

`engine/pre_event_expectation_gap/expectation.py` resolves its anchor by priority: **CONSENSUS → MANAGEMENT_GUIDANCE → HISTORICAL_BASELINE_3Y_CAGR**. The first two are stubs:

```python
async def _fetch_consensus(symbol, snapshot):
    """... No provider integrated — returns None. ..."""
    return None

async def _fetch_guidance(symbol, snapshot):
    """... No structured provider yet."""
    return None
```

So every trade falls to the third tier, which the code explicitly annotates:

```python
anchor_type = "HISTORICAL_BASELINE_3Y_CAGR"
is_market_expectation = False               # NEVER a market expectation
```

**Verified against live data:** all 199 `PRE_EVENT_EXPECTATION_GAP` trades in the window carry `anchor_used = None` in `confidence_factors` — i.e. none used consensus or guidance.

### 3.2 Why this destroys the edge

The strategy's premise is: *the market has not yet priced in what we can infer about this upcoming event.* Evaluating that **requires knowing what the market expects.** With consensus and guidance both absent, the computed "gap" reduces to:

> recent quarterly growth trend − the company's own 3-year CAGR

That is a **mean-reversion/acceleration signal on the company's own history**. It says nothing about positioning, expectations, or what is priced in. Two stocks with identical fundamentals — one already up 40% into results on euphoric expectations, one flat and ignored — produce the **same** gap signal.

Compounding this, the `nowcast` is **not a forecast either**. `sector_adapters/common.py` computes it deterministically from trailing point-in-time financials (`get_pit_quarterly_series` → `recent_growth` → direction if growth >±3%). **The pre-event strategy makes no LLM call at all** (verified: zero `call_llm`/`call_mantle` references in the package). Its "prediction" is *last quarter's growth direction, extrapolated forward*.

**So the complete edge is:** recent growth > 3yr average, AND price not overextended. No forward-looking information, no market expectation, no catalyst-specific insight. A profit factor of 1.069 is exactly what such a signal should produce.

### 3.3 External confirmation — GENESYS (largest single loss, −₹8,099, −17.76%)

This trade is the perfect illustration. Our nowcast said POSITIVE. **The results were genuinely excellent** — revenue +26.20% to ₹72.14 cr, EBITDA +41.04%, PAT +32.29% YoY. The nowcast was *correct*.

**The stock fell 8.41% anyway** (to ₹192.8 on 14 Aug). Why? MarketsMojo had downgraded it to **"Strong Sell" on 4 August — three days before our entry** — citing a P/E of 37.68 and "deteriorating valuation profile." The good news was already priced in and then some.

> This is the exact failure mode an expectation anchor exists to prevent. The system had no way to see it, because it has no market-expectation input.

Sources: [Univest — GENESYS falls 8.41%, 14 Aug 2026](https://univest.in/blogs/why-genesys-international-corporation-ltd-share-price-fall-2026-08-14) · [MarketsMojo — downgraded to Strong Sell, 4 Aug 2026](https://www.marketsmojo.com/news/stock-recommendation/genesys-international-corporation-ltd-downgraded-to-strong-sell-amid-valuation-and-financial-concerns-4137266)

### 3.4 External confirmation — GODREJIND (−₹1,018, −7.15%)

Nowcast POSITIVE. Actual Q1 FY27: revenue +19% YoY to ₹6,360 cr, but **net profit fell 19%** (₹349 cr → ₹284 cr) on margin compression. Here the trailing-trend extrapolation was simply wrong — precisely the failure expected when "last quarter's direction" is projected onto a quarter with different margin dynamics.

Source: [InvestyWise — Godrej Industries Q1 FY27 results](https://www.investywise.com/godrej-industries-board-approves-financial-results-leadership-changes/)

---

## 4. Root Cause #2 — `nc.confidence` is a sector label, not a conviction score (CRITICAL)

Across all 199 trades there are only **8 distinct** `nc.confidence` values, each mapping **1:1 onto a sector**:

| conf | Sector | n | Win rate | PnL |
|---|---|---|---|---|
| 0.06 | Banking | 15 | 60.0% | +1,018 |
| 0.07 | Metals | 26 | 57.7% | +15,142 |
| 0.08 | Energy | 12 | 16.7% | −5,446 |
| 0.09 | Pharma | 19 | 42.1% | +19,166 |
| 0.10 | (unmapped) | 49 | 26.5% | −14,534 |
| 0.11 | Consumer/FMCG | 57 | 45.6% | +2,899 |
| 0.13 | Telecom | 2 | 100.0% | +2,462 |
| 0.24 | **IT** | 19 | **21.1%** | **−17,689** |

This follows directly from `sector_adapters/common.py:110-113`:

```python
confidence = round(min(
    self.confidence_ceiling,
    self.confidence_ceiling * history_factor * coarse_penalty * (0.5 + data_completeness),
), 3)
```

Every symbol sharing an adapter and input shape emits that adapter's **constant**. `nc.confidence` carries **zero per-trade information**.

### 4.1 Correlation tests — confidence does not predict outcomes

| Test | r | Verdict |
|---|---|---|
| final `signal_confidence` vs PnL % | +0.045 | **FAIL** (threshold >0.3) |
| final `signal_confidence` vs win/loss | −0.064 | **FAIL** |
| raw `nc.confidence` vs PnL % | **−0.180** | **FAIL — inverted** |
| raw `nc.confidence` vs PnL absolute | **−0.201** | **FAIL — inverted** |

The apparent negative correlation is a **sector artefact**, not evidence that low confidence is good. The 0.24 band is entirely IT, and IT had a bad fortnight — externally confirmed: Nifty IT fell 1.64% mid-August, and on 17 Aug *"Nifty trades below 24,250 mark; IT shares decline."*

Sources: [Business Standard — Nifty below 24,250; IT shares decline, 17 Aug 2026](https://www.business-standard.com/markets/capital-market-news/nifty-trades-below-24-250-mark-it-shares-decline-126081700409_1.html) · [Business Standard — Nifty below 24,600; IT shares decline, 4 Aug 2026](https://www.business-standard.com/markets/capital-market-news/nifty-below-24-600-level-it-shares-decline-126080400330_1.html)

**Implication:** the 25%-weight `nowcast` factor injects a per-sector constant into every score. Combined with the 0.5 floor in `_nowcast_subscore`, a quarter of the composite score is a sector dummy variable dressed as conviction.

---

## 5. Root Cause #3 — Post-event holding and the 0-for-10 exit path (HIGH)

### 5.1 Holding period is the single strongest predictor in the dataset

| Exit timing vs event date | n | Win rate | Total PnL | Avg |
|---|---|---|---|---|
| Exited BEFORE event | 96 | 43.8% | +260 | +3 |
| Exited ON event day | 38 | 31.6% | +210 | +6 |
| **Held 1–2 days after** | 43 | 46.5% | **+30,432** | **+708** |
| **Held 3–5 days after** | 18 | 27.8% | **−25,412** | **−1,412** |
| **Held >5 days after** | 4 | **0.0%** | **−2,473** | −618 |

**All profit is generated in the 0–2 day post-event window. Beyond it, −₹27,885.** This single dimension separates the profitable book from the unprofitable one more cleanly than any score, confidence, or sector variable.

### 5.2 Exit-reason economics

| Exit reason | n | Win rate | Total PnL | Median hold |
|---|---|---|---|---|
| STOP_LOSS | 109 | 23.9% | **−29,945** | 101.7h |
| **POST_EVENT_REVERSAL** | 10 | **0.0%** | **−16,275** | 160.4h |
| SECTOR_REVERSAL | 61 | 49.2% | −5,690 | 72.8h |
| T1_REVERSAL_EXIT | 15 | 100.0% | +20,470 | 96.4h |
| TAKE_PROFIT | 10 | 100.0% | +32,741 | 146.2h |

### 5.3 Why `POST_EVENT_REVERSAL` cannot win — a structural defect

From `paper_trading/trade_simulator.py:988-996`:

```python
if _adverse:                                   # only fires at <= -3.0% unrealised
    _room = abs(price - pos.stop_loss)
    _new_sl = (price - _room / 2) if is_buy else (price + _room / 2)
```

The mechanism only activates once the position is **already ≥3% under water**, and then merely moves the stop to the **midpoint** between current price and the old stop. It does not exit. The position then bleeds to that halved stop.

It is therefore **mathematically incapable of producing a winner** — it is a "lose more slowly" device, not a protective one. The data confirms it exactly: **0 wins in 10 attempts, −₹16,275, median hold 160 hours.** It also fires only **once** per position (`post_event_handled` flag), so a continuing adverse move gets no further protection.

### 5.4 Risk-breach analysis

| Metric | Value | Threshold | Verdict |
|---|---|---|---|
| Trades where actual loss > intended risk | **85 / 205 (41.5%)** | <5% | **FAIL** |
| Total PnL in breached trades | −₹56,160 | — | — |
| Avg intended risk | 2.64% | — | — |
| Avg actual loss | 3.80% | — | — |
| Avg overshoot | **+1.17 pp** | — | — |
| Trades worse than −1R | 14 (6.8%) | — | — |

Worst individual breach: **JAMNAAUTO** — intended risk 1.04%, actual loss **11.23%** (10.2pp overshoot).

Note the 41.5% figure overstates severity somewhat: many breaches are small overshoots on trades whose stop had been *tightened* post-entry (breakeven after T1, trailing, post-event halving), so "intended risk" measured against the final stop is a moving target. The genuinely alarming subset is the POST_EVENT_REVERSAL cluster, where gap-throughs of 1–2pp beyond a tightened stop are systematic.

### 5.5 Whipsaw vs. bad entries — decomposition of the 109 stop-outs

| Category | n | Share | Reading |
|---|---|---|---|
| Never went favourable (MFE ≤0.5%) | 22 | 20% | Entry was simply wrong |
| Modest favourable (0.5–3%) | 54 | 50% | Ordinary noise |
| **Ran >3% favourable, then stopped** | **33** | **30%** | **Gave back a real gain (+₹8,173, median MFE 4.9%)** |

30% of stop-outs had a genuine profit available and surrendered it, because trailing protection only engages after Target 1 is touched.

---

## 6. Hypotheses Tested — including those that FAILED to implicate

Rigour requires recording what was *ruled out*.

| # | Hypothesis | Test | Verdict |
|---|---|---|---|
| H1 | Confidence predicts success | r(conf, PnL) > 0.3 | **REJECTED** — r = +0.045 / −0.180 (§4.1) |
| H2 | Stops protect against large losses | breaches <5% | **REJECTED** — 41.5% (§5.4) |
| H3 | Post-event holding is optimal | ≤2d ≈ >2d PnL | **REJECTED** — +30,432 vs −27,885 (§5.1) |
| H4 | Long-only bias is not a problem | trade correlation <0.3 | **REJECTED** — 99% long, 101 concurrent (§7) |
| H5 | LLM reasoning is hallucinating | % ungrounded claims | **NOT APPLICABLE** — strategy makes **no LLM calls** |
| H6 | Market conditions were unusually adverse | Nifty drawdown >3% | **REJECTED** — see below |
| H7 | Position sizing / risk caps breached | any position >5% equity | **REJECTED** — see below |
| H8 | LLM infrastructure was degraded | live Bedrock probe | **REJECTED** — see below |

### 6.1 H6 — Market conditions were NOT the cause

From our own `candles` table, NIFTY 50 across the window: 24,774 (3 Aug) → 24,348 (17 Aug) = **−1.7%**. On 17 Aug itself, the day of the −₹18,500 loss, Nifty fell only **−0.31%** and Sensex −0.44%. Market breadth was mixed (1,681 advancing / 1,848 declining).

Moreover, the broader market was *favourable* to this book's profile: Nifty Midcap 100 **+12.88%** and Smallcap 100 **+12.49%** over the trailing 12 months, outperforming large caps. A predominantly small/midcap long book should have had a tailwind.

**A −1.7% index move cannot explain an 82.6% giveback of cumulative profit.** The losses are idiosyncratic and self-inflicted.

Sources: [India TV — Sensex/Nifty 17 Aug 2026](https://www.indiatvnews.com/business/markets/17-august-2026-stock-market-updates-sensex-nifty-open-in-red-amid-persistent-geopolitical-tensions-2026-08-17-1051581) · [Whalesbook — midcaps/smallcaps outperform](https://www.whalesbook.com/news/English/economy/Indian-Stock-Market-Midcaps-Smallcaps-Outshine-Headline-Indices-in-Volatile-Year/6a7fee596ffbe1e6461d43e5)

Contextual macro during the window: firm crude (Brent ~$88–89/bbl) and US–Iran / Strait of Hormuz tension kept risk appetite subdued — a headwind, but not a shock.

### 6.2 H7 — Position sizing is CORRECT (ruled out)

| Metric | Value | Cap | Status |
|---|---|---|---|
| Median position | ₹13,554 (0.68% of equity) | — | OK |
| Largest position | ₹86,589 (**4.34%**) | 5% | **within cap** |
| Positions exceeding 5% | **0** | — | **OK** |
| Median risk per trade | ₹1,056 (**0.05%** of equity) | — | conservative |

`MIN_STOP_DISTANCE_PCT = 1.5%` is correctly enforced at signal creation across all three tiers (dynamic → ATR → static) in `engine/risk_manager.py:89,123`. The 27.8% of trades showing final stop distance <1.5% are **post-entry mutations** (breakeven-after-T1, trailing ratchet, post-event halving), not entry-gate violations.

**Sizing and risk caps are working as designed and are not a contributing cause.**

### 6.3 H8 — LLM infrastructure is healthy (ruled out)

Live probe of `call_llm_chat()` against AWS Bedrock (`nvidia.nemotron-super-3-120b`, us-east-1) returned correctly in **1.78s**; circuit breaker closed (`mantle_breaker_remaining() = 0.0`); `causal_events` shows 9 rows written today. No LLM/Bedrock errors in worker logs.

*(Note: the `llm_reasoning_log` table is stale since 24 July — but this is an observability gap, not an outage. It is only wired to `api/stock_chat.py` and the ReAct tooluse path, both inactive since the news-only pivot. The active pipelines never wrote to it.)*

---

## 7. Root Cause #4 — Concentration, correlation, and no hedge (HIGH)

### 7.1 Sector exposure

| Sector | n | Share | Win rate | PnL |
|---|---|---|---|---|
| IT | 21 | 10.2% | 19.0% | **−18,653** |
| Infra | 50 | 24.4% | 26.0% | **−15,223** |
| Energy | 12 | 5.9% | 16.7% | −5,446 |
| FMCG | 3 | 1.5% | 33.3% | −122 |
| Banking | 14 | 6.8% | 64.3% | +1,050 |
| Telecom | 2 | 1.0% | 100.0% | +2,462 |
| Consumer | 55 | 26.8% | 45.5% | +2,761 |
| Metals | 28 | 13.7% | 57.1% | +15,162 |
| Pharma | 20 | 9.8% | 45.0% | +19,310 |

Three sectors account for **−₹39,322**; two account for **+₹34,472**. Outcomes are dominated by *which sectors happened to be in favour*, not by trade selection — the signature of an unhedged, undiversified directional book.

### 7.2 Structural exposure

- **99.0% long** (203 BUY / 2 SELL). Short side is disabled by design (`decision.py`: *"short-side disabled in Phase 1"*).
- **Peak 101 concurrent open positions** (5 Aug), ≈**69% of equity deployed** simultaneously.
- **Same-day entry clustering:** 60 entries on 31 July; 40 on 4 Aug; 29 on 5 Aug — each batch sharing one thesis and heavy sector overlap.
- **10 Aug batch: 8 entries, 0% win rate, −₹7,241.** When the thesis fails, the entire cohort fails together.

With no short exposure, no index hedge, and no per-sector exposure cap, a single sector rotation propagates straight to the P&L — which is precisely what IT weakness did in the bleeding period.

---

## 8. Incident: A Defective Fix Was Deployed and Reverted During This Investigation

**Full disclosure, since it materially affects the record.**

Earlier on 17 August (before this forensic review), based on a *partial* analysis, a fix was implemented, committed (`6ca53cc`), merged to `main` (`f31ac8f`), pushed, and deployed to the live backend:

- `decision.py`: new hard gate `MIN_NOWCAST_CONFIDENCE = 0.15`
- `scoring.py`: `_nowcast_subscore` squared confidence before applying it

**The reasoning was wrong.** It assumed `nc.confidence` measured per-trade conviction, so 0.06–0.11 readings meant "the model is guessing." §4 disproves this: it is a sector constant. A 0.15 threshold therefore reduces to **"trade the IT sector only."**

Replayed against the 199 real trades:

| | n | Share | Win rate | PnL |
|---|---|---|---|---|
| Gate would **keep** | 19 | 9.5% | 21.1% | **−17,689** (all IT) |
| Gate would **block** | 180 | 90.5% | 41.7% | **+20,705** |

**The deployed fix discarded the profitable 90% of the book and retained the single worst-performing sector.**

**Remediation completed during this investigation:**
1. `git revert -m 1 f31ac8f` → commit `76c1e23`, with the full failure analysis in the commit message and an explicit *"do not re-apply this gate"* warning.
2. All 55 tests in the three pre-event test modules re-run — pass.
3. Pushed to `origin/main`.
4. Backend (`uvicorn` + `celery-worker` + `celery-beat`) restarted; `/health` 200; gate confirmed absent from running code.

**Lesson:** the existing 55-test suite passed the defective change without objection, because no fixture exercised a confidence below 0.15 and no test asserts anything about the *distribution* of that field. Tests validated mechanics, not semantics.

---

## 9. Recommendations (prioritised)

### P0 — Immediate (do before the next trading session)

| # | Action | Rationale | Effort | Risk |
|---|---|---|---|---|
| **P0-1** | **Force exit ≤2 trading days after the event date**, unconditionally | Cleanest, largest, best-evidenced effect in the dataset: +30,432 inside the window vs −27,885 outside (§5.1) | Low | Low |
| **P0-2** | **Replace `POST_EVENT_REVERSAL` stop-halving with an immediate market exit** when the event resolves against the thesis | 0-for-10, −16,275; mathematically cannot win as written (§5.3) | Low | Low |
| **P0-3** | **Do not re-introduce any `nc.confidence` gate** until the field carries real per-trade information | It is a sector dummy; gating on it selects sectors blindly (§4, §8) | — | — |

### P1 — Short term (this week)

| # | Action | Rationale | Effort |
|---|---|---|---|
| **P1-1** | **Per-sector exposure cap** (suggest ≤15% of open positions, ≤20% of deployed capital) | IT/Infra/Energy concentration drove −39,322 (§7.1) | Medium |
| **P1-2** | **Cap concurrent open positions** (suggest ≤40 vs the observed 101, config currently allows 500) | 69% of equity in one correlated basket (§7.2) | Low |
| **P1-3** | **Activate trailing protection before T1** — e.g. move to breakeven at +2% | Recovers a share of the +8,173 given back by the 33 whipsawed winners (§5.5) | Medium |
| **P1-4** | **Add a valuation/analyst-sentiment veto** at entry | Would have blocked GENESYS (Strong Sell, P/E 37.7, downgraded 3 days pre-entry) — the single largest loss (§3.3) | Medium |

### P2 — Structural (the real fix)

| # | Action | Rationale | Effort |
|---|---|---|---|
| **P2-1** | **Integrate a real consensus-estimate feed** into `_fetch_consensus` | Without it the strategy's core premise is unimplementable. **This is the actual root cause.** (§3) | High |
| **P2-2** | **Until P2-1 lands, rename the strategy and cut its allocation** | It is currently a trailing-growth momentum screen, not an expectation-gap strategy. Its 97% book share is unjustified at PF 1.069 (§3.2) | Low |
| **P2-3** | **Add distribution/semantic assertions to the test suite** | The 55-test suite green-lit a change that would have destroyed the book (§8) | Medium |
| **P2-4** | **Enable short side or index hedging** | 99% long with no hedge (§7.2) | High |
| **P2-5** | **Re-derive the `_nowcast_subscore` 0.5 floor** | With confidence as a sector constant, the floor launders a sector label into 25% of the composite score (§4) | Medium |

### Recommended sequencing

P0-1 and P0-2 alone address the −₹27,885 post-event bleed and the −₹16,275 broken exit path — together **~₹44,000** of the identified damage, at low effort and low risk. **Do these first.** Do not attempt P2-1 before validating that the strategy has any edge once P0 is in place.

---

## 10. Appendix

### 10.1 Open risk as of this report
43 open positions remain (`paper_trades.status='OPEN'`), unrealised **+₹3,861**. Wallet: balance ₹11,03,718 · equity ₹19,92,864 · realised PnL **−₹10,997** · peak balance ₹20,29,455 · max drawdown 2.46%. **These positions were opened under the flawed logic described above and should be reviewed individually against P0-1.**

### 10.2 Unresolved / deferred
- **`uvicorn` OOM-kills**: kernel OOM killer terminated the API service on **14 Aug** (6.0 GB peak) and **17 Aug 08:49** (5.6 GB peak). It also fails to honour SIGTERM, requiring SIGKILL on every restart. Memory leak — unrelated to trading logic, but a live availability risk. **Not investigated here.**
- `llm_reasoning_log` stale since 24 July (observability gap, §6.3).
- Uncommitted working-tree changes (yfinance→Upstox migration, `main.py` `init_db()` commented out, `patch_*.py`/`test_*.py` scratch files) — 10+ days old, unrelated to this investigation, still pending a decision.

### 10.3 Method notes
- All trade data from local Postgres (`autotrade_pro`) via `asyncpg`, not the API, to avoid serialisation loss.
- Sector attribution uses the engine's own `_get_sector_for_symbol`, so it matches production behaviour.
- Correlations are Pearson on n=199–205; with only 8 distinct confidence values, these are reported as *evidence of a sector artefact*, not as clean linear relationships.
- Outlier robustness was checked per band via median and trimmed totals before drawing conclusions (§4).

---

*Report generated 2026-08-17 by Claude Sonnet 5 for AutoTrade Pro. Paper-trading mode throughout — no real capital was at risk.*
