# PHASE 25 — V2 IMPLEMENTATION

Built 2026-08-26 evening for the 2026-08-27 session.
**Mode: V2 selected locally. CONTROL preserved. Rollback = one `.env` line.**

---

## 1. Files changed

### New

| File | What |
|---|---|
| `engine/exit_policy.py` | The taxonomy and the gate. The only place the mode is read. |
| `scripts/research/v2_exit_replay.py` | Historical replay, CONTROL vs every horizon. Research-only. |
| `tests/test_exit_policy_v2.py` | 100 tests |
| `tests/test_trailing_ratchet_gate.py` | 9 tests |
| `tests/test_rank_overflow_capture.py` | 14 tests |
| `tests/test_research_scripts_cannot_trade.py` | 87 tests |
| `tests/test_telemetry_cannot_break_trading.py` | 11 tests |

### Modified

| File | Change |
|---|---|
| `utils/config.py` | `TRADING_STRATEGY_MODE` (default **CONTROL**), `V2_MIN_HOLD_MINUTES` (120) |
| `tasks/india_tasks.py` | `_pm_ok` gate on EXHAUSTION, T2 partial, TAKE_PROFIT/T1, trailing ratchet |
| `paper_trading/trade_simulator.py` | `_pm_ok` gate on T1 + trailing; SL/TP routed through the policy; exit attribution at close |
| `paper_trading/position_tracker.py` | `update_trailing_stop(..., ratchet=)` |
| `engine/tactical_scoring.py` | optional `overflow_out` capture |
| `engine/tactical_executor.py` | rank 16–40 capture, entry-quality telemetry |
| `scripts/research/exit_horizon_shadow.py` | horizons extended to 150/180, grouped by exit family |
| `.env` | `TRADING_STRATEGY_MODE=V2`, `V2_MIN_HOLD_MINUTES=120` (backup: `.env.bak.phase25`) |
| `tests/test_exit_management.py`, `tests/test_exit_shadow_isolation.py` | two assertions updated — see §5 |

---

## 2. Exact strategy changes

**One thing changed:** under V2, an exit classified `PROFIT_MANAGEMENT` is
deferred until the position has been open 120 minutes.

Nothing else. Signal rules, universe, turnover, `TACTICAL_TOP_N` (**15**), R:R,
capital limits, Master Score, prompts, AI routing, BUG-1, the candidate queue,
entry execution and position sizing are untouched. Verified in Phase 25.1 by
diffing all 134 strategy-affecting settings between the two modes: exactly one
differs.

### Deferred under V2 (6 reasons)

`TAKE_PROFIT` · `TRAIL_STOP` · `EXHAUSTION` · `T1_REVERSAL_EXIT` · `T1_HIT` · `T2_HIT`

### Never deferred, in either mode, at any age

| Layer | Reasons |
|---|---|
| 1 — hard risk | `STOP_LOSS`, `MARKET_SHOCK_FLATTEN` |
| 2 — setup invalidation | `CONFIRMATION_LOST`, `SECTOR_REVERSAL`, `POST_EVENT_REVERSAL`, `LLM_DYNAMIC_EXIT` |
| 4 — max hold | `MIS_SQUAREOFF`, `STALE_EXIT`, `POST_EVENT_TIME_EXIT` |
| operator | `MANUAL`, `KILL_SWITCH`, `REALLOCATED` |

### The one non-obvious part: the trailing ratchet

The ratchet and the hard stop **share `pos.stop_loss`**. A stop moved to
breakeven at +2% closes the position at +0% and records itself as `STOP_LOSS` —
Layer 3 wearing Layer 1's label. Gating only the exits nameable as profit
management would have left the trailing stop closing winners early and the
experiment would have measured nothing.

So V2 defers the ratchet too, via `update_trailing_stop(..., ratchet=False)`.
**The peak keeps being tracked throughout**, so when the window opens the
chandelier applies from the true high rather than restarting from wherever
price then sits. Pinned by `test_releasing_the_gate_trails_from_the_tracked_peak`.

