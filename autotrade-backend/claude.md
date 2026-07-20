# Deep Dive Analysis: Stock Signal Timestamps & Quant Architecture Evolution

This document is a comprehensive breakdown of the architectural roadmap and PM review provided in the "Stock Signal Timestamps.pdf" document. The document outlines a critical pivot from a retail-style technical scanner to an institutional-grade event-driven quant engine.

## 1. Initial Observations & Signal Rejections
The document begins by analyzing why the AI system skipped specific signals (ITC, Tech Mahindra, Wipro, IEX) generated after market close (19:43) on 16 July when the market opened on 17 July.

**Key Reasons for Rejection:**
- **Market Hours & Flash News Rule:** The Event Arbitrage module requires instant execution. Since the market was closed, the system aborted instant execution (`No live price for ITC.NS, aborting instant execution`). The system correctly avoids placing blind AMC orders because overnight gap-ups/gap-downs drastically alter the risk-to-reward ratio.
- **Morning Gap-Up Pricing:** If a stock opens significantly higher due to overnight news, the AI will not chase it, as the risk-to-reward is no longer favorable.
- **Strict Thresholds (Master Score):** The system requires a Master Score of `> 75`. During morning re-analysis, indicators like RSI/MACD may have been overbought, dropping the score below 75, resulting in a safe `SKIP`.

## 2. Validation & Missing Evidence
The reviewer notes that while the theoretical explanations are sound, they need concrete evidence from logs:
- Must verify if `market_closed` check explicitly aborts trades.
- Must verify log entries showing `Gap = +3.8% -> Risk reward unacceptable`.
- Must verify the morning re-evaluation actually occurred.

*Later in the document, database logs from `17 July, 09:00:37 UTC` confirm that the morning re-analysis DID occur, proving the system works as intended.*

## 3. Thresholds & Score Composition
The document critiques the hardcoded threshold of `75`.
- A score of `74.9` skips, while `75.0` executes. This rigid binary logic is suboptimal.
- **Proposed Solution:** Implement a tiered system:
  - `>= 85`: Auto Execute
  - `75-85`: LLM Review
  - `50-75`: Watchlist / Re-evaluate velocity
  - `< 50`: Reject
- **Score Velocity ($\Delta$Score):** Track how fast a score is moving. An accelerating score (`61 -> 66 -> 71`) is more valuable than a decaying score (`78 -> 74 -> 69`).

## 4. The Weighting Problem
The document identifies an issue where "News" is underweighted in the Master Score:
- Technical: 0.65
- Volume: 0.15
- News: 0.12
- Sector: 0.10
- Macro: 0.10
*(Total normalized: Tech ~58%, News ~11%)*

Because News is only 11%, even a perfect 100/100 news event cannot trigger a trade if the macro environment is negative (-24). 
**Solution:** Strategy-specific weighting. A "News/Event Swing Strategy" should weight News at 40% and Technicals at 30%, while a "Technical Breakout Strategy" weights Technicals at 58%.

## 5. The V4 Architectural Roadmap (Highest Priority)
The reviewer dictates a massive pivot. Instead of scanning 3000 stocks to see if they have news, the system should monitor news and only score the affected stocks.

**The Ultimate Event-Driven Pipeline:**
1. **News Collection:** Official Filings, Media, Macro.
2. **Deduplication & Clustering:** Merge 4 articles about the same event into ONE event.
3. **Event Intelligence:** Calculate Surprise, Confidence, Novelty, and Duration via LLM.
4. **Dependency Graph (Ripple Effect):** Map the primary stock (e.g., L&T) to secondary peers (ABB, Siemens).
5. **Candidate Ranking:** Rank the 5-30 affected stocks based on event impact.
6. **Technical Execution Filter:** Use technicals *only* to time the entry (wait for EMA breakout, avoid gap-ups).
7. **Risk & Portfolio Engine:** Block trades if sector exposure is too high.

## 6. System Audit & "Quant-Ready" State
The document performs a rigorous code review of the system's temporal correctness (Look-Ahead Bias).
- **Timezones:** Perfect UTC alignment.
- **Feature Timestamps:** Candles and news strictly use `datetime.utcnow()` bounds.
- **Event Sourcing:** A minor drift was found during deterministic replay because code logic changed and exact raw dataframes were not serialized.
- **Verdict:** The system is "Research-ready and Paper Trading-ready" (Institutional-inspired).

## 7. The Final Mandate
Before any new AI features are built, the next 2-4 weeks MUST focus exclusively on:
1. **Persistent Event Store:** Log every event with a lifecycle, decay, and map all trades to an `event_id`.
2. **Duplicate Event Clustering:** Prevent a single news event from inflating scores due to multiple media reports.
3. **Paper-Trading Analytics Dashboard:** Track Event Win-Rate, Maximum Favorable Excursion (MFE), and Maximum Adverse Excursion (MAE).

---
*Summary compiled by Claude AI based on direct document analysis.*
