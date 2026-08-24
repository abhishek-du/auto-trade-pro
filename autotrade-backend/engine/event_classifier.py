import json
import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from utils.config import settings
from utils.logger import logger
from utils.llm import call_llm_chat

# ── NSE's own announcement taxonomy ──────────────────────────────────────────
# When NSE publishes a corporate announcement it files it under a category of
# its own. That category is the exchange's classification of the filing, not a
# model's opinion of the headline, and it measurably carries the direction our
# LLM does not.
#
# Measured over 4,309 NSE announcements (docs/2026-08-24_PHASE3_GROUND_TRUTH_
# NEWS_ALPHA.md), same category, two taxonomies:
#
#     ORDER_WIN, NSE's category   n=84    mean excess +1.053%   win 65.5%
#     ORDER_WIN, our classifier   n=158   mean excess -0.245%   win 37.3%
#
# The strongest bullish category under the exchange's own labels is the
# second-worst under ours. The LLM is not adding information here; it is
# replacing a good label with a bad one. So for NSE-sourced items the category
# decides direction and the LLM keeps only the work it is actually good at —
# sectors, entities, horizon, reasoning.
_NSE_LONG = {
    "Bagging/Receiving of orders/contracts": "ORDER_WIN",
    "Awarding of order(s)/contract(s)":      "ORDER_WIN",
    "Acquisition":                           "ACQUISITION",
    "Amalgamation/Merger":                   "ACQUISITION",
    "Buyback":                               "BUYBACK",
    "Product launch":                        "PRODUCT_LAUNCH",
    "Agreements":                            "MAJOR_PARTNERSHIP",
    "Memorandum of Understanding/Agreements": "MAJOR_PARTNERSHIP",
}
_NSE_SHORT = {
    "Resignation of Director/KMP/SMP":                  "MANAGEMENT_RESIGNATION",
    "Resignation":                                      "MANAGEMENT_RESIGNATION",
    "Resignation of Statutory Auditor":                 "AUDITOR_RESIGNATION",
    "Reasons for Delayed/Non-submission of Financial":  "DELAYED_FILING",
    "Granting/withdrawal/surrender/cancellation/suspe": "REGULATORY_ACTION",
}
# Categories that genuinely carry no direction. "Outcome of Board Meeting" is
# NSE's category for results declarations: it says results were declared, not
# whether they beat. Measured mean excess -0.737% with a 36.3% win rate over
# 1,169 observations — the label is not merely uninformative, acting on it
# loses money. These return NEUTRAL and produce NO directional event.
_NSE_NEUTRAL = {
    "Outcome of Board Meeting", "Press Release", "Press Release (Revised)",
    "Dividend", "Reply to Clarification- Financial results",
    "Clarification - Financial Results", "Monthly Business Updates",
    "Preferential issue", "Rights Issue", "Scheme of Arrangement", "Demerger",
}
# Two categories measured NEGATIVE as longs and are deliberately not traded
# long: CORPORATE_RESTRUCTURE (mean -0.975%, CI [-1.891, -0.136], n=23) and
# MAJOR_PARTNERSHIP (mean -0.319%, n=39). Scheme of Arrangement and Demerger
# are therefore in the neutral set above rather than in _NSE_LONG.

# Only these sources file under NSE's taxonomy. Gating on source rather than on
# the category string alone means an unrelated feed whose category happens to
# collide with an exchange label cannot flip a direction.
_EXCHANGE_FILING_SOURCES = {"NSE-Announcements"}

_RATING_UP   = re.compile(r"\b(upgrad|revised upward|improve|positive outlook)", re.I)
_RATING_DOWN = re.compile(r"\b(downgrad|revised downward|negative outlook|default|watch with negative)", re.I)


def resolve_nse_direction(nse_category: str | None, text: str = "") -> tuple[str, str] | None:
    """NSE category -> ('LONG'|'SHORT'|'NEUTRAL', our category name).

    Returns None when the category is outside the table, which means "we have
    no exchange-supplied opinion" — the caller should then keep whatever the
    LLM said rather than inventing a direction.

    Credit-rating direction comes from the announcement text, never from the
    bare category: "Credit Rating" alone does not say which way.
    """
    cat = (nse_category or "").strip()
    if not cat:
        return None
    if cat.startswith("Credit Rating"):
        if _RATING_DOWN.search(text):
            return ("SHORT", "RATING_DOWNGRADE")
        if _RATING_UP.search(text):
            return ("LONG", "RATING_UPGRADE")
        return ("NEUTRAL", "RATING_UNCLEAR")
    if cat in _NSE_LONG:
        return ("LONG", _NSE_LONG[cat])
    if cat in _NSE_SHORT:
        return ("SHORT", _NSE_SHORT[cat])
    if cat in _NSE_NEUTRAL:
        return ("NEUTRAL", "ROUTINE_DISCLOSURE")
    return None

