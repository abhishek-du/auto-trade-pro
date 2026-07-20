"""
Advanced Unstructured Data Parsing (The "Alpha" Edge)
Implements:
1. Supply Chain & SEC Filings impact analysis.

Its sibling function, detect_sentiment_divergence(), was removed 2026-07-20
(Phase 2, docs/NEWS_ONLY_TARGET_ARCHITECTURE_CONTRACT.md) — confirmed zero
callers anywhere in the codebase, including tests. analyze_supply_chain_shock()
below is kept: it still has a live test in tests/test_strategies.py, even
though its only production caller (tasks/unstructured_alpha_scan.py, an
hourly "apple"-only scan that only ever logged results and never persisted
or traded on them) was deleted in the same pass.
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
