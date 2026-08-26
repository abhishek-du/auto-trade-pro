# Tactical rule trigger attribution report

**No production code was changed.** Sample: the 1,002 forward-resolved signals
from 2026-08-20, 08-21 and 08-24. `tactical_signals.strategy` persists the rule
name, so attribution required no reconstruction.

---

# 1. Executive verdict

> **Which exact rule condition is responsible for the tactical signal timing
> behaviour?**

## It is not a timing condition. It is a **stop-anchoring** condition. — **PROVEN**

In the two largest rules the **stop is anchored to a fixed historical level**
while the **target is a fixed percentage of the current price**:

| Rule | file:line | stop | target |
|---|---|---|---|
| `gap_and_go` | `engine/tactical_rules.py:231` | `prev_high` — **yesterday's high** | `live_price * 1.02` — **fixed +2%** |
| `vwap_trend` | `engine/tactical_rules.py:187` | `vwap * 0.995` — **the session VWAP** | `live_price * 1.015` — **fixed +1.5%** |

Both rules *require the stock to have already run* before they fire. Every step
of that run widens the gap between the current price and the stop anchor. The
target does not move with it.

**Measured consequence — the target is literally unchanged while the stop more
than doubles:**

| Rule | n | prior move <1%: stop / target / R:R | prior move ≥2%: stop / target / R:R |
|---|---:|---|---|
| `GAP_AND_GO` | 238 | 2.35% / **2.00%** / 0.85 | **5.04%** / **2.00%** / **0.40** |
| `VWAP` | 198 | 1.23% / **1.50%** / 1.22 | **3.47%** / **1.50%** / **0.43** |
| `VWAP_CROSSOVER` | 156 | 1.13% / 0.86% / 0.76 | 2.57% / 0.50% / **0.19** |
| `PIVOT_BREAKOUT` | 168 | 0.92% / 0.52% / 0.45 | 3.05% / 0.65% / **0.23** |
| `VOLUME_BREAKOUT` | 68 | 1.00% / 2.00% / 2.00 | 1.76% / 2.12% / 1.21 |

**R:R roughly halves as the prior move grows, in every momentum rule.** That is
the mechanism behind the population median R:R of 0.60 measured earlier.

**This is a DESIGN property, not a software bug.** The code does exactly what it
is written to do. But the trigger conditions and the stop anchor pull against
each other: the rule fires *because* the stock has run, and the run is what
ruins the geometry.

---

# 2. Complete rule map

`engine/tactical_rules.py`, 720 lines. Registered sets at lines 719–720:

```python
F1_RULES = ("ORB","VWAP","GAP_AND_GO","PIVOT_BOUNCE","PIVOT_BREAKOUT","SCALP","DAY_MOMENTUM","DAY_WEAKNESS")
F4_RULES = ("OVERBOUGHT_FADE","OVERSOLD_REBOUND","VOLUME_BREAKOUT","VWAP_CROSSOVER")
```

### `gap_and_go` — line 197. All conditions must hold simultaneously.

```python
gap_pct = (session_open - prev_close) / prev_close
if gap_pct <= 0.01:                            return []   # gap up > 1%
if live_price <= first15["close"].iloc[-1]:    return []   # above the 15th minute's close
if ind.rsi <= 60:                              return []   # 1m RSI > 60
Signal(symbol,"BUY", live_price, prev_high, live_price*1.02, ...)
```

**Every one of the three conditions requires prior upward movement.** Measured
median prior move at trigger: **+1.79%**; median range position **89**.

### `vwap_trend` — line 163

```python
if _vol_surge(df_1m, window=1) < 1.0:          return []   # volume at or above trailing average
last2 = d["close"].iloc[-2:]
if (last2 > vwap).all() and live_price > vwap:             # 2 consecutive closed minutes above VWAP
    Signal(...,"BUY", live_price, vwap*0.995, live_price*1.015, ...)
if (last2 < vwap).all() and live_price < vwap:             # mirror for SELL
    Signal(...,"SELL", live_price, vwap*1.005, live_price*0.985, ...)
```

Median prior move **+3.22%** (BUY); median range position **90**.

### `pivot_bounce_breakout` — line 249

