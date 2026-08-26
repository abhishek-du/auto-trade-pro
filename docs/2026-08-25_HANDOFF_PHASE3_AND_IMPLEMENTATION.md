# AutoTrade Pro — Phase 3 study + everything implemented after it

**Handoff brief.** Written 2026-08-25. Covers the Phase 3 ground-truth news-alpha
study and the three production changes that followed it.

System: Indian equity (cash-segment) algo-trading system. FastAPI + Celery +
Postgres + Redis, Zerodha Kite + Upstox for market data, AWS Bedrock for the LLM
agent. **PAPER MODE — no real money.** Capital modelled at ₹5,00,000.

---

# PART 1 — PHASE 3: THE GROUND-TRUTH STUDY

## 1.1 Why it was run

Phase 2 had measured our own `causal_events` stream over 2,167 independent
observations and found **no directional reaction at any horizon 1m→EOD**,
benchmark-adjusted, across 22 sessions. Directional accuracy was 41.9% at EOD
against a 45.0% always-LONG base rate.

That verdict could not be generalised, because the experiment was contaminated
by our own event generation, our own classifier, our own detection timestamp,
and an event set dominated by non-events (generic `EARNINGS` was 730 of 2,167).

Phase 3 therefore had to test **real news** using a source we do not produce.

## 1.2 Ground truth construction

Source: `news_items WHERE source = 'NSE-Announcements'` — 4,497 rows,
14 Jul – 21 Aug 2026.

Two fields make this independent of our AI:

- **`published_at`** comes from NSE's own `an_dt` field
  (`crawler/news_crawler.py:435` → `_parse_nse_announcement_dt`), parsed from
  IST and stored as naive UTC. It is the **exchange's publication timestamp** —
  not our crawl time, not our DB insert time.
- **`category`** is NSE's own classification of the filing.

`causal_events` was not read anywhere in the study.

**Timestamp integrity verified:** 0 of 4,497 rows have `published_at >
crawled_at`. A documented 5h30m parser bug (fixed 2026-08-17, where IST
wall-clock was stored in a UTC column) left no residue.

### Deterministic taxonomy — no model involved

| Direction | NSE categories |
|---|---|
| **LONG** | Bagging/Receiving of orders, Awarding of order(s), Acquisition, Amalgamation/Merger, Buyback, Product launch, Agreements, MoU |
| **SHORT** | Resignation of Director/KMP/SMP, Resignation, Resignation of Statutory Auditor, Delayed/Non-submission of Financials, Granting/withdrawal/cancellation |
| **NEUTRAL** | Outcome of Board Meeting, Press Release, Dividend, Clarifications, Monthly Business Updates, Rights/Preferential issue, Scheme of Arrangement, Demerger |

Credit-rating direction comes from regex on the announcement text
(`upgrad|revised upward|improve|positive outlook` vs
`downgrad|revised downward|negative outlook|default`), never from the bare
category — "Credit Rating" alone does not say which way.

Categories carrying no direction are labelled NEUTRAL and are **not forced onto
a side**. `Scheme of Arrangement` and `Demerger` sit in the neutral set (not
LONG) because CORPORATE_RESTRUCTURE measured significantly negative as a long.

### Funnel

| Stage | Kept | % | Dropped | Examples |
|---|---:|---:|---:|---|
| S0 NSE announcements | 4,497 | 100.0% | — | — |
| S1 Has publication timestamp | 4,497 | 100.0% | 0 | — |
| S2 Category in taxonomy | 4,451 | 99.0% | 46 | `None`, `Public Announcement - Buyback` |
| S3 Symbol resolvable | 4,309 | 95.8% | 142 | VIJIFIN.NS, CLCIND.NS, GBGLOBAL.NS |

**4,309 ground-truth events.**

| Window | LONG | SHORT | NEUTRAL |
|---|---:|---:|---:|
| INTRADAY (09:15–15:30 IST) | 79 | 58 | 673 |
| PREMARKET | 16 | 5 | 61 |
| **POSTMARKET** | **393** | **422** | **2,602** |

**79% of NSE announcements publish after the close.** This one fact shapes
everything.

## 1.3 Two studies

- **Study A — intraday.** T_public inside the session → 1m reaction curve.
  After price validation: 302 observations, **but only 21 LONG and 21 SHORT.**
  **Underpowered. Reported, not relied upon.**
