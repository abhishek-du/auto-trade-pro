# AutoTrade Pro — Quant Validation Report

**Role:** Quantitative analyst / algorithmic-trading auditor
**Date:** 2026-06-18
**Scope:** Full codebase audit + re-run historical backtest (daily-only) + walk-forward + analytical stress tests + risk/ops audit + capital recommendation.
**Decision question:** *Can this bot be trusted with real money, and up to how much?*

> **Numeric tables (Sections 3–5) are filled from the re-run artifacts (`results/bt_revalidate.json`, `results/bt_deephist.json`, `results/phase2_rerun.json`, `results/phase3*.json`). All P&L is net of a realistic Indian delivery cost model (brokerage capped ₹20 + STT 0.10% + exchange + SEBI + stamp + 18% GST ≈ 0.24–0.30% round-trip) plus 1–8 bps simulated slippage.**

---

## 1. Executive Summary — GO / NO-GO

**VERDICT: NO-GO for real capital in the current (long-only) configuration. Maximum safe live capital today = ₹0.**

The system is **well-engineered software** — a sophisticated, fully transparent decision pipeline with disciplined per-trade risk control. It is **not** a broken bot. But as a *strategy* it fails the audit's edge gates (**2/5 passed**), and the reason is **structural, not a tuning problem**:

| Headline (2022-06 → 2026-06, 5,770 trades, net of costs) | Value | Gate |
|---|---|---|
| Profit factor | 1.17 | ❌ (<1.5) |
| Max drawdown | −26.9% (406 days underwater) | ❌ (>20%) |
| Sharpe | 1.28 | ✅ |
| Bull-year win rate (2023) | 64.2% | ✅ |
| Bear-year win rate (2022) | 44.7% | ❌ (<45%) |
| **Out-of-sample 2025-26 expectancy (live path)** | **−0.085 R, CI [−0.149, −0.021]** | ❌ **NO EDGE** |

- It is a **100% long-only momentum/trend book** (a short strategy exists in code but is disabled — `EQUITY_SHORT_ENABLED=False`).
- Profitability is **entirely regime-dependent**: ~all multi-year net profit came from 2023–2024 (strong-trend/up tape); 2022, 2025 and 2026-YTD are net-losing.
- The edge held out-of-sample into 2024 but **inverted in 2025-26** — statistically confirmed negative on the live decision path. The walk-forward train→val degradation (PF 1.67→0.86) exceeds the 30% overfitting tolerance.
- A true 2016–2025 / 10-year backtest **could not be run** — local data has no tradable pre-2022 history (see §3).

This reproduces the project's own prior Phase 1–3 conclusion on freshly re-run, current-code data. **The fix is structural (add a validated short side + a fail-closed defensive gate), not parameter tuning** (Phase 3 is explicitly BLOCKED from tuning until OOS turns positive).

---

## 2. Codebase Review — algorithms, formulas, pattern detection, exits

### 2.1 Data handling & the "5-minute ATR" bug — RESOLVED
- The live agent now fetches a **single deliberate timeframe**, `settings.AGENT_TIMEFRAME = "1d"` (`engine/agent/agent_loop.py:378`), with an explicit comment that the timeframe is no longer "whatever candle data happened to be available." **Daily ATR is confirmed.**
- The backtest **always** used daily candles (`scripts/run_backtest.py:419`, `WHERE timeframe='1d'`), so the historic 5-minute-ATR defect was a **live-engine-only** bug and never affected any backtest verdict.
- ATR = Wilder 14-period on daily bars (`scripts/run_backtest.py:73`, mirrored in `engine/indicators.py`).

### 2.2 Strategies & signal generation
The live agent runs four long strategies + one short, selected per-symbol by `StrategySelectorAgent` and scored by a 7-factor "Intelligence Hub" master score (technical, fundamental, news/sentiment, options PCR/IV, FII/DII flow, earnings tone, sector bias):

