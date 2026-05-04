"""Candlestick pattern detection for AutoTrade Pro.

Uses TA-Lib as the sole detection engine.  Every pattern is characterised
by reliability tier and a signed score so downstream confluence logic can
aggregate multiple concurrent signals into one numeric verdict.

Scoring system
--------------
  HIGH   reliability → ±3
  MEDIUM reliability → ±2
  LOW    reliability → ±1
  NEUTRAL pattern    →  0  (no directional bias)

  Positive score = bullish signal.
  Negative score = bearish signal.

TA-Lib convention (checked on the LAST bar only)
-------------------------------------------------
  +100 → bullish instance detected
  -100 → bearish instance detected
     0 → no pattern on this bar
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import pandas as pd
import talib

from utils.logger import logger


# ── Result type returned to callers ──────────────────────────────────────────

@dataclass
class PatternResult:
    """A single candlestick pattern detected on the most recent bar.

    Attributes
    ----------
    name        : Human-readable pattern name, e.g. 'Bullish Engulfing'.
    direction   : 'BULLISH', 'BEARISH', or 'NEUTRAL'.
    reliability : 'HIGH', 'MEDIUM', or 'LOW' — based on academic back-test consensus.
    score       : Signed numeric weight.  +3/-3 for HIGH, +2/-2 for MEDIUM, +1/-1 for LOW.
                  NEUTRAL patterns always score 0.
    description : Plain-English explanation of what the pattern means and when it is most valid.
    """
    name:        str
    direction:   str
    reliability: str
    score:       float
    description: str

    def __str__(self) -> str:
        sign = "+" if self.score >= 0 else ""
        return (
            f"{self.name} [{self.direction}/{self.reliability}] "
            f"score={sign}{self.score:.0f}"
        )


# ── Internal pattern specification ───────────────────────────────────────────

class _Spec(NamedTuple):
    fn:           str         # talib function name, e.g. 'CDLENGULFING'
    reliability:  str         # 'HIGH' | 'MEDIUM' | 'LOW'
    abs_score:    float       # magnitude; sign is determined by talib output
    bull_name:    str | None  # name when talib returns +100 and not neutral
    bull_desc:    str         # plain-English bullish description
    bear_name:    str | None  # name when talib returns -100
    bear_desc:    str         # plain-English bearish description
    neutral_name: str | None  # name when pattern is always directionally neutral
    neutral_desc: str         # plain-English neutral description


# ── Pattern registry ──────────────────────────────────────────────────────────

_REGISTRY: list[_Spec] = [

    # ── HIGH reliability (±3) ─────────────────────────────────────────────────

    _Spec(
        fn="CDLENGULFING", reliability="HIGH", abs_score=3.0,
        bull_name="Bullish Engulfing",
        bull_desc=(
            "A large bullish candle completely engulfs the previous bearish candle's body. "
            "Signals a decisive shift to buyer control after a downtrend. "
            "Most reliable at a key support level or a historical swing low."
        ),
        bear_name="Bearish Engulfing",
        bear_desc=(
            "A large bearish candle completely engulfs the previous bullish candle's body. "
            "Signals a decisive shift to seller control after an uptrend. "
            "Most reliable at a key resistance level or a historical swing high."
        ),
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLMORNINGSTAR", reliability="HIGH", abs_score=3.0,
        bull_name="Morning Star",
        bull_desc=(
            "Three-candle bullish reversal: (1) a large bearish candle, (2) a small-bodied "
            "star candle gapping lower showing indecision, (3) a large bullish candle closing "
            "at least halfway into the first body. "
            "Represents buyers reclaiming control after seller exhaustion."
        ),
        bear_name=None, bear_desc="",
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLEVENINGSTAR", reliability="HIGH", abs_score=3.0,
        bull_name=None, bull_desc="",
        bear_name="Evening Star",
        bear_desc=(
            "Three-candle bearish reversal: (1) a large bullish candle, (2) a small-bodied "
            "star candle gapping higher showing buyer exhaustion, (3) a large bearish candle "
            "closing at least halfway into the first body. "
            "Signals seller takeover after a protracted rally."
        ),
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDL3WHITESOLDIERS", reliability="HIGH", abs_score=3.0,
        bull_name="Three White Soldiers",
        bull_desc=(
            "Three consecutive long bullish candles, each opening within the prior body and "
            "closing progressively higher with minimal upper wicks. "
            "Demonstrates sustained, orderly buying — strong reversal or continuation confirmation."
        ),
        bear_name=None, bear_desc="",
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDL3BLACKCROWS", reliability="HIGH", abs_score=3.0,
        bull_name=None, bull_desc="",
        bear_name="Three Black Crows",
        bear_desc=(
            "Three consecutive long bearish candles, each opening within the prior body and "
            "closing progressively lower with minimal lower wicks. "
            "Demonstrates sustained, orderly selling — strong reversal or continuation confirmation."
        ),
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLHAMMER", reliability="HIGH", abs_score=3.0,
        bull_name="Hammer",
        bull_desc=(
            "Small real body near the candle top with a lower shadow ≥ 2× the body, "
            "appearing after a downtrend. "
            "Buyers aggressively rejected lower prices; the close near the high signals "
            "seller exhaustion. Strongest with a gap-up or bullish next candle confirmation."
        ),
        bear_name=None, bear_desc="",
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLSHOOTINGSTAR", reliability="HIGH", abs_score=3.0,
        bull_name=None, bull_desc="",
        bear_name="Shooting Star",
        bear_desc=(
            "Small real body near the candle bottom with an upper shadow ≥ 2× the body, "
            "appearing after an uptrend. "
            "Sellers aggressively rejected higher prices; the close near the low signals "
            "buyer exhaustion. Strongest with a gap-down or bearish next candle confirmation."
        ),
        neutral_name=None, neutral_desc="",
    ),

    # ── MEDIUM reliability (±2) ───────────────────────────────────────────────

    _Spec(
        fn="CDLDOJI", reliability="MEDIUM", abs_score=2.0,
        bull_name=None, bull_desc="",
        bear_name=None, bear_desc="",
        neutral_name="Doji",
        neutral_desc=(
            "Open and close are virtually equal, forming a cross or plus shape. "
            "A perfect standoff between buyers and sellers. "
            "After a directional move it warns of potential reversal; "
            "requires confirmation from the following candle."
        ),
    ),

    _Spec(
        fn="CDLLONGLEGGEDDOJI", reliability="MEDIUM", abs_score=2.0,
        bull_name=None, bull_desc="",
        bear_name=None, bear_desc="",
        neutral_name="Long-Legged Doji",
        neutral_desc=(
            "A Doji with unusually long upper and lower shadows — extreme intra-bar volatility "
            "that resolved to no net change. "
            "Signals intense indecision; often precedes a significant directional breakout. "
            "The next candle's direction is the entry trigger."
        ),
    ),

    _Spec(
        fn="CDLGRAVESTONEDOJI", reliability="MEDIUM", abs_score=2.0,
        bull_name=None, bull_desc="",
        bear_name="Gravestone Doji",
        bear_desc=(
            "Open, close, and low are all near the same level with a long upper shadow. "
            "Buyers rallied prices during the session but gave back every gain by the close — "
            "a sign of buyer failure. "
            "Bearish reversal signal, most potent at resistance or the top of an uptrend."
        ),
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLDRAGONFLYDOJI", reliability="MEDIUM", abs_score=2.0,
        bull_name="Dragonfly Doji",
        bull_desc=(
            "Open, close, and high are all near the same level with a long lower shadow. "
            "Sellers drove prices down but buyers recovered every loss by the close — "
            "a sign of seller failure. "
            "Bullish reversal signal, most potent at support or the bottom of a downtrend."
        ),
        bear_name=None, bear_desc="",
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLDARKCLOUDCOVER", reliability="MEDIUM", abs_score=2.0,
        bull_name=None, bull_desc="",
        bear_name="Dark Cloud Cover",
        bear_desc=(
            "Two-candle bearish reversal: a bullish candle followed by a bearish candle that "
            "opens above the prior high but closes more than halfway into the prior bullish body. "
            "Sellers overwhelm the gap-up open; deeper penetration = stronger signal."
        ),
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLPIERCING", reliability="MEDIUM", abs_score=2.0,
        bull_name="Piercing Line",
        bull_desc=(
            "Two-candle bullish reversal: a bearish candle followed by a bullish candle that "
            "opens below the prior low but closes more than halfway into the prior bearish body. "
            "Buyers absorb all selling and push back strongly; deeper penetration = stronger signal."
        ),
        bear_name=None, bear_desc="",
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLHANGINGMAN", reliability="MEDIUM", abs_score=2.0,
        bull_name=None, bull_desc="",
        bear_name="Hanging Man",
        bear_desc=(
            "Visually identical to the Hammer but appears after an uptrend. "
            "A small body near the top with a long lower shadow shows that sellers tested "
            "lower prices intra-bar — bears are gaining confidence. "
            "Requires bearish confirmation on the next candle before acting."
        ),
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLINVERTEDHAMMER", reliability="MEDIUM", abs_score=2.0,
        bull_name="Inverted Hammer",
        bull_desc=(
            "Small body near the bottom with a long upper shadow, appearing after a downtrend. "
            "Buyers attempted a significant rally intra-bar; sellers pushed back but could not "
            "drive a new low — a sign of exhaustion. "
            "Gap-up or bullish next candle confirms the potential reversal."
        ),
        bear_name=None, bear_desc="",
        neutral_name=None, neutral_desc="",
    ),

    # ── LOW reliability (±1) ──────────────────────────────────────────────────

    _Spec(
        fn="CDLSPINNINGTOP", reliability="LOW", abs_score=1.0,
        bull_name=None, bull_desc="",
        bear_name=None, bear_desc="",
        neutral_name="Spinning Top",
        neutral_desc=(
            "Small real body roughly centred between upper and lower shadows of similar length. "
            "Neither bulls nor bears controlled the session. "
            "Combined with a prior strong trend it hints at momentum loss; "
            "the next candle's direction is the follow-through clue."
        ),
    ),

    _Spec(
        fn="CDLMARUBOZU", reliability="LOW", abs_score=1.0,
        bull_name="Bullish Marubozu",
        bull_desc=(
            "Full-bodied bullish candle with no (or minimal) shadows — open at session low, "
            "close at session high. Pure, uninterrupted buying pressure throughout the period. "
            "Typically a continuation signal in an established uptrend."
        ),
        bear_name="Bearish Marubozu",
        bear_desc=(
            "Full-bodied bearish candle with no (or minimal) shadows — open at session high, "
            "close at session low. Pure, uninterrupted selling pressure throughout the period. "
            "Typically a continuation signal in an established downtrend."
        ),
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLHARAMI", reliability="LOW", abs_score=1.0,
        bull_name="Bullish Harami",
        bull_desc=(
            "A small bullish candle whose body is entirely contained within the prior large "
            "bearish candle ('harami' = pregnant in Japanese). "
            "Signals a pause in bearish momentum — an early warning of possible reversal. "
            "Best used as an alert; wait for confirmation before entering."
        ),
        bear_name="Bearish Harami",
        bear_desc=(
            "A small bearish candle whose body is entirely contained within the prior large "
            "bullish candle. Signals a pause in bullish momentum. "
            "More significant at overbought readings or key resistance zones."
        ),
        neutral_name=None, neutral_desc="",
    ),

    _Spec(
        fn="CDLHARAMICROSS", reliability="LOW", abs_score=1.0,
        bull_name="Bullish Harami Cross",
        bull_desc=(
            "Like the Bullish Harami but the second candle is a Doji (open ≈ close) "
            "contained entirely within the prior large bearish candle. "
            "The Doji amplifies indecision at the bottom — marginally stronger than a "
            "plain Harami as a reversal alert."
        ),
        bear_name="Bearish Harami Cross",
        bear_desc=(
            "Like the Bearish Harami but the second candle is a Doji contained entirely "
            "within the prior large bullish candle. "
            "Maximum indecision following a strong up-candle warns that buyers are "
            "losing momentum. Confirm with a bearish close on the next bar."
        ),
        neutral_name=None, neutral_desc="",
    ),
]


# ── Core detection ────────────────────────────────────────────────────────────

def detect_patterns(df: pd.DataFrame) -> list[PatternResult]:
    """Detect all registered candlestick patterns on the most recent bar.

    Passes the full OHLCV DataFrame to TA-Lib and checks only the **last
    element** of each function's output array, per TA-Lib convention.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns ``open``, ``high``, ``low``, ``close``
        (case-insensitive).  At least 10 rows recommended; returns ``[]``
        for fewer than 5 rows.

    Returns
    -------
    list[PatternResult]
        Every pattern detected on the final bar, sorted by absolute score
        descending (strongest first).  Returns ``[]`` when no patterns fire
        or when the DataFrame has insufficient data.
    """
    if df is None or df.empty or len(df) < 5:
        logger.warning(
            f"detect_patterns: need ≥ 5 bars, got "
            f"{len(df) if df is not None else 0}"
        )
        return []

    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        logger.error(f"detect_patterns: missing required columns {missing}")
        return []

    o: np.ndarray = df["open"].to_numpy(dtype=np.float64)
    h: np.ndarray = df["high"].to_numpy(dtype=np.float64)
    l: np.ndarray = df["low"].to_numpy(dtype=np.float64)
    c: np.ndarray = df["close"].to_numpy(dtype=np.float64)

    found: list[PatternResult] = []

    logger.debug(
        f"detect_patterns: scanning {len(_REGISTRY)} patterns "
        f"over {len(df)} bars"
    )

    for spec in _REGISTRY:
        fn = getattr(talib, spec.fn, None)
        if fn is None:
            logger.warning(f"talib.{spec.fn} not available — skipping")
            continue

        try:
            result: np.ndarray = fn(o, h, l, c)
        except Exception as exc:
            logger.error(f"talib.{spec.fn} raised: {exc}")
            continue

        if len(result) == 0:
            continue

        last_val = int(result[-1])
        if last_val == 0:
            continue

        # ── Neutral ──────────────────────────────────────────────────────────
        if spec.neutral_name is not None:
            pr = PatternResult(
                name        = spec.neutral_name,
                direction   = "NEUTRAL",
                reliability = spec.reliability,
                score       = 0.0,
                description = spec.neutral_desc,
            )

        # ── Bullish ───────────────────────────────────────────────────────────
        elif last_val > 0:
            if spec.bull_name is None:
                logger.debug(
                    f"talib.{spec.fn} returned +{last_val} "
                    f"but no bullish name defined — skipped"
                )
                continue
            pr = PatternResult(
                name        = spec.bull_name,
                direction   = "BULLISH",
                reliability = spec.reliability,
                score       = +spec.abs_score,
                description = spec.bull_desc,
            )

        # ── Bearish ───────────────────────────────────────────────────────────
        else:
            if spec.bear_name is None:
                logger.debug(
                    f"talib.{spec.fn} returned {last_val} "
                    f"but no bearish name defined — skipped"
                )
                continue
            pr = PatternResult(
                name        = spec.bear_name,
                direction   = "BEARISH",
                reliability = spec.reliability,
                score       = -spec.abs_score,
                description = spec.bear_desc,
            )

        found.append(pr)
        sign = "+" if pr.score >= 0 else ""
        logger.info(
            f"Pattern  ▶  {pr.name:<32}  "
            f"{pr.direction:<8}  {pr.reliability:<6}  "
            f"score={sign}{pr.score:.0f}"
        )

    found.sort(key=lambda p: abs(p.score), reverse=True)

    if found:
        logger.info(
            f"detect_patterns: {len(found)} pattern(s) — "
            + ", ".join(p.name for p in found)
        )
    else:
        logger.debug("detect_patterns: no patterns on the last bar")

    return found


# ── Summary aggregation ───────────────────────────────────────────────────────

def get_pattern_summary(patterns: list[PatternResult]) -> dict:
    """Aggregate a list of PatternResults into a single-bar verdict.

    Parameters
    ----------
    patterns : list[PatternResult]
        Output of ``detect_patterns()``.

    Returns
    -------
    dict
        ``total_score``       — signed sum of all pattern scores.
        ``direction``         — ``'BULLISH'``, ``'BEARISH'``, or ``'NEUTRAL'``.
        ``strongest_pattern`` — name of the highest-|score| pattern, or ``None``.
        ``count``             — total number of patterns detected.

    Notes
    -----
    Neutral patterns (score = 0) count toward ``count`` but do not shift
    ``total_score``.  A tied total (== 0) resolves to ``'NEUTRAL'``.
    """
    if not patterns:
        return {
            "total_score":       0.0,
            "direction":         "NEUTRAL",
            "strongest_pattern": None,
            "count":             0,
        }

    total_score = sum(p.score for p in patterns)
    strongest   = max(patterns, key=lambda p: abs(p.score))

    if total_score > 0:
        direction = "BULLISH"
    elif total_score < 0:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    logger.info(
        f"Pattern summary  ▶  {len(patterns)} pattern(s)  "
        f"total_score={total_score:+.1f}  "
        f"direction={direction}  "
        f"strongest='{strongest.name}'"
    )

    return {
        "total_score":       round(total_score, 2),
        "direction":         direction,
        "strongest_pattern": strongest.name,
        "count":             len(patterns),
    }
