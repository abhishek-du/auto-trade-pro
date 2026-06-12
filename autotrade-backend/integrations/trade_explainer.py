# Builds the human-readable, expert-style narrative for every trade lifecycle stage:
#   • build_expert_note()    — "Why Bought" at entry (entry rationale + forward plan)
#   • build_hold_analysis()  — "Why Still Holding" for open positions (live monitoring)
#   • build_postmortem_note() — "Profit/Loss Explanation" when trade closes
#
# Synchronous by design — runs in a worker thread inside the journal sync.
# Falls back gracefully to deterministic templates if Groq is unavailable.

from __future__ import annotations

from utils.config import settings
from utils.logger import logger

_GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.3-70b-versatile"

# llama-3.3-70b-versatile free-tier limits: 30 RPM / 1K RPD / 12K TPM
# 2.5 s gap → 24 RPM, safely under 30 RPM.  400 tokens × 24 = 9.6K TPM < 12K TPM.
_GROQ_MIN_INTERVAL = 2.5   # seconds between calls
_groq_last_call_ts: float = 0.0
# Circuit breaker: when daily quota (RPD) is exhausted, skip all remaining calls
# this session rather than sleeping 200+ s per trade.  Resets each process start.
_groq_quota_exhausted: bool = False


def _groq_sync(prompt: str, system: str, *, max_tokens: int = 400,
               timeout: float = 20.0) -> str:
    if not getattr(settings, "GROQ_API_KEY", ""):
        return ""
    import time
    import httpx
    global _groq_last_call_ts, _groq_quota_exhausted

    # Skip immediately if we already know the daily quota is gone
    if _groq_quota_exhausted:
        return ""

    # Pace calls to ≤24 RPM
    gap = _groq_last_call_ts + _GROQ_MIN_INTERVAL - time.monotonic()
    if gap > 0:
        time.sleep(gap)

    for attempt in range(3):
        try:
            _groq_last_call_ts = time.monotonic()
            resp = httpx.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": _GROQ_MODEL,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": prompt}],
                      "max_tokens": max_tokens, "temperature": 0.25},
                timeout=timeout,
            )
            if resp.status_code == 429:
                wait = float(resp.headers.get("retry-after", 60))
                if wait > 60:
                    # retry-after > 60 s = daily RPD quota exhausted, not a burst spike.
                    # Trip the circuit breaker so we stop trying for the rest of this run.
                    _groq_quota_exhausted = True
                    logger.warning(
                        f"[trade_explainer] Groq RPD quota exhausted (retry-after {wait:.0f}s) "
                        f"— switching to template notes for this session"
                    )
                    return ""
                logger.warning(
                    f"[trade_explainer] Groq 429 burst — retry-after {wait:.0f}s "
                    f"(attempt {attempt+1}/3)"
                )
                time.sleep(wait + 1)
                continue
            resp.raise_for_status()
            data = resp.json()
            return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            logger.warning(f"[trade_explainer] Groq call failed: {exc}")
            return ""
    logger.warning("[trade_explainer] Groq: 3 retries exhausted — using template fallback")
    return ""


# ── ETA to first target ────────────────────────────────────────────────────────

def estimate_eta_to_target(entry: float, target_1: float, atr: float,
                           direction: str) -> str:
    """ATR-based time-to-Target-1 estimate."""
    try:
        dist = abs(target_1 - entry)
        if atr and atr > 0 and dist > 0:
            days = dist / (0.6 * atr)
            lo = max(1, round(days * 0.7))
            hi = max(lo + 1, round(days * 1.4))
            return f"≈ {lo}–{hi} trading days"
        return "≈ 5–15 trading days"
    except Exception:
        return "≈ 5–15 trading days"


# ── Deterministic templates (always available, no LLM needed) ─────────────────

