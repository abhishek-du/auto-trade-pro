# V4 Architecture & Quant Review Notes

Based on the detailed review from the "Stock Signal Timestamps" document, here is the roadmap for moving towards an institutional-grade, Event-Driven V4 architecture.

## 1. News Categorization & Exponential Decay (Highest Alpha Potential)
- **Structured Events:** Move away from a single `surprise_score` to a structured JSON object for news:
  - `category` (e.g., ORDER_WIN, EARNINGS, FDA_APPROVAL, PROMOTER)
  - `impact` (e.g., HIGH)
  - `confidence` (e.g., 0.94)
  - `time_horizon` (e.g., 2_5_DAYS)
  - `expected_half_life_hours` (e.g., 72)
- **Exponential Decay:** Replace linear decay with `score(t) = score0 * e^(-λt)`. Use category-specific half-lives (e.g., Regulatory Approval = 10 days, Earnings = 5 days, Rumor = 4 hours).
- *Action Item:* Implement this backward-compatibly in `news_crawler.py` by saving metadata alongside the existing score.

## 2. Event Clustering & Deduplication
- **Problem:** If Mint, CNBC, and ET all report "L&T wins order", the system might parse 3 separate news events and artificially inflate the score.
- **Solution:** Add an **Event Intelligence Layer** to deduplicate news headlines into single **Structured Events**. Only score the 1 consolidated event.

## 3. Strategy-Specific Weighting Engine
- **Problem:** The current universal `Master Score` (`Tech: 65%, News: 12%, Sector: 10%, Macro: 10%, Vol: 15%`) underweights news for event-driven trades and overweights it for pure technical setups.
- **Solution:** Shift to a multi-strategy scoring engine:
  - **Technical Swing:** Tech 45%, News 20%, Vol 15%, Sector 10%, Macro 10%
  - **Event Swing:** News 40%, Tech 30%, Sector 10%, Macro 10%, Vol 10%
  - **Intraday Momentum:** Tech 50%, Vol 25%, Options 15%, News 5%, Macro 5%

## 4. Feature Availability & Immutable Decision Snapshot
- **Problem:** The system doesn't log *exactly* what data was available at `T_0` (e.g., did Options feed fail and fallback to Index? Was the RSI value 68 or 70?).
- **Solution:** Persist a heavy JSON snapshot with every trade decision including:
  - `feature_vector` (Exact RSI, EMA, News Score, Macro Score)
  - `availability_logs` (Which features were available vs fallback)
  - `explainability_versioning` (Git commit SHA, LLM Prompt version, Strategy version)

## 5. Better Universe Selection
- **Problem:** Fixed Top 3000 cap and hardcoded ₹1 Cr minimum turnover.
- **Solution:** 
  - Exclude SME, BE, BZ (illiquid/Trade-to-Trade).
  - Use adaptive scoring based on 30-Day Turnover + 7-Day Acceleration + Premarket Activity.
  - Implement dynamic configurable liquidity and bid-ask spread filters.

## Implementation Guidelines
- **One major change at a time, then measure.**
- Do not let category tags influence live trade execution until they are validated through paper-trading.
- Freeze weights and strategies for 2-4 weeks and run deterministic replays to generate statistical evidence.
