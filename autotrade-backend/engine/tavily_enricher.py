"""Tavily web-research enricher for AutoTrade Pro.

Two use cases:
1. News enrichment for small-cap stocks with zero RSS/DB coverage — called once
   per hub cycle for symbols where news_score == 0.  Returns a sentiment float
   in −1…+1 and headline snippets.

2. Deep research for shortlist Telegram alerts — generates a structured summary
   (why to buy, key risks, when to exit) to augment the LLM-generated AI note.

Credits budget (1 000 free/month):
  - Basic search: 1 credit each.  At most 10 symbols/cycle × 30 cycles/day = 300/day.
  - Research: ~5-10 credits each.  Used only for top-5 Telegram shortlist alerts.

The enricher is fully async and non-blocking — all failures are swallowed so
the hub cycle never stalls due to a Tavily error or rate limit.
"""
from __future__ import annotations

import asyncio
import re
import time
from functools import lru_cache

from utils.config import settings
from utils.logger import logger

# Per-symbol Tavily news cache — avoids re-fetching within TTL.
# Format: {symbol: (score, headlines, fetched_at_epoch)}
_enriched_cache: dict[str, tuple[float, list[str], float]] = {}
_CACHE_TTL_S = 6 * 3600  # re-fetch after 6 h


# ── Tavily client (lazy singleton) ───────────────────────────────────────────

@lru_cache(maxsize=1)
def _client():
    if not settings.tavily_available:
        return None
    try:
        from tavily import TavilyClient
        return TavilyClient(api_key=settings.TAVILY_API_KEY)
    except Exception as exc:
        logger.warning(f"[tavily] client init failed: {exc}")
        return None


# ── Simple sentiment scorer for news headlines ────────────────────────────────

_BULLISH_WORDS = {
    "surge", "rally", "record", "beats", "upgraded", "outperform", "buy",
    "growth", "profit", "revenue", "expansion", "wins", "gains", "strong",
    "positive", "bullish", "award", "contract", "order", "approved",
}
_BEARISH_WORDS = {
    "drop", "fall", "decline", "misses", "downgrade", "loss", "cut",
    "debt", "fraud", "probe", "risk", "weak", "negative", "bearish",
    "concern", "warning", "sells", "exits", "penalty", "sebi", "regulatory",
}


def _score_text(text: str) -> float:
    """Keyword sentiment fallback — used only when FinBERT is unavailable."""
    words = re.findall(r"\b\w+\b", text.lower())
    bull = sum(1 for w in words if w in _BULLISH_WORDS)
    bear = sum(1 for w in words if w in _BEARISH_WORDS)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 4)


def _score_headlines_finbert(headlines: list[str]) -> float:
    """Score a list of headlines with FinBERT; keyword fallback if torch is absent.

    Scores each headline individually and returns the mean, which is more
    accurate than concatenating all text into one blob.
    """
    if not headlines:
        return 0.0
    try:
        from crawler.news_crawler import SentimentAnalyser
        sa = SentimentAnalyser()
        if sa._available:
            scores = [sa.analyse(h)["score"] for h in headlines if h.strip()]
            return round(sum(scores) / len(scores), 4) if scores else 0.0
    except Exception:
        pass
    return _score_text(" ".join(headlines))


# ── News enrichment: one symbol, one search call ─────────────────────────────

async def fetch_news_score(symbol: str, company_name: str = "") -> tuple[float, list[str]]:
    """Search Tavily for recent news about a stock and return (sentiment, headlines).

    Uses FinBERT to score each headline (keyword fallback if torch absent).
    Results are cached per-symbol for _CACHE_TTL_S to avoid redundant API calls.
    Returns (0.0, []) on any failure — safe to call fire-and-forget.
    Cost: 1 credit per call (basic search).
    """
    bare = symbol.replace(".NS", "").replace(".BO", "")

    # Return cached result if still fresh
    cached = _enriched_cache.get(bare)
    if cached and (time.monotonic() - cached[2]) < _CACHE_TTL_S:
        return cached[0], cached[1]

    client = _client()
    if client is None:
        return 0.0, []

    query_name = company_name or bare
    query = f"{query_name} NSE stock news India latest"

    try:
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: client.search(
                query,
                search_depth="basic",
                topic="finance",
                max_results=5,
                include_answer=False,
                time_range="week",
                country="india",
            ),
        )
        results = resp.get("results") or []
        headlines = [r.get("title", "") or r.get("content", "")[:120] for r in results[:5]]
        headlines = [h for h in headlines if h]
        score = _score_headlines_finbert(headlines)
        logger.debug(f"[tavily/news] {bare}: {len(results)} results, sentiment={score:+.2f}")
        _enriched_cache[bare] = (score, headlines, time.monotonic())
        return score, headlines
    except Exception as exc:
        logger.debug(f"[tavily/news] {bare}: {exc}")
        return 0.0, []