### Layer 2 — what "setup invalidation" actually is

No new indicator was invented. The four conditions above already exist and
already close positions; Phase 25 only names them as a layer. `CONFIRMATION_LOST`
re-checks the price/volume confirmation that justified the entry;
`SECTOR_REVERSAL` fires when the sector turns against the position;
`POST_EVENT_REVERSAL` fires when an event thesis resolves adversely;
`LLM_DYNAMIC_EXIT` is the agent calling the thesis dead. All four run
unmodified at every hold duration.

---

## 3. CONTROL vs V2

| | CONTROL | V2 |
|---|---|---|
| Signals | identical | identical |
| Entries, sizing | identical | identical |
| Hard stop | active | active |
| Setup invalidation | active | active |
| Profit management | from minute 0 | **from minute 120** |
| Trailing ratchet | from minute 0 | from minute 120 (peak tracked throughout) |
| MIS squareoff | active | active |
| Exit family recorded | **yes** | yes |

The taxonomy is recorded in **both** modes — it is measurement, not strategy.

---

## 4. Configuration

```bash
TRADING_STRATEGY_MODE=V2      # or CONTROL
V2_MIN_HOLD_MINUTES=120       # 60 / 90 / 120 / 150 / 180; <= 0 disables the gate
```

Code default is **CONTROL**, deliberately: a process that cannot read `.env`
must get the old behaviour, never the experiment. Pinned by
`test_control_is_the_code_default`.

Verified live in a worker process:

```
{'mode': 'V2', 'min_hold_minutes': 120.0, 'gated_family': 'PROFIT_MANAGEMENT'}
```

---

## 5. Tests

| | |
|---|---:|
| passed | **2,046** |
| failed | 27 |
| skipped | 7 |
| errors | 5 |

Baseline was 1,791 / 27 / 7 / 5 at the start of Phase 25 and 1,818 after the
measurement commit. **+228 passed, zero new failures.** All 27 failures and 5
errors sit in files untouched by this work — `test_upstox_isin`,
`test_entry_confirmation`, `test_pre_event_gap_*`, `test_alert_router`,
`test_alert_reports`, `test_trade_simulator_confirmation_lost` — and have been
constant since the Phase-19 baseline.

### Two existing tests were changed, on purpose

Both had asserted the *absence* of what Phase 25 deliberately adds. Left alone
they would have failed for the right reason, and weakening them silently is the
failure mode this whole programme exists to avoid.

- `test_exit_management.py::test_trailing_is_NOT_gated_on_it` anchored on the
  literal call `update_trailing_stop(pos, price, _atr)`. Its **intent** — the
  SWING hold must not gate trailing — still holds and is still asserted. Two
  new tests were added beside it for the V2 gate.
- `test_exit_shadow_isolation.py::TestControlRemainsDefault` asserted
  `"EXIT_MODE" not in src`, i.e. that no mode could exist anywhere. That was
  true when CONTROL was the only behaviour. It is now replaced by three
  narrower assertions that are more useful: the research script is unreachable
  from the trading path, CONTROL is the code default, and no call site reads
  the mode except through `engine/exit_policy.py`.

### What the new tests actually pin

- Every mapped reason × two ages × CONTROL → **never deferred**
- `STOP_LOSS` and `MARKET_SHOCK_FLATTEN` at 0/1/30/60/119 minutes under V2 → **always fire**
- All four invalidation reasons inside the window → **always fire**
- Boundary is inclusive at 120, deferred at 119
- Unknown reason, missing `opened_at`, zero horizon, an exception inside the
  gate, a broken settings object → **all allow the exit**
- `test_only_profit_management_is_ever_gated` walks the whole taxonomy and
  asserts the blast radius, so a future mapping mistake fails a test rather
  than trapping a position

---

## 6. Historical replay

43 TACTICAL/MIS closed trades; 34 replayable (9 lack 1m candles in the window).

