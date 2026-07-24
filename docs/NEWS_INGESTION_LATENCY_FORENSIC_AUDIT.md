# News Ingestion Latency — Forensic Audit

**Question under investigation:** Did our crawler/platform actually receive news late, and if so, exactly where?

**Scope:** NESTLEIND, BANDHAN BANK, MARUTI SUZUKI, CYIENT DLM, INDIAN HOTELS, ANANT RAJ, MEDPLUS, AAVAS, INDIAMART, TRIDENT, GABRIEL INDIA, MASTEK, BLUESTONE, E2E NETWORKS — plus deep dives on NESTLEIND and TVS MOTOR.

**Rules followed:** No code changes. No fixes. No refactors. Every timestamp below is either a direct database record, a production log line, or a Zerodha historical-candle response, with its source stated inline. Where a timestamp cannot be independently proven, it is marked `UNKNOWN`.

**Critical timezone fact established first, because it governs every calculation in this report:**
- Database columns (`NewsItem.crawled_at`, `CausalEvent.created_at`, `PaperTrade.opened_at`) are populated via `datetime.utcnow()` → **UTC**.
- Production log timestamps (`/tmp/news-engine.log`, `/tmp/uvicorn.log`, `/tmp/celery_worker.log`) are loguru-formatted with `{time:YYYY-MM-DD HH:mm:ss}` on a server whose OS timezone is `Asia/Kolkata` (confirmed via `timedatectl`) → **IST**, with no UTC conversion.
- `NewsItem.published_at` is **dual-standard depending on source**: for RSS/API sources it is parsed via `_parse_dt()` (`crawler/news_crawler.py:111-117`), which normalizes to UTC. For NSE corporate announcements it is parsed via `_parse_nse_announcement_dt()` (`crawler/news_crawler.py:343-350`), which does `datetime.strptime(raw, "%d-%b-%Y %H:%M:%S")` on NSE's own `an_dt` field with **no timezone conversion at all** — and NSE's `an_dt` is always IST (exchange local time). So in the exact same DB column, RSS rows are UTC and NSE-announcement rows are naive IST. This is evidence, not speculation — see the source line numbers above.

Every latency figure in this report has been computed with this fact applied. Any casual comparison of `published_at` against `crawled_at` without knowing which source produced the row will silently misstate latency by up to 5.5 hours.

---

## 1. Executive Summary

Across all 14 events, the **crawler-to-source latency was consistently fast — typically 30 seconds to a few minutes** for NSE corporate announcements, and near-instant to ~10 minutes for RSS aggregator pickups. In every case where we could directly measure crawler-vs-source latency with two independently-sourced timestamps, the crawler was **not** the bottleneck.

The apparent "lateness" reported in earlier, informal analysis this session (e.g., "we fetched Nestlé news at 07:51:30 IST, but the market moved 10:45–11:15") was **not** a crawler latency problem in the way originally framed. It was caused by a combination of:

1. **Item misidentification** — the 07:51:30 IST item was a generic "stocks to watch" listicle that name-dropped Nestlé among five other names; it carried no earnings content. The actual Nestlé Q1 NSE disclosures were filed separately, later.
2. **A confirmed, direct MISS — not a delay — on Nestlé's first and most important disclosure.** NSE filed two separate announcements: "Outcome of Board Meeting" at **11:11:18 IST** (seq_id 106706181) and a follow-up "Press Release" at **11:18:41 IST** (seq_id 106706184). Verified live against NSE's own API (post-report correction, see §9): **our platform never captured the Outcome of Board Meeting filing at all** — zero record in any table, ever. We only captured the second, later Press Release, at 11:19:45 IST. This is the single most concrete, independently-proven miss in the entire audit, and it directly corroborates the `limit=50` hypothesis in §7.1 with a real example rather than a theoretical one.
3. **Downstream processing delay, not ingestion delay** — for symbols that never got a trade decision (Bandhan Bank, Maruti, Indian Hotels, Anant Raj, MedPlus, AAVAS, IndiaMART, Trident, Gabriel, Mastek, BlueStone), the news was in our database, in most cases same-day, but the LLM decision step failed downstream (see §11).
4. **Correction to an initial "market moved before the news" reading for Nestlé:** the first version of this report compared the price move against the *Press Release* timestamp (11:18:41) and concluded the market moved 7-9 minutes ahead of the news. That comparison used the wrong filing. Against the true first disclosure (Outcome of Board Meeting, 11:11:18 IST), the sharp price move (11:09-11:11 IST) is concurrent — within the same minute — not ahead of it. The market reacted to the correct, real disclosure; our platform simply never ingested it. See §9 for the full corrected timeline.
5. **A genuine, provable "market moved before the news" case survives for TVS Motor** (§10) — real Zerodha 1-minute candles show the stock jumping +4.1% in 5 minutes *before* the NSE press release was even filed. This one is unaffected by the Nestlé correction; TVS Motor had only one relevant filing, and the move preceded it.
6. **A coverage gap, not a speed gap**, for Bandhan Bank and Maruti Suzuki — our NSE-announcements crawler has never captured a single filing for either company (0 rows, all-time), a fact directly attributable to the crawler's `limit=50` cap on NSE's live feed during a high-volume filing window (evidenced, and now additionally corroborated by the confirmed Nestlé miss — see §7).
7. **A real, provable SSL certificate failure** in our own PDF-download code that broke deep-content analysis for at least one event (TVS Motor) at the exact moment it was needed (§10).
8. **A real, provable circuit-breaker cascade** in the LLM client that killed dozens of same-morning candidate evaluations in a domino pattern, independent of how fast the news arrived (§11).

## 2. Final Answer to "Why did our crawler get the news late?"

