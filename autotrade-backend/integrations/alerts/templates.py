"""Phase 2: real per-category renderers.

Decision-first / reason-second / detail-third structure per the redesign
brief -- replaces Phase 1's thin wrapper around the old fmt_entry/fmt_exit/
fmt_shortlist_alert (which is now dead code, deleted from
integrations/telegram_service.py along with send()/fire()).

Telegram's HTML subset has no color/font-color support (only b/strong, i/em,
u, s, span class="tg-spoiler", a, code, pre, blockquote) -- the "color"
language in the brief is approximated here with a fixed severity-emoji
badge, the only real lever Telegram HTML offers.
"""
from __future__ import annotations

from .events import (
    AlertEvent,
    RawTextPayload,
    ReportPayload,
    Severity,
    ShortlistPayload,
    TradeEntryPayload,
    TradeEntryRawPayload,
    TradeExitPayload,
)

_SEVERITY_BADGE = {
    Severity.INFO: "🔵",
    Severity.SUCCESS: "🟢",
    Severity.WARNING: "🟠",
    Severity.CRITICAL: "🔴",
    Severity.EMERGENCY: "🚨",
}

_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"


def _score_bar(val: float, width: int = 10) -> str:
    val = float(val or 0)
    filled = min(width, max(0, round((val + 100) / 20)))
    return "█" * filled + "░" * (width - filled)


def _strongest_factor(hub: dict) -> tuple[str, float] | None:
    """Pick the single largest-magnitude factor to lead the one-line reason
    with -- the decision-first summary names the ONE thing that mattered
    most, not all seven; the full breakdown is still in the detail section."""
    labels = {
        "technical": "Technical", "news": "News", "sector": "Sector",
        "macro": "Macro/FII", "earnings": "Earnings",
        "fundamental": "Fundamentals", "options": "Options flow",
    }
    best = None
    for key, label in labels.items():
        v = float(hub.get(key, 0) or 0)
        if best is None or abs(v) > abs(best[1]):
            best = (label, v)
    return best


def _render_trade_entry(payload: TradeEntryPayload) -> str:
    d = payload.decision
    sym = d.symbol.replace(".NS", "")
    side = getattr(d, "action", "BUY")
    is_buy = side == "BUY"
    entry = getattr(d, "entry", None) or getattr(d, "entry_price", 0.0) or 0.0
    stop = getattr(d, "stop", None) or getattr(d, "stop_loss", 0.0) or 0.0
    target = getattr(d, "target", None) or getattr(d, "take_profit", 0.0) or 0.0
    conf = getattr(d, "confidence", 0) or 0
    strategy = getattr(d, "strategy", "") or getattr(d, "timeframe", "")
    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr = round(reward / risk, 1) if risk > 0 else 0

    hub = getattr(d, "hub_subscores", {}) or {}
    reasoning = hub.get("reasoning", {}) if isinstance(hub.get("reasoning"), dict) else {}
    score = getattr(d, "master_score", None) or getattr(d, "final_score", None)

    lines = [
        f"{'🟢' if is_buy else '🔴'} <b>{'LONG' if is_buy else 'SHORT'} POSITION OPENED</b>",
        _DIVIDER,
        f"<b>{sym}</b>  ·  {payload.qty} shares  ·  {strategy or 'signal'}",
        f"Entry <b>₹{entry:,.2f}</b>  ·  Stop ₹{stop:,.2f}  ·  Target ₹{target:,.2f}",
        f"R:R <b>{rr}×</b>  ·  Confidence <b>{conf:.0f}%</b>" + (f"  ·  Score {score:+.0f}" if score is not None else ""),
    ]

    # ── Reason: the single strongest factor, one line ────────────────────────
    best = _strongest_factor(reasoning or hub)
    if best and best[1] != 0:
        label, val = best
        lines.append(f"\nWhy: <b>{label}</b> {'supportive' if val > 0 else 'against consensus but overridden'} ({val:+.0f})")
    reasons = getattr(d, "reasons", None) or []
    expert_note = next((r for r in reasons if not str(r).startswith("[web]") and len(str(r)) > 40), None)
    if expert_note:
        lines.append(f"<i>{str(expert_note)[:220]}</i>")

    # ── Detail: full 7-factor breakdown, only if present ─────────────────────
    factor_keys = ("technical", "news", "sector", "macro", "earnings", "fundamental", "options")
    if any(k in (reasoning or hub) for k in factor_keys) or any(k in hub for k in factor_keys):
        lines += [f"\n{_DIVIDER}", "<b>7-Factor Breakdown</b>"]
        for key, label in (
            ("technical", "Technical"), ("news", "News"), ("sector", "Sector"),
            ("macro", "Macro/FII"), ("earnings", "Earnings"),
            ("fundamental", "Fundamentals"), ("options", "Options"),
        ):
            v = float((reasoning or hub).get(key, hub.get(key, 0)) or 0)
            lines.append(f"{label:<13} {_score_bar(v)}  {v:+.0f}")

    lines.append(f"\n<i>⚠️ Paper mode — virtual money only</i>")
    return "\n".join(lines)


