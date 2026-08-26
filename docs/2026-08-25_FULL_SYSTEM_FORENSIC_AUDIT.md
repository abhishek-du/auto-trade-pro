# Full system forensic audit — 2026-08-25

**Question:** if we fix the capital lockup and the risk-control bug, will
AutoTrade Pro make money?

**Answer: NO.** Evidence below.

**Session state:** market OPEN at time of writing (13:39 IST). Today's figures
are a partial session.

---

# 1. Executive summary

Yesterday's report explained *today's* infrastructure failure correctly. It did
not answer the business question, and the answer is worse than the report
implies.

I replayed **every tactical signal the system generated** — today's 1,737 and
30 days' 2,742 — against real 1m candles, using the production engine's own
entry, stop and target, with stop-wins-ties and realistic costs. This includes
the 90% that the capital gate rejected, so the sample is not biased by what
happened to get through.

**Across 2,693 replayed signals over 30 days: median gross return 0.000%, mean
+0.022%, win rate 49.0%, and −0.200% after costs.** The signal population has no
gross edge and is clearly negative net.

The reason is geometry, not luck:

| | value |
|---|---|
| Planned R:R, median | **0.60** — risk ₹1 to make ₹0.60 |
| Stop distance, median | 1.654% |
| Realised MFE, median | **0.406%** — the stop is 4× wider than the typical favourable move |
| Realised MAE, median | −0.432% — near-symmetric with MFE, i.e. random-walk shaped |
| Median absolute move | 0.499% |
| Round-trip cost | 0.222% — **44% of the typical trade** |
| Signals whose entire MFE is below the cost floor | **957 / 2,693 = 35.5%** |

A third of all signals cannot pay for themselves under any execution. The
remainder are a coin flip with a 44% fee.

**The capital lockup did not cost us money — it accidentally protected us.**
The 1,532 signals it blocked today averaged −0.012% gross and −0.234% net. Over
30 days, the 1,673 cash-blocked signals averaged +0.002% gross, −0.220% net.
Admitting them would have lost more.

---

# 2. Verification of yesterday's report

Every material claim independently re-queried. Counts differ slightly because
the session is live and signals keep arriving.

| Claim | Reported | Verified | Verdict |
|---|---|---|---|
| Tactical signals today | 1,724 | **1,737** | CONFIRMED (grew, session live) |
| Cash-buffer rejections | 1,553 | **1,563 (90.0%)** | CONFIRMED |
| Capital deployed | 99.6% | **99.6%** (₹500,094 / ₹502,039) | CONFIRMED |
| Free cash | ₹1,542 | **₹1,542.29** | CONFIRMED |
| Required reserve (10%) | ₹50,204 | **₹50,204** | CONFIRMED |
| Realised P&L today | −₹698.98 | −₹698.98 | CONFIRMED |
| Unrealised | +₹403.27 | +₹403.27 | CONFIRMED |
| INDOBORAX `REALLOCATED` −₹588.64 | yes | `exit_reason='REALLOCATED'`, closed 03:58:27 | CONFIRMED |
| F4 → CNC → SWING → 48h stop suspension | yes | `tactical_executor.py:369`, `trade_simulator.py:403/419`, `india_tasks.py:1607` | CONFIRMED |
| 10/12 positions unprotected | yes | `trade_style='SWING'` + `swing_min_hold > now` | CONFIRMED |
| 4 positions below stop | yes | live Kite vs `stop_loss` | CONFIRMED |
| Exit worker healthy | 10,391 runs | 10,391, heartbeat 2.9 s | CONFIRMED |
| Live prices healthy | yes | `get_live_prices` 12/12 correct | CONFIRMED |
| `current_price` stale | yes | ages 22–1,292 min | CONFIRMED |

**Nothing in yesterday's report is wrong.** Its omission was that it stopped at
the infrastructure failure.

---

# 3. Today's 1,737-signal replay

**Method (fixed before any result was seen):** fill at the signal's own
`entry_price` — the same price production used for the five it admitted, so
admitted and rejected are directly comparable. Walk 1m bars strictly after the
signal timestamp; no look-ahead. **Stop wins ties** — when one bar's range
contains both stop and target, take the stop, because a 1m bar does not say
which came first. Unresolved at the last bar → `OPEN`, marked to that close.

