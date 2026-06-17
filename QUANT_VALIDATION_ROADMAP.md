# AutoTrade Pro — Quant Research & Validation Roadmap

**Author role:** Senior Quantitative Researcher / PM / Risk / Systems Architect
**Date:** 2026-06-17
**Governing rule:** *Do not optimize before measuring. Do not recommend changes before proving a problem exists.*
**Scope of this document:** measurement design, edge-validation methodology, and a data-gated optimization roadmap. **No strategy logic is modified by this plan.**

---

## 0. Executive Summary — What the existing evidence already says

Before designing new instrumentation, I read the system you already have. You are **not** starting from zero: `scripts/run_backtest.py` already produces strategy- and year-level attribution, and `results/bt_final.json` (2022-01-01 → 2026-06-17, 443 symbols, 7,812 trades) is a real edge measurement. The headline numbers:

| Metric | Value | Read |
|---|---|---|
| Total trades | 7,812 | Adequate sample at the aggregate level |
| Win rate | 51.9% | Fine |
| Profit factor | **1.13** | Marginal (audit target is 1.3) |
| Expectancy | **+₹338.56 / trade** | Positive but thin |
| Sharpe (annual) | 1.05 | Borderline |
| Max drawdown | −85.29% | **Metric is broken — see Finding C** |
| Net P&L | +₹2.64M | Positive in aggregate |

**The single most important fact in your own data — year-by-year:**

| Year | Trades | Win% | PF | Net P&L | Verdict |
|---|---|---|---|---|---|
| 2022 | 781 | 48.8 | 0.94 | −₹120,618 | FAIL |
| 2023 | 1,955 | 67.0 | **1.93** | **+₹3,204,630** | PASS |
| 2024 | 2,517 | 51.5 | 1.11 | +₹736,284 | PASS |
| 2025 | 1,812 | 41.7 | **0.83** | **−₹982,999** | FAIL |
| 2026 (partial) | 747 | 42.2 | 0.92 | −₹192,481 | FAIL |

> **The entire net profit of the system — and more — was generated in a single year (2023). 2022, 2025, and 2026 are all net-losing. The two most recent years are losing.**