| Strategy | Side | Stop | Target | Notes |
|---|---|---|---|---|
| `PULLBACK_LONG` | BUY | prev-low − 0.5·ATR | entry + 2·risk (2:1) | uptrend pullback to EMA |
| `TREND_BREAKOUT_LONG` | BUY | max(swing20−1.5ATR, ema20−0.5ATR) | entry + 2·risk | breakout + volume spike |
| `RANGE_REVERSAL_LONG` | BUY | range-low − 0.5·ATR | BB mid | only if ema50≥ema200 |
| `MEAN_REVERSION_SHORT` | SELL | range-high + 0.5·ATR | BB mid | **gated off** by default |
| `HUB_SIGNAL` | BUY/SELL | entry ∓ 2·ATR | entry ± **4·ATR** | catch-all; only this one matches the spec's literal 4×ATR/2×ATR |

**Discrepancy vs. brief:** the brief assumes "Target 4×ATR / Stop 2×ATR" everywhere. In reality only `HUB_SIGNAL` uses literal 4×/2× ATR; the three Varsity long strategies use **structure-based stops with a 2:1 reward:risk target**, which is tighter than 4×ATR. Both are internally consistent; just note the headline "12% targets" only applies to the wide-ATR `HUB_SIGNAL` path.

### 2.3 Decision fusion (how the AI agent decides)
`engine/agent/decision_engine.py`:
```
final_confidence = signal_strength × (regime_factor × news_factor × earnings_factor × fii_factor)
```
- `news_factor` ∈ [0.5, 1.5] from per-symbol sentiment; `news_raw < −0.3` suppresses.
- `earnings_factor` from earnings tone {OPTIMISTIC +5 … NEGATIVE −20}.
- `fii_factor` ∈ [0.6, 1.4] from FII/DII bias.
- A trade is taken only if `final_confidence ≥ AGENT_CONFIDENCE_THRESHOLD` **and** R:R ≥ 1.5.
- **Every trade is fully explained** — the `reasons` list records the firing strategy/pattern, the price levels, and the full multiplicative factor breakdown (`conf_multi:sig=…,regime=…,news=…`). This satisfies the brief's explainability requirement.

### 2.4 Partial exit (T1) + trailing stop — IMPLEMENTED correctly
`paper_trading/trade_simulator.py` `update_positions_with_current_prices()`:
- On touching **T1, books 50%** of the position, then **moves the stop to breakeven** (`stop = max(stop, entry)`).
- Remaining half either **trails at 1×ATR** (`AGENT_EXIT_POLICY="current"`) or **holds to T2** (`partial_fixed`). Stop only ever tightens. Works for both long and short legs.
- Matches the backtest's exit engine (`run_backtest.py:293`).

### 2.5 Regime handling
- **Per-symbol** regime (`engine/agent/analyzer._classify_regime`): Dow-theory + ADX (ADX≥25 ⇒ trending; ATR vs avg ⇒ vol band).
- **Market-wide** morning regime (`engine/agent/morning_regime`): LLM call over NIFTYBEES 5-day return + India VIX + NIFTYBEES-vs-EMA50 → `AGGRESSIVE | SELECTIVE | WAIT`. WAIT blocks new entries; SELECTIVE restricts to `TREND_BREAKOUT_LONG`.
- **Caveat:** morning_regime **fails open to AGGRESSIVE** on any error/LLM outage — i.e. a model outage results in *full deployment*, not caution. And in the historical backtest the per-symbol regime labelled ~88% of bars `BULL_TRENDING` *during* the 2025-26 decline, so it was **not protective**.

### 2.6 Missing / disabled features (vs. brief)
| Feature in brief | Status |
|---|---|
| Bear-market intraday short-sell + close before EOD | **Disabled** (`EQUITY_SHORT_ENABLED=False`); MIS square-off logic exists (`agent_loop._is_mis_squareoff_window`, 15:15) but unused for shorts |
| News sentiment filter | **Present** (confidence factor + −0.3 suppression) |
| Sector rotation detection | **Present** (Hub `rotating_into/out_of`, sector biases) but **not a hard gate** |
| Sector exposure cap (<20%) | **Tracked, not enforced** — no sector cap in `risk_manager.can_take_trade` |
| Per-position cap (<5%) | **Not enforced at 5%** — agent path bounds by 1% risk + capital-utilization weight; paper `TradeSimulator` allows up to **20%** (`_MAX_POSITION_PCT=0.20`) |
| Corrupted-data / outlier filter | **Absent** — only NaN/zero guards; no reverse-split / spike / out-of-sequence detection |
| Manual global kill switch | **Only automatic** DD circuit breakers; no single manual "stop everything" flag |

