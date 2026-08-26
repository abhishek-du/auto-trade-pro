# PHASE 6 — NEWS / AGENT DECISION PIPELINE FORENSICS

**Window:** 2026-08-03 → 2026-08-25 (23 calendar days, 12 sessions with usable 1m coverage)
**Mode:** forensic, read-only. **Zero production changes.**

---

## 1. Executive verdict

The surviving trade-originating pipeline is the news path. Over 23 days it turned **20,266 news
items into 11 non-SKIP agent decisions** — 7 BUY and 4 SELL — and **8 real DIRECT_NEWS
executions**.

| question | answer |
|---|---|
| Where do news candidates disappear? | **At the agent.** 6,465 of 6,476 decisions are SKIP (99.83%). Every other stage passes far more than it drops. |
| Which stage creates the biggest attrition? | The LLM's own verdict — not a gate, not a filter, not a config flag. |
| Do executed candidates outperform skipped ones? | **EVIDENCE NOT AVAILABLE** — 11 non-SKIP decisions cannot support a comparison. |
| Any evidence of information before the price move? | **NO EVIDENCE.** Against a well-matched control every horizon is inconclusive; against same-symbol random timestamps every horizon is inconclusive. |
| Is the pipeline operationally capable of preserving signal? | **Partly.** Three defects found, one of them contaminating the production audit trail. |

**Two findings were not anticipated by the brief and matter more than the return numbers:**

1. **The production audit trail contains synthetic test traffic.** A symbol `TESTCO.NS` accounts
   for **134 of 142** DIRECT_NEWS `EXECUTED_PAPER` gate events (94%). It exists in
   `simulation_logs` and **nowhere else** — not in `news_items`, `causal_events`,
   `agent_decisions`, `paper_trades` or `candles`. Every occurrence carries the same confidence
   (85.0) and the same `event_id` (2848).
2. **`causal_events.news_id` is NULL for all 6,303 events in the window.** The table that links
   an event back to the news item that produced it is never populated, so the
   `NEWS_RECEIVED → CAUSAL_EVENT_CREATED` transition is **not reconstructable from the database**.

---

## 2. BUG-2 verification — **PARTIALLY VERIFIED**

No market session has occurred since the Phase 5 fix. It deployed at **20:01 IST on 2026-08-25**,
after the 15:30 close; the clock now reads 2026-08-25 21:45 IST.

Measured over the 103.5 minutes since deployment:

| metric | target | observed |
|---|---|---|
| cycles | — | **105** |
| window | — | 20:01:30 → 21:45:03 IST |
| expected at 60 s | 104 | — |
| **coverage** | — | **101.4%** |
| median interval | ≈60 s | **60 s** |
| p95 interval | — | **60 s** |
| max interval | — | **61 s** |
| gaps > 2 min | 0 | **0** |
| gaps > 5 min | 0 | **0** |
| gaps > 15 min | 0 | **0** |
| exceptions | — | **0** |
| expired tasks | — | **not observable** — Celery logs nothing for expiry |

Pre-fix baseline: **11 cycles** inside 09:15–15:30 IST on 2026-08-25, against ~375 expected.

**Why this is not CONFIRMED FIXED:** the market is closed, so `_india_trade_loop` returns at its
own status check on `:524` and never reaches the expensive work. These are cheap cycles. The test
that matters — ~375 cycles under market-hours load, when the Hub is simultaneously scoring 1,663
symbols — has not been run.

Consistent with that: **zero `UnboundLocalError` on the new worker**, because BUG-1 lives at
`:610` and the loop returns at `:524` outside market hours. In-session the crash will reappear,
by design.

**Classification: PARTIALLY VERIFIED.** Scheduling is demonstrably sound; behaviour under load
is unmeasured.

---

## 3. News funnel reconciliation

### Production writers and readers