def _entry_template(symbol: str, side: str, entry: float, stop: float,
                    target_1: float, target_2: float, confidence: float,
                    hub: dict | None, reasoning: str, strategy: str,
                    regime: str) -> str:
    """Rich template for 'Why Bought' — covers all 7 factors and plan."""
    rr = 0.0
    risk = abs(entry - stop)
    try:
        if risk > 0:
            rr = abs(target_1 - entry) / risk
    except Exception:
        pass

    sl_pct  = abs(entry - stop) / entry * 100 if entry else 0
    t1_pct  = abs(target_1 - entry) / entry * 100 if entry else 0
    action  = "LONG" if side == "BUY" else "SHORT"

    lines = [
        f"📥 {action} {symbol} @ ₹{entry:,.2f}  |  Conviction: {confidence:.0f}%  |  Regime: {regime}  |  Strategy: {strategy}",
        "",
        "📊 SIGNAL RATIONALE",
    ]

    if hub:
        scored = [(k, v) for k, v in hub.items() if isinstance(v, (int, float))]
        drivers  = [(k, v) for k, v in scored if v >= 40]
        neutral  = [(k, v) for k, v in scored if 10 <= v < 40]
        headwind = [(k, v) for k, v in scored if v < 10]
        if drivers:
            lines.append("  ✅ Strong: " + ", ".join(f"{k.upper()}={v:+.0f}" for k, v in drivers))
        if neutral:
            lines.append("  ➡ Neutral: " + ", ".join(f"{k.upper()}={v:+.0f}" for k, v in neutral))
        if headwind:
            lines.append("  ⚠️ Headwind: " + ", ".join(f"{k.upper()}={v:+.0f}" for k, v in headwind))
    else:
        lines.append("  Hub scores not available at entry.")

    if reasoning:
        lines.append("")
        lines.append("🔍 ENTRY SIGNALS")
        for ln in [l.strip() for l in reasoning.splitlines() if l.strip()][:6]:
            lines.append(f"  • {ln}")

    lines += [
        "",
        "📐 TRADE LEVELS",
        f"  Stop-loss   : ₹{stop:,.2f}  ({sl_pct:.1f}% risk — max loss if wrong)",
        f"  Target 1    : ₹{target_1:,.2f}  ({t1_pct:.1f}% gain — book 50% here)",
        f"  Target 2    : ₹{target_2:,.2f}  (trail remaining with tightened stop)",
        f"  Risk:Reward : 1:{rr:.1f}",
        "",
        "📋 EXIT PLAN",
        "  • Book 40–50% at T1, move stop to break-even on the balance.",
        "  • Trail remaining position toward T2 using ATR-based stop.",
        f"  • Hard cut below ₹{stop:,.2f} — no averaging down.",
    ]

    return "\n".join(lines)


def _hold_template(symbol: str, side: str, entry: float, current: float,
                   stop: float, target_1: float, target_2: float,
                   pnl: float, pnl_pct: float, days_held: int,
                   hub: dict | None, strategy: str) -> str:
    """Template for 'Why Still Holding' — live monitoring note."""
    risk       = abs(entry - stop)
    dist_stop  = abs(current - stop)
    dist_t1    = abs(target_1 - current)
    pct_stop   = dist_stop / risk * 100 if risk > 0 else 0
    pct_t1     = dist_t1 / abs(target_1 - entry) * 100 if abs(target_1 - entry) > 0 else 0
    momentum   = "▲ IN PROFIT" if pnl >= 0 else "▼ IN DRAWDOWN"
    sl_status  = "✅ Well above stop" if pct_stop > 60 else ("⚠️ Stop nearby!" if pct_stop < 25 else "ℹ️ Approaching stop zone")

    lines = [
        f"⏳ HOLDING: {symbol} since {days_held}d  |  P&L: ₹{pnl:+,.0f} ({pnl_pct:+.2f}%)  |  {momentum}",
        "",
        "📍 POSITION STATUS",
        f"  Entry          : ₹{entry:,.2f}",
        f"  Current        : ₹{current:,.2f}  ({(current-entry)/entry*100:+.1f}% from entry)",
        f"  Distance to SL : ₹{dist_stop:,.2f}  ({pct_stop:.0f}% of original risk remaining)  {sl_status}",
        f"  Distance to T1 : ₹{dist_t1:,.2f}  ({100-pct_t1:.0f}% of way to target)",
        "",
        "🔍 WHY STILL HOLDING",
    ]

    reasons = []
    if pnl >= 0:
        reasons.append(f"Position is profitable (+₹{pnl:,.0f}). Thesis is playing out — letting winners run.")
    else:
        reasons.append(f"Position is in drawdown (₹{pnl:,.0f}). Still above the stop-loss — thesis being tested.")

    if hub:
        scored = [(k, v) for k, v in hub.items() if isinstance(v, (int, float))]
        positive = [(k, v) for k, v in scored if v >= 20]
        if positive:
            reasons.append("Hub intelligence still supports: " + ", ".join(f"{k}({v:+.0f})" for k, v in positive[:4]))
        else:
            reasons.append("Hub scores are weakening — watch for exit signal.")

    reasons.append(f"Strategy '{strategy}' — position within valid range, stop intact.")
    for r in reasons:
        lines.append(f"  • {r}")

    lines += [
        "",
        "🚨 EXIT CONDITIONS TO WATCH",
        f"  • Immediate exit: close below ₹{stop:,.2f} (stop-loss hit)",
        f"  • Book 50% profit: price reaches ₹{target_1:,.2f} (T1)",
        f"  • Full target: price reaches ₹{target_2:,.2f} (T2)",
        "  • Early exit: Hub 7-factor score turns negative",
        "  • Review if held >10 days without progress toward T1",
    ]

    return "\n".join(lines)


