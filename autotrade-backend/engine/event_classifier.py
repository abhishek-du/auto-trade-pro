import json
import asyncio
from pydantic import BaseModel, Field
from utils.logger import logger
from utils.llm import call_llm_chat

class EventClassification(BaseModel):
    category: str = Field(description="Category of news (e.g. ORDER_WIN, EARNINGS_BEAT, REGULATORY_APPROVAL, MACRO_EVENT, RUMOR, MANAGEMENT_INTERVIEW)")
    subcategories: list[str] = Field(description="List of subcategories (e.g. ['GOVERNMENT', 'INFRASTRUCTURE'])")
    impact: str = Field(description="Impact level: HIGH, MEDIUM, LOW")
    confidence: float = Field(description="Confidence in this classification from 0.0 to 1.0")
    bullish: bool = Field(description="True if bullish, False if bearish")
    time_horizon: str = Field(description="Expected time horizon (e.g. '2_5_DAYS', 'WEEKS')")
    expected_half_life_hours: int = Field(description="Exponential decay half-life in hours")
    entities: dict = Field(description="Affected entities: {'companies': [], 'sectors': [], 'countries': []}")
    reasoning: str = Field(description="Reasoning behind the classification")
    surprise_score: int = Field(description="Impact score from 1 to 100 representing market surprise")
    is_new_information: bool = Field(description="Is this genuinely new information or circulating old news?")
    market_priced_in: float = Field(description="Estimated % of how much the market has already priced this in (0.0 to 1.0)")
    source_reliability: float = Field(description="Reliability of the source (0.0 to 1.0) e.g., NSE=1.0, Rumor=0.3")

async def classify_event(headline: str) -> EventClassification | None:
    """
    Sends a news headline to the LLM to classify its global and sectoral impact.
    Returns a structured EventClassification object.
    """
    sys_prompt = '''You are a world-class Quantitative Event Classification Engine (similar to a hedge fund's proprietary impact map).
Your job is to read a news headline and map out exactly how it will cascade through the Indian stock market.
Do NOT just look for positive/negative text. You reason about supply chains, macroeconomics, and sector impacts.
Output exactly valid JSON matching the following structure and nothing else. No markdown wrappers.

{
  "category": "ORDER_WIN",
  "subcategories": ["GOVERNMENT", "INFRASTRUCTURE"],
  "impact": "HIGH",
  "confidence": 0.94,
  "bullish": true,
  "time_horizon": "2_5_DAYS",
  "expected_half_life_hours": 72,
  "entities": {
    "companies": ["LT"],
    "sectors": ["Capital Goods"],
    "countries": ["India"]
  },
  "reasoning": "Large government-backed order with material revenue impact.",
  "surprise_score": 91,
  "is_new_information": true,
  "market_priced_in": 0.20,
  "source_reliability": 0.95
}
'''
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"Classify this event:\n\n{headline}"}
    ]

    try:
        response_text = await call_llm_chat(messages, max_tokens=2500, temperature=0.1)
        if not response_text:
            return None
            
        import re
        # Find json block if wrapped in markdown
        match = re.search(r'```(?:json)?\s*(.*?)\s*```', response_text, re.DOTALL)
        if match:
            cleaned = match.group(1)
        else:
            cleaned = response_text.replace("```json", "").replace("```", "").strip()
            
        data = json.loads(cleaned)
        return EventClassification(**data)
    except Exception as e:
        logger.error(f"[event_classifier] Failed to classify '{headline}': {e}")
        return None
