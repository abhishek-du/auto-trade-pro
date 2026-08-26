# PHASE 22 — ECONOMICALLY CORRECT OPPORTUNITY → TRADE CONVERSION AUDIT

**Mode:** READ-ONLY. No strategy, threshold, routing, capital, exit or execution
change. **Nothing was deployed in this phase** — see §10 for why the one
candidate fix failed its own gate.

---

## The answer to the primary question

Not one of A–E. **F: a combination, and the components are unequal.**

| | Verdict |
|---|---|
| **A — fails to detect attractive opportunities** | **RULED OUT.** The signal stage picks opportunities with materially higher peak excursion than the baseline. |
| **B — detects but filters/ranks them out** | **INSUFFICIENT EVIDENCE.** Ranks 16–40 are discarded before persistence and exist in no table on any date. |
| **C — risk/capital prevents execution** | **PARTIALLY SUPPORTED.** Real, but downstream of a larger problem. |
| **D — poor entry quality** | **CONFIRMED and dominant.** Median MFE on intraday TACTICAL trades is **0.00%**. |
| **E — cost accounting distorts results** | **CONFIRMED.** ₹3,588 of the recorded loss is a charging defect, not trading. |

**The binding constraint is D, and E has been hiding its size.**

---

## 1. Corrected P&L baseline

`paper_trading/trade_simulator.py:133` — `estimate_trade_cost()` takes no
`product` argument, its docstring says *delivery*, and `:142` charges delivery
STT (0.1% on **both** legs) to every trade. NSE equity intraday STT is 0.025% on
the **sell leg only**.

Reproduced independently from stored `entry_price` / `exit_price` /
`size_units` / `pnl`:

| Product | n | median charged | correct |
|---|---:|---:|---|
| MIS | 44 | **0.294%** | ~0.11% |
| CNC | 28 | 0.294% | 0.294% ✓ |

Identical rates for products whose real costs differ by ~3×.

### The corrected baseline

| | ₹ |
|---|---:|
| Gross P&L (from stored prices) | **9,633** |
| As charged today | **806** |
| **Corrected (product-aware)** | **4,393** |
| **Charging defect** | **+3,588** |

**82% of the recorded profit has been consumed by costs; nearly half of those
costs were charged in error.** → **CONFIRMED**

### Consumers — exhaustive

`estimate_trade_cost` is called **only** at trade-close in
`trade_simulator.py:502,505,584,587`, plus a separate copy in
`engine/agent/backtester.py:22`. It is called **zero** times in
`risk_manager.py`, `decision_router.py`, `zerodha_executor.py`,
`agent/execution.py` and `virtual_wallet.py`.

**But it is NOT behaviour-neutral**, and this is the finding that blocks
deployment:

```
trade_simulator.py:507   partial_pnl = gross_pnl - cost
                  :523   VirtualWallet.return_margin(session, close_usd, partial_pnl, ...)
                            ↓
risk_manager.py   :239   equity = wallet_balance + deployed_capital + unrealised
                            ↓
                  :592   max_notional = balance × AGENT_MAX_POSITION_WEIGHT
```

Overcharging costs shrinks the wallet, which shrinks future position sizes and
brings capital exhaustion forward. ₹3,588 against a ₹5.02L wallet is ~0.7% of
equity — small, but **not zero**, and the brief's gate is *behaviour-neutral*.

→ **The cost fix is a real defect that must NOT be deployed under this phase's
gate.** Design in §10.

---

## 2–3. The 1,105 opportunity control group and the selection test

Phase-21 definition used **exactly as documented**, unmodified after seeing any
outcome: in-universe (turnover ≥ ₹5cr), priced > ₹20, ≥30 prior 1m bars,
30-bar breakout, volume ≥ 2× 30-bar mean, 09:45–15:00 IST, first event per
symbol.

**1,105 opportunities · 80 signalled after t0 · 1,025 not signalled.**

### Signalled vs not — every horizon

