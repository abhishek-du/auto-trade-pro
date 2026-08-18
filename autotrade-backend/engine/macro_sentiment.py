"""Tier 1 MACRO scorer — LLM sentiment for news FinBERT cannot read.

FinBERT (ProsusAI/finbert) is trained on company financial statements. On
macro and geopolitical text it does one of two things, both verified against
live data for 11-18 Aug 2026 (549 routed-MACRO headlines):

    25.0%  |score| < 0.60  -> silent; never reaches classify_event
    69.4%  |score| >= 0.75 -> loud and WRONG; crosses evaluate_news_flash's
                              trading gate on out-of-domain text

The second number is the dangerous one. "Rupee opens 5 paise lower at 95.48"
scored -0.962 — a 0.05% currency move read as a strong bearish signal. "Gold
and silver prices rise on MCX" scored -0.858 despite describing a rise. These
were not missing signals; they were false ones the system could act on.

So the macro path replaces FinBERT's score for these items rather than
supplementing it. Direction is asked relative to the INDIAN equity market,
which is the only frame the rest of the system trades in — "oil spikes" is
bullish for ONGC and bearish for the Nifty, and a raw sentiment score cannot
express that.

Cost is bounded by construction: the caller dedupes syndicated copies first
(63% of macro volume) and passes a hard cap. Measured budget is ~22 unique
macro headlines/day against a 90 RPM limiter shared with the trade loop.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator

from utils.llm import call_llm_chat
from utils.logger import logger

# Signals weaker than this are treated as no signal. Macro news is mostly
# ambient — most days there is no tradable macro catalyst, and forcing a
# direction onto ambient noise is exactly the FinBERT failure being fixed.
MIN_ACTIONABLE_CONFIDENCE = 0.55


class MacroSentiment(BaseModel):
    """Structured macro read, scoped to the Indian equity market."""

    direction: str = Field(description="BULLISH | BEARISH | NEUTRAL for Indian equities")
    score: float = Field(ge=-1.0, le=1.0, description="signed strength, negative = bearish")
    confidence: float = Field(ge=0.0, le=1.0)
    theme: str = Field(default="OTHER", description="OIL|RATES|CURRENCY|GEOPOLITICS|TRADE|GROWTH|OTHER")
    sectors_hit: list[str] = Field(default_factory=list)
    reasoning: str = Field(default="")

    @field_validator("direction", mode="before")
    @classmethod
    def _norm_direction(cls, v):
        v = str(v or "NEUTRAL").strip().upper()
        return v if v in {"BULLISH", "BEARISH", "NEUTRAL"} else "NEUTRAL"

    @field_validator("theme", mode="before")
    @classmethod
    def _norm_theme(cls, v):
        v = str(v or "OTHER").strip().upper()
        allowed = {"OIL", "RATES", "CURRENCY", "GEOPOLITICS", "TRADE", "GROWTH", "OTHER"}
        return v if v in allowed else "OTHER"

    @field_validator("sectors_hit", mode="before")
    @classmethod
    def _norm_sectors(cls, v):
        if not isinstance(v, list):
            return []
        return [str(s).strip().upper() for s in v if str(s).strip()][:6]

    @property
    def is_actionable(self) -> bool:
        return self.direction != "NEUTRAL" and self.confidence >= MIN_ACTIONABLE_CONFIDENCE


_SYS_PROMPT = """You are a macro analyst for an INDIAN equity trading desk.

You are given one macro, geopolitical, rates, currency or commodity headline.
Judge its effect on the INDIAN equity market (Nifty 50 / Sensex) over the next
1-3 trading sessions.

Rules:
- Direction is from the INDIAN MARKET's point of view, not the asset's. Rising
  crude is BEARISH for Indian equities (India imports ~85% of its oil) even
  though it is bullish for oil producers.
- Judge the NEWS, not the words. "Rupee opens 5 paise lower" is a 0.05% move —
  that is NEUTRAL with low confidence, not bearish. Reserve strong scores for
  genuine catalysts.
- If the headline reports a move that has ALREADY happened and is small or
  routine, return NEUTRAL. Most macro headlines are ambient, not tradable.
- confidence reflects how sure you are this moves Indian equities at all.

Respond with ONLY a JSON object, no prose, no markdown fence:
{"direction":"BULLISH|BEARISH|NEUTRAL","score":-1.0..1.0,"confidence":0.0..1.0,
 "theme":"OIL|RATES|CURRENCY|GEOPOLITICS|TRADE|GROWTH|OTHER",
 "sectors_hit":["OIL_GAS","BANKING",...],"reasoning":"one short sentence"}

