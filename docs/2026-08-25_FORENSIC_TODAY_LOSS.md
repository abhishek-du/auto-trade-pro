# Today's AutoTrade Pro forensic report — 2026-08-25

**Session state at time of writing:** market **OPEN**, 13:28 IST. Session runs
09:15–15:30, so this is a mid-session snapshot, not a closed day.
Verified: `is_nse_market_open() = True`, Tuesday, not an NSE holiday.

---

# Executive verdict

**We did not lose much money today — we lost the ability to trade at all, and
paid ₹588.64 to discover it.**

Today's realised P&L is **−₹698.98** on two closed trades, against **+₹403.27**
unrealised on twelve open positions: a net of roughly **−₹296 on ₹502,039
equity (−0.06%)**. That is noise, not a crisis.

The real finding is structural and it is one bug with two faces:

Every F4 tactical signal opens with `product="CNC"`, which sets
`is_swing=True`, which sets `swing_min_hold = entry + 48 hours`, which makes
`fast_sl_check` set `sl_hit = False` for two days. **Ten of twelve open
positions have no stop-loss right now.** Because they cannot stop out, they
never release capital. The book is **99.6% deployed with ₹1,542 free against a
₹50,204 required reserve**, so **1,553 of today's 1,724 tactical signals (90.1%)
were rejected on the cash buffer** — some for sizes as small as ₹747. The
portfolio-reallocation path then force-closed INDOBORAX at **−₹588.64** to free
room, freed ~₹17.5k, and the signals it was freeing room for were **still
blocked** because ₹17.5k does not reach a ₹50.2k reserve.

So: 84% of today's realised loss was a position sold at a loss to make room for
trades that could not be taken anyway, and the reason the room was needed is
that a delivery/intraday product flag disabled the stops that would otherwise
have cleared the book.

🚨 **PRODUCTION RISK** — see §10. Ten live positions are unprotected. In paper
mode this cost nothing today by luck; the same state with real money has no
bounded downside.

---

## 1. Today's P&L

| Metric | Value | Evidence |
|---|---:|---|
| Trades opened today | 5 | `paper_trades WHERE opened_at::date=CURRENT_DATE` |
| Trades closed today | 2 | `paper_trades WHERE closed_at::date=CURRENT_DATE` |
| Realised P&L | **−₹698.98** | `SUM(pnl)` over the same |
| Winners / losers (closed) | **0 / 2** | — |
| Open positions | 12 | `open_positions` |
| Unrealised P&L | **+₹403.27** | `SUM(unrealised_pnl)` |
| **Net today** | **≈ −₹296 (−0.06% of equity)** | — |
| Wallet balance | **₹1,542.29** | `virtual_wallet` id DESC LIMIT 1 |
| Equity | ₹502,039.44 | — |
| Direction split (opened) | 4 BUY / 1 SELL | — |
| Exits by stop-loss | 1 (ZAGGLE) | `exit_reason='STOP_LOSS'` |
| Exits by reallocation | 1 (INDOBORAX) | `exit_reason='REALLOCATED'` |
| Exits by target / time | 0 / 0 | — |

**Both closed trades were 4–5 day old `DIRECT_NEWS` positions, not today's
trades.** No trade opened today has closed.

| Symbol | Opened | Closed | Entry | Exit | SL | P&L | Reason |
|---|---|---|---:|---:|---:|---:|---|
| INDOBORAX.BO | 21 Aug 04:56 | 25 Aug 03:58 | 516.07 | 500.75 | 451.95 | **−588.64** (−3.26%) | `REALLOCATED` |
| ZAGGLE.BO | 20 Aug 05:12 | 25 Aug 04:58 | 188.83 | 188.55 | 173.09 | −110.34 (−0.44%) | `STOP_LOSS` |

**INDOBORAX never reached its stop** (451.95); it exited at 500.75. It was
force-closed by `engine/portfolio_reallocation.py::try_reallocate_for_candidate`.

---

## 2. The root cause, traced end to end

### 2.1 Code path