- **Study B — overnight.** T_public after close → next session.
  **2,714 observations: 349 LONG / 300 SHORT / 2,065 NEUTRAL.** Primary study.

**Benchmark:** `NIFTYBEES.NS` 1m — an **ETF PROXY** for NIFTY 50. `^NSEI` has no
1m candles in this DB (5m only). The proxy carries tracking error, its own
bid-ask, and can trade at a premium/discount to NAV. Results are called
**benchmark-adjusted reaction**, not clean beta adjustment. Benchmark present on
22 of 22 sessions.

## 1.4 Primary result — Study B

Excess over NIFTYBEES, signed by the event's own direction. `*` = 95% bootstrap
CI (2,000 resamples, seed 7) excluding zero.

| Leg | Side | n | median | mean | win% | 95% CI | |
|---|---|---:|---:|---:|---:|---|---|
| GAP (prev close→open) | LONG | 349 | +0.181 | **+0.430** | 59.6% | [+0.197, +0.672] | `*` |
| GAP | SHORT | 300 | +0.086 | +0.142 | 53.0% | [−0.165, +0.472] | |
| GAP | NEUTRAL | 2,065 | +0.026 | +0.055 | 50.8% | [−0.099, +0.219] | |
| DAY (open→close) | LONG | 349 | −0.097 | **−0.051** | 47.0% | [−0.294, +0.190] | |
| DAY | SHORT | 300 | +0.150 | +0.218 | 55.7% | [−0.037, +0.501] | |
| DAY | NEUTRAL | 2,065 | −0.670 | −0.533 | 38.8% | [−0.701, −0.364] | `*` |
| TOTAL | LONG | 349 | +0.280 | **+0.368** | 55.3% | [+0.053, +0.713] | `*` |
| TOTAL | SHORT | 300 | +0.599 | +0.366 | 61.0% | [−0.065, +0.800] | |
| TOTAL | NEUTRAL | 2,065 | −0.735 | −0.496 | 39.1% | [−0.722, −0.286] | `*` |

**Material news DOES move stocks.** Phase 2's "no edge" was a property of our
pipeline, not of the market.

## 1.5 The finding that matters most

```
LONG    gap +0.430   after the open -0.051   total +0.368   -> gap is 117% of the move
SHORT   gap +0.142   after the open +0.218   total +0.366   -> gap is  39% of the move
```

For positive news published after the close, **the entire excess return is in
the opening print**, and the rest of the day gives a little back.

**You cannot buy at the previous close.** The earliest executable moment is the
open — by then the information is priced. This is the auction working, not a
latency defect any engineering can fix.

## 1.6 Cost adjustment

Round-trip assumption for a ₹50k ticket in a small/mid-cap:

| Component | % |
|---|---:|
| Brokerage (flat ₹20 both legs) | 0.030 |
| STT (sell side 0.025%) | 0.025 |
| Exchange transaction (0.00345% ×2) | 0.007 |
| GST 18% on brokerage + txn | 0.007 |
| SEBI turnover + stamp | 0.003 |
| Spread + slippage (conservative) | 0.150 |
| **Total round-trip** | **0.222** |

### The only executable strategy: buy at open, exit at close

| Strategy | n | gross | **net** | win% | 95% CI (gross) |
|---|---:|---:|---:|---:|---|
| LONG `ORDER_WIN` | 84 | +0.013 | **−0.209** | 48.8% | [−0.437, +0.470] |
| **LONG `RATING_UPGRADE`** | 59 | +0.569 | **+0.347** | 55.9% | **[+0.130, +1.003]** |
| SHORT `MANAGEMENT_RESIGNATION` | 258 | +0.238 | +0.016 | 48.1% | [−0.036, +0.544] |

**`ORDER_WIN` — the strongest ground-truth category on total return (+1.053%) —
is dead after costs** once the uncapturable gap is removed. Only
`RATING_UPGRADE` survives with a CI above zero, at n=59: a hypothesis to test
forward, not a strategy to size.

## 1.7 Category breakdown — excess TOTAL

