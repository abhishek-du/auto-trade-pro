# AutoTrade Pro — 8-Year Backtest: Deep Analysis & Market Cross-Check

**Run date:** 2026-07-07
**Period:** 2018-01-01 → 2026-07-07 (8.5 years)
**Engine:** `scripts/run_backtest.py` (vectorized, look-ahead-safe, real Indian transaction costs)
**Universe:** 491 symbols tested / 9 skipped (top-500 hub universe by turnover)
**Sizing:** ₹5,00,000 notional per symbol, 2.5% risk per trade, long-only
**Config:** default — `PULLBACK_LONG` strategy, 5-state macro regime gate, momentum + relative-strength + breadth filters
**Data:** 1d candles, 2016→present (5.7M rows, 3,817 symbols); this is the **same timeframe the live agent uses** (`AGENT_TIMEFRAME=1d`)

> **Read this first (scope):** This backtest exercises the **long-only technical
> engine** — specifically `PULLBACK_LONG`, which is the one strategy your live
> config actually leans on (breakout, range-reversal and hub-signal are disabled
> in code). It does **not** replay the LLM veto, Tavily research gate, the intraday
> MIS burst, or the F&O spread engine that the *live* system also runs — none of
> those have historical data to replay against. So this is the **cleanest, most
> validated slice** of the system, not the whole live behaviour. Treat the numbers
> as the ceiling of what the validated part can do, not a forecast of the live bot.

---

## 1. Headline Result

| Metric | Value | Deploy gate | Pass? |
|---|---|---|---|
| Total trades | **1,325** | ≥100 | ✓ |
| Win rate | **53.96%** | ≥40% | ✓ |
| Profit factor | **1.33** | ≥1.3 | ✓ (barely) |
| Net P&L | **+₹18,86,156** | >0 | ✓ |
| Sharpe (annualized) | **2.29** | ≥1.0 | ✓ |
| Max drawdown | **−13.49%** (−₹3,37,195) | ≥−20% | ✓ |
| Expectancy | **+₹1,424 / trade** | >0 | ✓ |
| Avg win / avg loss | ₹10,601 / ₹9,333 | — | 1.14× |

**The engine passes every deploy gate over the full 8 years.** This is a more
favourable result than the older `QUANT_VALIDATION_REPORT.md` NO-GO verdict — the
difference is the full 500-symbol universe and the 2018 start, versus the smaller
sample that verdict used. **But the pass is fragile, and the rest of this report is
about *why* — where the money actually came from, and the three reasons the live
result will be worse than these numbers.**

---

## 2. The single most important chart: cumulative P&L by year-end

```
End 2018:  −₹62,738     ← underwater
End 2019:  −₹2,41,615   ← deepest early hole
End 2020:  −₹54,424     ← still underwater (COVID year recovered most of it)
End 2021:  +₹4,26,038   ← FIRST time in profit — 3.5 years in
End 2022:  +₹5,81,190
End 2023:  +₹16,37,856  ← +₹10.6L in ONE year
End 2024:  +₹17,92,489
End 2025:  +₹18,36,304  ← FII-exodus year, near-flat
End 2026:  +₹18,86,156  (H1)
```

Two facts jump out and they dominate everything else:

1. **The strategy was cumulatively LOSING money for the first ~3.5 years (2018 →
   mid-2021).** A real person running this from Jan 2018 would have sat in a
   continuous drawdown for three and a half years before ever seeing green. Most
   traders quit long before that.
2. **2023 produced ₹10,56,666 — 56% of the entire 8-year net profit — in a single
   year.** Strip 2023 out and the remaining 7.5 years net **+₹8,29,490 (PF 1.18)**,
   about **₹1.1L/year** across a 491-symbol book sized at ₹5L each. That is a thin,
   ordinary edge, not the 2.29-Sharpe machine the headline implies.

---

## 3. Year-by-Year — with the actual market conditions cross-checked from the web