def _postmortem_template(symbol: str, side: str, entry: float, exit_price: float,
                         pnl: float, pnl_pct: float, reason: str,
                         target_achieved: str, duration: str) -> str:
    """Rich template for 'Expert Note' on closed trade."""
    outcome  = "PROFIT" if pnl >= 0 else "LOSS"
    emoji    = "✅" if pnl >= 0 else "❌"
    move_pct = abs(exit_price - entry) / entry * 100 if entry else 0
    direction_text = "above" if (side == "BUY" and exit_price > entry) or (side == "SELL" and exit_price < entry) else "against"

    lines = [
        f"{emoji} CLOSED: {side} {symbol} | Outcome: {outcome} ₹{pnl:+,.0f} ({pnl_pct:+.1f}%) | Duration: {duration}",
        "",
        "📊 TRADE SUMMARY",
        f"  Entry price  : ₹{entry:,.2f}",
        f"  Exit price   : ₹{exit_price:,.2f}  (moved {move_pct:.1f}% {direction_text} entry)",
        f"  Exit reason  : {reason}",
        f"  Target status: {target_achieved}",
        "",
    ]

    if pnl >= 0:
        lines += [
            "✅ WHAT WORKED",
            f"  The trade moved as expected. ",
            "  • Price respected the entry zone and moved in the planned direction.",
            "  • Risk management held — stop was never threatened before T1.",
            f"  • Booked ₹{pnl:,.0f} within {duration}. Capital recycled for next setup.",
        ]
    else:
        lines += [
            "❌ WHAT WENT WRONG",
            "  The trade moved against the entry thesis.",
            f"  • Price hit stop-loss at ₹{exit_price:,.2f} before reaching T1.",
            "  • Stop-loss worked as designed — contained the loss to the planned 1% risk.",
            f"  • Lost ₹{abs(pnl):,.0f} ({abs(pnl_pct):.1f}%) — within pre-defined max risk per trade.",
        ]

    lines += [
        "",
        "📚 LESSON",
    ]

    if "STOP" in reason.upper() or "SL" in reason.upper():
        lines.append("  Stop-loss exit was correct — do not second-guess risk management rules.")
        lines.append("  Review if stop placement was too tight relative to ATR on next similar setup.")
    elif "T1" in reason.upper() or "TARGET" in reason.upper():
        lines.append("  Good discipline booking at T1. Consider trailing stop on next similar trade.")
        lines.append("  If stock continued beyond T2, evaluate if partial exit and trailing was missed.")
    elif "HUB" in reason.upper():
        lines.append("  Intelligence-driven exit. Hub scores are a valuable early exit signal.")
    else:
        lines.append("  Manual or time-based exit. Define clearer mechanical exit rules going forward.")

    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────

def build_expert_note(symbol: str, direction: str, entry: float, stop: float,
                      target_1: float, target_2: float, confidence: float,
                      hub: dict | None, reasoning: str,
                      strategy: str = "HUB_SIGNAL", regime: str = "") -> str:
    """Entry rationale: 'Why Bought'. Uses Groq when enabled, else rich template."""
    template = _entry_template(symbol, direction, entry, stop, target_1, target_2,
                               confidence, hub, reasoning, strategy, regime)

    _llm_ok = getattr(settings, "SHEET_LOG_USE_LLM", False) and getattr(settings, "GROQ_API_KEY", "")
    if not _llm_ok:
        return template

    hub_txt = ", ".join(f"{k}={v:+.0f}" for k, v in (hub or {}).items())
    rr = 0.0
    try:
        risk = abs(entry - stop)
        if risk > 0:
            rr = abs(target_1 - entry) / risk
    except Exception:
        pass

    prompt = f"""You are a senior Indian equity swing trader writing a professional trade journal entry.
Write a 4-5 sentence expert note explaining WHY this trade was taken.
Be specific, use market terminology, mention the key factors driving the setup.
No bullet points — flowing professional prose like a seasoned fund manager.

Trade: {direction} {symbol}
Entry ₹{entry:.2f} | Stop ₹{stop:.2f} | T1 ₹{target_1:.2f} | T2 ₹{target_2:.2f} | R:R 1:{rr:.1f}
Confidence: {confidence:.0f}% | Strategy: {strategy} | Regime: {regime}
7-Factor Hub: {hub_txt or 'n/a'}
Signals: {reasoning[:500]}

Cover: (1) why bought at this level, (2) what technical/fundamental factor is the primary driver,
(3) what the R:R implies, (4) the single biggest risk to this trade."""

    note = _groq_sync(prompt, "You are an expert Indian equity trader and journal writer. Write clear, professional, jargon-appropriate prose.")
    return note or template


