"""Market Regime Engine — 5-state adaptive macro gate.

Replaces the single EMA50 Nifty check with a composite regime classifier
that combines five independent signals to detect the true market state.

States (with position-size multiplier):
  STRONG_BULL  → 1.25×  — all systems go, size up
  MODERATE_BULL → 1.0×  — normal entry, standard size
  SIDEWAYS      → 0.5×  — cautious; only high-confidence setups
  WEAK_BEAR     → 0.0   — block all new BUY entries (corrections caught here)
  STRONG_BEAR   → 0.0   — block all new BUY entries (deep bear)

Signal stack:
  1. Nifty EMA stack   (EMA20 > EMA50 > EMA100 > EMA200) → trend quality
  2. EMA50 slope       → trend accelerating or decelerating?
  3. 20-day ROC        → active correction detector (faster than EMA cross)
  4. Breadth momentum  → market participation expanding or narrowing?
  5. VIX level         → fear premium

Why this beats a single EMA50 gate:
  In Jan-Feb 2025, Nifty started correcting but stayed ABOVE EMA50 for ~40 days
  while falling 10%. A single EMA50 gate allowed all those losing trades.
  The ROC gate + slope gate catches the correction in 3-5 days instead of 40.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from utils.logger import logger


# ── State definitions ──────────────────────────────────────────────────────────

STRONG_BULL   = "STRONG_BULL"
MODERATE_BULL = "MODERATE_BULL"
SIDEWAYS      = "SIDEWAYS"
WEAK_BEAR     = "WEAK_BEAR"
STRONG_BEAR   = "STRONG_BEAR"

# Minimum confidence threshold for each regime (overrides AGENT_CONFIDENCE_THRESHOLD)
REGIME_MIN_CONF: dict[str, int] = {
    STRONG_BULL:   72,   # slightly looser — market is with us
    MODERATE_BULL: 74,   # standard
    SIDEWAYS:      78,   # high-conviction only in chop (was 82 — too high, blocked everything)
    WEAK_BEAR:     999,  # effectively blocked
    STRONG_BEAR:   999,  # blocked
}

# Position size multiplier for each regime
REGIME_SIZE_MULT: dict[str, float] = {
    STRONG_BULL:   1.25,
    MODERATE_BULL: 1.00,
    SIDEWAYS:      0.50,
    WEAK_BEAR:     0.00,
    STRONG_BEAR:   0.00,
}


@dataclass
class RegimeResult:
    state:          str
    size_mult:      float
    min_conf:       int
    can_buy:        bool
    score:          float   # composite score -100..+100
    signals:        dict    # individual signal breakdown for logging


def classify_regime(
    closes:     pd.Series,
    breadth_pct: Optional[float] = None,   # % hub stocks above 50d proxy (0-100)
    vix:        Optional[float] = None,    # India VIX current level
) -> RegimeResult:
    """Classify the current market regime from Nifty close prices + breadth + VIX.

    Args:
        closes: Nifty/NIFTYBEES daily close prices, oldest → newest, length >= 210.
        breadth_pct: today's market breadth (% of hub stocks above 50d proxy).
        vix: current India VIX level.

    Returns RegimeResult with state, size multiplier, and signal breakdown.
    """
    n = len(closes)
    if n < 60:
        return RegimeResult(MODERATE_BULL, 1.0, 76, True, 0.0,
                            {"note": "insufficient_data_fail_open"})

    price = float(closes.iloc[-1])

    # ── Signal 1: EMA Stack Quality ───────────────────────────────────────────
    ema20  = float(closes.ewm(span=20,  adjust=False).mean().iloc[-1])
    ema50  = float(closes.ewm(span=50,  adjust=False).mean().iloc[-1])
    ema100 = float(closes.ewm(span=100, adjust=False).mean().iloc[-1]) if n >= 110 else ema50
    ema200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1]) if n >= 210 else ema50

    # Count how many EMA levels price is above (0-4)
    ema_levels = sum([
        price > ema20,
        price > ema50,
        price > ema100,
        price > ema200,
    ])
    # Normalized to -100..+100
    ema_score = (ema_levels / 4.0) * 200 - 100   # 0→-100, 4→+100

    # ── Signal 2: EMA50 Slope (5-day rate of change of EMA50) ─────────────────
    ema50_series = closes.ewm(span=50, adjust=False).mean()
    ema50_now    = float(ema50_series.iloc[-1])
    ema50_5d_ago = float(ema50_series.iloc[-6]) if n >= 6 else ema50_now
    # Percentage change of EMA50 over 5 days
    ema50_slope_pct = (ema50_now - ema50_5d_ago) / max(ema50_5d_ago, 1e-9) * 100
    # Map slope: -0.5% or worse → -100, +0.5% or better → +100
    slope_score = max(-100.0, min(100.0, ema50_slope_pct * 200))

    # ── Signal 3: 20-Day Rate of Change (active correction detector) ──────────
    price_20d_ago = float(closes.iloc[-21]) if n >= 21 else float(closes.iloc[0])
    roc_20d = (price - price_20d_ago) / max(price_20d_ago, 1e-9) * 100
    # Map ROC: -5% or worse → -100, +5% or better → +100
    roc_score = max(-100.0, min(100.0, roc_20d * 20))

    # ── Signal 4: Breadth Momentum ────────────────────────────────────────────
    if breadth_pct is not None:
        # Map breadth: 30% → -100, 70% → +100  (50% = 0)
        breadth_score = max(-100.0, min(100.0, (breadth_pct - 50.0) * 5.0))
    else:
        breadth_score = 0.0   # neutral when unavailable

    # ── Signal 5: VIX Fear Level ──────────────────────────────────────────────
    if vix is not None:
        # Map VIX: >= 30 → -100, <= 12 → +50  (normal VIX ~15 → 0)
        vix_score = max(-100.0, min(50.0, -(vix - 15.0) * 10.0))
    else:
        vix_score = 0.0   # neutral when unavailable

    # ── Composite Score (weighted) ─────────────────────────────────────────────
    # EMA stack carries most weight (structural trend).
    # ROC is the fast-reacting correction detector (second highest weight).
    composite = (
        0.30 * ema_score      +   # structural trend quality
        0.25 * roc_score      +   # active correction / momentum
        0.20 * slope_score    +   # EMA deceleration early warning
        0.15 * breadth_score  +   # market participation
        0.10 * vix_score          # fear premium
    )

    # ── State classification ───────────────────────────────────────────────────
    if composite >= 40:
        state = STRONG_BULL
    elif composite >= 10:
        state = MODERATE_BULL
    elif composite >= -15:
        state = SIDEWAYS
    elif composite >= -50:
        state = WEAK_BEAR
    else:
        state = STRONG_BEAR

    # ── EMA50 Hard Gate (belt & suspenders) ──────────────────────────────────
    # The composite score can still rate SIDEWAYS when Nifty is in a brief
    # bounce within a larger downtrend (ROC / VIX signals are neutral on bounce
    # days, but the structural trend is broken).
    # Rule: price BELOW EMA50 = at most SIDEWAYS (not BULL); and if the composite
    # is already bearish (< -5), force WEAK_BEAR. This prevents buying pullbacks
    # against the 50-day trend — the core discipline of the PULLBACK_LONG strategy.
    if price < ema50:
        if state in (STRONG_BULL, MODERATE_BULL):
            state = SIDEWAYS    # downgrade; composite still open but careful
        if composite < -5:
            state = WEAK_BEAR   # composite says bad AND price below EMA50 → block

    # ── EMA200 Absolute Gate ──────────────────────────────────────────────────
    # If Nifty is clearly below its 200-day EMA, the long-term structural trend
    # is broken — "pullbacks" are dead-cat bounces, not re-entries.
    #
    # Tolerance band: only hard-block when price is >1.5% BELOW EMA200.
    # A gap of 0–1.5% (market hugging EMA200) is treated as SIDEWAYS ceiling,
    # not a full bear block — it's within daily noise and doesn't deserve the
    # same blanket veto as a -5% or -10% correction.
    EMA200_BLOCK_MARGIN = 0.985   # block only when price < EMA200 × 0.985
    if n >= 210:
        if price < ema200 * EMA200_BLOCK_MARGIN:
            # Clearly below EMA200 (>1.5% gap) — true structural downtrend, block all entries.
            state = WEAK_BEAR
        elif price < ema200:
            # Within 1.5% band: market is at/near EMA200. Cap at SIDEWAYS — don't allow
            # bullish calls, but don't completely block high-conviction setups.
            if state in (STRONG_BULL, MODERATE_BULL):
                state = SIDEWAYS

    signals = {
        "composite":     round(composite, 1),
        "ema_levels":    f"{ema_levels}/4",
        "ema_score":     round(ema_score, 1),
        "ema50_slope_%": round(ema50_slope_pct, 3),
        "slope_score":   round(slope_score, 1),
        "roc_20d_%":     round(roc_20d, 2),
        "roc_score":     round(roc_score, 1),
        "breadth":       round(breadth_pct, 1) if breadth_pct is not None else None,
        "breadth_score": round(breadth_score, 1),
        "vix":           vix,
        "vix_score":     round(vix_score, 1),
    }

    return RegimeResult(
        state     = state,
        size_mult = REGIME_SIZE_MULT[state],
        min_conf  = REGIME_MIN_CONF[state],
        can_buy   = REGIME_SIZE_MULT[state] > 0,
        score     = round(composite, 1),
        signals   = signals,
    )


async def get_market_regime(
    session,
    breadth_pct: Optional[float] = None,
) -> RegimeResult:
    """Async wrapper — fetches NIFTYBEES candles + live VIX, returns RegimeResult.

    Called once per agent cycle (after the EMA50 gate is replaced by this).
    Fail-open: any DB/network error returns MODERATE_BULL so trading continues.
    """
    try:
        from sqlalchemy import text as _text
        from crawler.live_prices import PRICE_CACHE

        rows = (await session.execute(_text("""
            SELECT close FROM candles
            WHERE symbol = 'NIFTYBEES.NS' AND timeframe = '1d'
            ORDER BY timestamp DESC LIMIT 220
        """))).scalars().all()

        if not rows or len(rows) < 60:
            logger.warning("[regime] Insufficient NIFTYBEES history — fail-open MODERATE_BULL")
            return RegimeResult(MODERATE_BULL, 1.0, 76, True, 0.0,
                                {"note": "insufficient_nifty_history"})

        closes = pd.Series(list(reversed(rows)), dtype=float)   # oldest → newest

        # Live VIX from WebSocket cache
        vix: Optional[float] = None
        try:
            vix_tick = PRICE_CACHE.get("^INDIAVIX", {})
            if vix_tick:
                vix = float(vix_tick.get("price", 0) or 0) or None
        except Exception:
            pass

        # Live breadth from market breadth cache (advances / total as %)
        if breadth_pct is None:
            try:
                from crawler.market_breadth import get_breadth_cache
                nse = (get_breadth_cache() or {}).get("nse", {})
                adv = nse.get("advances") or 0
                dec = nse.get("declines") or 0
                if adv + dec > 0:
                    breadth_pct = adv / (adv + dec) * 100.0
            except Exception:
                pass

        result = classify_regime(closes, breadth_pct=breadth_pct, vix=vix)
        logger.info(
            f"[regime] {result.state} | score={result.score} | "
            f"size_mult={result.size_mult} | "
            f"ema_levels={result.signals.get('ema_levels')} | "
            f"roc_20d={result.signals.get('roc_20d_%')}% | "
            f"slope={result.signals.get('ema50_slope_%')}% | "
            f"vix={vix}"
        )
        return result

    except Exception as exc:
        logger.warning(f"[regime] Engine failed — fail-open MODERATE_BULL: {exc}")
        return RegimeResult(MODERATE_BULL, 1.0, 76, True, 0.0,
                            {"note": f"error:{exc}"})


def build_regime_map_from_df(
    nifty_df: pd.DataFrame,
    breadth_map: dict[str, float] | None = None,
    vix_df: pd.DataFrame | None = None,
) -> dict[str, RegimeResult]:
    """Build a {date_str: RegimeResult} map for backtesting.

    Uses a sliding window so each date gets the regime computed from the
    candles available up to that date (avoids look-ahead).
    """
    closes    = nifty_df["close"].copy()
    results:  dict[str, RegimeResult] = {}
    min_bars  = 60   # minimum to classify

    for i, ts in enumerate(nifty_df.index):
        if i < min_bars:
            results[str(ts)[:10]] = RegimeResult(
                MODERATE_BULL, 1.0, 76, True, 0.0, {"note": "warmup"}
            )
            continue

        window = closes.iloc[:i + 1]   # up to and including today
        ts_str = str(ts)[:10]

        breadth = breadth_map.get(ts_str) if breadth_map else None

        vix_val: Optional[float] = None
        if vix_df is not None and not vix_df.empty and ts in vix_df.index:
            try:
                vix_val = float(vix_df.loc[ts, "close"])
            except Exception:
                pass

        results[ts_str] = classify_regime(window, breadth_pct=breadth, vix=vix_val)

    return results
