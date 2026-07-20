# Step-0 Dependency Audit: Source News → CausalEvent → Trade Decision → TradeIntent → Execution Gate

**Date:** 2026-07-20
**Type:** Read-only research artifact. No production code, schema, prompts, thresholds, or execution behavior were modified to produce this document.
**Scope:** Trace every hop from a news/filing event reaching the system to a trade reaching the central execution gate (`engine/decision_router.py`, added earlier today), across all three parallel "alpha" pipelines that currently exist in the codebase. Every claim below is cited to a file and line number; nothing is inferred from names, docstrings, or comments alone — each function's actual body was read.

---

## 1. Executive summary

There are **three separate, uncoordinated pipelines** that each independently evaluate news/market data and can produce a trade. Only one of the three (`news_discovery_engine.py`) is the pipeline that actually produced today's PNB/ULTRACEMCO/SGMART/ALLCARGO/ASHOKLEY trades, but it does not use the most structured of the three data sources available.

| Pipeline | Writes to | Read by trading code? |
|---|---|---|
| A. Event Classification (`event_pipeline.py` → `classify_event()`) | `CausalEvent` table | **No.** Only `api/news.py` (display endpoint) and `query_today_news.py` (debug script) read it. |
| B. News-Discovery Trade Decision (`news_discovery_engine.py` → `llm_tooluse_candidate()`) | `PaperTrade`/`OpenPosition` via the gate | **Yes — this is the live trading path.** It does not read `CausalEvent` or `classify_event()`'s output at all. |
| C. Intelligence Hub (`intelligence_hub.py`) | `MasterIntelligenceScore` table | Yes — read by `agent_loop.py` and 3 paths in `india_tasks.py` for the *technical/multi-factor* scan, not the news-cascade path. |

**The core finding:** Pipeline A (the rich, structured event classifier) and Pipeline B (the pipeline that actually decides and executes news-driven trades) never intersect. `CausalEvent.evidence_ids`-style linkage was never built because the function that would consume it (`llm_tooluse_candidate`) doesn't know `CausalEvent` exists — it has its own, separately-invoked, less-structured "news" tool that performs a live Google News RSS fetch instead.

---

## 2. Pipeline A — Event Classification (writes `CausalEvent`, then nothing reads it)

### 2.1 Trigger
`crawler/news_crawler.py`'s `run_news_crawl()` calls `crawler/event_pipeline.py::process_latest_events(session)` at the end of each crawl cycle (confirmed earlier in this session's diff review — `news_crawler.py` now calls `process_latest_events` wrapped in try/except).

### 2.2 `crawler/event_pipeline.py::process_latest_events()` (lines 11–86)
- **Input:** the 20 most recent `NewsItem` rows that have no matching `CausalEvent` yet (`event_pipeline.py:16–22`, an outer-join `WHERE CausalEvent.id IS NULL`).
- **Step 1 — clustering:** `engine.news_discovery_engine.DuplicateEventEngine.cluster_news()` (imported at `event_pipeline.py:9`, defined at `news_discovery_engine.py:24–~55`). This groups near-duplicate headlines using `difflib.SequenceMatcher` string similarity (>0.5 ratio) — a headline-text-only similarity clustering, no semantic/entity matching.
- **Step 2 — classification:** for each cluster's *primary headline only* (`event_pipeline.py:43-44`), calls `engine/event_classifier.py::classify_event(primary_headline)`.
- **Step 3 — write:** constructs a `CausalEvent` row (`event_pipeline.py:51-62`) from the classification result, and a zeroed-out duplicate stub for every other article in the cluster (`event_pipeline.py:66-78`, `importance=0, confidence=0.0` — explicitly to "prevent inflation").
- **Notable field mapping (schema misuse):** `CausalEvent.country = classification.impact` (`event_pipeline.py:54`) — the `country` column is repurposed to store the impact-level string (`"HIGH"`/`"MEDIUM"`/`"LOW"`), not an actual country. `CausalEvent.duration = str(classification.expected_half_life_hours)` (`event_pipeline.py:61`) — stored as a string, not numeric, confirming the audit finding from earlier today (decay half-life is non-numeric).

