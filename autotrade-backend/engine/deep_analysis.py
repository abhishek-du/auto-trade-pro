"""Deep technical analysis + trade setup + news + AI commentary for NSE stocks.

Called by GET /api/v1/zerodha/deep-analysis/{symbol}.

Returns:
  technicals   — per-indicator human-readable reasoning (bullish/bearish/neutral)
  trade_setup  — entry zone, stop-loss, T1, T2, R:R, when to buy/sell/hold
  news         — up to 5 recent Finnhub headlines for the stock
  ai_summary   — Groq LLM commentary (empty string when GROQ_API_KEY absent)
"""

from __future__ import annotations

import datetime
import math

import httpx

from engine.indicators import IndicatorSignals
from utils.config import settings
from utils.logger import logger

_FH_BASE = "https://finnhub.io/api/v1"


# ── Reasoning generator ───────────────────────────────────────────────────────

def generate_reasoning(sig: IndicatorSignals, ltp: float) -> dict:
    """Produce human-readable bullet points from indicator values."""
    bull:    list[str] = []
    bear:    list[str] = []
    neutral: list[str] = []

    def _nan(v: float) -> bool:
        return math.isnan(v)

    # RSI
    if not _nan(sig.rsi):
        r = sig.rsi
        if r <= 25:
            bull.append(f"RSI deeply oversold at {r:.1f} — historically a high-probability reversal zone")
        elif r <= 35:
            bull.append(f"RSI at {r:.1f} in oversold territory — buyers tend to step in below 35")
        elif r >= 75:
            bear.append(f"RSI at {r:.1f} is overbought — elevated risk of profit-booking pullback")
        elif r >= 65:
            bear.append(f"RSI at {r:.1f} approaching overbought — consider tightening stops or booking partial profits")
        else:
            neutral.append(f"RSI at {r:.1f} in healthy neutral range — no extreme reading")

    # MACD
    if sig.macd_cross == "BULLISH_CROSS":
        bull.append("MACD just crossed above signal line — fresh bullish momentum confirmation (high-reliability entry trigger)")
    elif sig.macd_cross == "BEARISH_CROSS":
        bear.append("MACD just crossed below signal line — bearish momentum shift confirmed")
    elif not _nan(sig.macd_histogram):
        h = sig.macd_histogram
        if h > 0:
            bull.append(f"MACD histogram positive ({h:.3f}) — buying pressure exceeds selling; trend intact")
        else:
            bear.append(f"MACD histogram negative ({h:.3f}) — selling pressure dominant")

    # EMA trend
    def _p(v): return f"₹{v:.2f}" if not _nan(v) else "N/A"
    if sig.ema_trend == "STRONG_BULL":
        bull.append(
            f"Price (₹{ltp:.2f}) above EMA20 ({_p(sig.ema_20)}), EMA50 ({_p(sig.ema_50)}), EMA200 ({_p(sig.ema_200)}) "
            "— textbook strong uptrend across all timeframes"
        )
    elif sig.ema_trend == "BULL":
        bull.append(
            f"Price above EMA20 ({_p(sig.ema_20)}) and EMA50 ({_p(sig.ema_50)}) "
            "— intermediate uptrend intact; pullbacks are buying opportunities"
        )
    elif sig.ema_trend == "STRONG_BEAR":
        bear.append(
            f"Price below EMA20, EMA50, and EMA200 ({_p(sig.ema_200)}) "
            "— strong multi-timeframe downtrend; avoid longs"
        )
    elif sig.ema_trend == "BEAR":
        bear.append(
            f"Price below EMA20 ({_p(sig.ema_20)}) and EMA50 ({_p(sig.ema_50)}) "
            "— bearish trend structure; resistance overhead"
        )
    else:
        neutral.append("EMAs are mixed/flat — no clear directional bias; ranging market")

    # Ichimoku
    if sig.ichimoku_signal == "STRONG_BUY":
        bull.append(
            f"Ichimoku strong buy: price above cloud, Tenkan ({_p(sig.ichimoku_tenkan)}) > Kijun ({_p(sig.ichimoku_kijun)}), "
            "future cloud is positive — all five components aligned bullishly"
        )
    elif sig.ichimoku_signal == "BUY":
        bull.append(
            f"Ichimoku buy: price above cloud (Tenkan {_p(sig.ichimoku_tenkan)}, Kijun {_p(sig.ichimoku_kijun)}) "
            "— cloud provides strong dynamic support"
        )
    elif sig.ichimoku_signal == "STRONG_SELL":
        bear.append(
            "Ichimoku strong sell: price below cloud, Tenkan < Kijun, negative future cloud "
            "— all components bearish"
        )
    elif sig.ichimoku_signal == "SELL":
        bear.append(f"Ichimoku sell: price below Kumo cloud — trend is bearish")
    else:
        neutral.append(
            "Ichimoku neutral: price is inside or near the cloud edge — consolidation phase, "
            "wait for a clean break"
        )

    # Supertrend
    if not _nan(sig.supertrend):
        if sig.supertrend_direction == "BULLISH":
            bull.append(
                f"Supertrend bullish at ₹{sig.supertrend:.2f} — acts as a trailing dynamic support floor; "
                "as long as price stays above this level the uptrend is intact"
            )
        else:
            bear.append(
                f"Supertrend bearish at ₹{sig.supertrend:.2f} — acts as overhead resistance; "
                "price needs to close above this level to flip trend"
            )

    # ADX
    if not _nan(sig.adx):
        a = sig.adx
        d = sig.adx_direction
        if a >= 30:
            if d == "BULLISH":
                bull.append(
                    f"ADX at {a:.1f} (strong) with +DI > -DI — powerful directional bullish trend; "
                    "pullbacks likely shallow and short-lived"
                )
            else:
                bear.append(
                    f"ADX at {a:.1f} (strong) with -DI > +DI — powerful directional bearish trend"
                )
        elif a >= 20:
            neutral.append(f"ADX at {a:.1f} — moderate trend strength ({d.lower()}); trend is real but not explosive")
        else:
            neutral.append(
                f"ADX at {a:.1f} — weak trend; market is likely ranging or consolidating. "
                "Breakout strategies work better than trend-following here"
            )

    # Bollinger Bands
    if not _nan(sig.bb_lower) and not _nan(sig.bb_upper):
        if sig.bb_position == "BELOW_LOWER":
            bull.append(
                f"Price is below lower Bollinger Band (₹{sig.bb_lower:.2f}) — statistically extreme oversold; "
                "mean reversion bounce is highly probable in next 1–3 sessions"
            )
        elif sig.bb_position == "NEAR_LOWER":
            bull.append(
                f"Price near lower BB (₹{sig.bb_lower:.2f}) — approaching the oversold boundary; "
                "risk/reward favours longs near this zone"
            )
        elif sig.bb_position == "ABOVE_UPPER":
            bear.append(
                f"Price above upper BB (₹{sig.bb_upper:.2f}) — statistically overbought; "
                "short-term traders should consider booking profits or tightening stops"
            )
        elif sig.bb_position == "NEAR_UPPER":
            neutral.append(
                f"Price near BB upper band (₹{sig.bb_upper:.2f}) — strong momentum but approaching resistance; "
                "watch for a close above to confirm breakout or look for reversal candle"
            )

    # VWAP
    if not _nan(sig.vwap) and sig.vwap > 0:
        gap = (ltp - sig.vwap) / sig.vwap * 100
        if gap > 1:
            bull.append(
                f"Price (₹{ltp:.2f}) is {abs(gap):.1f}% above VWAP (₹{sig.vwap:.2f}) "
                "— institutional participants are net buyers; intraday momentum is positive"
            )
        elif gap < -1:
            bear.append(
                f"Price (₹{ltp:.2f}) is {abs(gap):.1f}% below VWAP (₹{sig.vwap:.2f}) "
                "— institutional participants are net sellers; avoid intraday longs"
            )

    return {"bullish": bull, "bearish": bear, "neutral": neutral}