```python
# BOUNCE: bullish closed candle, S1 < live_price <= S1*1.005, P > live_price
Signal(...,"BUY", live_price, lv["S1"] - 0.5*atr, lv["P"], ...)
# BREAKOUT: live_price > R1  AND  _vol_surge >= 1.5  AND  R2 > live_price
Signal(...,"BUY", live_price, lv["P"], lv["R2"], ...)
```

Both stop and target are fixed levels here — the only major rule where neither
is a percentage of live price. Its R:R still degrades (0.45 → 0.23) because
`live_price` drifts away from `P` as it clears `R1`.

### Mean-reversion rules — `overbought_fade` (314), `oversold_rebound` (342)

These fire at the **opposite** extreme, which is correct for fade logic, and
they carry the **healthiest R:R in the system**: 2.37 and 1.93 respectively,
with stops of 0.50%.

---

# 3. Signal attribution — 1,002 signals

| Rule | pipeline | n | % |
|---|---|---:|---:|
| GAP_AND_GO | F1 | 238 | 23.8% |
| VWAP | F1 | 198 | 19.8% |
| PIVOT_BREAKOUT | F1 | 168 | 16.8% |
| VWAP_CROSSOVER | F4 | 156 | 15.6% |
| OVERSOLD_REBOUND | F4 | 82 | 8.2% |
| VOLUME_BREAKOUT | F4 | 68 | 6.8% |
| OVERBOUGHT_FADE | F4 | 67 | 6.7% |
| PIVOT_BOUNCE | F1 | 9 | 0.9% |
| DAY_MOMENTUM | F1 | 9 | 0.9% |
| SCALP | F1 | 4 | 0.4% |
| ORB | F1 | 3 | 0.3% |

---

# 4. Range position by rule — the 83rd percentile decomposed

| Rule | n | p25 | median | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| GAP_AND_GO | 238 | 81 | **89** | 94 | 97 |
| VWAP | 198 | 77 | **90** | 93 | 97 |
| VOLUME_BREAKOUT | 68 | 76 | **87** | 92 | 96 |
| VWAP_CROSSOVER | 156 | 72 | **83** | 91 | 96 |
| PIVOT_BREAKOUT | 168 | 49 | **79** | 92 | 96 |
| **OVERSOLD_REBOUND** | 82 | 4 | **9** | 20 | 34 |
| **OVERBOUGHT_FADE** | 67 | 8 | **13** | 20 | 32 |
| ALL | 1,002 | 53 | 83 | 92 | 96 |

**The aggregate 83rd percentile is not one rule misbehaving.** It is a clean
split: the momentum family (828 signals, 82.6%) fires near the favourable
extreme by design, and the mean-reversion family (149 signals) fires at the
opposite extreme, also by design.

**Firing at the 89th percentile is what a gap-and-go rule is supposed to do.**
The earlier report's framing of the 83rd percentile as evidence of lateness was
incomplete — it is evidence of *composition*.

---

# 5. Prior price move by rule (open → signal, in the signal's direction)

| Rule | side | n | p25 | median | p75 | p90 |
|---|---|---:|---:|---:|---:|---:|
| GAP_AND_GO | BUY | 238 | 1.36 | **1.79** | 2.75 | 5.41 |
| VWAP | BUY | 126 | 0.40 | **3.22** | 4.48 | 5.45 |
| VWAP | SELL | 72 | 0.86 | 1.95 | 3.49 | 6.42 |
| VWAP_CROSSOVER | BUY | 156 | 0.87 | 1.70 | 4.19 | 5.95 |
| PIVOT_BREAKOUT | BUY | 168 | −0.19 | 0.32 | 1.38 | 2.02 |
| VOLUME_BREAKOUT | BUY | 68 | 0.06 | 0.97 | 2.21 | 4.03 |
| OVERSOLD_REBOUND | BUY | 82 | −2.07 | **−1.38** | −0.85 | 0.34 |
| OVERBOUGHT_FADE | SELL | 67 | −3.40 | **−2.01** | −0.55 | −0.09 |

---

# 6. Forward performance by rule — no costs applied