class EventClassification(BaseModel):
    # Required — the fields _build_evidence()/DecisionEvidence.from_classification()
    # actually need to build a usable CausalEvent/trade decision. If the model
    # can't reliably produce even these, the classification genuinely isn't
    # usable and SHOULD fail (return None) — that's a correct "no event, no
    # trade", not a bug.
    category: str = Field(default="UNKNOWN", description="Category of news (e.g. ORDER_WIN, EARNINGS_BEAT, REGULATORY_APPROVAL, MACRO_EVENT, RUMOR, MANAGEMENT_INTERVIEW)")
    impact: str = Field(default="LOW", description="Impact level: HIGH, MEDIUM, LOW")
    confidence: float = Field(default=0.0, description="Confidence in this classification from 0.0 to 1.0")
    bullish: bool = Field(default=False, description="True if bullish, False if bearish")
    entities: dict = Field(default_factory=dict, description="Affected entities: {'companies': [], 'sectors': [], 'countries': []}")

    subcategories: list | None = Field(default_factory=list, description="List of subcategories (e.g. ['GOVERNMENT', 'INFRASTRUCTURE'])")
    time_horizon: str | None = Field(default="UNKNOWN", description="Expected time horizon (e.g. '2_5_DAYS', 'WEEKS')")
    expected_half_life_hours: int | float | None = Field(default=48, description="Exponential decay half-life in hours")
    reasoning: str | None = Field(default="", description="Reasoning behind the classification")
    surprise_score: int | float | None = Field(default=50, description="Impact score from 1 to 100 representing market surprise")
    is_new_information: bool | None = Field(default=True, description="Is this genuinely new information or circulating old news?")
    market_priced_in: float | None = Field(default=0.0, description="Estimated % of how much the market has already priced this in (0.0 to 1.0)")
    source_reliability: float | None = Field(default=0.7, description="Reliability of the source (0.0 to 1.0) e.g., NSE=1.0, Rumor=0.3")

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data):
        """Treat an explicit JSON `null` exactly like an absent key.

        A pydantic default only applies when the key is MISSING. When the model
        emits the key with a null value the default is bypassed and validation
        fails outright — observed live 2026-08-17 on the required-but-defaulted
        fields, e.g. `{"bullish": null}` -> "Input should be a valid boolean
        [input_value=None]". classify_event() treats that as malformed JSON and
        retries the whole call up to 4 times before giving up and returning
        None, so one null field burned four LLM round-trips AND still dropped a
        genuine catalyst ("no event, no trade").

        Stripping nulls here lets the declared defaults do their job while
        keeping the field types non-Optional for consumers
        (_build_evidence/DecisionEvidence.from_classification read them
        directly and would otherwise need None-guards at every use).
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data

def _direction_contradicts_sentiment(bullish: bool, score: float | None) -> bool:
    """True when a confident LLM direction disagrees with a confident FinBERT score.

    WHY THIS EXISTS (2026-08-21)
    ----------------------------
    Two sugar headlines were ingested that day:
        "Sugar stocks fall over 7% as govt's duty-free import move sparks price woes"
        "Sugar stocks ... tumble up to 5% as govt allows duty-free imports"
    Both scored -0.96 on FinBERT. From those two headlines the pipeline produced
    SEVEN consecutive BULLISH CausalEvents, while the sector fell 3-7%. Re-running
    classify_event on the identical headlines afterwards returned bullish=False
    both times -- so the model is not systematically wrong here, it is
    NON-DETERMINISTIC, and a single bad roll authorised a whole sector's worth of
    long signals.

    A second, independent read is the cheap defence. FinBERT is already computed
    for every headline, costs nothing extra, and is deterministic.

    Only fires when BOTH sides are confident: a weak score says nothing, and this
    must not veto ordinary events over sentiment noise.
    """
    if score is None:
        return False
    try:
        s = float(score)
    except (TypeError, ValueError):
        return False
    if abs(s) < float(getattr(settings, "EVENT_SENTIMENT_CONFLICT_MIN", 0.70)):
        return False
    return (bullish and s < 0) or ((not bullish) and s > 0)


async def classify_event(
    headline: str,
    summary: str | None = None,
    sentiment_score: float | None = None,
    nse_category: str | None = None,
    source: str | None = None,
) -> EventClassification | None:
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
        from utils.llm import mantle_breaker_remaining as _breaker_remaining
        import asyncio as _asyncio
        import re as _re

        for _attempt in range(4):
            # ── 1. Get a response, retrying across a transient circuit-breaker
            # window — a dropped event here means "no canonical event → no
            # trade" for a possibly-material catalyst, which was the #1
            # coverage gap (500+ classifications/day lost to breaker blips).
            # Only wait+retry when the breaker is actually OPEN; a plain None
            # for any other reason is given up on immediately (and, with
            # mocked LLMs in tests, the breaker is never open, so behavior
            # there is unchanged).
            response_text = await call_llm_chat(messages, max_tokens=2500, temperature=0.1)
            if not response_text:
                # Root-caused 2026-07-23: this used to be completely silent,
                # which is why a Bedrock circuit-breaker cascade (utils/llm.py)
                # killing 45-50 candidates in a row looked identical to
                # routine classification misses in the logs. call_llm_chat()
                # returns falsy for two reasons: the circuit breaker is open
                # (blocking ALL calls, not just this one), or a genuine
                # empty-content response survived both of call_mantle_chat's
                # internal retries.
                remaining = _breaker_remaining()
                if remaining <= 0 or _attempt == 3:
                    logger.warning(f"[event_classifier] classify_event: no response for '{headline[:60]}' (LLM call failed or circuit breaker open)")
                    return None
                wait = min(remaining + 0.5, 20.0)
                logger.info(
                    f"[event_classifier] LLM breaker open — retry {_attempt + 1}/4 for "
                    f"'{headline[:50]}' in {wait:.1f}s (so a material event isn't dropped)"
                )
                await _asyncio.sleep(wait)
                continue

            # ── 2. Parse + validate — retry on malformed JSON too (2026-07-27
            # forensic): nemotron occasionally produces JSON with a genuine
            # syntax slip (a missing comma, a stray control character embedded
            # raw in a string value) rather than an empty response, which used
            # to hard-fail classify_event with no retry at all -- a second
            # attempt at temperature=0.1 usually comes back clean.
            match = _re.search(r'```(?:json)?\s*(.*?)\s*```', response_text, _re.DOTALL)
            cleaned = match.group(1) if match else response_text.replace("```json", "").replace("```", "").strip()
            try:
                # strict=False tolerates literal control characters (raw
                # newlines/tabs) inside string values instead of requiring
                # them escaped as \n/\t -- the json module's default (strict
                # =True) rejects those outright, which was one of the
                # observed live failure modes ("Invalid control character").
                data = json.loads(cleaned, strict=False)
                _cls = EventClassification(**data)

                # ── The exchange's own label outranks the model's ────────────
                # Applied BEFORE the sentiment check below, and it short-
                # circuits it: that check exists to catch a model whose
                # direction is unverifiable, and NSE's category is not the
                # model's opinion. Letting FinBERT sentiment veto the
                # exchange's own filing category would be the tail wagging
                # the dog.
                # The source check lives HERE, not at the call site, so a future
                # caller cannot accidentally hand an RSS feed's `category` field
                # to a table built for exchange filings. Every other source's
                # category vocabulary is unrelated, and the measurement behind
                # this table covers NSE filings only.
                _nse = (
                    resolve_nse_direction(nse_category, f"{headline} {summary or ''}")
                    if source in _EXCHANGE_FILING_SOURCES else None
                )
                if _nse is not None:
                    _dir, _cat = _nse
                    if _dir == "NEUTRAL":
                        # No directional claim. The news item is still stored by
                        # the caller; only the tradable event is withheld, which
                        # is the correct NO EVENT -> NO TRADE outcome.
                        logger.info(
                            f"[event_classifier] NSE category '{nse_category}' carries no "
                            f"direction — no event emitted: '{headline[:60]}'"
                        )
                        return None
                    _was = "BULLISH" if _cls.bullish else "BEARISH"
                    _now = "BULLISH" if _dir == "LONG" else "BEARISH"
                    if _was != _now:
                        logger.info(
                            f"[event_classifier] NSE category '{nse_category}' overrides LLM "
                            f"{_was} -> {_now}: '{headline[:60]}'"
                        )
                    _cls.bullish = (_dir == "LONG")
                    _cls.category = _cat
                    # The exchange is the primary source; this is what
                    # source_reliability is for.
                    _cls.source_reliability = 1.0
                    return _cls

                # Deterministic second opinion. See
                # _direction_contradicts_sentiment for the 2026-08-21 incident:
                # two headlines scoring -0.96 produced seven BULLISH events
                # while the sector fell 3-7%.
                #
                # REFUSES rather than flipping. Under NO EVENT -> NO TRADE, no
                # event is a safe outcome; overriding the model with FinBERT
                # would just swap one unverified direction for another, and
                # FinBERT is documented in this codebase as out-of-domain on
                # some text. When two independent reads disagree confidently,
                # the honest answer is that we do not know.
                if _direction_contradicts_sentiment(_cls.bullish, sentiment_score):
                    logger.warning(
                        f"[event_classifier] direction conflict — LLM says "
                        f"{'BULLISH' if _cls.bullish else 'BEARISH'} but sentiment "
                        f"score is {sentiment_score:+.2f}; refusing to classify: "
                        f"'{headline[:70]}'"
                    )
                    return None
                return _cls
            except Exception as parse_exc:
                if _attempt < 3:
                    logger.info(
                        f"[event_classifier] malformed JSON for '{headline[:50]}' "
                        f"(attempt {_attempt + 1}/4): {parse_exc} — retrying"
                    )
                    continue
                logger.error(f"[event_classifier] Failed to classify '{headline}' after retries: {parse_exc}")
                return None
        return None
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


# ─────────────────────────────────────────────────────────────────────────────
# Sector-level fallback (2026-08-20)
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY: on 19-20 Aug 2026 the crawler ingested five sugar/ethanol headlines with
# tickers correctly extracted and sentiment up to 0.89, and `classify_event`
# returned None for every one of them -- so zero CausalEvents were created and,
# under NO EVENT -> NO TRADE, the news engine could not act while the sector
# rallied up to 16%. The classifier is built around single-company events
# (earnings, orders, M&A); a story about a whole sector matches no category.
#
# WHY NOT THE OBVIOUS DESIGN: the brief specified "look up each ticker's sector,
# fire if they all match". Measured, that is unsafe here -- `NSE_SECTOR_MAP` has
# 59 entries, `india_specific.SECTOR_MAP` has 18, `market_shortlist.sector` is
# empty for 193 of 206 symbols, and NONE of them contain a single sugar name.
# Every unmapped ticker resolves to "Other", so "all the same sector" would be
# TRUE for any three unrelated unmapped companies. That would mint spurious
# sector events -- and a CausalEvent is what AUTHORISES a trade.
#
# So the theme is read from the HEADLINE, which is the one place the sector is
# actually stated, and the check FAILS CLOSED: no recognised theme, no event.

SECTOR_THEME_KEYWORDS: dict[str, str] = {
    "sugar": "Sugar", "ethanol": "Sugar", "sugarcane": "Sugar", "cane": "Sugar",
    "bank": "Banking", "banking": "Banking", "lender": "Banking", "nbfc": "Banking",
    "it stocks": "IT", "software": "IT", "tech stocks": "IT",
    "pharma": "Pharma", "drugmaker": "Pharma", "api": "Pharma",
    "fmcg": "FMCG", "consumer goods": "FMCG",
    "auto": "Auto", "automobile": "Auto", "carmaker": "Auto", "two-wheeler": "Auto",
    "metal": "Metals", "steel": "Metals", "aluminium": "Metals", "zinc": "Metals",
    "oil": "Energy", "gas": "Energy", "power": "Energy", "refiner": "Energy",
    "cement": "Cement",
    "realty": "Realty", "real estate": "Realty", "housing": "Realty",
    "textile": "Textiles", "fertiliser": "Fertilisers", "fertilizer": "Fertilisers",
    "airline": "Aviation", "aviation": "Aviation",
    "defence": "Defence", "shipping": "Shipping", "paper": "Paper",
    "tyre": "Tyres", "chemical": "Chemicals", "specialty chemical": "Chemicals",
}

# Plural/collective cues. A sector story says "sugar STOCKS rally", not
# "Balrampur Chini rallies" -- requiring one of these keeps single-company news
# out of the sector path even when the company happens to be in a themed industry.
_COLLECTIVE_CUES = ("stocks", "shares", "sector", "counters", "names", "pack",
                    "index", "companies", "mills", "makers", "firms")


def detect_sector_theme(headline: str) -> str | None:
    """The sector a headline is about, or None if it is not a sector story.

    Requires BOTH a sector keyword and a collective cue, so
    "Sugar stocks rally 14%" matches and "Balrampur Chini Q1 profit up" does not.
    Returns None on anything ambiguous -- the caller must treat None as
    "create no event".
    """
    if not headline:
        return None
    low = headline.lower()
    if not any(cue in low for cue in _COLLECTIVE_CUES):
        return None
    for kw, sector in SECTOR_THEME_KEYWORDS.items():
        if kw in low:
            return sector
    return None
