# STEP 2B — Upstox Market Data Integrity Audit

**Date:** 2026-09-04 · **Mode:** READ-ONLY audit · **Market state:** OPEN (Friday, 14:30–14:55 IST)
**Production code changed:** none. Added `tests/test_step2b_market_data_invariants.py` (22 tests)
and two re-runnable scripts under `autotrade-backend/audit/`.

> Every number below is a measurement taken during this audit. Where something
> could not be measured it is marked **UNPROVEN**, not estimated.

---

## 1. Executive verdict

**The Upstox market-data foundation is NOT yet trustworthy for point-in-time prediction research.**

Connectivity and API correctness are genuinely good. Data correctness is not.
Five findings are material; two would silently corrupt any dataset built today.

The previous report's headline metrics were true and misleading in exactly the way
this brief anticipated. The clearest demonstration: `ticks_held=2677` with
`state=connected` became, twelve hours later, `ticks_held=2677` with
**`connected=False`**. The same number, with the feed dead. It counts cached
records, not instruments receiving ticks.

### The two blockers

| # | Finding | Evidence |
|---|---|---|
| **B1** | Daily history is split across **two irreconcilable series** | 162/376 hub symbols differ >20% between them; 82 differ >60%. DELTACORP 64.50 → 181.70 (+181.7%) |
| **B2** | Live daily bars carry a **one-day date-label offset** | Bar stored `2026-08-30 18:30 UTC` is Monday 31 Aug's session. `timestamp::date` reports the day *before* the trading day |

B2 is the more dangerous of the two, because it looks plausible: the dates line
up, they are simply all shifted by one.

### Verdict by category

| Category | Verdict |
|---|---|
| Connectivity | **PASS** |
| Schema correctness | **PASS** |
| Identity correctness | **PARTIAL** |
| Temporal correctness | **FAIL** |
| Numerical correctness | **PARTIAL** |
| Completeness | **PARTIAL** |
| Persistence correctness | **PARTIAL** |
| Live freshness | **FAIL** |

---

## 2. Instrument master — PASS (master) / PARTIAL (persisted)

### Exact counts, from `NSE.json.gz`

| Segment | Count |
|---|---|
| **Total instruments** | **76,596** |
| NSE_FO | 32,489 |
| NSE_COM | 24,913 |
| NSE_EQ | 9,705 |
| NCD_FO | 9,350 |
| NSE_INDEX | 139 |

### Why exactly 2,639 rows are classified NSE_EQ

The sync's filter is `segment == "NSE_EQ" AND instrument_type == "EQ"`, which
today yields **2,638** (the 2,639 figure was yesterday's file; one listing
changed). The other **7,067** NSE_EQ rows are non-equity series:

| Series | Count | What it is |
|---|---|---|
| SG | 4,311 | State Development Loans |
| N0 | 990 | NCDs |
| SM | 450 | SME |
| BE | 253 | Trade-for-trade |
| GS | 131 | Government securities |
| ST | 116 | — |
| TB | 84 | Treasury bills |

The filter is **correct**: everything excluded is debt, SME or a restricted series.

### Master identity integrity — clean

| Check | Result |
|---|---|
| duplicate `instrument_key` | **0** |
| duplicate `trading_symbol` | **0** |
| duplicate ISIN | **0** |
| NULL/blank `instrument_key` | **0** |
| NULL/blank ISIN | **0** |
| `instrument_key` not prefixed `NSE_EQ|` | **0** |
| malformed ISIN | **0** |
| unusual `trading_symbol` | **0** |

### Persisted table — NOT clean

| Check | Result |
|---|---|
| duplicate `instrument_key` (NSE) | **1** |
| duplicate ISIN (NSE) | **1** |
| duplicate (exchange, tradingsymbol) | 0 |
| NSE rows whose key is not `NSE_EQ|` | 0 |
| BSE rows carrying an `instrument_key` | 0 |
| rows with a `.BO` tradingsymbol | 0 |

**F1 — Symbol-change collision.**