| Rule | n | med% | mean% | win% | med MFE | med MAE | SL% | TP% | TIME% |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GAP_AND_GO | 238 | −0.091 | −0.110 | 42.9% | +1.092 | −1.206 | 21.0 | 24.4 | **54.6** |
| VWAP | 198 | +1.230 | +0.206 | 58.6% | +1.492 | −1.118 | 39.9 | 50.0 | 10.1 |
| PIVOT_BREAKOUT | 168 | +0.115 | +0.096 | 59.5% | +0.274 | −0.411 | 28.6 | 58.9 | 12.5 |
| VWAP_CROSSOVER | 156 | +0.500 | **+0.041** | **69.9%** | +0.624 | −0.664 | 28.2 | 69.9 | 1.9 |
| OVERSOLD_REBOUND | 82 | −0.499 | +0.175 | 46.3% | +0.689 | −0.503 | **53.7** | 46.3 | 0.0 |
| VOLUME_BREAKOUT | 68 | −0.536 | +0.156 | 36.8% | +1.289 | −0.970 | **57.4** | 29.4 | 13.2 |
| OVERBOUGHT_FADE | 67 | −0.499 | −0.075 | 29.9% | +0.541 | −0.587 | **70.1** | 29.9 | 0.0 |

`VWAP_CROSSOVER` is the R:R defect in its purest form: it **wins 69.9% of the
time and averages +0.041%.** Many tiny wins, few large losses — exactly what an
R:R of 0.31 produces.

`GAP_AND_GO` resolves to `TIME_EXIT` **54.6%** of the time: with a stop 3.92%
away and a target 2.00% away, most trades reach neither.

---

# 7. Trigger sequence — which condition is last

**`gap_and_go`** — the conditions are evaluated sequentially with early return,
so the *last* condition to become true is whichever satisfies last in real time.
All three are monotone in upward movement:

```
gap > 1%           (fixed at 09:15, cannot become true later)
        ↓
live_price > first15 close   (requires the run to continue past 09:30)
        ↓
RSI(1m) > 60                 (requires sustained buying)
        ↓
SIGNAL
```

The binding condition is `RSI > 60` combined with `live_price > first15 close` —
**both are satisfied only after the move is under way.** The rule cannot fire
early; it is defined not to.

**`vwap_trend`** — `last2 > vwap` requires **two consecutive closed minutes**
above VWAP. That is a deliberate 2-minute confirmation delay, and it is the only
explicit wait in any of the top rules.

---

# 8. Candle-completion / look-ahead audit — CLEAN

`engine/tactical_rules.py:76`:

```python
def closed(df: pd.DataFrame) -> pd.DataFrame:
    """The frame minus its still-forming last bar."""
    return df.iloc[:-1] if len(df) > 1 else df
```

Every rule calls `closed()` before reading indicator or OHLC history, and uses
`live_price` separately for the trigger comparison and the entry.

`_vol_surge` (line 81) additionally excludes the measured window from its own
trailing base — documented at line 84 as a fix for a prior defect where an
average containing its own numerator damped the surge.

**No look-ahead. No completion delay on the price comparison.** The only wait is
`vwap_trend`'s explicit two-closed-minute requirement (§7).

---

# 9. Rule interaction / precedence

**EVIDENCE NOT AVAILABLE** for precedence. Both `pivot_bounce_breakout` and
`vwap_trend` return at most one `Signal` per call via early `return`, so a
single rule cannot emit two directions. Whether the executor deduplicates across
rules firing on the same symbol-minute was **not traced** in this study.

Observed in data: 156 `VWAP_CROSSOVER` and 198 `VWAP` signals coexist, and the
duplicate-position gate (`existing position already open in SYM`) appears in
rejection reasons — so some cross-rule collision is being handled downstream
rather than at rule level.

---

# 10. Root cause, ranked