| stage | file:line | persisted? |
|---|---|---|
| news ingestion (RSS) | `crawler/news_crawler.py::run_news_crawl` | `news_items` |
| news ingestion (NSE) | `news_discovery_engine.py` §2 poller | `news_items` |
| ticker extraction | `news_discovery_engine.py::_extract_ticker_from_news` | **no** — 7 exits, log-only (instrumented in Phase 1B) |
| event construction | `crawler/event_pipeline.py:61,165`; `news_discovery_engine.py:904` | `causal_events` |
| evidence building | `news_discovery_engine.py::_build_evidence` | **no** |
| agent decision | `news_discovery_engine.py:1292`, `engine/agent/decision_engine.py:2325` | `agent_decisions` |
| execution gate | `engine/decision_router.py::_log_intent_audit` | `simulation_logs` (`EXECUTION_GATE`) |
| trade | `paper_trading/trade_simulator.py::open_paper_trade` | `paper_trades` |

### The funnel

| # | stage | count | unique symbols | first | last | source |
|---|---|---:|---:|---|---|---|
| 1 | `NEWS_RECEIVED` | **20,266** | 35 sources | 08-03 | 08-25 | `news_items` |
| 1b | …carrying a ticker | 5,737 (28.3%) | — | | | `tickers_affected` |
| 2 | `TICKER_EXTRACTED` | **EVIDENCE NOT AVAILABLE** | | | | not persisted |
| 3 | `CAUSAL_EVENT_CREATED` | **6,303** | — | 08-03 | 08-25 | `causal_events` |
| 3b | …linked to a news row | **0** | — | | | `news_id` NULL for all |
| 4 | `AGENT_CALLED` | **6,476** | 738 | 08-03 | 08-25 | `agent_decisions` |
| 5 | `SKIP` | **6,465** (99.83%) | 737 | | | |
| 5 | `BUY` | **7** | 7 | 08-05 | 08-24 | |
| 5 | `SELL` | **4** | 4 | 08-03 | 08-19 | |
| 7 | `ROUTER_RECEIVED` (news families) | **336** | 189 | 08-03 | 08-25 | `EXECUTION_GATE`, TESTCO excluded |
| 8 | `ROUTER_REJECTED` | **135** | | | | |
| 9 | `EXECUTED_PAPER` (news families) | **201** | 147 | 08-03 | 08-25 | PRE_EVENT 182 · EVENT_DRIVEN 11 · DIRECT_NEWS 8 |
| 10 | `TRADE_OPENED` | **45 rows survive** | | 08-19 | 08-25 | see below |

### Two reconciliation defects, both explained

**(a) 236 `TRADE_OPENED` log events but only 45 `paper_trades` rows.** `paper_trades` holds ids
4278–4491 — a span of 214 with **169 missing** — and its earliest row is 2026-08-19.
**Rows before 08-19 were deleted.** This is a data-retention artefact, not a pipeline failure, and
it is why PRE_EVENT shows 182 gate executions and zero trades: PRE_EVENT ran 08-03 → 08-13,
entirely inside the purged range.

*I initially read this as a gate-to-trade failure. The id-gap check corrected it.*

**(b) DIRECT_NEWS `EXECUTED_PAPER` = 142, of which 134 are `TESTCO.NS`.** Excluding it, the real
count is **8**, across 7 symbols. Every headline DIRECT_NEWS figure in this report uses the
TESTCO-excluded number.

---

## 4. Terminal-state histogram

`EXECUTION_GATE` outcomes, TESTCO excluded, full window:

| outcome | family | n | symbols | window |
|---|---|---:|---:|---|
| `BLOCKED_SAFETY_GATE` | PRE_EVENT | 11,734 | 167 | 08-03 → 08-13 |
| `BLOCKED_SAFETY_GATE` | TACTICAL | 2,168 | 350 | 08-20 → 08-25 |
| `EXECUTED_PAPER` | PRE_EVENT | 182 | 130 | 08-03 → 08-13 |
| `ERROR` | PRE_EVENT | 85 | 1 | 08-03 → 08-07 |
| `BLOCKED_AGENT_DISABLED` | TACTICAL | 74 | 38 | 08-21 |
| `BLOCKED_SHORT` | DIRECT_NEWS | 73 | 1 | 08-20 → 08-25 |
| `BLOCKED_TECHNICAL_ORIGIN` | TECHNICAL | 57 | 7 | 08-19 → 08-25 |
| `BLOCKED_SAFETY_GATE` | EVENT_DRIVEN | 40 | 15 | 08-03 → 08-25 |
| `BLOCKED_SAFETY_GATE` | DIRECT_NEWS | 38 | 11 | 08-19 → 08-24 |
| `EXECUTED_PAPER` | TACTICAL | 35 | 29 | 08-21 → 08-25 |
| `BLOCKED_MARKET_CLOSED` | EVENT_DRIVEN | 19 | 16 | 08-04 → 08-25 |
| `BLOCKED_MARKET_CLOSED` | DIRECT_NEWS | 17 | 7 | 08-21 → 08-24 |
| `BLOCKED_EVIDENCE_DRIFT` | EVENT_DRIVEN | 13 | 9 | 08-03 → 08-18 |
| `EXECUTED_PAPER` | EVENT_DRIVEN | 11 | 10 | 08-03 → 08-24 |
| `BLOCKED_SECOND_ORDER_CONFIDENCE` | EVENT_DRIVEN | 10 | 9 | 08-05 → 08-24 |
| `EXECUTED_PAPER` | DIRECT_NEWS | **8** | 7 | 08-20 → 08-24 |
| others (`ERROR`, `BLOCKED_NO_EVENT`, …) | mixed | 15 | | |

**`BLOCKED_SHORT` is 73 events on a single symbol.** One name repeatedly proposed short and
repeatedly refused, for the reason Phase 1A established: a delivery short is not permitted in the
cash segment.

---

## 5. Silent-drop audit

| location | mechanism | instrumented? |
|---|---|---|
| `_extract_ticker_from_news` — 7 `return None` exits | log line per exit | **yes**, since Phase 1B (`_drop_candidate`) — **but not persisted** |
| `news_discovery_engine.py:1523` `except Exception as _rss_exc` | logs and continues | log only |
| `causal_events.news_id` never set | — | **OBSERVABILITY GAP** — the news→event edge cannot be reconstructed |
| `_build_evidence` returning no canonical event | `return` before any LLM call | **not persisted** |
| `decision_router.py:330` broad `except` | generic `ERROR` outcome | persisted as `ERROR` (96 events) |
| `AgentDecision.skip_reason` | free-text prose | persisted but **not classifiable** — §7 |
| everything upstream of `authorize_trade_intent` | — | log-only; `simulation_logs` starts at the router |

**The observability boundary is the central router.** Downstream of it every decision is a typed
`RoutingOutcome` in `simulation_logs`. Upstream, nothing survives a restart.

---

## 6. Agent decision lifecycle

6,476 decisions in the window. **Every one carries `strategy = 'NEWS'`** — there is no other
originator on this table.

| field | population |
|---|---|
| `action` | SKIP 6,465 · BUY 7 · SELL 4 |
| `master_score` | **0 of 6,476** |
| `macro_bias` | **0 of 6,476** |
| `fund_score` | **0 of 6,476** |
| `order_id` | **NULL on all 11 non-SKIP** |
| `is_paper` | True on all 11 |
| `created_at` vs `ts` | **identical on all 6,476** → no latency measurable |

### The 11 non-SKIP decisions, in full

| date | symbol | action | conf | entry | stop | target |
|---|---|---|---:|---:|---|---|
| 08-03 07:50 | ASTEC.NS | SELL | 59 | 636.90 | — | — |
| 08-05 06:23 | DIXON.BO | BUY | 55 | 14,317.90 | — | — |
| 08-05 06:59 | GREAVESCOT.NS | BUY | 65 | 206.16 | — | — |
| 08-07 09:56 | ZEEL.BO | BUY | 53 | 94.10 | — | — |
| 08-18 04:01 | BSE.NS | SELL | 55 | 3,262.80 | — | — |
| 08-18 09:35 | HILINFRA.NS | BUY | 50 | 46.10 | — | — |
| 08-19 06:11 | TURTLEMINT.NS | SELL | 53 | 139.38 | — | — |
| 08-19 06:37 | SIGMAADV.NS | BUY | 65 | 691.35 | — | — |
| 08-19 09:18 | TURTLEMINT.NS | SELL | 61 | 140.74 | — | — |
| 08-21 05:06 | CEIGALL.NS | BUY | 60 | 317.00 | — | — |
| 08-24 05:53 | RUBICON.NS | BUY | 59 | 1,751.50 | — | — |