| Year | Trades | WR | PF | Net ₹ | Verdict | Nifty (cal.) | What the market did — and why the engine did what it did |
|---|---|---|---|---|---|---|---|
| **2018** | 9 | 33% | 0.20 | **−62,738** | FAIL | +3.2% | **IL&FS collapse → NBFC crisis.** Nifty +3% masked a bloodbath: Nifty Midcap −19%, Smallcap **−32%**. Our universe is midcap-heavy → almost no valid pullback setups (only 9 trades), and the few that fired were false bounces in a falling knife. |
| **2019** | 64 | 44% | 0.59 | **−1,78,877** | FAIL | +12.0% | **Narrowest rally in a decade.** Nifty +12% but driven by ~5-8 mega-caps; the median stock fell. The engine kept buying midcap pullbacks that had no index tailwind under them → 64 trades, worst year in absolute ₹. This is the classic "index up, breadth dead" trap. |
| **2020** | 113 | 58% | 1.40 | **+1,87,191** | PASS | +14.9% | **COVID crash (−38% Mar) → V-recovery.** The regime gate correctly blocked entries during the crash, then the post-March liquidity rally gave clean broad pullback setups. First genuinely positive year. |
| **2021** | 270 | 50% | 1.41 | **+4,80,462** | PASS | +24.1% | **Everything-rally / retail boom.** Broadest bull market of the sample; 270 trades, high activity, strong follow-through. This is the year cumulative P&L finally crossed zero. |
| **2022** | 149 | 54% | 1.25 | **+1,55,151** | PASS | +4.3% | **Ukraine war + Fed rate hikes + FII selling.** Choppy, range-bound. The engine survived a flat/hostile year positive (PF 1.25) — evidence the regime + breadth gates do dampen the bad tape. Passed the "crash-year" check. |
| **2023** | 297 | **63%** | **2.03** | **+10,56,666** | PASS | +20.0% | **Midcap/smallcap super-cycle.** The single best environment for this exact strategy: strong, broad, persistent trends in the midcaps our universe is full of. 63% win rate, PF 2.0. **This one year is the edge.** |
| **2024** | 292 | 51% | 1.12 | **+1,54,633** | PASS | +8.8% | **Late-year top forming.** Strong H1, then the market topped ~Sep-24 and FIIs began the exodus. PF collapsed to 1.12 — the engine was still trading the old regime as the market rolled over. |
| **2025** | 119 | 53% | 1.08 | **+43,816** | FAIL* | negative | **FII-exodus correction** (₹1.1L cr out Oct-24→Feb-25, Nifty −11-14% from peak). Activity correctly cut (119 vs 292 trades) — the macro gate did its job — but PF 1.08 is below the 1.1 pass bar. Near-flat survival, not profit. |
| **2026 H1** | 12 | 58% | 1.96 | **+49,852** | PASS | −8.5% | Nifty hit an all-time high 26,373 on 5-Jan-2026 then fell ~8.5%. Tiny sample (12 trades) — the gate kept the book mostly in cash. Not statistically meaningful. |

\* 2025 "FAIL" is by the strict PF≥1.1 bar; it was still slightly net-positive.

**Cross-check verdict:** the year-by-year P&L lines up *exactly* with what the
broad Indian market did to midcaps. The engine makes money when trends are broad
and persistent (2020, 2021, **2023**), grinds flat when the tape is hostile but the
gate is working (2022, 2025), and **bleeds when the index rises on narrow breadth**
(2018, 2019) — because it buys pullbacks in stocks that have no real trend under
them. This is honest, expected trend-following behaviour, not a bug.