---

## 3. Historical Backtest (daily-only, forced `timeframe='1d'`)

> **Data reality check (decisive):** the local `candles` table nominally spans **2015-12-31 → 2026-06-17**, but pre-2022 coverage is only ~10–23 symbols/year and **none of them are in the liquid top-N trading universe**. I ran the backtest from **2016-01-01** (`bt_deephist.json`) and it produced **ZERO trades before 2022** — byte-identical to the 2022-start run. **Conclusion: the 2016–2021 regimes (the bull runs, COVID-2020, the H1-2022 geopolitical bear) cannot be tested on local data at all.** A true 1,000+-trade, 10-year, survivorship-adjusted NSE backtest requires an external adjusted-EOD dataset (see §10). Everything below is the **2022-06 → 2026-06** window (5,770 trades, current code), which **is** statistically meaningful.

### 3.1 Overall metrics (2022-06 → 2026-06, 5,770 trades, ₹20L wallet)
| Metric | Value | Read |
|---|---|---|
| Net profit | **+₹26.2 L** (+130.8% on wallet) | positive in aggregate… |
| CAGR | +23.0% | …but see year split |
| Profit factor | **1.17** | thin (gate ≥1.5) |
| Expectancy / trade | +₹453 | positive but small |
| Win rate | 50.4% | fine |
| Avg win / avg loss | ₹6,227 / ₹5,401 (R:R 1.15) | losers well-controlled |
| Sharpe (ann, rf 6.5%) | **1.28** | passes |
| Sortino | 1.36 | |
| Max drawdown | **−26.9%** | fails (gate <20%) |
| Max DD duration | **406 trading days** underwater | severe — ~19 months |
| Calmar | 0.85 | |
| Positive months | 27/49 (55%) | |
| Trade frequency | 119/month | high |
| Avg holding | 15.4 days (median 9) | well inside the 45-day cap |
| Exit mix | 69% stop-hit / 31% target-hit | |
| Cost drag | ~₹17.7 L (~10% of gross profit) | realistic, survivable |

### 3.2 Critical gate status — **2 / 5 PASS → NO-GO**
| Gate | Threshold | Actual | Status |
|---|---|---|---|
| Sharpe | ≥ 1.0 | 1.28 | ✅ PASS |
| Profit factor | ≥ 1.5 | 1.17 | ❌ FAIL |
| Max drawdown | ≤ 20% | 26.9% | ❌ FAIL |
| Bull-market win rate | ≥ 55% | 64.2% (2023) | ✅ PASS |
| Bear-market win rate | ≥ 45% | 44.7% (2022) | ❌ FAIL (marginal) |

### 3.3 Per-year / per-regime (the central finding)
| Year | Regime | N | Win% | PF | Net P&L | Verdict |
|---|---|---|---|---|---|---|
| 2022 | bear/correction | 559 | 44.7 | 0.87 | **−₹2.13 L** | FAIL |
| 2023 | bull trend | 1341 | 64.2 | **1.94** | **+₹24.0 L** | PASS |
| 2024 | mixed | 1855 | 52.3 | 1.30 | +₹14.3 L | PASS |
| 2025 | chop/decline | 1430 | 40.0 | 0.82 | **−₹8.33 L** | FAIL |
| 2026-YTD | chop/decline | 585 | 43.1 | 0.91 | −₹1.71 L | FAIL |

> **The entire net profit comes from 2023–2024 (a strong-trend + mixed-up tape). 2022, 2025 and 2026 are all net-losing, and the two most recent years are losing.** `diagnose_breakdown` confirms the decay is **universal across all three strategies** (each flips PF≥1 → PF<1 between the good and bad eras) and is a **hit-rate collapse**, not a sizing failure (avg loss is flat: ₹5,385 → ₹5,445). The per-symbol regime label was **non-protective** — it tagged ~81–84% of bars `BULL_TRENDING` in *both* the winning and losing eras.