```
  variant                n       net      avg   median   win%     PF    maxDD  avg hold
  ACTUAL (ground truth)  34    -3,364      -99     -149     24   0.50   -3,385        —
  SIM-CONTROL            34      -840      -25      -77     38   0.87   -2,420     124m
  SIM-V2-60              34      -558      -16      -77     38   0.91   -2,420     125m
  SIM-V2-90              34      -535      -16      -77     38   0.92   -2,420     125m
  SIM-V2-120             34       291        9      -77     38   1.05   -2,420     127m
  SIM-V2-150             34      -323       -9      -77     38   0.95   -2,420     128m
  SIM-V2-180             34      -357      -11      -77     38   0.94   -2,420     130m
  SIM-HOLD_TO_CLOSE      34    -1,392      -41      -77     38   0.78   -2,553     139m
```

### The replay does NOT support 120 minutes. Read this before using the table.

**V2-120 looks best and that is noise.** The diagnostic:

```
  variant               changed   of n   net delta
  SIM-V2-60                   1     34         282
  SIM-V2-90                   2     34         305
  SIM-V2-120                  3     34       1,131
  SIM-V2-150                  3     34         517
  SIM-V2-180                  3     34         482
  SIM-HOLD_TO_CLOSE           3     34        -553
```

**Three trades out of 34 differ.** V2-120, V2-150 and V2-180 change the *same
three trades* and rank differently only in how much. A ranking that rests on
three observations has not shown one horizon to be better than its neighbours;
it has shown them to be indistinguishable. `n=34` cannot separate them.

**The model is also a poor stand-in for CONTROL.** SIM-CONTROL is −₹840 against
ACTUAL −₹3,364, a ₹2,525 gap. The gap is `EXHAUSTION` (−₹3,999 across 17 real
exits) and `T1_REVERSAL_EXIT`, neither of which is replayable: exhaustion reads
the live 5m frame, and the stored 5m series is rebuilt from 1m by the
resampler, so a replay cannot recover the bar the live check consumed — the
same limitation already recorded at the `EXHAUSTION_AUDIT` instrumentation.
T1 reanalysis needs an LLM call.

### The counterfactual that does not depend on the model

Taking the 20 real trades that have forward candles and asking what holding
each to squareoff would have produced, net of corrected costs:

| exit reason | n | ACTUAL | held to squareoff | delta |
|---|---:|---:|---:|---:|
| EXHAUSTION | 14 | −3,618 | −4,027 | **−409** |
| STOP_LOSS | 4 | −33 | 279 | +312 |
| TAKE_PROFIT | 1 | 1,404 | 732 | −672 |
| T1_REVERSAL_EXIT | 1 | 32 | 865 | +833 |
| **TOTAL** | **20** | **−2,215** | **−2,150** | **+65** |

**Holding every early exit to the close would have gained ₹65 on ₹2,215 of
losses — nothing.** And `EXHAUSTION`, the family V2 defers most, would have
been **₹409 worse**.

At the trade level, on this sample, there is no evidence that exits are leaking
edge.

### So why is V2-120 the chosen horizon?

**Not because of the replay.** 120 comes from Phase 24, which measured at the
*opportunity* level: 5,488 t0 observations across five sessions, signalled
subset net −0.052% at 60m / +0.054% at 120m / +0.342% to close, with the
direction replicating on a held-out session (−0.032% / +0.109% / +0.342%). That
is the larger sample and the pre-registered reason. 120 is the first horizon at
which the signalled subset turns net-positive there.

The replay is reported as a **negative result**: it neither confirms nor
selects a horizon, and its trade-level counterfactual points the other way. The
horizon was fixed before the replay was run and is not being changed after
seeing it.

---

## 7. Cost reconciliation

Product-aware costs were deployed 2026-08-26 and are unchanged tonight.

| | MIS | CNC |
|---|---|---|
| STT | 0.025%, sell leg only | 0.1%, both legs |
| Stamp | 0.003%, buy leg | 0.015%, buy leg |
| Round trip | ~0.11% | ~0.294% |

