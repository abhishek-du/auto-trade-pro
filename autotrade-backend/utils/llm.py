# Dual LLM client: Groq for fast day-to-day inference, Claude for detailed explanations.
# Groq is tried first on every call; Claude is the fallback / explicit "explain" path.

from functools import lru_cache
from utils.config import settings
from utils.logger import logger

# Groq model used for all fast, real-time analysis
GROQ_MODEL  = "llama-3.3-70b-versatile"
# Claude model used for deep explanations (only when API key is present)
CLAUDE_MODEL = "claude-sonnet-4-6"


@lru_cache(maxsize=1)
def _groq_client():
    if not settings.groq_available:
        return None
    try:
        from groq import Groq
        return Groq(api_key=settings.GROQ_API_KEY)
    except Exception as exc:
        logger.warning(f"Groq client init failed: {exc}")
        return None


@lru_cache(maxsize=1)
def _claude_client():
    if not settings.claude_available:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    except Exception as exc:
        logger.warning(f"Claude client init failed: {exc}")
        return None


# ── Public interface ──────────────────────────────────────────────────────────

def quick_analysis(prompt: str, system: str = "You are a concise financial analyst.") -> str:
    """
    Fast inference via Groq (llama-3.3-70b).
    Used for: signal commentary, trade summaries, daily market recap.
    Falls back to an empty string if Groq is unavailable.
    """
    client = _groq_client()
    if client is None:
        logger.debug("Groq unavailable — skipping quick_analysis")
        return ""
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=512,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error(f"Groq quick_analysis failed: {exc}")
        return ""


def explain(prompt: str, system: str = "You are an expert trading strategy explainer.") -> str:
    """
    Detailed explanation via Claude.
    Used for: strategy deep-dives, indicator rationale, educational breakdowns.
    Falls back to Groq if Claude key is not yet configured.
    """
    client = _claude_client()
    if client is None:
        logger.info("Claude key not set — routing explain() to Groq instead")
        return quick_analysis(prompt, system)
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.error(f"Claude explain failed: {exc}. Falling back to Groq.")
        return quick_analysis(prompt, system)


def summarise_signal(symbol: str, signal: str, score: float, indicators: dict) -> str:
    """Convenience wrapper: generate a one-paragraph Groq commentary for a trade signal."""
    prompt = (
        f"Symbol: {symbol}\n"
        f"Signal: {signal}  (confluence score: {score:.3f}/1.0)\n"
        f"Indicator snapshot: {indicators}\n\n"
        f"Write a 2-sentence trading commentary explaining why this signal was generated "
        f"and the key risks to watch. Be concise and factual."
    )
    return quick_analysis(prompt)


def explain_signal(symbol: str, signal: str, reasoning: str) -> str:
    """Convenience wrapper: ask Claude for a detailed educational explanation of a signal."""
    prompt = (
        f"A paper-trading system generated a {signal} signal for {symbol}.\n"
        f"Internal reasoning: {reasoning}\n\n"
        f"Explain in plain English (3-4 paragraphs) what each indicator is saying, "
        f"why they converged on this signal, and what a trader should watch for next."
    )
    return explain(prompt)