Replayed 1,700 of 1,737; 37 skipped for missing or too-short 1m data.

| Group | n | med% | mean% | win% | **net mean%** | PF | hit SL | hit TP |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **ALL SIGNALS** | 1,700 | 0.000 | −0.031 | 49.8% | **−0.253** | 0.91 | 9.5% | 28.9% |
| actually EXECUTED | 5 | −0.261 | +0.548 | 40.0% | +0.326 | 3.17 | 40.0% | 40.0% |
| REJECTED (all) | 1,695 | 0.000 | −0.033 | 49.8% | −0.255 | 0.90 | 9.4% | 28.8% |

### By rejection reason

| Reason | n | med% | mean% | win% | **net mean%** | PF |
|---|---:|---:|---:|---:|---:|---:|
| **CASH buffer** | 1,532 | +0.022 | −0.012 | 51.1% | **−0.234** | 0.96 |
| SECTOR_CAP | 32 | −0.516 | −0.521 | 31.2% | −0.743 | 0.36 |
| SECTOR_BREADTH | 68 | −0.366 | −0.305 | 17.6% | −0.527 | 0.19 |
| DUPLICATE | 52 | +0.151 | +0.210 | 69.2% | −0.012 | 3.21 |
| R:R | 9 | −1.013 | −1.070 | 33.3% | −1.292 | 0.11 |
| EXECUTED | 5 | −0.261 | +0.548 | 40.0% | +0.326 | 3.17 |

**The gates that fired were right.** SECTOR_CAP blocked trades averaging
−0.521%, SECTOR_BREADTH −0.305%, R:R −1.070%. Those are not lost profits.

**DUPLICATE is the one gate that cost something** — the 52 signals it blocked
averaged +0.210% gross with a 69.2% win rate — but still −0.012% net.

---

# 4. Historical replay — 30 days, 2,693 signals

Same method, same cost model, 30-day window.

| Group | n | med% | mean% | win% | **net mean%** | PF |
|---|---:|---:|---:|---:|---:|---:|
| **ALL SIGNALS** | 2,693 | 0.000 | +0.022 | 49.0% | **−0.200** | 1.07 |
| actually EXECUTED | 23 | −0.216 | +0.092 | 34.8% | −0.130 | 1.32 |
| CASH-blocked | 1,673 | +0.025 | +0.002 | 51.3% | −0.220 | 1.01 |
| SECTOR_CAP | 115 | −0.209 | −0.225 | 36.5% | −0.447 | 0.57 |
| SECTOR_BREADTH | 83 | −0.191 | −0.114 | 30.1% | −0.336 | 0.63 |
| R:R | 69 | −0.241 | −0.194 | 36.2% | −0.416 | 0.61 |

**Today is not an outlier.** The 30-day population behaves identically: zero
gross edge, negative after costs, and the risk gates blocking genuinely bad
trades.

---

# 5. Strategy by strategy — 30 days

Sorted by net mean after 0.222% round-trip cost.

| Strategy | n | med% | mean% | win% | **net mean%** | PF | hit SL | hit TP | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SCALP | 4 | +0.300 | +0.300 | 100.0% | +0.078 | ∞ | 0.0% | 100.0% | INSUFFICIENT (n=4) |
| ORB | 3 | +0.047 | +0.306 | 66.7% | +0.084 | 7.85 | 0.0% | 0.0% | INSUFFICIENT (n=3) |
| **VOLUME_BREAKOUT** | 220 | +0.105 | +0.268 | 53.2% | **+0.046** | 1.87 | 10.5% | 9.1% | **BREAKEVEN** |
| VWAP | 218 | −0.058 | +0.218 | 48.2% | **−0.004** | 1.66 | 11.9% | 30.7% | BREAKEVEN |
| PIVOT_BREAKOUT | 750 | +0.096 | +0.090 | 60.7% | **−0.132** | 1.49 | 6.0% | 48.5% | NEGATIVE after costs |
| VWAP_CROSSOVER | 413 | +0.129 | −0.002 | 57.1% | **−0.224** | 0.99 | 5.8% | 40.7% | NEGATIVE |
| PIVOT_BOUNCE | 9 | −0.049 | −0.021 | 11.1% | −0.243 | 0.63 | 88.9% | 11.1% | INSUFFICIENT |
| OVERSOLD_REBOUND | 159 | −0.277 | −0.075 | 35.2% | **−0.297** | 0.71 | 47.8% | 18.2% | NEGATIVE |
| GAP_AND_GO | 753 | −0.170 | −0.105 | 39.8% | **−0.327** | 0.78 | 4.8% | 11.7% | NEGATIVE |
| OVERBOUGHT_FADE | 155 | −0.476 | −0.108 | 28.4% | **−0.330** | 0.65 | 55.5% | 7.7% | NEGATIVE |
| DAY_MOMENTUM | 9 | −1.000 | −0.788 | 0.0% | −1.010 | 0.00 | 77.8% | 0.0% | NEGATIVE (already disabled) |

