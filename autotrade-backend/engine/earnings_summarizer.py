"""AI Earnings Call Summarizer for AutoTrade Pro.

Orchestrates transcript fetching → PDF extraction → gpt-oss-120b summarization.
Caches results in the EarningsCallSummary DB table.

Public API
----------
get_earnings_summary(symbol, quarter, session) -> EarningsSummary | None
summarize_transcript(...)                      -> EarningsSummary
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class EarningsSummary:
    symbol:               str
    company_name:         str
    quarter:              str
    call_date:            str
    pdf_url:              str
    source:               str

    financial_highlights: list = field(default_factory=list)
    management_guidance:  list = field(default_factory=list)
    key_risks:            list = field(default_factory=list)
    analyst_questions:    list = field(default_factory=list)
    strategic_updates:    list = field(default_factory=list)

    revenue_guidance:     Optional[str] = None
    margin_guidance:      Optional[str] = None
    capex_guidance:       Optional[str] = None
    dividend_info:        Optional[str] = None
    management_tone:      str = "NEUTRAL"
    tone_reason:          str = ""

    ai_confidence:        str = "MEDIUM"
    transcript_length:    int = 0
    is_ai_generated:      bool = False
    generated_at:         str = ""
    word_count:           int = 0

    def to_dict(self) -> dict:
        return {
            "symbol":               self.symbol,
            "company_name":         self.company_name,
            "quarter":              self.quarter,
            "call_date":            self.call_date,
            "pdf_url":              self.pdf_url,
            "source":               self.source,
            "financial_highlights": self.financial_highlights,
            "management_guidance":  self.management_guidance,
            "key_risks":            self.key_risks,
            "analyst_questions":    self.analyst_questions,
            "strategic_updates":    self.strategic_updates,
            "revenue_guidance":     self.revenue_guidance,
            "margin_guidance":      self.margin_guidance,
            "capex_guidance":       self.capex_guidance,
            "dividend_info":        self.dividend_info,
            "management_tone":      self.management_tone,
            "tone_reason":          self.tone_reason,
            "ai_confidence":        self.ai_confidence,
            "transcript_length":    self.transcript_length,
            "is_ai_generated":      self.is_ai_generated,
            "generated_at":         self.generated_at,
            "word_count":           self.word_count,
        }


# ── System prompt ─────────────────────────────────────────────────────────────

def get_summarizer_system_prompt() -> str:
    return (
        "You are an expert Indian equity research analyst specialising in earnings call "
        "analysis for NSE-listed companies.\n\n"
        "You have deep knowledge of:\n"
        "- Indian accounting standards (Ind AS)\n"
        "- Indian financial terminology (crores, lakhs, EBITDA margins, PAT)\n"
        "- Sector dynamics: IT services, banking (NIM, CASA), pharma (ANDA), FMCG\n"
        "- Management communication patterns in Indian companies\n"
        "- What institutional investors look for in concalls\n\n"
        "When analysing transcripts:\n"
        "- Extract actual numbers, not vague statements\n"
        "- Identify what management committed to vs what they hedged\n"
        "- Note changes in guidance vs previous quarter\n"
        "- Flag red flags: declining margins, attrition, client concentration\n"
        "- Note positive signals: deal wins, market share gains, new verticals\n"
        "- Distinguish between management remarks and analyst Q&A\n\n"
        "Format all monetary values in Indian system: Crores (₹X,XXX Cr), Lakhs (₹XX L)."
    )


# Removed obsolete Groq helper functions.


# ── Rule-based fallback ───────────────────────────────────────────────────────

def _rule_based_summary(
    text: str, symbol: str, company_name: str,
    quarter: str, call_date: str, pdf_url: str, source: str,
    word_count: int, transcript_length: int,
    llm_reason: str = "",
) -> EarningsSummary:
    """Basic regex extraction when LLM is unavailable or rate-limited."""
    highlights = []

    revenue_matches = re.findall(
        r'revenue[^.]*?₹?\s*(\d[\d,\.]+)\s*(crore|cr|lakh|billion)',
        text[:5000].lower()
    )
    growth_matches = re.findall(
        r'(\d+\.?\d*)\s*%\s*(growth|increase|rise|jump)',
        text[:5000].lower()
    )

    if revenue_matches:
        r = revenue_matches[0]
        highlights.append(f"Revenue mentioned: ₹{r[0]} {r[1]}")
    if growth_matches:
        g = growth_matches[0]
        highlights.append(f"Growth figure: {g[0]}% {g[1]}")
    highlights.append(f"Transcript available: {word_count:,} words from {source}")

    llm_configured = getattr(settings, "mantle_available", False)
    if llm_configured:
        ai_note = llm_reason or "AI rate-limited — try refreshing in a few minutes"
    else:
        ai_note = "Add MANTLE_API_KEY to .env for full AI analysis"

    highlights.append(ai_note)
    while len(highlights) < 5:
        highlights.append("Full AI analysis pending — refresh to retry")

    pending_msg = "Refresh page to retry AI analysis" if llm_configured else "Configure MANTLE_API_KEY in .env"

    return EarningsSummary(
        symbol=symbol, company_name=company_name,
        quarter=quarter, call_date=call_date,
        pdf_url=pdf_url, source=source,
        financial_highlights=highlights,
        management_guidance=[pending_msg],
        key_risks=[pending_msg],
        analyst_questions=[pending_msg],
        strategic_updates=[pending_msg],
        revenue_guidance=None, margin_guidance=None,
        capex_guidance=None, dividend_info=None,
        management_tone="NEUTRAL",
        tone_reason="Rule-based extraction — AI tone analysis pending",
        ai_confidence="LOW",
        transcript_length=transcript_length,
        is_ai_generated=False,
        generated_at=datetime.utcnow().isoformat(),
        word_count=word_count,
    )


# ── Core AI summarizer ────────────────────────────────────────────────────────

async def summarize_transcript(
    transcript_text: str,
    symbol: str,
    company_name: str,
    quarter: str,
    call_date: str,
    pdf_url: str,
    source: str,
) -> EarningsSummary:
    word_count         = len(transcript_text.split())
    transcript_length  = len(transcript_text)

    # Keep first 70% + last 30% for very long transcripts
    MAX_CHARS = 80_000
    if len(transcript_text) > MAX_CHARS:
        first  = transcript_text[:int(MAX_CHARS * 0.70)]
        last   = transcript_text[-int(MAX_CHARS * 0.30):]
        transcript_text = first + "\n...[MIDDLE SECTION TRUNCATED]...\n" + last
        logger.info(f"[earnings] Truncated transcript from {transcript_length} to {MAX_CHARS} chars")

    prev_context = ""
    try:
        from db.database import AsyncSessionLocal
        from db.models import EarningsCallSummary
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            prev_call = (await session.execute(
                select(EarningsCallSummary.management_tone, EarningsCallSummary.tone_reason)
                .where(EarningsCallSummary.symbol == symbol)
                .order_by(EarningsCallSummary.call_date.desc())
                .limit(1)
            )).first()
            if prev_call:
                prev_context = f"\n\nPRIOR QUARTER CONTEXT: The CEO's tone last quarter was {prev_call[0]} ({prev_call[1]}). Please compare the current tone against this baseline, specifically noting if they are using more hedging words (like 'uncertainty', 'headwinds')."
    except Exception:
        pass

    prompt = f"""Analyse this earnings call transcript for {company_name} ({symbol}) — {quarter}.