Sources: [Stable Investor annual Nifty returns](https://x.com/StableInvestor/status/1840812027696074963) · [IL&FS/NBFC 2018 smallcap −32%](https://cedcapital.in/?p=1113) · [2018 NBFC crisis timeline](https://qz.com/india/1860466/how-indias-nbfc-crisis-deepened-from-ilfs-defaults-to-covid-19) · [2024-25 FII selling ₹1.1L cr](https://www.wrightresearch.in/blog/fiis-are-selling-on-the-indian-dream-when-will-fiis-return/) · [2026 H1 −8.5%](https://www.business-standard.com/markets/news/can-sensex-nifty-hit-new-highs-by-dec-2026-tech-analysts-decode-charts-126070200094_1.html)

---

## 4. How many trades, and *why* each one profited or lost — the exit mechanics

Every trade exits through exactly one of three doors. This table **is** the
"why profit / why loss" answer, because the strategy's entire P&L is the sum of them:

| Exit reason | Trades | % | Win rate | Net ₹ | What it means |
|---|---|---|---|---|---|
| **TARGET_HIT** | 174 | 13% | 100% | **+45,31,081** | Price reached the fixed target (~2× ATR / 2.5R). These are the winners that carry the whole system. |
| **STOP_HIT** | 501 | 38% | — | **−13,86,839** | Price hit the stop-loss (below the pullback low − 1×ATR). The primary bleed. Avg loss ₹15,998. |
| **TIME_EXIT** | 650 | **49%** | 38% | **−12,58,087** | 12 trading bars passed without reaching T1 → exit at close. **Half of all trades die here, at a net loss.** |

**Read this carefully — it is the core finding:**

- The **entire net profit is manufactured by 174 target-hits (just 13% of trades)**
  producing **+₹45.3L gross**, which then pays for the **−₹26.5L** combined bleed
  from stops and time-exits. Net: +₹18.9L. Remove or degrade that thin 13% tail and
  the edge vanishes.
- **49% of all trades exit by TIMEOUT, and they lose money** (38% win rate, −₹12.6L).
  The 12-bar time-exit is a large, permanent drag. It exists to stop capital rotting
  in dead trades, but the data says half the book never works and gets cut at a small
  loss. A trend system living off a 13% winner-tail is inherently low-hit-rate.
- **35% of trades (468) book a 50% partial at T1** before the final exit — that
  partial-booking ladder is what lets the 0-1R bucket be net positive.

### R-multiple distribution (realised profit ÷ initial risk) — the asymmetry that makes it work

| Bucket | Trades | Net ₹ | Interpretation |
|---|---|---|---|
| ≤ −1R (full stop) | 218 | −35,24,162 | Clean stop-outs — the cost of doing business |
| −1R..0R | 392 | −21,69,122 | Partial losses / time-exits below breakeven |
| 0R..1R | 538 | +29,98,090 | T1 partials booked, small net winners |
| 1R..2R | 173 | +44,54,783 | The money-makers — rode to target |
| 2R..3R | 4 | +1,26,567 | Rare home runs |
| >3R | 0 | 0 | **None. The 2.5R target caps every winner.** |

**Average R per trade = +0.095. Median R = +0.109.** The edge is razor-thin per
trade — it works only because there are 1,325 of them and the right tail (1-2R
winners) slightly outweighs the left tail (−1R stops). This is a **positive-expectancy
grind, not a high-conviction system**. A small increase in real-world slippage on the
losing side (see §6) would flip the median negative.

---

## 5. Where the money came from — concentration & anatomy

### Holding period tells the trend-following story cleanly
| Held | Trades | WR | Net ₹ | |
|---|---|---|---|---|
| 0-3 days | 84 | 56% | −1,29,379 | fast stop-outs |
| 4-7 days | 211 | 42% | **−7,37,860** | the death zone — quick failures |
| 8-14 days | 697 | 48% | −28,346 | breakeven churn (the time-exit band) |
| 15-30 days | 260 | 65% | **+14,96,381** | winners that got room to run |
| 31-60 days | 53 | 100% | +8,85,366 | big trends |
| 60+ days | 20 | 100% | +3,99,996 | the rare monsters |

**The losers die in under a week; the winners need 2-8 weeks to mature.** Mean hold
12.9 days, median 9. This is textbook: cut fast, let winners run. It also means the
strategy is genuinely a *swing/positional* system — the intraday MIS burst the live
bot runs has nothing to do with this validated edge.

### P&L is concentrated in a handful of midcap names
- **Top winners:** BLUESTARCO (+88k), APLAPOLLO (+88k), GABRIEL (+74k), RHIM (+74k),
  JKTYRE (+71k), HAL (+69k), COHANCE, RICOAUTO, INDIANB, NHPC. Almost all **auto-ancillary
  / capital-goods / PSU midcaps** — exactly the 2021-2023 super-cycle leaders.
- **Worst:** ICICIBANK (−64k), KEI (−62k), JBMA (−52k), KIRLPNU (−45k), TORNTPHARM (−45k).
- **Longest win streak 14, longest loss streak 10** — you must be able to stomach 10
  losers in a row, which happens in the 2018-2019 and 2025 stretches.

### Monthly seasonality (all years pooled — small-sample, treat as descriptive)
Best: **June +₹6.6L, July +₹6.1L, January +₹3.7L.** Worst: **September −₹1.7L, March
−₹1.5L, May −₹1.5L.** June-July strength is largely the 2023 midcap run landing in
those months; don't over-fit to it.

---

## 6. Brutally honest caveats — why the LIVE result will be worse than +₹18.86L

The backtest is well-built (I verified: no look-ahead — swing highs are `shift(1)`-ed,
the regime map is a sliding window, costs are modelled). But four structural issues
mean the real number is lower, possibly much lower:

1. **Survivorship bias (the big one).** The 491 symbols are *today's* top-500 by
   turnover — stocks that survived and grew liquid enough to be in the 2026 universe.
   Backtesting them from 2018 automatically excludes every midcap that blew up,
   delisted, or stagnated. Since the winner list is dominated by midcaps that *became*
   winners (GABRIEL, JKTYRE, RHIM, APLAPOLLO), this **systematically inflates the
   result**. A point-in-time universe would score materially lower — this alone could
   account for a large chunk of the 2021-2023 outperformance.

2. **Idealized fills.** Winners exit exactly at the target price (a limit fill —
   plausible), but **stops also exit exactly at the stop price** with no gap-through.
   In reality stops gap down (overnight news, circuit-downs) and fill *worse* than
   modelled, while targets can't fill *better*. The −13.9L stop bleed is therefore
   understated. On a strategy whose median R is +0.11, a few extra rupees of slippage
   per stop flips expectancy toward zero.

3. **Unlimited-capital assumption.** The aggregate sums 1,325 trades across 491 symbols
   as if you could hold them all simultaneously with ₹5L each (₹24.5 crore of buying
   power). The live book caps at ~15 positions with a cash buffer — so it can only take
   a *subset*, and *which* subset (whichever signals fire first each cycle) is not
   modelled here. Real portfolio-constrained returns will differ and are path-dependent.

4. **This is not what trades live.** Per the code audit, the live engine trades **Hub
   7-factor scores** filtered by an **enabled, non-shadow LLM veto**, Tavily research,
   news-keyword breakers, an **intraday MIS burst**, and **F&O spreads** — none of which
   are in this backtest. The one thing validated here (`PULLBACK_LONG`) is only one
   input to the live decision, and the live `.env` doesn't cleanly gate to it. So a
   passing backtest of `PULLBACK_LONG` does **not** validate the live bot's actual
   behaviour.

---

## 7. Verdict

**On its own terms, the validated PULLBACK_LONG engine has a real, positive edge over
8 years** — PF 1.33, Sharpe 2.29, survives 2018-19-22-25 hostile tape, and behaves
exactly as trend-following theory predicts against the actual market history. That is
genuinely better than the flat "NO-GO" the old report implied.

**But it is not a green light for real money, for five concrete reasons:**

1. **56% of all profit is one year (2023).** Ex-2023 it's ~₹1.1L/yr, PF 1.18 — a thin edge.
2. **3.5 years underwater at the start.** Cumulative P&L was negative until mid-2021. Psychologically and financially, most operators would not survive the entry period.
3. **Survivorship bias inflates it** by an unknown but material amount (§6.1).
4. **Idealized stop fills understate the losing side** (§6.2); median R is only +0.11 — little margin for real-world friction.
5. **It doesn't test what the live bot actually does** (§6.4) — LLM veto, intraday, F&O are unvalidated.

**Recommended posture (consistent with the code audit):** keep this in **paper trading**.
Before any real capital: (a) re-run on a **point-in-time / survivorship-adjusted universe**,
(b) add **gap-through slippage** to stop fills, (c) run the backtest under the **real
15-position portfolio constraint**, and (d) build a separate historical harness for the
Hub/LLM/intraday/F&O paths that actually trade live. Until the edge holds *after* those
four corrections — and *without* leaning on 2023 — the honest call remains: **do not go live.**

---

## 8. Reproduce this

```bash
cd autotrade-backend
# Full 8-year universe backtest (writes results/backtest_8yr_long.json + .log)
.venv/bin/python scripts/run_backtest.py --from 2018-01-01 --top-n 500 \
    --out results/backtest_8yr_long.json

# Deep per-trade analysis (year/strategy/regime/exit/R-multiple/holding/monthly/streaks)
PYTHONPATH=. .venv/bin/python scripts/analyze_backtest.py results/backtest_8yr_long.json
```

Variants worth running next: `--shorts` (adds the short leg — live has
`EQUITY_SHORT_ENABLED=True`), `--hub` (replays real Hub DB scores where they exist),
`--research-gate` (promoter-pledge + news-keyword vetoes). Raw per-trade data for all
1,325 trades is in `results/backtest_8yr_long.json` under `all_trades`.