| Side · Subcategory | n | median | mean | win% | 95% CI | |
|---|---:|---:|---:|---:|---|---|
| LONG · `ORDER_WIN` | 84 | +1.072 | **+1.053** | 65.5% | [+0.444, +1.687] | `*` |
| LONG · `RATING_UPGRADE` | 59 | +0.566 | **+0.898** | 61.0% | [+0.244, +1.736] | `*` |
| SHORT · `MANAGEMENT_RESIGNATION` | 258 | +0.633 | +0.438 | 62.8% | [−0.021, +0.921] | |
| LONG · `ACQUISITION` | 133 | +0.063 | +0.129 | 51.9% | [−0.452, +0.745] | |
| SHORT · `RATING_DOWNGRADE` | 29 | +0.025 | +0.166 | 51.7% | [−0.430, +0.701] | |
| LONG · `MAJOR_PARTNERSHIP` | 39 | −0.366 | −0.319 | 46.2% | [−0.938, +0.303] | |
| LONG · `CORPORATE_RESTRUCTURE` | 23 | −0.737 | **−0.975** | 39.1% | [−1.891, −0.136] | `*` |

Controls: `ROUTINE_BOARD_MEETING` n=1,169 mean **−0.737** win 36.3%;
`ROUTINE_DISCLOSURE` n=735 mean −0.299 win 41.9%.

Materiality tiers show **no monotonic gradient**: Tier 1 (n=149) +0.077,
Tier 2 (n=195) +0.635, Tier 3 (n=305) +0.338.

## 1.8 Control-group caveat — stated, not buried

```
median |gap|:   LONG 0.846   SHORT 0.973   NEUTRAL 1.677
```

**Neutral announcements move MORE in absolute terms than directional ones.**
`Outcome of Board Meeting` is NSE's results-declaration category — the
highest-information event on the calendar — and the deterministic rules label it
NEUTRAL because the category alone cannot say beat or miss.

Consequence: the "directional vs neutral" differences (+0.864 LONG, +0.862
SHORT, both CIs excluding zero) are **inflated by the control being unusually
negative**. The honest test is the absolute CI in §1.4, not the difference
against this control. On that test LONG TOTAL and LONG GAP are significant;
SHORT TOTAL is not.

## 1.9 Our classifier vs ground truth

Same category, two taxonomies:

| `ORDER_WIN` | n | mean excess | win rate |
|---|---:|---:|---:|
| **Ground truth (NSE category)** | 84 | **+1.053%** | **65.5%** |
| **Our `causal_events` classifier** (Phase 2) | 158 | **−0.245%** | **37.3%** |

The strongest bullish category under the exchange's labels is the **second-worst**
under ours.

**Detection is not the bottleneck.** Our lag against NSE publication is a
**1.4-minute median, 6.0 at p90**. (The earlier "6.7 min" figure was across all
news sources; for NSE filings specifically we are fast.)

## 1.10 Study A — intraday (underpowered)

n = 21 LONG, 21 SHORT, 260 NEUTRAL. No conclusion drawn.

| | n | median | mean | win% | 95% CI | |
|---|---:|---:|---:|---:|---|---|
| LONG EOD excess | 21 | −0.357 | −0.337 | 28.6% | [−0.878, +0.251] | |
| SHORT +60m excess | 21 | +0.247 | +0.279 | 71.4% | [+0.055, +0.519] | `*` |
| NEUTRAL EOD excess | 258 | −0.193 | −0.423 | 40.3% | [−0.793, −0.030] | `*` |

Pre-event drift on the 42 directional intraday events: −15m median **+0.195%**,
**69% already moving in the event's direction before publication.** A
leakage/anticipation flag worth its own study; at n=42 it is not a finding.

## 1.11 Look-ahead audit

| Risk | Control |
|---|---|
| Hindsight labels | Direction from a fixed category table written before any return was computed |
| Detection-time contamination | `T_public` = NSE's `an_dt`; 0/4,497 rows have publication after crawl |
| Future prices in entry | Study B entry = next session's **first** 1m bar; Study A = last bar at/before T_public |
| Benchmark leakage | Benchmark windows identical to the stock's |
| Outcome-dependent filtering | Every exclusion defined on availability, never on return; counts published |
| Survivorship | Symbols resolved against full `kite_instruments`, not a still-trading list |
| Category cherry-picking | Full taxonomy published; `CORPORATE_RESTRUCTURE` came out significantly **negative** and is reported |