**`stop` and `target` are NULL on all 11**, and `order_id` is NULL on all 11. Confidence spans
50–65, i.e. all sit below the `agent_confidence_threshold` of 76 in `runtime_settings`.

---

## 7. Skip-reason forensic taxonomy — read-only, not written back

`skip_reason` is free-text LLM prose: **5,949 distinct strings across 6,465 rows — 92.0% unique**,
mean length 80 characters, 58 effectively empty. Categories below were derived by inspecting the
data, not assumed.

| category | n | % | example |
|---|---:|---:|---|
| unclassified | 1,320 | 38.7% | *"Entering long against negative earnings catalyst without…"* |
| no_price_confirmation | 905 | 26.6% | *"The canonical earnings event is bullish but appears pric…"* |
| technical_context | 430 | 12.6% | *"No access to daily price history to confirm breakout or…"* |
| no_volume_confirmation | 361 | 10.6% | *"Buying at top of range with no fresh breakout or volume…"* |
| liquidity_depth | 131 | 3.8% | *"Conflicting signals: depth favors SELL but momentum/macr…"* |
| low_materiality | 101 | 3.0% | *"News is LOW materiality; price may ignore it…"* |
| gate_blocked_downstream | 62 | 1.8% | *"TAKE verdict but execution gate blocked"* |
| llm_tool_failure | 47 | 1.4% | *"LLM returned 3 consecutive empty/unparseable responses"* |
| degenerate_empty | 27 | 0.8% | *"."* |
| evidence_inconsistency | 14 | 0.4% | *"Evidence inconsistency: event materiality=LOW…"* |
| generic_criteria | 10 | 0.3% | *"Did not meet criteria"* |

**Ambiguity rate: 475 rows (13.9%) matched more than one category; first match used.
Unclassified: 38.7%.**

**This field cannot be used as a taxonomy.** Nearly two in five reasons resist categorisation and
one in seven is genuinely ambiguous. Any future measurement of *why* the agent skips needs a
structured field; the prose is for humans.

### Forward return by skip category (EOD, measured long)

| category | n | symbols | gross | net | vs control |
|---|---:|---:|---:|---:|---|
| no_price_confirmation | 905 | 158 | −0.195 | −0.402 | **−0.145 [−0.313, −0.008]** |
| no_volume_confirmation | 361 | 102 | −0.143 | −0.350 | −0.052 [−0.270, +0.157] |
| technical_context | 430 | 104 | −0.077 | −0.284 | −0.076 [−0.341, +0.175] |
| liquidity_depth | 131 | 57 | −0.207 | −0.414 | +0.027 [−0.262, +0.384] |
| low_materiality | 101 | 52 | −0.485 | −0.692 | — |
| gate_blocked_downstream | 62 | 25 | −0.361 | −0.568 | — |
| unclassified | 1,320 | 177 | −0.106 | −0.313 | −0.044 [−0.181, +0.103] |

**Every category is negative.** The skips avoided losses. Only `no_price_confirmation` has a
control difference excluding zero — and it is negative, meaning those events did worse than
comparable stocks, so the skip was correct.

---

## 8. Executed news sample

**8 real DIRECT_NEWS executions** (TESTCO excluded), 7 symbols, 08-20 → 08-24:
`VIPULORG.BO` ×2, `JUNIPER.NS`, `MUTHOOTFIN.NS`, `RAILTEL.BO`, `HEG.NS`, `ZAGGLE.BO`,
`INDOBORAX.BO`. Plus **11 EVENT_DRIVEN** and **182 PRE_EVENT** (the latter entirely inside the
purged trade range).

Aggregate forward return for all `EXECUTED_PAPER` gate events across families:

```
n=191  symbols=150  gross -0.078%  net -0.285%  win 40.8%  [-0.257, +0.104]
vs matched control:  -0.168pp  [-0.456, +0.087]   inconclusive
```

Compared against what was blocked:

```
BLOCKED_SAFETY_GATE   n=9,706  gross +0.023%  net -0.184%  ctl +0.016 [-0.076, +0.115]
EXECUTED_PAPER        n=  191  gross -0.078%  net -0.285%  ctl -0.168 [-0.456, +0.087]
```