Both implementations are now pinned against each other
(`test_the_two_implementations_cannot_drift`) — a replay that disagrees with
the live simulator would prove nothing. Unknown or NULL product falls back to
delivery: fail expensive, never cheap. **No historical row was rewritten.**

Four new tests pin the wallet chain: `pnl = gross − cost` reaches
`return_margin`, and margin returned is the original notional, so cost is never
double-counted out of the wallet.

---

## 8. MFE/MAE correction

Candle-derived at close, tracker as fallback. Measured on 38 intraday trades:
median stored 0.010% against **0.353%** from candles; 17 stored exactly 0.00, of
which 11 had real movement above 0.1%; PAYTM stored 0.00% against 5.31%.

**A defect found and fixed tonight:** `_mfe_src` was computed and then thrown
away — nothing persisted it. It and `_mae_src` are now written to
`indicator_snapshot.exit_meta` and to the `TRADE_CLOSED` log, so tomorrow's
report can say which trades used which source.

---

## 9. Telemetry

| Signal | Where | Status |
|---|---|---|
| Scan funnel (universe/scanned/no_price/no_candles/raw/kept/persisted/skipped) | `TACTICAL_SCAN_FUNNEL` | live, Phase 21 |
| Universe snapshot | per rebuild | live, Phase 21 |
| Exit family + mode + hold + MFE source | `indicator_snapshot.exit_meta`, `TRADE_CLOSED` | **new** |
| Rank 16–40 | `TACTICAL_RANK_OVERFLOW` | **new** |
| Entry quality | `tactical_signals.meta_json.entry_quality` | **new** |

Risk rejected / capital rejected / executed are already in `tactical_signals`
(`reason`, `routing_outcome`, `executed`).

**Scan logic was not modified to achieve any of this.** Eleven tests enforce
that a telemetry failure loses a measurement and nothing else: own sessions,
wrapped, after the trading commit, no re-raise.

---

## 10. Rank 16–40 capture

> **CORRECTED IN PHASE 25.1.** This section originally said `TACTICAL_TOP_N` is
> 5 and `TACTICAL_MAX_SIGNALS_PER_CYCLE` is 15. **Both were wrong**, and the
> capture was aimed at the wrong band as a result. See §14.

The effective values are **`TACTICAL_TOP_N = 15`** and
**`TACTICAL_MAX_SIGNALS_PER_CYCLE = 40`** (`utils/config.py:433-434`), neither
overridden in `.env`. The pipeline cuts twice:

```
score_and_filter   keeps the best 40 above the composite floor
rank_signals       selects 15 of those; only those 15 are persisted
```

So ranks 16–40 are dropped at `rank_signals`, not inside `score_and_filter`.
The capture derives its band as `scored` minus `ranked`, by object identity —
correct whether or not the ML ranker is loaded (it currently is not, so
`rank_signals` preserves composite order and the band is exactly ranks 16–40).

Each entry records symbol, rank, score, signal type, reference price, stop,
target, `entry_eligible`, and why it was not persisted.

Twenty-two tests enforce that it **cannot execute**: no `TradeIntent`, no
`execute_trade_intent`, no sizing requested, no risk booked, no `TacticalSignal`
row, own session, bounded at 25, runs after the scan has committed.

**`TACTICAL_TOP_N` is unchanged at 15**, in both modes, pinned on the VALUE
rather than on a source literal. Capturing is not trading.

---

## 11. Known risks

1. **The replay does not support the chosen horizon, and its trade-level
   counterfactual points the other way** (§6). V2 rests on Phase 24's
   opportunity-level evidence alone. This is the single largest caveat.
2. **Deferring the ratchet is a real increase in open risk between minute 0 and
   120.** A position that runs to +5% and reverses is no longer protected at
   breakeven; it is protected at the original hard stop. That is the
   experiment, and Layer 1 bounds it, but it is not free.