| Horizon | Group | med MFE | med MAE | ≥+0.25% | ≥+0.5% | ≥+1% | ≥+2% |
|---|---|---:|---:|---:|---:|---:|---:|
| 5m | **signalled** | 0.12% | −0.14% | 34% | 16% | 11% | 1% |
| 5m | not signalled | 0.09% | −0.12% | 24% | 10% | 3% | 0% |
| 15m | **signalled** | 0.26% | −0.23% | 51% | 34% | 14% | 2% |
| 15m | not signalled | 0.14% | −0.20% | 35% | 18% | 7% | 1% |
| 30m | **signalled** | 0.43% | −0.28% | 60% | 49% | 28% | 6% |
| 30m | not signalled | 0.22% | −0.27% | 46% | 28% | 12% | 3% |
| 60m | **signalled** | 0.59% | −0.32% | 65% | 52% | 35% | 12% |
| 60m | not signalled | 0.29% | −0.34% | 54% | 36% | 18% | 6% |

The signalled group leads on every metric at every horizon.

### The rigorous version — and it tempers that considerably

MFE is a **peak**, not something a trade can capture. Adding the capturable
close-to-close return, with a 3,000-resample bootstrap (seed 11) on the
difference in medians:

| Metric at 60m | signalled | not signalled | diff | 95% CI | verdict |
|---|---:|---:|---:|---|---|
| **MFE (peak)** | +0.589% | +0.294% | +0.295pp | **[+0.003, +0.503]** | **excludes zero** |
| **Return (capturable)** | +0.092% | −0.062% | +0.154pp | [−0.077, +0.416] | **includes zero** |
| MAE | −0.315% | −0.336% | +0.021pp | [−0.121, +0.082] | includes zero |

**The selection edge is established on peak excursion and NOT established on
realisable return.** → MFE edge **CONFIRMED**; return edge **INSUFFICIENT
EVIDENCE** at n=80.

### Net economics — neither group is profitable at 60m

| Cost model | signalled | not signalled |
|---|---:|---:|
| As charged today (0.294%) | **−0.202%** | −0.356% |
| Corrected intraday (~0.11%) | **−0.018%** | −0.172% |

Even the selected group loses money at a 60-minute horizon under either cost
model. Correcting the cost defect moves the signalled group from clearly
negative to roughly break-even — **it does not make it profitable**.

**This reconciles with Phase 15.** That phase found executed signals
indistinguishable from rejected ones (+0.736% vs +0.744%) — a comparison
*within* the signal pool. This phase compares *signalled vs unsignalled
opportunities*, a wider stage. Both can hold: signal generation shows selection
on peaks; execution selection among signals adds nothing further.

---

## 4. Conversion funnel

| Stage | Count | % of prior | First-loss confidence |
|---|---:|---:|---|
| t0 opportunities | 1,105 | — | CONFIRMED |
| → tactical signal after t0 | 80 | **7.2%** | CONFIRMED |
| → symbol traded that day | 28 | 35% of signalled · **2.5%** of opportunities | CONFIRMED |

**Stages 1–5 of the requested ten cannot be separated for 2026-08-26.** The
Phase-21 telemetry that distinguishes *outside universe · in universe not
scanned · scanned no rule · score rejected · ranked out* deployed **after the
session closed**. Per the brief's instruction not to infer:

**Stages 1–5: UNKNOWN. Stages 6–10: partly readable from
`tactical_signals.reason` (R:R, sector, concurrency, capital, executed).**

First data: 2026-08-27.

---

## 5. Top-N — the data does not exist

**NOT MEASURED, and not measurable from stored data on any date.**

Scoring keeps 40 (`"keeping 40"` in the scan log), then
`tactical_executor.py:218` applies `rank_signals(top_n=TACTICAL_TOP_N)`.
**Ranks beyond the cut are discarded before anything is written.** Maximum
signals ever persisted in one scan minute across all history is **30**,
consistent with two pipelines at 15 each.

The Phase-21 funnel row does not close this either — it records counts, not the
discarded signals themselves.

**No claim is made about whether ranks 16–40 contain value.** `TACTICAL_TOP_N`
is unchanged, as required.

---

## 6. Entry quality — the binding constraint

Corrected costs, split as the brief requires. Same-day close is **not** used as
a counterfactual for multi-day trades.

