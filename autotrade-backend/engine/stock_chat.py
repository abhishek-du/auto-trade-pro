"""AI Stock Chat engine — intent detection, context formatting, Groq calls.

Uses the same httpx pattern as engine/llm_explainer.py.
No new data-fetching; calls stock_context_builder for all data.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from utils.config import settings
from utils.logger import logger
from utils.llm import call_llm_chat as call_groq_chat

# ── Groq constants (same as llm_explainer.py) ────────────────────────────────

# ── Intent classification ─────────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "BUY_SELL_ANALYSIS": [
        "should i buy", "should i sell", "is it good to buy", "buy or sell",
        "worth buying", "good investment", "buy now", "sell now", "should i invest",
        "is it a buy", "is it a sell", "buy signal", "sell signal",
    ],
    "PRICE_CHECK": [
        "price", "current price", "trading at", "how much", "what is the price",
        "rate of", "share price", "stock price",
    ],
    "TECHNICAL_ANALYSIS": [
        "rsi", "macd", "support", "resistance", "trend", "chart", "technical",
        "indicators", "pattern", "bollinger", "ema", "moving average", "supertrend",
        "candlestick", "breakout", "breakdown", "overbought", "oversold",
    ],
    "FUNDAMENTAL": [
        "pe ratio", "p/e", "earnings", "revenue", "profit", "fundamentals",
        "valuation", "cheap", "expensive", "overvalued", "undervalued",
        "debt", "roe", "dividend", "market cap", "book value", "eps",
    ],
    "NEWS_SENTIMENT": [
        "news", "latest", "what happened", "announcement", "any news",
        "sentiment", "recent", "today", "update", "result", "quarterly",
    ],
    "SIGNAL": [
        "signal", "recommendation", "ai says", "what does ai think",
        "ai recommendation", "system says",
    ],
    "COMPARISON": [
        " vs ", " versus ", "compare", "better than", " or ", "which is better",
        "difference between",
    ],
    "PORTFOLIO_ADVICE": [
        "should i add to portfolio", "how much to invest", "good for long term",
        "long term", "portfolio", "allocation", "hold", "exit",
    ],
}

# Known NSE tickers (bare, no .NS)
_KNOWN_TICKERS = {
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "INFOSYS", "ICICIBANK", "SBIN",
    "BHARTIARTL", "KOTAKBANK", "LT", "WIPRO", "HCLTECH", "AXISBANK",
    "MARUTI", "SUNPHARMA", "ITC", "BAJFINANCE", "NIFTY", "SENSEX", "BANKNIFTY",
}


def detect_intent(message: str) -> dict:
    """Classify the user message and extract symbols + timeframe."""
    msg_lower = message.lower()

    intent = "GENERAL"
    for intent_name, keywords in _INTENT_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            intent = intent_name
            break

    # Extract known tickers (bare words, case-insensitive)
    symbols: list[str] = []
    for word in message.upper().split():
        clean = word.strip(".,!?;:'\"()")
        if clean in _KNOWN_TICKERS:
            symbols.append(clean)
        # Also catch .NS / .BO suffixed
        if clean.endswith(".NS") or clean.endswith(".BO"):
            symbols.append(clean)

    # Detect timeframe
    timeframe = "1h"
    if "daily" in msg_lower or "1d" in msg_lower or "day" in msg_lower:
        timeframe = "1d"
    elif "5 min" in msg_lower or "5min" in msg_lower or "5m" in msg_lower:
        timeframe = "5m"
    elif "15 min" in msg_lower or "15m" in msg_lower:
        timeframe = "15m"
    elif "weekly" in msg_lower or "1w" in msg_lower or "week" in msg_lower:
        timeframe = "1d"

    is_index = any(w in message.upper() for w in ["NIFTY", "SENSEX", "BANKNIFTY"])

    return {
        "intent":    intent,
        "symbols":   list(dict.fromkeys(symbols)),  # deduplicated, order-preserved
        "timeframe": timeframe,
        "is_index":  is_index,
    }


# ── System prompt ─────────────────────────────────────────────────────────────

def build_system_prompt() -> str:
    return (
        "You are Avishk, an expert Indian stock market analyst embedded in AutoTrade Pro, "
        "a professional trading platform.\n\n"
        "Your expertise:\n"
        "- Deep knowledge of NSE and BSE listed companies\n"
        "- Technical analysis (RSI, MACD, Bollinger Bands, Supertrend, Ichimoku)\n"
        "- Indian market dynamics: FII/DII flows, RBI policy, sector rotation\n"
        "- Fundamental analysis using Indian accounting standards\n"
        "- IPO analysis, F&O market, derivatives\n"
        "- Mutual funds and SIP strategies for Indian investors\n\n"
        "Your communication style:\n"
        "- Direct and actionable — give clear views, not just 'it depends'\n"
        "- Use Indian rupee (₹) for all prices\n"
        "- Reference Indian market context (SEBI, RBI, NSE, BSE)\n"
        "- Use technical terms but explain them when needed\n"
        "- Keep responses conversational but data-driven\n"
        "- 2-4 paragraphs maximum per response\n"
        "- Use bullet points sparingly — prefer flowing analysis\n\n"
        "Important rules:\n"
        "- Always base analysis on the data provided in the context\n"
        "- Acknowledge data limitations honestly ('no recent news available')\n"
        "- Add disclaimer: 'This is analysis, not financial advice'\n"
        "- Never invent data — only use what is in the context\n"
        "- If asked about a stock not in the context, say so clearly"
    )


# ── Context formatter for LLM ─────────────────────────────────────────────────

def format_context_for_llm(context: dict) -> str:
    """Convert structured context dict into clean readable text for the LLM."""
    lines: list[str] = []
    name   = context["display_name"]
    symbol = context["symbol"]

    # Master Intelligence Hub score (most decision-relevant — show first)
    hs = context.get("hub_score") or {}
    if hs.get("master_score") is not None:
        lines.append("MASTER INTELLIGENCE SCORE:")
        lines.append(f"  Score: {hs['master_score']:.1f}/100 ({hs.get('signal','NEUTRAL')})")
        if hs.get("rank"):
            lines.append(f"  Rank: #{hs['rank']} in NSE universe")
        if hs.get("is_blocked"):
            lines.append(f"  ⚠ Blocked: {hs.get('blocked_reason')}")
        r = hs.get("reasoning") or {}
        if r:
            lines.append(
                f"  Components — Technical {r.get('technical',0):.0f}, News {r.get('news',0):.0f}, "
                f"Sector {r.get('sector',0):.0f}, Macro {r.get('macro',0):.0f}, "
                f"Earnings {r.get('earnings',0):.0f}, Fundamental {r.get('fundamental',0):.0f}"
            )
        mc = context.get("macro") or {}
        if mc:
            lines.append(
                f"  Market: bias {mc.get('total_bias',0):+d}, VIX {mc.get('vix','—')}, "
                f"mood {mc.get('market_mood','NEUTRAL')}, FII 3d ₹{mc.get('fii_3d',0):.0f}Cr"
            )
        lines.append("")

    price = context.get("price", {})
    if price.get("price"):
        lines.append(f"LIVE DATA FOR {name} ({symbol})")
        lines.append(f"Current Price: ₹{price['price']:,.2f}")
        if price.get("change") is not None:
            sign = "+" if price["change"] >= 0 else ""
            lines.append(f"Today: {sign}₹{price['change']:.2f} ({sign}{price.get('change_pct', 0):.2f}%)")
        if price.get("high") and price.get("low"):
            lines.append(f"Day Range: ₹{price['low']:,.2f} – ₹{price['high']:,.2f}")
        if price.get("52w_high") and price.get("52w_low"):
            lines.append(f"52W Range: ₹{price['52w_low']:,.2f} – ₹{price['52w_high']:,.2f}")
        lines.append("")

    ind = context.get("indicators")
    if ind:
        lines.append("TECHNICAL INDICATORS:")
        rsi = ind.get("rsi")
        if rsi is not None:
            lines.append(f"RSI(14): {rsi:.1f} — {ind.get('rsi_signal', 'N/A')}")
        lines.append(f"MACD: {ind.get('macd_cross', 'N/A')}")
        lines.append(f"Bollinger: {ind.get('bb_position', 'N/A')}")
        lines.append(f"EMA Trend: {ind.get('ema_trend', 'N/A')}")
        if ind.get("supertrend_direction"):
            lines.append(f"Supertrend: {ind['supertrend_direction']}")
        if ind.get("ichimoku_signal"):
            lines.append(f"Ichimoku: {ind['ichimoku_signal']}")
        if ind.get("vwap_position"):
            lines.append(f"VWAP: {ind['vwap_position']}")
        lines.append(f"Composite Score: {ind.get('composite_score', 0):.1f} / 100")
        lines.append("")

    pat = context.get("patterns")
    if pat and pat.get("count", 0) > 0:
        lines.append("CANDLESTICK PATTERNS:")
        lines.append(f"Direction: {pat.get('direction', 'NEUTRAL')}")
        lines.append(f"Strongest: {pat.get('strongest', 'None')}")
        lines.append(f"Pattern Score: {pat.get('total_score', 0)}")
        lines.append("")

    sig = context.get("signal")
    if sig:
        lines.append("LATEST AI SIGNAL:")
        lines.append(f"Action: {sig['action']} (Confidence: {sig['confidence']:.0f}%)")
        lines.append(f"Final Score: {sig['final_score']:.1f}")
        lines.append(f"Generated: {sig['created_at'][:10]}")
        lines.append("")

    fund = context.get("fundamentals", {})
    if fund:
        lines.append("FUNDAMENTALS:")
        if fund.get("pe_ratio"):
            lines.append(f"P/E Ratio: {fund['pe_ratio']:.1f}x")
        if fund.get("pb_ratio"):
            lines.append(f"P/B Ratio: {fund['pb_ratio']:.1f}x")
        if fund.get("roe"):
            lines.append(f"ROE: {fund['roe'] * 100:.1f}%")
        if fund.get("debt_equity"):
            lines.append(f"Debt/Equity: {fund['debt_equity']:.2f}")
        if fund.get("market_cap_cr"):
            lines.append(f"Market Cap: ₹{fund['market_cap_cr']:,.0f} Crore")
        if fund.get("dividend_yield"):
            lines.append(f"Dividend Yield: {fund['dividend_yield'] * 100:.2f}%")
        if fund.get("sector"):
            lines.append(f"Sector: {fund['sector']}")
        lines.append("")

    sent = context.get("sentiment", {})
    if sent.get("news"):
        lines.append("RECENT NEWS:")
        for n in sent["news"][:3]:
            dot = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(n.get("sentiment", ""), "⚪")
            lines.append(f"{dot} {n['headline'][:80]} ({n.get('source', '')})")
        lines.append(f"Overall Sentiment Score: {sent.get('score', 0):.2f}")
        lines.append("")

    fii = context.get("fii_dii")
    if fii:
        fii_label = "BUYING" if fii["fii_net_3day"] > 0 else "SELLING"
        dii_label = "BUYING" if fii["dii_net_3day"] > 0 else "SELLING"
        lines.append("INSTITUTIONAL FLOWS (3-day):")
        lines.append(f"FII: {fii_label} (₹{abs(fii['fii_net_3day']):.0f} Cr net)")
        lines.append(f"DII: {dii_label} (₹{abs(fii['dii_net_3day']):.0f} Cr net)")
        lines.append("")

    if context.get("candle_count", 0) < 20:
        lines.append("NOTE: Limited historical data available for this symbol.")
        lines.append("")

    return "\n".join(lines)


# ── No-AI fallback ────────────────────────────────────────────────────────────

def generate_no_ai_response(message: str, contexts: dict) -> str:
    if not contexts:
        return (
            "I couldn't find data for that stock. "
            "Try entering the exact NSE symbol like RELIANCE, TCS, or HDFCBANK."
        )
    lines = ["Here is the current data (AI analysis unavailable — configure GROQ_API_KEY for full Avishk AI):"]
    for sym, ctx in contexts.items():
        price = ctx.get("price", {})
        p  = price.get("price", 0) or 0
        cp = price.get("change_pct", 0) or 0
        lines.append(f"\n{sym}: ₹{p:,.2f} ({cp:+.2f}%)")
        sig = ctx.get("signal")
        if sig:
            lines.append(f"Latest signal: {sig['action']} at {sig['confidence']:.0f}% confidence")
        ind = ctx.get("indicators")
        if ind:
            rsi = ind.get("rsi")
            rsi_str = f"RSI: {rsi:.1f}" if rsi is not None else "RSI: N/A"
            lines.append(f"{rsi_str} | MACD: {ind.get('macd_cross', 'N/A')} | Trend: {ind.get('ema_trend', 'N/A')}")
        fund = ctx.get("fundamentals", {})
        if fund.get("pe_ratio"):
            lines.append(f"P/E: {fund['pe_ratio']:.1f}x")
    return "\n".join(lines)


# ── Groq call ─────────────────────────────────────────────────────────────────

async def _call_groq(messages: list[dict], max_tokens: int = 600) -> str | None:
    return await call_groq_chat(
        messages, max_tokens=max_tokens, temperature=0.4, timeout=20.0,
    )


# ── Main entry point ──────────────────────────────────────────────────────────

async def process_chat_message(
    user_message: str,
    conversation_history: list[dict],
    session: AsyncSession,
) -> dict:
    """Process a user chat message and return the AI response with data context."""
    from engine.stock_context_builder import resolve_symbol, build_stock_context

    # Step 1: Detect intent
    intent_data = detect_intent(user_message)

    # Step 2: Resolve symbols
    raw_syms = intent_data.get("symbols", [])
    resolved: list[str] = []
    for s in raw_syms:
        r = resolve_symbol(s)
        if r and r not in resolved:
            resolved.append(r)

    # Fallback: scan message words directly
    if not resolved:
        for word in user_message.upper().split():
            clean = word.strip(".,!?;:'\"()")
            r = resolve_symbol(clean)
            if r:
                resolved.append(r)
                break

    # Step 3: Build data contexts (max 2 symbols)
    contexts: dict[str, dict] = {}
    for sym in resolved[:2]:
        try:
            contexts[sym] = await build_stock_context(sym, session, intent_data["timeframe"])
        except Exception as exc:
            logger.warning("Context build failed for %s: %s", sym, exc)

    # Step 4: Format context
    context_text = ""
    for ctx in contexts.values():
        context_text += format_context_for_llm(ctx) + "\n"

    # Step 5: Build Groq messages
    llm_messages: list[dict] = [
        {"role": "system", "content": build_system_prompt()}
    ]
    # Last 6 turns of history
    llm_messages.extend(conversation_history[-6:])

    if context_text.strip():
        full_user_msg = f"{context_text}\nUser Question: {user_message}"
    else:
        full_user_msg = user_message

    llm_messages.append({"role": "user", "content": full_user_msg})

    # Step 6: Call Groq (or fallback)
    if not settings.groq_available:
        reply  = generate_no_ai_response(user_message, contexts)
        source = "rule_based"
    else:
        reply = await _call_groq(llm_messages)
        if reply:
            source = "groq"
        else:
            reply  = generate_no_ai_response(user_message, contexts)
            source = "rule_based"

    from datetime import datetime, timezone
    return {
        "reply":    reply,
        "contexts": contexts,
        "intent":   intent_data["intent"],
        "symbols":  list(contexts.keys()),
        "source":   source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