# ── Batch enrichment for hub cycle ───────────────────────────────────────────

async def enrich_missing_news(
    symbol_list: list[str],
    existing_scores: dict[str, float],
    max_symbols: int = 20,
) -> dict[str, tuple[float, list[str]]]:
    """Fetch Tavily news for up to max_symbols stocks with no existing news score.

    TTL cache in fetch_news_score ensures each symbol is only actually fetched
    once per 6 h regardless of how many hub cycles call this — so raising the
    cap to 20 does not proportionally increase credit spend.

    Returns {symbol: (sentiment, headlines)} for newly fetched + cache hits.
    """
    client = _client()
    if client is None:
        return {}

    now = time.monotonic()

    # Candidates: no RSS/DB coverage AND cache expired (or never fetched)
    missing = [
        s for s in symbol_list
        if s not in existing_scores and s.endswith(".NS")
        and (s.replace(".NS", "") not in _enriched_cache
             or now - _enriched_cache[s.replace(".NS", "")][2] >= _CACHE_TTL_S)
    ]

    # Also include symbols whose cache is still fresh but not in existing_scores
    fresh_hits: dict[str, tuple[float, list[str]]] = {}
    for s in symbol_list:
        bare = s.replace(".NS", "")
        if s not in existing_scores and s.endswith(".NS") and bare in _enriched_cache:
            cv = _enriched_cache[bare]
            if now - cv[2] < _CACHE_TTL_S:
                fresh_hits[s] = (cv[0], cv[1])

    if not missing and not fresh_hits:
        return {}

    to_fetch = missing[:max_symbols]
    if to_fetch:
        logger.info(
            f"[tavily/news] enriching {len(to_fetch)} new symbols "
            f"(+{len(fresh_hits)} from 6h cache, {len(missing)-len(to_fetch)} over cap)"
        )

    results: dict[str, tuple[float, list[str]]] = dict(fresh_hits)
    for sym in to_fetch:
        score, headlines = await fetch_news_score(sym)
        if score != 0.0 or headlines:
            results[sym] = (score, headlines)
        await asyncio.sleep(0.25)

    if to_fetch:
        logger.info(
            f"[tavily/news] fetched {len(to_fetch)} symbols → "
            f"{sum(1 for r in results.values() if r[0] != 0.0)} with non-zero sentiment"
        )
    return results


# ── Deep research for shortlist alert AI notes ────────────────────────────────

def _get_company_name(symbol: str) -> str:
    """Attempt to get the full company name from yfinance for a more precise query."""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).fast_info
        name = getattr(info, "long_name", None) or getattr(info, "company_name", None)
        return str(name).strip() if name and name != "None" else ""
    except Exception:
        return ""


async def research_stock_for_alert(
    symbol: str,
    score: float,
    tech_score: float,
    news_score: float,
    regime: str,
    entry: float,
    stop: float,
    t1: float,
    t2: float,
) -> str:
    """Use Tavily Search to build a concise research note for a shortlist Telegram alert.

    Returns a formatted 3–4 sentence analysis string.
    Cost: 2 credits (advanced search).
    """
    client = _client()
    if client is None:
        return ""

    bare = symbol.replace(".NS", "")

    # Get full company name for a more precise query (avoids ticker ambiguity)
    loop = asyncio.get_running_loop()
    company_name = await loop.run_in_executor(None, lambda: _get_company_name(symbol))
    search_term = company_name if company_name and len(company_name) > 4 else bare
    query = f'"{search_term}" NSE India stock analysis news 2025 outlook buy sell'

    try:
        resp = await loop.run_in_executor(
            None,
            lambda: client.search(
                query,
                search_depth="advanced",
                topic="finance",
                max_results=4,
                include_answer=True,
                time_range="month",
                country="india",
                chunks_per_source=2,
            ),
        )
        # Use the Tavily-generated answer if available
        answer = (resp.get("answer") or "").strip()
        if answer and len(answer) > 50:
            sentences = re.split(r"(?<=[.!?])\s+", answer)
            note = " ".join(sentences[:4])
        else:
            results = resp.get("results") or []
            snippets = [r.get("content", "")[:200] for r in results[:3] if r.get("content")]
            note = " | ".join(snippets)[:500]

        if note:
            logger.debug(f"[tavily/research] {bare}: {len(note)} chars (query: {search_term})")
        return note

    except Exception as exc:
        logger.debug(f"[tavily/research] {bare}: {exc}")
        return ""


