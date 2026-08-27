# STEP 1A — AFTER-MARKET INFORMATION AVAILABILITY AUDIT

**2026-08-27, 14:37–14:52 IST. Read-only.** No code, config, service or row changed.
Every verdict below is measured against the live database and source, not documentation.

---

## THE SCORECARD

| # | Data | Verdict | One-line evidence |
|---|---|---|---|
| 1 | NSE corporate announcements | 🟡 **PARTIAL** | Fetch works (203 today, 33 high-impact); **only 3 reached the DB** |
| 2 | Results / earnings | ✅ **YES** | 2,324-row earnings calendar + 397 FINANCIAL_RESULTS + 4,371 EARNINGS events |
| 3 | Order wins | ✅ **YES** | 139 NSE filings mapped → 788 ORDER_WIN events; NSE category beats our LLM label |
| 4 | Board meeting / outcome | ✅ **YES** | 2,134 filings — largest single category; dedicated sub-type classifier |
| 5 | Ratings / credit events | ✅ **YES** | 261 filings across 4 NSE rating categories → 122 CREDIT_RATING events |
| 6 | Corporate actions | 🟡 **PARTIAL** | **Reactive only** — detects splits from a >40% price drop on positions we already hold |
| 7 | News headlines | ✅ **YES** | 38,215 rows, 14+ sources, 8 live today |
| 8 | Announcement timestamp | ✅ **YES** | NSE `an_dt` parsed IST→UTC; a 4,159-row tz bug was fixed 2026-08-17 |
| 9 | Event classification | ✅ **YES** | 40+ canonical types; NSE's own category overrides the LLM, with measured justification |
| 10 | Stock → NSE symbol mapping | ✅ **YES** | 10,270 NSE instruments + canonical normaliser (built today) |
| 11 | **Event → stock linkage** | ❌ **BROKEN** | **49.5% of event symbols do not resolve to any NSE instrument** |
| 12 | Historical storage | 🟡 **PARTIAL** | Stored and queryable, but daily volume fell ~75% since 2026-08-14 |
| 13 | After-market cutoff / date separation | ✅ **YES** | Strict `is_nse_market_open()` gate; 97.9% of queued news is genuinely post-close |

**7 YES · 4 PARTIAL · 1 BROKEN · 1 partial-critical.**

The headline finding: **the raw data is almost all there. Two conversion steps
lose it.**

---

## 1. NSE CORPORATE ANNOUNCEMENTS — 🟡 PARTIAL

**The fetch is fixed and working.** Verified live at 14:40:

```
NSE_ANNOUNCEMENT_POLL date_scope=27-08-2026 date_scoped=True
  nse_total=203  nse_high_impact=33  returned=33  truncated_by_limit=0
```

**But conversion is broken:**

```
crawler returns          33 high-impact
in-process queue         33/200  (depth static across polls 62, 63, 64…)
news_items today          3
```

The queue is filling and not draining. `new=0 dup=33` on every poll means the
poller correctly recognises it has already enqueued these — the consumer is
simply not taking them off. Root cause **not established**; it needs the
consumer path traced, which I am not doing read-only.

**Stored history: 4,683 rows.** Categories actually captured:

| NSE category | rows |
|---|---:|
| Outcome of Board Meeting | 2,134 |
| Press Release | 1,078 |
| Resignation of Director/KMP/SMP | 303 |
| Resignation | 190 |
| Acquisition | 165 |
| Credit Rating (4 variants) | 261 |
| Bagging/Receiving of orders | 118 |
| Awarding of order(s)/contract(s) | 21 |
| Dividend | 71 |
| Financial results (clarifications) | 88 |

The filter accepts 40+ keyword families. **Coverage of event *types* is good;
coverage of event *volume* is not.**

---

## 2. RESULTS / EARNINGS — ✅ YES

Three independent layers:

| Layer | Where | Rows |
|---|---|---:|
| Forward calendar | `market_events` type=EARNINGS | **2,324** (102 future, 2,222 past) |
| Filed results | `news_items` NSE categories | 88 clarification/results filings |
| Classified events | `causal_events` | 4,371 EARNINGS · 409 EARNINGS_SURPRISE · 397 FINANCIAL_RESULTS · 205 EARNINGS_BEAT |

