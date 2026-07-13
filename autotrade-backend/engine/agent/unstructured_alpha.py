"""
Advanced Unstructured Data Parsing (The "Alpha" Edge)
Implements:
1. Supply Chain & SEC Filings impact analysis.
2. Sentiment Divergence (Retail vs Price Action) detection.
"""

import json
from utils.logger import logger
from utils.llm import call_llm_chat

async def analyze_supply_chain_shock(company: str, headline: str, news_body: str) -> dict | None:
    """
    Reads a news article or filing about a major company (e.g. Apple)
    and identifies the Indian suppliers (e.g. Dixon, Amber, Kaynes) that might be affected.
    Returns {"affected_suppliers": [{"symbol": "DIXON", "action": "SHORT", "reason": "..."}]}
    """
    prompt = f"""Analyze the following breaking news/filing for {company}.

Headline: {headline}
Content: {news_body}

Identify any 2nd-order or supply chain impacts on Indian listed companies (NSE).
For example, if {company} is cutting production, its suppliers will suffer. If {company} is expanding in India, contract manufacturers benefit.
Respond ONLY with valid JSON:
{{
  "affected_suppliers": [
    {{
      "symbol": "TICKER_WITHOUT_NS",
      "action": "LONG" or "SHORT",
      "reason": "Brief 1-sentence reason based on the supply chain dynamic"
    }}
  ]
}}
If no obvious Indian listed supplier is affected, return an empty list for affected_suppliers.
"""
    try:
        resp = await call_llm_chat(
            [{"role": "system", "content": "You are a supply-chain and equity analyst specializing in the Indian market."},
             {"role": "user", "content": prompt}],
            max_tokens=400, temperature=0.2
        )
        
        from engine.agent.decision_engine import _parse_first_json
        data = _parse_first_json(resp)
        return data
    except Exception as exc:
        logger.error(f"[unstructured_alpha] Supply chain analysis failed: {exc}")
        return None

async def detect_sentiment_divergence(symbol: str, price_trend: str, retail_sentiment: str) -> dict | None:
    """
    Looks for divergences between retail sentiment and price action.
    price_trend: e.g., "Making lower highs over the last 5 days"
    retail_sentiment: e.g., "Wildly bullish on Twitter/Telegram, expecting breakout"
    
    Returns {"divergence_detected": True/False, "thesis": "Institutional Distribution", "recommended_action": "SHORT"}
    """
    prompt = f"""Analyze the following divergence between retail sentiment and price action for NSE stock: {symbol}.

Price Action Trend: {price_trend}
Retail Sentiment (Social Media/Forums): {retail_sentiment}

Is there a significant sentiment divergence? 
Specifically, look for "Institutional Distribution" (retail is highly bullish but price makes lower highs) or "Institutional Accumulation" (retail is extremely bearish/panicking but price is holding support or making higher lows).

Respond ONLY with valid JSON:
{{
  "divergence_detected": true/false,
  "thesis": "e.g., Institutional Distribution / Institutional Accumulation / None",
  "recommended_action": "SHORT" or "LONG" or "HOLD",
  "reasoning": "1-sentence explanation"
}}
"""
    try:
        resp = await call_llm_chat(
            [{"role": "system", "content": "You are an expert quantitative analyst specializing in market microstructure and sentiment divergences."},
             {"role": "user", "content": prompt}],
            max_tokens=300, temperature=0.2
        )
        
        from engine.agent.decision_engine import _parse_first_json
        data = _parse_first_json(resp)
        return data
    except Exception as exc:
        logger.error(f"[unstructured_alpha] Sentiment divergence analysis failed: {exc}")
        return None