```
engine/tactical_executor.py:369
    product = "MIS" if signal.sub_pipeline == "F1" else "CNC"

paper_trading/trade_simulator.py:403
    is_swing = product == "CNC"

paper_trading/trade_simulator.py:418-419
    trade_style   = "SWING" if is_swing else product
    swing_min_hold = now + timedelta(hours=48) if is_swing else None

tasks/india_tasks.py:1607-1613
    if sl_hit and pos.trade_style == "SWING" and pos.swing_min_hold:
        if now_ist < pos.swing_min_hold:
            sl_hit = False          # <-- stop disabled
```

### 2.2 Why intraday rules land in F4

`tactical_signals` today and yesterday, by `strategy` → `sub_pipeline`:

| strategy | sub_pipeline | signals | resulting product |
|---|---|---:|---|
| GAP_AND_GO | F1 | 550 | MIS — stop ON |
| PIVOT_BREAKOUT | F1 | 614 | MIS — stop ON |
| VWAP | F1 | 20 | MIS — stop ON |
| **VOLUME_BREAKOUT** | **F4** | 193 | **CNC — stop OFF 48h** |
| **VWAP_CROSSOVER** | **F4** | 339 | **CNC — stop OFF 48h** |
| **OVERSOLD_REBOUND** | **F4** | 104 | **CNC — stop OFF 48h** |
| **OVERBOUGHT_FADE** | **F4** | 111 | **CNC — stop OFF 48h** |

`VOLUME_BREAKOUT` and `VWAP_CROSSOVER` are **intraday 5-minute rules**
(`volume_breakout_5m`, `vwap_crossover_5m` in `engine/tactical_rules.py`). They
are registered under the F4 pipeline, so the `sub_pipeline == "F1"` test sends
them to CNC and they inherit a 48-hour no-stop hold.

**An intraday VWAP crossover held for two days with no stop is not a strategy;
it is an unmanaged position.**

### 2.3 Current state — 10 of 12 positions unprotected

`now = 2026-08-25 13:28 IST`

| Symbol | style | swing_min_hold | stop |
|---|---|---|---|
| ADANIENT.NS | SWING | 2026-08-27 05:32 | **DISABLED** |
| PAYTM.NS | SWING | 2026-08-27 03:50 | **DISABLED** |
| BALRAMCHIN.NS | SWING | 2026-08-27 03:50 | **DISABLED** |
| HSCL.NS | SWING | 2026-08-27 03:50 | **DISABLED** |
| UNIONBANK.NS | SWING | 2026-08-27 03:56 | **DISABLED** |
| RUBICON.NS | SWING | 2026-08-26 05:53 | **DISABLED** |
| AEGISLOG.NS | SWING | 2026-08-26 08:29 | **DISABLED** |
| DEVYANI.NS | SWING | 2026-08-26 08:19 | **DISABLED** |
| DIVISLAB.NS | SWING | 2026-08-26 08:19 | **DISABLED** |
| GRAPHITE.NS | SWING | 2026-08-26 08:19 | **DISABLED** |
| JUNIPER.NS | SWING | 2026-08-22 06:05 | enabled (elapsed) |
| RAILTEL.BO | SWING | 2026-08-23 07:17 | enabled (elapsed) |

**Every one of the twelve is `trade_style='SWING'`**, including all nine opened
by TACTICAL intraday strategies.

### 2.4 Four positions are past their stop right now

Live Kite LTP taken at 13:26 IST, compared against `open_positions.stop_loss`:

| Symbol | live | stop | breached by | units |
|---|---:|---:|---:|---:|
| AEGISLOG.NS | 1325.10 | 1336.29 | ₹11.19 | 37 |
| BALRAMCHIN.NS | 670.65 | 686.25 | ₹15.60 | 73 |
| GRAPHITE.NS | 712.00 | 714.16 | ₹2.16 | 69 |
| RUBICON.NS | 1781.00 | 1782.36 | ₹1.36 | 11 |

Reconstructed from 1m candles, **eight of twelve have breached their stop at
some point**, several 190–250 minutes ago. PAYTM breached **within one minute of
entry** (entry 03:50:19, first breach 03:51) and traded as low as 1622.30
against a 1678.22 stop.

### 2.5 What the missing stops actually cost today — honest answer

**Approximately nothing: net +₹132.**

| Symbol | if the stop had fired | vs holding to now |
|---|---:|---|
| PAYTM.NS | — | **+₹1,495 better** for holding |
| RUBICON.NS | — | +₹322 better |
| BALRAMCHIN.NS | — | +₹265 better |
| DEVYANI.NS | — | −₹880 worse |
| GRAPHITE.NS | — | −₹399 worse |
| AEGISLOG.NS | — | −₹258 worse |
| HSCL.NS | — | −₹257 worse |