```
NSE_EQ|INE348N01042  ->  ['KDGREEN', 'MANBRO']
_to_key('MANBRO.NS')  == NSE_EQ|INE348N01042
_to_key('KDGREEN.NS') == NSE_EQ|INE348N01042
get_live_prices(['MANBRO.NS','KDGREEN.NS'])  ->  {'KDGREEN.NS': 50.91}
```

MANBRO was renamed KDGREEN. The old Kite row (refreshed 08-29) and the new bulk
row (09-03) both persist, so one `instrument_key` maps to two symbols and a batch
quote **silently returns one symbol instead of two** — no error, no warning.

Current impact is nil (neither is in `hub_universe`, neither has an open
position), but the mechanism would drop a held position's price without raising.

**F2 — `instrument_type` is unreliable for NSE rows.** All 10,323 persisted NSE
rows are labelled `'EQ'`, including **7,163** carrying a series suffix
(`-SG`, `-N0`, `-SM`…). The old Kite sync flattened the series. Do not filter on
this column; filter on the symbol pattern or re-derive from the master.

**F3 — 7,686 stale rows.** Present in the DB, absent from today's master —
legacy debt instruments (`0ABCL31-N0`, `0IRFC35-N0`…) from the dead Kite sync.
Harmless while filtered, but they inflate any naive `count(*)` denominator.

---

## 3. NSE-only active feed — PASS

| Check | Result |
|---|---|
| Subscription map size | 2,678 |
| Segments present | **`NSE_EQ` only (2,678)** |
| Symbols ending `.BO` in the map | **0** |
| `hub_universe` rows ending `.BO` | **0** / 1,634 |
| `NSE_ONLY_UNIVERSE` | `True` |

Gate behaviour verified directly:

| Input | Allowed | Reason |
|---|---|---|
| `RELIANCE.NS` | ✅ | — |
| `RELIANCE.BO` | ❌ | `BSE_SYMBOL` |
| `RELIANCE` (bare) | ❌ | `UNRESOLVED_BARE_SYMBOL` |
| `FOO.XYZ` | ❌ | `UNKNOWN_EXCHANGE` |

### Every BSE occurrence, classified

| Location | Class | Note |
|---|---|---|
| `engine/hub_universe.py:49` | **B — conditional** | Gated on `NSE_ONLY_UNIVERSE=True`; live table has 0 `.BO` |
| `crawler/india_price_feed.py:960` (`ki_bse`) | **E — dead code** | Query executes but the result is never consumed; a final NSE gate follows |
| `utils/config.py:1042/1046` (`WATCHLIST_BSE_*`) | **E — dead code** | Defined, imported nowhere |

**Zero active BSE equity subscriptions.** Historical BSE data (12,913 rows) left untouched.

---

## 4. WebSocket tick quality — FAIL

**The feed has been down for the entire trading session.** Market opened 09:15 IST;
this audit ran 14:30–14:55.

```
2026-09-04 05:00 IST  [upstox/websocket] Handshake status 401 Unauthorized
                      connected=False   ticks_held=2677   subscribed=2678
```

**F4 — the streamer cannot survive the daily token rotation.**

The Upstox access token rotates at **03:30 IST**. The socket object bakes its
token in at construction. It dropped at 05:00 and every auto-reconnect retried
with the dead credential. REST on the *current* token returns 200 — so this is
not an auth failure, it is a stale-credential-in-a-long-lived-object failure.

This is the **identical failure mode `CLAUDE.md` documents for the KiteTicker**
("bakes the now-expired token in at construction and auto-reconnects on it"),
now reproduced on Upstox.

### The `ticks_held` question, answered

> *"Check whether `ticks_held=2677` means 2677 instruments with recent ticks, or
> merely 2677 cached instrument records."*

**It means cached records.** The counter was identical before and after the feed
died. Right now it represents 2,677 records roughly **17 hours old**.

### What is NOT broken

Stale ticks are **not** leaking into pricing. `market_snapshot` correctly fell
through to `upstox_rest` (RELIANCE 1329.40). The `_age_seconds` guards work as
designed.