| Family | Scope | n | gross | as-charged | corrected | delta | **med MFE** |
|---|---|---:|---:|---:|---:|---:|---:|
| TACTICAL | intraday | 47 | 1,186 | −5,248 | **−1,742** | +3,506 | **0.00%** |
| TACTICAL | multi-day | 14 | 8,925 | 7,097 | 7,155 | +59 | 0.48% |
| DIRECT_NEWS | multi-day | 4 | −405 | −588 | −588 | 0 | 0.66% |
| DIRECT_NEWS | intraday | 2 | −246 | −361 | −361 | 0 | 0.00% |
| EVENT_DRIVEN | multi-day | 2 | −130 | −250 | −250 | 0 | 0.89% |
| EVENT_DRIVEN | intraday | 1 | 158 | 121 | 121 | 0 | 2.22% |
| **ALL** | | **72** | **9,633** | **806** | **4,393** | **+3,588** | |

**The single most important number in this report: median MFE on the 47
intraday TACTICAL trades is 0.00%.** The median intraday trade never moves in
its favour at all — not far enough to cover costs, not far enough to reach a
target, not at all.

Multi-day TACTICAL is where the money is (+₹7,155 corrected, n=14) and its
median MFE is 0.48%. News families remain unusable at n=1–4.

→ **Entry quality CONFIRMED as the dominant constraint.**

---

## 7. Exit families, corrected

| Exit | n | gross | as-charged | corrected | delta |
|---|---:|---:|---:|---:|---:|
| TAKE_PROFIT | 4 | 5,406 | 5,037 | **5,023** | −14 |
| T1_REVERSAL_EXIT | 4 | 3,837 | 3,299 | **3,456** | +158 |
| STOP_LOSS | 24 | 2,795 | −75 | **390** | +465 |
| CONFIRMATION_LOST | 2 | −246 | −361 | −361 | 0 |
| MIS_SQUAREOFF | 18 | −1 | −2,215 | **−807** | +1,407 |
| REALLOCATED | 2 | −1,026 | −1,132 | −1,133 | 0 |
| EXHAUSTION | 18 | −1,132 | −3,747 | **−2,175** | +1,572 |

**MIS_SQUAREOFF's gross P&L is −₹1.** Eighteen positions, essentially zero
price movement in aggregate, and −₹807 after corrected costs. It is not
destroying value; it is closing positions that went nowhere and paying friction
to do it — the same story as the entry-quality finding.

**No exit change is recommended.** T1_REVERSAL_EXIT (n=4) and CONFIRMATION_LOST
/ REALLOCATED (n=2) are far too small; EXHAUSTION's −₹2,175 across 18 trades sits
on gross of −₹1,132, so most of it is cost on trades that never worked.

---

## 8. Where ₹100 of opportunity disappears

Per opportunity, at the 60-minute horizon, using median capturable return:

| Stage | Count | % | Economic reading | Confidence |
|---|---:|---:|---|---|
| t0 opportunities | 1,105 | 100% | median return **−0.062%** — the raw pool is not profitable | CONFIRMED |
| → signalled | 80 | 7.2% | median return **+0.092%** — selection lifts it above zero | CONFIRMED (return diff not statistically established) |
| → traded | 28 | 2.5% | — | CONFIRMED |
| Transaction cost, as charged | — | — | **−0.294%** — wipes out the +0.092% three times over | CONFIRMED |
| Transaction cost, corrected | — | — | **−0.11%** — still exceeds the +0.092% | CONFIRMED |

**₹100 of opportunity disappears before any exit rule is involved.** The
median selected opportunity gains **+0.092%** over an hour against a corrected
cost floor of **0.11%**. The trade is under water at entry, arithmetically,
before ranking, risk, capital or exits touch it.

That is the economic bottleneck. It is not detection (A), not ranking (B), not
capital (C), and not exits.

---

## 9. Hindsight controls observed

The opportunity set was defined at t0 using only prior-bar information, fixed
before any forward return was computed, and **not modified afterwards**. No
stock was selected because it rose. No threshold was lowered after seeing
winners. No recommendation rests on a single day's example.

**This corrects my own earlier work:** the "14 biggest movers" framing in
Phases 19B/20 selected on outcome and is superseded by the 1,105-opportunity
denominator here.

---

## 10. Deployment gate — nothing deployed

The only candidate was the cost-model correction, and **it fails the gate**.

`cost → partial_pnl → VirtualWallet.return_margin → wallet_balance →
risk_manager sizing` is a real causal chain. Correcting the charge raises the
wallet by ~₹3,588 (~0.7% of equity), which changes future position sizes. Small,
but the gate says *behaviour-neutral*, and this is not.