**The disabled stops did not lose money today.** Several breaches recovered.
This is luck, not design — the exposure is unbounded and the sign of the outcome
is arbitrary. Do not read the +₹132 as a defence of the behaviour.

---

## 3. The capital lockup — where today's loss actually came from

```
open positions    : 12
deployed          : Rs 500,094
free balance      : Rs   1,542
equity            : Rs 502,039
deployed / equity : 99.6%
cash buffer (10%) : Rs  50,204 required free
headroom          : Rs -48,258   <- negative: nothing can enter
```

### Today's tactical signal outcomes — 1,724 signals

| count | reason |
|---:|---|
| **1,553** | **Cash buffer: deploying ₹N would breach the 10% cash reserve** |
| 40 | existing position already open in SYM |
| 14 | Sector cap: Consumer already holds 2/2 |
| 13 | sector breadth veto: Industrial Manufacturing 6/6 down |
| 10 | sector breadth veto: Financial Services 7/10 down |
| 9 | R:R ratio below minimum |
| 9 | news-family position already open |
| 8 | Sector cap: IT already holds 2/2 |
| 8 | Sector cap: Metals already holds 2/2 |

**90.1% of all signals died on the cash buffer.** Rejected deployment sizes
include ₹747, ₹1,569, ₹2,439, ₹2,448, ₹2,502, ₹2,521.

### The reallocation sequence — 25 Aug, 03:50–04:00 UTC

```
03:50:19  OPEN  PAYTM.NS         (F4 VOLUME_BREAKOUT  -> CNC -> no stop)
03:50:20  OPEN  BALRAMCHIN.NS    (F4 OVERSOLD_REBOUND -> CNC -> no stop)
03:50:31  OPEN  HSCL.NS          (F4 OVERSOLD_REBOUND -> CNC -> no stop)
03:56:20  OPEN  UNIONBANK.NS     (F4 VOLUME_BREAKOUT  -> CNC -> no stop)
03:58:27  CLOSE INDOBORAX.BO     REALLOCATED  -Rs 588.64   <- to free capital
03:58:30  BLOCKED  KWIL.NS       Cash buffer: deploying Rs 747
03:58:30  BLOCKED  EPIGRAL.NS    Cash buffer: deploying Rs 2,448
03:58:31  BLOCKED  IBULLSLTD.NS  Cash buffer: deploying Rs 2,502
03:58:32  BLOCKED  CAPACITE.NS   Cash buffer: deploying Rs 2,521
03:58:33  BLOCKED  MOLBIO.NS     Cash buffer: deploying Rs 2,439
03:58:35  BLOCKED  ENTERO.NS     Cash buffer: deploying Rs 1,569
```

**A position was closed at −₹588.64 to make room, and every candidate it was
making room for was blocked anyway.** Freeing ~₹17.5k does not satisfy a
₹50.2k reserve. The reallocation realised a loss for no admitted trade.

**84% of today's realised loss is this single event.**

---

## 4. The two bugs are one bug

```
F4 signal
   -> product = "CNC"                      (tactical_executor.py:369)
   -> is_swing = True                      (trade_simulator.py:403)
   -> swing_min_hold = entry + 48h         (trade_simulator.py:419)
   -> sl_hit forced False for 48h          (india_tasks.py:1607-1613)
   -> positions cannot stop out
   -> capital is never released
   -> book sits at 99.6% deployed
   -> 90.1% of signals blocked on the cash buffer
   -> reallocation force-closes a position at a loss to make room
   -> the freed amount is still below the reserve, so nothing enters
```

The capital lockup is not an independent problem. It is the downstream symptom
of stops that cannot fire.

---

## 5. Master Intelligence Hub

