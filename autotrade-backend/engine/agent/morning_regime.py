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


def _deterministic_regime(
    nifty_above_ema50: bool | None,
    nifty_5d_ret: float | None,
    vix_val: float | None,
) -> str:
    """Compute regime from technicals alone — no LLM, no network.

    Used as both the primary fail-safe and the LLM fallback.
    Rules mirror the LLM prompt exactly so behaviour is consistent:
      WAIT       : Nifty below EMA50  OR  5d return < -2%  OR  VIX > 25
      SELECTIVE  : VIX in 18-25 range OR Nifty near EMA50 (unknown position)
      AGGRESSIVE : Nifty above EMA50  AND  5d return > +0.5%  AND  VIX < 18
    """
    vix = vix_val or 15.0
    ret = nifty_5d_ret  # may be None if insufficient candle history

    # Hard WAIT conditions — any one is enough to block new entries.
    if nifty_above_ema50 is False:
        return "WAIT"
    if ret is not None and ret < -2.0:
        return "WAIT"
    if vix > 25.0:
        return "WAIT"

    # Full AGGRESSIVE: all three conditions green.
    if nifty_above_ema50 is True and (ret is None or ret > 0.5) and vix < 18.0:
        return "AGGRESSIVE"

    # Everything else is mixed signals.
    return "SELECTIVE"


async def get_morning_regime(session: AsyncSession) -> str:
    """Return today's regime: AGGRESSIVE | SELECTIVE | WAIT.

    Strategy: compute the deterministic regime from candle data first, then
    attempt the LLM call to allow it to override. If the LLM fails for any
    reason (network error, timeout, garbled output) the deterministic answer
    is used — never fail-open to AGGRESSIVE.
    """
    today = str(date.today())
    if today in _regime_cache:
        return _regime_cache[today]

    # Always compute the deterministic baseline first so we have a safe fallback.
    det_regime = "SELECTIVE"   # conservative default if DB is also unavailable
    nifty_above_ema50: bool | None = None
    nifty_5d_ret: float | None     = None
    vix_val: float | None          = None

    try:
        nifty_above_ema50, nifty_5d_ret, vix_val = await _fetch_regime_inputs(session)
        det_regime = _deterministic_regime(nifty_above_ema50, nifty_5d_ret, vix_val)
        logger.info(
            f"[morning_regime] deterministic → {det_regime} "
            f"(nifty_ema50={'above' if nifty_above_ema50 else 'below' if nifty_above_ema50 is False else 'unknown'} "
            f"5d={nifty_5d_ret}% vix={vix_val})"
        )
    except Exception as exc:
        logger.warning(f"[morning_regime] DB inputs failed — using SELECTIVE: {exc}")

    # Attempt LLM override. If it fails or returns garbage, keep the deterministic answer.
    try:
        llm_regime = await _classify_regime_llm(nifty_above_ema50, nifty_5d_ret, vix_val)
        if llm_regime != det_regime:
            logger.info(
                f"[morning_regime] LLM overrides deterministic: {det_regime} → {llm_regime}"
            )
        regime = llm_regime
    except Exception as exc:
        logger.warning(
            f"[morning_regime] LLM failed — keeping deterministic {det_regime}: {exc}"
        )
        regime = det_regime

    _regime_cache[today] = regime
    logger.info(f"[morning_regime] today={today} → {regime}")
    return regime


async def _fetch_regime_inputs(
    session: AsyncSession,
) -> tuple[bool | None, float | None, float | None]:
    """Fetch (nifty_above_ema50, nifty_5d_ret, vix_val) from the candles DB."""
    from sqlalchemy import text as _text

    # NIFTYBEES last 60 candles — enough for EMA50 + 5-day return.
    nifty_rows = (await session.execute(_text("""
        SELECT close FROM candles
        WHERE symbol = 'NIFTYBEES.NS' AND timeframe = '1d'
        ORDER BY timestamp DESC LIMIT 60
    """))).scalars().all()

    nifty_5d_ret: float | None     = None
    nifty_above_ema50: bool | None = None

    if nifty_rows and len(nifty_rows) >= 5:
        closes = list(reversed(nifty_rows))
        nifty_5d_ret = round((closes[-1] - closes[-5]) / closes[-5] * 100, 2)
    if nifty_rows and len(nifty_rows) >= 50:
        closes_s  = pd.Series(list(reversed(nifty_rows)), dtype=float)
        ema50     = closes_s.ewm(span=50, adjust=False).mean().iloc[-1]
        nifty_above_ema50 = bool(closes_s.iloc[-1] > ema50)

    # India VIX — try common symbol variants.
    vix_val: float | None = None
    for vix_sym in ("^INDIAVIX", "INDIAVIX.NS", "INDIA_VIX"):
        vix_rows = (await session.execute(_text(
            f"SELECT close FROM candles WHERE symbol = '{vix_sym}' "
            "AND timeframe = '1d' ORDER BY timestamp DESC LIMIT 1"
        ))).scalars().all()
        if vix_rows:
            vix_val = round(float(vix_rows[0]), 1)
            break

    return nifty_above_ema50, nifty_5d_ret, vix_val


async def _classify_regime_llm(
    nifty_above_ema50: bool | None,
    nifty_5d_ret: float | None,
    vix_val: float | None,
) -> str:
    """Ask the LLM to classify regime given pre-fetched inputs.

    Raises on any LLM/network error — caller handles fallback.
    """
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
        raise ValueError("LLM returned empty response")

    first_word = re.split(r"[\s\n]", response.strip())[0].upper()
    if first_word in ("AGGRESSIVE", "SELECTIVE", "WAIT"):
        logger.info(f"[morning_regime] LLM → '{response.strip()[:120]}'")
        return first_word

    for mode in ("AGGRESSIVE", "SELECTIVE", "WAIT"):
        if mode in response.upper():
            logger.info(f"[morning_regime] LLM fuzzy matched {mode} from: {response.strip()[:80]}")
            return mode

    raise ValueError(f"unrecognised LLM response: '{response[:80]}'")