**Design for when it is authorised separately:**

1. `estimate_trade_cost(qty, price, side, product="CNC")` — add the parameter
   with the current delivery behaviour as the default, so every existing caller
   is unchanged until it opts in.
2. Intraday branch: STT 0.025% sell-leg only; stamp 0.003% buy-leg.
3. Callers at `:502,505,584,587` pass `trade.product`.
4. **Do not rewrite `paper_trades`.** History stays as charged; the corrected
   figure is a derived view, computed exactly as this report does — from
   `entry_price`, `exit_price`, `size_units`, `pnl` — so old and corrected P&L
   remain simultaneously auditable.
5. Product-aware tests: MIS ≠ CNC for the same notional; a zero-move MIS trade
   costs ~0.11%; a zero-move CNC trade costs ~0.294%; default argument preserves
   today's behaviour exactly.
6. Deploy with a before/after wallet reconciliation, since the wallet will step
   up on the first corrected close.

`engine/agent/backtester.py:22` holds a **duplicate copy** of the same function
and has the same defect. It must be corrected in the same change or the
backtest and the simulator will disagree.

**Unchanged, as required:** `TACTICAL_TOP_N`, turnover threshold, R:R, capital
limits, AI routing, BUG-1, exits, prompts, Master Score, execution rules.

**Tests:** no code changed, so the Phase-21 baseline of **1,777 passed / 27
failed / 7 skipped / 5 errors** stands untouched. Zero new failures by
construction.

---

## Recommended next experiments — no strategy changes

| # | Experiment | Why | Risk |
|---|---|---|---|
| 1 | **Deploy the cost fix under its own gate**, with wallet reconciliation | ₹3,588 of recorded loss is fictional and it distorts every cost-floor comparison | low, but not neutral |
| 2 | **Re-run this audit on 2026-08-27** with Phase-21 telemetry live | closes funnel stages 1–5, currently UNKNOWN | none |
| 3 | **Persist discarded ranks 16–40** (telemetry only) | the only way PART 5 ever becomes answerable | none |
| 4 | **Repeat the 80-vs-1,025 test across 10+ sessions** | the return-edge CI includes zero at n=80; more sessions may resolve it | none |
| 5 | **Ask why intraday TACTICAL has median MFE of 0.00%** while multi-day has 0.48% | this is the whole problem, and the split is a real clue | none |

Experiments 2–5 are read-only. **No strategy change is recommended by this
phase**, because the dominant finding — entry quality — has no safe fix that
this evidence supports yet.

---

## Classification of every conclusion

| Conclusion | Status |
|---|---|
| `estimate_trade_cost` ignores product; MIS charged delivery STT | **CONFIRMED** |
| ₹3,588 of recorded loss is a charging defect | **CONFIRMED** |
| The cost fix is behaviour-neutral | **RULED OUT** — wallet → sizing chain |
| Signal stage selects higher peak excursion | **CONFIRMED** (bootstrap CI excludes zero) |
| Signal stage selects higher realisable return | **INSUFFICIENT EVIDENCE** (CI includes zero, n=80) |
| Selected opportunities are profitable after costs | **RULED OUT** — −0.018% even corrected |
| Detection is the bottleneck (A) | **RULED OUT** |
| Ranking/top-N is the bottleneck (B) | **INSUFFICIENT EVIDENCE** — data does not exist |
| Capital is the bottleneck (C) | **PARTIALLY SUPPORTED** — real, but downstream |
| Entry quality is the bottleneck (D) | **CONFIRMED** — median intraday TACTICAL MFE 0.00% |
| Cost accounting distorts results (E) | **CONFIRMED** |
| MIS_SQUAREOFF destroys value | **RULED OUT** — gross P&L −₹1 |
| Any exit family warrants change | **INSUFFICIENT EVIDENCE** |
| Funnel stages 1–5 for 2026-08-26 | **NOT MEASURED** — telemetry deployed after close |
| Ranks 16–40 value | **NOT MEASURED** — never persisted, any date |

---

*Read-only throughout. All figures reproducible from `paper_trades`,
`candles`, `hub_universe` and `tactical_signals`. No production code,
configuration, database row or order was touched, and nothing was deployed.*
