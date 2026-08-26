# PHASE 21B — the MIS_SQUAREOFF gap, and what it turned out to be

**Mode:** READ-ONLY. No production change, no order, no fix applied.
**Question:** Phase 21 reported an unexplained ₹2,541 gap on MIS_SQUAREOFF
exits — 18 positions worth **+₹326** held to 15:29 realised **−₹2,215** — and
flagged squareoff pricing, slippage or a defect as candidates.

---

## The gap is fully explained. It is arithmetic, not pricing.

| Component | ₹ |
|---|---:|
| Transaction costs on 18 trades | **2,214** |
| Exit price differing from the 15:29 close | **327** |
| **Total** | **2,541** |

**That is the reported gap, to the rupee.**

### Where it came from — my own benchmark

Phase 21's hold-to-close comparison computed **gross** P&L (price difference ×
units) and compared it against **realised** P&L, which is already net of costs.
Every exit family was measured that way, so every "net vs hold" figure in that
report was biased downward by roughly (cost per trade × n).

**Squareoff pricing is RULED OUT.** Exit prices sit within ±1.2% of the last
traded price, and the total P&L attributable to that difference across all 18
trades is **−₹327** — ordinary execution variance, not a defect. The proof is
three trades where exit price equals entry price exactly:

| Symbol | entry | exit | units | notional | realised |
|---|---:|---:|---:|---:|---:|
| SHRIRAMFIN.NS | 1136.30 | 1136.30 | 44 | 49,997 | **−147** |
| WELCORP.NS | 2364.11 | 2364.11 | 21 | 49,646 | **−146** |
| INDOMIM.NS | 889.82 | 889.82 | 56 | 49,830 | **−147** |

Zero price movement, ₹146–147 of loss: **0.294% of notional**, and that is the
round-trip cost.

---

## Corrected Workstream B

Hold-to-close is now **net of the same round-trip cost** — the counterfactual
position still has to be closed, so it pays the same friction. Cost is computed
exactly per trade as `gross(from stored prices) − realised`.

| Exit reason | n | realised | if held (net) | **NET** | costs |
|---|---:|---:|---:|---:|---:|
| STOP_LOSS | 22 | 125 | 2,917 | **−2,792** | 2,774 |
| T1_REVERSAL_EXIT | 4 | 3,299 | 4,226 | −928 | 538 |
| **MIS_SQUAREOFF** | 18 | −2,215 | −1,888 | **−327** | 2,214 |
| CONFIRMATION_LOST | 1 | −88 | −69 | −19 | 57 |
| REALLOCATED | 1 | −589 | −1,154 | +565 | 52 |
| TAKE_PROFIT | 4 | 5,037 | 3,804 | **+1,233** | 369 |
| **EXHAUSTION** | 18 | −3,747 | −6,559 | **+2,812** | 2,615 |
| **ALL** | **68** | **1,823** | **1,278** | **+544** | **8,620** |

### Three conclusions this reverses

**The exit stack is net POSITIVE (+₹544), not −₹8,076.** Phase 21's headline
that exits were leaking ₹8,076 was entirely the gross-versus-net artifact.

**EXHAUSTION is the best exit family, at +₹2,812.** I have now moved on this
three times — blamed it in 19B, called it neutral in 20/21, and it is in fact
the largest positive contributor. Each revision came from a better benchmark,
and this one is the first that compares like with like.

**MIS_SQUAREOFF is −₹327, not −₹2,541.** The item I ranked #1 for investigation
in Phase 21 is, on its own terms, close to harmless.

---

## But the investigation found a real defect

`paper_trading/trade_simulator.py:133`

```python
def estimate_trade_cost(qty: int, price: float, side: str = "BUY") -> float:
    """Realistic Indian equity delivery transaction cost (Varsity Module 7)."""
    ...
    stt = notional * 0.001          # :142
```

**The function has no `product` parameter.** Its own docstring says *delivery*,
and it charges delivery STT — 0.1% on **both** legs — to every trade regardless
of product. NSE equity **intraday** STT is 0.025% on the **sell leg only**.

Measured directly: MIS and CNC trades are charged an identical median of
**0.294%**, when they should differ materially.

| Product | n | median charged | should be |
|---|---:|---:|---|
| MIS | 44 | 0.294% | ~0.11% |
| CNC | 28 | 0.294% | 0.294% (correct) |

### Impact

| | ₹ |
|---|---:|
| Phantom cost charged to 44 MIS trades | **3,787** |
| Total realised P&L on record (all 72 trades) | **806** |
| **Ratio** | **4.7×** |

**Every intraday trade in the record has been charged roughly ₹90 per ₹50,000
of notional that a real broker would not charge.** The overcharge is nearly five
times the entire recorded P&L.

**CONFIRMED.** The arithmetic is reproducible from the stored `entry_price`,
`exit_price`, `size_units` and `pnl` columns.

### What this does and does not mean

- It is a **paper-accounting defect**, not a trading-behaviour defect. Position
  sizing, entry and exit decisions are unaffected — only the P&L attributed to
  them.
- It makes every historical intraday result **understated**, and it biases every
  cost-floor comparison in the earlier phases. Where I wrote "round-trip cost of
  0.21–0.39%" as the bar an opportunity must clear, the *simulator's* bar was
  0.294% for intraday when reality is nearer 0.11%.
- It is **not** the reason the strategy is unprofitable. Realised P&L plus the
  phantom cost is still small: ₹806 + ₹3,787 ≈ ₹4,593 across 72 trades.

**Not fixed here.** Changing it alters reported P&L on every historical row's
interpretation and needs its own deployment with its own before/after.

---

## The number that matters more than any exit

Total transaction costs across 68 trades: **₹8,620**. Realised P&L: **₹1,823**.
So gross P&L was ~₹10,443 and **costs consumed 83% of it** — and roughly ₹3,787
of that was charged in error.

Set against Phase 21's entry-quality finding — **median MFE +0.06%**, and 48 of
68 trades never reaching +0.5% — the picture is arithmetic rather than
strategic: **the median trade never moves far enough to clear its own cost
floor**, whichever floor is used.

That is a stronger and simpler statement than anything about exit families, and
it does not depend on the benchmark question that produced three revisions.

---

## Status

| Claim | Verdict |
|---|---|
| MIS_SQUAREOFF squareoff pricing is defective | **RULED OUT** (−₹327 across 18 trades) |
| The ₹2,541 gap | **EXPLAINED** — 2,214 costs + 327 pricing variance |
| Phase 21's "exits leak ₹8,076" | **WITHDRAWN** — gross-vs-net artifact; corrected figure **+₹544** |
| EXHAUSTION harmful | **RULED OUT** — best family at **+₹2,812** |
| `estimate_trade_cost` ignores product | **CONFIRMED** — `trade_simulator.py:133`, no product parameter, delivery STT on all trades |
| Phantom cost on intraday trades | **CONFIRMED** — ₹3,787 across 44 MIS trades, 4.7× realised P&L |
| Costs consume 83% of gross P&L | **CONFIRMED** (inflated by the above) |
| STOP_LOSS −₹2,792 | **PARTIALLY SUPPORTED** — 75% from 3 multi-day trades; tail protection unmeasurable at this n |

---

*Read-only. All figures reproducible from `paper_trades` and `candles`. No
production code, configuration, database row or order was touched.*
