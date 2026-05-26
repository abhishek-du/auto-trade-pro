# IPO analysis engine — Groq AI verdict + rule-based fallback.

import json
import re
from typing import Any

import httpx

from utils.config import settings
from utils.logger import logger

_GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.1-8b-instant"
_TIMEOUT    = 15.0
_MAX_TOKENS = 600


async def _call_groq(prompt: str) -> str | None:
    if not settings.groq_available:
        return None
    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    body = {
        "model":      _GROQ_MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages":   [{"role": "user", "content": prompt}],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_GROQ_URL, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("Groq call failed: %s", exc)
        return None


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_ipo_prompt(ipo: dict) -> str:
    name       = ipo.get("company_name") or ipo.get("name", "Unknown")
    price      = ipo.get("price_display", "TBA")
    size       = ipo.get("issue_size_cr", 0)
    ipo_type   = ipo.get("ipo_type", "EQ")
    gmp_pct    = ipo.get("gmp_pct")
    sub_total  = (ipo.get("subscription") or {}).get("total")
    sector     = ipo.get("sector") or ipo.get("industry", "")
    promoter   = ipo.get("promoter_holding_post") or ipo.get("promoter_holding", "")
    lot_size   = ipo.get("lot_size") or ipo.get("lotSize", "")
    min_invest = ipo.get("min_investment") or ipo.get("minInvestment", "")

    gmp_line   = f"GMP: {gmp_pct:.1f}% ({'+' if gmp_pct > 0 else ''}{ipo.get('gmp_inr', 0):.0f})" if gmp_pct else "GMP: N/A"
    sub_line   = f"Subscription: {sub_total:.2f}x total" if sub_total else "Subscription: Not yet open"

    return f"""Analyse this Indian IPO and respond ONLY with valid JSON (no markdown, no extra text):

Company: {name}
Sector: {sector}
IPO Type: {ipo_type} ({'Mainboard' if ipo_type == 'EQ' else ipo_type})
Price Band: {price}
Issue Size: ₹{size:.0f} Cr
Lot Size: {lot_size}
Min Investment: ₹{min_invest}
Promoter Holding Post-IPO: {promoter}
{gmp_line}
{sub_line}

Respond with this exact JSON structure:
{{
  "verdict": "SUBSCRIBE" | "AVOID" | "NEUTRAL",
  "conviction": "HIGH" | "MEDIUM" | "LOW",
  "score": <integer 1-10>,
  "summary": "<2 sentence overview>",
  "positives": ["<point1>", "<point2>", "<point3>"],
  "concerns":  ["<concern1>", "<concern2>"],
  "strategy": {{
    "listing_play": "<one sentence>",
    "long_term":    "<one sentence>"
  }}
}}"""


# ── AI analysis ────────────────────────────────────────────────────────────────

async def generate_ipo_analysis(ipo: dict) -> dict:
    """Call Groq for structured IPO analysis; fall back to rule-based on failure."""
    prompt = _build_ipo_prompt(ipo)
    raw    = await _call_groq(prompt)

    if raw:
        # Strip any markdown fences if model adds them
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        try:
            data = json.loads(raw)
            data["source"] = "ai"
            return data
        except (json.JSONDecodeError, KeyError):
            logger.warning("Failed to parse Groq IPO response — falling back")

    return generate_rule_based_analysis(ipo)


def generate_rule_based_analysis(ipo: dict) -> dict:
    """Heuristic analysis based on GMP and subscription data."""
    gmp_pct   = ipo.get("gmp_pct") or 0.0
    sub       = ipo.get("subscription") or {}
    sub_total = sub.get("total") or 0.0
    ipo_type  = ipo.get("ipo_type", "EQ")

    score     = 5
    verdict   = "NEUTRAL"
    conviction= "LOW"
    positives = []
    concerns  = []

    # GMP scoring
    if gmp_pct >= 30:
        score    += 2
        positives.append(f"Strong grey market premium of {gmp_pct:.1f}% signals positive market sentiment")
    elif gmp_pct >= 10:
        score    += 1
        positives.append(f"Positive GMP of {gmp_pct:.1f}% indicates market interest")
    elif gmp_pct < 0:
        score    -= 2
        concerns.append(f"Negative GMP of {gmp_pct:.1f}% indicates weak market sentiment")

    # Subscription scoring
    if sub_total >= 50:
        score    += 3
        conviction = "HIGH"
        positives.append(f"Heavily oversubscribed at {sub_total:.1f}x — strong institutional and retail demand")
    elif sub_total >= 10:
        score    += 2
        conviction = "MEDIUM"
        positives.append(f"Good subscription of {sub_total:.1f}x across categories")
    elif sub_total >= 3:
        score    += 1
        positives.append(f"Reasonable subscription of {sub_total:.1f}x")
    elif 0 < sub_total < 1:
        score    -= 1
        concerns.append(f"Undersubscribed at {sub_total:.2f}x — weak demand")

    # QIB check
    qib = sub.get("qib") or 0.0
    if qib >= 20:
        positives.append(f"Strong QIB interest ({qib:.1f}x) — institutional confidence is high")
    elif qib and qib < 1:
        concerns.append("Low QIB subscription — limited institutional interest")

    # SME-specific
    if ipo_type == "SME":
        concerns.append("SME IPOs carry higher risk and lower liquidity — suitable for risk-tolerant investors only")

    # Issue size check
    issue_size = ipo.get("issue_size_cr", 0)
    if issue_size > 2000:
        concerns.append("Large issue size may limit listing gains — typically absorbs more supply")
    elif issue_size < 100:
        concerns.append("Small issue size can lead to high price volatility post-listing")

    score = max(1, min(10, score))

    if score >= 8:
        verdict, conviction = "SUBSCRIBE", "HIGH"
    elif score >= 6:
        verdict = "SUBSCRIBE"
        if not conviction or conviction == "LOW":
            conviction = "MEDIUM"
    elif score <= 3:
        verdict = "AVOID"

    summary = _build_summary(ipo, verdict, score, gmp_pct, sub_total)

    return {
        "verdict":    verdict,
        "conviction": conviction,
        "score":      score,
        "summary":    summary,
        "positives":  positives or ["Insufficient data for detailed analysis"],
        "concerns":   concerns  or ["Market conditions may affect listing"],
        "strategy": {
            "listing_play": "Apply for listing gains only if GMP > 15% closer to allotment date",
            "long_term":    "Evaluate fundamentals and sector outlook before long-term holding",
        },
        "source": "rule_based",
    }


def _build_summary(ipo: dict, verdict: str, score: int, gmp_pct: float, sub_total: float) -> str:
    name = ipo.get("company_name") or ipo.get("name", "This IPO")
    parts = [f"{name} scores {score}/10."]
    if gmp_pct:
        parts.append(f"GMP of {gmp_pct:.1f}%.")
    if sub_total:
        parts.append(f"Subscribed {sub_total:.1f}x.")
    parts.append(f"Overall verdict: {verdict}.")
    return " ".join(parts)


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def analyze_ipo(ipo_slug: str) -> dict | None:
    """Fetch IPO from cache, enrich with subscription data, generate analysis."""
    from crawler.ipo_crawler import IPO_CACHE, fetch_single_ipo, fetch_subscription_status, enrich_ipo_data

    # Try cache first
    ipo = IPO_CACHE["by_slug"].get(ipo_slug) or IPO_CACHE["by_id"].get(ipo_slug)
    if ipo is None:
        ipo = await fetch_single_ipo(ipo_slug)
        if ipo is None:
            return None
        ipo = enrich_ipo_data(ipo)

    # Fetch subscription if NSE URL available
    nse_url = ipo.get("nse_url") or ipo.get("nse_info_url") or ""
    if nse_url and not ipo.get("subscription"):
        sub = await fetch_subscription_status(nse_url)
        ipo = dict(ipo)
        ipo["subscription"] = sub

    analysis = await generate_ipo_analysis(ipo)
    return {"ipo": ipo, "analysis": analysis}
