"""Phase 2: LLM-driven renderers.
All Telegram messages are now written dynamically by the LLM in a short, punchy Eagle Eyes style.
"""
from __future__ import annotations

from .events import (
    AlertEvent,
    AlertCategory,
    RawTextPayload,
    ReportPayload,
    Severity,
    ShortlistPayload,
    TradeEntryPayload,
    TradeEntryRawPayload,
    TradeExitPayload,
)
from utils.llm import call_llm_chat

_SEVERITY_BADGE = {
    Severity.INFO: "🔵",
    Severity.SUCCESS: "🟢",
    Severity.WARNING: "🟠",
    Severity.CRITICAL: "🔴",
    Severity.EMERGENCY: "🚨",
}

_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"
_CATEGORY_LABEL = {
    "TRADE": "Trade", "FNO_SIGNAL": "F&O", "SHORTLIST": "Shortlist",
    "DISCOVERY": "Discovery", "NEWS_EVENT": "Market News",
    "WEEKLY_REPORT": "Weekly Report", "OPERATIONS": "System",
}


async def _render_trade_entry(payload: TradeEntryPayload) -> str:
    d = payload.decision
    sym = d.symbol.replace(".NS", "")
    side = getattr(d, "action", "BUY")
    is_buy = side == "BUY"
    entry = getattr(d, "entry", None) or getattr(d, "entry_price", 0.0) or 0.0
    stop = getattr(d, "stop", None) or getattr(d, "stop_loss", 0.0) or 0.0
    target = getattr(d, "target", None) or getattr(d, "take_profit", 0.0) or 0.0

    from utils.sector_cache import get_sector
    sector = get_sector(f"{sym}.NS")

    reasons = getattr(d, "reasons", None) or []
    expert_note = next((r for r in reasons if not str(r).startswith("[web]") and len(str(r)) > 40), "")
    if not expert_note:
        expert_note = "Stock is showing strong action."

    prompt = (
        f"You are an expert stock market trader writing a quick, punchy Telegram alert (like the Eagle Eyes channel).\n"
        f"Write a short alert for a {'LONG' if is_buy else 'SHORT'} trade.\n"
        f"Symbol: {sym}\n"
        f"Sector: {sector}\n"
        f"CMP (Entry): {entry}\n"
        f"Support/Stop: {stop}\n"
        f"Target/Upside: {target}\n"
        f"Rationale/Note: {expert_note}\n\n"
        f"Instructions:\n"
        f"- Do NOT use a rigid template or bullet points.\n"
        f"- Keep it human and extremely short (max 2-3 lines).\n"
        f"- Use emojis like 👀 or 🔥 or 📉.\n"
        f"- Output ONLY the final message."
    )
    res = await call_llm_chat([{"role": "user", "content": prompt}], max_tokens=150, temperature=0.7)
    return res.strip() if res else f"Keep eyes on [{sector}] {sym} CMP {entry} 👀\n{expert_note}"


async def _render_trade_exit(payload: TradeExitPayload) -> str:
    sym = payload.symbol.replace(".NS", "")
    win = payload.pnl >= 0
    notional = payload.qty * payload.entry
    pnl_pct = (payload.pnl / notional * 100) if notional else 0.0
    
    prompt = (
        f"You are an expert trader updating your Telegram channel.\n"
        f"Write a short alert for closing a position in {sym}.\n"
        f"Result: {'Win' if win else 'Loss'}\n"
        f"P&L: ₹{payload.pnl:,.0f} ({pnl_pct:+.1f}%)\n"
        f"Reason: {payload.reason.replace('_', ' ').title()}\n\n"
        f"Instructions:\n"
        f"- Keep it short, conversational, and punchy.\n"
        f"- Use ✅ for a win or 🛑 for a loss.\n"
        f"- Output ONLY the final message."
    )
    res = await call_llm_chat([{"role": "user", "content": prompt}], max_tokens=100, temperature=0.7)
    return res.strip() if res else f"Position closed in {sym} - {'Win' if win else 'Loss'} ({pnl_pct:+.1f}%)"


async def _render_shortlist(payload: ShortlistPayload) -> str:
    c = payload.candidate
    sym = c.symbol.replace(".NS", "")
    entry = getattr(c, "entry", None) or getattr(c, "entry_price", 0.0) or 0.0
    stop = getattr(c, "stop", None) or getattr(c, "stop_loss", 0.0) or 0.0
    
    from utils.sector_cache import get_sector
    sector = get_sector(f"{sym}.NS")
    
    expert_note = payload.ai_note or ""
    if not expert_note and payload.crawl_data and payload.crawl_data.get("search_answer"):
        expert_note = payload.crawl_data["search_answer"]

    prompt = (
        f"You are an expert stock trader. Write a short, punchy Telegram alert for a shortlisted stock (watchlist) in the style of the 'Eagle Eyes' channel.\n"
        f"Symbol: {sym}\n"
        f"Sector: {sector}\n"
        f"CMP (Entry): {entry}\n"
        f"Support: {stop}\n"
        f"Note/News: {expert_note}\n\n"
        f"Instructions:\n"
        f"- Be extremely brief and human (max 2-3 lines).\n"
        f"- Do NOT use bullet points or rigid structures.\n"
        f"- Use emojis like 🔥 or 👀.\n"
        f"- Output ONLY the final message."
    )
    res = await call_llm_chat([{"role": "user", "content": prompt}], max_tokens=150, temperature=0.7)
    return res.strip() if res else f"Keep eyes on [{sector}] {sym} 👀"


async def _render_report(payload: ReportPayload) -> str:
    # Use AI commentary directly if available, otherwise just basic
    return payload.ai_commentary or "Weekly Portfolio Report generated."


async def _render_raw_text(event: AlertEvent, payload) -> str:
    text = getattr(payload, "text", str(payload))
    if event.category == AlertCategory.NEWS_EVENT:
        prompt = (
            f"You are the admin of a premium stock market Telegram channel.\n"
            f"Rewrite this market news alert in a short, punchy, human-expert style (like 'Eagle Eyes').\n\n"
            f"Original News Data:\n{text}\n\n"
            f"Instructions:\n"
            f"- Make it eye-catching with emojis like ⚡, 📌, or 👀.\n"
            f"- No bullet points. Just conversational flow.\n"
            f"- Do not add any disclaimers.\n"
            f"- Output ONLY the final message."
        )
        res = await call_llm_chat([{"role": "user", "content": prompt}], max_tokens=200, temperature=0.7)
        if res:
            return res.strip()
    
    # Fallback to appending severity for other types
    badge = _SEVERITY_BADGE.get(event.severity, "")
    label = _CATEGORY_LABEL.get(event.category.value, event.category.value)
    return f"{badge} <b>{label}</b>\n{_DIVIDER}\n{text}"


async def render_async(event: AlertEvent) -> str:
    """Entry point for all template rendering — dispatches by payload type."""
    p = event.payload
    if isinstance(p, TradeEntryPayload):
        return await _render_trade_entry(p)
    elif isinstance(p, TradeExitPayload):
        return await _render_trade_exit(p)
    elif isinstance(p, ShortlistPayload):
        return await _render_shortlist(p)
    elif isinstance(p, ReportPayload):
        return await _render_report(p)
    elif isinstance(p, (RawTextPayload, TradeEntryRawPayload)):
        return await _render_raw_text(event, p)
    
    return f"[{event.category.name}] {event.action.name} (unsupported payload)"
