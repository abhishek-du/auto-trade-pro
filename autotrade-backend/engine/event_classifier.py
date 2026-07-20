import json
import asyncio
from pydantic import BaseModel, Field
from utils.logger import logger
from utils.llm import call_llm_chat

class EventClassification(BaseModel):
    category: str = Field(description="Category of news (e.g. ORDER_WIN, EARNINGS_BEAT, REGULATORY_APPROVAL, MACRO_EVENT, RUMOR, MANAGEMENT_INTERVIEW)")
    impact: str = Field(description="Impact level: HIGH, MEDIUM, LOW")
    importance: int = Field(description="Impact score from 1 to 100 (100 being highly market-moving surprise)")
    confidence: float = Field(description="Confidence in this classification from 0.0 to 1.0")
    affected_sectors: list[str] = Field(description="List of sectors positively or negatively affected")
    affected_indices: list[str] = Field(description="Broad market indices affected (e.g., 'NIFTY', 'BANKNIFTY')")
    bullish: list[str] = Field(description="Specific stock tickers (e.g., 'HDFCBANK') that will benefit")
    bearish: list[str] = Field(description="Specific stock tickers that will be harmed")
    expected_half_life_hours: int = Field(description="Exponential decay half-life in hours (e.g. Earnings=120, Order Win=72, Rumor=4)")
    reasoning: str = Field(description="1-2 sentences reasoning behind the classification")

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
  "impact": "HIGH",
  "importance": 90,
  "confidence": 0.95,
  "affected_sectors": ["Infrastructure"],
  "affected_indices": ["NIFTYINFRA"],
  "bullish": ["LART", "NCC"],
  "bearish": [],
  "expected_half_life_hours": 72,
  "reasoning": "Large government-backed order with material revenue impact."
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
