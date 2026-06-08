# Builds the human-readable, expert-style narrative + a time-to-target ETA for a
# trade, used by the spreadsheet journal. Everything here is best-effort and
# synchronous (it runs inside a worker thread from the journal sync) — it never
# raises into the caller and always degrades to a clean template if the LLM or
# any field is missing.

from __future__ import annotations

from utils.config import settings
from utils.logger import logger

# Groq via raw HTTP (httpx) — avoids the optional `groq` SDK package. Mirrors
# utils.llm.call_groq_chat but is synchronous, so it runs inside the journal's
# worker thread without touching the event loop.
_GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.3-70b-versatile"


def _groq_sync(prompt: str, system: str, *, max_tokens: int = 320,
               timeout: float = 12.0) -> str:
    if not getattr(settings, "GROQ_API_KEY", ""):
        return ""
    try:
        import httpx
        resp = httpx.post(
            _GROQ_URL,
            headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": _GROQ_MODEL,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": prompt}],
                  "max_tokens": max_tokens, "temperature": 0.3},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    except Exception as exc:
        logger.warning(f"[trade_explainer] Groq HTTP call failed: {exc}")
        return ""


# ── ETA to first target ───────────────────────────────────────────────────────

def estimate_eta_to_target(entry: float, target_1: float, atr: float,
                           direction: str) -> str:
    """Rough time-to-Target-1 estimate from ATR-based daily velocity.

    Daily ATR ≈ the stock's typical one-day range. A trending name covers
    roughly 0.6×ATR of *directional* progress per day, so:

        days ≈ distance_to_T1 / (0.6 × ATR)

    Returns a human range like "≈ 3–6 trading days". Falls back gracefully
    when ATR is unknown.
    """
    try:
        dist = abs(target_1 - entry)
        if atr and atr > 0 and dist > 0:
            days = dist / (0.6 * atr)
            lo = max(1, round(days * 0.7))
            hi = max(lo + 1, round(days * 1.4))
            return f"≈ {lo}–{hi} trading days"
        # No ATR — fall back to the engine's standard swing horizon.
        return "≈ 5–15 trading days"
    except Exception:
        return "≈ 5–15 trading days"


# ── Expert narrative ──────────────────────────────────────────────────────────

def _template_note(symbol: str, direction: str, entry: float, stop: float,
                   target_1: float, target_2: float, confidence: float,
                   hub: dict | None, reasoning: str) -> str:
    """Deterministic expert-style note — always available, no LLM needed."""
    rr = 0.0
    try:
        risk = abs(entry - stop)
        if risk > 0:
            rr = abs(target_1 - entry) / risk
    except Exception:
        pass

    bits = [
        f"{direction} {symbol} at ₹{entry:,.2f}. Conviction {confidence:.0f}%."
    ]
    if hub:
        strong = [k for k, v in hub.items() if isinstance(v, (int, float)) and v >= 40]
        weak   = [k for k, v in hub.items() if isinstance(v, (int, float)) and v <= -10]
        if strong:
            bits.append(f"Driven by strong {', '.join(strong)}.")
        if weak:
            bits.append(f"Headwind from {', '.join(weak)}.")
    bits.append(
        f"Stop ₹{stop:,.2f}, T1 ₹{target_1:,.2f}, T2 ₹{target_2:,.2f} "
        f"(R:R ≈ 1:{rr:.1f}). Booking 40–50% at T1, trailing the rest to T2."
    )
    if reasoning:
        first = [ln.strip() for ln in reasoning.splitlines() if ln.strip()][:2]
        if first:
            bits.append("Signals: " + "; ".join(first) + ".")
    return " ".join(bits)


def build_expert_note(symbol: str, direction: str, entry: float, stop: float,
                      target_1: float, target_2: float, confidence: float,
                      hub: dict | None, reasoning: str) -> str:
    """Expert trade rationale. Uses Groq when enabled, else a rich template.

    Synchronous by design — call from a worker thread, not the event loop.
    """
    template = _template_note(symbol, direction, entry, stop, target_1,
                              target_2, confidence, hub, reasoning)

    if not getattr(settings, "SHEET_LOG_USE_LLM", False):
        return template
    if not getattr(settings, "GROQ_API_KEY", ""):
        return template

    hub_txt = ", ".join(f"{k}={v}" for k, v in (hub or {}).items())
    prompt = (
        f"You are a veteran Indian-market swing trader writing a one-paragraph "
        f"journal note for a paper trade. Be concrete, calm and professional — "
        f"no hype, no disclaimers.\n\n"
        f"Trade: {direction} {symbol}\n"
        f"Entry ₹{entry:.2f} | Stop ₹{stop:.2f} | Target1 ₹{target_1:.2f} | "
        f"Target2 ₹{target_2:.2f} | Confidence {confidence:.0f}%\n"
        f"7-factor Hub score: {hub_txt or 'n/a'}\n"
        f"Engine reasoning: {reasoning[:600]}\n\n"
        f"In 3-4 sentences explain WHY this trade was taken, which factor is "
        f"carrying it, what the targets imply, and the single biggest risk."
    )
    note = _groq_sync(prompt, "You are an expert equities trader and journal writer.")
    return note or template


def build_postmortem_note(symbol: str, direction: str, entry: float, exit_price: float,
                          pnl: float, pnl_pct: float, reason: str,
                          target_achieved: str, duration: str) -> str:
    """Short retrospective written when a trade closes."""
    outcome = "profit" if pnl >= 0 else "loss"
    template = (
        f"Closed {direction} {symbol} at ₹{exit_price:,.2f} for a {outcome} of "
        f"₹{pnl:,.0f} ({pnl_pct:+.1f}%) after {duration}. "
        f"Exit reason: {reason}. {target_achieved}."
    )
    if not getattr(settings, "SHEET_LOG_USE_LLM", False) or not getattr(settings, "GROQ_API_KEY", ""):
        return template
    prompt = (
        f"You are a swing trader writing a one-paragraph post-mortem for a closed "
        f"paper trade. Be honest and instructive.\n\n"
        f"{direction} {symbol}: entry ₹{entry:.2f} → exit ₹{exit_price:.2f}, "
        f"P&L ₹{pnl:.0f} ({pnl_pct:+.1f}%), held {duration}, "
        f"exit reason {reason}, {target_achieved}.\n\n"
        f"In 2-3 sentences: did the thesis play out, was the exit good, and one lesson."
    )
    note = _groq_sync(prompt, "You are an expert equities trader reviewing your own trade.")
    return note or template