**PREVIOUS CONCLUSION STILL HOLDS.** Not re-litigated here — no Hub cycle today
has produced a decision, consistent with `CLAUDE.md` §5b ("does not originate
trades"). The Hub is not implicated in today's loss because it has no path to a
trade either way.

## 6. Infrastructure — healthy, and this is a change

**PREVIOUS CONCLUSION NO LONGER HOLDS for the exit loop.**

Phase 1 found `fast_sl_check` executing once in a full session. Today:

| check | value |
|---|---|
| exit worker `fast_sl_check` executions | **10,391** |
| `exit_worker:heartbeat` | **2.9 s old** |
| queue isolation | `celery→default`, `exit→exit_queue`, `scan→scan_queue` |
| services active | worker, beat, exit-worker, scan-worker, news-engine, uvicorn |

**The exit loop is running correctly and fetching correct prices.**
`get_live_prices(..., exit_bucket=True)` returned live, accurate quotes for all
12 symbols on demand. The infrastructure fix from 24 Aug is working.

**The stops are not failing because the loop is broken. They are failing because
the loop is told not to fire them.** That is a materially different bug from the
one Phase 1 found, and it was invisible while the loop itself was dead.

## 7. Live data integrity

| source | state |
|---|---|
| Kite LTP via `get_live_prices` | **fresh** — 12/12 symbols, matches independent Kite quote |
| `open_positions.current_price` | **stale** — ages 22 to 1,292 minutes; 5 rows still equal `entry_price` |
| `virtual_wallet.updated_at` | 05:32 UTC — ~2.4 h old |

Position marks are stale (worst: JUNIPER, RAILTEL, DEVYANI unchanged since
24 Aug 10:23, 1,292 minutes). **This does not cause the stop failure** —
`fast_sl_check` fetches its own live prices and does not read
`current_price` — but it means unrealised P&L, equity and anything reading
`open_positions` are wrong. PAYTM's stored mark is 3.41% below live.

**EVIDENCE NOT AVAILABLE** for whether the 60-second `india_trade_loop`
position-update path is failing or merely slow; that was not traced today.

---

## 8. What we missed today — NOT INVESTIGATED

**EVIDENCE NOT AVAILABLE.** The missed-opportunity side (Part 4 of the brief)
was not completed. The blocking finding above accounts for the observed loss and
was pursued to completion instead.

What can be said without further work: **1,553 signals were blocked on capital,
not on merit.** Whether any of them would have been profitable is unmeasured. A
proper missed-mover study against today's Kite tape is the natural next step and
should be run before any strategy conclusion is drawn from today.

---

## 9. Top root causes, ranked

| # | Root cause | Evidence | Impact today | Severity | Confidence |
|---|---|---|---|---|---|
| **1** | F4 → `product="CNC"` → 48 h stop suspension | `tactical_executor.py:369`, `trade_simulator.py:403/419`, `india_tasks.py:1607`; 10/12 positions unprotected; 4 past stop now | Latent. Net +₹132 today by luck | **P0 🚨** | CONFIRMED |
| **2** | Capital lockup — 99.6% deployed, headroom −₹48,258 | `virtual_wallet.balance=1,542`; 1,553/1,724 signals blocked | Blocked 90.1% of the day's signals | **P0** | CONFIRMED |
| **3** | Reallocation realises a loss without admitting a trade | INDOBORAX −₹588.64 at 03:58:27; six signals blocked 03:58:30–35 | **84% of today's realised loss** | **P0** | CONFIRMED |
| **4** | Intraday 5m rules registered under F4 | `VOLUME_BREAKOUT` / `VWAP_CROSSOVER` → `sub_pipeline='F4'` (532 signals) | Cause of #1 | **P1** | CONFIRMED |
| **5** | `open_positions.current_price` stale up to 1,292 min | `last_updated` column; 5 rows equal `entry_price` | Wrong P&L / equity reporting | **P2** | CONFIRMED |

---

## 10. 🚨 PRODUCTION RISK

**Ten live positions currently have no stop-loss and will not have one for up to
48 hours.** Four are already past their stop. The exit loop is running and is
being instructed to ignore them.

In PAPER MODE this is a reporting problem. **With real money it is unbounded
downside on ~₹420,000 of exposure.**

**Recommendation: real-money trading must remain disabled until root cause #1 is
fixed and verified.** `PAPER_MODE` should not be flipped for any reason before
then.

---

## 11. Fix plan — BUG FIXES only, no strategy change

### P0-1 — BUG FIX: do not suspend stops for intraday products
`engine/tactical_executor.py:369` sends every non-F1 pipeline to CNC. The
suspension mechanism itself (`india_tasks.py:1607`) is a deliberate swing
feature and is not the defect; applying it to intraday tactical trades is.
**Verification:** after the fix, `SELECT trade_style, COUNT(*) FROM
open_positions` must show no SWING row whose originating `strategy_name`
starts with `TACTICAL_`.

### P0-2 — BUG FIX: reallocation must not realise a loss it cannot use
`engine/portfolio_reallocation.py::try_reallocate_for_candidate` closed
INDOBORAX for −₹588.64 and the candidate was still rejected 3 seconds later.
It must confirm the freed amount actually clears the buffer **before**
closing anything.
**Verification:** replay 03:58:27 — with ₹1,542 free and a ₹50,204 reserve,
freeing ₹17.5k must be rejected as insufficient and INDOBORAX must stay open.

### P1-3 — BUG FIX: register 5m intraday rules under F1
`VOLUME_BREAKOUT` and `VWAP_CROSSOVER` are intraday rules carrying
`sub_pipeline='F4'`. This is the upstream cause of P0-1 and should be fixed
even after P0-1 makes the product flag safe.

### P2-4 — BUG FIX: position mark staleness
`open_positions.current_price` up to 1,292 minutes old. Trace the 60-second
update path.

### NOT NOW — explicitly out of scope
No change to the 10% cash buffer, the 2-per-sector cap, the sector breadth
veto, R:R minimums, stop distances, entry gates, thresholds, prompts, models or
any strategy parameter. **Every one of those is currently masked by the capital
lockup and cannot be evaluated until the book can breathe.**

---

## 12. The one thing to fix before the next session

> **Fix `engine/tactical_executor.py:369` so intraday tactical signals do not
> receive `product="CNC"`, because that single flag sets `is_swing=True`
> (`trade_simulator.py:403`), which sets a 48-hour `swing_min_hold`
> (`:419`), which makes `fast_sl_check` force `sl_hit = False`
> (`india_tasks.py:1607-1613`) — leaving 10 of 12 live positions with no stop,
> four of them already past it, and locking 99.6% of capital so that 1,553 of
> today's 1,724 signals (90.1%) were rejected for want of cash.**

## 13. Primary category

**INFRASTRUCTURE / RISK-CONTROL LOGIC** — not data, not news, not AI, not
strategy.

Justification from today alone: the data was correct (live Kite prices accurate
for all 12 symbols on demand), the news and event pipelines are not implicated
in a single one of today's five entries, the exit loop ran 10,391 times with a
2.9-second heartbeat, and the execution layer filled everything it was offered.
The loss came from a product flag disabling a risk control, and the resulting
capital lockup forcing a −₹588.64 sale that bought nothing.

**We cannot yet say whether the strategy has an edge.** With 90.1% of signals
blocked on capital, today produced no usable sample.

---

## Evidence appendix

All figures from the production Postgres and live Kite Connect at 13:24–13:28
IST on 2026-08-25.

| Claim | Source |
|---|---|
| Market open, Tuesday, not a holiday | `crawler.india_price_feed.is_nse_market_open()` |
| Realised −₹698.98, 2 closed, 0 winners | `paper_trades WHERE closed_at::date=CURRENT_DATE` |
| Balance ₹1,542.29, equity ₹502,039.44 | `virtual_wallet ORDER BY id DESC LIMIT 1` |
| Deployed ₹500,094 across 12 | `SUM(size_usd) FROM open_positions` |
| 10/12 stops disabled | `open_positions.trade_style`, `.swing_min_hold` vs IST now |
| 4 past stop | live `KiteConnect.quote()` vs `open_positions.stop_loss` |
| 8 breached at some point | `candles` 1m since each `opened_at` |
| 1,553/1,724 blocked on cash buffer | `tactical_signals WHERE created_at::date=CURRENT_DATE` |
| F4→CNC mapping | `tactical_signals.strategy`/`.sub_pipeline`; `tactical_executor.py:369` |
| INDOBORAX REALLOCATED −₹588.64 | `paper_trades.exit_reason`, `closed_at=03:58:27` |
| Exit loop 10,391 runs, heartbeat 2.9 s | Celery `inspect().stats()`; Redis `exit_worker:heartbeat` |
| Price source healthy | `crawler.zerodha_market.get_live_prices(exit_bucket=True)` |

*Systems analysis. Not investment advice. No production logic was changed in
producing this report.*