## 1.12 VERDICT

### **B — CONDITIONAL NEWS ALPHA**

Directional reaction is real and significant for LONG events. The capturable
fraction is small: the gap is 117% of the LONG move, and after costs only
`RATING_UPGRADE` retains a CI above zero (net +0.347%, n=59).

### Secondary: why AutoTrade Pro fails to capture it

| Candidate | Evidence |
|---|---|
| A — AI misses events | **NO.** Detection median 1.4 min, p90 6.0 min |
| **B — AI interprets incorrectly** | **YES.** ORDER_WIN +1.053%/65.5% ground truth vs −0.245%/37.3% ours |
| C — latency kills capture | **PARTLY, structurally.** Not our latency — the gap is unreachable by anyone |
| D — technical/risk gates | Established in Phase 1, unaffected by this study |
| E — no real alpha | **REFUTED** |
| F — alpha only in specific categories | **YES, sharpest form of the answer** |

---

# PART 2 — WHAT WAS IMPLEMENTED AFTER PHASE 3

Three commits, all on branch `fix/audit-2026-08-19-critical`, all pushed.
**No production strategy was redesigned.** Every change is traceable to a
measurement in Part 1.

---

## 2.1 Commit `1e7be02` — NSE category decides direction in `classify_event`

### What changed

**`engine/event_classifier.py`**
- New tables `_NSE_LONG`, `_NSE_SHORT`, `_NSE_NEUTRAL` — the exact taxonomy
  validated in Part 1.
- New `_EXCHANGE_FILING_SOURCES = {"NSE-Announcements"}`.
- New `resolve_nse_direction(nse_category, text) -> ("LONG"|"SHORT"|"NEUTRAL", category) | None`.
  `None` means "no exchange opinion" → keep whatever the LLM said.
- `classify_event()` gains `nse_category` and `source` params. When the source
  is an exchange filing:
  - LONG/SHORT → overrides `bullish` and `category`, sets `source_reliability=1.0`
  - NEUTRAL → **returns `None`** — no directional event at all
  - unmapped → LLM answer untouched

**Ordering decision:** the override is applied **before** the FinBERT
contradiction guard and short-circuits it. That guard exists to catch a model
whose direction nothing corroborates; NSE's filing category is not the model's
opinion, so letting a sentiment score veto it would reinstate the label measured
as worse.

**Source gate placement:** the `source in _EXCHANGE_FILING_SOURCES` check lives
**inside `classify_event`**, not at the call site, so a future caller cannot
hand an RSS feed's `category` field to a table built for exchange filings.

**`crawler/news_crawler.py`** — a second, separate defect:

Eligibility for classification required `|FinBERT| > 0.6`, a threshold for news
prose. NSE writes in dry legalese. Measured over 14 days:

| source | rows | median &#124;sentiment&#124; | >0.6 |
|---|---:|---:|---:|
| Markets (wire copy) | 2,860 | 0.911 | 74.4% |
| Share Market Today | 3,195 | 0.823 | 66.0% |
| **NSE-Announcements** | **2,100** | **0.000** | **8.7%** |

**Result: 2,100 NSE announcements in 14 days, ZERO classified.** The 8.7% that
cleared then lost the top-15 cap ranking to that same wire copy.

New `_filing_with_direction(i)` admits a filing on the strength of a
**directional** exchange category, not its emotional register. Filings rank
ahead of prose. Neutral categories are excluded from that path too (they would
be suppressed downstream anyway), so this spends **fewer** LLM round-trips:
393 directional admitted, 1,682 neutral skipped, ~0.10 extra items per crawl
against a cap of 15.

### Testing
25 tests in `tests/test_nse_category_direction.py`. **Nine mutations applied,
nine now fail.** Two initially PASSED and exposed real gaps:
- caller dropping `source=`/`nse_category=` — every direct-call test kept
  passing while production silently stopped applying the override
- reverting the eligibility gate

Both are now covered by **AST assertions** on the call site and on
`_filing_with_direction`, so a comment or docstring cannot satisfy them.

### ⚠️ CRITICAL CAVEAT — this commit is INERT for NSE announcements

**NSE announcements do not flow through `crawler/news_crawler.py::run_news_crawl`.**
They flow through `news_discovery_engine.py`, which never calls
`classify_event()`. This was discovered only after deploying and watching the
logs.

