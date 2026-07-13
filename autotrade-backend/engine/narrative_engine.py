"""
Narrative Intelligence Engine — Top-Down Sector Theme Builder

This module implements the 3-step "Eagle Eyes" strategy:

  Step 1: SCRAPER — Fetch raw signals from Telegram channels + RSS feeds every 5-10 minutes
  Step 2: LLM DECODER — Use gpt-oss-120b to extract hot sectors + keywords from the noise
  Step 3: HUB INJECTION — Write a "narrative boost" cache that Intelligence Hub reads to
                          give +20 score bonus to stocks in hot sectors

Architecture:
  ┌─────────────────────────────────────┐
  │    Telegram / RSS / Twitter feeds   │  ← Raw headlines (noise)
  └──────────────┬──────────────────────┘
                 ↓
  ┌─────────────────────────────────────┐
  │   LLM Decoder (gpt-oss-120b)        │  ← Extracts narrative themes
  │   Output: {sector: score, reason}   │
  └──────────────┬──────────────────────┘
                 ↓
  ┌─────────────────────────────────────┐
  │   NARRATIVE_BOOST_CACHE             │  ← In-memory dict (refreshed every cycle)
  │   {sector: {boost: +20, reason}}    │
  └──────────────┬──────────────────────┘
                 ↓
  ┌─────────────────────────────────────┐
  │   Intelligence Hub (scoring loop)   │  ← Applies bonus to matching stocks
  └─────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from utils.logger import logger
from utils.config import settings

# ── In-memory cache (refreshed every cycle) ──────────────────────────────────
NARRATIVE_BOOST_CACHE: dict[str, dict] = {}
"""
Schema:
  {
    "Auto": {"boost": 20, "reason": "India-Japan MoU + Monthly auto sales numbers"},
    "EMS":  {"boost": 15, "reason": "Semiconductor PLI push in latest news"},
    ...
  }