### UNPROVEN — requires a live feed

Tick frequency · zero/negative prices · impossible jumps · duplicate ticks ·
out-of-order timestamps · symbol mismatch · malformed messages · % of subscribed
instruments actually ticking.

---

## 5. REST vs WebSocket — PASS (REST) / UNPROVEN (WS)

18 symbols, three liquidity tiers, near-simultaneous. **Not just RELIANCE/HDFCBANK.**

| Tier | Symbols | Max Δ (LTP vs quote vs OHLC) |
|---|---|---|
| Large (rank 1–20) | HDFCBANK, BSE, RELIANCE, BHARTIARTL, ETERNAL, ICICIBANK | **0.000%** |
| Mid (rank ~40%) | ABBOTINDIA, PSUBNKBEES, FINEORG, IKS, ALEMBICLTD, IBULLSLTD | **0.000%** |
| Low (rank ~90%) | PATELRMART, SHANKARA, PVTBANKADD, RUPA, STANLEY, DHABRIYA | **0.000%** |

LTP v2, full-quote and OHLC V3 agree **exactly** at every tier. No symbol
mismatch, no instrument_key mismatch, no stale response.

WebSocket comparison: **UNPROVEN** — feed down.

---

## 6. Historical candles — PARTIAL

| TF | Rows | Symbols | Range | OHLC violations | Duplicates | Future ts |
|---|---|---|---|---|---|---|
| 1m | 25,592,878 | 3,737 | 2026-06-18 → 2026-09-03 | **0** | 0 | 0 |
| 1d | 5,862,771 | 4,350 | 2016-01-01 → 2026-09-04 | **14** | 0 | 0 |
| 5m | 3,217,328 | 2,984 | 2026-06-09 → 2026-09-04 | **0** | 0 | 0 |
| 1h | 823,275 | 2,991 | 2023-06-30 → 2026-09-04 | **0** | 0 | 0 |
| 15m | 235,122 | 2,631 | 2026-06-19 → 2026-09-03 | **0** | 0 | 0 |

**Invariants** (`low ≤ open ≤ high`, `low ≤ close ≤ high`, `high ≥ low`): 14
violations in 35.7M rows, **all INAV instruments** — ETF net-asset-value feeds
with `open=0.0` alongside `high=1047`, all dated 2026-07-06. Not equities;
safely excludable. 22 non-positive-price rows, same instruments.

**Session window:** 1m spans exactly **03:45–09:59 UTC** with **0** rows outside
it. Zero weekend 1m rows.

**Timestamp convention — verified, not guessed:** candle **OPEN**, naive UTC.

---

## 7. Live candle builder — PASS (contract) / UNPROVEN (live)

22 deterministic tests, all passing, covering the boundaries specified in the brief.

| Property | Result |
|---|---|
| First tick opens a bucket, emits nothing | ✅ |
| Ticks at :01 / :15 / :59 do not roll over | ✅ |
| 09:16:00 closes the 09:15 bar | ✅ |
| Emitted timestamp = `2026-09-04T03:45:00` (09:15 IST) | ✅ |
| OHLC correct across a bucket | ✅ |
| Volume = `last_cum − first_cum` | ✅ |
| Volume never negative on a counter reset | ✅ |
| 09:15→09:20 yields exactly 5 bars | ✅ |
| No duplicate bar for one minute | ✅ |
| A published bar is never mutated by a late tick | ✅ |
| Symbols bucketed independently | ✅ |
| Zero/negative price rejected | ✅ |

**Two of my own assumptions were wrong and the code was right.** I expected the
bar to carry a `datetime` timestamp and a `symbol` key. It emits a naive-UTC ISO
**string** (the bar goes to Redis as JSON) and **no symbol** (the caller owns
that mapping). The tests were corrected to the real contract.

**F5 — `ltpc` mode yields zero volume by design.** Only ~500 priority
instruments receive `full` mode; the rest get `ltpc`, which returns
`volume_traded: 0` and `ohlc {open:0, high:0, low:0}`. **~79% of subscribed
instruments therefore produce zero-volume tick bars**, and their OHLC fields are
zeros. This is a documented mode contract, not a bug — but any volume feature
built on tick-derived bars must know it.