### 2.3 `engine/event_classifier.py::classify_event(headline: str)` (lines 21–66)
- **Input: the headline string only.** The function signature is `async def classify_event(headline: str) -> EventClassification | None` (`event_classifier.py:21`) — no article body, no NSE-filing content, no summary text is passed in. The only call site (`event_pipeline.py:44`) confirms this: `classification = await classify_event(primary_headline)`.
- **Output schema (`EventClassification`, `event_classifier.py:6-19`):** `category`, `subcategories`, `impact` (HIGH/MEDIUM/LOW), `confidence` (0-1), `bullish`, `time_horizon`, `expected_half_life_hours`, `entities` ({companies, sectors, countries}), `reasoning`, `surprise_score` (1-100), `is_new_information` (bool), `market_priced_in` (0-1), `source_reliability` (0-1).
- This schema is substantially close to what a "News Truth Contract" would need — `impact` is effectively a materiality tier already, and `is_new_information`/`market_priced_in`/`source_reliability` are more sophisticated fields than currently exist anywhere in Pipeline B. **But it is built entirely from a bare headline string**, so `is_new_information`/`market_priced_in`/`source_reliability` are LLM guesses unconstrained by any actual source text.

### 2.4 Who reads `CausalEvent` afterward
Exhaustive grep for `CausalEvent` usage outside its own definition and the writer above:
- `api/news.py:206-208` — `GET /api/v1/news/causal` (`api/news.py`), a read-only display endpoint. Response model `CausalEventOut` in `api/schemas.py`. Consumed by the frontend's `client.js::getCausalEvents()` and `News.jsx`'s `EventIntelligencePanel` (which — per the original codebase-map audit — falls back to 3 hardcoded fabricated example events when this table is empty, rendered with identical UI chrome to real data).
- `query_today_news.py:5,11` — a one-off root-level debug script, not imported by any production entry point.

**No scoring function, no strategy file, no `TradeIntent` construction anywhere in the codebase reads `CausalEvent`.** This was independently verified via `grep -rn "CausalEvent" --include="*.py" .` returning only the four locations above plus the model definition itself.

---

## 3. Pipeline B — the pipeline that actually decides and executes news-driven trades

### 3.1 Trigger
`news_discovery_engine.py` runs as its own standalone systemd service (`autotrade-news-engine.service`, confirmed running as a separate process earlier today), polling NSE announcements/RSS every 15s via `run_news_discovery_loop()` (`news_discovery_engine.py:222+`).

