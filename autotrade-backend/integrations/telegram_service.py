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
        logger.warning("[telegram] missing token or chat_id")
        return
    # api.telegram.org can be slow on this network — generous timeout + retries
    # so alerts (equity AND F&O) aren't silently dropped on a transient delay.
    import asyncio as _aio
    payload = {
        "chat_id":                  chat_id,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(_API_URL.format(token=token), json=payload)
            if r.status_code == 200:
                logger.info(f"[telegram] ✓ sent to {chat_id}")
                return
            logger.warning(f"[telegram] {r.status_code}: {r.text[:200]}")
            return  # non-200 (e.g. bad chat) — don't retry
        except Exception as exc:
            if attempt == 2:
                logger.warning(f"[telegram] send failed after retries: {exc}")
            else:
                await _aio.sleep(2)


async def send(text: str) -> None:
    """Awaitable send — use this inside async contexts for guaranteed delivery."""
    await _post(text)


def fire(text: str) -> None:
    """Non-blocking send — schedules on the running event loop."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_post(text))
    except RuntimeError:
        logger.warning("[telegram] fire() called outside async loop")


# ── Formatters ────────────────────────────────────────────────────────────────

def _score_bar(val: float, width: int = 10) -> str:
    """Render a filled/empty bar from a −100…+100 score."""
    val = float(val or 0)
    filled = min(width, max(0, round((val + 100) / 20)))
    return "█" * filled + "░" * (width - filled)


def _fmt_tech_proof(td: dict) -> str:
    """Build a concise technical-indicator proof block from tech_detail dict."""
    if not td:
        return ""
    lines = []

    rsi = td.get("rsi")
    if rsi is not None:
        rsi_sig = td.get("rsi_signal", "")
        rsi_tag = "🔴 OVERBOUGHT" if rsi_sig == "OVERBOUGHT" else ("🟢 OVERSOLD" if rsi_sig == "OVERSOLD" else "⚪ NEUTRAL")
        lines.append(f"  RSI({rsi:.1f}) → {rsi_tag}")

    mc = td.get("macd_cross", "")
    mh = td.get("macd_hist")
    if mc or mh is not None:
        mc_tag = "🟢 Bullish cross" if mc == "BULLISH_CROSS" else ("🔴 Bearish cross" if mc == "BEARISH_CROSS" else "—")
        hist_str = f"  hist={mh:+.2f}" if mh is not None else ""
        lines.append(f"  MACD → {mc_tag}{hist_str}")

    ema_trend = td.get("ema_trend", "")
    e20, e50, e200 = td.get("ema_20"), td.get("ema_50"), td.get("ema_200")
    if ema_trend:
        ema_tag = {"STRONG_BULL": "🟢🟢", "BULL": "🟢", "NEUTRAL": "⚪", "BEAR": "🔴", "STRONG_BEAR": "🔴🔴"}.get(ema_trend, ema_trend)
        parts = [f"EMA trend → {ema_tag} {ema_trend}"]
        if e20 and e50:
            parts.append(f"(20:{e20:.0f} / 50:{e50:.0f}{f' / 200:{e200:.0f}' if e200 else ''})")
        lines.append("  " + "  ".join(parts))

    adx = td.get("adx")
    if adx is not None:
        adx_dir = td.get("adx_direction", "")
        adx_str = td.get("adx_strength", "")
        lines.append(f"  ADX({adx:.1f}) → {adx_str} trend  {adx_dir}")

    stoch_k = td.get("stoch_k")
    stoch_sig = td.get("stoch_signal", "")
    if stoch_k is not None:
        stoch_tag = "🔴 OVERBOUGHT" if stoch_sig == "OVERBOUGHT" else ("🟢 OVERSOLD" if stoch_sig == "OVERSOLD" else "⚪")
        lines.append(f"  Stoch K({stoch_k:.1f}) → {stoch_tag}")

    st_dir = td.get("supertrend_dir", "")
    if st_dir:
        st_tag = "🟢 BULLISH" if st_dir == "BULLISH" else "🔴 BEARISH"
        lines.append(f"  Supertrend → {st_tag}")

    ich = td.get("ichimoku_signal", "")
    if ich and ich != "NEUTRAL":
        ich_tag = {"STRONG_BUY": "🟢🟢 STRONG BUY", "BUY": "🟢 BUY",
                   "SELL": "🔴 SELL", "STRONG_SELL": "🔴🔴 STRONG SELL"}.get(ich, ich)
        lines.append(f"  Ichimoku → {ich_tag}")

    vs = td.get("volume_surge")
    if vs and vs >= 1.5:
        lines.append(f"  Volume surge → {vs:.1f}× avg  📈")

    return "\n".join(lines)


def fmt_entry(decision, qty: float | None = None) -> str:
    """Rich HTML alert for a new trade entry with full 7-factor proof."""
    sym      = decision.symbol.replace(".NS", "")
    side     = decision.action
    conf     = getattr(decision, "confidence",       0)
    score    = getattr(decision, "master_score",     None) or getattr(decision, "final_score", None)
    regime   = getattr(decision, "regime",           "")
    strategy = getattr(decision, "strategy",         "") or getattr(decision, "timeframe", "")
    reasons  = (
        getattr(decision, "reasons",          None)
        or getattr(decision, "reasoning_points", None)
        or []
    )
    entry    = getattr(decision, "entry",       None) or getattr(decision, "entry_price",  0.0)
    stop     = getattr(decision, "stop",        None) or getattr(decision, "stop_loss",    0.0)
    target   = getattr(decision, "target",      None) or getattr(decision, "take_profit",  0.0)
    target2  = getattr(decision, "target_2",    0.0) or 0.0
    atr      = getattr(decision, "atr",         0.0) or 0.0
    qty      = qty if qty is not None else getattr(decision, "qty", 0)
    rr       = round(abs(target2 - entry) / abs(entry - stop), 1) if abs(entry - stop) > 0 else 0

    hub      = getattr(decision, "hub_subscores", {}) or {}
    reasoning_dict = hub.get("reasoning", {}) if isinstance(hub.get("reasoning"), dict) else {}

    side_emoji   = "🟢" if side == "BUY" else "🔴"
    regime_emoji = _REGIME_EMOJI.get(regime, "📈")
    score_str    = f"<b>{score:+.0f}</b>" if score is not None else "—"

    # ── 7-factor scores ──────────────────────────────────────────────────────
    tech  = float(hub.get("technical",   reasoning_dict.get("technical",   0)) or 0)
    news  = float(hub.get("news",        reasoning_dict.get("news",        0)) or 0)
    sect  = float(hub.get("sector",      reasoning_dict.get("sector",      0)) or 0)
    macro = float(hub.get("macro",       reasoning_dict.get("macro",       0)) or 0)
    earn  = float(hub.get("earnings",    reasoning_dict.get("earnings",    0)) or 0)
    fund  = float(hub.get("fundamental", reasoning_dict.get("fundamental", 0)) or 0)
    opts  = float(hub.get("options",     reasoning_dict.get("options",     0)) or 0)

    sector_name = reasoning_dict.get("sector_name") or hub.get("sector_name", "")
    fund_grade  = reasoning_dict.get("fund_grade")  or hub.get("fund_grade",  "")
    news_tone   = reasoning_dict.get("news_tone")   or ""
    headlines   = reasoning_dict.get("headlines",   []) or []

    # Technical proof
    tech_detail = reasoning_dict.get("tech_detail", {}) or {}
    tech_proof  = _fmt_tech_proof(tech_detail)

    # Macro proof
    macro_detail = reasoning_dict.get("macro_detail", {}) or {}
    macro_proof  = ""
    if macro_detail:
        vix   = macro_detail.get("india_vix", "")
        fii3d = macro_detail.get("fii_net_3d", 0)
        dii3d = macro_detail.get("dii_net_3d", 0)
        macro_proof = (
            f"  VIX {vix} ({macro_detail.get('vix_label','')})"
            f"  FII 3d ₹{fii3d:+.0f}Cr  DII 3d ₹{dii3d:+.0f}Cr"
        )

    # Sector proof
    sec_detail = reasoning_dict.get("sector_detail", {}) or {}
    sect_proof = ""
    if sec_detail:
        sect_proof = f"  {sec_detail.get('sector_name','')} · mood={sec_detail.get('sector_mood','')}"

    # Earnings proof
    earn_detail = reasoning_dict.get("earnings_detail", {}) or {}
    earn_proof  = f"  Tone: {earn_detail.get('tone','NEUTRAL')}  {'(data available)' if earn_detail.get('has_data') else '(no earnings data)'}"

    # Fundamental proof
    fund_detail = reasoning_dict.get("fundamental_detail", {}) or {}
    fund_proof  = f"  Score: {fund_detail.get('fund_score',50):.0f}/100  Grade: {fund_detail.get('fund_grade','?')}"

    # News headlines
    news_proof = ""
    if headlines:
        news_proof = "\n".join(f"  • {h[:90]}" for h in headlines[:3])
    elif news_tone:
        news_proof = f"  Tone: {news_tone}"

    # Web research note (appended by pre-trade gate)
    web_note = next((r[5:].strip() for r in reasons if r.startswith("[web]")), "")
    expert_note = next((r for r in reasons if not r.startswith("[web]") and len(r) > 60), "")

    lines = [
        f"🧪 <b>[PAPER TRADE] VIRTUAL EXECUTION</b> 🧪",
        f"{side_emoji} <b>TRADE EXECUTED — {side}</b> {side_emoji}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📌 <b>{sym}</b>  ·  Hub Score {score_str}  ·  <code>{regime}</code>",
        f"{regime_emoji} Confidence <b>{conf:.0f}%</b>  ·  Strategy: {strategy}",
        f"",
        f"<b>💰 Entry :</b>  ₹{entry:,.2f}",
        f"<b>🛑 SL    :</b>  ₹{stop:,.2f}" + (f"  (ATR ₹{atr:.2f})" if atr else ""),
        f"<b>🎯 T1    :</b>  ₹{target:,.2f}  (+{abs(target-entry)/entry*100:.1f}%)",
    ]
    if target2:
        lines.append(f"<b>🎯 T2    :</b>  ₹{target2:,.2f}  (+{abs(target2-entry)/entry*100:.1f}%)  R:R {rr}×")
    lines.append(f"<b>📦 Qty   :</b>  {qty} shares")

    # ── 7-factor breakdown ──────────────────────────────────────────────────
    lines += [
        f"",
        f"<b>📊 7-Factor Breakdown</b>  (total {score_str})",
        f"",
        f"1️⃣ <b>Technical</b>  {_score_bar(tech)}  <b>{tech:+.0f}</b>",
    ]
    if tech_proof:
        lines.append(tech_proof)

    lines += [
        f"",
        f"2️⃣ <b>News/Sentiment</b>  {_score_bar(news)}  <b>{news:+.0f}</b>",
    ]
    if news_proof:
        lines.append(news_proof)

    lines += [
        f"",
        f"3️⃣ <b>Sector</b>  {_score_bar(sect)}  <b>{sect:+.0f}</b>" + (f"  <i>{sector_name}</i>" if sector_name else ""),
    ]
    if sect_proof:
        lines.append(sect_proof)

    lines += [
        f"",
        f"4️⃣ <b>Macro/FII</b>  {_score_bar(macro)}  <b>{macro:+.0f}</b>",
    ]
    if macro_proof:
        lines.append(macro_proof)

    lines += [
        f"",
        f"5️⃣ <b>Earnings</b>  {_score_bar(earn)}  <b>{earn:+.0f}</b>",
        earn_proof,
        f"",
        f"6️⃣ <b>Fundamental</b>  {_score_bar(fund)}  <b>{fund:+.0f}</b>",
        fund_proof,
        f"",
        f"7️⃣ <b>Options/Flow</b>  {_score_bar(opts)}  <b>{opts:+.0f}</b>",
    ]
    opts_detail = reasoning_dict.get("options_detail", {}) or {}
    if opts_detail.get("nifty_bias") is not None:
        lines.append(f"  Nifty OI bias: {opts_detail['nifty_bias']:+d}")

    # Live web research (Tavily) done in the pre-trade gate — was previously
    # extracted but never shown. (Headlines already render under factor 2 above.)
    if web_note:
        lines += [f"", f"<b>🌐 Web Research (live):</b>", f"<i>{web_note[:400]}</i>"]

    if expert_note:
        lines += [f"", f"<b>🤖 AI Note:</b>", f"<i>{expert_note[:500]}</i>"]

    if web_note:
        lines += [f"", f"<b>🌐 Web Research:</b>", f"<i>{web_note[:400]}</i>"]

    lines += [f"", f"<i>⚠️ Paper mode — virtual money only</i>"]
    return "\n".join(lines)


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
        f"🧪 <b>[PAPER TRADE] VIRTUAL EXECUTION</b> 🧪\n"
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


def fmt_shortlist_alert(
    candidate,
    df=None,
    ai_note: str = "",
    executed: bool = False,
    crawl_data: dict | None = None,
) -> str:
    """Rich HTML alert for a shortlisted BUY/STRONG_BUY candidate.

    Includes complete 7-factor breakdown with indicator proof values,
    candle patterns, latest bar, web-crawl insights, and AI analysis.
    """
    sym     = candidate.symbol.replace(".NS", "")
    score   = getattr(candidate, "master_score", None) or getattr(candidate, "final_score", 0.0) or 0.0
    entry   = getattr(candidate, "entry", None) or getattr(candidate, "entry_price", 0.0) or 0.0
    stop    = getattr(candidate, "stop",  None) or getattr(candidate, "stop_loss",   0.0) or 0.0
    t1_val  = getattr(candidate, "take_profit", None) or getattr(candidate, "target", None) or 0.0
    t2_val  = getattr(candidate, "target_2", 0.0) or 0.0
    risk    = abs(entry - stop)
    t1      = t1_val if t1_val > 0 else round(entry + 1.0 * risk, 2)
    t2      = t2_val if t2_val > 0 else round(entry + 2.0 * risk, 2)
    t3      = round(entry + 3.0 * risk, 2)
    rr      = round(abs(t2 - entry) / risk, 1) if risk > 0 else 0

    hub     = getattr(candidate, "hub_subscores", {}) or {}
    rd      = hub.get("reasoning", {}) if isinstance(hub.get("reasoning"), dict) else {}
    # Prefer candidate.regime (set from DB row or features), then hub_subscores, then reasoning dict
    _cand_regime = getattr(candidate, "regime", "") or ""
    regime  = _cand_regime or hub.get("regime") or rd.get("regime") or ""
    signal  = hub.get("signal") or "BUY"
    regime_emoji = _REGIME_EMOJI.get(regime, "📈")

    # ── 7-factor scores ──────────────────────────────────────────────────────
    tech  = float(hub.get("technical",   rd.get("technical",   0)) or 0)
    news  = float(hub.get("news",        rd.get("news",        0)) or 0)
    sect  = float(hub.get("sector",      rd.get("sector",      0)) or 0)
    macro = float(hub.get("macro",       rd.get("macro",       0)) or 0)
    earn  = float(hub.get("earnings",    rd.get("earnings",    0)) or 0)
    fund  = float(hub.get("fundamental", rd.get("fundamental", 0)) or 0)
    opts  = float(hub.get("options",     rd.get("options",     0)) or 0)

    # Detail dicts
    tech_detail  = rd.get("tech_detail", {})  or {}
    macro_detail = rd.get("macro_detail", {}) or {}
    sec_detail   = rd.get("sector_detail", {}) or {}
    earn_detail  = rd.get("earnings_detail", {}) or {}
    fund_detail  = rd.get("fundamental_detail", {}) or {}
    opts_detail  = rd.get("options_detail", {}) or {}

    sector_name = sec_detail.get("sector_name") or rd.get("sector_name", "") or ""
    fund_grade  = fund_detail.get("fund_grade") or rd.get("fund_grade", "") or ""
    news_tone   = rd.get("news_tone", "") or ""
    headlines   = rd.get("headlines", []) or []

    # ── Header ──────────────────────────────────────────────────────────────
    lines = [
        f"🔥 <b>{'STRONG ' if signal == 'STRONG_BUY' else ''}BUY SIGNAL</b> 🔥",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📌 <b>{sym}</b>  ·  Score: <b>{score:+.1f}</b>  ·  Signal: <b>{signal}</b>",
        f"{regime_emoji} Regime: <b>{regime or 'N/A'}</b>",
        f"",
        f"<b>💰 Entry :</b>  ₹{entry:,.2f}",
        f"<b>🛑 Stop  :</b>  ₹{stop:,.2f}  (−₹{risk:.0f}  ·  {risk/entry*100:.1f}%)" if entry else f"<b>🛑 Stop  :</b>  ₹{stop:,.2f}",
        f"<b>🎯 T1    :</b>  ₹{t1:,.2f}  (+{abs(t1-entry)/entry*100:.1f}%)" if entry and t1 else "",
        f"<b>🎯 T2    :</b>  ₹{t2:,.2f}  (+{abs(t2-entry)/entry*100:.1f}%)  R:R {rr}×" if entry and t2 else "",
        f"<b>🎯 T3    :</b>  ₹{t3:,.2f}  (+{abs(t3-entry)/entry*100:.1f}%)" if entry and t3 else "",
        f"",
        f"<b>📊 7-Factor Breakdown</b>  (total: <b>{score:+.0f}</b>)",
    ]

    # ── Factor 1: Technical ──────────────────────────────────────────────────
    tech_proof = _fmt_tech_proof(tech_detail)
    lines += [
        f"",
        f"1️⃣ <b>Technical</b>  <code>{_score_bar(tech)}</code>  <b>{tech:+.0f}</b>  (wt {rd.get('active_weights',{}).get('technical',0)*100:.0f}%)",
    ]
    if tech_proof:
        lines.append(tech_proof)

    # ── Factor 2: News ───────────────────────────────────────────────────────
    lines += [
        f"",
        f"2️⃣ <b>News/Sentiment</b>  <code>{_score_bar(news)}</code>  <b>{news:+.0f}</b>  (tone: {news_tone or '—'})",
    ]
    if headlines:
        for h in headlines[:3]:
            lines.append(f"  • {h[:90]}")
    elif news_tone:
        lines.append(f"  Sentiment tone: {news_tone}")
    else:
        lines.append(f"  No tagged news (yfinance fallback if available)")

    # ── Factor 3: Sector ─────────────────────────────────────────────────────
    lines += [
        f"",
        f"3️⃣ <b>Sector</b>  <code>{_score_bar(sect)}</code>  <b>{sect:+.0f}</b>"
        + (f"  <i>{sector_name}</i>" if sector_name else ""),
    ]
    if sec_detail:
        lines.append(f"  Sector bias: {sec_detail.get('sector_bias',0):+.1f}  ·  Mood: {sec_detail.get('sector_mood','?')}")

    # ── Factor 4: Macro ──────────────────────────────────────────────────────
    lines += [
        f"",
        f"4️⃣ <b>Macro / FII-DII</b>  <code>{_score_bar(macro)}</code>  <b>{macro:+.0f}</b>",
    ]
    if macro_detail:
        fii3d = macro_detail.get("fii_net_3d", 0)
        dii3d = macro_detail.get("dii_net_3d", 0)
        vix   = macro_detail.get("india_vix", "")
        vix_l = macro_detail.get("vix_label", "")
        fii_b = macro_detail.get("fii_bias", 0)
        dii_b = macro_detail.get("dii_bias", 0)
        lines.append(f"  India VIX {vix} ({vix_l})")
        lines.append(f"  FII 3d: ₹{fii3d:+.0f}Cr (bias {fii_b:+d})  DII 3d: ₹{dii3d:+.0f}Cr (bias {dii_b:+d})")

    # ── Factor 5: Earnings ───────────────────────────────────────────────────
    lines += [
        f"",
        f"5️⃣ <b>Earnings</b>  <code>{_score_bar(earn)}</code>  <b>{earn:+.0f}</b>",
    ]
    earn_tone = earn_detail.get("tone", "NEUTRAL")
    earn_has  = earn_detail.get("has_data", False)
    lines.append(f"  Call tone: {earn_tone}  {'✅ data available' if earn_has else '⚠️ no earnings data yet'}")

    # ── Factor 6: Fundamental ────────────────────────────────────────────────
    lines += [
        f"",
        f"6️⃣ <b>Fundamental</b>  <code>{_score_bar(fund)}</code>  <b>{fund:+.0f}</b>",
    ]
    f_score = fund_detail.get("fund_score", 50)
    f_grade = fund_detail.get("fund_grade", fund_grade or "?")
    f_has   = fund_detail.get("has_data", False)
    lines.append(f"  Score: {f_score:.0f}/100  Grade: <b>{f_grade}</b>  {'✅' if f_has else '⚠️ no DB data'}")

    # ── Factor 7: Options ────────────────────────────────────────────────────
    lines += [
        f"",
        f"7️⃣ <b>Options / Nifty OI</b>  <code>{_score_bar(opts)}</code>  <b>{opts:+.0f}</b>",
    ]
    if opts_detail.get("nifty_bias") is not None:
        ob = opts_detail["nifty_bias"]
        ob_str = "Bullish OI skew" if ob > 0 else ("Bearish OI skew" if ob < 0 else "Neutral OI")
        lines.append(f"  Nifty OI bias: {ob:+d}  ({ob_str})")

    # ── Latest bar + candle patterns ─────────────────────────────────────────
    if df is not None and not df.empty:
        try:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else last
            chg  = round((float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100, 2)
            arrow = "▲" if chg >= 0 else "▼"
            lines += [
                f"",
                f"<b>📊 Latest Bar:</b>  O:{last['open']:.0f}  H:{last['high']:.0f}  "
                f"L:{last['low']:.0f}  C:<b>{last['close']:.0f}</b>  {arrow}{abs(chg)}%",
            ]
        except Exception:
            pass
        try:
            from engine.candlestick import detect_patterns
            patterns = detect_patterns(df)
            if patterns:
                lines.append("<b>🕯 Patterns:</b>  " + "  |  ".join(
                    f"{p.name} ({'Bullish' if p.score > 0 else 'Bearish'})"
                    for p in patterns[:3]
                ))
        except Exception:
            pass

    # ── Web research (crawl) ─────────────────────────────────────────────────
    if crawl_data:
        answer = crawl_data.get("search_answer", "")
        crawled = crawl_data.get("crawled", [])
        lines.append(f"")
        lines.append(f"<b>🌐 Web Research:</b>")
        if answer and len(answer) > 40:
            lines.append(f"<i>{answer[:400]}</i>")
        elif crawled:
            # Show first 250 chars of best crawled article
            lines.append(f"<i>{crawled[0]['content'][:300]}</i>")
            if len(crawled) > 1:
                lines.append(f"  <i>Source: {crawled[0].get('url','')[:70]}</i>")
        elif crawl_data.get("snippets"):
            lines.append(f"<i>{crawl_data['snippets'][0][:250]}</i>")

    # ── AI analysis ──────────────────────────────────────────────────────────
    if ai_note:
        lines += [f"", f"<b>🤖 AI Analysis:</b>", f"<i>{ai_note[:600]}</i>"]

    status = (
        "✅ <b>TRADE EXECUTED</b> — position opened" if executed
        else "📋 <b>WATCHLIST ALERT</b> — monitoring only"
    )
    lines += [f"", status, f"<i>⚠️ Paper mode — virtual money only</i>"]

    return "\n".join(l for l in lines if l is not None)


def fmt_cycle_summary(n_traded: int, n_scanned: int, equity: float, cash: float,
                      open_pos: int) -> str:
    """Short end-of-cycle summary (sent only when at least one trade executed)."""
    return (
        f"📈 <b>Cycle Summary</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Scanned: {n_scanned}  ·  Traded: <b>{n_traded}</b>  ·  Open: {open_pos}\n"
        f"Equity: ₹{equity:,.0f}  ·  Cash: ₹{cash:,.0f}\n"
    )
