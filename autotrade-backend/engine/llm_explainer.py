"""LLM-powered trade explanation for AutoTrade Pro.

Primary  : Groq API (llama-3.1-8b-instant — fast, free tier available)
Fallback : returns a plain-text summary built from signal.reasoning_points

Designed so the primary can be swapped to Claude (Anthropic) later by
changing _call_llm() without touching any other code.

Public API
----------
generate_trade_explanation(signal: TradingSignal) -> str
format_paper_trade_notification(trade: PaperTrade, explanation: str) -> str
"""

import httpx

from db.models import PaperTrade
from utils.config import settings
from utils.logger import logger

# ── Groq API constants ────────────────────────────────────────────────────────
_GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL   = "llama-3.1-8b-instant"
_MAX_TOKENS   = 150
_TIMEOUT      = 15.0

# ── System prompt (same wording as the Claude spec — works with any LLM) ─────
_SYSTEM_PROMPT = (
    "You are a trading assistant explaining paper (simulated) trades to a beginner. "
    "This is FAKE money only — no real funds are involved. Be educational. "
    "Explain in 2-3 sentences what pattern or indicator triggered this signal. "
    "Be specific. Keep under 100 words. No jargon without explanation."
)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_user_prompt(signal) -> str:
    patterns_str   = ", ".join(signal.patterns_detected) or "None"
    reasoning_str  = "\n".join(f"• {r}" for r in signal.reasoning_points)
    return (
        f"Paper Trade Signal:\n"
        f"Symbol: {signal.symbol}\n"
        f"Direction: {signal.action}\n"
        f"Confidence: {signal.confidence:.0f}%\n"
        f"Entry: {signal.entry_price:.5f} | Stop-Loss: {signal.stop_loss:.5f} | Take-Profit: {signal.take_profit:.5f}\n"
        f"\n"
        f"Analysis:\n"
        f"- Pattern Score: {signal.pattern_score:.1f} | Patterns: {patterns_str}\n"
        f"- Indicator Score: {signal.indicator_score:.1f}\n"
        f"- News Sentiment: {signal.sentiment_score:.1f}\n"
        f"- Final Score: {signal.final_score:.1f}\n"
        f"\n"
        f"Reasoning points:\n"
        f"{reasoning_str}\n"
        f"\n"
        f"Explain why the AI system generated this signal."
    )


# ── Groq API call ─────────────────────────────────────────────────────────────

async def _call_groq(user_prompt: str) -> str | None:
    """POST to Groq chat completions. Returns text or None on any failure."""
    if not settings.groq_available:
        return None

    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    body = {
        "model":      _GROQ_MODEL,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_GROQ_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            f"Groq API HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        )
    except Exception as exc:
        logger.warning(f"Groq API call failed: {exc}")
    return None


# ── Fallback ──────────────────────────────────────────────────────────────────

def _fallback_explanation(signal) -> str:
    """Build a plain-English summary from the top 3 reasoning points."""
    top = signal.reasoning_points[:3]
    return "Signal based on: " + ". ".join(top) if top else (
        f"{signal.action} signal with {signal.confidence:.0f}% confidence "
        f"(score {signal.final_score:+.1f})."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

async def generate_trade_explanation(signal) -> str:
    """Generate a beginner-friendly explanation for a TradingSignal.

    Calls Groq (llama-3.1-8b-instant) when GROQ_API_KEY is configured.
    Falls back to a bullet-point summary when the key is absent or the call fails.

    NOTE: When ANTHROPIC_API_KEY is available, swap _call_groq() for _call_claude()
    without changing this function's interface.

    Parameters
    ----------
    signal : TradingSignal — the signal to explain.

    Returns
    -------
    str — 1–3 sentence explanation, always succeeds (never raises).
    """
    user_prompt  = _build_user_prompt(signal)
    explanation  = await _call_groq(user_prompt)

    if explanation:
        logger.info(
            f"AI explanation generated for {signal.symbol} ({len(explanation)} chars)"
        )
    else:
        explanation = _fallback_explanation(signal)
        logger.info(
            f"AI explanation (fallback) for {signal.symbol} ({len(explanation)} chars)"
        )

    return explanation


def format_paper_trade_notification(trade: PaperTrade, explanation: str) -> str:
    """Format a paper-trade open event into a human-readable notification card.

    Suitable for console output, Telegram messages, or WebSocket push.
    The card is clearly marked as virtual / simulated to avoid any confusion
    with real money.

    Parameters
    ----------
    trade       : The freshly opened PaperTrade ORM record.
    explanation : Output from generate_trade_explanation().

    Returns
    -------
    str — multi-line formatted notification string.
    """
    direction  = trade.direction.value if hasattr(trade.direction, "value") else str(trade.direction)
    size_usd   = f"${trade.size_usd:,.2f}"
    confidence = trade.signal_confidence

    sep = "━" * 35

    return (
        f"\n"
        f"🎮 PAPER TRADE OPENED [VIRTUAL MONEY ONLY]\n"
        f"{sep}\n"
        f"Symbol:     {trade.symbol:<10}  Direction: {direction}\n"
        f"Entry:      {trade.entry_price:.5f}\n"
        f"Stop-Loss:  {trade.stop_loss:.5f}   Take-Profit: {trade.take_profit:.5f}\n"
        f"Confidence: {confidence:.0f}%{' ' * 11}Size: {size_usd}\n"
        f"{sep}\n"
        f"AI Analysis: {explanation}\n"
        f"{sep}\n"
        f"⚠️  This is a simulated trade. No real money is used.\n"
    )