Calendar window **2026-04-21 → 2026-11-20**, 100% carry a `.NS` symbol, 100%
`is_confirmed`, source YFINANCE.

**Gap:** `time_ist` is **NULL on all 2,324 rows.** We know the *date* a company
reports but never the *time*. For an after-market strategy that is the decisive
field — it separates "reports before open" from "reports after close."

Transcripts exist too (`earnings_call_summaries`, 7 rows; NSE/BSE/Trendlyne
fetchers), but that table is effectively empty.

---

## 3. ORDER WINS — ✅ YES, and this is the system's best-measured edge

139 NSE filings across two categories map to a canonical `ORDER_WIN`; 788 such
events stored.

`engine/event_classifier.py` records a measurement that matters, over 4,309
announcements:

```
ORDER_WIN, NSE's own category   n=84    mean excess +1.053%   win 65.5%
ORDER_WIN, our LLM classifier   n=158   mean excess -0.245%   win 37.3%
```

**The exchange's label is profitable; our model's label on the same event type
is not.** The code already defers to NSE's category for direction and keeps the
LLM only for sectors, entities and horizon. That is the correct design and it is
already in place.

---

## 4. BOARD MEETING / OUTCOME — ✅ YES

**2,134 filings — the single largest category.** A dedicated
`engine/board_meeting_classifier.py` breaks "Financial Results" board meetings
into actionable sub-types.

**Important nuance already encoded:** `Outcome of Board Meeting` sits in the
**NEUTRAL** set, with the reason recorded in code — measured mean excess
**−0.737%, 36.3% win over 1,169 observations.** The label says results *were
declared*, not whether they *beat*. Acting on it loses money, so it produces no
directional event.

