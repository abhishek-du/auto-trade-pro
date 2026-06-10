"""Telegram notification service for AutoTrade Pro.

Sends real-time trade alerts to a Telegram chat/channel using the Bot API.
Uses httpx (already a project dependency) — no new packages needed.

All sends are fire-and-forget: fire() schedules the coroutine on the running
event loop so the trading pipeline is never blocked by a slow network call.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from utils.config import settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

_REGIME_EMOJI: dict[str, str] = {
    "BULL_TRENDING":  "🐂",
    "BEAR_TRENDING":  "🐻",
    "RANGE":          "📊",
    "LOW_VOL_RANGE":  "😴",
    "HIGH_VOL_RANGE": "⚡",
}


# ── Core sender ───────────────────────────────────────────────────────────────

async def _post(text: str) -> None:
    token   = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_CHAT_ID",   "")
    if not token or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                _API_URL.format(token=token),
                json={
                    "chat_id":                  chat_id,
                    "text":                     text,
                    "parse_mode":               "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if r.status_code != 200:
                logger.debug(f"[telegram] {r.status_code}: {r.text[:120]}")
    except Exception as exc:
        logger.debug(f"[telegram] send failed: {exc}")


def fire(text: str) -> None:
    """Non-blocking send — schedules on the running event loop."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_post(text))
    except RuntimeError:
        pass   # no loop (sync context / tests) — skip silently


# ── Formatters ────────────────────────────────────────────────────────────────

def fmt_entry(decision) -> str:
    """Rich HTML alert for a new trade entry."""
    sym      = decision.symbol.replace(".NS", "")
    side     = decision.action          # "BUY" or "SELL"
    conf     = getattr(decision, "confidence",   0)
    score    = getattr(decision, "master_score", None)
    regime   = getattr(decision, "regime",       "")
    strategy = getattr(decision, "strategy",     "")
    reasons  = getattr(decision, "reasons",      []) or []
    risk_pct = round(getattr(decision, "risk_pct", 0) * 100, 2)

    side_emoji   = "🟢" if side == "BUY" else "🔴"
    regime_emoji = _REGIME_EMOJI.get(regime, "📈")
    score_str    = f"  ·  Score <b>{score:+.0f}</b>" if score is not None else ""

    reasons_block = (
        "\n".join(f"  • {r}" for r in reasons[:4])
        if reasons
        else "  • Signal confirmed by technical indicators"
    )

    return (
        f"{side_emoji} <b>{side} SIGNAL</b> {side_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{sym}</b>  ·  <code>{strategy}</code>\n"
        f"\n"
        f"<b>💰 Entry :</b>  ₹{decision.entry:,.2f}\n"
        f"<b>🛑 Stop  :</b>  ₹{decision.stop:,.2f}\n"
        f"<b>🎯 Target:</b>  ₹{decision.target:,.2f}\n"
        f"<b>📦 Qty   :</b>  {decision.qty} shares\n"
        f"\n"
        f"<b>🧠 Confidence:</b> {conf}%{score_str}\n"
        f"{regime_emoji} <b>Regime:</b> {regime}  ·  Risk {risk_pct}%\n"
        f"\n"
        f"<b>📊 Why this trade:</b>\n"
        f"{reasons_block}\n"
        f"\n"
        f"<i>⚠️ Paper mode — virtual money only</i>"
    )


def fmt_exit(
    symbol:     str,
    side:       str,
    entry:      float,
    exit_price: float,
    qty:        int,
    pnl:        float,
    reason:     str,
) -> str:
    """Rich HTML alert for a closed trade."""
    sym       = symbol.replace(".NS", "")
    notional  = qty * entry
    pnl_pct   = (pnl / notional * 100) if notional else 0.0
    win        = pnl >= 0
    icon       = "✅" if win else "❌"
    arrow      = "▲" if win else "▼"
    reason_str = reason.replace("_", " ").replace(":", " · ")

    return (
        f"{icon} <b>TRADE CLOSED</b> {icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{sym}</b>  ·  {side}\n"
        f"\n"
        f"<b>Entry :</b>  ₹{entry:,.2f}\n"
        f"<b>Exit  :</b>  ₹{exit_price:,.2f}\n"
        f"<b>Qty   :</b>  {qty} shares\n"
        f"\n"
        f"<b>P&amp;L:  {arrow} ₹{abs(pnl):,.0f}  ({pnl_pct:+.1f}%)</b>\n"
        f"<b>Reason:</b> {reason_str}\n"
        f"\n"
        f"<i>⚠️ Paper mode — virtual money only</i>"
    )


def fmt_cycle_summary(n_traded: int, n_scanned: int, equity: float, cash: float,
                      open_pos: int) -> str:
    """Short end-of-cycle summary (sent only when at least one trade executed)."""
    return (
        f"📈 <b>Cycle Summary</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Scanned: {n_scanned}  ·  Traded: <b>{n_traded}</b>  ·  Open: {open_pos}\n"
        f"Equity: ₹{equity:,.0f}  ·  Cash: ₹{cash:,.0f}\n"
    )