This is the central question this roadmap must answer: **is there a durable edge, or is there one good regime (2023's strong bull trend) surrounded by break-even-to-losing behaviour?** The aggregate PF of 1.13 is an *average over a structural break*, not evidence of a stable edge.

### Five things that must be fixed/built before any edge claim is trustworthy

These are not strategy changes — they are measurement and methodology corrections. Per the governing rule, I flag them because they are calculation/observability defects, not opinions.

- **Finding A — Live trades are not attributable.** `strategy` and `regime` exist on `TradeCandidate` (`engine/agent/strategies/base.py`) and `TradingSignal` (`engine/signal_generator.py:72`) and flow through `decision_engine.py`, but `open_paper_trade()` (`paper_trading/trade_simulator.py`) **never writes them to the `PaperTrade` row**. The table (`db/models.py`) has `pattern_name` and `signal_confidence` but **no `strategy_name`, no `regime_at_entry/exit`, no `exit_reason` column, no MFE/MAE**. Exit reasons (`STOP_LOSS`/`TAKE_PROFIT`/`TRAIL_STOP`/`STALE_EXIT`/`MANUAL`/`SIGNAL_REVERSAL`) are computed but only buried in `SimulationLog.data` JSON. **Today you cannot run strategy- or regime-attribution on live/paper trades — only on the backtest.** This is Phase 1's top priority.

- **Finding B — The backtest and the live engine are two different code paths.** `scripts/run_backtest.py::_signal_at()` is a *hand-re-implementation* of the strategies (hardcoded thresholds: RSI 55–75, ADX>20, etc.). The live system uses `selector.propose()` → the actual `Strategy` classes. `engine/agent/backtester.py` uses the *real* analyzer/selector but is the slow path and is not what produced `bt_final.json`. **Your headline backtest therefore does not necessarily measure the strategy you actually trade.** Edge validation is only valid if the backtest executes the live decision code.

- **Finding C — The drawdown metric is invalid.** `aggregate_stats()` divides peak-to-trough ₹ drawdown by a *proxy* `ref_capital = 5 × _EQUITY` (a guessed "≈5 simultaneous positions" assumption, per its own comment). The −85.29% figure is an artifact of that assumption, not a real account drawdown. You cannot assess risk or production-readiness on a fabricated denominator. Drawdown must be computed off a single, real portfolio equity curve with position-count and capital constraints applied.

- **Finding D — Results are in-sample / curve-fit.** Recent commit history (`PULLBACK RSI≥50`, `adx gate`, `Nifty macro gate`, "local parameter optimum", "tested-and-reverted changes") shows parameters were iterated **on the same 2022–2026 dataset** that reports the edge. There is no walk-forward, no out-of-sample hold-out, no parameter-stability test. The reported PF 1.13 is therefore an **optimistically biased upper bound**. This is the difference between "we found an edge" and "we found the parameters that best fit the past."

- **Finding E — Coverage blind spots.** `HUB_SIGNAL` produced **0 trades** in `bt_final.json` (it's the priority-last catch-all in `_signal_at` and is shadowed by the earlier branches), yet it is a live strategy and the prompt asks whether it dominates. Also: the universe backtest is **long-only on daily candles**, while the live system trades intraday/`MIS`, F&O, and SELL — so the backtest does not exercise large parts of the live system at all.

**Bottom line for the reader:** the correct posture today is *edge is unproven and recent performance is negative*. The work below is sequenced so that no capital decision and no rule change is made until the measurement is trustworthy and the edge (or its absence) is demonstrated out-of-sample.

---

# PHASE 1 — Measurement & Observability

**Goal:** make every trade fully attributable and every metric reproducible. No strategy logic changes. Phase 1 is "instrument the truth," nothing more.

## 1. Trade Attribution Framework

### 1.1 Database changes (additive, non-breaking)

All columns are nullable / defaulted so existing rows and code keep working. New Alembic migration `0003_trade_attribution.py` (you already use Alembic — `db/migrations/versions/`).

**Extend `paper_trades`:**

| Column | Type | Source (already in memory, just not persisted) |
|---|---|---|
| `strategy_name` | `String(40)` | `candidate.strategy` / `signal.strategy` |
| `regime_at_entry` | `String(20)` | `signal.regime` (`decision_engine` already has it) |
| `regime_at_exit` | `String(20)` | regime classifier at close time |
| `entry_reason` | `String(40)` | strategy branch that fired (e.g. `BREAKOUT_NEW_HIGH`) |
| `exit_reason` | `String(20)` | the `reason` already passed to `close_paper_trade()` |
| `confidence_bucket` | `String(8)` | derived: `floor(confidence/10)*10` |
| `mfe_abs` / `mfe_pct` / `mfe_r` | `Float` | max favorable excursion (see §4) |
| `mae_abs` / `mae_pct` / `mae_r` | `Float` | max adverse excursion |
| `max_open_profit` | `Float` | running peak unrealised ₹ (see §5) |
| `r_multiple` | `Float` | realised P&L ÷ initial risk (`entry−stop`) |
| `initial_risk_inr` | `Float` | `(entry−stop) × size_units` snapshotted at entry |
| `holding_bars` / `holding_hours` | `Integer/Float` | from `opened_at`/`closed_at` |
| `instrument_segment` | `String(8)` | `EQUITY_CNC` / `EQUITY_MIS` / `FUT` / `OPT` |

> **R (the unit everything is normalized to):** `R = |entry − initial_stop| × size_units`. `r_multiple = realised_pnl / R`. This makes a ₹5,000 win on a small-risk trade and a ₹5,000 win on a large-risk trade comparable. **Snapshot `initial_risk_inr` at entry** — it must not be recomputed off the trailed stop.

**New table `trade_excursion_samples`** (optional, high-value): one row per mark-to-market tick per open trade — `(trade_id, ts, price, unrealised_pnl, unrealised_r)`. This is what makes MFE/MAE and exit-efficiency exact rather than approximate. Append-only; prune to closed-trade summaries after close.

### 1.2 Code wiring (the only edits Phase 1 needs)

1. `open_paper_trade()` — populate `strategy_name`, `regime_at_entry`, `entry_reason`, `confidence_bucket`, `initial_risk_inr`. These values are **already in `signal`** — this is a persistence fix, not new logic.
2. `close_paper_trade()` — write `exit_reason`, `regime_at_exit`, `r_multiple`, `holding_*`, and final `mfe/mae/max_open_profit` (read from the running tracker in `update_positions_with_current_prices`).
3. `update_positions_with_current_prices()` — already loops every position every tick; add running-max/running-min of unrealised P&L into `trade_mgmt` JSON (cheap) or `trade_excursion_samples` (exact).

### 1.3 APIs

```
GET /api/v1/analytics/trades              # filterable: strategy, regime, conf_bucket, date, segment
GET /api/v1/analytics/strategies          # §2 report
GET /api/v1/analytics/regimes             # §3 report
GET /api/v1/analytics/exit-effectiveness  # §5 report
GET /api/v1/analytics/portfolio           # §6 equity curves + ratios
GET /api/v1/analytics/risk                # §7 heat / concentration
GET /api/v1/analytics/operational         # §8 failures / slippage
```
All read-only, all served from `paper_trades` + `performance_snapshots` + `simulation_logs`. No new compute service required initially.

## 2. Strategy Performance Analytics

For each `strategy_name`, over a date range, with these **exact** definitions:

```
N            = count(trades)
wins         = trades where pnl > 0;  losses = trades where pnl <= 0
win_rate     = len(wins) / N
avg_win      = sum(pnl in wins) / len(wins)
avg_loss     = abs(sum(pnl in losses)) / len(losses)
profit_factor= sum(pnl in wins) / abs(sum(pnl in losses))
expectancy_R = mean(r_multiple)                       # PRIMARY metric — currency-neutral
expectancy_₹ = win_rate*avg_win - (1-win_rate)*avg_loss
avg_hold     = mean(holding_hours)
max_dd       = worst peak-to-trough on the strategy's own cumulative-R curve
sharpe_like  = mean(daily_R) / std(daily_R) * sqrt(252)   # on per-day aggregated R
```

> **Use R-expectancy as the headline, not ₹.** `bt_final.json` reports ₹ only; ₹ mixes position size with edge. Report both, lead with R.

Output shape (matches your requested example):
```
TREND_BREAKOUT_LONG:
  Trades: 2566 | Win: 58.8% | PF: 1.15 | Expectancy: +0.XX R | AvgHold: …h | MaxDD(R): …
```

## 3. Regime Attribution Analytics

Same metric block, grouped by `regime_at_entry` across `BULL_TRENDING / BEAR_TRENDING / RANGE / HIGH_VOL_RANGE / LOW_VOL_RANGE / CHOPPY / UNKNOWN`. Cross-tab **strategy × regime** — this is the table that answers "which strategy works in which environment." Note: today the vectorized backtest only emits `BULL_TRENDING / BEAR_TRENDING / RANGE / UNKNOWN` (`run_backtest.py::precompute`), while the live `morning_regime.py` uses `AGGRESSIVE / SELECTIVE / WAIT`. **These two regime taxonomies must be unified into one enum** before regime attribution is meaningful — flagged as a Phase-1 schema task, not a logic change.

## 4. Trade Quality Analytics (MFE / MAE)

```
MFE_abs = max over life( unrealised_pnl )       # best the trade ever was
MAE_abs = min over life( unrealised_pnl )        # worst it ever was
MFE_R   = MFE_abs / initial_risk_inr
MAE_R   = MAE_abs / initial_risk_inr
give_back = MFE_abs - realised_pnl               # profit surrendered
```
Reports: distribution of MFE_R by strategy; "give-back ratio" `realised / MFE`; and the diagnostic you named — *trade earned +1.2R but MFE was +4.5R* → systematic profit surrender. **This is descriptive only in Phase 1.** It tells us *where* P&L leaks; it does not authorize an exit change (that's Phase 3, gated on data).

## 5. Exit Effectiveness Analytics

```
profit_capture = realised_pnl / max_open_profit      # for trades that were ever green
```
Segment by `exit_reason` (`STOP_LOSS`, `TAKE_PROFIT`, `TRAIL_STOP`, `STALE_EXIT`, `MANUAL`, `SIGNAL_REVERSAL`). Report capture %, count, and avg R per exit type. Specifically measure the T1 partial-scale-out + 1×ATR trail logic that already exists in `trade_simulator.py` and `run_backtest.py` — is the 1×ATR trail (chosen by prior backtests) actually capturing profit, or is it the source of the MFE give-back?

## 6. Portfolio Analytics

A **single real equity curve** (this fixes Finding C). Daily/weekly/monthly resample of `performance_snapshots` (you already snapshot daily). Metrics off the real curve:
```
CAGR    = (equity_end/equity_start)^(252/n_days) - 1
MaxDD   = min((equity - cummax(equity)) / cummax(equity))    # REAL %, single curve
Recovery= days from trough back to prior peak
Sharpe  = mean(daily_ret)/std(daily_ret) * sqrt(252)
Sortino = mean(daily_ret)/std(neg daily_ret) * sqrt(252)
Calmar  = CAGR / abs(MaxDD)
```
Dashboard: equity curve + drawdown underwater plot + rolling 60-day Sharpe + monthly returns heatmap (this last one would have made the 2023-only profit obvious at a glance).

## 7. Risk Analytics

```
daily_risk_used   = sum(initial_risk_inr of trades opened that day) / equity
portfolio_heat    = sum(initial_risk_inr of OPEN trades) / equity     # live, every tick
sector_exposure   = sum(size_usd by sector) / equity                  # join PortfolioHolding.sector
correlation_expo  = count of concurrently-open names in same sector / cluster
```
Report avg and peak portfolio heat, exposure-by-sector time series. `OpenPosition` + `size_usd` already give you everything except the sector join.

## 8. Operational Analytics

Source: `simulation_logs` (already append-only) + a new `event_type` taxonomy. Track API failures, login/token expiry (`KiteSession`), order rejections, **realized slippage** (`PaperTrade.slippage_applied` already stored — aggregate it), missed executions, data-feed gaps (candle staleness). Alerting framework: threshold rules on rolling counts → existing notification path (Telegram is already wired). Severity: `CRITICAL` (data feed down, token expired) / `WARN` (slippage > X bps, rejection rate > Y%).

**Phase 1 exit criteria:** every closed trade has strategy, both regimes, exit reason, R-multiple, MFE/MAE persisted; all 8 reports render from the DB; the portfolio equity curve and a *correct* max-drawdown exist. No strategy code changed.

---

# PHASE 2 — Edge Validation

**Goal:** determine whether a durable edge exists. Still no strategy changes. The deliverable of Phase 2 is a yes/no/conditional verdict with statistics attached.

## 1. Historical Backtesting Framework (corrected)

**Prerequisite fix (Finding B):** the validation backtest must execute the **live decision path** (`selector.propose` → real `Strategy` classes → `decision_engine`), not the hand-re-implemented `_signal_at()`. Use `engine/agent/backtester.py` (the analyzer/selector one) as the base, optimize it, and **retire or reconcile** the duplicated `_signal_at` logic. If the two must coexist for speed, add a CI test asserting they produce identical signals on a fixed fixture — otherwise every Phase 2 conclusion is about code you don't ship.

Requirements:
- **Period:** 2022→present, explicitly tagged into regimes: 2022 bear/correction, 2023 bull trend, 2024 mixed, 2025–26 chop/decline. You already have the candles (`candles` table, `1d`).
- **Corporate actions:** verify the candle source is split/dividend adjusted. yfinance "Adj Close" handles splits+dividends; confirm the backfill used adjusted series, or survivorship/jump artifacts will pollute results. **Delisting / survivorship bias:** `load_hub_symbols` selects *today's* liquid universe — a survivorship-biased lookback. Document this; ideally reconstruct the point-in-time universe.
- **Costs:** the Varsity M7 cost model (`estimate_cost`) is already realistic — keep it. Add modeled slippage consistent with the live `_SLIP_MIN/_SLIP_MAX`.

## 2. Strategy-Level Validation

Run the corrected backtest, produce the §2 metric block per strategy **per regime-year**. Answer with numbers:
- Which strategy makes money / loses money / contributes most P&L?
- *Current evidence to confirm or overturn:* PULLBACK_LONG is 57.9% of trades at PF 1.13; TREND_BREAKOUT_LONG has the best win rate (58.8%) but **negative avg-win/avg-loss skew** (avg win ₹4,246 < avg loss ₹5,269) — i.e. it wins often but its losers are bigger than its winners, which is fragile. RANGE_REVERSAL_LONG is 38% win rate. **HUB_SIGNAL must be un-shadowed and measured** (0 trades currently). These are hypotheses to validate, not yet conclusions.

## 3. Regime-Level Validation

The strategy×regime cross-tab over the corrected backtest. Explicitly test the hypothesis the year-table implies: **the edge is concentrated in `BULL_TRENDING` (the 2023 condition) and is absent or negative in chop/decline.** If true, that is a *regime-gating* finding for Phase 3, not a reason to touch entry rules.

## 4. Confidence Score Validation

Bucket all trades by entry confidence (30–40, 40–50, …, 80+). Per bucket compute win rate, PF, expectancy-R. **Question: is confidence monotonic with expectancy?** If a 70+ bucket does not out-expect a 40–50 bucket, the confidence score is not predictive and thresholds are theater. **Do not move thresholds in Phase 2** — only measure the relationship. (Note: the backtest floors confidence at 40, so low buckets are only observable on live/paper data once Phase 1 lands — another reason Phase 1 comes first.)

## 5. HUB_SIGNAL Validation

Un-shadow it (run it as its own pass, not priority-last) and measure: % of trades, % of P&L, PF, drawdown, and a head-to-head vs the Varsity strategies (TREND/PULLBACK/RANGE) on the same symbols/period. Answer "is it useful / dominant / does it outperform?" purely from that table.

## 6. Exit Validation

Holding entries fixed, replay the **same entry set** through alternative exit policies and compare total R and profit-capture:
- Current (T1 partial + 1×ATR trail + 45-day stale exit)
- Full trail (no partial)
- Partial at T1, fixed T2 (no trail)
- Break-even stop after +1R
- Wider (1.5×/2×ATR) and tighter (0.75×ATR) trails

This is a controlled experiment (same entries, vary one block) → clean attribution. The MFE give-back data from Phase 1 §4 predicts which alternatives are worth testing. **No recommendation unless an alternative beats current on out-of-sample R with non-overlapping confidence intervals.**

## 7. Statistical Significance Review

For every edge claim:
- **Sample size & power:** 7,812 aggregate trades is plenty; *per strategy×regime×year* cells can be thin (e.g. RANGE_REVERSAL in a single bear year) — report N per cell and suppress conclusions under N≈30.
- **Expectancy confidence interval:** bootstrap the trade-level R distribution (10k resamples) → 95% CI on mean R. **If the CI for mean R straddles 0, there is no demonstrated edge**, regardless of the point estimate.
- **Regime robustness:** the edge must survive out-of-sample. **Walk-forward** (e.g. train/observe 2022–2023, validate 2024; roll forward) and report the OOS PF/expectancy separately. The in-sample 1.13 is the ceiling; the OOS number is the truth.
- **Multiple-testing caution:** given the iterative tuning history, treat any single good parameter set with suspicion; require stability across neighboring parameters, not a single peak.

**Phase 2 verdict template (the actual deliverable):**
> "Out-of-sample, on the live decision path, the system shows mean expectancy of **X R/trade (95% CI [a, b])**. The edge is [present everywhere / concentrated in BULL_TRENDING / absent in chop]. Strategy Y carries it; strategy Z is dilutive. Confidence score [is / is not] predictive. Therefore: [EDGE CONFIRMED / EDGE CONDITIONAL ON REGIME / NO EDGE — do not deploy real capital]."

---

# PHASE 3 — Optimization Roadmap (gated)

**Entry gate:** Phase 3 begins **only** if Phase 2 shows a positive out-of-sample expectancy with a CI excluding zero. If the only positive expectancy is regime-conditional, Phase 3's first item is regime-gating, not entry tuning. Every item below is a *template requiring evidence*; nothing here is yet recommended.

## 1. Prioritized Improvement Matrix

| Tier | Trigger (must be measured) | Example finding from current data to confirm first |
|---|---|---|
| **Critical** | Negative OOS expectancy, broken risk metric, untrustworthy backtest | Finding B (backtest ≠ live), Finding C (drawdown metric), 2025–26 losing |
| **High** | A strategy/regime with statistically negative expectancy dragging the book | RANGE_REVERSAL 38% WR; TREND_BREAKOUT adverse win/loss skew — *if* CI confirms |
| **Medium** | Profit leakage with no expectancy loss | MFE give-back via exits — *if* an alternative wins OOS |
| **Low** | Reporting/UX, marginal cost savings | Slippage tuning, dashboard polish |

## 2. Strategy Improvements (each requires Evidence / Expected Impact / Risk)

Candidates that the *current* data merely **hints** at (to be confirmed in Phase 2 before any are actioned):
- **Regime gating** — if edge is BULL_TRENDING-only, gate position size or entries by regime. Evidence required: regime cross-tab with CI. (The live `morning_regime` WAIT mode already partially does this — measure whether it fires at the right times.)
- **Confidence thresholding** — only if the bucket analysis proves monotonicity.
- **HUB_SIGNAL inclusion/exclusion** — only after §5 head-to-head.
- **Exit policy change** — only if §6 produces an OOS winner.
- **Position sizing** — tie size to measured per-regime expectancy, not fixed 1%.

For each: state the **measured** evidence, the expected ΔExpectancy with CI, and the risk (overfitting, regime dependence, reduced sample).

## 3. Production Readiness Review

Readiness is a function of *measured, out-of-sample, correctly-risk-adjusted* performance — not aggregate ₹.

| Capital | Gate |
|---|---|
| ₹1 lakh | OOS expectancy CI > 0; correct max-DD < 15%; ≥6 months forward paper-trade matching backtest |
| ₹10 lakh | Above + liquidity check: position sizes vs ADV (slippage stays modeled); stable across ≥2 regimes |
| ₹50 lakh | Above + capacity analysis (do fills move the book?); live slippage ≈ modeled |
| ₹1 crore | Above + 12-month live track record; drawdown discipline proven through a losing regime |
| ₹10 crore | Above + market-impact modeling, execution algo, capacity ceiling per name; institutional risk controls |

**Current honest status:** the system is **not** ready for real capital at any tier. It has a marginal, in-sample, regime-concentrated, recently-negative backtest on a code path that differs from production, measured with a broken drawdown metric and no out-of-sample validation. Phase 1 + Phase 2 must close those gaps first.

---

## Final Deliverable Index (mapping to the request)

| Requested artifact | Where |
|---|---|
| Measurement Framework Design | Phase 1 §1–§8 |
| Database Changes Required | Phase 1 §1.1 (migration `0003_trade_attribution`) |
| Analytics Dashboard Design | Phase 1 §2–§8 report shapes + §6 portfolio dashboard |
| Backtesting Framework Design | Phase 2 §1 (corrected to run live decision path) |
| Edge Validation Methodology | Phase 2 §2–§6 |
| Statistical Validation Methodology | Phase 2 §7 (bootstrap CI + walk-forward) |
| Optimization Roadmap | Phase 3 §1–§2 |
| Production Readiness Criteria | Phase 3 §3 |

**Sequencing rule restated:** Phase 1 (instrument) → Phase 2 (validate OOS on the real code path) → Phase 3 (optimize only what the data proves is broken). No rule, threshold, exit, or sizing change is made before Phase 2 delivers a verdict with a confidence interval.