score must agree in sign with direction (BEARISH negative, BULLISH positive,
NEUTRAL near 0)."""


def _extract_json(text: str) -> dict | None:
    """Pull the JSON object out of a response.

    nemotron intermittently wraps output in a ```json fence or prefixes a
    sentence — the same slippage classify_event already retries around.
    """
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def score_macro_headline(headline: str, summary: str | None = None) -> MacroSentiment | None:
    """Score one macro headline, confirming any actionable reading.

    Returns None if the LLM is unavailable. None means "no macro read", and the
    caller must treat that as no signal — never as neutral-and-confident. A
    silent failure here degrades to the pre-router behaviour (macro ignored),
    which is the safe direction.

    Why the second sample
    ---------------------
    Measured on the live model, 2026-08-18. Scoring "Gold and silver prices
    rise on MCX on a weaker dollar; US-Iran stalled talks" six times gave
    NEUTRAL/conf 0.30-0.40 five times and once gave BULLISH +0.80 at
    confidence 0.90 — justified by "positive sentiment around AI and tech
    innovation", which appears nowhere in the headline. A ~1-in-6 fabrication
    that lands on is_actionable=True is not acceptable for something feeding a
    market-wide read.

    The confirmation is only spent when the first sample is ACTIONABLE. Macro
    news is overwhelmingly ambient, so in the common case this costs nothing
    extra; the budget is only spent on the readings that can move the system,
    which are exactly the ones worth being sure about.
    """
    first = await _score_once(headline, summary)
    if first is None or not first.is_actionable:
        return first

    second = await _score_once(headline, summary)
    if second is None:
        # Cannot confirm. Keep the reading but strip its authority rather than
        # discarding it — an unconfirmed signal is weak evidence, not zero.
        first.confidence = min(first.confidence, MIN_ACTIONABLE_CONFIDENCE - 0.05)
        return first

    if second.direction != first.direction:
        logger.info(
            f"[macro_sentiment] unconfirmed ({first.direction}/{first.score:+.2f} then "
            f"{second.direction}/{second.score:+.2f}) — downgrading to NEUTRAL: '{headline[:60]}'"
        )
        first.direction  = "NEUTRAL"
        first.score      = 0.0
        first.confidence = min(first.confidence, 0.3)
        return first

    # Both agree on direction. Take the more conservative magnitude so a single
    # over-confident sample cannot set the level on its own.
    first.score      = min(first.score, second.score, key=abs)
    first.confidence = min(first.confidence, second.confidence)
    return first


async def _score_once(headline: str, summary: str | None = None) -> MacroSentiment | None:
    """One LLM round-trip. See score_macro_headline for the calling contract."""
    if not headline or not headline.strip():
        return None

    user = f"Headline: {headline}"
    if summary:
        user += f"\n\nContext: {summary[:600]}"

    messages = [
        {"role": "system", "content": _SYS_PROMPT},
        {"role": "user", "content": user},
    ]

    # Two attempts only. classify_event uses four, but it is gating whether a
    # material company event exists at all; a missed macro read costs far less
    # than holding up a bounded crawl budget.
    for attempt in range(2):
        try:
            raw = await call_llm_chat(messages, max_tokens=400, temperature=0.1)
        except Exception as exc:
            logger.warning(f"[macro_sentiment] LLM call failed for '{headline[:60]}': {exc}")
            return None

        if not raw:
            if attempt == 0:
                continue
            logger.info(f"[macro_sentiment] no LLM response for '{headline[:60]}'")
            return None

        data = _extract_json(raw)
        if data is None:
            if attempt == 0:
                continue
            logger.warning(f"[macro_sentiment] unparseable response for '{headline[:60]}': {raw[:120]}")
            return None

        try:
            result = MacroSentiment(**data)
        except Exception as exc:
            if attempt == 0:
                continue
            logger.warning(f"[macro_sentiment] invalid schema for '{headline[:60]}': {exc}")
            return None

        # Enforce sign agreement rather than trusting it. An LLM that says
        # BEARISH with score +0.8 is contradicting itself, and downstream code
        # keys off the sign.
        if result.direction == "BEARISH" and result.score > 0:
            result.score = -result.score
        elif result.direction == "BULLISH" and result.score < 0:
            result.score = -result.score
        elif result.direction == "NEUTRAL":
            result.score = 0.0

        return result

    return None