Live construction: **UNPROVEN** — feed down.

---

## 8. Live vs historical candles — FAIL

120 hub symbols × 5 completed sessions, stored bars vs Upstox historical:

| Metric | Match | Differ | Detail |
|---|---|---|---|
| **Close** | 475 | 117 | **80.2% match**; median 0.175%, p90 0.498%, max 2.03%; 4 differ >1%, 0 >5% |
| **Volume** | 387 | 205 | **65.4% match**; median **−0.384%**, p10 −2.85% |
| **Missing** | — | 8 | broker bars absent from the DB |

**F6 — stored bars are pre-settlement snapshots.**

**100% of the 205 volume differences are stored-LOWER than the broker.** A
perfectly one-sided bias across 205 observations is not noise — the bar was
captured before the session settled (missing the closing auction) and never
re-fetched.

Consequence: any volume-based feature computed from stored daily bars is
**systematically understated**, and closes drift ~0.18% from the settled value.

---

## 9. Database persistence — PARTIAL

| Property | Value |
|---|---|
| `candles.timestamp` type | `timestamp without time zone` (naive UTC) ✅ |
| Unique constraint | `uq_candle_bar UNIQUE (symbol, timeframe, timestamp)` ✅ |
| Duplicate bars, all timeframes | **0** ✅ |
| Future-dated rows | **0** ✅ |

**F7 — the dead 00:00 daily series is still being written.**

3–4 rows/day, created 09:01 today. The symbols are **indices**:

```
^NSEBANK   ts=2026-09-04 00:00:00  created=2026-09-04 09:01:01
^NSEI      ts=2026-09-04 00:00:00  created=2026-09-04 09:01:00
NIFTYBEES  ts=2026-09-04 00:00:00  created=2026-09-04 09:01:00
```

Compare the live series on the same days: **1,115–1,714 rows/day at 18:30**.

So **index daily bars sit in the dead series while stock daily bars sit in the
live one.** A query joining index context to stock data on `timestamp` silently
returns nothing.

---

## 10. Volume semantics — PASS (established by measurement)

Determined empirically, not assumed:

| Source | Semantics | Evidence |
|---|---|---|
| **Candle volume** (1m/5m/…/1d) | **Per-interval** | Not monotonic across the day; Σ(1m) = 10,494,234 vs quote 10,506,479 — differing only by the partial current minute |
| **REST full-quote `volume`** | **Cumulative session** | Matches Σ(1m), not max(1m) |
| **WebSocket tick `volume_traded`** | **Cumulative session** | Builder correctly diffs it; documented in its own docstring |

Three different semantics in three places. A volume feature must state which it uses.

---

## 11. Timezone / market hours — FAIL

Correct: 09:15 IST → 03:45 UTC · 15:29 IST → 09:59 UTC · `_to_naive_utc` verified ·
0 rows outside the session window · `candles.timestamp` is naive UTC throughout.

**F8 — one-day date-label offset on the live daily series.** ← *blocker B2*

```
stored 2026-09-02 18:30 UTC (Wed)  ->  IST Thu 03 Sep   close=1302.5
stored 2026-09-01 18:30 UTC (Tue)  ->  IST Wed 02 Sep   close=1313.1
stored 2026-08-30 18:30 UTC (Sun)  ->  IST Mon 31 Aug   close=1277.0
```

RELIANCE closed at **1277.0 on Monday 31 Aug** — independently confirmed against
Upstox. The stored label is **Sunday 30 Aug**.

`18:30 UTC + 05:30 = 00:00 IST of the *next* day`, so **`timestamp::date` reports
the day before the trading day** for every bar in the live series.

This also fully explains the **203,728 "weekend" daily rows**: they are Monday
sessions labelled Sunday. (19,439 further weekend rows sit in the 00:00 series.)

Any point-in-time join keyed on `timestamp::date` is off by one day — and looks
correct.

---

