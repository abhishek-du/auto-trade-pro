"""Deterministic price/volume confirmation gate -- a hard backstop for news-
driven entries.

Added 2026-07-28 after live data showed every one of that week's stopped-out
trades (7/7, both Direct News and the News-strategy's LLM debate) shared the
same signature: near-zero MFE -- price never moved favorably, even briefly,
before reversing against the position. The LLM debate already has a "verify
price/volume before entry" lesson injected into its own prompt every single
time (engine.agent.reflection.get_relevant_lessons(), wired into
_candidate_context() well before this module existed) -- confirmed live that
this soft, text-context reminder was not enough to change the verdict.
Direct News has no LLM step at all to reason over such a reminder in the
first place.

This module is the hard version of that same check: has the market ALREADY
reacted to the news with real, directionally-consistent movement, or is this
still just an unconfirmed headline? Reuses whatever MarketSnapshot the caller
already fetched for entry pricing -- no extra API calls. Fail-closed: a
signal that's genuinely unavailable is treated as "not confirmed," never as
a free pass -- consistent with every other central gate in this codebase
(MIN_STOP_DISTANCE_PCT, the market-hours gate, etc.).
"""

from __future__ import annotations

# Minimum same-day move required in the signaled direction. Below this, the
# stock is considered "flat on the news" -- exactly the failure mode observed
# live (e.g. MOLDTKPAC.NS stopped out 4 minutes after entry; ASIIL.BO's MFE
# was the news move itself, not genuine follow-through).
MIN_DAY_CHANGE_PCT = 1.5

# Order-book skew tolerance: the side opposing the trade direction is allowed
# to be somewhat larger (real books are noisy) but not dominant.
MAX_OPPOSING_DEPTH_RATIO = 1.0 / 0.6  # opposing side may be up to ~1.67x, no more


def check_price_volume_confirmation(snap, side: str) -> tuple[bool, str]:
    """Given a MarketSnapshot (crawler.market_snapshot) and the intended BUY/
    SELL side, decide whether the market has genuinely confirmed the move.

    Two independent checks, both must pass when their data is available:
      1. Same-day price change is already in the signaled direction and past
         MIN_DAY_CHANGE_PCT (not flat).
      2. Order-book depth (when present) isn't dominated by the opposing side.

    Returns (confirmed, reason) -- reason is always populated, for logging /
    skip-reason surfacing either way.
    """
    if snap is None:
        return False, "no market snapshot available to confirm price reaction"

    change = getattr(snap, "change_pct", None)
    if change is None:
        return False, "no day-change data available to confirm price reaction"

    side = (side or "").upper()
    if side == "BUY" and change < MIN_DAY_CHANGE_PCT:
        return False, (
            f"price only {change:+.2f}% on the day — not enough follow-through "
            "to confirm the bullish signal"
        )
    if side == "SELL" and change > -MIN_DAY_CHANGE_PCT:
        return False, (
            f"price only {change:+.2f}% on the day — not enough follow-through "
            "to confirm the bearish signal"
        )

    buy_depth = getattr(snap, "buy_depth", None) or []
    sell_depth = getattr(snap, "sell_depth", None) or []
    try:
        buy_q = sum(float(d.get("quantity") or 0) for d in buy_depth)
        sell_q = sum(float(d.get("quantity") or 0) for d in sell_depth)
    except Exception:
        buy_q = sell_q = 0.0

    if buy_q > 0 or sell_q > 0:
        if side == "BUY" and buy_q > 0 and sell_q > buy_q * MAX_OPPOSING_DEPTH_RATIO:
            return False, (
                f"order book skewed toward sellers (buy={buy_q:.0f} vs "
                f"sell={sell_q:.0f}) despite bullish news"
            )
        if side == "SELL" and sell_q > 0 and buy_q > sell_q * MAX_OPPOSING_DEPTH_RATIO:
            return False, (
                f"order book skewed toward buyers (sell={sell_q:.0f} vs "
                f"buy={buy_q:.0f}) despite bearish news"
            )

    return True, f"confirmed: {change:+.2f}% day move, order book supports {side}"


# Day-range instability threshold, as a fraction of the day's low --
# (high - low) / low. v0.1 baseline, tunable (same status as the scoring
# weights in engine/pre_event_expectation_gap/scoring.py). AASTHA.NS's
# entry-day range was ~23% (high 105.79, low 81.9) on a stock that had been
# trading a flat ~102-104 range the prior sessions -- entered near the day's
# low mid-collapse, then gapped through its stop overnight. Normal small/
# mid-cap ranges, even on active-news days, are typically 1-8%; 12% gives
# comfortable room above that while still catching AASTHA-shaped whipsaws.
MAX_DAY_RANGE_PCT = 0.12


def check_day_range_stability(snap) -> tuple[bool, str]:
    """Supplementary filter (2026-07-29): reject entries into a stock whose
    own session has already been unusually wide/unstable, independent of
    which direction it's currently pointing.

    Added after AASTHA.NS (-11.27%, DIRECT_NEWS's worst trade): its entry
    passed check_price_volume_confirmation() (a real bullish move existed at
    entry) but the day's price action was itself a ~23% whipsaw -- the kind
    of session that's statistically more likely to keep gapping around than
    settle, which is exactly what happened overnight. This is a
    supplementary check, not a replacement for check_price_volume_confirmation:
    it fails OPEN (returns confirmed=True) when OHLC data isn't available,
    since the primary fail-closed gate is already covered by that other
    check -- this one only adds an extra reason to say no, never the only
    reason to say yes.
    """
    ohlc = getattr(snap, "ohlc", None) or {}
    high = ohlc.get("high")
    low = ohlc.get("low")
    if not high or not low or low <= 0:
        return True, "no day-range data available — skipping stability check"

    day_range_pct = (high - low) / low
    if day_range_pct > MAX_DAY_RANGE_PCT:
        return False, (
            f"day range too wide ({day_range_pct * 100:.1f}%, high={high:.2f} "
            f"low={low:.2f}) — elevated whipsaw/gap risk"
        )
    return True, f"day range stable ({day_range_pct * 100:.1f}%)"