# ── Trade setup generator ─────────────────────────────────────────────────────

def build_trade_setup(sig: IndicatorSignals, ltp: float, signal: str) -> dict:
    """Compute entry zone, stop-loss, targets, R:R, and textual guidance."""
    nan = math.isnan

    # Support = BB lower unless NaN
    support    = sig.bb_lower    if not nan(sig.bb_lower)    else ltp * 0.95
    resistance = sig.bb_upper    if not nan(sig.bb_upper)    else ltp * 1.05

    # Stop-loss: just below supertrend when bullish (closest dynamic level),
    # otherwise 2% below support
    if sig.supertrend_direction == "BULLISH" and not nan(sig.supertrend):
        sl = max(sig.supertrend * 0.985, support * 0.985)
    else:
        sl = support * 0.985

    sl_pct  = (sl - ltp) / ltp * 100
    t1      = resistance
    t1_pct  = (t1 - ltp) / ltp * 100
    t2      = ltp + abs(ltp - sl) * 3        # 3x R:R from SL for T2
    t2_pct  = (t2 - ltp) / ltp * 100
    rr      = abs(t1 - ltp) / abs(ltp - sl) if abs(ltp - sl) > 0.01 else 0.0

    if signal in ("STRONG_BUY", "BUY"):
        entry_low  = max(sl * 1.01, ltp * 0.97)
        entry_high = ltp * 1.005
        when_buy = (
            f"**Ideal entry:** ₹{entry_low:.2f}–₹{entry_high:.2f} zone.\n"
            f"Strategy A — Dip buy: wait for price to pull back into the ₹{entry_low:.2f}–₹{entry_low*1.01:.2f} zone and enter when a reversal candle forms (hammer, bullish engulfing).\n"
            f"Strategy B — Breakout: enter if price closes above ₹{ltp*1.01:.2f} with above-average volume, confirming continuation."
        )
        when_sell = (
            f"**Take-profit plan:**\n"
            f"• Book 40–50% at Target 1: ₹{t1:.2f} (+{t1_pct:.1f}%)\n"
            f"• Trail remaining with Supertrend or SL at ₹{sl:.2f}\n"
            f"• Exit remaining at Target 2: ₹{t2:.2f} (+{t2_pct:.1f}%)\n"
            f"**Stop-loss:** Hard stop at ₹{sl:.2f} ({sl_pct:.1f}%) — exit immediately on daily close below this level."
        )
        hold = (
            f"Hold for 5–15 trading sessions. Re-evaluate if RSI exceeds 75 (overbought) "
            f"or if price closes below ₹{sl:.2f} on the daily chart."
        )
    elif signal == "NEUTRAL":
        entry_low  = support
        entry_high = support * 1.02
        when_buy = (
            f"**Wait for one of:**\n"
            f"• A) Dip to support zone ₹{entry_low:.2f}–₹{entry_high:.2f} with a bullish reversal candle\n"
            f"• B) Bullish breakout above resistance ₹{resistance:.2f} with 50%+ higher volume than average\n"
            f"• C) MACD bullish cross confirming direction\n"
            f"Do NOT chase at current price — neutral setup needs a trigger."
        )
        when_sell = (
            f"If already holding, trail stop at Supertrend level or ₹{sl:.2f}.\n"
            f"Take partial profits near ₹{resistance:.2f} resistance.\n"
            f"Do NOT initiate fresh shorts unless ₹{support:.2f} support breaks on volume."
        )
        hold = (
            "Neutral setup — give it 1–3 sessions to show direction. "
            "Watch for MACD cross or volume-based breakout as entry trigger."
        )
    else:  # SELL / STRONG_SELL
        entry_low  = resistance * 0.97
        entry_high = resistance
        when_buy = (
            f"**Avoid new longs now.**\n"
            f"Re-entry only if price recovers convincingly above ₹{resistance:.2f} "
            f"AND RSI turns back above 40 AND MACD shows bullish cross.\n"
            f"Possible accumulation zone for patient investors: ₹{support * 0.95:.2f}–₹{support:.2f} (deep value zone)."
        )
        when_sell = (
            f"**If holding:**\n"
            f"• Consider exiting at any bounce to ₹{ltp * 1.02:.2f}–₹{ltp * 1.04:.2f}\n"
            f"• Hard stop at ₹{sl:.2f} — do not hold below this\n"
            f"• Do not average down in a downtrend"
        )
        hold = (
            "Bearish/Sell signal — reduce or exit longs. "
            "Capital preservation takes priority. "
            "Do not average down."
        )

    return {
        "signal":       signal,
        "support":      round(support, 2),
        "resistance":   round(resistance, 2),
        "entry_low":    round(entry_low, 2),
        "entry_high":   round(entry_high, 2),
        "stop_loss":    round(sl, 2),
        "stop_loss_pct":round(sl_pct, 1),
        "target_1":     round(t1, 2),
        "target_1_pct": round(t1_pct, 1),
        "target_2":     round(t2, 2),
        "target_2_pct": round(t2_pct, 1),
        "risk_reward":  round(rr, 1),
        "when_to_buy":  when_buy,
        "when_to_sell": when_sell,
        "hold_strategy":hold,
    }