### 3.4 Largest winners / losers (what the agent did right / wrong)
- **Top 10 winners** (+₹18.1 k to +₹20.7 k): *all* `RANGE_REVERSAL_LONG`, *all* in **2023–2024**, *all* exited `TARGET_HIT` (e.g. JWL, HFCL, RADICO, LINDEINDIA, CREDITACC). **Right:** in a rising/range-up tape the mean-reversion-to-BB-mid longs hit their targets cleanly.
- **Top 10 losers** (−₹6.3 k to −₹6.6 k): *all* in **2025–2026**, *all* `STOP_HIT`, mostly `RANGE_REVERSAL_LONG` (MOTHERSON, J&KBANK, MRF, HINDPETRO, HDFCBANK…). **Wrong:** the same buy-the-dip logic kept firing into a declining tape and got stopped. **Note the asymmetry is healthy** — the worst loss (−₹6.6 k) is ~⅓ the best win (+₹20.7 k); the 1%-risk cap is doing its job. The problem is **frequency of losers**, not their size.

---

## 4. Walk-Forward (fixed-parameter out-of-sample stability)

> `run_backtest.py` uses **fixed** thresholds, so there is no per-window re-optimization. The honest test is OOS *stability*: hold parameters constant, measure year-by-year survival. (Given the project's tuning-on-the-2022-26-set history, re-optimizing per window would only inflate in-sample numbers; the Phase-3 OOS split — §5 — is the real overfitting check.)

| Test year | N | Win% | PF | Sharpe | Max DD (in-year) | Net P&L |
|---|---|---|---|---|---|---|
| 2022 | 559 | 44.7 | 0.87 | −1.22 | −21.9% | −₹2.13 L |
| 2023 | 1341 | 64.2 | 1.94 | **6.60** | −7.6% | +₹24.0 L |
| 2024 | 1855 | 52.3 | 1.30 | 2.47 | −12.4% | +₹14.3 L |
| 2025 | 1430 | 40.0 | 0.82 | −1.98 | **−48.5%** | −₹8.33 L |
| 2026-YTD | 585 | 43.1 | 0.90 | −1.32 | −23.4% | −₹1.71 L |

**Robustness verdict: FAIL.** Avg per-year Sharpe 0.91 is entirely carried by 2023's outlier (6.60); only **1/5 years** clear PF≥1.5; **3/5 years** breach the 30%-equivalent within-year drawdown discipline (2025 −48.5%, 2026 −23.4%, 2022 −21.9%); and performance **degrades over time** (the opposite of the "adapts to new regimes" criterion). The strategy is not stable out-of-sample.

---

## 5. Phase 2 / Phase 3 — edge on the *live decision path* (the strongest test)

Phase 2 (`validate_edge.py`) re-runs the **actual live decision engine** (not the backtest's re-implementation) over 443 symbols, 4,065 trades, and measures expectancy in **R-multiples** with bootstrap confidence intervals. This is the most trustworthy of all the tests because it exercises the code that would trade real money.

### 5.1 Overall + per-strategy expectancy (R per trade, 95% CI)
| Slice | N | Win% | PF | mean R | 95% CI | Verdict |
|---|---|---|---|---|---|---|
| **Overall (in-sample 2022-26)** | 4065 | 45.8 | 1.32 | **+0.165** | [+0.126, +0.204] | POSITIVE |
| `TREND_BREAKOUT_LONG` | 1348 | 48.4 | 1.48 | **+0.217** | [+0.156, +0.280] | POSITIVE (carries the edge) |
| `PULLBACK_LONG` | 2710 | 44.6 | 1.25 | +0.140 | [+0.091, +0.190] | POSITIVE (dilutive) |
| `RANGE_REVERSAL_LONG` | 7 | 42.9 | 0.88 | −0.095 | [−0.89, +0.84] | UNCERTAIN (n too small on live path) |

### 5.2 Walk-forward train → validation (the overfitting check)
| Split | Train mean R | Val mean R | Val PF | Val verdict |
|---|---|---|---|---|
| train 2022-23 → **val 2024** | +0.393 | **+0.190** | 1.39 | POSITIVE (held up) |
| train 2022-24 → **val 2025-26** | +0.301 | **−0.085** | 0.86 | **NEGATIVE (collapsed)** |

> The edge **survived** into the 2024 out-of-sample year (val +0.19 R) but **inverted** in the 2025-26 out-of-sample window (val −0.085 R, CI **[−0.149, −0.021] fully below zero**). Train→val degradation in split 2 is PF 1.67 → 0.86 (≈48% drop) — **far beyond the 30% overfitting tolerance**. This is the same structural break the Phase-1 year-split shows, now confirmed on the live code path.

### 5.3 Phase 3 gate — **BLOCKED**
`phase3_plan.py` gate: **NOT PASSED.** Reason: *"OOS CI [−0.149, −0.021] fully negative — edge is statistically confirmed absent in 2025-26."* Phase 3's own instruction: do **not** change any entry rule, threshold, or sizing until the OOS CI excludes zero — i.e. there is no parameter tweak that legitimately rescues this; the problem is upstream.

### 5.4 Two genuinely positive structural findings (worth preserving)
1. **Confidence is predictive (monotonic):** conf 70-79 → +0.140 R; conf 80-89 → higher still (PF 1.48). Raising the confidence threshold cleanly concentrates expectancy — a real, usable lever.
2. **The deployed exit policy is already optimal.** Of 6 exit variants tested on identical entries, the live `T1-partial + fixed-T2 (no trail)` policy is the **best** (mean R +0.176, PF 1.35) — better than full-trail (−0.149) or the older 1×ATR trail (+0.069). No easy win was left on the table here; the exit logic is sound. **The deficit is entirely on the entry/regime side.**

### 5.5 Paper-trading vs. backtest (Phase 3 evaluation)
Only **31 real paper trades** exist (2026-06-12 → 06-18) — far too few for a statistically valid paper-vs-backtest divergence test. The "paper" evaluation therefore necessarily relies on the **simulated** live decision path (§5.1–5.2), which is itself the high-fidelity proxy. **Action item:** accumulate a multi-month real paper-trade log before any go-live, and re-run the §5.2 OOS comparison on it.

---

## 6. Stress Testing (analytical — code-path assessment)

> No liquidity/spread/depth simulator exists; slippage is a **static 1–8 bps random draw**. So these answers assess **what the bot's code would do**, and flag where a scenario is **not modeled at all** (a gap, not a pass).

### a) 10% overnight gap-down
1. **Avg slippage:** the simulator would apply only its 1–8 bps band — it does **not** widen on gaps, so *modeled* slippage is unrealistically small. Realistically a stop placed inside a 10% gap fills at the **open**, i.e. the full gap distance below the stop = potentially **5–10% adverse** vs. the intended stop.
2. **Stops vs market orders:** stops are evaluated bar-by-bar against price; on a gap the stop triggers at the next available price (open). There are **no true intraday stop-limit orders** — exits are effectively market-on-touch, so on a gap they become market-at-open fills.
3. **Max single-day loss:** bounded *per position* by ~1% equity risk **only if the stop holds**; on a 10% gap the realized loss per name can be **2–5× the intended 1%**. With up to 15 positions, a correlated gap could produce a **−10% to −20% single-day** equity hit — the daily 3% DD breaker halts *new* entries but cannot protect *open* positions from a gap.
4. **Days to recover:** not directly simulated; from the equity curve, drawdowns of this size historically took **months** to recover (see DD-duration in §3).

### b) Russia-Ukraine-style geopolitical shock (VIX +200%)
1. **Sector rotation:** the Hub *detects* rotation (`rotating_into/out_of`, sector biases) and feeds it into scoring, but does **not hard-rotate** the book. Partial.
2. **Stops adequate for 2× vol:** stops are ATR-based, so they **widen automatically** with volatility — good. But position size is risk-normalized to the *current* (pre-spike) ATR at entry; an open position entered pre-spike keeps its original stop distance.
3. **Overtrade during spike:** the morning-regime WAIT/SELECTIVE gate *should* throttle entries — **but it fails open to AGGRESSIVE on LLM error**, and in **paper mode the consec-loss and max-daily-entry locks are bypassed**. So under stress + an LLM hiccup the bot **can overtrade**.
4. **Max DD during event:** the bear-market gates fail (§3); a VIX-200% event maps to the 2022/COVID behavior — expect a **double-digit drawdown**.

### c) 2008-style liquidity crisis (spreads 5×, depth −80%)
1. **Slippage on exits:** **not modeled** — the static 1–8 bps band massively *understates* crisis slippage. This is a **blind spot**, not a pass.
2. **Stops failing to execute:** the engine assumes every stop fills at the touched price; with depth collapse some stops would **not fill at the modeled price** (or at all for illiquid names). The universe filter (`AVG(volume×close) ≥ ₹5cr`) reduces but does not eliminate this.
3. **Worst-case exit:** unbounded in reality; the model cannot express it.
4. **Win-rate impact:** unmeasurable in-sim; real win-rate would degrade as stops slip through.

### d) Crowded AI / herding
1. **Detect own crowding:** **No** — no signal-crowding or correlation-to-market-flow detector.
2. **Adjust sizing when crowded:** **No.**
3. **Exit plan for crowded signals:** **No.**
4. **Execution-quality impact:** unmodeled; would manifest as extra slippage the simulator can't see. **Full gap.**

### e) Corrupted data feed
1. **Trades from bad data:** with a 5% error rate and **no outlier filter**, bad ticks would pass into indicators and could trigger spurious signals; only `atr<=0`/NaN guards catch the most degenerate cases.
2. **Detect/filter corruption:** **No** dedicated validation (reverse-split, out-of-sequence, spike). **Gap.**
3. **Fallback:** price resolver has a source fallback chain (Zerodha → yfinance cache), giving *availability* resilience but not *correctness* validation.
4. **False-signal rate:** would scale with feed error rate; not bounded by code.

**Stress-test bottom line:** the bot is robust to *availability* failures and uses ATR-adaptive stops, but it has **no model for liquidity/spread/depth stress, no crowding detector, and no data-integrity filter** — exactly the tail risks that hurt most with real money.

---

## 7. Risk Management & Operational Audit

| Control (brief target) | Implemented? | Detail |
|---|---|---|
| Max exposure per position <5% | **Partial/No** | agent: 1% risk + capital-util weight; paper sim cap = **20%** (`_MAX_POSITION_PCT`) |
| Max sector exposure <20% | **No (tracked only)** | Hub computes `sector_exposure`/overweight flags; not enforced in `can_take_trade` |
| Daily loss limit | **Yes** | `AGENT_DAILY_DD_STOP=3%` → HALT_ALL_ENTRIES (applies even in paper) |
| Weekly / monthly loss limit | **Yes** | 5% weekly, 10% monthly |
| Max open positions | **Yes** | `AGENT_MAX_POSITIONS=15` (+ safety ceiling 20) |
| Portfolio open-risk cap | **Yes** | `AGENT_MAX_OPEN_RISK=15%` of equity |
| Correlation check | **Yes** | blocks pair if ρ>0.70 (`risk_manager.py:138`) |
| Consec-loss lockout / max daily entries | **Yes (live) / bypassed (paper)** | `AGENT_CONSEC_LOSS_LOCKOUT=2`, `MAX_NEW_ENTRIES_DAY=20` |
| Leverage limit | **Yes** | CNC delivery, no leverage; margin returned 1:1 |
| Kill switch | **Auto only** | DD breakers; **no manual global stop flag** |
| Market-hours alignment | **Yes** | `_is_market_hours` 9:15–15:30 IST, `_is_trading_day`, MIS square-off 15:15 |
| Holiday calendar / corporate actions | **Partial** | trading-day check exists; split/bonus adjustment of stored candles **not verified** — a data-quality risk |
| Tax reporting | **Yes** | `engine/tax_engine.py` |
| Logging / dashboard / alerting | **Yes** | SimulationLogger, Telegram alerts, agent APIs |

**Cost realism:** confirmed — backtest deducts the full Indian delivery cost stack; reported P&L is **net of costs**.

---

## 8. Capital Scalability

The universe filter requires `AVG(daily turnover) ≥ ₹5 crore`, and the agent trades ≤15 names with ≤20% (sim) / risk-bounded sizing. Liquidity/impact reasoning:

- **₹1–10 lakh:** no liquidity constraint whatsoever; impact ≈ 0. *(But edge is unproven — see verdict.)*
- **₹10 lakh – ₹1 crore:** still well within liquidity of the ₹5cr-turnover universe; single-name positions stay a small fraction of daily volume.
- **₹1–10 crore:** begins to matter for mid-cap names in the universe; would need a participation-rate cap (e.g. ≤5–10% of ADV) and order slicing — **not implemented**.
- **>₹10 crore:** not advisable without an execution-algo layer and a hard ADV cap.

**But capital scalability is moot until the edge is proven.** The binding constraint is **strategy**, not liquidity.

---

## 9. Sudden-News Handling with ₹10 lakh deployed (simulation walk-through)

On a pandemic/war-type shock with ₹10L deployed across ≤15 long positions:
1. **Open positions:** ATR stops are in place and trail after T1; on a gap they exit at the open (worse than the stop — see §6a). The book is **long-only**, so a broad crash hits *every* position simultaneously.
2. **Stop execution:** market-on-touch; **no protection against gap-throughs**.
3. **New entries:** morning-regime *should* go WAIT (NIFTY 5d return negative + VIX spike) and block entries — **unless the LLM call errors, in which case it fails open to AGGRESSIVE**. Daily 3% DD breaker then halts new entries once realized loss crosses 3%.
4. **Cash preservation:** 20% cash buffer minimum (`AGENT_CASH_BUFFER_MIN`) is always held; no forced de-risking of *open* longs beyond their stops.
5. **Decision-making:** news factor would suppress *new* bullish signals on negative sentiment, but does nothing for the **already-open** book.

**Missing for genuine event safety:** (a) a real short/hedge side, (b) volatility-scaled position sizing that *cuts* existing exposure when VIX spikes, (c) a hard manual kill switch, (d) a correlation/breadth circuit breaker that flattens the book, (e) gap-aware/liquidity-aware exit logic, (f) deterministic (non-LLM) fail-*closed* regime gate.

---

## 10. Recommendations (concrete)

**Strategy (required before any GO):**
1. **Add a real short side** — the code path exists (`MEAN_REVERSION_SHORT`, `HUB_SIGNAL` SELL, MIS square-off). Enabling + validating it is the only structural fix for the long-only single-regime dependence. Needs its own OOS validation.
2. **Index/breadth kill-switch that fails *closed*** — replace the fail-open LLM morning-regime with a deterministic NIFTY-below-EMA50 / VIX-threshold gate that *flattens or halts* in downtrends (defense; adds no alpha but caps the bear-market bleed).
3. **Volatility-scaled sizing** — cut size (or exit) on VIX spikes for *existing* positions, not just new ones.

**Risk/ops (required before live):**
4. Enforce the **5% per-position** and **20% per-sector** caps in `risk_manager.can_take_trade` (data already in the Hub).
5. Add a **manual global kill switch** and a **correlation/breadth circuit breaker** that flattens the book.
6. Add a **data-integrity filter** (spike / reverse-split / out-of-sequence detection) before indicators.
7. Add **gap/liquidity-aware exit** modeling and a **participation-rate (ADV) cap** for capital >₹1cr.

**Measurement:**
8. Acquire an **external survivorship-bias-free adjusted-EOD dataset** to make the 2016–2021 / 10-year backtest decision-grade.

---

## 11. Final Answer

**Can I rely on this bot for real money? — Not yet, and not in its current long-only form.** The engineering is strong and the per-trade risk discipline is real, but the *edge* fails the audit gates and is out-of-sample negative in the most recent regime. The fix is structural (add a validated short side + a fail-closed defensive gate), not parameter tuning.

**Maximum safe capital today: ₹0 live / unlimited paper.** Once a short side is added and clears the same gates out-of-sample, liquidity supports roughly **₹1 crore** without execution upgrades, and up to **~₹10 crore** only after an ADV-cap + order-slicing execution layer is added.