## 12. Corporate actions — FAIL

Candles are **UNADJUSTED**.

### Within the live series, since 2026-06-01 (hub symbols)

| Threshold | Count |
|---|---|
| jumps > 20% | **46** |
| jumps > 40% | **14** |
| jumps > 60% | **11** |

| Symbol | Date | Prev → Close | Move |
|---|---|---|---|
| GOLDADD | 2026-08-27 | 152.93 → 15.40 | −89.93% |
| SILVERADD | 2026-08-27 | 228.00 → 23.21 | −89.82% |
| TEMBO | 2026-08-03 | 556.10 → 57.40 | −89.68% |
| PSUBANK | 2026-07-09 | 823.52 → 86.00 | −89.56% |
| CORDELIA | 2026-08-23 | 967.00 → 104.35 | −89.21% |
| INDIAGLYCO | 2026-09-01 | 1111.70 → 236.20 | −78.75% |

A clean −89.9% is a 1:10 split, not a market move.

### Across the two series — the fake-gap risk ← *blocker B1*

Last close of the dead (00:00) series vs first close of the live (18:30) series:

| Threshold | Symbols |
|---|---|
| differ > 20% | **162 / 376** |
| differ > 40% | **120 / 376** |
| differ > 60% | **82 / 376** |

| Symbol | Dead last | Live first | Diff |
|---|---|---|---|
| DELTACORP | 64.50 | 181.70 | **+181.7%** |
| ABFRL | 62.43 | 166.15 | +166.1% |
| CHEMPLASTS | 202.60 | 535.60 | +164.4% |
| BATAINDIA | 714.60 | 1629.95 | +128.1% |
| ALKYLAMINE | 1798.80 | 3606.55 | +100.5% |
| DIACABS | 206.22 | 2.35 | −98.9% |

The dead series holds pre-split history back to 2016 (JLHL last close **1348.0**);
the live series starts ~June 2026 (JLHL first close **315.45**). **They cannot be
concatenated.** This is the same defect class that produced the previously
retracted +1.9% fake gap — now quantified rather than anecdotal.

---

## 13. Coverage — PARTIAL

**Denominator = 2,678 keyed NSE equities from the master** (not the strategy universe).

| TF | Today | % | Any history | % |
|---|---|---|---|---|
| 1m | **0** | 0.0% | 2,557 | 95.5% |
| 5m | 994 | 37.1% | 2,186 | 81.6% |
| 15m | **0** | 0.0% | 2,100 | 78.4% |
| 1h | 1,004 | 37.5% | 2,187 | 81.7% |
| 1d | 1 | 0.0% | 2,650 | 99.0% |

Symbols with **no candles at all: 16 (0.6%)** — good.

**F9 — 1m and 15m produced nothing today.** 1m's last row was created
2026-09-03 11:54; its last bar is 2026-09-03 09:59 UTC. 5m and 1h have today's
data, so this is specific to those two pipelines.

---

## 14. Freshness — FAIL

The aggregate is reassuring and wrong.

| TF | Latest bar | Aggregate age | Verdict |
|---|---|---|---|
| 5m | 2026-09-04 09:10 | 13.5 min | "LIVE" |
| 1h | 2026-09-04 09:00 | 23.5 min | "LAGGING" |
| 1m | 2026-09-03 09:59 | 1,404 min | STALE |
| 15m | 2026-09-03 09:45 | 1,418 min | STALE |
| 1d | 2026-09-04 00:00 | 563 min | STALE |

**Per-symbol, hub universe (n = 1,632):**

| TF | p50 | p90 | p95 | p99 | max | Fresher than 15 min |
|---|---|---|---|---|---|---|
| 5m | **10,048 min** | 10,063 | 10,063 | 10,063 | 14,418 | **139 / 1,632 (8.5%)** |
| 1h | **10,058 min** | 10,058 | 10,118 | 10,118 | 14,438 | **0 / 1,632 (0%)** |

The "5m is 13.5 minutes fresh" headline reflects a handful of symbols. **The
median hub symbol's 5-minute data is 7 days old.** For 1h, *not one* symbol is
fresh.