**Executed and blocked are statistically indistinguishable, and both are negative after costs.**
The point estimate favours the blocked population.

---

## 9. News vs skipped / rejected comparison

The brief asks for four comparisons — BUY-rejected, BUY-skipped, SELL-rejected, SELL-skipped.

**EVIDENCE NOT AVAILABLE for all four.** With 7 BUY and 4 SELL decisions in 23 days, of which
4 and 3 respectively have usable forward data, no comparison is possible. Reporting a mean over
four observations would be arithmetic, not evidence.

The measurable comparison is SKIP versus the gate populations, above.

---

## 10. Forward-return diagnostics

3,415 DECISION rows and 10,106 GATE rows scored, 12 sessions, 1m candles, symbol-clustered
bootstrap, MIS cost 0.2072%. Direction respected: SELL measured short.

| population | n | symbols | gross EOD | net EOD | win% | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| agent SKIP | 3,408 | 219 | −0.154 | −0.362 | 40.6 | [−0.269, −0.035] |
| agent BUY | 4 | — | — | — | — | **INSUFFICIENT SAMPLE** |
| agent SELL | 3 | — | — | — | — | **INSUFFICIENT SAMPLE** |
| gate BLOCKED_SAFETY_GATE | 9,706 | 460 | +0.023 | −0.184 | 47.0 | [−0.064, +0.113] |
| gate EXECUTED_PAPER | 191 | 150 | −0.078 | −0.285 | 40.8 | [−0.257, +0.104] |
| gate BLOCKED_AGENT_DISABLED | 73 | 37 | −0.153 | −0.360 | 24.7 | [−0.375, +0.106] |
| gate BLOCKED_TECHNICAL_ORIGIN | 22 | 7 | −0.048 | −0.255 | 40.9 | [−0.261, +0.106] |
| gate BLOCKED_EVIDENCE_DRIFT | 12 | 9 | −0.504 | −0.711 | 25.0 | [−1.129, +0.126] |

**No population is positive after costs.**

---

## 11. Matched controls

Coverage **60.1%** (2,052 of 3,415). Match quality is good on all three axes:

```
SMD trailing-15m return  -0.006      SMD realised vol  +0.044      SMD liquidity  +0.059
```

| horizon | n | signal | control | diff | 95% CI | verdict |
|---|---:|---:|---:|---:|---|---|
| +5m | 2,052 | +0.004 | +0.000 | +0.004 | [−0.005, +0.011] | inconclusive |
| +15m | 2,052 | −0.009 | −0.000 | −0.008 | [−0.027, +0.007] | inconclusive |
| +30m | 2,052 | −0.010 | +0.001 | −0.011 | [−0.042, +0.015] | inconclusive |
| +60m | 2,052 | −0.025 | −0.014 | −0.011 | [−0.065, +0.036] | inconclusive |
| +120m | 2,052 | −0.076 | −0.009 | −0.068 | [−0.153, +0.007] | inconclusive |
| EOD | 2,052 | −0.144 | −0.040 | −0.105 | [−0.240, +0.052] | inconclusive |

**Every horizon inconclusive**, with all six point estimates negative at and beyond +15m.

---

## 12. Same-symbol random-timestamp test

Control: a random timestamp on the same symbol and session within ±60 bars. EOD excluded — a
random timestamp up to 60 bars earlier gets a longer window to the close.

| horizon | n | signal | random-t | diff | 95% CI | classification |
|---|---:|---:|---:|---:|---|---|
| +5m | 3,415 | +0.001 | −0.007 | +0.008 | [−0.010, +0.023] | **inconclusive** |
| +15m | 3,415 | −0.015 | −0.007 | −0.008 | [−0.028, +0.009] | **inconclusive** |
| +30m | 3,415 | −0.024 | −0.012 | −0.012 | [−0.033, +0.007] | **inconclusive** |
| +60m | 3,415 | −0.051 | −0.043 | −0.008 | [−0.030, +0.012] | **inconclusive** |
| +120m | 3,415 | −0.101 | −0.082 | −0.019 | [−0.041, +0.005] | **inconclusive** |

