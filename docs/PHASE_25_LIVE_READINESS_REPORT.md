# PHASE 25 — LIVE READINESS

Prepared 2026-08-26 evening for the 2026-08-27 session.
**Strategy changes: NONE. Control mode: ACTIVE. Experimental exit: SHADOW ONLY.**

---

## PHASE 25 READY

```
Git
  branch  = fix/audit-2026-08-19-critical
  baseline = 50d6834   (recorded in PHASE_25_BASELINE.md)

Tests
  passed  = 1,818        (baseline 1,791)
  failed  = 27           (baseline 27 — unchanged, all pre-existing)
  skipped = 7
  errors  = 5            (baseline 5 — unchanged)
  delta   = +27 passed, ZERO new failures

Local fixes
  1. V1_COST_CORRECTED — product-aware transaction costs   [DEPLOYED]
  2. Candle-derived MFE/MAE at close                        [DEPLOYED]

Telemetry
  1. Scan funnel counters + per-scan row      (Phase 21, live)
  2. Universe snapshot per rebuild            (Phase 21, live)
  3. Exit horizon shadow experiment           (Phase 25, manual, research-only)

Control mode      = ACTIVE (the only exit behaviour that exists)
Experimental exit = SHADOW ONLY, proven incapable of trading
Strategy changes  = NONE

Services          = 7/7 active, NRestarts=0, 0 import errors, endpoints 200
```

---

## Section A — System health

| Service | PID | State | Restarts |
|---|---|---|---|
| autotrade-uvicorn | 3487886 | active | 0 |
| autotrade-celery-worker | 3487712 | active | 0 |
| autotrade-celery-trade-worker | 3487644 | active | 0 |
| autotrade-celery-exit-worker | 3487643 | active | 0 |
| autotrade-celery-scan-worker | 3487640 | active | 0 |
| autotrade-celery-beat | 3300942 | active | 0 |
| autotrade-news-engine | 3391682 | active | 0 |

Import / syntax / name errors after restart: **0** across all five worker logs.
`/portfolio/`, `/portfolio/positions`, `/agent/status`: **200**.

---

## SAFE FIX #1 — V1_COST_CORRECTED

Built in Phase 23, verified and **deployed tonight**.

| Check | Result |
|---|---|
| `estimate_trade_cost(qty, price, side, product="CNC")` | ✅ both simulator and backtester |
| MIS uses intraday STT (0.025%, sell leg only) | ✅ |
| CNC retains delivery behaviour (0.294% round trip) | ✅ pinned by test |
| Call sites pass `trade.product` | ✅ `:533,538,619,624` |
| Historical `paper_trades` rewritten | ❌ **no** — corrected P&L is derived |
| Unknown / NULL product | falls back to delivery — fail expensive, never cheap |

### Wallet reconciliation

| | ₹ |
|---|---:|
| Starting balance | 477,054.99 |
| Starting equity | 501,863.67 |
| Open positions affected | **1** (JUNIPER.NS, **CNC**) |
| **Expected immediate wallet impact** | **+0** |
| As % of equity | 0.000% |
| Position-sizing impact tonight | **none** |

**The deployment lands on a clean boundary.** The only open position is CNC, so
the correction changes nothing retroactively; the behaviour change begins with
the first MIS trade tomorrow, where it will be attributable.

**This is a behaviour-affecting accounting correction, not telemetry.** The chain
is real: `:507 partial_pnl → VirtualWallet.return_margin → wallet_balance →
risk_manager sizing`. It is deployed alone, identifiable, and reversible.

---

## SAFE FIX #2 — Candle-derived MFE/MAE

`paper_trading/trade_simulator.py::_candle_excursion`, called from
`close_paper_trade` **after** price, P&L, status and exit reason are settled.

### The defect it replaces

MFE came from a tracker inside `update_positions_with_current_prices()`, so it
only advanced when that task happened to run — bounded by trade-loop cadence
(53.7% coverage, gaps to 605s) and price freshness (p50 16-minute lag).

Measured on 38 intraday TACTICAL trades:

| | stored | candle-derived |
|---|---:|---:|
| median | 0.010% | **0.353%** |
| stored exactly 0.00 | **17 / 38** | — |
| of those, real movement > 0.1% | **11** | — |

PAYTM stored 0.00% against a candle MFE of **5.31%**.

### Guarantees, each enforced by a test

- **No look-ahead** — the window ends at the trade's own exit; the SQL is bounded
  by `timestamp <= :b` and contains no `now()` or `utcnow`.
- **Cannot affect trading** — runs after every trading field is assigned, and a
  regex guard proves the block writes *only* `mfe_abs / mae_abs /
  max_open_profit / mfe_pct / mae_pct / mfe_r / mae_r`.
- **Degrades to the old behaviour** — a query failure or an empty window keeps
  the tracker's values.
- Timestamp conventions verified aligned: `paper_trades.opened_at` and
  `candles.timestamp` are both UTC-naive (03:45–09:59 raw = 09:15–15:29 IST).

16 tests: long/short arithmetic, the between-samples peak, a never-favourable
position, window boundaries, multi-day windows, missing candles, four
missing-input cases, and three isolation guards.

`_mfe_src` records `"candles"` or `"tracker"` per close, so tomorrow's report can
say which trades used which.

---

## Sections B–H — the exit experiment, and what tomorrow will produce

### CONTROL vs EXPERIMENT

**CONTROL is not a mode — it is the only behaviour.** No `EXIT_MODE` switch was
added to the trading path, and a test forbids one. The existing exit stack is
byte-identical to this morning.

**EXPERIMENT** is `scripts/research/exit_horizon_shadow.py`, run manually after
the session. For every closed trade it computes, from candles, what holding to
+30 / +60 / +90 / +120 minutes and to the session close would have produced —
net of product-aware costs — alongside the actual exit.