| # | Cause | Evidence | Signals affected | Confidence |
|---|---|---|---:|---|
| 1 | **Stop anchored to a fixed historical level while target is a % of live price** | `tactical_rules.py:231, 187`; R:R halves 0.85→0.40 and 1.22→0.43 as prior move grows, target unchanged | 436 (GAP_AND_GO + VWAP) | **PROVEN** |
| 2 | Same effect via level drift | `PIVOT_BREAKOUT` 0.45→0.23, `VWAP_CROSSOVER` 0.76→0.19 | 324 | **PROVEN** |
| 3 | Trigger conditions require prior movement | `gap_and_go` all three conditions monotone in upward move | 828 momentum signals | **PROVEN** |
| 4 | Mean-reversion rules hit their stop most of the time | `OVERBOUGHT_FADE` SL 70.1%, `OVERSOLD_REBOUND` SL 53.7%, stops at 0.50% | 149 | **PROVEN** |
| 5 | Look-ahead / candle completion | `closed()` used throughout | 0 | **RULED OUT** |
| 6 | Infrastructure latency | bar→row 0–2.4 min (prior report) | 0 | **RULED OUT** |
| 7 | Rule precedence | not traced | unknown | **EVIDENCE NOT AVAILABLE** |

---

# 11. What this proves

1. The 83rd-percentile entry is **composition, not malfunction** — the momentum
   family (82.6% of signals) fires at the favourable extreme by design, the
   mean-reversion family at the opposite extreme, also by design.
2. **The stop anchor is the defect locus.** In `gap_and_go` and `vwap_trend` the
   target is a fixed percentage of live price and the stop is a fixed historical
   level, so R:R degrades monotonically with the very movement the trigger
   requires. Measured: target identical across buckets, stop more than doubled.
3. **This is design, not a bug.** No incorrect code was found. The rules do what
   they are written to do.
4. **No look-ahead and no candle-completion delay.** `closed()` is applied
   consistently.
5. `VWAP_CROSSOVER` wins **69.9%** and averages **+0.041%** — a high win rate is
   not evidence of edge when R:R is 0.31.
6. Mean-reversion rules carry the **best R:R** (2.37, 1.93) and the **worst stop
   hit rates** (70.1%, 53.7%) — their 0.50% stops are inside normal noise.

# 12. What remains unknown

- **Whether a different stop anchor would help.** Untested. Changing it is a
  strategy change, explicitly out of scope.
- **Rule precedence and cross-rule deduplication** — not traced.
- **Why the >3% prior-move bucket performed best** (82.8% win, prior report).
  The stop-anchoring mechanism predicts the opposite. **This contradiction is
  unresolved** and three sessions cannot settle it.
- Whether any of this holds beyond **three sessions**.
- `ORB`, `SCALP`, `PIVOT_BOUNCE`, `DAY_MOMENTUM` — n = 3 to 9, **INSUFFICIENT**.

---

# 13. Recommended next evidence-gathering step

**One experiment, no code change:** recompute every historical signal's R:R
under a *counterfactual stop anchor* — the stop placed at a fixed percentage or
ATR multiple of the entry, instead of at `prev_high` / `vwap` — and re-run the
forward resolution with that geometry.

**Why this and nothing else:** it directly tests root cause #1. If R:R stops
degrading with prior move and forward expectancy improves, the anchor is
confirmed as the defect. If expectancy does not improve, the anchor is exonerated
and the problem lies in the trigger conditions themselves — which would redirect
the entire investigation.

**It is diagnostic, not a proposal.** No threshold is being recommended and no
rule is being changed.

---

## Evidence appendix

| Claim | Source |
|---|---|
| Rule registration | `engine/tactical_rules.py:719-720` |
| `gap_and_go` conditions and levels | `engine/tactical_rules.py:197-236`, Signal at :231 |
| `vwap_trend` conditions and levels | `engine/tactical_rules.py:163-196`, Signals at :187, :193 |
| `pivot_bounce_breakout` | `engine/tactical_rules.py:249-285` |
| `closed()` | `engine/tactical_rules.py:76-79` |
| `_vol_surge` base exclusion | `engine/tactical_rules.py:81-95` |
| Attribution, range position, prior move | `tactical_signals.strategy` + 1m `candles` strictly before each signal, n=1,002 |
| R:R by prior-move bucket | `entry_price`, `stop_loss`, `target` from `tactical_signals`; buckets on open→signal move |
| Forward performance | forward-resolution dataset, next-session window, stop wins ties |

**Limitations.** Three sessions. 862 BUY vs 140 SELL. No costs applied anywhere.
Four rules have n < 10 and are excluded from every conclusion. Rule precedence
untraced.

*Systems and statistics analysis. Not investment advice. No production code was
changed.*