def _render_trade_exit(payload: TradeExitPayload) -> str:
    sym = payload.symbol.replace(".NS", "")
    win = payload.pnl >= 0
    notional = payload.qty * payload.entry
    pnl_pct = (payload.pnl / notional * 100) if notional else 0.0
    reason_str = payload.reason.replace("_", " ").title()

    lines = [
        f"{'✅' if win else '🛑'} <b>POSITION CLOSED</b>  ·  {reason_str}",
        _DIVIDER,
        f"<b>{sym}</b>  ·  {payload.side}  ·  {payload.qty} shares",
        f"Entry ₹{payload.entry:,.2f}  →  Exit ₹{payload.exit_price:,.2f}",
        f"\n<b>P&L: {'+' if win else ''}₹{payload.pnl:,.0f}  ({pnl_pct:+.1f}%)</b>",
        f"\n<i>⚠️ Paper mode — virtual money only</i>",
    ]
    return "\n".join(lines)


def _render_shortlist(payload: ShortlistPayload) -> str:
    c = payload.candidate
    sym = c.symbol.replace(".NS", "")
    entry = getattr(c, "entry", None) or getattr(c, "entry_price", 0.0) or 0.0
    stop = getattr(c, "stop", None) or getattr(c, "stop_loss", 0.0) or 0.0
    hub = getattr(c, "hub_subscores", {}) or {}
    signal = hub.get("signal") or "BUY"

    lines = [
        f"{'🔥' if signal == 'STRONG_BUY' else '👀'} <b>{'STRONG ' if signal == 'STRONG_BUY' else ''}SHORTLIST — {sym}</b>",
        _DIVIDER,
        f"Score <b>{payload.score:+.1f}</b>  ·  News {payload.news_subscore:+.0f}"
        + ("  ·  ✅ EXECUTED" if payload.executed else "  ·  watchlist only"),
    ]
    if entry:
        lines.append(f"Entry ₹{entry:,.2f}" + (f"  ·  Stop ₹{stop:,.2f}" if stop else ""))

    if payload.ai_note:
        lines.append(f"\n<i>{payload.ai_note[:400]}</i>")
    elif payload.crawl_data and payload.crawl_data.get("search_answer"):
        lines.append(f"\n<i>{payload.crawl_data['search_answer'][:400]}</i>")

    reasoning = hub.get("reasoning", {}) if isinstance(hub.get("reasoning"), dict) else {}
    factor_keys = ("technical", "news", "sector", "macro", "earnings", "fundamental", "options")
    if any(k in reasoning for k in factor_keys) or any(k in hub for k in factor_keys):
        lines += [f"\n{_DIVIDER}", "<b>7-Factor Breakdown</b>"]
        for key, label in (
            ("technical", "Technical"), ("news", "News"), ("sector", "Sector"),
            ("macro", "Macro/FII"), ("earnings", "Earnings"),
            ("fundamental", "Fundamentals"), ("options", "Options"),
        ):
            v = float(reasoning.get(key, hub.get(key, 0)) or 0)
            lines.append(f"{label:<13} {_score_bar(v)}  {v:+.0f}")

    return "\n".join(lines)


