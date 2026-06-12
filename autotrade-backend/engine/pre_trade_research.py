"""Pre-trade research gate.

Runs before every BUY order is placed. Two layers:
  1. Tavily web search  — fetch latest headlines, detect red flags.
  2. LLM verdict        — Ollama/Groq issues a binary CONFIRM / VETO
                          with a one-line reason.

Returns a dict:
  veto          bool   — True blocks the trade
  veto_reason   str    — why (empty if not vetoed)
  research_note str    — 1-3 sentence web summary appended to trade reasoning
  sentiment     float  — −1…+1 from headline analysis
  source        str    — which layers ran: "tavily+llm" | "tavily" | "llm" | "skip"

Design constraints:
  • Total wall-clock time ≤ 8 s (called with asyncio.wait_for from trade loop).
  • All errors are swallowed → default ALLOW so research never blocks trades.
  • Results cached 20 min per symbol (trade loop runs every 60 s).
  • Tavily: basic search depth (1 credit/call).  LLM: ~100-token prompt.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from utils.config import settings
from utils.logger import logger

# ── In-process cache: {symbol: (result_dict, fetched_epoch)} ─────────────────
_cache: dict[str, tuple[dict, float]] = {}
_CACHE_TTL = 20 * 60   # 20 minutes


# ── Red-flag keywords that auto-veto regardless of LLM verdict ───────────────
_HARD_VETO_PATTERNS = re.compile(
    r"\b("
    r"sebi.{0,20}(notice|ban|suspend|penalt|fraud|order|action|investig)"
    r"|ed.{0,15}raid"
    r"|cbi.{0,15}(arrest|raid|probe)"
    r"|promoter.{0,20}(sell|pledg|exit)"
    r"|corporate.{0,15}fraud"
    r"|accounting.{0,15}(fraud|irregularit)"
    r"|insolvency|liquidat|bankrupt|wind.up|nclt"
    r"|trading.{0,10}suspend"
    r"|delist"
    r"|default.{0,20}(loan|npa|debt)"
    r"|earnings.{0,15}miss"
    r")\b",
    re.I,
)


def _is_cached(symbol: str) -> dict | None:
    entry = _cache.get(symbol)
    if entry and (time.monotonic() - entry[1]) < _CACHE_TTL:
        return entry[0]
    return None


def _store_cache(symbol: str, result: dict) -> None:
    _cache[symbol] = (result, time.monotonic())


def _allow(note: str = "", sentiment: float = 0.0, source: str = "skip") -> dict:
    return {"veto": False, "veto_reason": "", "research_note": note,
            "sentiment": sentiment, "source": source}


def _veto(reason: str, note: str = "", source: str = "skip") -> dict:
    return {"veto": True, "veto_reason": reason, "research_note": note,
            "sentiment": -0.5, "source": source}


# ── Company name lookup (for better Tavily queries) ───────────────────────────

def _company_name(symbol: str) -> str:
    bare = symbol.replace(".NS", "").replace(".BO", "")
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).fast_info
        name = getattr(info, "name", None) or ""
        if name and len(name) > 4:
            return name
    except Exception:
        pass
    return bare


# ── Layer 1: Tavily web search ────────────────────────────────────────────────

async def _tavily_research(symbol: str) -> tuple[str, float, bool]:
    """Return (research_note, sentiment, hard_veto_triggered)."""
    if not settings.tavily_available:
        return "", 0.0, False

    try:
        from engine.tavily_enricher import _client, _score_text
        client = _client()
        if client is None:
            return "", 0.0, False

        bare = symbol.replace(".NS", "")
        name = await asyncio.get_running_loop().run_in_executor(
            None, _company_name, symbol
        )
        search_term = name if len(name) > len(bare) else bare
        query = f'"{search_term}" NSE India stock news latest 2025 2026'

        resp = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: client.search(
                query,
                search_depth="basic",
                topic="finance",
                max_results=5,
                include_answer=True,
                time_range="week",
                country="india",
            ),
        )

        answer   = (resp.get("answer") or "").strip()
        results  = resp.get("results") or []
        snippets = [r.get("content", "") for r in results if r.get("content")]
        all_text = " ".join([answer] + snippets[:3])

        # Hard-veto check on raw text
        hard_veto = bool(_HARD_VETO_PATTERNS.search(all_text))

        # Build concise note (prefer Tavily's own answer summary)
        if answer and len(answer) > 40:
            sentences = re.split(r"(?<=[.!?])\s+", answer)
            note = " ".join(sentences[:3]).strip()
        elif snippets:
            note = snippets[0][:300].strip()
        else:
            note = ""

        sentiment = _score_text(all_text)
        logger.debug(
            f"[pre_trade/{bare}] tavily note={len(note)}ch "
            f"sentiment={sentiment:+.2f} hard_veto={hard_veto}"
        )
        return note, sentiment, hard_veto

    except Exception as exc:
        logger.debug(f"[pre_trade/{symbol}] tavily error: {exc}")
        return "", 0.0, False


# ── Layer 2: LLM CONFIRM / VETO verdict ──────────────────────────────────────

async def _llm_verdict(
    symbol: str,
    action: str,
    score: float,
    regime: str,
    entry: float,
    stop: float,
    t1: float,
    research_note: str,
    fund_grade: str,
    enriched_ctx: dict | None = None,
) -> tuple[bool, str]:
    """Ask LLM: CONFIRM or VETO this trade? Returns (veto, reason)."""
    system = (
        "You are a strict risk manager for an Indian equity trading desk. "
        "Review the trade proposal and respond EXACTLY with one of:\n"
        "  CONFIRM\n"
        "  VETO: <one sentence reason>\n"
        "Veto only on clear red flags: high promoter pledging, declining earnings trend, "
        "fraud/SEBI/NCLT news, very high debt. Never veto on price momentum alone."
    )

    rr = round(abs(t1 - entry) / abs(entry - stop), 2) if abs(entry - stop) > 0 else 0

    # Build rich context block from enriched_ctx
    ctx_block = ""
    if enriched_ctx:
        from engine.stock_enricher import format_for_llm
        ctx_block = format_for_llm(enriched_ctx, symbol)

    prompt = (
        f"Trade Proposal\n"
        f"  Symbol : {symbol.replace('.NS', '')} (NSE)\n"
        f"  Action : {action}\n"
        f"  Hub Score : {score:+.0f}  |  Regime : {regime}\n"
        f"  Entry ₹{entry:.2f}  SL ₹{stop:.2f}  T1 ₹{t1:.2f}  R:R {rr:.1f}x\n"
        f"  Fundamental Grade : {fund_grade}\n"
    )
    if ctx_block:
        prompt += f"\n{ctx_block}\n"
    news = enriched_ctx.get("tavily_news", research_note) if enriched_ctx else research_note
    prompt += (
        f"\n  Latest Web Research : {news or 'No news found in past 7 days.'}\n\n"
        f"Should I place this {action} trade? CONFIRM or VETO?"
    )

    try:
        from utils.llm import call_llm_chat
        reply = await asyncio.wait_for(
            call_llm_chat(
                [{"role": "system", "content": system},
                 {"role": "user",   "content": prompt}],
                max_tokens=80,
                temperature=0.1,
                groq_fallback=False,  # background — protect Groq quota
            ),
            timeout=6.0,
        )
        if not reply:
            return False, ""

        reply = reply.strip()
        first_word = reply.split()[0].upper() if reply.split() else ""

        if first_word == "VETO":
            reason = reply[5:].strip().lstrip(":").strip()
            logger.info(f"[pre_trade/{symbol}] LLM VETO: {reason}")
            return True, reason[:200]

        logger.debug(f"[pre_trade/{symbol}] LLM CONFIRM")
        return False, ""

    except asyncio.TimeoutError:
        logger.debug(f"[pre_trade/{symbol}] LLM verdict timed out → ALLOW")
        return False, ""
    except Exception as exc:
        logger.debug(f"[pre_trade/{symbol}] LLM verdict error: {exc} → ALLOW")
        return False, ""


# ── Public entry point ────────────────────────────────────────────────────────

async def run_pre_trade_research(
    symbol: str,
    action: str = "BUY",
    score: float = 0.0,
    regime: str = "UNKNOWN",
    entry: float = 0.0,
    stop: float = 0.0,
    t1: float = 0.0,
    fund_grade: str = "UNKNOWN",
) -> dict[str, Any]:
    """Run the full pre-trade research gate for a BUY signal.

    Always returns a result dict — never raises.
    Cached for 20 min so the same symbol isn't re-researched every cycle.
    """
    # Sell signals don't need web research (we already hold the stock)
    if action != "BUY":
        return _allow(source="skip_sell")

    cached = _is_cached(symbol)
    if cached:
        logger.debug(f"[pre_trade/{symbol}] cache hit → {cached['source']}")
        return cached

    # Both layers unavailable → fast allow
    tavily_ok = settings.tavily_available
    llm_ok    = getattr(settings, "ollama_available", False) or settings.groq_available

    if not tavily_ok and not llm_ok:
        result = _allow(source="skip_no_apis")
        _store_cache(symbol, result)
        return result

    source_parts: list[str] = []

    # Layer 1: Tavily news + Screener/yfinance enrichment in parallel
    from engine.stock_enricher import get_enriched_context
    research_note, sentiment, hard_veto = "", 0.0, False
    enriched_ctx: dict = {}

    if tavily_ok:
        # Run Tavily news AND multi-source enrichment concurrently
        tavily_task   = _tavily_research(symbol)
        enricher_task = get_enriched_context(symbol)
        (research_note, sentiment, hard_veto), enriched_ctx = await asyncio.gather(
            tavily_task, enricher_task
        )
        source_parts.append("tavily+screener")
    else:
        # No Tavily — still fetch Screener + yfinance context (free)
        enriched_ctx = await get_enriched_context(symbol)
        source_parts.append("screener")

    # Promoter-pledging hard veto (independent of LLM — data-driven)
    pledged = (enriched_ctx or {}).get("promoter_pledged") or 0.0
    if pledged > 50:
        result = _veto(
            reason=f"High promoter pledging {pledged:.0f}% — elevated stock-crash risk",
            note=f"Promoter pledging: {pledged:.0f}%",
            source="+".join(source_parts),
        )
        _store_cache(symbol, result)
        logger.warning(f"[pre_trade/{symbol}] HARD VETO (pledging {pledged:.0f}%)")
        return result

    # Hard veto from web keywords — no need to ask LLM
    if hard_veto:
        result = _veto(
            reason=f"Red-flag keyword detected in latest web search for {symbol.replace('.NS','')}",
            note=research_note,
            source="+".join(source_parts),
        )
        _store_cache(symbol, result)
        logger.warning(
            f"[pre_trade/{symbol}] HARD VETO (keyword): {result['veto_reason']}"
        )
        return result

    # Layer 2: LLM verdict with full enriched context
    veto, reason = False, ""
    if llm_ok:
        veto, reason = await _llm_verdict(
            symbol, action, score, regime, entry, stop, t1,
            research_note, fund_grade,
            enriched_ctx=enriched_ctx,
        )
        source_parts.append("llm")

    if veto:
        result = _veto(reason=reason, note=research_note,
                       source="+".join(source_parts))
        _store_cache(symbol, result)
        return result

    result = _allow(note=research_note, sentiment=sentiment,
                    source="+".join(source_parts) or "skip")
    _store_cache(symbol, result)
    return result