**A design constraint worth stating:** the hypothetical horizons need candles
that do not exist at the moment a position closes. Computing them inside
`close_paper_trade` is therefore impossible. The experiment runs after the
close instead, which also makes the isolation absolute rather than conditional.

### Isolation — proven, not asserted

11 tests enforce that the shadow module:

- imports **none** of `trade_simulator`, `decision_router`, `zerodha_executor`,
  `agent/execution`, `risk_manager`, `virtual_wallet`
- never references `close_paper_trade`, `open_paper_trade`, `place_real_order`,
  `execute_trade_intent`, `route_decision`, `scale_out_paper_trade`,
  `deduct_margin`, `return_margin`
- constructs no `PaperTrade`, `OpenPosition`, `TacticalSignal`, `VirtualWallet`
  or `AgentDecision` — only `SimulationLog`
- contains no `UPDATE`, `DELETE`, `DROP` or `ALTER` SQL
- **is imported by no production module** (verified by scanning every non-test,
  non-script `.py` in the backend)
- **is not in the beat schedule** — it must be run deliberately

Run it with:

```bash
cd autotrade-backend
PYTHONPATH=$PWD .venv/bin/python scripts/research/exit_horizon_shadow.py 2026-08-27
```

---

## Section I — What was NOT built, and why

Reported plainly rather than glossed.

| Brief item | Status |
|---|---|
| §7 per-stage timestamps (`scan_ts`, `risk_ts`, `capital_ts`, …) | **NOT BUILT.** Phase-21 telemetry gives per-scan counts and the dropped-symbol lists, and `tactical_signals` carries the signal timestamp and rejection reason. The intermediate per-candidate timestamps do not exist. Tomorrow's funnel will therefore have stage *counts* but not stage *latencies*. |
| §8 ranks 16–40 research capture | **NOT BUILT.** Ranks beyond the cut are discarded at `tactical_executor.py:218` before any write. Capturing them is a real change to the scan path and I did not want to touch that path the night before a controlled session. **Tomorrow cannot answer whether ranks 16–40 are valuable.** |
| §11 automatic end-of-day dashboard | **NOT BUILT.** The shadow script produces the exit comparison; the opportunity funnel and signal-quality sections must be produced by running the Phase-24 research script against tomorrow's data. |
| §9 opportunity funnel row-per-opportunity | **PARTIAL.** The Phase-21 t0 definition can reconstruct the denominator from candles after the fact, which is how Phases 22–24 did it. There is no live per-opportunity record. |

Three of the four gaps share one cause: **they require changes to the live scan
path**, and doing that tonight would compromise the control the session is meant
to provide.

---

## Section J — Still unknown

- Whether the Phase-24 horizon result replicates on a sixth session.
- Whether ranks 16–40 hold value — **unmeasurable tomorrow**.
- Whether `composite_score` predicts forward return — it was present on only 11%
  of rows in the Phase-24 dataset.
- Whether the candle-freshness fix (Phase 18) actually improved p50 lag — the
  first beat execution is 08:30 tomorrow and it has never run under beat.
- What the scan funnel telemetry will show — it has produced no data yet.

---

## Tomorrow's acceptance checklist

**09:15–10:00 — did the deployed fixes work?**

| Check | Baseline | Expect |
|---|---|---|
| Candle p50 per-symbol lag | 16.0 min | materially lower |
| `saved / candles` per candle run | 2.5% | materially higher |
| Scan funnel row appears | never | one per scan, with `universe = scanned + no_price + no_candles` |
| Universe snapshot row | never | one per rebuild |
| Trade-loop `SoftTimeLimitExceeded` | 0 since 13:00 | stay 0 |
| `_mfe_src` on closed trades | n/a | `"candles"` on the majority |
| MIS trades charged | 0.294% | ~0.11% |

**After 15:30 — the experiment:**

1. Run `exit_horizon_shadow.py 2026-08-27`.
2. Rebuild the Phase-24 opportunity dataset for 08-27 and check whether the
   signalled subset again shows return growing with horizon.
3. Compare CONTROL against each hypothetical horizon, per exit family.

**The primary question is not "did we make money."** One session cannot answer
that. It is: *did the signal continue to show positive edge, and which exit
horizon converts the most of it into net realised P&L?*

---

## Known risks

1. **The cost correction changes sizing from tomorrow.** Impact tonight is ₹0,
   but every MIS close from tomorrow returns ~₹80 more per ₹50k of notional to
   the wallet, which permits slightly larger subsequent positions. Small,
   directional, and intended — but it means tomorrow is not a pure control for
   sizing.
2. **One session proves nothing.** Phase 24's result rested on five sessions and
   one validation day of 61 signals, and **08-20 contradicted it outright**.
3. **Three of tomorrow's four telemetry gaps are structural** (§I) — the funnel
   will be less complete than the brief asked for.
4. **`mfe_pct` changes meaning tomorrow.** Comparisons against historical rows
   are not like-for-like; `_mfe_src` distinguishes them.
5. **The shadow experiment looks past each trade's exit by design.** That is
   correct for the question but means its numbers are counterfactual, never
   achieved.

---

## Rollback

```bash
cd /home/cis/windows/auto-trade-pro
git checkout -- autotrade-backend/paper_trading/trade_simulator.py \
                autotrade-backend/engine/agent/backtester.py
systemctl --user restart autotrade-celery-worker autotrade-celery-trade-worker \
    autotrade-celery-exit-worker autotrade-celery-scan-worker autotrade-uvicorn
```

The shadow experiment needs no rollback — deleting the file is sufficient, and
nothing imports it. No migration in either direction.