Call Date: {call_date}{prev_context}

TRANSCRIPT:
{transcript_text}

Extract the following and respond ONLY with valid JSON — no markdown, no explanation outside JSON:

{{
  "financial_highlights": [
    "5 bullets with specific numbers. E.g. 'Revenue grew 12.4% YoY to ₹38,000 Cr; PAT up 18% to ₹7,200 Cr'",
    "Gross/EBITDA/PAT margins with basis point changes vs prior year",
    "Key segment performance breakdown with numbers",
    "Balance sheet highlight: cash, debt, FCF, return ratios",
    "Key operating metric (IT=headcount/utilisation, bank=NIM/CASA, pharma=ANDA filings)"
  ],
  "management_guidance": [
    "Forward guidance with specific range. E.g. 'FY27 revenue growth guided at 10-12% in CC terms'",
    "Margin outlook with specific range",
    "Capex plan: amount, purpose, timeline",
    "Key strategic initiative with expected timeline"
  ],
  "key_risks": [
    "Macro risk mentioned. E.g. 'US banking sector macro uncertainty affecting deal velocity'",
    "Margin pressure sources with specifics",
    "Competitive or regulatory headwind",
    "Balance sheet or working capital concern if any"
  ],
  "analyst_questions": [
    "Most important analyst concern raised in Q&A section",
    "Second key concern from analysts",
    "Third key concern from analysts"
  ],
  "strategic_updates": [
    "Strategic development. E.g. 'Acquired XYZ for ₹1,200 Cr to enter cloud segment'",
    "Second strategic update",
    "Third strategic update or partnership"
  ],
  "revenue_guidance": "Specific revenue guidance if stated, else null",
  "margin_guidance": "Specific margin guidance if stated, else null",
  "capex_guidance": "Specific capex guidance if stated, else null",
  "dividend_info": "Dividend declared if any (amount + record date), else null",
  "management_tone": "OPTIMISTIC or CAUTIOUS or NEUTRAL or NEGATIVE",
  "tone_reason": "One sentence: specific evidence from transcript supporting this tone",
  "tone_comparison_vs_last_quarter": "Analysis of CEO tone shift vs prior quarter (hedging words, confidence changes) under 30 words, or null if no prior context provided.",
  "ai_confidence": "HIGH if transcript clear and complete, MEDIUM if partial, LOW if very short"
}}