The commit is **safe but does not act on NSE filings**. It remains valid for
every other source. Commits 2.2 and 2.3 fix the path that actually matters.

---

## 2.2 Commit `53fd0fa` — a duplicate RSS headline was starving the NSE feed

### Root cause

`run_news_discovery_loop()` runs two sections per cycle: RSS (section 1), then
NSE corporate announcements (section 2). **Both sat inside one `try/except`**
whose handler logs and `sleep(15)`s to the next cycle — so anything raised in
section 1 skipped section 2 entirely.

`uq_news_items_headline_day` is a unique index on `(md5(headline), date)`. RSS
feeds re-serve the same story every cycle, so the ORM insert raised
`UniqueViolationError` on the first repeat — **every 15 seconds**.

**Measured consequence: NSE corporate announcements stopped being ingested after
2026-08-21 03:29 and produced nothing for three days**, while the loop looked
healthy and logged its RSS fetches normally.

The exchange feed was never the problem — called directly it returned that day's
filings immediately (3/20 high-impact, seq_ids present).

### Two fixes (one alone would be cosmetic)

1. **`INSERT ... ON CONFLICT DO NOTHING`** replaces the ORM adds at **both**
   insert sites. `crawler/news_crawler.py` was given this on 2026-08-20; the
   engine's own inserts were missed. The announcement insert matters more than
   it looks: a violation there aborts the section **after** the PDF has been
   downloaded, OCR'd and sent to the LLM, and **before** seq_ids are marked
   processed — so the next cycle re-fetches the same filings and repeats the
   whole cost indefinitely.
2. **RSS article handling gets its own `try/except`.** Fixing only the duplicate
   would leave the next unexpected error free to starve the filing path the same
   way. This failure mode has already cost three days of silence once.

### Live verification (not inferred)

```
BEFORE: zero "corporate-announcements" lines in the engine log; no NSE row since 21 Aug

AFTER restart:
  23:16:18  [news] NSE corporate-announcements: 3/20 high-impact
            Found 3 new high-impact NSE corporate announcements
            Analyzing: Rategain ... Resignation of Director/KMP/SMP
            Analyzing: Siyaram Silk Mills ... Credit Rating
  NSE news_items written in last 30 min: 3
  "Error in News Loop": none since restart
```

### Testing
AST assertions on the loop's structure (behaviour needs a live feed + populated
DB). **Three mutations, all caught.** The first test also caught a **second**
`session.add(NewsItem(...))` — the announcement insert — that the first pass of
this fix had missed.

---

## 2.3 Commit `6b1234e` — NSE category decides whether an announcement is tradable

### What was wrong

Direction for NSE announcements came from a keyword scan that **defaults to BUY**:

```python
side = "SELL" if any(w in text for w in _ANNOUNCEMENT_BEARISH_KEYWORDS) else "BUY"
# _ANNOUNCEMENT_BEARISH_KEYWORDS = ("resign","downgrade","default","loss",
#                                   "decline","disqualif","suspend")
```

### The replay changed the framing

Replayed over 4,500 historical announcements against `resolve_nse_direction`:

```
BUY  -> SKIP    3222
SELL -> SELL     511
BUY  -> BUY      430
SELL -> SKIP     282
BUY  -> keep      43
SELL -> BUY        7   <- flipped
SELL -> keep       3
BUY  -> SELL       2   <- flipped

directions flipped :    9  (0.2%)
no longer traded   : 3504 (77.9%)
```

**The scan was nearly right about direction.** The damage was the *default*:
3,504 announcements (77.9%) come from categories the exchange files as routine
and that carry no direction, and the scan made every one a BUY candidate. Those
are dominated by `Outcome of Board Meeting` — measured at **−0.737% mean excess,
36.3% win rate, n=1,169**.

**So the value of this wiring is suppression, not direction correction.**

### The change

- NEUTRAL category → `continue` (skip the announcement entirely)
- LONG/SHORT → `side = BUY/SELL`
- unmapped category → keyword scan survives **as the fallback only** (dropping
  those would silently lose genuine filings; guessing a direction would be the
  behaviour being removed)

### Live before/after — same table, same night