**No forward-looking board-meeting calendar exists** (i.e. "who meets next
Tuesday"). We see outcomes, not schedules.

---

## 5. RATINGS / CREDIT EVENTS — ✅ YES

261 filings across `Credit Rating` (159), `Credit Rating- Revision` (47),
`Credit Rating- New` (40), `Credit Rating- Others` (15) → **122 CREDIT_RATING**
canonical events.

Adjacent negatives also captured: 517 resignation filings (director/KMP/auditor)
mapping to `MANAGEMENT_RESIGNATION` / `AUDITOR_RESIGNATION`, plus
`DELAYED_FILING` and `REGULATORY_ACTION`.

---

## 6. CORPORATE ACTIONS — 🟡 PARTIAL, and the gap is structural

**What exists** (`crawler/corporate_actions.py`) is a *position-protection*
mechanism, not a data feed. Its own flow:

```
1. for each OPEN POSITION, compare yesterday's close to today's first tick
2. if price dropped >40%  -> suspect split/bonus
3. fetch Tavily news to confirm
4. adjust units x ratio, entry/stop/target / ratio
```

Three consequences, stated plainly:

- **It only sees stocks we already hold.** A split in a stock we do not own is
  invisible.
- **It is reactive, not forward-looking.** There is no ex-date calendar. We
  learn about a split by being surprised by the price.
- **It cannot see actions below the 40% threshold** — most dividends, small
  bonuses, rights issues.

Dividend/bonus/split *announcements* do arrive as NSE filings (71 dividend rows),
so the **news** is captured. The **ex-date calendar** is not.

---

## 7. NEWS HEADLINES — ✅ YES

**38,215 rows**, 14+ sources. Live today: mint (9,368 total), Economic Times
(3,728), CNBC TV18 (2,216), Reuters (1,843), Share Market Today, Zee Business,
Bloomberg, plus RBI (378) and PIB (75).

Two sources are stale — **Markets (10,912 rows) last updated 2026-08-18**, and
RBI/PIB carry `published_at = NULL` entirely.

---

## 8. ANNOUNCEMENT TIMESTAMP — ✅ YES

NSE's `an_dt` (`'14-Jul-2026 20:10:07'`, IST) is parsed to naive UTC by
`_parse_nse_announcement_dt`, matching the convention of every other source.

A real bug here was already found and fixed on 2026-08-17: the parser returned
IST wall-clock into a UTC column, putting **4,159 rows (15.3% of all
`news_items` with a `published_at`) 5h30m in the future** relative to their own
`crawled_at`. Verified today on a live row:

```
published 2026-08-27 03:22:32 UTC (08:52:32 IST)
crawled   2026-08-27 03:24:14 UTC   -> 1m42s latency, ordering correct
```

**⚠️ But conventions are NOT uniform across tables** — see §13.

---

## 9. EVENT CLASSIFICATION — ✅ YES

40+ canonical types in `causal_events.event_title`. Top of the distribution:

```
EARNINGS 4,371 · ORDER_WIN 788 · EARNINGS_SURPRISE 409 · FINANCIAL_RESULTS 397
M&A 258 · PRODUCT_LAUNCH 232 · GOVERNANCE_UPDATE 220 · EARNINGS_BEAT 205
CAPACITY_EXPANSION 190 · REGULATORY_APPROVAL 134 · CREDIT_RATING 122
```

**Schema correction worth recording:** `causal_events` has **no `category`
column and no `ticker` column.** Earlier phase notes assumed both. The real
columns are:

```
id, news_id, event_title, country, importance, confidence,
affected_sectors, affected_indices, bullish_stocks, bearish_stocks,
duration, created_at
```

`event_title` is the de-facto type field; `bullish_stocks` / `bearish_stocks`
carry the linkage. Any query written against `category` or `ticker` fails
outright.

---

## 10. STOCK → NSE SYMBOL MAPPING — ✅ YES (infrastructure)

`kite_instruments` now holds **10,270 NSE rows** (BSE purged today). The
canonical normaliser `utils/symbols.py` — built earlier today — is suffix-safe,
idempotent, and fails open. 34 tests.

**The lookup table is sound. What feeds it is not — see §11.**

---

## 11. EVENT → STOCK LINKAGE — ❌ BROKEN

**This is the most serious finding in the audit.**

Over the last 7 days of events, 220 distinct symbols appear in
`bullish_stocks` / `bearish_stocks`:

```
resolvable to an NSE instrument :  111  (50.5%)
UNRESOLVABLE                    :  109  (49.5%)
```

**Every one of the unresolvable ones is a real, liquid NSE stock** — the
identifier is simply wrong:

| Event says | NSE actually uses |
|---|---|
| `ADANITOTALGAS` | **ATGL** |
| `ADITYABIRLACAPITAL` | **ABCAPITAL** |
| `BANK OF MAHARASHTRA` | **MAHABANK** |
| `BHARAT ELECTRONICS LIMITED` | **BEL** |
| `BALRAMPUR CHINI` | **BALRAMCHIN** |
| `DATA PATTERNS` | **DATAPATTNS** |
| `CSB BANK` | **CSBBANK** |
| `CAPRI GLOBAL CAPITAL` | **CGCL** |

Two distinct failure modes:
1. **Company names instead of tickers** — `"BANK OF MAHARASHTRA"`, with spaces.
2. **Plausible-but-wrong tickers** — `ADANITOTALGAS` looks like a symbol and is
   not one.

Additionally, **0 of 506 symbols carried a `.NS` suffix.** They are bare, so
every consumer must normalise before use.

**Why this matters more than it looks:** an event that cannot be resolved to a
tradeable symbol is an event that cannot become a trade — no candles, no price,
no validation. The classification was correct and the information is simply
dropped on the floor. **Roughly half our correctly-classified events are
unreachable.**

`utils/symbols.py` cannot fix this: it repairs *suffixes*, not *identities*.
A name→ticker resolver (fuzzy match against `kite_instruments.name`) does not
exist.

---

## 12. HISTORICAL STORAGE — 🟡 PARTIAL

Everything is persisted and queryable: `news_items` 38,215 · `causal_events`
12,443 · `market_events` 2,971 · `premarket_news_queue` 7,751 ·
`sse_announcements` 85 · `earnings_call_summaries` 7.

**No trading day in the last 21 has zero announcements.** But volume regressed
sharply:

```
2026-08-11 Tue   377      2026-08-19 Wed    62
2026-08-12 Wed   355      2026-08-20 Thu    92
2026-08-13 Thu   393      2026-08-21 Fri     5   <-- LOW
2026-08-14 Fri   361      2026-08-24 Mon    10   <-- LOW
      ~370/day             2026-08-25 Tue    88
                           2026-08-26 Wed    85
                           2026-08-27 Thu     3   <-- today
```

**A ~75% drop after 2026-08-14**, with three near-zero days. Whatever changed
mid-August is *still* suppressing storage, and today's `33 fetched → 3 stored`
is the same wound. **Cause not established** — I am flagging the pattern, not
diagnosing it read-only.

`premarket_news_queue` also holds **2,580 PENDING rows** dating to 2026-08-14,
outside the 3-day drain cutoff. They are frozen, not processed, not deleted.

---

## 13. AFTER-MARKET CUTOFF / DATE SEPARATION — ✅ YES

**This works, and it works for a documented reason.**

`news_discovery_engine.py:1645` uses the strict `is_nse_market_open()`
(09:15–15:30 IST), *not* the extended `_is_india_trading_window()` (which runs
to 16:00 for position management). The code records why: the extended flag once
let **SHAKTIPUMP.BO open a live position at 15:51 IST**, after the real close.

Behaviour: news arriving while the market is open is processed live; news
arriving after close goes to `premarket_news_queue` as PENDING and is drained at
the next open, bounded to 3 days old.

**Measured, correctly timezone-converted:**

```
capture hour (IST)   16:00  1,297   17:00  1,592   18:00  1,407
                     19:00    947   20:00    841   21:00    314

captured DURING market hours: 161 / 7,751  (2.1%)
```

**97.9% of queued news is genuinely after-market.** The separation is real.

### ⚠️ A timezone trap worth recording

Conventions are **not uniform**:

| Table | Column | Type |
|---|---|---|
| `news_items` | `published_at`, `crawled_at` | `timestamp` (naive UTC) |
| `causal_events` | `created_at` | `timestamp` (naive UTC) |
| `market_events` | `created_at` | `timestamp` (naive UTC) |
| **`premarket_news_queue`** | **`captured_at`, `processed_at`** | **`timestamptz`** |

**I hit this during the audit.** Applying the codebase's usual
`AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata'` to the tz-aware column
double-converts and shifts everything 5h30m — it made after-market news look
like pre-market news. The correct form for `premarket_news_queue` is a single
`AT TIME ZONE 'Asia/Kolkata'`. **Any future query joining these tables on time
will be wrong unless it accounts for the mixed convention.**

---

## WHAT THIS MEANS

**The data is there. The plumbing between collection and use is where it dies.**

```
NSE publishes           203 filings today
   |
we fetch                 33 high-impact          ✅ works
   |
in-process queue         33 enqueued, depth static  ❌ CONVERSION BLOCKED
   |
news_items                3 stored                ❌ 91% lost here
   |
classified               correct types            ✅ works
   |
linked to a stock        ~50% resolvable          ❌ HALF LOST HERE
   |
tradeable                 what's left
```

Two chokepoints, both **engineering**, neither **strategy**:

1. **The NSE queue is not draining** (§1, §12) — 33 fetched → 3 stored.
2. **Half of event symbols are unresolvable** (§11) — correct events, wrong
   identifiers, no name→ticker resolver.

Fixing either does not require touching a threshold, a model, a prompt or a risk
limit.

---

## THREE GAPS THAT LIMIT AN AFTER-MARKET STRATEGY SPECIFICALLY

1. **`market_events.time_ist` is NULL on all 2,324 earnings rows.** We know the
   date, never the time. "Reports after close" is unanswerable from our data.
2. **No ex-date / corporate-action calendar.** Splits are discovered by being
   surprised by a >40% price drop, and only on stocks we already hold.
3. **No forward board-meeting calendar.** We see outcomes, never schedules.

---

## CONFIDENCE AND LIMITS

**HIGH confidence:** every count is a direct query run today; the 49.5%
resolution failure was verified by looking up each unresolvable name in
`kite_instruments` and finding the real ticker.

**Stated limits:**
- The 7-day symbol-resolution sample is 220 symbols. Directionally solid, not a
  full census.
- I did **not** establish *why* the NSE queue is not draining, or *why* daily
  volume fell after 2026-08-14. Both need the consumer path traced.
- `sse_announcements` (85 rows) was inspected only shallowly; it holds SME/trust
  filings with frequently NULL symbols.
- Nothing was changed. Every finding is reversible because nothing was done.
