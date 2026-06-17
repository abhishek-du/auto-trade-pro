"""Morning regime classification — one LLM call per trading day.

Classifies the day's macro environment into three modes:
  AGGRESSIVE — trending market, deploy all strategies
  SELECTIVE  — mixed signals, TREND_BREAKOUT only (highest win-rate)
  WAIT       — downtrend / high fear, no new entries

Cached by calendar date so the LLM is called only once per day regardless
of how many agent cycles run (every 15 min).
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from utils.logger import logger

# Date → regime string cache (reset on process restart, fine — one call per day)
_regime_cache: dict[str, str] = {}


async def get_morning_regime(session: AsyncSession) -> str:
    """Return today's regime: AGGRESSIVE | SELECTIVE | WAIT.

    Fails open (AGGRESSIVE) on any error so a LLM outage never blocks trading.
    """
    today = str(date.today())
    if today in _regime_cache:
        return _regime_cache[today]

    try:
        regime = await _classify_regime(session)
    except Exception as exc:
        logger.warning(f"[morning_regime] classification failed — defaulting AGGRESSIVE: {exc}")
        regime = "AGGRESSIVE"

    _regime_cache[today] = regime
    logger.info(f"[morning_regime] today={today} → {regime}")
    return regime


async def _classify_regime(session: AsyncSession) -> str:
    from sqlalchemy import text as _text

    # ── 1. NIFTYBEES 5-day return ────────────────────────────────────────────
    nifty_rows = (await session.execute(_text("""
        SELECT close FROM candles
        WHERE symbol = 'NIFTYBEES.NS' AND timeframe = '1d'
        ORDER BY timestamp DESC LIMIT 10
    """))).scalars().all()

    nifty_5d_ret = None
    if nifty_rows and len(nifty_rows) >= 5:
        closes = list(reversed(nifty_rows))
        nifty_5d_ret = round((closes[-1] - closes[-5]) / closes[-5] * 100, 2)

    # ── 2. India VIX (symbol = 'INDIAVIX.NS' or '^INDIAVIX') ────────────────
    vix_val = None
    for vix_sym in ("^INDIAVIX", "INDIAVIX.NS", "INDIA_VIX"):
        vix_rows = (await session.execute(_text(f"""
            SELECT close FROM candles
            WHERE symbol = '{vix_sym}' AND timeframe = '1d'
            ORDER BY timestamp DESC LIMIT 1
        """))).scalars().all()
        if vix_rows:
            vix_val = round(float(vix_rows[0]), 1)
            break

    # ── 3. NIFTYBEES above / below EMA50 ─────────────────────────────────────
    ema_rows = (await session.execute(_text("""
        SELECT close FROM candles
        WHERE symbol = 'NIFTYBEES.NS' AND timeframe = '1d'
        ORDER BY timestamp DESC LIMIT 60
    """))).scalars().all()

    nifty_above_ema50 = None
    if ema_rows and len(ema_rows) >= 50:
        closes_s = pd.Series(list(reversed(ema_rows)), dtype=float)
        ema50 = closes_s.ewm(span=50, adjust=False).mean().iloc[-1]
        nifty_above_ema50 = bool(closes_s.iloc[-1] > ema50)

    # ── 4. Build LLM prompt ───────────────────────────────────────────────────
    ctx_lines = [f"Date: {date.today().isoformat()}"]
    if nifty_5d_ret is not None:
        ctx_lines.append(f"NIFTYBEES 5-day return: {nifty_5d_ret:+.2f}%")
    if nifty_above_ema50 is not None:
        ctx_lines.append(f"NIFTYBEES vs EMA50: {'ABOVE' if nifty_above_ema50 else 'BELOW'}")
    if vix_val is not None:
        ctx_lines.append(f"India VIX: {vix_val}")
    context = "\n".join(ctx_lines)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a market regime classifier for an Indian equity trading system. "
                "Given macro data, output EXACTLY one word on the first line — "
                "AGGRESSIVE, SELECTIVE, or WAIT — then one short sentence of reasoning.\n\n"
                "Rules:\n"
                "AGGRESSIVE: NIFTYBEES above EMA50, 5d return > +0.5%, VIX < 18\n"
                "SELECTIVE:  mixed signals (NIFTYBEES near EMA50, or VIX 18-25)\n"
                "WAIT:       NIFTYBEES below EMA50, OR 5d return < -2%, OR VIX > 25"
            ),
        },
        {
            "role": "user",
            "content": f"Market data:\n{context}\n\nClassify today's regime:",
        },
    ]

    from utils.llm import call_llm_chat
    response = await call_llm_chat(messages, max_tokens=80, temperature=0.1, groq_fallback=True)

    if not response:
        logger.warning("[morning_regime] LLM returned empty response — defaulting AGGRESSIVE")
        return "AGGRESSIVE"

    # Extract first token from response (AGGRESSIVE / SELECTIVE / WAIT)
    first_word = re.split(r"[\s\n]", response.strip())[0].upper()
    if first_word in ("AGGRESSIVE", "SELECTIVE", "WAIT"):
        logger.info(f"[morning_regime] LLM → '{response.strip()[:120]}'")
        return first_word

    # Fuzzy match in case LLM added punctuation or wrapped it
    for mode in ("AGGRESSIVE", "SELECTIVE", "WAIT"):
        if mode in response.upper():
            logger.info(f"[morning_regime] LLM fuzzy matched {mode} from: {response.strip()[:80]}")
            return mode

    logger.warning(f"[morning_regime] unrecognised response '{response[:80]}' — defaulting AGGRESSIVE")
    return "AGGRESSIVE"