RULES:
- Every bullet MUST contain at least one specific number or percentage
- Do NOT use vague language without supporting numbers
- If information absent: write 'Not mentioned in this call'
- Use Indian format: Crores not Millions (unless company uses USD)
- Each bullet under 120 words"""

    if not getattr(settings, "mantle_available", False):
        return _rule_based_summary(
            transcript_text, symbol, company_name,
            quarter, call_date, pdf_url, source,
            word_count, transcript_length,
        )

    from utils.llm import call_llm_chat
    reply = await call_llm_chat(
        [
            {"role": "system", "content": get_summarizer_system_prompt()},
            {"role": "user", "content": prompt}
        ],
        max_tokens=2000, temperature=0.2
    )

    if not reply:
        return _rule_based_summary(
            transcript_text, symbol, company_name,
            quarter, call_date, pdf_url, source,
            word_count, transcript_length,
        )

    # Clean markdown fences
    reply = re.sub(r'```json\s*', '', reply)
    reply = re.sub(r'```\s*', '', reply)

    try:
        data = json.loads(reply)
    except json.JSONDecodeError:
        m = re.search(r'\{[\s\S]+\}', reply)
        if m:
            try:
                data = json.loads(m.group())
            except Exception:
                logger.error(f"[earnings] Failed to parse gpt-oss JSON: {reply[:300]}")
                return _rule_based_summary(
                    transcript_text, symbol, company_name,
                    quarter, call_date, pdf_url, source,
                    word_count, transcript_length,
                )
        else:
            return _rule_based_summary(
                transcript_text, symbol, company_name,
                quarter, call_date, pdf_url, source,
                word_count, transcript_length,
            )

    def _clean_null(v):
        if v in (None, "null", "None", "not disclosed", "Not mentioned in this call"):
            return None
        return v

    return EarningsSummary(
        symbol=symbol,
        company_name=company_name,
        quarter=quarter,
        call_date=call_date,
        pdf_url=pdf_url,
        source=source,
        financial_highlights=(data.get("financial_highlights") or [])[:5],
        management_guidance=(data.get("management_guidance") or [])[:4],
        key_risks=(data.get("key_risks") or [])[:4],
        analyst_questions=(data.get("analyst_questions") or [])[:3],
        strategic_updates=(data.get("strategic_updates") or [])[:3],
        revenue_guidance=_clean_null(data.get("revenue_guidance")),
        margin_guidance=_clean_null(data.get("margin_guidance")),
        capex_guidance=_clean_null(data.get("capex_guidance")),
        dividend_info=_clean_null(data.get("dividend_info")),
        management_tone=data.get("management_tone", "NEUTRAL"),
        tone_reason=data.get("tone_reason", "") + (" | Vs Last Qtr: " + data["tone_comparison_vs_last_quarter"] if data.get("tone_comparison_vs_last_quarter") else ""),
        ai_confidence=data.get("ai_confidence", "MEDIUM"),
        transcript_length=transcript_length,
        is_ai_generated=True,
        generated_at=datetime.utcnow().isoformat(),
        word_count=word_count,
    )


# ── Long-transcript handler ───────────────────────────────────────────────────

async def summarize_long_transcript(
    transcript_text: str,
    symbol: str,
    company_name: str,
    quarter: str,
    call_date: str,
    pdf_url: str,
    source: str,
) -> EarningsSummary:
    if len(transcript_text) <= 80_000:
        return await summarize_transcript(
            transcript_text, symbol, company_name,
            quarter, call_date, pdf_url, source,
        )

    if not getattr(settings, "mantle_available", False):
        return _rule_based_summary(
            transcript_text, symbol, company_name, quarter,
            call_date, pdf_url, source,
            len(transcript_text.split()), len(transcript_text),
        )

    from crawler.earnings_crawler import chunk_transcript
    chunks = chunk_transcript(transcript_text)

    intermediates = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(3)  # avoid back-to-back 429s on free tier
        from utils.llm import call_llm_chat
        reply = await call_llm_chat(
            [
                {"role": "system", "content": "You are a financial analyst summarizing an Indian earnings call transcript."},
                {"role": "user", "content": (
                    f"Summarize section {i+1}/{len(chunks)} of the {company_name} earnings call "
                    f"in 200 words, preserving all numbers and percentages:\n\n{chunk[:60_000]}"
                )}
            ],
            max_tokens=400, temperature=0.2
        )
        if reply:
            intermediates.append(reply)

    combined = "\n\n".join(intermediates) if intermediates else transcript_text[:80_000]
    return await summarize_transcript(
        combined, symbol, company_name, quarter, call_date, pdf_url, source
    )


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_cached_summary(
    symbol: str, quarter: str | None, session: AsyncSession
) -> EarningsSummary | None:
    from db.models import EarningsCallSummary

    q = select(EarningsCallSummary).where(EarningsCallSummary.symbol == symbol)
    if quarter:
        q = q.where(EarningsCallSummary.quarter == quarter.upper())
    q = q.order_by(EarningsCallSummary.created_at.desc()).limit(1)

    row = (await session.execute(q)).scalar_one_or_none()
    if not row:
        return None

    return EarningsSummary(
        symbol=row.symbol,
        company_name=row.company_name,
        quarter=row.quarter,
        call_date=row.call_date,
        pdf_url=row.pdf_url,
        source=row.source,
        financial_highlights=row.financial_highlights or [],
        management_guidance=row.management_guidance or [],
        key_risks=row.key_risks or [],
        analyst_questions=row.analyst_questions or [],
        strategic_updates=row.strategic_updates or [],
        revenue_guidance=row.revenue_guidance,
        margin_guidance=row.margin_guidance,
        capex_guidance=row.capex_guidance,
        dividend_info=row.dividend_info,
        management_tone=row.management_tone,
        tone_reason=row.tone_reason,
        ai_confidence=row.ai_confidence,
        transcript_length=row.transcript_length,
        is_ai_generated=row.is_ai,
        generated_at=row.created_at.isoformat(),
        word_count=row.word_count,
    )


async def _cache_summary(summary: EarningsSummary, session: AsyncSession) -> None:
    from db.models import EarningsCallSummary
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    vals = {
        "symbol":               summary.symbol,
        "company_name":         summary.company_name,
        "quarter":              summary.quarter,
        "call_date":            summary.call_date,
        "pdf_url":              summary.pdf_url,
        "source":               summary.source,
        "financial_highlights": summary.financial_highlights,
        "management_guidance":  summary.management_guidance,
        "key_risks":            summary.key_risks,
        "analyst_questions":    summary.analyst_questions,
        "strategic_updates":    summary.strategic_updates,
        "revenue_guidance":     summary.revenue_guidance,
        "margin_guidance":      summary.margin_guidance,
        "capex_guidance":       summary.capex_guidance,
        "dividend_info":        summary.dividend_info,
        "management_tone":      summary.management_tone,
        "tone_reason":          summary.tone_reason,
        "ai_confidence":        summary.ai_confidence,
        "transcript_length":    summary.transcript_length,
        "word_count":           summary.word_count,
        "is_ai":                summary.is_ai_generated,
    }

    try:
        stmt = pg_insert(EarningsCallSummary).values(**vals)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_earnings_symbol_quarter",
            set_={k: v for k, v in vals.items() if k not in ("symbol", "quarter")},
        )
        await session.execute(stmt)
        await session.commit()
    except Exception:
        # Fallback to plain insert for non-Postgres DBs
        try:
            existing = (await session.execute(
                select(EarningsCallSummary).where(
                    EarningsCallSummary.symbol == summary.symbol,
                    EarningsCallSummary.quarter == summary.quarter,
                )
            )).scalar_one_or_none()
            if existing:
                for k, v in vals.items():
                    if k not in ("symbol", "quarter"):
                        setattr(existing, k, v)
            else:
                session.add(EarningsCallSummary(**vals))
            await session.commit()
        except Exception as exc:
            logger.warning(f"[earnings] Cache save failed: {exc}")


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def get_earnings_summary(
    symbol: str,
    quarter: str | None = None,
    session: AsyncSession | None = None,
    refresh: bool = False,
) -> EarningsSummary | None:
    """End-to-end pipeline: fetch → extract → summarize → cache."""
    if session and not refresh:
        cached = await _get_cached_summary(symbol, quarter, session)
        if cached:
            logger.info(f"[earnings] Cache hit: {symbol} {cached.quarter}")
            return cached

    from crawler.earnings_crawler import get_all_transcripts, extract_transcript_text
    transcripts = await get_all_transcripts(symbol, limit=5)

    if not transcripts:
        logger.warning(f"[earnings] No transcripts found for {symbol}")
        return None

    if quarter:
        target = next(
            (t for t in transcripts if quarter.upper() in (t.get("quarter", "")).upper()),
            transcripts[0],
        )
    else:
        target = transcripts[0]

    logger.info(f"[earnings] Fetching PDF: {target['pdf_url'][:80]}")
    transcript_text = await extract_transcript_text(target["pdf_url"])

    company_name = symbol.replace(".NS", "").replace(".BO", "")
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        company_name = info.get("longName") or info.get("shortName") or company_name
    except Exception:
        pass

    summary = await summarize_long_transcript(
        transcript_text=transcript_text,
        symbol=symbol,
        company_name=company_name,
        quarter=target.get("quarter", ""),
        call_date=target.get("date", ""),
        pdf_url=target["pdf_url"],
        source=target["source"],
    )

    if session and summary:
        await _cache_summary(summary, session)

    return summary