**The news decision timestamp does not identify a better moment than an arbitrary moment in the
same stock on the same day.**

---

## 13. Event / source analysis

**EVIDENCE NOT AVAILABLE at the granularity the brief asks for.**

`causal_events.news_id` is NULL for all 6,303 events, so an event cannot be joined back to its
source or category. `news_items.category` is populated only for NSE announcements — and Phase 1B
established that **zero** NSE announcements were ingested in-session on any day from 08-17
onward, which is exactly the population this analysis would need.

What can be reported is source volume (§3, stage 1): 20,266 items from 35 sources, dominated by
`mint - markets` (5,545), `Markets` (4,673), `Share Market Today` (3,322) and
`NSE-Announcements` (2,827). Their **outcomes** cannot be separated.

Categorising by regex over the free-text `skip_reason` is not a substitute and was not attempted
as one — §7 classifies the *reason*, not the *event type*.

---

## 14. Latency analysis

**EVIDENCE NOT AVAILABLE.**

`agent_decisions.created_at` equals `ts` on all 6,476 rows, so no intra-pipeline latency is
recoverable. `causal_events` carries only `created_at` and no link to the originating news row,
so `T_news → T_event` is not computable either. The chain the brief asks for —
event → extraction → causal event → agent call → decision → router → execution — has **exactly
two** usable timestamps: the decision and the gate event.

No timeout or latency setting was changed.

---

## 15. News vs tactical overlap

Only 2026-08-20 → 08-25 has both populations (tactical signals begin 08-20).

| | count |
|---|---:|
| unique news symbols | 129 |
| unique tactical symbols | 385 |
| symbol overlap | **49** |
| same-symbol same-session overlap | **49** (all of it) |

Ordering on the 49 overlapping symbol-days:

```
news first      26
tactical first  23
mean gap        13.4 minutes
```

**Neither systematically precedes the other.** 38% of news symbols also produced a tactical
signal. Per the brief, independence is **not** concluded from symbol overlap alone; the
populations were not merged.

---

## 16. Master Intelligence contamination audit

**Master Intelligence does NOT reach the news decision. CONFIRMED by code trace, not by symbol
overlap.**

Writer: `engine/intelligence_hub.py:1552` — the only one.

Three things mention `master_score` near the news path. None of them puts Hub content into a news
decision:

| # | site | what it actually is |
|---|---|---|
| 1 | `news_discovery_engine.py:215` | `class NewsDecision: self.master_score = 75` — a **hardcoded constant**. The news path carries a field *named* `master_score` whose value is literally 75, never read from the Hub. |
| 2 | `engine/agent/decision_engine.py:2159 fetch_hub_candidate()` | **genuinely queries** `MasterIntelligenceScore` (`:2176–2190`) — but its only caller is `agent_loop.py:480`, and `agent_loop` is **not in the beat schedule** (Phase 3). Not on the news path. |
| 3 | `engine/agent/execution.py:222 _fetch_hub_scores_for_exits` | reads Hub scores for **exit management of open positions**, not origination. |

Files on the news origination path with **zero** references: `engine/decision_router.py`,
`engine/direct_news_strategy.py`, `crawler/event_pipeline.py`, `engine/event_classifier.py`.

Does master_score reach evidence / prompt / decision / router / execution? **No / No / No / No /
No** — with the single exception of exit management, which is not origination.

Re-quantified for this window: **`master_score` is NULL on 0 of 6,476 `AgentDecision` rows.** The
news path's writer (`news_discovery_engine.py:1292`) never sets the column, which is why the
hardcoded 75 does not appear there either.

---

## 17. What is proven