def _render_report(payload: ReportPayload) -> str:
    m = payload.metrics or {}
    has_metrics = m.get("sharpe_ratio") is not None

    lines = [f"📈 <b>Weekly Portfolio Report</b>", _DIVIDER]

    # ── Decision-first: rebalance signals, if any ─────────────────────────────
    if payload.rebalance_trades:
        lines.append(f"<b>{len(payload.rebalance_trades)} rebalance signal(s)</b>")
        for t in payload.rebalance_trades[:10]:
            emoji = "🟢" if t["action"] == "BUY" else "🔴"
            lines.append(
                f"{emoji} <b>{t['action']}</b> {t['symbol'].replace('.NS', '')}: "
                f"{t['current_weight']:.1f}% → {t['target_weight']:.1f}% (drift {t['drift']:.1f}%)"
            )
    else:
        lines.append("<b>Portfolio within tolerance</b> — no rebalancing needed")

    # ── Reason: one-line risk read ────────────────────────────────────────────
    if has_metrics:
        alpha = m["jensens_alpha"]
        lines.append(f"\nWhy: Sharpe <b>{m['sharpe_ratio']:.2f}</b>, alpha {'positive' if alpha and alpha > 0 else 'negative'} ({alpha:+.2f}%)")
    else:
        lines.append("\n<i>Insufficient history yet for risk metrics</i>")

    if payload.ai_commentary:
        lines.append(f"\n<i>{payload.ai_commentary[:500]}</i>")

    # ── Detail: structured metrics table + top sectors/positions ─────────────
    if has_metrics:
        lines += [
            f"\n{_DIVIDER}",
            "<b>Risk Metrics (30d)</b>",
            f"Return {m.get('portfolio_return', 0):+.1f}%  ·  NIFTY {m.get('benchmark_return', 0):+.1f}%  ·  Beta {m.get('portfolio_beta', 0):.2f}",
            f"Sharpe {m.get('sharpe_ratio', 0):.2f}  ·  Treynor {m.get('treynor_ratio', 0):.2f}  ·  Alpha {m.get('jensens_alpha', 0):+.2f}%",
        ]

    if payload.sector_weights:
        top_sectors = sorted(payload.sector_weights.items(), key=lambda x: x[1], reverse=True)[:5]
        lines.append("\n<b>Top Sectors</b>")
        lines += [f"{s}: {w:.1f}%" for s, w in top_sectors]

    return "\n".join(lines)


_CATEGORY_LABEL = {
    "TRADE": "Trade", "FNO_SIGNAL": "F&O", "SHORTLIST": "Shortlist",
    "DISCOVERY": "Discovery", "NEWS_EVENT": "Market News",
    "WEEKLY_REPORT": "Weekly Report", "OPERATIONS": "System",
}


def _render_raw_text(event: AlertEvent, payload) -> str:
    """Call sites for these categories build their own message body (F&O,
    discovery, news, operations, weekly report, and the agent's hub-override
    'TRADE PLACED' follow-up) -- Phase 2 doesn't restructure that content
    (would mean re-touching ~15 call sites, out of this phase's scope), but
    every one of them now gets a consistent severity badge + category tag
    prepended here at render time, so it's visually recognizable at a glance
    even though the body itself is category-specific."""
    badge = _SEVERITY_BADGE.get(event.severity, "")
    label = _CATEGORY_LABEL.get(event.category.value, event.category.value)
    header = f"{badge} <b>{label}</b>"
    return f"{header}\n{_DIVIDER}\n{payload.text}"


def render(event: AlertEvent) -> str:
    p = event.payload

    if isinstance(p, TradeEntryPayload):
        return _render_trade_entry(p)
    if isinstance(p, TradeExitPayload):
        return _render_trade_exit(p)
    if isinstance(p, ShortlistPayload):
        return _render_shortlist(p)
    if isinstance(p, ReportPayload):
        return _render_report(p)
    if isinstance(p, (RawTextPayload, TradeEntryRawPayload)):
        return _render_raw_text(event, p)

    raise TypeError(f"No renderer for payload type {type(p)!r} (event={event.category}/{event.action})")