```
17:46 UTC (before)   SIYSIL.NS    BUY    Credit Rating- New
                     SIYSIL.NS    BUY    Credit Rating
                     RATEGAIN.NS  SELL   Resignation of Director/KMP/SMP

18:01, 18:07 (after) RATEGAIN.NS  SELL   (only)
                     "NSE category 'Credit Rating' carries no direction
                      — not a trade candidate: SIYSIL.NS"

SIYSIL rows queued in the 12 minutes after the change: 0
```

Neither SIYSIL filing said upgrade or downgrade, so the text gives no direction
and the category alone cannot supply one. Under the old rule both became BUY.
RATEGAIN's resignation resolves SELL under both rules — expected agreement.

### Testing
Three mutations. **The third initially PASSED** — the fallback test looked for
`"_res is not None"` near the keyword scan, which the NEUTRAL guard above it
already satisfies, so it would have passed with the exact regression it was
written to catch. It now asserts on the `side` assignment itself and on the scan
sitting in the `else` branch of a test on `_res`.

---

## 2.4 Test and deployment status

- **Full suite: 27 failed / 5 errors after every change — byte-identical to the
  pre-change baseline.** Zero regressions. (All 27 are pre-existing: order-book
  confirmation, pre-event-gap phases, upstox ISIN single-flight, trade-simulator
  confirmation-lost.)
- All services restarted and verified: `celery-worker`, `celery-beat`,
  `celery-scan-worker`, `celery-exit-worker`, `news-engine`, `uvicorn`.
- Branch `fix/audit-2026-08-19-critical`, pushed.

---

# PART 3 — WHAT IS STILL OPEN

1. **The intraday capture problem is untouched.** 79% of NSE announcements
   publish post-close and their information is in the opening print. These
   changes stop the system trading noise; they do not create an intraday edge.
2. **`RATING_UPGRADE` is the only prospective candidate** (n=59, net +0.347%,
   CI [+0.130, +1.003]). Paper-trade forward before sizing. **Do not fit
   anything else to this dataset.**
3. **Pre-event drift needs its own study.** 69% of 42 intraday directional
   events were already moving before publication.
4. **Commit `1e7be02` remains inert for NSE.** Either route NSE announcements
   through `classify_event`, or accept that `resolve_nse_direction` is applied
   twice from two call sites.
5. **Phase 1 remediation is still outstanding** — the `+1.5%` entry-confirmation
   gate (measured monotonically harmful across 14 sessions), stop sizing
   (median stop 1.58% against median +0.35% favourable excursion), and the
   Master Intelligence Hub having no trade authority (29 cycles, 42,835 scores,
   `decisions_made = 0`).

## What NOT to build

- Intraday strategies on post-close announcements.
- Anything fitted to `ORDER_WIN`'s +1.053% — that number is not executable.
- A larger LLM for event classification while NSE's own field outperforms it.

---

# PART 4 — ARCHITECTURAL FACT WORTH KNOWING

News reaches `news_items` through **two independent paths** that share nothing
but the table:

1. `tasks.news_scan` → `crawler/news_crawler.py::run_news_crawl` (celery, 5 min)
   — RSS, NewsAPI, Finnhub, yfinance. Direction via `classify_event()`.
2. `news_discovery_engine.py::run_news_discovery_loop` (own service, 15s)
   — RSS **and** NSE corporate announcements. Announcement direction never
   touched `classify_event()`.

**NSE announcements only ever use path 2.** Reading `news_items` tells you
nothing about which writer produced a row. Both paths also needed the same
duplicate-key fix, applied 4 days apart, and the gap between them is what killed
NSE ingestion for three days.

---

## Reference documents in the repo

| Doc | Contents |
|---|---|
| `docs/2026-08-24_FORENSIC_MISSED_OPPORTUNITIES.md` | Phase 1 — why 1 of 107 movers was traded on 24 Aug |
| `docs/2026-08-24_PHASE2_EVENT_REACTION_STUDY.md` | Phase 2 — our own event stream has no reaction |
| `docs/2026-08-24_PHASE3_GROUND_TRUTH_NEWS_ALPHA.md` | Phase 3 — this study, in full |
| `autotrade-backend/scripts/research/` | All 13 analysis scripts + README |

*Systems and statistics analysis. Not investment advice.*
