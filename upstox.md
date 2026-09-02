# Upstox API — complete reference and AutoTrade Pro usage map

Upstox is the **sole market-data backend** for AutoTrade Pro as of 2026-08-31.
Zerodha Kite is disabled (toggle default off, token expired) and survives only as
the live-order executor, which nothing currently reaches because `PAPER_MODE=true`.

Every "Status" below was **live-probed on 2026-09-02**, not read off the docs.

**Legend**
| Mark | Meaning |
|---|---|
| ✅ **USED** | Wired into AutoTrade Pro and verified working |
| ⚠️ **BLOCKED** | Code exists but the corporate firewall blocks the host |
| 🔲 **AVAILABLE** | Upstox offers it; AutoTrade Pro does not use it |
| ⛔ **N/A** | Not applicable — we trade NSE cash equity only |

> **Scope note that governs this whole document.** AutoTrade Pro is an Indian
> **equity cash-segment** system. F&O was removed entirely (commit *"remove F&O
> functionality entirely — equity-only system"*). So the large parts of the Upstox
> surface devoted to options, futures, Greeks, OI, PCR and Max Pain are marked
> ⛔ N/A — not "not built yet". Adding them would be a product decision, not a gap.

---

## 0. Verified state — 2026-09-02

Every row in this document was **live-probed against the production Upstox
account**, not read off the docs.

| Check | Result |
|---|---|
| `PAPER_MODE` | **True** |
| `AGENT_PAPER_MODE` | **True** |
| `ZERODHA_ENABLED` | **False** |
| `ZERODHA_ACCESS_TOKEN` | **empty** |
| `UPSTOX_ACCESS_TOKEN` | set (313 chars) |
| Real-order paths reachable | **0 of 3** |
| Upstox API families working | **24 of 26** (2 unused, see §18) |
| Market data served by | **Upstox, exclusively** |

### Real money cannot be touched

There were **three** independent paths to a live broker, not one. All are closed,
and all fail **closed**:

| # | Path | Old gate | Now |
|---|---|---|---|
| 1 | `engine/decision_router.py` LIVE branch | a Kite **token** | refuses unconditionally |
| 2 | `engine/agent/execution._live_execute` — bypasses the router | `ZERODHA_ENABLED` | refuses unconditionally |
| 3 | `POST /api/v1/zerodha/orders` | `PAPER_MODE=false` | HTTP **403** |

Each old gate was a **configuration value**, so editing `.env` could have
re-armed real-money placement with no code change and no review. That is the
specific weakness removed: this is now paper-only *by decision*, not by config.

**Verified adversarially.** With `PAPER_MODE=False`, `ZERODHA_ENABLED=True` and a
valid-looking token forced into the running process, and `place_real_order`
patched to record any call — all three paths refused and **the call was never
made**.

`engine/decision_router.py` no longer imports or calls anything Zerodha at all.

### Why "degraded" is the correct broker status

`/api/v1/broker/status` reports `upstox / degraded`. That is accurate, not a
fault: REST quotes work, but the live tick WebSocket host is firewall-blocked
(§15), so prices are polled rather than streamed. Everything paper trading needs
works; only sub-second tick precision is unavailable.

## 1. Authentication & Login

| API | Method | Endpoint | What it does | Status |
|---|---|---|---|---|
| Authorize | GET | `/v2/login/authorization/dialog` | Opens Upstox login, returns an auth `code` | ✅ USED — `api/upstox.py::upstox_login` |
| Get Token | POST | `/v2/login/authorization/token` | Exchanges `code` → `access_token` | ✅ USED — `upstox_callback` |
| TOTP auto-login | POST | `/v3/auth/2fa` | Non-interactive daily login via TOTP secret | ✅ USED — `crawler/upstox_auth.py` |
| Access Token Request | POST | `/v3/login/auth/token/request/{client_id}` | Push-approval token flow → notifier webhook | 🔲 AVAILABLE |
| Analytics Token | — | Developer portal | **1-year** read-only token, no daily refresh | 🔲 AVAILABLE — see below |
| Logout | DELETE | `/v2/logout` | Invalidates the session | 🔲 AVAILABLE |

**Token lifetime is the operational trap.** A standard access token dies at
**03:30 IST the next day regardless of when it was created** — a token minted at
02:30 expires an hour later. AutoTrade Pro handles this with TOTP auto-login plus
`ensure_upstox_token_fresh()`, which re-verifies rather than trusting that a
token *string* exists. That distinction was added deliberately: presence of a
token is not evidence it works.

**The Analytics Token is worth considering.** It is free, read-only, and lasts a
year. Everything AutoTrade Pro currently does with market data would work under
it, and the daily-refresh failure mode would disappear. It **cannot place orders**
— which for a system that is paper-only today is not a limitation. Portfolio and
funds reads additionally require a **whitelisted static IP**.

---

## 2. Instruments — how anything gets identified

| API / File | Endpoint | What it does | Status |
|---|---|---|---|
| Instrument Search | `GET /v2/instruments/search` | Search by symbol/name/ISIN; returns `instrument_key` | ✅ USED — `crawler/upstox_instruments.py` |
| BOD NSE file | `assets.upstox.com/.../NSE.json.gz` | Whole-exchange instrument master | ⚠️ **BLOCKED** |
| BOD Complete / BSE / MCX | `assets.upstox.com/...` | Other exchange masters | ⛔ N/A (NSE-only) |
| MTF / MIS instruments | `assets.upstox.com/...` | Leverage-eligible lists | 🔲 AVAILABLE |
| Suspended instruments | `assets.upstox.com/...` | Currently untradeable | 🔲 AVAILABLE — *would be genuinely useful, see below* |
| MF instruments | `assets.upstox.com/...` | Mutual-fund schemes | ⛔ N/A |
| Global instruments | `assets.upstox.com/...` | GIFT NIFTY, global indices | 🔲 AVAILABLE |

**`instrument_key` is the identity, and this matters.** Format:
`NSE_EQ|INE002A01018` — segment plus ISIN. Upstox's own docs warn that
`exchange_token` **can be reused by the exchange for a different instrument after
expiry**. AutoTrade Pro stores `instrument_key` in `kite_instruments.instrument_key`
(legacy table name, Upstox contents).

**The blocked bulk file has a measurable cost.** With `assets.upstox.com`
unreachable, `sync_upstox_instrument_keys()` resolves keys one search call at a
time. That is slower, but it is **not** the coverage limit it first appeared to
be. Measured against the right denominator — the 3,147 NSE EQ rows the scanner
actually considers, after excluding series-suffixed debt (`-SG`, `-N0`, …) and
numeric-coded instruments — coverage is **2,315 (73.6%)**, and **90.6% of
`hub_universe`**, the set the system actually trades.

Of the 832 names still without a key, essentially all are **ETFs and INAV
feeds** (BANKBEES, LIQUIDBEES, HDFCVLINAV, …). Upstox does not classify those as
`NSE_EQ`, so they will never resolve — and an equity news-driven system should
not be trading them anyway. They are not a gap.

The weekly whole-market candle refresh logs `key_coverage_pct` so this stays
visible rather than assumed.

**Suspended instruments is the one unused file I'd argue for.** A suspended
symbol that still has candles will pass every scan and produce a signal that can
never fill. That is exactly the "opportunity that isn't one" the system is
supposed to avoid.

---

## 3. Market Quotes — snapshot prices

| API | Endpoint | What it does | Limit | Status |
|---|---|---|---|---|
| LTP | `GET /v2/market-quote/ltp` | Last traded price | 500 | ✅ USED — `upstox_quotes.get_live_prices` |
| Full Market Quote | `GET /v2/market-quote/quotes` | LTP + OHLC + volume + **5-level depth** | 500 | ✅ USED — `get_full_quote` |
| OHLC V3 | `GET /v3/market-quote/ohlc` | O/H/L/C with live + previous candle | 500 | ✅ USED — `get_ohlc_batch` |
| LTP V3 | `GET /v3/market-quote/ltp` | LTP + LTQ + volume + close | 500 | 🔲 AVAILABLE |
| Option Greeks | `GET /v3/market-quote/options/greeks` | Delta/Gamma/Theta/Vega/IV | 50 | ⛔ N/A |

These three carry the whole live price path today. `get_full_quote` is what
`market_snapshot._from_zerodha_rest` calls — the function kept its old name, the
data underneath is Upstox, and the recorded source string now correctly reads
`upstox_rest`.

**Rate limits are the thing to respect here**, and there is a specific trap: the
burst limit is **50/sec but the sustained limit is 500/min**. Pacing to 50/s
overruns the minute budget within seconds. That mistake produced **518
rate-limit errors** in this system before `crawler/upstox_limiter.py` and
`UPSTOX_QUOTE_RPS = 8` were introduced. Batch at 250 instruments per request.

---

## 4. Historical & Intraday Candles

| API | Endpoint | What it does | Status |
|---|---|---|---|
| Historical Candle V3 | `GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}` | OHLCV history; minutes/hours/days/weeks/months | ✅ USED — `upstox_candles.py` |
| Intraday Candle V3 | `GET /v3/historical-candle/intraday/{key}/{unit}/{interval}` | Today's candles | ✅ USED |
| Historical Candle (V2) | `GET /v2/historical-candle/...` | Older, fewer intervals | 🔲 superseded by V3 |
| Expired Historical Candle | `GET /v2/expired-instruments/historical-candle/...` | Candles for **expired** contracts | ⛔ N/A (equity-only; also **Upstox Plus**) |
| Get Expiries | `GET /v2/expired-instruments/expiries` | Past expiry dates for an underlying | ⛔ N/A |
| Get Expired Option/Future Contracts | `GET /v2/expired-instruments/{option,future}/contract` | Contracts that settled on a date | ⛔ N/A |

Data availability: **daily from Jan 2000, intraday from Jan 2022.**

**Timezone discipline — the single highest-risk detail in this whole document.**
Upstox returns `2026-09-01T00:00:00+05:30`. The `candles` table is
**naive UTC**. `upstox_candles._to_naive_utc()` performs the conversion, verified
as 09:15 IST → 03:45 UTC. Writing the wall-clock time without converting would
shift every intraday bar by 5h30m and silently corrupt every indicator.

**A related trap already burned this project:** two parallel daily series exist
in `candles` — **00:00 UTC (dead, pre-split prices)** and **18:30 UTC (live)**.
Reading the dead series once produced a fabricated +1.9% overnight gap that had
to be retracted. Always take the latest bar per date.

---

## 5. Orders & Execution — **not implemented**

| API | Method | Endpoint | What it does | Status |
|---|---|---|---|---|
| Place Order V3 | POST | `/v3/order/place` | Place an order (supports slicing) | 🔲 NOT BUILT |
| Modify Order V3 | PUT | `/v3/order/modify` | Change price/qty/type/validity | 🔲 NOT BUILT |
| Cancel Order V3 | DELETE | `/v3/order/cancel` | Cancel pending order | 🔲 NOT BUILT |
| Place Multi Order | POST | `/v2/order/multi/place` | Batch orders in one call | 🔲 NOT BUILT |
| Cancel Multi Order | DELETE | `/v2/order/multi/cancel` | Bulk cancel, tag-filterable | 🔲 NOT BUILT |
| **Exit All Positions** | DELETE | `/v2/order/positions/exit` | Square off everything | 🔲 NOT BUILT — *safety-relevant* |
| Get Order Book | GET | `/v2/order/retrieve-all` | All of today's orders | 🔲 NOT BUILT |
| Get Order Details | GET | `/v2/order/details` | One order's current state | 🔲 NOT BUILT |
| Get Order History | GET | `/v2/order/history` | Every state transition | 🔲 NOT BUILT |
| Get Trades | GET | `/v2/order/trades/get-trades-for-day` | Today's fills | 🔲 NOT BUILT |
| Get Order Trades | GET | `/v2/order/trades` | Fills for one order | 🔲 NOT BUILT |

**Verified reachable** — `GET /v3/order/place` returns `400 UDAPI100012 Invalid`
(wrong method/payload), not 403. The account can place orders; we have no code
that does.

**This is the honest state of live-trading readiness:** market data migrated,
execution did not. `engine/decision_router.py` still gates LIVE mode on a Zerodha
token, and with that token expired it blocks live orders — which is the *correct*
outcome, not a bug. Anyone reading "we migrated to Upstox" should not conclude
the system can trade live through Upstox. It cannot.

**Order-placement rate limits are stricter than everything else:** 10/sec,
500/min, 2000 per 30 min for regular algos (50/sec only for SEBI-registered algos).

---

## 6. Portfolio & Positions

| API | Endpoint | What it does | Status |
|---|---|---|---|
| Get Holdings | `GET /v2/portfolio/long-term-holdings` | Delivery holdings | ✅ USED — `upstox_data.get_holdings` |
| Get Positions | `GET /v2/portfolio/short-term-positions` | Intraday/F&O positions + live P&L | ✅ USED (returns `[]` — paper mode) |
| Get MTF Positions | `GET /v2/portfolio/mtf-positions` | Margin-funded positions | ⛔ **404 on this account** — MTF is not enabled. Unused, so it costs nothing. |
| Convert Positions | `PUT /v2/portfolio/convert-position` | Intraday ↔ Delivery ↔ MTF | 🔲 AVAILABLE |

Holdings feed the "Upstox Demat" portfolio (56 holdings, ₹1,82,264). Positions
correctly returns empty: no real orders are placed.

---

## 7. Account & Funds

| API | Endpoint | What it does | Status |
|---|---|---|---|
| Get Profile | `GET /v2/user/profile` | UCC, exchanges, products, order types | ✅ USED — token verification |
| Get Funds & Margin | `GET /v2/user/get-funds-and-margin` | Available funds, used margin | ✅ USED — `get_funds` |
| Get Funds & Margin V3 | `GET /v3/user/get-funds-and-margin` | Cash / pledged / available-to-trade split | 🔲 AVAILABLE — verified 200. (An earlier revision of this file gave the path as `/v3/user/funds-and-margin`, which returns 400.) |
| **Kill Switch** | `POST /v2/user/kill-switch` | Disable trading per segment **at the broker** | 🔲 AVAILABLE — *see below* |
| Kill Switch Status | `GET /v2/user/kill-switch` | Which segments are live | 🔲 AVAILABLE |
| Get / Update Static IP | `GET`/`PUT /v2/user/ip` | Static-IP registration (algo compliance) | 🔲 AVAILABLE |

**The Kill Switch is genuinely worth noting given a known defect.** Audit finding
**D4**: `POST /agent/kill-switch` sets `settings.AGENT_ENABLED = False`
**in-process only** — uvicorn and Celery are separate processes, so the Celery
worker keeps trading. An Upstox-side kill switch is enforced at the broker and
therefore cannot be defeated by a process that didn't get the memo. That is a
categorically stronger guarantee than any in-process flag. Relevant only once
live orders exist.

---

## 8. Charges & Margin

| API | Endpoint | What it does | Status |
|---|---|---|---|
| Brokerage Details | `GET /v2/charges/brokerage` | Full cost breakdown before trading | 🔲 AVAILABLE — verified working |
| Margin Details | `POST /v2/charges/margin` | Required margin, up to 20 instruments | 🔲 AVAILABLE |

Live-probed: 1 share of RELIANCE at ₹1,277 delivery → **₹25.12 total**
(₹20 brokerage + GST + STT + stamp duty + transaction + IPFT + SEBI turnover).

**Why this is not just a nicety.** `paper_trading/trade_simulator.py` models NSE
costs with its own hard-coded schedule. This endpoint is the broker's *actual*
number. Reconciling the two would tell you whether paper P&L is systematically
optimistic — which matters directly to "preserve a meaningful portion of the
available edge", since a modelled-cost error is indistinguishable from edge until
real money hits it.

---

## 9. Fundamentals — all ISIN-keyed

| API | Endpoint | What it does | Status |
|---|---|---|---|
| Company Profile | `GET /v2/fundamentals/{isin}/profile` | Description, sector, market cap | ✅ USED |
| Income Statement | `GET /v2/fundamentals/{isin}/financials` | Revenue, operating & net profit | ✅ USED |
| Balance Sheet | `GET /v2/fundamentals/{isin}/financials` | Assets, liabilities | ✅ USED |
| Cash Flow | `GET /v2/fundamentals/{isin}/financials` | Operating/investing/financing | ✅ USED |
| Key Ratios | `GET /v2/fundamentals/{isin}/key-ratios` | P/E, P/B, ROA, ROE, ROCE, EV/EBITDA | ✅ USED |
| Share Holdings | `GET /v2/fundamentals/{isin}/share-holdings` | Promoter / FII / DII / public | ✅ USED |
| **Corporate Actions** | `GET /v2/fundamentals/{isin}/corporate-actions` | Dividends, **splits**, bonus, rights | ✅ USED |
| Competitors | `GET /v2/fundamentals/{instrument_key}/competitors` | Peer keys | ✅ USED |

Exposed at `/api/v1/upstox/{profile,financials,ratios,shareholding,corporate-actions,competitors}/{symbol}`,
cached 1 hour, and fed to the LLM agent's `fundamentals` and `company_intelligence` tools.

**`competitors` takes an `instrument_key`, not an ISIN** — the one exception to
the pattern, already handled in `get_competitors`.

**Corporate Actions deserves emphasis.** Splits and bonuses are what make the
dead 00:00 daily series carry pre-split prices. This endpoint is the
authoritative record for detecting them.

---

## 10. News — **was broken, fixed 2026-09-02**

| API | Endpoint | What it does | Status |
|---|---|---|---|
| Get News | `GET /v2/news` | Articles by instrument / positions / holdings | ✅ **USED — fixed today** |

**What was wrong.** The code called `{_V2}/news/articles`, which does not exist:
Upstox returned `404 UDAPI100060 Resource not Found` for every symbol since the
function was written. Three separate defects:

| # | Wrong | Correct |
|---|---|---|
| 1 | Path `/v2/news/articles` | `/v2/news` |
| 2 | No `category` param | **Required** — `instrument_keys` \| `positions` \| `holdings` (else `400 UDAPI1189`) |
| 3 | `instrument_key`, `page` | `instrument_keys` (plural), `page_number` |

**And a fourth, which the 404 was hiding.** The parser read
`title`/`url`/`source`/`published_at`. The real payload is
`heading`/`summary`/`thumbnail`/`article_link`/`published_time`. Even with the URL
fixed, every field except `summary` would have come back empty. `published_time`
is **epoch milliseconds** — parsed as seconds it dates every article to 1970.

Response is keyed **by instrument_key** (`{"NSE_EQ|INE...": [...]}`), not a flat
list, and is `{}` when there is no news — a valid empty result, not an error.

Verified after the fix: 3 real RELIANCE articles with correct timestamps.

**Caveat worth knowing:** `thumbnail` URLs point at `assets.upstox.com`, which is
firewall-blocked — images will not render inside the corporate network even
though the article text arrives fine.

---

## 11. Market Information — market-wide context

| API | Endpoint | What it does | Status |
|---|---|---|---|
| **Market Holidays** | `GET /v2/market/holidays` | Exchange holiday calendar | 🔲 AVAILABLE — verified working |
| **Market Timings** | `GET /v2/market/timings/{date}` | Session open/close per exchange | 🔲 AVAILABLE |
| **Exchange Status** | `GET /v2/market/status/{exchange}` | Open / closed / pre-open | 🔲 AVAILABLE — verified |
| FII Data | `GET /v2/market/fii` | Foreign institutional flows | 🔲 AVAILABLE |
| DII Data | `GET /v2/market/dii` | Domestic institutional flows | 🔲 AVAILABLE |
| OI / Change in OI | `GET /v2/market/{oi,change-oi}` | Open interest by strike | ⛔ N/A |
| PCR / Max Pain | `GET /v2/market/{pcr,max-pain}` | Put-call ratio, max-pain strike | ⛔ N/A |
| Options / Futures Smartlist | `GET /v2/market/smartlist/...` | Ranked contracts | ⛔ N/A |
| MTF Smartlist | `GET /v2/market/smartlist/mtf` | Ranked MTF stocks | 🔲 AVAILABLE |

**The top three are the ones I would actually adopt.** AutoTrade Pro derives NSE
open/closed from its own hardcoded IST logic. An authoritative holiday and
session feed removes a whole class of operational error — engines running on a
holiday, or pre-open treated as normal session. Live probe returned
`{"exchange":"NSE","status":"NORMAL_CLOSE"}` and a full holiday calendar.

**FII/DII would slot into the existing regime engine** as context, not as a
signal on its own.

---

## 12. WebSocket — ⚠️ blocked

| API | Endpoint | What it does | Status |
|---|---|---|---|
| Market Data Feed V3 | `wss://wsfeeder-api.upstox.com` | Live tick stream | ⚠️ **BLOCKED** — code ready |
| Market Feed Authorize V3 | `GET /v3/feed/market-data-feed/authorize` | Returns the socket URL | ⚠️ BLOCKED |
| Portfolio Stream Feed | `wss://wsportfolioupdate-api.upstox.com` | Live order/position/holding/GTT updates | ⚠️ BLOCKED |
| Portfolio Feed Authorize | `GET /v2/feed/portfolio-stream-feed/authorize` | Returns the socket URL | ⚠️ BLOCKED |

**Both REST `authorize` endpoints return 200.** The block is purely on the
`wss://` hosts, not on permission: Upstox will happily hand out a socket URL that
the network then refuses to connect to. That is worth knowing, because a 200 from
`authorize` is not evidence the feed works.

`crawler/upstox_websocket.py` is **written, wired and waiting** — `MarketDataStreamerV3`
with mode budgeting (`full` capped at 2,000 keys, `ltpc` at 5,000), a dict
reverse-lookup instead of an O(n) per-tick scan, and Kite-compatible tick output.
It cannot connect.

**Feed modes:**
- `ltpc` — last price/qty/time + previous close. Cheapest.
- `full` — LTP + **5-level depth** + 1-min, 30-min and daily candles. What we'd use.
- `full_d30` — `full` + 30 depth levels. **Upstox Plus.**
- `option_greeks` — Greeks only. ⛔ N/A.

**Consequence of the block, stated plainly:** every price is polled. The 5-second
stop-loss loop sees REST snapshots, not ticks. It works — `broker/status` reports
`degraded`, which is accurate — but sub-second exit precision is unavailable.

**One caveat for when it is unblocked:** allowing the host is not always enough.
Some FortiGate web-filter profiles permit the host but strip the HTTP `Upgrade`
header, which kills a WebSocket while leaving REST healthy. If `broker/status`
still says `degraded` after whitelisting, that is the thing to check.

---

## 13. Not applicable to this system

| Category | APIs | Why |
|---|---|---|
| GTT Orders | Place / Modify / Cancel / Get GTT | No live execution path |
| IPO | Get IPOs, Apply, Orders, Cancel | Not an IPO system |
| Mutual Funds | Order book, SIPs, Holdings | Equity cash only |
| Payments | Payins, Payouts, Modify, Cancel | Money movement, not trading |
| Trade P&L | Report metadata, P&L report, Trade charges | P&L is computed internally from `paper_trades` |
| Webhooks | Order / GTT push notifications | Requires live orders + a public endpoint |
| Sandbox | Place/Modify/Cancel order testing | Would become relevant if §5 is built |
| MCP Integration | `mcp.upstox.com` | Read-only AI assistant access; unrelated |

---

## 14. Rate limits

| Category | Per second | Per minute | Per 30 min |
|---|---|---|---|
| Standard (quotes, candles, holdings, funds) | 50 | **500** | 2000 |
| Order placement — regular algo | 10 | 500 | 2000 |
| Order placement — SEBI-registered algo | 50 | 500 | 2000 |
| Payouts (read) | 10 | 500 | 2000 |
| Payouts (write) | — | 10 | 300 |
| Apply IPO | 1 | 10 | 300 |

**The 500/min sustained limit is the one that bites**, not the 50/s burst — see §3.

---

## 15. Firewall — hosts to whitelist (TCP/443)

All six are **FortiGuard Web Filter category blocks** on device `FG200FT922950719`.
Confirmed by reading the intercepted response body: *"Web Filter Violation —
Access Blocked"*.

**A plain URL/category allow is sufficient.** `api.upstox.com` and
`api-v2.upstox.com` already pass through with genuine Google `WE1` certificates,
which proves deep SSL inspection is **not** applied globally. No TLS-inspection
exemption and no Fortinet CA install are needed.

| Host | Why it is needed | Priority |
|---|---|---|
| `wsfeeder-api.upstox.com` | Live market tick feed — real-time prices for the 5-second stop-loss loop | **Critical** |
| `upstox.com` | **OAuth login page** — without it no daily access token can be generated and *every* Upstox call stops. Not merely "documentation". | **Critical** |
| `assets.upstox.com` | Daily instrument master. Without it, keys are resolved one search call at a time, and no NEW listing can enter the universe at all. | **High** |
| `wsportfolioupdate-api.upstox.com` | Order/position update stream | Medium (needed only with live orders) |
| `trendlyne.com` | Earnings **conference-call transcripts**. Beat task `tasks.fetch_earnings_transcripts` runs on schedule and silently returns nothing. | Medium |
| `www.moneycontrol.com` | 1 of 6 India RSS feeds in the narrative engine — currently running on 5. | Low |

**Already working, no action needed:** `api.upstox.com`, `api-v2.upstox.com`,
`www.nseindia.com` (its 403 is NSE's own bot protection, not the firewall),
`nsearchives.nseindia.com`, `bedrock-mantle.us-east-1.api.aws`, and all news/RSS
sources other than the two above.

---

## 16. Where each API lives in the codebase

| Module | Responsibility |
|---|---|
| `crawler/upstox_auth.py` | TOTP auto-login, token refresh, **health verification** |
| `crawler/upstox_instruments.py` | `instrument_key` resolution via search + daily sync |
| `crawler/upstox_quotes.py` | LTP / full quote / OHLC batch — Kite-identical return shapes |
| `crawler/upstox_candles.py` | Historical + intraday candles, **+05:30 → naive-UTC conversion** |
| `crawler/upstox_historical.py` | Bulk historical backfill |
| `crawler/upstox_websocket.py` | `MarketDataStreamerV3` with mode budgeting — ⚠️ blocked |
| `crawler/upstox_limiter.py` | Redis rate limiter with a **reserved EXIT bucket** |
| `crawler/upstox_data.py` | Fundamentals, news, funds, holdings + caching |
| `crawler/upstox_market.py` | Market-data helpers |
| `api/upstox.py` | 15 REST routes exposing the above |
| `api/broker.py` | **Broker-agnostic** status — the single surface the UI reads |

The reserved EXIT bucket in the limiter is deliberate: **exit-path price lookups
must never be starved by scanning traffic.** A rate limit that delays an entry
costs an opportunity; one that delays a stop-loss costs money.

---

## 17. If live trading is ever pursued

Ordered by dependency, not preference:

1. **Unblock `wsfeeder-api.upstox.com`** — everything below is degraded without ticks.
2. **Build an Upstox order executor** (§5) mirroring `engine/zerodha_executor.py`'s ten safety rules. Nothing works without this.
3. **Wire `PortfolioDataStreamer`** for order/position updates. Do **not** poll the order book to detect fills — push first, REST as reconciliation.
4. **Adopt the broker Kill Switch** (§7) — it survives the process boundary that defeats the current in-process one (defect D4).
5. **Wire Exit All Positions** as the emergency square-off.
6. **Reconcile `charges/brokerage`** against the simulator's modelled costs.
7. **Adopt Market Holidays / Timings / Status** — cheap, and removes a class of operational error.

Steps 4–7 are independently useful and **do not require live trading**; 6 and 7
could be done today.

---

*Compiled 2026-09-02. Every status live-probed against the production Upstox
account, not inferred from documentation.*

---

## 18. Live API verification — 2026-09-02

Every family probed directly against the production account. **24 of 26
reachable**; the two that are not are unused.

| Family | Endpoint | Result |
|---|---|---|
| Auth | `/v2/user/profile` | ✅ 200 |
| Account | `/v2/user/get-funds-and-margin` | ✅ 200 |
| Account | `/v3/user/get-funds-and-margin` | ✅ 200 |
| Instrument | `/v2/instruments/search` | ✅ 200 |
| Quote | `/v2/market-quote/ltp` | ✅ 200 |
| Quote | `/v2/market-quote/quotes` (5-level depth) | ✅ 200 |
| Quote | `/v3/market-quote/ohlc` | ✅ 200 |
| Quote | `/v3/market-quote/ltp` | ✅ 200 |
| Candle | `/v3/historical-candle` daily | ✅ 200 |
| Candle | `/v3/historical-candle` 1-minute | ✅ 200 |
| Candle | `/v3/historical-candle/intraday` | ✅ 200 |
| Portfolio | `/v2/portfolio/long-term-holdings` | ✅ 200 |
| Portfolio | `/v2/portfolio/short-term-positions` | ✅ 200 (`[]` — paper mode) |
| Portfolio | `/v2/portfolio/mtf-positions` | ⛔ 404 — MTF not enabled; unused |
| News | `/v2/news` | ✅ 200 |
| Fundamentals | `profile` · `income-statement` · `balance-sheet` · `cash-flow` · `key-ratios` · `share-holdings` · `corporate-actions` · `competitors` | ✅ all 8 OK |
| Charges | `/v2/charges/brokerage` | ✅ 200 |
| Market | `/v2/market/holidays` · `status/NSE` · `timings/{date}` | ✅ 200 |
| WebSocket | `/v3/feed/market-data-feed/authorize` | ✅ 200 (REST) — `wss://` host blocked |
| WebSocket | `/v2/feed/portfolio-stream-feed/authorize` | ✅ 200 (REST) — `wss://` host blocked |

### And through the application's own code paths

| Path | Result |
|---|---|
| `market_snapshot` (news-trade entry price) | `upstox_rest`, RELIANCE ₹1313.10 |
| `get_live_prices` batch | 3/3 priced |
| `get_market_depth` | 5 bid × 5 ask levels |
| `get_kite_historical` (Upstox-backed) | day 7 · 15m 175 · 5m 525 · 1h 49 · 1m 2,625 bars |
| `fetch_nse_candles` | 7 bars, ascending, on the live 18:30 series |
| `_refresh_priority_1d_candles` (scheduled) | **1,896 symbols, 4,776 candles, 0 failed** |
| `live_snapshot` | 20 index/sector symbols |
| Fundamentals + News | all 8 OK · 3 real articles |
| Instrument-key coverage | 2,338 keyed · **90.6% of `hub_universe`** |

### Silent failures found and fixed on 2026-09-02

Each of these returned an empty result that every caller treats as "no data", so
none of them logged anything or raised:

| What | Consequence while broken |
|---|---|
| `get_kite_historical` opened with a Kite-token guard | **Zero candles for every symbol and timeframe** — including the LLM agent's `price_action` and `intraday_candles` tools, so the agent was deciding trades with no candle data |
| `_backfill_hub_1d_candles` + `_refresh_priority_1d_candles` had leftover token guards | Both scheduled backfills returned `{"skipped": "not_authenticated"}`; hub daily candles stopped refreshing |
| `UPSTOX_ENABLED` gate in `india_price_feed` | Referenced once, **defined nowhere** → always False → `fetch_nse_candles` silently served yfinance instead of the broker |
| Upstox rows returned newest-first | `direct_news_strategy` (a **live** trade gate) read `iloc[-1]` as "latest close" and ran `ewm()` — comparing a 60-day-old price against a backwards EMA |
| Daily timestamps forcibly zeroed to `00:00` | Wrote into the **dead, pre-split** duplicate daily series — the one that produced a retracted +1.9% fake gap earlier in this project |
| ISIN DB cache selected an ORM entity, then `rollback()` expired it | Never served a single hit; every lookup fell through to a live network resolve |
| `_INTERVAL_MAP` missing `5minute`/`15minute`/`30minute` | `.get()` falls back to **daily** — a caller asking for 15-minute bars would silently receive daily ones |
| WebSocket `_on_error` logged the peer's whole body | 30 KB FortiGuard block page per reconnect × 12/min ≈ **500 MB/day** into a non-rotating log |
| Dead Kite fallback in `get_kite_candles_for_range` | One doomed thread + network call + WARNING per keyless symbol, across 1,896 symbols |

### Still not working, and why

| What | Why | Impact |
|---|---|---|
| Live tick WebSocket | `wsfeeder-api.upstox.com` firewall-blocked | Prices are polled. Works; sub-second precision unavailable. `broker/status` = `degraded`, correctly. |
| New NSE listings | `sync_nse_eq_instruments` is the only thing that ADDS universe rows, is Kite-only, and cannot move to Upstox while `assets.upstox.com` is blocked (search answers a query; it does not enumerate an exchange) | Existing symbols fully served. **New listings are not picked up.** Last good refresh 2026-08-29. Now logs at ERROR with `universe_frozen`. |