# ── News fetcher ──────────────────────────────────────────────────────────────

async def fetch_stock_news(symbol: str) -> list[dict]:
    """Fetch up to 5 recent news items for the NSE symbol.

    Primary: yfinance (works for all NSE stocks, no key needed).
    Secondary: Finnhub company-news (only works for US stocks on free plan).
    """
    # yfinance in a thread (it's sync)
    try:
        import asyncio, functools
        import yfinance as yf

        def _yf_news():
            ticker = yf.Ticker(f"{symbol}.NS")
            return ticker.news or []

        loop    = asyncio.get_event_loop()
        raw     = await loop.run_in_executor(None, _yf_news)
        results = []
        for item in raw[:8]:
            c = item.get("content") or item
            title   = c.get("title", "")
            if not title:
                continue
            url     = (c.get("canonicalUrl") or {}).get("url") or c.get("url", "")
            source  = (c.get("provider") or {}).get("displayName") or "Yahoo Finance"
            pub     = c.get("pubDate") or c.get("displayTime") or ""
            summary = (c.get("summary") or "")[:300]
            results.append({
                "headline":     title.strip(),
                "source":       source,
                "url":          url,
                "published_at": pub,
                "summary":      summary,
            })
        if results:
            return results[:5]
    except Exception as exc:
        logger.warning(f"[deep_analysis] yfinance news failed for {symbol}: {exc}")

    # Finnhub fallback (only useful for US-listed stocks)
    if not settings.finnhub_available:
        return []
    from_dt = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    to_dt   = datetime.date.today().strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{_FH_BASE}/company-news",
                params={"symbol": f"NSE:{symbol}", "from": from_dt, "to": to_dt, "token": settings.FINNHUB_KEY},
            )
            if r.status_code != 200:
                return []
            items = r.json()[:5]
            return [
                {
                    "headline":     it.get("headline", "").strip(),
                    "source":       it.get("source", "Finnhub"),
                    "url":          it.get("url", ""),
                    "published_at": (
                        datetime.datetime.utcfromtimestamp(it["datetime"]).isoformat()
                        if it.get("datetime") else None
                    ),
                    "summary": (it.get("summary") or "")[:300],
                }
                for it in items if it.get("headline")
            ]
    except Exception as exc:
        logger.warning(f"[deep_analysis] Finnhub news failed for {symbol}: {exc}")
    return []