3. **`EXHAUSTION` closed 17 of 43 historical trades and is the family V2 defers
   most.** On the sample above, deferring it would have been *worse*. If
   tomorrow's EXHAUSTION-family trades come out worse under V2, that is the
   expected direction of this risk, not a surprise.
4. **One session proves nothing.** Phase 24 rested on five sessions plus a
   validation day, and **2026-08-20 contradicted it outright**.
5. **`n=34` on the replay and `n=20` on the counterfactual.** Both are too small
   to separate horizons. No confidence interval here excludes zero.
6. **`mfe_pct` changed meaning** on 2026-08-26. Comparisons against older rows
   are not like-for-like; `_mfe_src` distinguishes them.
7. **Two exit paths were not individually re-verified under V2 in a live
   session**: `shock_guard` and `portfolio_reallocation`. Both are classified
   and neither is gated, so V2 cannot suppress them — but neither fired during
   this work, so that is an argument from code, not from observation.
8. **Per-stage latencies still do not exist.** Tomorrow's funnel gives stage
   *counts*, not stage *timings*. `opportunity_ts` has no live definition and is
   still reconstructed post-hoc.

---

## 12. Rollback

```bash
# One line. Nothing else.
sed -i 's/^TRADING_STRATEGY_MODE=V2/TRADING_STRATEGY_MODE=CONTROL/' \
    /home/cis/windows/auto-trade-pro/autotrade-backend/.env
systemctl --user restart autotrade-celery-worker autotrade-celery-trade-worker \
    autotrade-celery-exit-worker autotrade-celery-scan-worker autotrade-uvicorn
```

Verified: under `CONTROL`, **zero** of the 18 mapped reasons are deferred at any
age — identical to pre-Phase-25. The full `.env` is backed up at `.env.bak.phase25`.

To abandon the code entirely: `git revert` the commit. No migration in either
direction; the taxonomy lives in an existing JSON column.

To change horizon only: edit `V2_MIN_HOLD_MINUTES` and restart. **Not tomorrow** —
tuning it after seeing the outcome is the failure this programme is set up to
avoid.

---

## 13. Tomorrow's checklist

**09:15–10:00 — did it start correctly?**

| Check | Expect |
|---|---|
| `exit_policy` mode in worker logs | `V2`, min_hold 120 |
| Any exit before 120m | family is `HARD_STOP`, `SETUP_INVALIDATION` or `MARKET_SQUAREOFF` — **never** `PROFIT_MANAGEMENT` |
| `[exit_policy] ... deferred` lines | present — proves the gate is being reached, not merely configured |
| `TACTICAL_SCAN_FUNNEL` row | one per scan, `universe = scanned + no_price + no_candles` |
| `TACTICAL_RANK_OVERFLOW` row | appears; **zero** of those symbols in `paper_trades` |
| Candle p50 lag | below the 16.0 min baseline |
| Trade-loop `SoftTimeLimitExceeded` | stays 0 |
| `_mfe_src` | `"candles"` on the majority |
| MIS cost charged | ~0.11%, not 0.294% |

**After 15:30:**

1. `exit_horizon_shadow.py 2026-08-27` — actual vs +30/60/90/120/150/180/close, by family.
2. `v2_exit_replay.py` — with the new day included.
3. Group realised P&L by `exit_meta.exit_family`. **The question is whether
   deferring profit management changed the family mix**, not whether the day
   was profitable.
4. Take the `TACTICAL_RANK_OVERFLOW` rows and derive their forward returns.
5. Rebuild the Phase-24 opportunity dataset for 08-27 and check whether the
   signalled subset again shows return growing with horizon — the sixth
   independent test of the claim V2 is built on.

**The primary question is not "did we make money."** One session cannot answer
that, and the sample sizes above cannot separate horizons. It is: *did the
gate fire where it was supposed to, did anything fire that should not have,
and did the family mix move the way the hypothesis predicts?*

---

## 14. Phase 25.1 — pre-market QA (2026-08-26, later that evening)

### The TOP_N contradiction was real, and the error was mine

