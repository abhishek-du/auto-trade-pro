# PHASE 19B — 2026-08-26 INTRADAY P&L ATTRIBUTION

**Mode:** READ-ONLY. No production change, no order.
**Question:** why did roughly +₹6,000 at ~11:00 IST become ~+₹1,863 by close?

---

## The answer, first

**It was not a market reversal. It was our own exits.**

The counterfactual is the whole finding: **the ten positions open at the 12:00
peak, simply held to 15:29 with no exit logic at all, were worth +₹2,971. We
realised +₹1,444. Our exits cost ₹1,527.**

The market gave no reason for the giveback. Between 12:00 and 15:29:

| Index | morning (09:15→12:00) | afternoon (12:00→15:29) |
|---|---:|---:|
| NIFTY 50 | −0.24% | **−0.29%** |
| NIFTY BANK | +0.06% | −0.15% |
| NIFTY IT | −0.93% | −0.32% |
| NIFTY AUTO | −0.11% | −0.53% |
| NIFTY METAL | +0.43% | **+1.23%** |
| NIFTY SMLCAP 100 | +0.31% | **+0.05%** |

*(live Kite 1m, 2026-08-26)*

The afternoon was flat-to-mixed, smallcaps were slightly **up**, metals were
strongly up. There was no reversal to be caught by. **Market reversal as the
explanation: RULED OUT.**

---

## 1. Reconstructed intraday P&L