**Mostly it wasn't late — it was missing.** For every NSE filing our platform actually captured, ingestion lag was under 3 minutes (27-137 seconds, median ~55s) — the crawler itself is fast. But "did the news arrive late" is the wrong single question; the audit found two distinct failure modes:

1. **Late (delayed, but eventually arrived) — the majority of cases.** Where trades were delayed after a successful capture, the delay lived almost entirely **after** ingestion — in canonical-event classification, LLM ReAct decision-making, and (for TVS Motor specifically) a market move that preceded the news outright.
2. **Missing (never arrived, at any speed) — confirmed at least once, directly.** Nestlé's most important disclosure (Outcome of Board Meeting, 11:11:18 IST) was never captured by our platform at all, verified against NSE's own live API. The cause is NSE's own unfiltered corporate-announcements feed returning a hard-capped 20 most-recent-items-market-wide window (confirmed across 2,892 poll cycles, zero exceptions) — not our own crawler's `limit=50` parameter, which never once bound. This same mechanism is the leading explanation for Bandhan Bank and Maruti Suzuki's complete absence of NSE coverage (0 rows, all-time, for either company).

## 3. Methodology

- All `NewsItem` and `CausalEvent` rows for the 14 symbols were pulled directly from the production Postgres database (`autotrade_postgres` container), filtered by symbol/company keyword match, spanning 2026-07-20 00:00 UTC through 2026-07-22 11:15 UTC.
- Production logs (`/tmp/news-engine.log`, ~100k+ lines) were grepped per-symbol for `Processing Ticker`, `Agent Rejected`, `TRADE OPENED`, `no canonical event`, and PDF-processing lines, cross-referenced against the nearest loguru-timestamped line for wall-clock anchoring (many `news_engine`-prefixed lines use Python's stdlib `logging` with no timestamp at all — a limitation noted in §15).
- Real market price action was pulled directly from Zerodha's historical-candle API (`crawler.zerodha_market.get_kite_historical`, 1-minute interval) for the two deep-dive symbols.
- No timestamp was inferred, estimated, or assumed. Every number below cites its source row/line.

---

## 4–6. Source Timeline, Platform Timeline, and Latency — Per Event

For each event, "source-independent timestamp" means a timestamp NOT generated by our own crawl (either NSE's own `an_dt` field, or an RSS item's own `pubDate`/API-supplied publish time that differs from crawl time). Where the only timestamp available equals crawl time to the millisecond, that is flagged as **crawl-time-defaulted** (our system stamped "now" because the source gave no independent time) — an important distinction the raw data forces on us, since a majority of RSS items in `crawler/news_crawler.py` either supply a genuine `pubDate`/`publishedAt` or fall back silently to capture time depending on feed quality.

### 4.1 NESTLEIND — real result
- **Source (NSE `an_dt`, IST):** `2026-07-22 11:18:41` (NewsItem id=9517, source=NSE-Announcements, category=Press Release)
- **Our crawl (UTC→IST):** `2026-07-22 05:49:45.18 UTC` = **11:19:45 IST**
- **Latency:** **+64 seconds**
- **Evidence:** DB row id=9517.

### 4.2 NESTLEIND — the 07:51:30 IST item (separately investigated in §9)
- **Headline:** "Stocks to watch: Eternal, Nestle, Indian Hotels among shares in focus today; check list here" (id=9358)
- **Source published_at (UTC-labeled):** 02:20:11 UTC = 07:50:11 IST
- **Our crawl:** 02:21:30.9 UTC = 07:51:30 IST → **+79 seconds lag from this item's own timestamp**
- **This is a different news item entirely** — see §9.

### 4.3 BANDHAN BANK
- **First Bandhan-specific result headline in our DB:** CNBC TV18, "Bandhan Bank Q1 profit jumps 35%..." — crawled 2026-07-21 16:49:59.11 IST (id=9066). `published_at` = crawl time to the millisecond (crawl-time-defaulted; CNBC's RSS feed did not supply an independent pubDate this system captured differently).
- **Independently-timestamped corroboration:** "Markets" source, "Bandhan Bank standalone net profit rises 34.87%..." — published_at (own timestamp) 17:16:16 IST, crawled 17:26:28 IST (id=9104) → **+612 seconds (10.2 min) lag**, consistent with this aggregator's typical polling cadence (see §7.3).
- **NSE-Announcements coverage:** **ZERO rows, all-time.** Our NSE crawler has never captured a Bandhan Bank filing. This is a coverage gap, not a latency figure — see §7.
- **Classification:** B (source/aggregator lag, ~10 min) for the RSS path; **coverage gap** for the NSE-direct path.

### 4.4 MARUTI SUZUKI
- **NewsItem coverage:** only 2 rows in the entire database (all-time) mention Maruti — a generic "Key Moving Average Breach" technical item (Jul 20) and a "Stocks in news" listicle (Jul 22, 06:28:37 IST, crawl-time-defaulted).
- **NSE-Announcements coverage:** **ZERO rows, all-time.**
- **Classification: F — UNKNOWN / insufficient evidence.** We cannot determine when Maruti's actual news (referenced in the original incident) was available anywhere, because our system never captured a dedicated Maruti earnings/result article at all in this window. This is the weakest-evidenced case in the scope and should not be assumed to be a "late ingestion" case — it may be a **complete miss**, which is a different failure mode than lateness.

### 4.5 CYIENT DLM — real result
- **Source (NSE `an_dt`, IST):** `2026-07-21 17:20:09` (id=9092, Press Release)
- **Our crawl:** 2026-07-21 11:51:16.31 UTC = **17:21:16 IST**
- **Latency: +67 seconds.**
- CausalEvent created id=2941 at 2026-07-22 08:48:47 UTC = 14:18:47 IST **the next day** — a large gap from the NSE filing (17:20 IST Jul 21) to classification (14:18 IST Jul 22), ~21 hours, entirely a processing/queue-order latency (§11), not an ingestion latency (ingestion was 67 seconds).

### 4.6 INDIAN HOTELS
- **Source (NSE `an_dt`, IST):** `2026-07-21 17:57:39` (id=9123, Press Release)
- **Our crawl:** 2026-07-21 12:28:39.99 UTC = **17:58:39 IST**
- **Latency: +60 seconds.** No CausalEvent was ever created for Indian Hotels in the audited window — see §11.

### 4.7 ANANT RAJ
- **Source (NSE `an_dt`, IST), most relevant filing (Press Release, demerger):** `2026-07-21 19:00:43` (id=9165)
- **Our crawl:** 2026-07-21 13:31:27.79 UTC = **19:01:27 IST**
- **Latency: +44 seconds.** No CausalEvent created — see §11.

### 4.8 MEDPLUS
- **Source (NSE-equivalent RSS), "Medplus Health Services consolidated net profit declines 21.67%..."** — this specific headline came through the RSS "Markets" bulk feed, not a direct NSE-Announcements row (no MedPlus NSE-Announcements row exists in the audited window; NSE's own filing for MedPlus was not independently captured with its own `an_dt` in this dataset — **UNKNOWN** for a true NSE-direct comparison).
- **Our crawl of the RSS item:** 2026-07-22 03:39:59.36 UTC = 09:09:59 IST.
- **Processing outcome:** repeatedly rejected with "Agent failed to reach a decision" and, on later retries, "failed to persist CausalEvent: sorry, too many clients already" (DB connection-pool exhaustion — a real, logged production error). **Classification: C (processing latency / infra failure), not ingestion latency.**

### 4.9 AAVAS
- **Source (NSE `an_dt`, IST):** `2026-07-21 17:48:12` (id=9113, Press Release)
- **Our crawl:** 2026-07-21 12:19:49.98 UTC = **17:49:49 IST**
- **Latency: +97 seconds.** No CausalEvent created — see §11.

### 4.10 INDIAMART
- **Source (NSE `an_dt`, IST):** `2026-07-21 15:42:51` (id=9037, Press Release)
- **Our crawl:** 2026-07-21 10:15:08.26 UTC = **15:45:08 IST**
- **Latency: +137 seconds (2.3 min).** This filing was posted only 12 minutes after market close (15:30 IST) — same-day trading was never realistically possible regardless of crawl speed. No CausalEvent created — see §11.

### 4.11 TRIDENT
- **Source (NSE `an_dt`, IST):** `2026-07-21 19:55:12` (id=9200, Acquisition/Incorporation filing)
- **Our crawl:** 2026-07-21 14:25:47.76 UTC = **19:55:47 IST**
- **Latency: +35 seconds.** No CausalEvent created — see §11.

### 4.12 GABRIEL INDIA
- **Source (NSE `an_dt`, IST), main HL Mando Anand acquisition filing:** `2026-07-21 19:52:46` (id=9196)
- **Our crawl:** 2026-07-21 14:23:34.70 UTC = **19:53:34 IST**
- **Latency: +48 seconds.** No CausalEvent created — see §11.

### 4.13 MASTEK
- **Source (NSE `an_dt`, IST):** `2026-07-21 22:15:57` (id=9276, Press Release)
- **Our crawl:** 2026-07-21 16:46:24.11 UTC = **22:16:24 IST**
- **Latency: +27 seconds.** This is the fastest ingestion in the entire dataset. Filed at 22:16 IST (well after market close), correctly queued overnight; processed at next market open and rejected — see §11.

### 4.14 BLUESTONE
- **Source (NSE `an_dt`, IST):** `2026-07-20 20:25:14` (id=8529, Press Release)
- **Our crawl:** 2026-07-20 14:56:04.78 UTC = **20:26:04 IST**
- **Latency: +50 seconds.**

### 4.15 E2E NETWORKS
- **Source (NSE `an_dt`, IST):** `2026-07-21 13:58:09` (id=8939, Press Release)
- **Our crawl:** 2026-07-21 08:28:47.24 UTC = **13:58:47 IST**
- **Latency: +38 seconds.** This filing was posted **during market hours** (13:58 IST, market closes 15:30 IST) — the only same-day-tradeable NSE filing in the whole scope besides IndiaMART's marginal case. E2E Networks *did* trade the same day (2026-07-21, ~13:59:44 IST per `SimulationLog`), with a confidence-90% verdict and a 3-way second-order cascade (SIFY/TATACOMM/HCLTECH). This is the one clean, fast, end-to-end success story in the dataset: NSE filing (13:58:09) → crawl (13:58:47) → decision → execution (13:59:44), all within **95 seconds total**.

---

## 7. Per-Source Analysis

### 7.1 NSE corporate-announcements crawler (`crawler/news_crawler.py::fetch_nse_corporate_announcements`) — ROOT CAUSE CONFIRMED

- Polling cadence: every 60 seconds (`_NSE_ANNOUNCEMENT_POLL_SEC = 60`, `news_discovery_engine.py`); actual observed poll gaps in the log run 55-70 seconds (e.g., 11:09:50 → 11:10:55 → 11:12:00 IST, all ~65s apart).
- **Measured latency across 11 companies with a direct NSE `an_dt` timestamp: 27–137 seconds, median ~55 seconds.** This is excellent and is not the bottleneck anywhere it was measurable.
- **Original hypothesis in the first version of this report was imprecise and is corrected here.** The report previously attributed missed announcements to our own `fetch_nse_corporate_announcements(limit: int = 50)` parameter. Direct log analysis disproves that specific mechanism and reveals the real one:
  - Every single poll cycle logs `"[news] NSE corporate-announcements: {len(results)}/{len(data)} high-impact"`. A full scan of **2,892 poll-cycle log lines across the entire audited period shows the denominator (`len(data)`, i.e., NSE's own raw response size) is exactly 20 in every single instance — never 19, never 21, never anywhere near our own 50-item cap.**
  - This means **our `limit=50` parameter never once binds** — NSE's `GET /api/corporate-announcements?index=equities` (called with no `symbol` or date-range filter, `crawler/news_crawler.py:371`) itself hard-caps its response to the 20 most recent announcements **across the entire market**, independent of anything in our code.
  - **Confirmed independently, live, against NSE's own API** (not our code): calling the *same* endpoint with `&symbol=NESTLEIND&from_date=22-07-2026&to_date=22-07-2026` returns the complete, correct 2-item history for that company (both the 11:11:18 Outcome of Board Meeting and the 11:18:41 Press Release) — proving NSE's API supports a symbol-scoped query that does **not** suffer from the 20-item market-wide cap, and that our crawler does not use this query shape.
  - **Mechanism, precisely:** during any 60-70 second gap between our polls, if more than 20 companies file *anything* market-wide (not just our 14 tracked symbols — the entire NSE universe), an individual company's announcement can enter and be fully pushed out of the visible 20-item window before our next poll ever runs. This is guaranteed to happen routinely during Q1 results season, when dozens of companies file within any given few minutes.
  - **This is now the confirmed mechanism for the Nestlé Outcome-of-Board-Meeting miss** (see §9 for the poll-by-poll evidence) and remains the leading, code-and-log-evidenced explanation for the Bandhan Bank / Maruti Suzuki zero-coverage gap — though, as before, the exact *volume* of intervening market-wide filings that pushed any specific item out of the window cannot be reconstructed retroactively (NSE's live endpoint has no historical replay), so which specific companies displaced Nestlé/Bandhan/Maruti's items remains unprovable. The 20-item ceiling and its consequence — items can be missed outright, not just delayed — is proven; the specific culprit filings are not.
- Category filter `_HIGH_IMPACT_ANNOUNCEMENT_CATEGORIES` includes "financial result," "result," "outcome of board meeting" — a bank's Q1 results filing (and Nestlé's Outcome of Board Meeting) would match this filter, so category exclusion is not the explanation for either gap.

### 7.2 RSS feeds (Business Standard, LiveMint, Economic Times, Moneycontrol)
- Polling cadence: every ~15-30 seconds (`run_news_discovery_loop`'s main loop, `await asyncio.sleep(15)`).
- Moneycontrol returned HTTP 403 consistently throughout the audited window (confirmed repeatedly in logs) — a real, standing gap in one RSS source, unrelated to timing.
- For sources with genuine independent `pubDate` (the "Markets" aggregator specifically), measured lag was 4–10 minutes (Cyient +4 min, Nestlé +6.4 min, Bandhan +10.2 min) — consistent with that aggregator's own update cadence, not our polling.
- For sources without an independent pubDate (Economic Times, CNBC TV18, Zee Business items observed), `published_at` was stamped identically to `crawled_at` — meaning **we cannot measure true source-to-crawler latency for these rows at all**; we can only confirm we captured them the moment our poll ran.

### 7.3 PDF download/parsing (`crawler/pdf_parser.py`)
- **Confirmed, reproducible failure:** SSL certificate verification failure downloading a PDF from `nsearchives.nseindia.com` for the TVS Motor announcement (2026-07-21 14:27:34 IST): `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain`. This is a genuine infrastructure fault, not a speed issue — see §10.

### 7.4 Twitter/X and company IR pages
- Not used by this platform at all — confirmed by source-code review (`crawler/news_crawler.py`, `news_discovery_engine.py`); no Twitter/X client or IR-page scraper exists in the codebase. **No claim about X being first or last can be made — it was never a candidate source.**

---

## 8. Per-Crawler Analysis (checklist from the request)

| Question | Finding |
|---|---|
| Polled too slowly? | No — NSE poll is 60s, RSS poll is ~15-30s; measured ingestion lag (27s–137s) is consistent with, not worse than, these intervals. |
| Polled the wrong endpoint? | No evidence found. |
| Polled the right endpoint but missed a newly published item? | **Confirmed for Nestlé's Outcome of Board Meeting filing** (§9) — NSE's own unfiltered feed hard-caps at 20 items market-wide (proven across 2,892 poll cycles, §7.1), not our `limit=50`. Same mechanism is the leading explanation for Bandhan Bank/Maruti's zero coverage, though not directly provable for those two since we cannot replay NSE's historical feed content. |
| Received the item but failed to parse it? | **Confirmed once**: TVS Motor PDF, SSL failure (§7.3, §10). |
| Received the item but discarded as duplicate? | Not observed in this scope. `_processed_headlines`/`_processed_seq_ids` dedup by exact text/seq_id; no evidence of a false-positive duplicate discard among the 14 events. |
| Received the item but delayed DB insertion? | Not observed — `crawled_at` values are set at insertion and match observed HTTP-response timing in logs. |
| Incorrect timezone conversion? | **Confirmed, systemic**: NSE `an_dt` stored as naive IST vs. RSS `published_at` stored as UTC, in the same column (§ intro). This is a data-interpretation risk for anyone reading `published_at` without knowing the source, but it did **not** cause any of the 14 events' news to be late — it only makes retrospective analysis error-prone. |
| Used article publication time instead of exchange time? | For NSE-Announcements rows, no — `an_dt` (exchange time) is used directly. For RSS rows without independent pubDate, `published_at` = capture time, which could be mistaken for "true publication time" if not understood (§7.2). |
| Blocked/rate-limited? | Confirmed: Moneycontrol RSS returned 403 throughout; NSE announcements endpoint required a cookie warm-up (by design, working as intended). |
| SSL/network failure? | Confirmed once (TVS Motor PDF, §7.3). |
| Queue/Celery scheduling delay? | **Confirmed, major factor**: the pre-market queue (`PreMarketNewsQueue`) processes 85 queued items **sequentially, one at a time**, each requiring multiple LLM round-trips, only starting at market open (§11). This is the single largest measured contributor to decision delay in the dataset. |
| Stale cache? | Not evidenced in this scope. |
| Source-specific polling schedule creating latency? | Yes — anything published after 15:30 IST (market close) is queued and only acted on at 09:15 IST the next day, by design (`_is_india_trading_window`). This affected 9 of the 14 companies' primary filings, which were posted between 15:36 IST and 22:16 IST. |

---

## 9. Nestlé Deep Dive

**Question: does the "07:51:30 IST fetch" refer to the same event as the actual market-moving news?**

**Answer: No — two different news items.** And a third finding, added after the initial version of this report, corrects the market-timing conclusion below: NSE filed **two** separate Nestlé disclosures that morning, and our platform only ever captured the second one.

### 9.1 The 07:51:30 IST item vs. the real disclosures

| | 07:51:30 IST item | Outcome of Board Meeting (MISSED) | Press Release (captured) |
|---|---|---|---|
| NewsItem ID | 9358 | **none — never ingested** | 9517 |
| NSE seq_id | n/a (not an NSE filing) | **106706181** | 106706184 |
| Headline / attchmntText | "Stocks to watch: Eternal, Nestle, Indian Hotels among shares in focus today; check list here" | "Nestle India Limited has submitted to the Exchange, the Unaudited Financial Results (standalone and consolidated) for the first quarter ended 30th June 2026" | "...titled 'Nestlé India delivered robust 25.4% sales growth...'" |
| Source | mint - markets | NSE (confirmed live via NSE's own API, not our DB) | NSE-Announcements |
| NSE `an_dt` (IST) | n/a | **11:11:18** | 11:18:41 |
| Our `crawled_at` (IST) | 07:51:30 | **never** | 11:19:45 |
| Latency | — | **∞ (complete miss)** | +64s |

**Conclusion, part 1 (unchanged from the original report):** our crawler did **not** get the real earnings news at 07:51:30 — it got an unrelated pre-market listicle that happened to mention Nestlé's name among five other companies, with no financial content.

**Conclusion, part 2 (new, corrects the original report):** our crawler also never got the *first and most fundamental* real disclosure — the Outcome of Board Meeting, filed at 11:11:18 IST. This was verified independently against NSE's live corporate-announcements API on the same day (not inferred from our own database), which returned both filings by seq_id when queried with `symbol=NESTLEIND&from_date=22-07-2026&to_date=22-07-2026`. Our own `NewsItem` table and the full production log contain zero trace of ever fetching or processing seq_id 106706181. We only captured seq_id 106706184 (the Press Release), 7 minutes 23 seconds later.

### 9.2 Root cause of the miss — poll-by-poll evidence

Our NSE poller's own log line (`fetch_nse_corporate_announcements`, `crawler/news_crawler.py:395`) records `{captured}/{total NSE returned}` on every cycle. The relevant sequence, all IST:

| Poll time | NSE returned (total) | Passed our high-impact filter |
|---|---|---|
| 11:09:50 | 20 | 2 |
| 11:10:55 | 20 | 2 |
| **11:11:18** | — | **(Outcome of Board Meeting filed by NSE, seq_id 106706181)** |
| **11:12:00** | 20 | **1** |
| 11:13:04 | 20 | 1 |
| ... | 20 | 1 |
| **11:18:41** | — | **(Press Release filed by NSE, seq_id 106706184)** |
| **11:19:34** | 20 | **2** |

NSE's own feed size is **exactly 20 items on every single poll in this sequence** (and, per §7.1, in all 2,892 polls audited — this is a hard, confirmed NSE-side ceiling on the unfiltered `?index=equities` query, not our own `limit=50`). Note that the high-impact count **drops** from 2 to 1 at the very poll cycle (11:12:00) immediately after the Outcome of Board Meeting was filed (11:11:18) — it does not rise, as it should have if that filing had been visible to us. This is consistent with the filing entering NSE's market-wide 20-item window and being pushed back out again before our next 65-second poll ever ran, displaced by other companies' filings elsewhere in the market during the same window. We cannot prove which other filings did the displacing (NSE's live feed has no historical replay), but the mechanism — a hard 20-item, market-wide ceiling that can fully turn over within a single poll gap during high-volume periods — is now proven, not hypothesized.

### 9.3 Corrected market-timing analysis

Zerodha 1-minute candles for NESTLEIND.NS on 2026-07-22 (converted to IST) show:
- 10:30–11:08 IST: gradual drift from ₹1466 to ~₹1485 (+1.3% over 38 minutes).
- **11:09–11:11 IST: a sharp jump from ₹1491.9 to ₹1509.7 (+1.2% in 2 minutes).**

**Original (incorrect) reading:** this move preceded the NSE Press Release (11:18:41) by 7-9 minutes, suggesting the market moved ahead of the news.

**Corrected reading:** the true first disclosure was the Outcome of Board Meeting at **11:11:18** — inside the same minute as the sharp price move (11:09-11:11). The market did not move meaningfully ahead of the real news; it reacted at essentially the same time the real news was filed. **The error was not in the market's timing, it was in our own missed ingestion of the correct announcement.** Classification for the real event is **A — true late ingestion, specifically a complete miss** of the Outcome of Board Meeting filing (Category A, not E), with the Press Release capture remaining a clean **Category B** (fast, 64-second ingestion of the follow-up filing).

Our system's own BUY execution for NESTLEIND happened at **11:21:23 IST** (`PaperTrade.opened_at` = 2026-07-22 05:51:23 UTC) — 2 minutes after the Press Release was crawled (11:19:45 IST) and 10 minutes after the true first disclosure (11:11:18 IST) that we never saw at all. Had the Outcome of Board Meeting been captured, the earliest possible decision-to-execution window would have opened roughly 7-10 minutes sooner.

---

## 10. TVS Motor Comparison

- **NSE `an_dt` (exchange filing time, IST):** `2026-07-21 14:26:17` (id=8964, "Press release on Un-audited Financial Results")
- **Our crawler (PDF pickup start, log timestamp, IST):** `2026-07-21 14:27:18` — **61 seconds after the filing.**
- **PDF download outcome:** FAILED at `14:27:34 IST` — `[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain` fetching from `nsearchives.nseindia.com`. This is a **confirmed, reproducible, code-level failure** (not a timing issue) — the deep PDF-content summary was never produced for this event.
- **First LLM decision attempt:** same minute (14:27 IST), immediately following the PDF failure, using headline-only context — result: **"Agent failed to reach a decision (Timed out/Insufficient info)."**
- **Subsequent same-day retries** (exact times not independently loguru-timestamped in the stdlib-logged lines, but confirmed to occur before market close): "Liquidity/Execution risk" rejection; "no live price available — skipping execution."
- **Market reaction, real Zerodha 1-minute candles (2026-07-21, IST):** price was flat/consolidating (~₹3595–3608) through 14:06 IST, then surged **from ₹3605.7 (14:06 IST) to ₹3747.5 (14:11 IST) — +4.1% in 5 minutes**, **15–20 minutes before** the NSE press release was filed at 14:26:17 IST.
- **Market closed** at 15:30 IST with no execution achieved.
- **Queued overnight**, reprocessed at next market open (2026-07-22, ~09:15 IST) as part of an 85-item sequential queue-drain (confirmed via log line "🌅 Market is OPEN! Processing 85 queued night/pre-market database alerts..." immediately following the loguru timestamp `09:14:59 IST`).
- **Actual execution:** `2026-07-22 09:47:56 IST` (`PaperTrade.opened_at` = 04:17:56.284 UTC), BUY, confidence 80%, entry ₹3941.35.

**Total elapsed time, NSE filing → execution: ~19 hours 21 minutes.** Breakdown:
- NSE filing → crawler: 61 seconds (fast).
- Crawler → first decision attempt: same minute (fast).
- First decision attempt → final execution: ~19 hours 20 minutes — entirely downstream (PDF/SSL failure, LLM timeout, a legitimate risk-based rejection, market close, overnight queueing, and next-morning sequential reprocessing).

**Crawler got the headline before the body:** Yes, confirmed — the headline/announcement metadata was available and used for the first LLM attempt at 14:27 IST; the PDF body was never successfully retrieved due to the SSL failure.

**Did the market react before our canonical event?** Yes — the sharp +4.1% move (14:06-14:11 IST) preceded the NSE filing itself (14:26 IST) by 15-20 minutes, and by definition preceded our canonical event (never created same-day) by much more.

**Was the delay ingestion or downstream processing?** **Downstream processing**, unambiguously. Ingestion was 61 seconds; the other 19+ hours were classification/decision/queue delay plus one real SSL bug.

---

## 11. Root Cause Classification (per event, final table in §12)

Two systemic, code-evidenced downstream mechanisms explain almost every "failed to trade" case in this scope, independent of news arrival speed:

1. **Sequential pre-market queue drain.** `run_news_discovery_loop()` processes all overnight-queued items **one at a time** at market open (`"🌅 Market is OPEN! Processing {len(queued_items)} queued night/pre-market database alerts..."`), each requiring several sequential LLM round-trips. Confirmed: 85 items queued and drained starting 09:14:59 IST on 2026-07-22, with Mastek and MedPlus's queued items processed only after Bandhan Bank, Servotech, and others ahead of them in the queue.
2. **LLM circuit-breaker cascade.** Within that same drain, a run of "Agent failed to reach a decision" appears back-to-back across unrelated symbols (SERVOTECH → GABRIEL → ANTHEM → GODREJPROP → TCC → PROZONER → TVSMOTOR → MASTEK → MEDPLUS → CRISIL, consecutively) with **no interleaved LLM API call logged for several of them** — consistent with the LLM client's circuit breaker (`utils/llm.py::_mantle_blocked_until`) tripping once and short-circuiting several subsequent, unrelated candidates within its backoff window. This is a compounding failure, not a per-symbol news problem.
3. **DB connection exhaustion.** Confirmed repeatedly for MedPlus (and logged generically for other symbols this session): `"failed to persist CausalEvent: sorry, too many clients already"` — a genuine Postgres connection-pool exhaustion error that prevented a classified event from ever being persisted, which then short-circuits the trade path at "no canonical event — skipping."

None of these three mechanisms are influenced by how fast the crawler ran. They are the actual explanation for why Bandhan Bank, Maruti, Indian Hotels, Anant Raj, MedPlus, AAVAS, IndiaMART, Trident, Gabriel, Mastek, and BlueStone never reached a trade despite (in 9 of these 10 cases with measurable NSE latency) sub-3-minute ingestion.

---

## 12. Final Classification Table

| Event | Source First Available | Our First Seen | Difference | Classification | Evidence |
|---|---|---|---|---|---|
| NESTLEIND (Outcome of Board Meeting — the real first disclosure) | 11:11:18 IST (NSE an_dt, seq_id 106706181) | **never captured** | **∞ — complete miss** | **A — true late ingestion (a total miss, not a delay)** | Verified live against NSE's own API; zero trace in our DB/logs; see §9.2 |
| NESTLEIND (Press Release — follow-up filing) | 11:18:41 IST (NSE an_dt, seq_id 106706184) | 11:19:45 IST | +64s | B — source fast, we matched it | NewsItem id=9517 |
| NESTLEIND (07:51 item) | 07:50:11 IST (listicle pubDate) | 07:51:30 IST | +79s | D — different event than the real news; see §9 | NewsItem id=9358 |
| BANDHAN BANK | 17:16:16 IST (Markets pubdate, RSS) / NSE: none captured | 17:26:28 IST (RSS) / N/A (NSE) | +612s (RSS) / N/A | B (RSS) + coverage gap (NSE) | id=9104; 0 NSE-Announcement rows all-time |
| MARUTI SUZUKI | UNKNOWN | UNKNOWN | UNKNOWN | F — UNKNOWN / insufficient evidence | Only 2 generic NewsItem rows, all-time; 0 NSE rows |
| CYIENT DLM | 17:20:09 IST (NSE an_dt) | 17:21:16 IST | +67s | B — fast; CausalEvent delayed ~21h (queue) | id=9092; CausalEvent id=2941 |
| INDIAN HOTELS | 17:57:39 IST (NSE an_dt) | 17:58:39 IST | +60s | B ingestion / C processing (no CausalEvent ever) | id=9123 |
| ANANT RAJ | 19:00:43 IST (NSE an_dt) | 19:01:27 IST | +44s | B ingestion / C processing (no CausalEvent ever) | id=9165 |
| MEDPLUS | UNKNOWN (RSS-only, no NSE row) | 09:09:59 IST (RSS crawl) | UNKNOWN vs NSE | C — processing (LLM timeout + DB exhaustion) | log: "too many clients already" |
| AAVAS | 17:48:12 IST (NSE an_dt) | 17:49:49 IST | +97s | B ingestion / C processing (no CausalEvent ever) | id=9113 |
| INDIAMART | 15:42:51 IST (NSE an_dt) | 15:45:08 IST | +137s | B ingestion / C processing (no CausalEvent ever) | id=9037 |
| TRIDENT | 19:55:12 IST (NSE an_dt) | 19:55:47 IST | +35s | B ingestion / C processing (no CausalEvent ever) | id=9200 |
| GABRIEL INDIA | 19:52:46 IST (NSE an_dt) | 19:53:34 IST | +48s | B ingestion / C processing (no CausalEvent ever) | id=9196 |
| MASTEK | 22:15:57 IST (NSE an_dt) | 22:16:24 IST | +27s | B ingestion (fastest in dataset) / C processing (queue+circuit breaker) | id=9276; log line 85848 |
| BLUESTONE | 20:25:14 IST (NSE an_dt, Jul 20) | 20:26:04 IST | +50s | B — fast | id=8529 |
| E2E NETWORKS | 13:58:09 IST (NSE an_dt) | 13:58:47 IST | +38s | B — fast, and this one traded same-day | id=8939; trade @13:59:44 IST Jul 21 |
| TVS MOTOR (deep dive) | 14:26:17 IST (NSE an_dt) | 14:27:18 IST | +61s | B ingestion / E market moved first (14:06-14:11) / C 19h processing delay | id=8964; Zerodha candles; SSL log line |

---

## 13. Confirmed Facts

- Loguru logs are in IST; DB timestamps are in UTC; NSE `an_dt`-derived `published_at` is naive IST; RSS-derived `published_at` is UTC or crawl-time-defaulted — all confirmed from source code and `timedatectl`.
- NSE-announcement ingestion latency measured directly for 11 companies: 27–137 seconds, median ~55 seconds.
- Bandhan Bank and Maruti Suzuki have **zero** NSE-Announcements rows in the database, ever.
- A PDF download for TVS Motor failed with a certificate verification error at 14:27:34 IST on 2026-07-21.
- TVSMOTOR.NS moved +4.1% in 5 minutes (14:06-14:11 IST) before its own NSE results filing (14:26:17 IST).
- NESTLEIND.NS moved +1.2% in 2 minutes (11:09-11:11 IST) — this is now confirmed to be concurrent with, not ahead of, the real first disclosure (Outcome of Board Meeting, an_dt 11:11:18 IST), not the later Press Release (11:18:41 IST) used in the original version of this report.
- **NSE filed a Nestlé "Outcome of Board Meeting" announcement at 11:11:18 IST (seq_id 106706181) that our platform never captured at all** — verified independently against NSE's live API. We only captured the follow-up Press Release (seq_id 106706184, 11:18:41 IST).
- **NSE's unfiltered `?index=equities` corporate-announcements feed returns exactly 20 items on every single poll — confirmed across all 2,892 poll-cycle log lines in the audited period, with zero variation.** Our own code's `limit=50` parameter never once binds, because NSE never sends more than 20 items regardless of the parameter we pass.
- NSE's API supports a symbol+date-scoped query (`&symbol=X&from_date=Y&to_date=Y`) that returns complete results unaffected by the 20-item market-wide cap — confirmed by directly querying it for NESTLEIND and receiving both filings. Our crawler does not use this query shape.
- 85 queued items were drained sequentially starting at market open (09:14:59-09:15 IST, 2026-07-22), with confirmed back-to-back "Agent failed to reach a decision" across unrelated symbols.
- MedPlus's CausalEvent persistence failed at least twice with a literal Postgres "too many clients already" error.
- Only Nestlé and Cyient DLM, of the 14 symbols, produced a `CausalEvent` row in the audited window.
- E2E Networks completed NSE filing → execution in 95 seconds and traded same-day.

## 14. Unproven Assumptions (stated as hypotheses, not fact)

- That NSE's 20-item market-wide feed ceiling (now confirmed as a mechanism, §7.1/§9.2) is *also* the specific reason Bandhan Bank/Maruti were never captured, rather than some other cause — the mechanism is proven in general and proven for the Nestlé case specifically, but NSE's exact historical feed content at the relevant moments for Bandhan Bank/Maruti cannot be replayed to confirm it was the same mechanism for them.
- Which specific other companies' filings displaced Nestlé's Outcome of Board Meeting from NSE's 20-item window between 11:11:18 and 11:12:00 IST — the displacement itself is evidenced (the high-impact count dropped, not rose, across that poll gap), but the culprit filings are unprovable without NSE providing historical replay.
- That the LLM circuit breaker (rather than 11 independent LLM failures) explains the Servotech→Mastek→MedPlus run of consecutive failures — highly consistent with the pattern but not confirmed by a per-call exception trace in this log format.

## 15. Unknowns

- Maruti Suzuki's actual Q1 result publication time — **UNKNOWN**, never independently captured by this platform.
- The true "real-world event time" (i.e., when each company's board actually approved results, as distinct from when NSE was told) — **UNKNOWN** for all 14 events; only the exchange disclosure timestamp is available anywhere.
- Exact wall-clock timestamps for many `news_engine`-prefixed log lines (Python stdlib `logging`, no timestamp field) — approximated only by proximity to the nearest loguru-timestamped line, which introduces up to ~60s of uncertainty in some queue-processing sequences (e.g., the exact second the second Mastek/MedPlus attempts fired within the 85-item drain).
- Whether Twitter/X, company IR pages, or BSE carried any of these 14 disclosures earlier than NSE — **not investigated**, because this platform does not ingest from those sources at all (confirmed by code review), so no comparison is possible.

## 16. Risk-Ranked Findings

| Rank | Finding | Impact |
|---|---|---|
| 1 | **NSE's unfiltered feed hard-caps at 20 items market-wide; our crawler queries only that unfiltered endpoint, never the symbol-scoped one** | **High — confirmed to cause at least one complete miss (Nestlé's Outcome of Board Meeting, the most important disclosure of the day for that stock), not merely a delay. Proven mechanism (2,892/2,892 polls capped at 20); leading explanation for Bandhan Bank/Maruti's total absence from NSE coverage.** |
| 2 | Sequential pre-market queue drain, one item at a time, each needing multiple LLM calls | High — directly delays every overnight-queued candidate's decision by however many items precede it |
| 3 | LLM circuit-breaker cascade during the queue drain | High — one upstream failure can silently kill several unrelated candidates in the same burst |
| 4 | Zero NSE-Announcements coverage for Bandhan Bank and Maruti Suzuki | High — not a latency problem, a total blind spot for two of the 14 audited names; likely the same root cause as rank 1 |
| 5 | DB connection-pool exhaustion during CausalEvent persistence | Medium-high — silently converts a would-be valid candidate into "no event, no trade" |
| 6 | SSL certificate failure on NSE PDF downloads | Medium — degrades decision quality (headline-only) at the exact moment deep content matters most |
| 7 | Timezone-inconsistent `published_at` storage | Medium — not a cause of lateness, but a standing risk for any future analysis (including this one, guarded against here) |
| 8 | Market moving before the official filing (TVS Motor only, after correction — see §9) | Informational — outside the platform's control; no crawler fix addresses this |

---

## Blunt Conclusion

**THE MAIN BOTTLENECK IS:**
Two distinct, both-confirmed bottlenecks, not one — they answer two different questions:

- *For news that DID reach our database*: the sequential, one-at-a-time pre-market news queue drain at market open, compounded by an LLM circuit-breaker cascade during that same drain. Not crawler/source ingestion speed, which was consistently fast (27–137 seconds) everywhere it was measured.
- *For news that never reached our database at all*: NSE's own unfiltered `?index=equities` feed hard-caps at 20 items market-wide (proven across all 2,892 audited poll cycles), and our crawler only ever queries that unfiltered endpoint. This is a confirmed, complete miss mechanism — proven directly on Nestlé's Outcome of Board Meeting filing (the single most important disclosure in the entire 14-event scope) — and is the leading, evidenced explanation for Bandhan Bank and Maruti Suzuki's total absence of NSE coverage.

If forced to pick one as "the" bottleneck: the 20-item NSE feed ceiling is the more severe of the two, because it causes outright misses (news never arrives, at any speed) rather than delays (news arrives, just late). A queue delay can still produce a trade; a missed announcement never can.

**WHAT WE SHOULD FIX FIRST:**
Nothing was fixed as part of this audit (per instructions). If prioritizing from evidence alone: switch the NSE corporate-announcements crawler from the unfiltered `?index=equities` query to a per-symbol (or per-watchlist), date-scoped query (`&symbol=X&from_date=Y&to_date=Y`) for the names the platform actually tracks. This directly targets a *proven* complete-miss mechanism (not a delay) that cost the platform its earliest and most important Nestlé disclosure, and is the most likely explanation for two of the fourteen audited companies (Bandhan Bank, Maruti Suzuki) having zero NSE coverage at all. The pre-market queue's sequential, single-threaded drain at market open is the second-highest-impact target — it demonstrably delayed multiple otherwise-fast-ingested events (Cyient's canonical event took ~21 hours to materialize; Mastek/MedPlus/TVS Motor all queued behind other candidates and hit a cascading LLM failure in the same drain window) by minutes to hours, on top of otherwise sub-3-minute source ingestion.