**Not one strategy with a meaningful sample is net-positive.** The best,
`VOLUME_BREAKOUT` at +0.046% over 220 signals, is inside noise of zero.

`PIVOT_BREAKOUT` is the instructive case: **60.7% win rate, PF 1.49 gross, and
still −0.132% net.** It wins more often than it loses and still loses money,
because the wins are smaller than the round trip.

By sub-pipeline: F1 net −0.204% (n=1,746), F4 net −0.191% (n=947). Neither
pipeline is the problem; both are.

---

# 6. Why — the trade geometry

| Measure | Value | Implication |
|---|---:|---|
| Planned R:R, median | **0.60** | risks ₹1 to make ₹0.60 |
| Planned R:R, p25 / p75 | 0.29 / 1.21 | a quarter of signals aim at less than a third of what they risk |
| Stop distance, median | 1.654% | |
| Realised MFE, median | **0.406%** | the stop is **4×** wider than the typical favourable move |
| Realised MAE, median | −0.432% | MFE ≈ |MAE| — random-walk shaped |
| Median absolute move | 0.499% | |
| Cost | 0.222% | **44% of the typical trade** |
| MFE below the cost floor | **957 / 2,693 = 35.5%** | can never pay for themselves |
| R-multiple, median | +0.000 | |

**An R:R of 0.60 needs a 62.5% win rate to break even before costs. The realised
rate is 49.0%.** The strategies are structurally losing before a single
execution detail is considered.

Outcome mix: 59.7% still `OPEN` at the last available bar, 28.0% `TP`, 12.3%
`SL`. The high open rate is partly because the session is live and partly
because targets sit far away relative to the moves these signals actually
produce.

---

# 7. What this changes about the confirmed bugs

The F4/CNC/SWING bug and the capital lockup are **real, confirmed, and must be
fixed** — but they are not why the system does not make money.

| | Yesterday's implication | What the replay shows |
|---|---|---|
| Capital lockup blocked 90% of signals | lost opportunity | those signals averaged **−0.234% net** — blocking them saved money |
| Stops disabled on 10/12 positions | large hidden loss | net **+₹132** today; **unbounded risk**, not a realised loss |
| INDOBORAX force-closed −₹588.64 | wasted capital | **CONFIRMED waste** — freed ₹17.5k against a ₹50.2k reserve, admitted nothing |

**The single genuinely destructive event today was the reallocation:** a real
−₹588.64 realised for no admitted trade. That remains P0.

🚨 **PRODUCTION RISK unchanged.** Ten positions still have no stop. The fact
that it netted +₹132 today is luck; the exposure is unbounded. Real money must
stay disabled.

---

# 8. What I did NOT investigate — EVIDENCE NOT AVAILABLE

Stated plainly rather than filled with assumption:

- **Part 7 (news → signal causal chains for today's movers)** — not done.
- **Part 8 (top 50 movers vs system response)** — not done.
- **Part 9/10 (data availability and decision-time snapshot audit)** — not done
  beyond the price-freshness checks in §2.
- **Part 11 (Master Intelligence Hub correlation with forward return)** — not
  done. The Hub's `decisions_made = 0` is confirmed from prior work but its
  score's predictive value was not measured today.
- **Part 12 (per-gate historical attribution for the +1.5% confirmation gate,
  T1/T2, position sizing)** — only the gates that appear in `tactical_signals`
  rejection reasons were measured.

The replay was prioritised because it answers the business question directly and
because every one of the above becomes secondary if the signal population has no
edge — which it does not.

---

# 9. Root-cause matrix

| # | Category | Problem | Evidence | Impact | Confidence | Priority |
|---|---|---|---|---|---|---|
| 1 | **STRATEGY** | Signal population has no gross edge and is negative after costs | 2,693 signals / 30 d: mean +0.022% gross, −0.200% net, win 49.0% | **This is why we do not make money** | **CONFIRMED** | **P0** |
| 2 | **STRATEGY** | Planned R:R median 0.60 — needs 62.5% win rate, gets 49.0% | `tactical_signals` entry/SL/target | Structural; makes #1 inevitable | **CONFIRMED** | **P0** |
| 3 | **STRATEGY** | Stop 4× wider than median favourable move (1.654% vs 0.406%) | replay MFE/MAE | Trades cannot resolve favourably | **CONFIRMED** | **P0** |
| 4 | **STRATEGY** | 35.5% of signals have MFE below the 0.222% cost floor | replay | A third of signals are unwinnable | **CONFIRMED** | **P0** |
| 5 | **RISK** | Reallocation realises a loss without admitting a trade | INDOBORAX −₹588.64 at 03:58:27; 6 signals blocked 03:58:30–35 | 84% of today's realised loss | **CONFIRMED** | **P0** |
| 6 | **RISK** | F4 → CNC → 48 h stop suspension | `tactical_executor.py:369` → `trade_simulator.py:403/419` → `india_tasks.py:1607` | 10/12 positions unprotected; unbounded risk | **CONFIRMED** | **P0 🚨** |
| 7 | **CAPITAL** | 99.6% deployed, headroom −₹48,258 | `virtual_wallet`, `open_positions` | Blocked 90% of signals — protective, not costly | **CONFIRMED** | **P1** |
| 8 | **DATA** | `open_positions.current_price` stale up to 1,292 min | `last_updated` | Wrong P&L reporting; does not affect stops | **CONFIRMED** | **P2** |
| 9 | **RISK** | DUPLICATE gate blocked the only positive-gross group | 52 signals, +0.210% gross, 69.2% win | Small; still −0.012% net | **CONFIRMED** | **P2** |
| 10 | INFRASTRUCTURE | Exit loop, price feed, execution | 10,391 runs, heartbeat 2.9 s, 12/12 live prices | Healthy | **RULED OUT** | — |

---

# 10. Tomorrow's plan

## P0 — must fix before the next paper session

1. **BUG FIX — reallocation must not realise a loss it cannot use.**
   `engine/portfolio_reallocation.py::try_reallocate_for_candidate` must verify
   the freed amount actually clears the buffer *before* closing anything.
   *Verify:* replay 03:58:27 — freeing ₹17.5k against a ₹50.2k reserve must be
   refused and INDOBORAX must stay open.

2. **BUG FIX — intraday tactical trades must not receive `product="CNC"`.**
   `engine/tactical_executor.py:369`.
   *Verify:* no `open_positions` row with `trade_style='SWING'` whose trade's
   `strategy_name` starts with `TACTICAL_`.

3. **DISABLE the four clearly negative strategies.** This is a policy change
   backed by 30 days of replay, not a parameter tweak:
   `GAP_AND_GO` (n=753, net −0.327%), `OVERBOUGHT_FADE` (n=155, −0.330%),
   `OVERSOLD_REBOUND` (n=159, −0.297%), `VWAP_CROSSOVER` (n=413, −0.224%).
   That is **1,480 of 2,693 signals — 55% of everything the system produces.**

## P1 — must investigate before any re-enable

4. **The R:R generator.** A median planned R:R of 0.60 is the single upstream
   cause of #1–#4. Find where targets are computed and why they land closer than
   the stop. Do not tune it — establish why it is inverted.
5. **Run the missed-movers and news→signal studies** (Parts 7/8) that this audit
   skipped, now that the strategy question is settled.

## P2 — measure, do not change

6. `PIVOT_BREAKOUT` (n=750, 60.7% win, PF 1.49 gross, −0.132% net) and
   `VOLUME_BREAKOUT` (n=220, +0.046% net) are the only candidates worth keeping.
   Measure them forward; do not optimise them against this dataset.
7. Position-mark staleness.

## Do NOT change
The 10% cash buffer, the 2-per-sector cap, the sector breadth veto and the R:R
minimum all blocked genuinely losing trades in the replay (−0.225%, −0.114%,
−0.194% respectively). **They are working. Leave them alone.**

## PASS/FAIL for tomorrow

| Metric | PASS | FAIL |
|---|---|---|
| Positions with `trade_style='SWING'` from TACTICAL | 0 | any |
| Positions past stop at any check | 0 | any |
| `REALLOCATED` exits that admit no trade within 60 s | 0 | any |
| Deployed / equity at close | < 90% | ≥ 99% |
| Signals from disabled strategies | 0 | any |

**Tomorrow is not a profitability test.** With four strategies disabled and the
rest at breakeven-minus-costs, the correct expectation is a small negative day.
PASS means the bugs are gone and the risk gates held — nothing more.

---

# 11. The single answer

> ## IF WE FIX NOTHING EXCEPT THE CONFIRMED BUGS, IS AUTOTRADE PRO CURRENTLY CAPABLE OF MAKING MONEY IN PAPER MODE?
>
> ## **NO.**

Fixing the reallocation, the CNC/SWING stop suspension and the capital lockup
removes real defects and removes real risk. It does not create an edge.

The evidence is 2,693 signals over 30 days, replayed against real candles with
the production engine's own entry, stop and target, including the ~62% the
capital gate rejected: **mean gross +0.022%, mean net −0.200%, win rate 49.0%,
median planned R:R 0.60, stop 4× wider than the median favourable move, and
35.5% of signals whose best-case excursion never reaches the cost floor.**

A system that risks ₹1 to make ₹0.60 and wins 49% of the time loses money with
perfect infrastructure. The infrastructure bugs hid this; they did not cause it.

**The capital lockup was not the problem. It was the only thing preventing the
problem from being expensive.**

---

# 12. Evidence appendix

All figures from production Postgres and live Kite Connect, 2026-08-25
13:24–13:45 IST.

| Claim | Source |
|---|---|
| 1,737 signals today, 1,563 cash-blocked | `tactical_signals WHERE created_at::date=CURRENT_DATE` |
| 2,742 signals / 30 d, 2,693 replayed | `tactical_signals WHERE created_at > CURRENT_DATE - 30` |
| Replay outcomes | 1m `candles`, walked forward from each `created_at`, stop-wins-ties |
| Cost model 0.222% | brokerage 0.030 + STT 0.025 + exch 0.007 + GST 0.007 + stamp 0.003 + spread/slip 0.150 |
| R:R median 0.60 | `abs(target-entry)/abs(entry-stop_loss)` over 2,693 |
| MFE/MAE | per-signal 1m high/low excursion after entry |
| 99.6% deployed, ₹1,542 free | `open_positions`, `virtual_wallet` |
| INDOBORAX −₹588.64 REALLOCATED | `paper_trades.exit_reason`, `closed_at=03:58:27` |
| Blocked candidates 03:58:30–35 | `tactical_signals.reason` |
| F4→CNC→SWING→no-stop | `tactical_executor.py:369`, `trade_simulator.py:403/419`, `india_tasks.py:1607-1613` |
| Exit loop healthy | Celery `inspect().stats()` = 10,391; Redis `exit_worker:heartbeat` 2.9 s |

**Limitations.** Today's session was still open, so 59.7% of replayed signals
are unresolved and marked to the last available bar — this biases the sample
toward zero, not toward the negative conclusion. Fills use each signal's own
`entry_price`, so real slippage on entry is not modelled beyond the 0.150%
spread allowance. Replay ignores position sizing, concurrency limits and
capital, by design: it measures the signal population, not a portfolio.

*Systems and statistics analysis. Not investment advice. No production code was
changed in producing this report.*