def build_hold_analysis(symbol: str, side: str, entry: float, current: float,
                        stop: float, target_1: float, target_2: float,
                        pnl: float, pnl_pct: float, days_held: int,
                        hub: dict | None, strategy: str,
                        reasoning: str = "", use_llm: bool = False) -> str:
    """Open position monitoring note: 'Why Still Holding + What to Watch'.

    Always uses the deterministic template — called on every 5-min sync tick for
    every open position, so LLM mode is off by default to protect RPD quota.
    Pass use_llm=True only when generating a one-off note outside of sync.
    """
    template = _hold_template(symbol, side, entry, current, stop, target_1, target_2,
                              pnl, pnl_pct, days_held, hub, strategy)

    _llm_ok = getattr(settings, "SHEET_LOG_USE_LLM", False) and getattr(settings, "GROQ_API_KEY", "")
    if not use_llm or not _llm_ok:
        return template

    hub_txt  = ", ".join(f"{k}={v:+.0f}" for k, v in (hub or {}).items())
    sl_dist  = abs(current - stop)
    t1_dist  = abs(target_1 - current)
    risk_pct = sl_dist / entry * 100 if entry else 0

    prompt = f"""You are a senior Indian equity trader monitoring an open swing trade.
Write a 4-5 sentence expert assessment of whether to HOLD, TIGHTEN STOP, or EXIT.
Be honest and analytical — if the trade is at risk, say so clearly.
Use market terminology, reference specific levels.

Trade: {side} {symbol} — OPEN {days_held} days
Entry ₹{entry:.2f} | Current ₹{current:.2f} | Stop ₹{stop:.2f} | T1 ₹{target_1:.2f} | T2 ₹{target_2:.2f}
Live P&L: ₹{pnl:+,.0f} ({pnl_pct:+.2f}%)
Distance to stop: ₹{sl_dist:.2f} ({risk_pct:.1f}% of entry)
Distance to T1: ₹{t1_dist:.2f}
Hub 7-Factor: {hub_txt or 'n/a'}
Original signals: {reasoning[:300]}

Cover: (1) is the original thesis still intact, (2) key price level to watch,
(3) whether to tighten stop or let it run, (4) estimated timeline to resolution."""

    note = _groq_sync(prompt, "You are an expert Indian equity trader reviewing an open position. Be analytical and direct.",
                      max_tokens=350)
    return note or template


def build_postmortem_note(symbol: str, direction: str, entry: float, exit_price: float,
                          pnl: float, pnl_pct: float, reason: str,
                          target_achieved: str, duration: str) -> str:
    """Profit/Loss explanation for a closed trade."""
    template = _postmortem_template(symbol, direction, entry, exit_price, pnl,
                                    pnl_pct, reason, target_achieved, duration)

    _llm_ok = getattr(settings, "SHEET_LOG_USE_LLM", False) and getattr(settings, "GROQ_API_KEY", "")
    if not _llm_ok:
        return template

    prompt = f"""You are a senior Indian equity trader writing a post-mortem for a closed trade.
Write 4-5 sentences of professional analysis. Be honest — if it was a loss, explain what failed.
If it was a profit, explain what worked. One concrete lesson at the end.

{direction} {symbol}: entry ₹{entry:.2f} → exit ₹{exit_price:.2f}
P&L: ₹{pnl:+,.0f} ({pnl_pct:+.1f}%) | Held: {duration} | Exit: {reason}
Result: {target_achieved}

Cover: (1) did the trade thesis play out, (2) was the exit timing/price good,
(3) what went right or wrong, (4) one actionable lesson for the next similar setup."""

    note = _groq_sync(prompt, "You are an expert equity trader reviewing your closed trades honestly.",
                      max_tokens=380)
    return note or template
