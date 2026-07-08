"""Shared high-impact / market-shock news classifier.

Single source of truth for "is this headline a market-moving event?" — used by
the fast shock guard (position de-risking), the high-impact news alert task, and
the /news API surfacing. Keeps the keyword list and threshold in one place so
all three agree on what counts as a shock.
"""
from __future__ import annotations

# Negative headlines matching these stems mark a market-wide shock (geopolitics
# or panic selling) rather than routine single-stock news.
SHOCK_KEYWORDS: tuple[str, ...] = (
    # geopolitical catalysts
    "war", "strike", "missile", "attack", "invasion", "ceasefire", "nuclear",
    "emergency", "sanction", "terror", "bomb", "escalat", "conflict",
    "airstrike", "retaliat", "hostilit",
    # market-panic language
    "crash", "plunge", "collapse", "tumble", "slump", "selloff", "sell-off",
    "rout", "bloodbath", "tank", "sink", "nosediv", "freefall",
)

# Balanced default: a headline must carry at least this |sentiment| to alert.
HIGH_IMPACT_MIN_ABS_SCORE = 0.6


def matches_shock_keyword(headline: str | None) -> bool:
    low = (headline or "").lower()
    return any(kw in low for kw in SHOCK_KEYWORDS)


def is_high_impact_news(
    headline: str | None,
    sentiment: str | None,
    score: float | None,
    min_abs_score: float = HIGH_IMPACT_MIN_ABS_SCORE,
) -> bool:
    """True when a headline names a market-shock catalyst AND carries strong
    negative sentiment (balanced: negative and |score| ≥ 0.6).

    Deliberately conservative — this drives push alerts, so routine single-stock
    or weakly-negative news must not trip it.
    """
    if not matches_shock_keyword(headline):
        return False
    if sentiment != "negative":
        return False
    return score is not None and score <= -abs(min_abs_score)
