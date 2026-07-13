"""Multi-source stock context enricher.

Aggregates data from Screener.in, yfinance, NSE, and Tavily into a single
context dict used by:
  - pre_trade_research._llm_verdict   (richer buy/sell LLM prompt)
  - agent_loop.pre_trade_gate         (pre-execution enrichment)
  - tavily_enricher.research_stock_for_alert (Telegram alert context)

Sources (in parallel, all failures swallowed):
  1. yfinance   — company name, description, industry, website (fast)
  2. Screener.in — pros/cons, shareholding, quarterly trend    (2-3s)
  3. Tavily     — latest news headlines (1 credit, optional)

Cache: 4-hour TTL per symbol (matches Hub scoring frequency).
All functions return safe defaults — never raise.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from utils.logger import logger

_CACHE: dict[str, tuple[dict, float]] = {}
_TTL = 4 * 3600  # 4 hours


def _cached(symbol: str) -> dict | None:
    entry = _CACHE.get(symbol)
    if entry and (time.monotonic() - entry[1]) < _TTL:
        return entry[0]
    return None


def _store(symbol: str, data: dict) -> dict:
    _CACHE[symbol] = (data, time.monotonic())
    return data


def _quarterly_trend(values: list) -> str:
    nums = [v for v in (values or []) if v is not None]
    if len(nums) < 3:
        return "insufficient_data"
    last, prev1, prev2 = nums[0], nums[1], nums[2]
    if last > prev1 and prev1 > prev2:
        return "improving"
    if last < prev1 and prev1 < prev2:
        return "declining"
    if last > prev1:
        return "recovering"
    return "stable"


def _yfinance_context(symbol: str) -> dict:
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        summary = info.get("longBusinessSummary", "") or ""
        # Take first 2 sentences only
        import re
        sentences = re.split(r"(?<=[.!?])\s+", summary)
        short_desc = " ".join(sentences[:2]).strip()
        return {
            "company_name":        info.get("longName") or info.get("shortName") or "",
            "company_description": short_desc[:300],
            "website":             info.get("website") or "",
            "industry":            info.get("industry") or "",
            "sector":              info.get("sector") or "",
            "market_cap_cr":       round((info.get("marketCap") or 0) / 1e7, 1),
        }
    except Exception as exc:
        logger.debug(f"[enricher] yfinance context failed for {symbol}: {exc}")
        return {}


async def _screener_context(bare: str) -> dict:
    try:
        from engine.screener_deep import fetch_screener_deep
        data = await asyncio.wait_for(fetch_screener_deep(bare), timeout=7.0)

        pc        = data.get("pros_cons", {})
        share     = data.get("shareholding", {})
        quarterly = data.get("quarterly", {})
        periods   = quarterly.get("periods", [])
        rows      = quarterly.get("rows", {})

        # Quarterly sales + profit trend (most recent = first in list)
        sales_row = (rows.get("Sales +") or rows.get("Revenue")
                     or rows.get("Net Sales") or [])
        profit_row = (rows.get("Net Profit") or rows.get("Profit after tax")
                      or rows.get("PAT") or [])

        result: dict[str, Any] = {
            "pros":                   (pc.get("pros") or [])[:3],
            "cons":                   (pc.get("cons") or [])[:3],
            "promoter_holding":       share.get("promoter_holding"),
            "promoter_pledged":       share.get("pledged_pct"),
            "fii_holding":            share.get("fii_holding"),
            "dii_holding":            share.get("dii_holding"),
            "quarterly_periods":      periods[:6],
        }

        if sales_row:
            result["quarterly_sales_trend"]  = _quarterly_trend(sales_row[:3])
            result["latest_quarterly_sales"] = sales_row[0] if sales_row else None

        if profit_row:
            result["quarterly_profit_trend"]  = _quarterly_trend(profit_row[:3])
            result["latest_quarterly_profit"] = profit_row[0] if profit_row else None
            result["quarterly_profit_series"] = profit_row[:6]

        # Header ratios from Screener
        ratios = data.get("header_ratios", {})
        for k in ("pe_ratio", "pb_ratio", "roe", "roce", "dividend_yield"):
            if ratios.get(k) is not None:
                result[k] = ratios[k]

        return result

    except asyncio.TimeoutError:
        logger.debug(f"[enricher] screener timeout for {bare}")
        return {}
    except Exception as exc:
        logger.debug(f"[enricher] screener failed for {bare}: {exc}")
        return {}


async def _tavily_news(symbol: str, company_name: str) -> tuple[str, float]:
    """Quick Tavily search for latest news. Returns (note, sentiment)."""
    bare = symbol.replace(".NS", "").replace(".BO", "")
    search_term = (company_name or bare)[:60]
    query = f'"{search_term}" NSE India stock news analysis 2026 latest'
    
    try:
        from utils.config import settings
        if not settings.tavily_available:
            return "", 0.0
        from engine.tavily_enricher import _client, _score_text
        try:
            from duckduckgo_search import DDGS
            def _ddg_run():
                with DDGS() as ddgs:
                    return [r for r in ddgs.news(query, max_results=3)]
            results = await asyncio.get_running_loop().run_in_executor(None, _ddg_run)
            if results:
                note = " | ".join([r.get("title", "") for r in results[:3]])
                sentiment = _score_text(note)
                logger.debug(f"[enricher/ddg] {bare}: {len(note)} chars, sentiment={sentiment:+.2f}")
                return note, sentiment
        except Exception as e:
            logger.debug(f"[enricher/ddg] failed for {symbol}: {e}. Trying Google News RSS.")
            
        try:
            import urllib.request, urllib.parse
            import xml.etree.ElementTree as ET
            def _gnews_run():
                url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    root = ET.fromstring(response.read())
                return [item.findtext('title') for item in root.findall('.//item')[:3] if item.findtext('title')]
                
            loop = asyncio.get_running_loop()
            headlines = await loop.run_in_executor(None, _gnews_run)
            if headlines:
                note = " | ".join(headlines)
                sentiment = _score_text(note)
                logger.debug(f"[enricher/gnews] {bare}: {len(note)} chars, sentiment={sentiment:+.2f}")
                return note, sentiment
        except Exception as e2:
            logger.debug(f"[enricher/gnews] failed for {symbol}: {e2}. Falling back to Tavily.")

        client = _client()
        if client is None:
            return "", 0.0

        import re
        loop = asyncio.get_running_loop()
        resp = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: client.search(
                    query,
                    search_depth="basic",
                    topic="finance",
                    max_results=5,
                    include_answer=True,
                    time_range="month",
                    country="india",
                ),
            ),
            timeout=5.0,
        )
        answer   = (resp.get("answer") or "").strip()
        results  = resp.get("results") or []
        snippets = [r.get("content", "")[:200] for r in results if r.get("content")]
        all_text = " ".join([answer] + snippets[:3])
        sentiment = _score_text(all_text)

        if answer and len(answer) > 40:
            sentences = re.split(r"(?<=[.!?])\s+", answer)
            note = " ".join(sentences[:3]).strip()
        elif snippets:
            note = snippets[0][:300]
        else:
            note = ""

        logger.debug(f"[enricher/tavily] {bare}: {len(note)} chars, sentiment={sentiment:+.2f}")
        return note, sentiment

    except Exception as exc:
        logger.debug(f"[enricher/tavily] failed for {symbol}: {exc}")
        return "", 0.0


async def get_enriched_context(symbol: str) -> dict:
    """Return a merged context dict from all sources.

    Keys (all optional — present only when source returned data):
      company_name, company_description, website, industry, sector,
      market_cap_cr, pros[3], cons[3], promoter_holding, promoter_pledged,
      fii_holding, dii_holding, quarterly_sales_trend, quarterly_profit_trend,
      latest_quarterly_sales, latest_quarterly_profit, quarterly_profit_series,
      pe_ratio, pb_ratio, roe, roce, dividend_yield,
      tavily_news, tavily_sentiment, quarterly_periods
    """
    cached = _cached(symbol)
    if cached:
        return cached

    bare = symbol.replace(".NS", "").replace(".BO", "")

    # Phase 1: yfinance (sync, run in executor)
    loop = asyncio.get_running_loop()
    yf_ctx = await loop.run_in_executor(None, _yfinance_context, symbol)
    company_name = yf_ctx.get("company_name", "") or bare

    # Phase 2: Screener.in + Tavily in parallel
    screener_task = _screener_context(bare)
    tavily_task   = _tavily_news(symbol, company_name)

    screener_ctx, (tavily_note, tavily_sentiment) = await asyncio.gather(
        screener_task, tavily_task, return_exceptions=False
    )

    ctx = {**yf_ctx, **screener_ctx}
    if tavily_note:
        ctx["tavily_news"]      = tavily_note
        ctx["tavily_sentiment"] = tavily_sentiment

    return _store(symbol, ctx)


def format_for_llm(ctx: dict, symbol: str) -> str:
    """Format enriched context as a compact block for LLM prompts."""
    bare = symbol.replace(".NS", "")
    lines: list[str] = []

    name = ctx.get("company_name", "")
    desc = ctx.get("company_description", "")
    if name or desc:
        lines.append(f"Company: {name}")
    if ctx.get("industry"):
        lines.append(f"Industry: {ctx['industry']} | Sector: {ctx.get('sector','')}")
    if desc:
        lines.append(f"Business: {desc[:200]}")

    # Valuation
    vals = []
    if ctx.get("pe_ratio"):  vals.append(f"PE={ctx['pe_ratio']:.1f}")
    if ctx.get("roe"):       vals.append(f"ROE={ctx['roe']:.1f}%")
    if ctx.get("roce"):      vals.append(f"ROCE={ctx['roce']:.1f}%")
    if ctx.get("pb_ratio"):  vals.append(f"PB={ctx['pb_ratio']:.2f}")
    if vals:
        lines.append("Ratios: " + " | ".join(vals))

    # Shareholding
    sh_parts = []
    if ctx.get("promoter_holding") is not None:
        sh_parts.append(f"Promoter={ctx['promoter_holding']:.1f}%")
    if ctx.get("promoter_pledged") is not None and ctx["promoter_pledged"] > 0:
        sh_parts.append(f"⚠ Pledged={ctx['promoter_pledged']:.1f}%")
    if ctx.get("fii_holding") is not None:
        sh_parts.append(f"FII={ctx['fii_holding']:.1f}%")
    if sh_parts:
        lines.append("Shareholding: " + " | ".join(sh_parts))

    # Quarterly trend
    tr_parts = []
    if ctx.get("quarterly_profit_trend"):
        pf = ctx.get("latest_quarterly_profit")
        pf_str = f"₹{pf:.0f}Cr" if pf is not None else ""
        tr_parts.append(f"Profit trend={ctx['quarterly_profit_trend']} {pf_str}")
    if ctx.get("quarterly_sales_trend"):
        sl = ctx.get("latest_quarterly_sales")
        sl_str = f"₹{sl:.0f}Cr" if sl is not None else ""
        tr_parts.append(f"Sales trend={ctx['quarterly_sales_trend']} {sl_str}")
    if tr_parts:
        lines.append("Quarterly: " + " | ".join(tr_parts))

    # Pros/Cons (most important for LLM)
    pros = ctx.get("pros", [])
    cons = ctx.get("cons", [])
    if pros:
        lines.append("Screener Pros: " + "; ".join(pros[:3]))
    if cons:
        lines.append("Screener Cons: " + "; ".join(cons[:3]))

    # News
    if ctx.get("tavily_news"):
        lines.append(f"Latest News: {ctx['tavily_news'][:300]}")
    elif ctx.get("company_description") and not desc:
        pass  # already shown above

    return "\n".join(lines) if lines else f"Symbol: {bare} (no additional context available)"