# ── Groq AI commentary ────────────────────────────────────────────────────────

async def groq_commentary(
    symbol: str,
    ltp: float,
    change_pct: float,
    sig: IndicatorSignals,
    reasoning: dict,
    setup: dict,
    news: list | None = None,
) -> str:
    """Generate expert-level equity research commentary using Groq. Returns '' on failure."""
    if not settings.groq_available:
        return ""

    bull_pts  = "\n".join(f"• {r}" for r in reasoning["bullish"])
    bear_pts  = "\n".join(f"• {r}" for r in reasoning["bearish"])
    neut_pts  = "\n".join(f"• {r}" for r in reasoning.get("neutral", []))

    bb_pos = getattr(sig, "bb_position", "")
    ichi   = getattr(sig, "ichimoku_signal", "")
    adx_d  = getattr(sig, "adx_direction", "")
    st_dir = getattr(sig, "supertrend_direction", "")
    st_val = getattr(sig, "supertrend", None)

    news_section = ""
    if news:
        news_lines = []
        for item in news[:5]:
            hl  = item.get("headline", "")
            src = item.get("source", "")
            dt  = (item.get("published_at") or "")[:10]
            sm  = (item.get("summary") or "")[:200]
            news_lines.append(f"• [{dt}] {hl} ({src})\n  Summary: {sm}")
        news_section = "RECENT NEWS & EVENTS:\n" + "\n".join(news_lines)

    prompt = (
        f"STOCK: NSE:{symbol}\n"
        f"LTP: ₹{ltp:.2f}  ({change_pct:+.2f}% today)\n"
        f"SIGNAL: {setup['signal']}  |  Technical Score: {sig.composite_score:.1f}/100\n\n"
        f"TECHNICAL INDICATORS:\n"
        f"• RSI {sig.rsi:.1f} ({sig.rsi_signal}) | MACD histogram {sig.macd_histogram:.2f} ({sig.macd_cross})\n"
        f"• EMA Trend: {sig.ema_trend} | BB Position: {bb_pos}\n"
        f"• Ichimoku: {ichi} | Supertrend: {st_dir}"
        + (f" at ₹{st_val:.2f}" if st_val else "") +
        f" | ADX {sig.adx:.1f} ({sig.adx_trend_strength}, {adx_d})\n"
        f"• Stochastic: K={sig.stoch_k:.1f}, D={sig.stoch_d:.1f} ({sig.stoch_signal})\n\n"
        f"BULLISH SIGNALS:\n{bull_pts or 'None'}\n\n"
        f"BEARISH SIGNALS:\n{bear_pts or 'None'}\n\n"
        + (f"NEUTRAL:\n{neut_pts}\n\n" if neut_pts else "")
        + f"TRADE SETUP:\n"
        f"• Entry zone: ₹{setup['entry_low']}–₹{setup['entry_high']}\n"
        f"• Stop loss: ₹{setup['stop_loss']} ({setup['stop_loss_pct']:.1f}%)\n"
        f"• Target 1: ₹{setup['target_1']} (+{setup['target_1_pct']:.1f}%) | "
        f"Target 2: ₹{setup['target_2']} (+{setup['target_2_pct']:.1f}%)\n"
        f"• Risk-reward: {setup['risk_reward']:.1f}x\n\n"
        + (news_section + "\n\n" if news_section else "")
        + "Write a comprehensive 4-5 paragraph expert equity research note:\n\n"
        "PARAGRAPH 1 — MARKET STRUCTURE: Describe exactly what the chart is showing. "
        "What trend/pattern is forming? Where is the stock in relation to its key moving averages, "
        "Bollinger Bands, and Supertrend? Is this a breakout, breakdown, mean-reversion, or consolidation setup?\n\n"
        "PARAGRAPH 2 — TECHNICAL LEVELS & TRIGGERS: Specify exact rupee levels — "
        "support, resistance, entry zone, stop-loss, and targets. "
        "What specific price action would CONFIRM the trade (e.g., close above ₹X with volume)? "
        "What would INVALIDATE the setup?\n\n"
        "PARAGRAPH 3 — NEWS & BUSINESS CONTEXT: Interpret what the recent news means for the company. "
        "How does each news item affect the company's revenue, margins, order book, or competitive position? "
        "What is the market pricing in or ignoring?\n\n"
        "PARAGRAPH 4 — RISK FACTORS: 3-4 specific risks to this trade. "
        "Include both technical risk (SL level) and fundamental risk (business/macro).\n\n"
        "PARAGRAPH 5 — TRADE MANAGEMENT: Specific entry advice (limit order vs breakout buy), "
        "position sizing guidance (% of portfolio for this risk level), "
        "and when to review/exit the trade.\n\n"
        "Use precise rupee levels throughout. Write like a SEBI-registered research analyst — "
        "professional, specific, data-driven. This is for informational purposes only."
    )

    from utils.llm import call_groq_chat
    reply = await call_groq_chat(
        [
            {
                "role": "system",
                "content": (
                    "You are a senior equity research analyst at a top Indian brokerage, "
                    "covering NSE-listed stocks. You write detailed, specific, expert-level "
                    "research notes that combine technical analysis with business context and news interpretation. "
                    "Always use exact rupee price levels. Never use vague terms like 'good support' "
                    "without specifying the exact level. Write in the style of a Goldman Sachs or "
                    "Kotak Securities research note. End every analysis with: "
                    "'This report is for informational purposes only and does not constitute financial advice.'"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=700, temperature=0.25, timeout=35.0,
    )
    return reply or ""