**Method note, stated plainly:** no intraday equity series exists.
`performance_snapshots` is one row per day (and today's row was written
2026-08-25 18:30, i.e. yesterday's close); `agent_capital_snapshots` stopped on
2026-08-18; `virtual_wallet` holds current state only. The table below is
**reconstructed** — realised P&L from `paper_trades.closed_at`, plus
mark-to-market of then-open positions priced from our own 1m candles. It is not
a recorded series. **CONFIRMED as a reconstruction, not as a captured measurement.**

| Time (IST) | realised | unrealised | **TOTAL** | open | win/loss |
|---|---:|---:|---:|---:|---|
| 09:30 | 3,335 | 931 | 4,266 | 5 | 3/2 |
| 09:45 | 2,579 | 62 | 2,642 | 9 | 5/4 |
| 10:00 | 2,579 | 812 | 3,391 | 10 | 6/4 |
| 10:30 | 2,993 | 988 | 3,982 | 10 | 5/5 |
| **11:00** | 2,993 | 2,755 | **5,748** | 10 | 7/3 |
| 11:30 | 2,993 | 3,384 | 6,377 | 10 | 7/3 |
| **12:00** | 2,993 | 3,737 | **6,730 ← peak** | 10 | 7/3 |
| 12:30 | 4,397 | 1,574 | 5,971 | 9 | 6/3 |
| 13:00 | 1,998 | 3,022 | 5,021 | 8 | 6/2 |
| 13:30 | 1,998 | 3,282 | 5,280 | 8 | 7/1 |
| 14:00 | 3,005 | 1,819 | 4,824 | 7 | 6/1 |
| 14:30 | 3,493 | 437 | 3,930 | 10 | 5/5 |
| 15:00 | 3,031 | 1,177 | 4,208 | 7 | 5/2 |
| 15:29 | 3,519 | 92 | 3,611 | 8 | 3/5 |

**The operator's ~₹6,000 at 11:00 is corroborated** — the reconstruction gives
₹5,748 at 11:00 and ₹6,377 at 11:30. Peak was **₹6,730 at 12:00**.

Final realised for the day: **+₹2,793** across 34 closed trades, plus one
position still open at +₹1,058. The operator's ~₹1,863 figure sits between
those; the exact closing display value is **INSUFFICIENT EVIDENCE** because no
snapshot was taken.

---

## 2–3. Where the giveback went

The ten positions open at the 12:00 peak, valued then versus what they actually
realised:

| Symbol | unreal @12:00 | realised | delta | exit |
|---|---:|---:|---:|---|
| RATNAVEER.NS | 2,221 | 1,007 | **−1,214** | STOP_LOSS |
| FINCABLES.NS | 297 | −123 | −420 | EXHAUSTION |
| DREDGECORP.NS | 507 | 97 | −410 | EXHAUSTION |
| KRN.NS | 429 | 203 | −225 | MIS_SQUAREOFF |
| UNIONBANK.NS | −877 | −1,067 | −190 | EXHAUSTION |
| IFCI.NS | 1,520 | 1,404 | −117 | TAKE_PROFIT |
| ABFRL.NS | −404 | −496 | −92 | EXHAUSTION |
| ATGL.NS | −455 | −525 | −69 | EXHAUSTION |
| RUBICON.NS | 62 | 121 | +60 | STOP_LOSS |
| GLAND.NS | 438 | 822 | **+384** | T1_REVERSAL_EXIT |
| **totals** | **3,737** | **1,444** | **−2,293** | |

### §9 Giveback decomposition

| Source | ₹ | n |
|---|---:|---:|
| EXHAUSTION | **−1,181** | 5 |
| STOP_LOSS | **−1,154** | 2 |
| MIS_SQUAREOFF | −225 | 1 |
| TAKE_PROFIT | −117 | 1 |
| T1_REVERSAL_EXIT | **+384** | 1 |
| Market reversal | **≈ 0** | — |
| **Total** | **−2,293** | 10 |

Every number traces to `paper_trades` and to marks from `candles`.

**Market reversal contributed approximately nothing.** The same book held
untouched to 15:29 was worth **+₹2,971** — *more* than the ₹1,444 we realised
and only ₹766 below its 12:00 mark. Prices did drift a little; exits did the
rest.

### §3 Classification

- **RATNAVEER.NS — largest single giveback (₹1,214).** Worth +₹2,221 at 12:00,
  exited at 13:58 via STOP_LOSS for +₹1,007. Still a winner, but it gave back
  55% of its peak. Category **C: held through a retracement until the stop
  caught it.**
- **The five EXHAUSTION exits (₹1,181).** FINCABLES and DREDGECORP were both
  *positive* at 12:00 (+₹297, +₹507) and were exited negative or near-flat.
  Category **E: forced out by EXHAUSTION.**
- **GLAND.NS is the counter-example (+₹384)** — T1_REVERSAL_EXIT beat holding.
  The exit stack is not uniformly harmful.

---

## §10 Statistics (closed trades only, unresolved excluded)

| | TACTICAL | EVENT_DRIVEN |
|---|---:|---:|
| closed | 33 | 1 |
| wins / losses | 13 / 20 | 1 / 0 |
| realised | +₹2,672 | +₹121 |
| avg win / avg loss | +₹681 / −₹309 | — |
| best / worst | +₹1,527 / −₹1,067 | — |
| profit factor | **1.43** | — |
| mean MFE / MAE | +0.67% / −0.27% | +2.22% / −1.20% |

Win rate 41.2% (14/34) against the operator's reported ~35.6% — the difference
is **INSUFFICIENT EVIDENCE**; likely a different denominator (the still-open
position, or a UI window). Profit factor 1.43 with avg win 2.2× avg loss is the
more informative pair, and n=34 in one session supports **no** conclusion about
edge.

### Exit reasons, whole day

| Exit | n | ₹ | mean MFE |
|---|---:|---:|---:|
| TAKE_PROFIT | 3 | **+4,002** | 1.73% |
| STOP_LOSS | 7 | +1,819 | 1.29% |
| T1_REVERSAL_EXIT | 3 | +1,282 | 1.13% |
| MIS_SQUAREOFF | 7 | −847 | 0.41% |
| **EXHAUSTION** | **14** | **−3,463** | **0.27%** |

EXHAUSTION is 41% of all exits and the entire day's loss bucket. Its mean MFE of
**0.27%** repeats the four-day finding: it mostly closes trades that never
worked — but today it also closed two that *were* working (FINCABLES,
DREDGECORP).

---

## §8 CEIGALL control — my own hypothesis, disproven

Phase 16 argued the LLM refused CEIGALL nine times partly because it was
reasoning on 7–21-minute-old candles. **Tested directly. NOT SUPPORTED.**

| Decision | stale bar px | TRUE px | stale day% | true day% | gate ≥1.5% |
|---|---:|---:|---:|---:|---|
| 09:41 | 325.40 | 325.00 | 0.56% | 0.43% | both fail |
| 09:55 | 327.75 | 327.25 | 1.28% | 1.13% | both fail |
| 10:14 | 327.15 | 326.95 | 1.10% | 1.04% | both fail |
| 10:51 | 328.00 | 327.80 | 1.36% | 1.30% | both fail |
| 11:27 | 327.60 | 327.90 | 1.24% | 1.33% | both fail |
| 12:18 | 335.00 | 334.50 | 3.52% | 3.37% | both pass |
| 13:23 | 337.45 | 338.75 | 4.28% | 4.68% | both pass |
| 14:53 | 338.85 | 337.75 | 4.71% | 4.37% | both pass |
| 15:11 | 337.90 | 336.45 | 4.42% | 3.97% | both pass |

Stale and fresh differ by **0.03–0.4 percentage points**. **Not one of the nine
refusals would have changed.** The five morning refusals fail the gate on either
price; the four afternoon ones pass on either — and were still SKIPped, for
reasons unrelated to candle age.

This is a **correction to Phase 16's own emphasis**. Candle staleness is real
and worth fixing; it was **not** what refused CEIGALL.

---

## §5–6 What the market offered and what we did

Today's biggest movers **inside our own tradable universe**:

| Symbol | move% | maxUp% | AI | signals | cap-blocked | traded | class |
|---|---:|---:|---:|---:|---:|---:|---|
| RAMBHAJO.NS | 18.99 | 18.99 | 0 | 0 | 0 | 0 | **3 NEVER EVALUATED** |
| WEL.NS | 18.22 | 20.17 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| RGL.NS | 13.50 | 19.28 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| VOEPL.NS | 11.81 | 16.82 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| INDSWFTLAB.NS | 11.63 | 14.64 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| TVSSRICHAK.NS | 11.16 | 11.82 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| EEPL-SM.NS | 10.72 | 11.84 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| ARIES.NS | 10.59 | 15.95 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| **GENESYS.NS** | 10.40 | 13.76 | 0 | **13** | **5** | 0 | **8 CAPITAL BLOCKED** |
| MILKYMIST.NS | 10.01 | 10.01 | 0 | 1 | 0 | 0 | 2 SIGNALLED, not executed |
| OMAXE.NS | 9.92 | 13.12 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| AASTHA.NS | 9.71 | 9.71 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| SBFC.NS | 9.50 | 10.38 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |
| IDBI.NS | 9.04 | 10.19 | 0 | 0 | 0 | 0 | 3 NEVER EVALUATED |

**11 of 14 were never evaluated at all** — no AI call, no tactical signal.
Notably **AI evaluations = 0 on every one of them**: the news path did not reach
a single one of today's biggest movers.

### §12 Capital

105 signals blocked for capital today across 62 symbols, mean composite score
**81.1**. GENESYS is the one clear case where capital demonstrably prevented a
+10.4% move: 13 signals, 5 refused for capital. **CONFIRMED for GENESYS**;
for the other 13, capital is **RULED OUT** as the first failure — they produced
no signal to block.

---

## §15 Direct answers

**A. Why did ~₹6,000 become ~₹1,863?**
The peak was ₹6,730 at 12:00. ₹2,293 of the giveback is attributable to the ten
positions held at that moment, of which **EXHAUSTION (−₹1,181) and STOP_LOSS
(−₹1,154)** account for essentially all of it. **CONFIRMED.**

**B. How much from market reversal?**
**Approximately zero. RULED OUT.** Nifty −0.29% in the afternoon, smallcaps
+0.05%, metals +1.23%; and the same book held untouched was worth **more** than
what we realised.

**C. How much from our own exit/position management?**
**₹1,527 measured** — held-to-close ₹2,971 versus realised ₹1,444. **CONFIRMED.**

**D. What did the market offer that we did not capture?**
14 movers of +9% to +19% inside our own universe. We traded none.

**E. Missed because of data/news?** — **UNPROVEN.** No news record exists for
these symbols today, but absence of a record is not proof the news did not exist.

**F. Rejected by AI?** — **None. AI evaluations were zero on all 14.** They were
never put to the model.

**G. Blocked by capital?** — **GENESYS.NS only (CONFIRMED).** 13 signals, 5
capital refusals, +10.4%.

**H. Simply not good opportunities?** — **INSUFFICIENT EVIDENCE.** Several are
SME/microcap names that may be circuit-locked and untradeable in size; this was
not tested.

**I. Which subsystem cost the most alpha today?**
Two, and they are different in kind:

- **Realised loss on captured trades: the exit stack.** EXHAUSTION alone
  −₹3,463 across 14 exits at mean MFE 0.27%.
- **Unrealised opportunity: candidate generation.** 11 of the 14 biggest movers
  produced no signal and no AI call. That is a larger number in principle, but
  it is **not quantified** here and must not be treated as a realised loss.

**J. Does this change the four-day conclusions?**

| Prior conclusion | Today |
|---|---|
| Candle staleness impaired AI decisions | **NOT SUPPORTED on CEIGALL** — stale vs fresh differ 0.03–0.4pp; no refusal changes |
| EXHAUSTION mostly closes trades that never worked | **PARTIALLY SUPPORTED** — mean MFE 0.27%, but today it also closed two profitable positions |
| Capital exhaustion blocks better signals | **PARTIALLY SUPPORTED** — 105 blocks, mean score 81.1, but only GENESYS is a demonstrable miss |
| System sees the big movers but does not trade them | **CONFIRMED and worse** — 11 of 14 were never evaluated at all |

---

## Limits of this analysis

- **No intraday equity series exists.** The §1 table is a reconstruction. The
  operator's exact 11:00 and closing display values are **INSUFFICIENT EVIDENCE**.
- **Marks come from our own candles**, which Phase 16 measured at p50 16-minute
  lag. Individual 5-minute rows may be off; the shape and the peak are robust
  because they are corroborated by realised P&L and the operator's observation.
- **Sector attribution of the portfolio was not computed** — `agent_capital_snapshots`
  stopped on 2026-08-18.
- **§7 news cross-reference was not completed** for the 14 movers; "no news
  record" is not "no news".
- **`holding_hours` in `paper_trades` reads 22–43 hours for intraday trades** —
  an unrelated data defect, noted but not investigated.

---

*All figures from the production database, our own 1m candles, and the live
Kite historical API on 2026-08-26. Read-only; no order placed, nothing modified.*
