# Forward-resolution experiment — does the signal timestamp carry information?

**Scope:** this experiment only. No production code was changed. No per-strategy
profitability verdicts are given.

---

# The answer

> **Does the actual signal timestamp contain information that survives a
> complete forward holding window, or does the system systematically enter
> after the favourable edge has already been consumed?**

## **It does not survive. The system enters after the edge is consumed.**

Over a complete forward window, entering at the signal's chosen minute is
**worse** than entering at a random minute of the same session in the same
stock, by **−0.297%**, 95% paired CI **[−0.376, −0.219]**.

The horizon curve shows exactly where it turns:

| horizon | real − random | 95% paired CI | |
|---|---:|---|---|
| +1m | +0.008 | [−0.005, +0.021] | not significant |
| +3m | +0.014 | [−0.010, +0.039] | not significant |
| +5m | +0.020 | [−0.013, +0.054] | not significant |
| +10m | +0.011 | [−0.027, +0.052] | not significant |
| **+15m** | **−0.022** | [−0.067, +0.023] | **crosses over** |
| +30m | −0.055 | [−0.121, +0.011] | not significant |
| **+60m** | **−0.195** | **[−0.270, −0.120]** | **significant** |

A small, statistically insignificant advantage for the first ten minutes; a
crossover near fifteen; and a significant deficit by sixty. **That is the
signature of entering at a local extreme** — brief continuation, then reversion.

---

# 1. Method

**Holding window:** from the signal timestamp to the close of the **next trading
session**. Deterministic, identical for every signal, fixed before any result
was seen.

**Walk:** 1m candles, **stop wins ties** — when one bar's range contains both
levels, the stop is taken, because a 1m bar does not say which came first.

**Classification:** every signal ends as exactly one of `SL`, `TP`, `TIME_EXIT`,
or `UNRESOLVED_DATA`. The last is used only where the required candles do not
exist.

**Nulls:** all three hold trade *geometry* constant (same stop %, same target %,
same forward window) and destroy only the information the signal claims to
carry. Real and null are marked identically, so every comparison is
**marking-invariant** — which is the defect this experiment exists to remove.

Bootstrap: 4,000 resamples, **paired** on the signal (`real_i − null_i`), seed
20260825.

---

# 2. Resolution

| | n | % |
|---|---:|---:|
| Total signals in the window | 2,790 | 100% |
| `UNRESOLVED_DATA` | 1,788 | 64.1% |
| **Forward-resolved** | **1,002** | **35.9%** |

### Why 64% are unresolved — and why that is legitimate

| session | signals | next session |
|---|---:|---|
| 2026-08-20 | 530 | 08-21 ✓ |
| 2026-08-21 | 268 | 08-24 ✓ |
| 2026-08-24 | 207 | 08-25 ✓ |
| **2026-08-25** | **1,800** | **does not exist yet** |

**1,800 of the 1,788 unresolved are today's signals**, which cannot have a next
session while today is still running. Of the 1,005 signals that *could* resolve,
1,002 did — three lost to per-symbol candle gaps. **The window logic is not
dropping data it could have used.**

### ⚠️ Correction to the previous audits

`tactical_signals` contains **2,805 rows across four distinct sessions**
(20, 21, 24, 25 August). There is no 30-day history.

My earlier reports repeatedly described this as a "30-day sample". **That
framing was wrong** — the *query window* was 30 days; the *data* spans four
sessions. Every conclusion that leaned on sample breadth must be read as
resting on **three complete sessions and 1,002 resolved signals**, not thirty
days.

### Outcome mix — complete, nothing marked arbitrarily

| outcome | n | % |
|---|---:|---:|
| TP | 449 | 44.8% |
| SL | 369 | 36.8% |
| TIME_EXIT | 184 | 18.4% |

Compare with the previous replay, where **59.7% never resolved** and were marked
to an arbitrary last bar. That defect is now gone: 81.6% resolve to a real level
and the remaining 18.4% resolve to a defined end-of-window mark.

Gross across all 1,002: **median +0.065%, mean +0.052%, win rate 51.6%.**

---

# 3. The three nulls — paired, forward-resolved