| | value |
|---|---|
| A. configured (`utils/config.py:434`) | **15** |
| B. code default (same line — there is no separate default) | **15** |
| C. `.env` | **absent** — not overridden |
| D. runtime in the scan worker | **15** |
| E. actual rank cut applied | **15** (`rank_signals`), after a 40-wide scoring cut |
| F. changed by Phase 25? | **No.** Last touched in `5451353`, months ago. |

**The configuration was always correct. The Phase 25 report was wrong.**

`engine/tactical_executor.py` reads the settings as
`_cfg("TACTICAL_TOP_N", 5)`, where `_cfg` is `getattr(settings, name, default)`.
Because `Settings` defines the field, **the `5` is dead** — `getattr` never
reaches it. I read those fallback literals as the live values and wrote "TOP_N
is unchanged at 5" and "MAX_SIGNALS_PER_CYCLE is 15" into the report, a code
comment, and a test.

### The consequence was a genuine implementation bug

Believing the cut was at 15, I hooked the capture into `score_and_filter` and
took `kept[top_n:]`. With the real `top_n = 40`, that captured **ranks 41+** —
not the 16–40 band the brief asked for. The band that is actually discarded
without trace is dropped one stage later, at `rank_signals`.

### Fixed

- `engine/tactical_scoring.py` **reverted to its pre-Phase-25 state.** The
  `overflow_out` hook was solving the wrong problem and is gone; the file is
  byte-identical to the baseline.
- `engine/tactical_executor.py` derives the band as `scored` minus `ranked` by
  object identity, and passes `len(ranked)` as the rank offset so the first
  recorded rank is 16.
- Dead `_cfg` fallback literals aligned to the real fields (`5`→`15`,
  `15`→`40`). **Zero behaviour change** — `Settings` defines both, so the
  fallback is unreachable — but the stale numbers were the landmine.
- `not_persisted_reason` named the wrong cut; corrected.
- Tests now pin the **value** (`settings.TACTICAL_TOP_N == 15`) in both modes,
  not a source literal. Asserting the literal is exactly what hid the error.

### Verification results

| Check | Result |
|---|---|
| Strategy config diff, CONTROL vs V2 | **134 settings compared, 1 differs**: `TRADING_STRATEGY_MODE` |
| Exit blast radius, all 18 reasons at 1/119/120 min | 12 never deferred; **exactly 6** deferrable, released at 120 |
| Entry path vs baseline `50d6834` | **byte-identical** — rules, scoring, ranker, risk, data fetcher, risk_manager, decision_router |
| Any entry-path module referencing the mode or gate | **none** |
| BUG-1 | shadowing import at `:706` still present; hub entry path still dead |
| MIS / CNC round trip | **0.107% / 0.294%**; simulator == backtester; unknown product → delivery |
| Historical `paper_trades` | **72 closed, 0 modified**, sum(pnl) 805.77 — unchanged |
| Wallet | balance 477,054.99, equity 501,863.67 — unchanged from baseline |
| `_mfe_src` / `_mae_src` / `exit_family` persisted | yes |
| Research isolation | 100 tests pass |
| Full suite | **2,054 passed / 27 failed / 7 skipped / 5 errors** |
| New failures / new errors | **0 / 0** — same nine files as the Phase-19 baseline |
| Runtime smoke | mode=V2, min_hold=120, TOP_N=15; 119m deferred, 120m allowed, stop/invalidation/squareoff allowed |

### What this means for tomorrow

The overflow rows will now carry ranks **16–40** rather than 41+. That is the
band the question was about, so tomorrow can actually answer whether the cut at
15 discards signals worth taking.

Nothing about the V2 experiment itself changed: same 120-minute hypothesis,
same six deferred reasons, same signals, same TOP_N of 15.

### Still unresolved

- The replay's negative result (§6) stands and is unchanged by any of this.
- `shock_guard` and `portfolio_reallocation` are classified and provably
  ungated, but still have not been observed firing under V2 in a live session.
- Per-stage latencies and a live `opportunity_ts` still do not exist.