### 3.2 `news_discovery_engine.py::process_ticker(ticker, side, headline, summary)` (lines 178–~232, post-Phase-1-gate-migration)
- Builds `NewsCandidate(side, headline, summary)` and `NewsDecision(side)` (lightweight shim objects, not DB rows) — see exact field construction below.
- Calls `result = await llm_tooluse_candidate(ticker, cand, dec)` (`engine/agent/decision_engine.py`).
- If `result['verdict'] == 'TAKE'`, calls `_execute_news_trade(...)` which (as of this session's Phase 1 migration) builds a `TradeIntent` and calls `execute_trade_intent()`.
- If the primary trade succeeds, additionally calls `engine.sector_graph.get_second_order_trades(...)` for cascade candidates, each executed via the same `_execute_news_trade()` with `confidence_source=HARDCODED` (now blocked by the gate; this was the `confidence=80` finding).

### 3.3 `NewsCandidate`/`NewsDecision` construction — where the headline/summary actually goes (`news_discovery_engine.py:48–66`)
```python
class NewsCandidate:
    def __init__(self, side, headline, summary):
        self.strategy = "NEWS_DISCOVERY"
        self.side = side
        self.reasons = [f"News Catalyst: {headline}"]   # <- headline stored here
        self.entry = 0
        self.stop = 0
        self.target = 0
        self.risk_reward = 2.5
        self.hub_subscores = {"technical": 0, "news": 95, "sector": 50, "macro": 50,
                               "earnings": 50, "fundamental": 50, "options": 0}
        self.chart_brief = summary   # <- summary text stored here, under a "chart" field

class NewsDecision:
    def __init__(self, action):
        self.action = action
        self.confidence = 60
        self.regime = "NEUTRAL"
        self.master_score = 75
        self.confidence_factors = {}
```
Note: `hub_subscores["news"] = 95` is a **hardcoded placeholder**, not a computed value — every news-triggered candidate reports an identical "news score" of 95 regardless of the actual event.

### 3.4 `engine/agent/decision_engine.py::llm_tooluse_candidate(symbol, candidate, decision)` (line 463+)
This is a shared function also used by `agent_loop.py`'s equity-scan reasoning gate — it is not news-specific. It runs a ReAct tool-use loop where the LLM can call up to 9 tools (`fundamentals`, `news`, `options`, `price_action`, `market_depth`, `intraday_candles`, `sector`, `macro`, `predict_candle`) before producing a final `{verdict, confidence, bull, bear, key_risk}` JSON (`decision_engine.py:496-519`). The rule "must use at least 6 core tools" is stated in the system prompt (`decision_engine.py:542`), not enforced in code.

**Initial context passed to the LLM** — `_candidate_context()` (`decision_engine.py:62-133`):
- Includes: symbol, side, strategy, regime, master_score, entry/stop/target/R:R, the 7-factor hub sub-scores, live NIFTY/BANKNIFTY change%, deep fundamentals from `FundamentalData` if present, and — critically — `candidate.chart_brief` (`decision_engine.py:110-112`), which for a news-triggered candidate **is actually the NSE-announcement summary text**, inserted under the label `"Technical / chart read:"` (a mislabeling — this field is designed for candlestick/indicator chart summaries, not news text, and is being repurposed).
- **Does NOT include** `candidate.reasons` (which holds `f"News Catalyst: {headline}"`) anywhere in `_candidate_context()`'s body — confirmed by exhaustive read of the function; `reasons` is never referenced. **The raw triggering headline itself never reaches the LLM's context** — only whatever `summary` string was passed in, and only via the repurposed `chart_brief` field.

**The `news` tool** — `_tool_news(symbol)` (`decision_engine.py:266-289`), available if the LLM chooses to call it mid-reasoning:
```python
url = f"https://news.google.com/rss/search?q={bare}+stock+OR+share+india&hl=en-IN&gl=IN&ceid=IN:en"
...
for item in root.findall('./channel/item')[:3]:
    headlines.append(f"[{pub_date}] {title}")
return "news (LIVE): " + " | ".join(headlines)
```
This is a **third, independent** news source — a live Google News RSS query returning 3 raw headline titles (no article body). It has no connection to the `NewsItem`/`CausalEvent` the original triggering event came from, and no connection to `classify_event()`'s structured output for that same event.

### 3.5 Where this breaks down for ULTRACEMCO specifically
The NSE-announcement `summary` for the ULTRACEMCO filing (whatever generated the `"[LLM Summary: ...no financial figures or material developments were provided, indicating a routine regulatory filing with limited market impact.]"` text embedded in the trade's stored `ai_reason`) **was present in the LLM's context** via `chart_brief` (§3.4). The LLM was shown an accurate, non-material summary. It nonetheless produced a `bull` field (`"Strong earnings beat and green plant news drive fresh breakout"`) that contradicts the very summary it was given. **There is no consistency check anywhere in `llm_tooluse_candidate()` or `_execute_news_trade()` that cross-references the `bull` claim against the summary/evidence actually shown to the model.** The system prompt instructs the LLM to "debate on data and facts using live data from every tool" (`decision_engine.py:494`) but nothing downstream verifies it did.

### 3.6 `TradeIntent.evidence_ids` — current state after today's gate migration
- Primary (`DIRECT`) news trades: `evidence_ids` is **never set** — defaults to `[]` (`engine/decision_router.py`'s `TradeIntent` dataclass default).
- Second-order (`SECOND_ORDER`) cascade trades: `evidence_ids=[f"cascade_from:{ticker}"]` (`news_discovery_engine.py`, added during today's Phase 1 migration) — a **synthetic string**, not a foreign-key reference to any real `CausalEvent.id` or `NewsItem.id`. There is currently no code path that could resolve this string back to a source document even if something tried.

---

## 4. Pipeline C — Intelligence Hub / `MasterIntelligenceScore` (the technical/multi-factor scan)

- `engine/intelligence_hub.py` scores the ~3,000-symbol universe roughly every 15 minutes, writing 7 sub-scores per symbol (technical/news/sector/macro/earnings/fundamental/options) to `MasterIntelligenceScore`.
- Read by: `agent_loop.py` (equity scan, confidence floor 30, migrated to the gate today), and three paths inside `india_tasks.py` (main equity/short loop, intraday MIS burst, NIFTY option MIS scalp — all migrated to the gate today).
- This pipeline's `news` sub-score is a *computed* factor (unlike Pipeline B's hardcoded `95`), but it is a numeric weight blended into a composite score, not a traceable link to a specific `NewsItem`/`CausalEvent`. A trade sourced from this pipeline currently carries `event_directness=NOT_APPLICABLE` in its `TradeIntent` (set explicitly during today's migration) — correctly reflecting that it is not event-driven, but also meaning it has no `evidence_ids` mechanism at all, by design.

---

## 5. Summary table — every hop, per the requested format

| Step | File | Function | Input | Output | DB table | Evidence traceable? |
|---|---|---|---|---|---|---|
| Crawl | `crawler/news_crawler.py` | `run_news_crawl()` | RSS/NSE feeds | `NewsItem` rows | `news_items` | — |
| Cluster | `crawler/event_pipeline.py` | `process_latest_events()` → `DuplicateEventEngine.cluster_news()` | Unclassified `NewsItem` headlines | Headline clusters (in-memory) | — | Headline text only |
| Classify | `engine/event_classifier.py` | `classify_event(headline)` | **Headline string only** | `EventClassification` (rich schema) | — | No body/filing text used |
| Persist | `crawler/event_pipeline.py` | `process_latest_events()` (write step) | `EventClassification` | `CausalEvent` row | `causal_events` | Linked to `news_id` (1 article per cluster) |
| Display | `api/news.py` | `GET /causal` | `CausalEvent` query | JSON to frontend | — | Read-only, dead-ends here |
| — | *(no trading code reads `causal_events`)* | | | | | **Chain ends** |
| Trigger | `news_discovery_engine.py` | `run_news_discovery_loop()` → `process_ticker()` | NSE announcement `(headline, summary)` — **independent of Pipeline A** | `NewsCandidate`/`NewsDecision` shims | — | `summary` → `chart_brief`; `headline` → `reasons` (unused) |
| Decide | `engine/agent/decision_engine.py` | `llm_tooluse_candidate()` → `_candidate_context()` + optional `_tool_news()` | Hub sub-scores + `chart_brief` (= summary) + optional live Google RSS (3 headlines) | `{verdict, confidence, bull, bear}` | — | No cross-check against summary or `CausalEvent` |
| Execute | `news_discovery_engine.py` | `_execute_news_trade()` | verdict + confidence | `TradeIntent` | — | `evidence_ids=[]` (primary) or synthetic string (cascade) |
| Gate | `engine/decision_router.py` | `execute_trade_intent()` / `authorize_trade_intent()` | `TradeIntent` | `RoutingResult` | `simulation_logs` (audit) | Confidence-source/directness checked; evidence *content* not checked |
| Persist | `paper_trading/trade_simulator.py` | `open_paper_trade()` | Approved signal | `PaperTrade` + `OpenPosition` | `paper_trades`, `open_positions` | Final `ai_reason` = headline + LLM's `bull` text (unverified) |

---

## 6. Answering the specific questions posed

**"Can every `CausalEvent` be uniquely referenced and traced to its original source/news item?"**
Yes, structurally — `CausalEvent.news_id` is a foreign key to `NewsItem.id` (`db/models.py`, confirmed via `event_pipeline.py:18` join). The traceability mechanism exists. It is simply never used by anything downstream of the classifier.

**Does `classify_event()` have a headline-only limitation?**
Confirmed, precisely: `classify_event(headline: str)` (`event_classifier.py:21`) takes a single string argument, and its only call site (`event_pipeline.py:44`) passes `cluster["headline"]` — never a summary, article body, or filing text. All of its richer output fields (`is_new_information`, `market_priced_in`, `source_reliability`, `reasoning`) are LLM inferences from a headline alone.

**Where exactly does evidence get lost?**
Two separate points, not one:
1. **Between Pipeline A and Pipeline B** — `CausalEvent`'s structured classification is never consulted by the function that actually decides trades (`llm_tooluse_candidate`). This is a missing connection, not a corrupted one.
2. **Inside Pipeline B itself** — even though the NSE-announcement summary *is* present in the LLM's context (via the repurposed `chart_brief` field), the model's final `bull`/`bear` output is not checked against it. This is a missing verification step, not a missing data feed. **Connecting `CausalEvent` to `TradeIntent.evidence_ids` alone would not fix this second point** — the hallucination happens downstream of where that connection would land.

---

## 7. What this implies for the proposed Phase 1–5 scope (not yet started, no code changes made)

- **Phase 1 (`StrategyFamily` enum)** is unaffected by this finding and can proceed as scoped.
- **Phase 2 (connect `CausalEvent` → `TradeIntent.evidence_ids`)** is necessary but not sufficient on its own — per §6, it addresses the A→B gap but not the verification gap inside B.
- **Phase 3 (evidence-consistency gate)** needs a precondition this audit surfaces: the consistency check has to sit between `llm_tooluse_candidate()`'s output and `_execute_news_trade()`, comparing `bull`/`verdict` against either `CausalEvent.importance`/`impact` (once connected) or, at minimum, against the `chart_brief`/summary text already in context — not against a field that doesn't exist yet.
- Classify_event()'s headline-only input is a separate, lower-priority weakness (affects Pipeline A's own accuracy, which nothing currently depends on) — worth fixing but not blocking for the Phase 1–3 sequence, since Pipeline A isn't in the live decision path yet.

---

*End of Step-0 audit. No implementation performed. Awaiting review before Phase 1 (`StrategyFamily`) begins.*
