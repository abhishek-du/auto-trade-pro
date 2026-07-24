import json
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
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

async def classify_event(headline: str, summary: str | None = None) -> EventClassification | None:
    """
    Sends a news headline (optionally + a longer summary/filing excerpt) to the
    LLM to classify its global and sectoral impact. Returns a structured
    EventClassification object.

    `summary` is optional and backward-compatible — existing callers that only
    have a headline (e.g. crawler/event_pipeline.py's NewsItem clustering pass)
    are unaffected. Added because a headline alone can be uninformative (e.g.
    a generic "Quarter ended 30 June 2026" press-release title) while the
    summary/filing text may explicitly say there's nothing material in it —
    classifying from the headline alone risks the same blindness that let the
    2026-07-20 ULTRACEMCO trade get called a bullish "earnings beat" event.
    """
    sys_prompt = '''You are a world-class Quantitative Event Classification Engine (similar to a hedge fund's proprietary impact map).
Your job is to read a news headline (and summary/filing excerpt, if provided) and map out exactly how it will cascade through the Indian stock market.
Do NOT just look for positive/negative text. You reason about supply chains, macroeconomics, and sector impacts.
If the summary explicitly states there are no material developments / no financial figures / a routine filing, you MUST classify impact as LOW regardless of how the headline reads on its own.
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
    user_content = f"Classify this event:\n\nHeadline: {headline}"
    if summary:
        user_content += f"\n\nSummary/filing excerpt: {summary}"

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_content}
    ]

    try:
        response_text = await call_llm_chat(messages, max_tokens=2500, temperature=0.1)
        if not response_text:
            # Root-caused 2026-07-23: this used to be completely silent, which
            # is why a Bedrock circuit-breaker cascade (utils/llm.py) killing
            # 45-50 candidates in a row looked identical to routine
            # classification misses in the logs -- see
            # docs/NEWS_INGESTION_LATENCY_FORENSIC_AUDIT.md-adjacent
            # investigation. call_llm_chat() returns falsy for two reasons:
            # the circuit breaker is open (blocking ALL calls, not just this
            # one -- see utils.llm's own block-window log), or a genuine
            # empty-content response survived both of call_mantle_chat's
            # internal retries. Either way, this is worth a trace.
            logger.warning(f"[event_classifier] classify_event: no response for '{headline[:60]}' (LLM call failed or circuit breaker open)")
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


# ═══════════════════════════════════════════════════════════════════════════════
# Evidence Contract — explicit transport for structured evidence into the
# trade-decision LLM (engine/agent/decision_engine.py::llm_tooluse_candidate),
# replacing the previous practice of smuggling news summary text through a
# `chart_brief` field meant for candlestick/indicator chart data.
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DecisionEvidence:
    source_type:     str              # "NSE_ANNOUNCEMENT" | "RSS" | "NEWSDATA_IO" | etc.
    source_id:       str | None
    title:           str
    summary:         str
    event_category:  str              # EventClassification.category
    materiality:     str              # EventClassification.impact — HIGH | MEDIUM | LOW
    direction:       str              # "BULLISH" | "BEARISH"
    confidence:      float            # EventClassification.confidence (0-1)
    published_at:    datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def from_classification(
        cls, classification: "EventClassification", *,
        source_type: str, source_id: str | None, title: str, summary: str,
    ) -> "DecisionEvidence":
        return cls(
            source_type=source_type, source_id=source_id, title=title, summary=summary,
            event_category=classification.category, materiality=classification.impact,
            direction="BULLISH" if classification.bullish else "BEARISH",
            confidence=classification.confidence,
        )


@dataclass
class EvidenceConsistencyResult:
    consistent:            bool
    contradiction_detected: bool
    unsupported_claims:    list[str]
    evidence_strength:     float
    reason:                str


# Bullish-conviction language that should never appear in a trade thesis when
# the underlying event's own classified materiality is LOW — this is the
# deterministic, fail-closed rule that would have blocked the ULTRACEMCO trade
# (materiality=LOW, thesis claimed "Strong earnings beat", confidence=71%).
_HIGH_CONVICTION_CLAIM_KEYWORDS = (
    "earnings beat", "profit surge", "record profit", "record results",
    "strong results", "beat estimates", "beat expectations", "blowout",
)
_LOW_MATERIALITY_TIERS = {"LOW", "NONE"}
# Above this confidence, a LOW-materiality event's trade thesis is blocked
# outright even without an explicit high-conviction keyword match.
_LOW_MATERIALITY_MAX_CONFIDENCE = 50.0


def validate_evidence_consistency(
    evidence: "DecisionEvidence | None", verdict: dict,
) -> EvidenceConsistencyResult:
    """Fail-closed check: does the LLM's trade thesis (verdict['bull']/['confidence'])
    contradict the structured evidence it was given? This is deterministic, not
    another LLM call — reliability and auditability matter more than nuance here.

    Only checks the LOW-materiality case for now (the demonstrated failure
    mode). A HIGH/MEDIUM-materiality event with a bearish thesis, or other
    direction-mismatch cases, are not yet covered — flagged as a known gap
    rather than silently assumed handled.
    """
    if evidence is None:
        return EvidenceConsistencyResult(
            True, False, [], 0.0,
            "no structured evidence available for this candidate — nothing to validate against",
        )

    materiality = (evidence.materiality or "").upper()
    # Phase 3: scan both `bull` (legacy field) and `thesis` (the explicit
    # canonical-event-grounded field added in engine/agent/decision_engine.py's
    # llm_tooluse_candidate() decide-output) for unsupported high-conviction
    # claims — a model could put the contradiction in either field.
    bull_text   = " ".join(str(verdict.get(k) or "") for k in ("bull", "thesis")).lower()
    confidence  = float(verdict.get("confidence") or 0)

    if materiality in _LOW_MATERIALITY_TIERS:
        unsupported = [kw for kw in _HIGH_CONVICTION_CLAIM_KEYWORDS if kw in bull_text]
        if unsupported or confidence >= _LOW_MATERIALITY_MAX_CONFIDENCE:
            return EvidenceConsistencyResult(
                consistent=False,
                contradiction_detected=bool(unsupported),
                unsupported_claims=unsupported,
                evidence_strength=evidence.confidence,
                reason=(
                    f"event materiality={materiality} (classifier category="
                    f"'{evidence.event_category}') but trade thesis carries "
                    f"confidence={confidence:.0f}%"
                    + (f" and claims unsupported by evidence: {unsupported}" if unsupported else "")
                ),
            )

    return EvidenceConsistencyResult(True, False, [], evidence.confidence, "consistent")