# ── Tavily Extract / Crawl ────────────────────────────────────────────────────

async def crawl_urls(
    urls: list[str],
    *,
    extract_depth: str = "basic",
    max_urls: int = 5,
) -> list[dict]:
    """Crawl specific URLs with Tavily Extract and return full-text content.

    Returns list of {url, title, content} dicts for URLs that succeeded.
    Cost: 1–2 credits per URL depending on extract_depth ("basic" | "advanced").
    Never raises — returns [] on any failure.

    Use cases:
    - Crawl the top news URLs from a Tavily search to get full article text
    - Crawl a company's investor-relations page for latest announcements
    - Crawl BSE/NSE filing pages for earnings/board-meeting details
    """
    if not urls:
        return []

    client = _client()
    if client is None:
        return []

    targets = urls[:max_urls]
    try:
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: client.extract(
                urls=targets,
                extract_depth=extract_depth,
            ),
        )
        raw_results = resp.get("results") or []
        out = []
        for r in raw_results:
            url     = r.get("url", "")
            content = (r.get("raw_content") or r.get("content") or "").strip()
            title   = r.get("title", "")
            if content and len(content) > 50:
                out.append({"url": url, "title": title, "content": content[:2000]})
        logger.debug(f"[tavily/crawl] {len(targets)} urls → {len(out)} extracted")
        return out
    except Exception as exc:
        logger.debug(f"[tavily/crawl] extract failed: {exc}")
        return []


async def search_and_crawl(
    symbol: str,
    *,
    query_suffix: str = "NSE India stock latest news 2026",
    max_search_results: int = 4,
    crawl_top: int = 2,
    extract_depth: str = "basic",
) -> dict:
    """Combined search + crawl: search Tavily → extract top-N article URLs.

    Returns:
      {
        "search_answer":  str   — Tavily's own answer summary
        "snippets":       list  — search result excerpts (max 4)
        "crawled":        list  — full-text from top crawled articles
        "headlines":      list  — title strings from search results
        "sentiment":      float — keyword sentiment score
        "urls":           list  — source URLs from search
      }

    Cost: 1 credit (search) + crawl_top credits (extract). Total: 2–3 credits.
    Never raises.
    """
    empty = {"search_answer": "", "snippets": [], "crawled": [],
             "headlines": [], "sentiment": 0.0, "urls": []}

    client = _client()
    if client is None:
        return empty

    bare = symbol.replace(".NS", "").replace(".BO", "")
    loop = asyncio.get_running_loop()
    company_name = await loop.run_in_executor(None, lambda: _get_company_name(symbol))
    search_term = company_name if company_name and len(company_name) > len(bare) else bare
    query = f'"{search_term}" {query_suffix}'

    try:
        search_resp = await loop.run_in_executor(
            None,
            lambda: client.search(
                query,
                search_depth="basic",
                topic="finance",
                max_results=max_search_results,
                include_answer=True,
                time_range="week",
                country="india",
            ),
        )
    except Exception as exc:
        logger.debug(f"[tavily/search_and_crawl] search failed for {bare}: {exc}")
        return empty

    answer    = (search_resp.get("answer") or "").strip()
    results   = search_resp.get("results") or []
    urls      = [r.get("url", "") for r in results if r.get("url")]
    snippets  = [r.get("content", "")[:300] for r in results if r.get("content")]
    headlines = [r.get("title", "") for r in results if r.get("title")]
    all_text  = " ".join([answer] + snippets)
    sentiment = _score_text(all_text)

    # Crawl the top-N articles for full text
    crawled: list[dict] = []
    if crawl_top > 0 and urls:
        crawled = await crawl_urls(urls[:crawl_top], extract_depth=extract_depth)

    return {
        "search_answer": answer,
        "snippets":      snippets,
        "crawled":       crawled,
        "headlines":     headlines,
        "sentiment":     sentiment,
        "urls":          urls,
    }