| Comparison | pairs | real | null | **diff** | 95% paired CI | Verdict |
|---|---:|---:|---:|---:|---|---|
| real symbol vs random symbol | 1,002 | +0.052 | −0.061 | **+0.113** | **[+0.000, +0.229]** | **MARGINAL** — CI lower bound sits on zero |
| real direction vs random direction | 1,002 | +0.052 | +0.020 | +0.032 | [−0.071, +0.139] | **INDISTINGUISHABLE** |
| real timestamp vs random timestamp | 1,002 | +0.052 | +0.349 | **−0.297** | **[−0.376, −0.219]** | **SIGNAL SUBTRACTS INFORMATION** |

Three separate statements, in descending order of confidence:

1. **Timing is harmful, and more so than the earlier run showed.** Under
   incomplete marking the deficit measured −0.201%; with every trade resolved it
   is **−0.297%**. Removing the marking defect made this result **stronger**, not
   weaker.

2. **Direction carries no information.** Replacing each long/short call with a
   coin flip is statistically indistinguishable. This reproduces the earlier
   finding on a fully-resolved sample.

3. **Symbol selection is marginal.** The paired CI is [+0.000, +0.229] — the
   lower bound rests exactly on zero. The earlier run reported this as a clear
   win (+0.090%, CI [+0.050, +0.133]); under complete resolution it **weakens to
   borderline**. It should not be described as established.

---

# 4. Excursions over the complete window

| | value |
|---|---|
| MFE median | **+0.776%** (p75 +1.502, p90 +2.006) |
| MAE median | **−0.739%** (p25 −1.316, p10 −1.983) |
| **MFE / \|MAE\| at the median** | **1.050** |
| time-to-MFE median | 38 min |
| time-to-MAE median | 56 min |

The favourable and adverse excursions are the same size — a ratio of **1.05**,
i.e. no directional asymmetry. The favourable move arrives *first* (38 min
against 56 min) and is then handed back.

That ordering is consistent with the horizon curve in the headline: whatever
edge exists is early, small, and gone by the hour mark.

---

# 5. What this settles, and what it does not

## Settled

- **The marking defect is removed.** The previous audit's central weakness —
  59.7% unresolved, per-strategy numbers unstable at ±0.13% — does not apply
  here. Every one of the 1,002 signals resolves.
- **Timing is harmful over a complete window.** −0.297%, CI excludes zero, and
  the effect grew when the defect was removed.
- **Direction is uninformative.** Reproduced on a fully-resolved sample.
- **The favourable-move-first ordering is confirmed** by time-to-MFE < time-to-MAE.

## Not settled

- **Sample breadth.** Three sessions. Not thirty days. A regime that lasted a
  week could produce all of this.
- **Symbol selection.** Now marginal, not established. It moved materially
  between two legitimate methods, which is itself a warning.
- **Per-strategy verdicts.** Deliberately not produced, per instruction. The
  earlier per-strategy table remains withdrawn.
- **Why the timestamp is late.** This experiment measures *that* it is late, not
  *what* makes it late. **EVIDENCE NOT AVAILABLE** on the mechanism.

---

# 6. Reproducibility

| Item | Detail |
|---|---|
| Script | `scripts/research/` — `fwd_resolve.py`, `fwd_analyse.py` |
| Source | `tactical_signals` (all 2,790 with entry/SL/target), `candles` 1m |
| Window | signal timestamp → next session's close |
| Tie rule | stop wins |
| Null pool | 1,458 symbols with >3,000 1m bars and >₹50cr traded value in the window |
| Bootstrap | 4,000 resamples, paired, seed 20260825 |
| Sessions resolved | 2026-08-20, 08-21, 08-24 |

**Limitations.** Three sessions. Fills use each signal's own `entry_price`
(production-faithful — verified 23/23 against executed trades — but queue
position is unmodelled). Null 1 fills at the alternate symbol's first bar close,
which is a different fill convention from the real signal's stored entry; that
asymmetry may contribute to the symbol result's instability. No costs are
applied anywhere in this document — every figure is gross.

*Systems and statistics analysis. Not investment advice. No production code was
changed.*