"""

_LAST_REFRESH: float = 0.0
_REFRESH_INTERVAL_SECONDS = 300  # 5 minutes

# ── Sector keyword mapping ───────────────────────────────────────────────────
# Maps keywords found in news/Telegram to internal sector names
SECTOR_KEYWORD_MAP: dict[str, list[str]] = {
    "Auto": [
        "auto sales", "automobile", "vehicle", "car sales", "two-wheeler",
        "monthly numbers", "passenger vehicle", "ev", "electric vehicle",
        "mou auto", "maruti", "tata motors", "bajaj auto", "hero motocorp",
        "eicher", "auto ancillary", "auto component"
    ],
    "EMS": [
        "ems", "electronics manufacturing", "pcb", "printed circuit",
        "manufacturing services", "contract manufacturing", "kaynes",
        "syrma", "avalon", "dixons", "amber", "component"
    ],
    "Semiconductor": [
        "semiconductor", "chip", "fab", "foundry", "wafer",
        "display fab", "india semiconductor", "pli chip",
        "micron", "vedanta foxconn", "tata semiconductor"
    ],
    "IT": [
        "it sector", "software", "tech layoffs", "ai deals", "tcs",
        "infosys", "wipro", "hcl tech", "deal wins", "digital transformation"
    ],
    "Defence": [
        "defence", "defense", "military", "drdo", "hal", "mq-9",
        "arms deal", "fighter jet", "indigenization", "bel",
        "bharat electronics", "ordnance"
    ],
    "Infra": [
        "infrastructure", "highway", "road project", "nhai", "metro",
        "bullet train", "smart city", "larsen toubro", "l&t infra",
        "ircon", "irb", "rvnl"
    ],
    "Banking": [
        "rbi", "repo rate", "credit growth", "npa", "loan growth",
        "banking sector", "fdii", "fii inflow banking", "nbfc"
    ],
    "Pharma": [
        "pharma", "fda approval", "drug", "api", "bulk drug",
        "biocon", "sun pharma", "dr reddy", "divi's", "aurobindo"
    ],
    "Energy": [
        "oil price", "crude", "solar", "renewable", "green energy",
        "adani green", "tata power", "ntpc", "power sector", "electricity"
    ],
    "Real Estate": [
        "real estate", "realty", "housing", "property", "dlf",
        "godrej properties", "prestige", "oberoi realty", "home sales"
    ],
}

# ── RSS Feed URLs for India ───────────────────────────────────────────────────
INDIA_RSS_FEEDS = [
    "https://news.google.com/rss/search?q=when:1h+india+business+stock+market&hl=en-IN&gl=IN&ceid=IN:en", # Google News Real-Time
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "https://economictimes.indiatimes.com/industry/auto/rssfeeds/53776724.cms",
    "https://www.moneycontrol.com/rss/business.xml",
    "https://www.businessstandard.com/rss/markets-106.rss",
    "https://feeds.feedburner.com/ndtvprofit-latest",
]


# ── Step 1: Fetch raw text from RSS ──────────────────────────────────────────

async def _fetch_rss_headlines(max_age_minutes: int = 60) -> list[str]:
    """Fetch recent headlines from India financial RSS feeds."""
    headlines = []
    
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for feed_url in INDIA_RSS_FEEDS:
            try:
                resp = await client.get(feed_url, headers={"User-Agent": "AutoTradePro/1.0"})
                if resp.status_code == 200:
                    import re
                    # Simple XML title extraction
                    titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", resp.text)
                    if titles:
                        headlines.extend(titles[1:30])  # Skip feed title
            except Exception as e:
                logger.debug(f"[narrative] RSS fetch failed ({feed_url}): {e}")

    # Remove duplicates
    headlines = list(set(headlines))
    logger.info(f"[narrative] RSS: fetched {len(headlines)} unique headlines")
    return headlines[:150]  # Cap at 150


# ── Step 1b: Fetch raw text from public Telegram channels ───────────────────
# This module's docstring/architecture diagram always described Telegram as a
# source alongside RSS, but only RSS was ever actually implemented (found
# 2026-07-06, while investigating why the Hub never caught narrative-driven
# calls like "water sector ₹20 lakh crore opportunity" or "India-Japan MoU"
# the way a manually-run channel like Eagle Eyes does). This closes that gap
# using the public t.me/s/<channel> web preview — works for any public
# channel, no bot token or login needed.

async def _fetch_telegram_headlines(max_age_minutes: int = 60) -> list[str]:
    """Fetch recent messages from configured public Telegram channels.

    Only public channels are supported this way (t.me/s/ is Telegram's public
    web preview). A private channel needs a logged-in session (Telethon) —
    out of scope here; NARRATIVE_TELEGRAM_CHANNELS is public-channel-only.
    """
    from bs4 import BeautifulSoup

    headlines: list[str] = []
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    channels = settings.narrative_telegram_channels
    if not channels:
        return headlines

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for channel in channels:
            try:
                resp = await client.get(
                    f"https://t.me/s/{channel}",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                if resp.status_code != 200:
                    logger.debug(f"[narrative] Telegram {channel}: HTTP {resp.status_code}")
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                for msg in soup.select("div.tgme_widget_message[data-post]"):
                    t = msg.select_one("time")
                    if not t or not t.get("datetime"):
                        continue
                    try:
                        msg_dt = (
                            datetime.fromisoformat(t["datetime"])
                            .astimezone(timezone.utc)
                            .replace(tzinfo=None)
                        )
                    except ValueError:
                        continue
                    if msg_dt < cutoff:
                        continue
                    txt_el = msg.select_one(".tgme_widget_message_text")
                    if txt_el:
                        text = txt_el.get_text(" ", strip=True)
                        if text:
                            headlines.append(text)
            except Exception as e:
                logger.debug(f"[narrative] Telegram fetch failed ({channel}): {e}")

    logger.info(
        f"[narrative] Telegram: fetched {len(headlines)} recent messages "
        f"from {len(channels)} channel(s)"
    )
    return headlines[:100]


# ── Step 2: Rule-based pre-filter (fast, no LLM) ─────────────────────────────

def _keyword_score(texts: list[str]) -> dict[str, dict]:
    """
    Quick keyword-match to pre-score sectors before sending to LLM.
    Returns {sector: {hit_count, matched_keywords}}
    """
    text_blob = " ".join(texts).lower()
    scores: dict[str, dict] = {}

    for sector, keywords in SECTOR_KEYWORD_MAP.items():
        hits = [kw for kw in keywords if kw.lower() in text_blob]
        if hits:
            scores[sector] = {
                "hit_count": len(hits),
                "matched_keywords": hits[:5],
            }

    return dict(sorted(scores.items(), key=lambda x: x[1]["hit_count"], reverse=True))


# ── Step 3: LLM Narrative Decoder ────────────────────────────────────────────

async def _llm_decode_narrative(headlines: list[str], keyword_scores: dict[str, dict]) -> dict[str, dict]:
    """
    Send headlines + pre-scored sectors to LLM.
    Returns {sector: {boost, reason}} where boost is 0-25.
    """
    try:
        from utils.llm import call_llm_chat

        headline_text = "\n".join(f"- {h}" for h in headlines[:50])
        pre_scores    = json.dumps(keyword_scores, indent=2)

        system_prompt = (
            "You are an expert Indian equity market analyst specializing in sector rotation and thematic investing. "
            "Your job is to read recent news headlines and identify which sectors have the STRONGEST narrative momentum RIGHT NOW. "
            "Return ONLY a compact JSON object mapping sector names to a boost score (0-25) and a short reason (max 15 words). "
            "Only include sectors with a boost >= 10 (i.e., meaningful narrative strength). "
            "Sectors to consider: Auto, EMS, Semiconductor, IT, Defence, Infra, Banking, Pharma, Energy, Real Estate. "
            'Format: {"Auto": {"boost": 20, "reason": "India-Japan MoU + monthly auto sales due"}, ...}'
        )

        user_prompt = (
            f"Recent India market headlines (last 60 min):\n{headline_text}\n\n"
            f"Keyword pre-score (auto-detected):\n{pre_scores}\n\n"
            "Based on these headlines, which sectors have the strongest bullish narrative momentum today? "
            "Return JSON only."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ]

        resp = await call_llm_chat(
            messages,
            max_tokens=400,
            temperature=0.2,
        )
        if not resp:
            return {}

        # Parse JSON from response
        import re
        json_match = re.search(r"\{.*\}", resp, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {}

    except Exception as e:
        logger.warning(f"[narrative] LLM decode failed: {e}")
        return {}


# ── Step 4: Fallback (rule-based, no LLM needed) ─────────────────────────────

def _rule_based_boost(keyword_scores: dict[str, dict]) -> dict[str, dict]:
    """Fallback when LLM is unavailable: convert hit_count to boost score."""
    result = {}
    for sector, data in keyword_scores.items():
        hits = data["hit_count"]
        if hits >= 3:
            boost = min(25, hits * 5)
            result[sector] = {
                "boost":  boost,
                "reason": f"Keyword hits: {', '.join(data['matched_keywords'][:3])}",
            }
    return result


# ── Main refresh function (called by Celery task every 5 min) ─────────────────

async def refresh_narrative_cache(force: bool = False) -> dict[str, dict]:
    """
    Full pipeline:
      1. Fetch RSS + Telegram
      2. Keyword pre-filter
      3. LLM narrative decode (with fallback)
      4. Update NARRATIVE_BOOST_CACHE
    
    Returns the updated cache.
    """
    global NARRATIVE_BOOST_CACHE, _LAST_REFRESH

    now = time.time()
    if not force and (now - _LAST_REFRESH) < _REFRESH_INTERVAL_SECONDS:
        return NARRATIVE_BOOST_CACHE  # Fresh enough, skip

    logger.info("[narrative] 🔍 Refreshing narrative intelligence cache...")

    # Step 1: Gather raw text from RSS feeds + public Telegram channels
    rss_headlines = await _fetch_rss_headlines(max_age_minutes=60)
    telegram_headlines = await _fetch_telegram_headlines(max_age_minutes=60)
    all_texts = rss_headlines + telegram_headlines

    if not all_texts:
        logger.warning("[narrative] No text fetched from any source — narrative cache unchanged")
        return NARRATIVE_BOOST_CACHE

    # Step 2: Quick keyword pre-score
    keyword_scores = _keyword_score(all_texts)
    logger.info(f"[narrative] Keyword pre-score: {list(keyword_scores.keys())}")

    # Step 3: LLM decode (async, with fallback)
    llm_boosts = await _llm_decode_narrative(all_texts, keyword_scores)
    
    # Apply Fake News Trap: Require at least 2 keyword hits for confirmation
    final_boosts = {}
    raw_boosts = llm_boosts if llm_boosts else _rule_based_boost(keyword_scores)
    
    for sector, data in raw_boosts.items():
        # Check if the sector had at least 2 hits in our keyword scanner
        if keyword_scores.get(sector, {}).get("hit_count", 0) >= 2:
            data["boost"] = 40  # Automatically give a +40 boost as requested
            final_boosts[sector] = data
        else:
            logger.info(f"[narrative] Fake News Trap triggered: Dropped {sector} (less than 2 sources/mentions)")

    if final_boosts:
        NARRATIVE_BOOST_CACHE = final_boosts
        logger.info(f"[narrative] ✅ Final verified narrative: {final_boosts}")
    else:
        # Fallback if everything is filtered
        NARRATIVE_BOOST_CACHE = {}
        logger.info(f"[narrative] ⚠️ No confirmed narratives passed the 2-source filter.")

    _LAST_REFRESH = now

    # Log a nice summary
    if NARRATIVE_BOOST_CACHE:
        summary = " | ".join(
            f"{s}: +{d['boost']} ({d['reason']})"
            for s, d in NARRATIVE_BOOST_CACHE.items()
        )
        logger.info(f"[narrative] Today's hot sectors → {summary}")

    return NARRATIVE_BOOST_CACHE


def get_narrative_boost(sector: str) -> float:
    """
    Called by Intelligence Hub to get the narrative bonus for a sector.
    Returns 0.0 if sector is not in the hot list.
    """
    data = NARRATIVE_BOOST_CACHE.get(sector, {})
    return float(data.get("boost", 0.0))


def get_narrative_summary() -> str:
    """Human-readable summary for Telegram alerts and dashboard."""
    if not NARRATIVE_BOOST_CACHE:
        return "No active sector narratives detected."

    lines = []
    for sector, data in sorted(NARRATIVE_BOOST_CACHE.items(), key=lambda x: -x[1]["boost"]):
        lines.append(f"🔥 {sector}: +{data['boost']}pts — {data['reason']}")
    return "\n".join(lines)