---

## 15. Reconnect behaviour — FAIL

`auto_reconnect(True)` is enabled, but:

- `_on_open()` only sets `_CONNECTED = True` — it does **not re-subscribe**.
- The token is fixed at streamer construction, so reconnects reuse a credential
  that expires at 03:30 IST daily (**F4**).

Observed consequence: the 05:00 401 loop, feed down for the whole session.

Live reconnect testing (subscriptions restored, no duplicate ticks/candles, no
timestamp corruption): **UNPROVEN** — a feed that is already down cannot be
disconnected to test.

Positive: stale cache is **not** treated as live — the age guards hold.

---

## 16. Automated data-quality invariants

`tests/test_step2b_market_data_invariants.py` — 22 deterministic tests, runnable
at any hour, no live market required.

Covers: instrument identity · NSE-only filtering · timestamp normalisation ·
candle construction · candle boundaries · OHLC invariants · volume semantics ·
duplicate handling · future-timestamp rejection · BSE rejection · interval-alias
mapping.

Includes a shared `validate_candles()` function usable by audit scripts and
tests alike — **plus tests for the validator itself**, since a control nobody has
tested is not a control.

Re-runnable audit scripts:

- `audit/step2b_instrument_master.py` — master counts by segment/type, identity validation
- `audit/step2b_candles.py` — schema, ranges, hour distribution, invariants, duplicates, coverage

Both print counts, not verdicts.

---

## 17. Test results

| | Passed | Failed | Errors |
|---|---|---|---|
| **Baseline (before)** | 2,366 | 27 | 5 |
| **After** | **2,388** | **27** | 5 |

- **New tests:** 22, all passing
- **Unchanged failures:** 27 (the pre-existing baseline)
- **New failures:** **0**
- **Existing tests weakened:** none

---

## 18. Findings classified

### PASS
NSE-only enforcement (0 active BSE subscriptions) · REST source consistency
(0.000% across 3 tiers) · candle uniqueness (0 duplicates in 35.7M rows) · OHLC
invariants for equities · session-window bounds · volume semantics (established)
· live-candle-builder contract · bulk master identity

### PARTIAL
Persisted instrument identity (**F1** collision, **F2** unreliable type, **F3**
stale rows) · coverage (**F9**) · persistence (**F7** dead-series index writes) ·
numerical accuracy (**F6**)

### FAIL
**F4** feed down all session · **F8** date-label offset · **B1** two
irreconcilable series · corporate actions unadjusted · **F6** stored-vs-broker
divergence · per-symbol freshness · reconnect

### UNPROVEN — requires a live market session with a working feed
Tick quality metrics · REST-vs-WebSocket agreement · live candle construction ·
live reconnect behaviour · duplicate-tick and out-of-order-tick handling

---

## 19. Blockers

| # | Blocker | Why it blocks |
|---|---|---|
| 1 | Two irreconcilable daily series | No valid multi-year daily dataset can be built; 43% of hub symbols disagree by >20% |
| 2 | One-day date-label offset | Silently mislabels every point-in-time join, and looks correct |
| 3 | WebSocket dies at daily token rotation | No live tick data on any session; sub-second exits unavailable |
| 4 | Stored bars are pre-settlement | 100% one-sided volume bias; features systematically understated |
| 5 | 1m/15m produced nothing today | Two of five timeframes have no current data |

---

## 20. Exact next step

**Fix #2 (the date offset) first.** It is the smallest change and the most
dangerous defect, and every dataset built later inherits it.

**Then #1**, which is a decision as much as a fix: whether usable daily history
begins in 2016 (requiring back-adjustment of the dead series) or in June 2026
(discarding it). That choice determines what research is even possible.

Nothing has been fixed. This audit changed no production code.

---

*Compiled 2026-09-04 from live measurement against the production Upstox account
and database. Connectivity, schema, identity, temporal, numerical, completeness,
persistence and freshness are reported separately and deliberately — an HTTP 200
and a connected socket prove none of the others.*