| # | finding | classification |
|---|---|---|
| 1 | The agent's own verdict is where news candidates disappear — 99.83% SKIP | **CONFIRMED** |
| 2 | 23 days produced 11 non-SKIP decisions and 8 real DIRECT_NEWS executions | **CONFIRMED** |
| 3 | Executed and blocked populations are indistinguishable, both negative after costs | **CONFIRMED** |
| 4 | News decisions carry no information vs a matched control at any horizon | **NO EVIDENCE** |
| 5 | News decision timestamps carry no timing information | **NO EVIDENCE** |
| 6 | Every skip-reason category has negative forward return — the skips avoided losses | **CONFIRMED** |
| 7 | Master Intelligence does not reach the news decision | **RULED OUT** (contamination) |
| 8 | `news_discovery_engine.py:215` hardcodes `master_score = 75` | **CONFIRMED** |
| 9 | `TESTCO.NS` contaminates `simulation_logs` with 144 synthetic gate events | **CONFIRMED** |
| 10 | `causal_events.news_id` is NULL for all 6,303 events | **CONFIRMED** |
| 11 | `paper_trades` before 08-19 was deleted (169 missing ids) | **CONFIRMED** |
| 12 | BUG-2's fix schedules correctly outside market hours | **PARTIALLY VERIFIED** |
| 13 | `skip_reason` is usable as a taxonomy | **RULED OUT** — 92% unique, 38.7% unclassifiable |

---

## 18. What is not proven

- **That executed news trades underperform skipped ones.** n=191 vs n=3,408 with overlapping
  intervals. The point estimate favours skipping; the interval does not exclude parity.
- **That the news path has no edge in principle.** What is shown is that *this* implementation,
  over *these* 12 sessions, produces populations indistinguishable from matched controls. In-session
  NSE announcements — the highest-information subset — are absent from the entire window.
- **That the 11 non-SKIP decisions were wrong.** They cannot be evaluated at all.
- **That BUG-2 is fixed.** §2.
- **Anything about event type or source quality.** §13.
- **Anything about latency.** §14.

---

## 19. Unknowns and observability gaps

| # | gap | consequence |
|---|---|---|
| G1 | `causal_events.news_id` never populated | the news→event edge is unreconstructable; event-type analysis impossible |
| G2 | `agent_decisions.created_at == ts` | no pipeline latency measurable anywhere |
| G3 | `skip_reason` is prose | the dominant attrition stage cannot be quantified by cause |
| G4 | ticker-extraction drops are logged, not persisted | Phase 1B's `_drop_candidate` counters vanish on restart |
| G5 | `simulation_logs` does not record the emitting process | test traffic (TESTCO) is indistinguishable from production at query time |
| G6 | `paper_trades` retention | pre-08-19 trade outcomes are gone; PRE_EVENT's 182 executions cannot be evaluated |
| G7 | no in-session NSE announcement history | the most plausible information source is untested |

---

## 20. Recommended next experiment

**One measurement, then one cheap instrumentation change — in that order.**

1. **Complete BUG-2 verification.** One query against `logs/celery-trade-worker.log` after the
   next session: cycles inside 09:15–15:30 IST, median/p95 interval, maximum gap. It costs
   nothing and it closes §2. Until it is done, BUG-2 is not fixed.

2. **Then, and only if the owner wants the news path measurable at all:** populate
   `causal_events.news_id`. It is a single foreign key that already exists on the model, and it
   is the difference between §13 being answerable and not. Without it, no future phase can ask
   *which kind of news* carries information — the question this phase could not reach.

**Not recommended:** tuning the agent's skip behaviour. Every skip category has negative forward
return and `no_price_confirmation` — the largest classifiable one — is significantly worse than
its matched control. The agent is skipping trades that would have lost money. Making it skip less
would make things worse, not better.

**Also worth flagging for the owner, outside this phase's scope:** `TESTCO.NS` is writing to the
production `simulation_logs` table. It corrupted the DIRECT_NEWS execution count by a factor of
18 in this analysis, and it would corrupt any future one the same way. **Not fixed here.**

---

## Final production statement

| question | answer |
|---|---|
| Any production file modified? | **No.** Working tree clean at HEAD `fec95f3` throughout. |
| Any `.env` or runtime setting modified? | **No.** |
| Any strategy parameter changed? | **No.** |
| Master Intelligence connected? | **No.** |
| Any order submitted? | **No.** |
| Any paper trade opened? | **No.** |
| Any database mutation? | **No.** SELECT statements only; no `INSERT`/`UPDATE`/`DELETE`, no `session.add`, no `commit`, no `flush`. No execution module imported. |

**PHASE 6 WAS FORENSICS ONLY. NOTHING WAS FIXED. NOTHING WAS OPTIMISED. STOP.**
