"""Confluence signal generator for AutoTrade Pro.

Combines candlestick pattern analysis, technical indicators, and FinBERT
news sentiment into a single weighted BUY / SELL / HOLD decision with
entry price, stop-loss, take-profit, confidence score, and human-readable
reasoning points.

Scoring weights:
    Pattern score (candlestick)  : 35 %
    Indicator score (technical)  : 45 %
    Sentiment score (news)       : 20 %
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from crawler.news_crawler import get_market_sentiment
from db.models import Signal, SignalType
from engine.candlestick import detect_patterns, get_pattern_summary
from engine.indicators import (
    IndicatorSignals,
    compute_indicators,
    suggest_stop_loss,
    suggest_take_profit,
)
from utils.config import settings
from utils.logger import logger

# ── Constants ─────────────────────────────────────────────────────────────────

# Practical maximum raw pattern score (3 HIGH-reliability patterns all agreeing).
# Used for normalising total_score → -100..+100.
_MAX_PATTERN_SCORE: float = 9.0

# final_score thresholds for BUY / SELL decisions
_BUY_THRESHOLD:  float = 30.0
_SELL_THRESHOLD: float = -30.0


# ── TradingSignal dataclass ───────────────────────────────────────────────────

@dataclass
class TradingSignal:
    """Complete trading decision with all supporting evidence.

    All score fields are on a -100 … +100 scale.
    ``confidence`` is in 0 … 100 (percentage).
    """
    symbol:             str
    timeframe:          str
    action:             str              # 'BUY' | 'SELL' | 'HOLD'
    confidence:         float            # 0–100 %
    entry_price:        float
    stop_loss:          float
    take_profit:        float            # Target 1 — first checkpoint / trailing trigger
    pattern_score:      float            # normalised candlestick contribution
    indicator_score:    float            # from indicators.composite_score
    sentiment_score:    float            # news_score * 100  →  -100..+100
    final_score:        float            # weighted combination
    patterns_detected:  list[str]        = field(default_factory=list)
    reasoning_points:   list[str]        = field(default_factory=list)
    timestamp:          datetime         = field(default_factory=datetime.utcnow)
    # Dynamic trade-management fields (ATR/technical-derived). Default 0/""/None
    # so legacy callers that don't set them still construct cleanly.
    target_2:           float            = 0.0   # final target — position rides here
    atr:                float            = 0.0   # ATR at entry → 1× trailing stop distance
    risk_reward_ratio:  float            = 0.0
    regime:             str              = ""
    hub_subscores:      dict             = field(default_factory=dict)  # 7-factor breakdown + indicator detail


# ── Internal helpers ──────────────────────────────────────────────────────────

def _normalize_pattern_score(raw: float) -> float:
    """Map raw pattern total_score → -100..+100.

    Uses _MAX_PATTERN_SCORE (=9, three bullish HIGH patterns) as the practical
    ceiling; anything beyond that is clamped to ±100.
    """
    if _MAX_PATTERN_SCORE == 0:
        return 0.0
    return max(-100.0, min(100.0, raw / _MAX_PATTERN_SCORE * 100.0))


def _build_reasoning(
    symbol:          str,
    indicators:      IndicatorSignals,
    pattern_summary: dict,
    patterns:        list,
    sentiment_score: float,             # already on -100..+100 scale
    final_score:     float,
    action:          str,
) -> list[str]:
    """Build a list of plain-English bullet points explaining the signal."""
    points: list[str] = []
    nan = math.isnan

    # ── RSI ───────────────────────────────────────────────────────────────────
    if not nan(indicators.rsi):
        if indicators.rsi_signal == "OVERSOLD":
            points.append(
                f"RSI at {indicators.rsi:.1f} indicates oversold conditions — reversal likely"
            )
        elif indicators.rsi_signal == "OVERBOUGHT":
            points.append(
                f"RSI at {indicators.rsi:.1f} indicates overbought conditions — pullback risk"
            )
        else:
            points.append(f"RSI at {indicators.rsi:.1f} — neutral momentum zone (30–70)")

    # ── MACD ──────────────────────────────────────────────────────────────────
    if indicators.macd_cross == "BULLISH_CROSS":
        points.append("MACD bullish crossover — momentum shifting upward")
    elif indicators.macd_cross == "BEARISH_CROSS":
        points.append("MACD bearish crossover — momentum shifting downward")
    elif not nan(indicators.macd_histogram):
        sign = "positive" if indicators.macd_histogram > 0 else "negative"
        bias = "bullish" if indicators.macd_histogram > 0 else "bearish"
        points.append(
            f"MACD histogram {sign} ({indicators.macd_histogram:+.5f}) — {bias} momentum"
        )

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    _bb_msgs = {
        "BELOW_LOWER": "Price below lower Bollinger Band — oversold, bounce expected",
        "NEAR_LOWER":  "Price near lower Bollinger Band — approaching support zone",
        "ABOVE_UPPER": "Price above upper Bollinger Band — overbought, pullback risk",
        "NEAR_UPPER":  "Price near upper Bollinger Band — approaching resistance zone",
        "MIDDLE":      "Price within Bollinger Bands — no extreme reading",
    }
    msg = _bb_msgs.get(indicators.bb_position)
    if msg:
        points.append(msg)

    # ── EMA trend ─────────────────────────────────────────────────────────────
    _ema_msgs = {
        "STRONG_BULL": "EMA trend: price above all 3 EMAs — confirmed uptrend",
        "BULL":        "EMA trend: price above EMA20 and EMA50 — bullish bias",
        "BEAR":        "EMA trend: price below EMA20 and EMA50 — bearish bias",
        "STRONG_BEAR": "EMA trend: price below all 3 EMAs — confirmed downtrend",
        "NEUTRAL":     "EMA trend: mixed signals — no clear directional bias",
    }
    msg = _ema_msgs.get(indicators.ema_trend)
    if msg:
        points.append(msg)

    # ── Stochastic ────────────────────────────────────────────────────────────
    if not (nan(indicators.stoch_k) or nan(indicators.stoch_d)):
        if indicators.stoch_signal == "OVERSOLD":
            points.append(
                f"Stochastic K={indicators.stoch_k:.1f} / D={indicators.stoch_d:.1f} "
                f"— oversold territory, reversal signal"
            )
        elif indicators.stoch_signal == "OVERBOUGHT":
            points.append(
                f"Stochastic K={indicators.stoch_k:.1f} / D={indicators.stoch_d:.1f} "
                f"— overbought territory, reversal risk"
            )

    # ── Candlestick patterns ──────────────────────────────────────────────────
    strongest = pattern_summary.get("strongest_pattern")
    if strongest:
        direction_word = pattern_summary.get("direction", "").lower()
        count          = pattern_summary.get("count", 1)
        suffix         = f" ({count} patterns total)" if count > 1 else ""
        points.append(
            f"{strongest} pattern detected{suffix} — {direction_word} signal"
        )
    else:
        points.append("No candlestick patterns detected on this bar")

    # ── News sentiment ────────────────────────────────────────────────────────
    raw_score = sentiment_score / 100.0     # back to -1..+1 for display
    if abs(raw_score) >= 0.05:
        label = "positive" if raw_score > 0 else "negative"
        points.append(
            f"FinBERT news sentiment: {raw_score:+.2f} ({label}) for {symbol}"
        )
    else:
        points.append(f"News sentiment neutral for {symbol} (score ≈ 0)")

    # ── Final verdict ─────────────────────────────────────────────────────────
    points.append(
        f"Final confluence score {final_score:+.1f} → {action} "
        f"(weights: patterns 35 %, indicators 45 %, sentiment 20 %)"
    )

    return points


# ═══════════════════════════════════════════════════════════════════════════════
# Core signal generator
# ═══════════════════════════════════════════════════════════════════════════════

async def generate_signal(
    symbol:      str,
    timeframe:   str,
    candles_df:  pd.DataFrame,
    session:     AsyncSession,
) -> TradingSignal:
    """Generate a BUY / SELL / HOLD decision for a single symbol.

    Steps
    -----
    1. Detect candlestick patterns and normalise their aggregate score.
    2. Compute technical indicators and retrieve their composite score.
    3. Fetch FinBERT news sentiment from the DB (last 10 relevant headlines).
    4. Combine the three scores using the configured weights.
    5. Apply decision thresholds with guard clauses (no BUY when overbought).
    6. Calculate entry price, stop-loss, and take-profit via ATR.
    7. Build human-readable reasoning points.

    Parameters
    ----------
    symbol      : Ticker string, e.g. 'EUR/USD' or 'AAPL'.
    timeframe   : Candle interval string, e.g. '1h'.
    candles_df  : OHLCV DataFrame (at least 5 rows; 50+ recommended).
    session     : Active async SQLAlchemy session for news DB query.

    Returns
    -------
    TradingSignal — always returns, never raises.
    """
    now = datetime.utcnow()

    # ── Fallback for insufficient data ────────────────────────────────────────
    if candles_df.empty or len(candles_df) < 5:
        logger.warning(
            f"generate_signal: insufficient candles for {symbol} ({len(candles_df)} rows)"
        )
        entry = float(candles_df["close"].iloc[-1]) if not candles_df.empty else 0.0
        return TradingSignal(
            symbol=symbol, timeframe=timeframe, action="HOLD",
            confidence=0.0, entry_price=entry,
            stop_loss=entry, take_profit=entry,
            pattern_score=0.0, indicator_score=0.0,
            sentiment_score=0.0, final_score=0.0,
            patterns_detected=[],
            reasoning_points=["Insufficient price data — defaulting to HOLD"],
            timestamp=now,
        )

    # ── Step 1: Candlestick patterns ──────────────────────────────────────────
    patterns        = detect_patterns(candles_df)
    pattern_summary = get_pattern_summary(patterns)
    pattern_score   = _normalize_pattern_score(pattern_summary["total_score"])
    patterns_names  = [p.name for p in patterns]

    # ── Step 2: Technical indicators ──────────────────────────────────────────
    indicators      = compute_indicators(candles_df)
    indicator_score = indicators.composite_score        # already -100..+100

    # ── Step 3: News sentiment ────────────────────────────────────────────────
    try:
        raw_sentiment = await get_market_sentiment(symbol, session)  # -1..+1
    except Exception as exc:
        logger.warning(f"generate_signal: sentiment fetch failed for {symbol}: {exc}")
        raw_sentiment = 0.0
    sentiment_score = raw_sentiment * 100.0             # scale to -100..+100

    # ── Step 4: Confluence score ──────────────────────────────────────────────
    final_score = (
        pattern_score   * 0.35
        + indicator_score * 0.45
        + sentiment_score * 0.20
    )

    # ── Step 5: Decision rules with guard clauses ─────────────────────────────
    if final_score > _BUY_THRESHOLD and indicators.rsi_signal != "OVERBOUGHT":
        action = "BUY"
    elif final_score < _SELL_THRESHOLD and indicators.rsi_signal != "OVERSOLD":
        action = "SELL"
    else:
        action = "HOLD"

    confidence = min(abs(final_score), 100.0)

    # ── Step 6: Price levels ──────────────────────────────────────────────────
    entry_price = float(candles_df["close"].iloc[-1])
    atr         = indicators.atr if not math.isnan(indicators.atr) else entry_price * 0.001
    # For HOLD, calculate levels as if BUY so fields are always populated
    direction   = action if action != "HOLD" else "BUY"
    stop_loss   = suggest_stop_loss(entry_price, direction, atr)
    take_profit = suggest_take_profit(entry_price, stop_loss, direction)

    # ── Step 7: Reasoning ─────────────────────────────────────────────────────
    reasoning = _build_reasoning(
        symbol, indicators, pattern_summary, patterns,
        sentiment_score, final_score, action,
    )

    signal = TradingSignal(
        symbol=symbol,
        timeframe=timeframe,
        action=action,
        confidence=round(confidence, 2),
        entry_price=round(entry_price, 6),
        stop_loss=round(stop_loss, 6),
        take_profit=round(take_profit, 6),
        pattern_score=round(pattern_score, 2),
        indicator_score=round(indicator_score, 2),
        sentiment_score=round(sentiment_score, 2),
        final_score=round(final_score, 2),
        patterns_detected=patterns_names,
        reasoning_points=reasoning,
        timestamp=now,
    )

    logger.info(
        f"SIGNAL │ {action:<4} {symbol:<12} │ "
        f"Confidence: {confidence:.0f}% │ "
        f"Score: {final_score:+.1f} │ "
        f"patterns={len(patterns_names)} "
        f"ind={indicator_score:+.1f} "
        f"sent={sentiment_score:+.1f}"
    )

    return signal


# ═══════════════════════════════════════════════════════════════════════════════
# DB persistence
# ═══════════════════════════════════════════════════════════════════════════════

async def save_signal(signal: TradingSignal, session: AsyncSession) -> Signal:
    """Persist a TradingSignal to the signals table.

    Maps the dataclass fields onto the Signal ORM model.
    Calls session.flush() but does NOT commit — the caller owns the transaction.

    Returns the newly created Signal row (with id populated after flush).
    """
    strongest = (
        signal.patterns_detected[0] if signal.patterns_detected else ""
    )[:80]

    row = Signal(
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        signal_type=SignalType(signal.action),
        confidence=signal.confidence,
        pattern_name=strongest,
        indicators_data={
            "indicator_score": signal.indicator_score,
            "pattern_score":   signal.pattern_score,
            "sentiment_score": signal.sentiment_score,
            "patterns":        signal.patterns_detected,
            "reasoning":       signal.reasoning_points,
            "entry_price":     signal.entry_price,
            "stop_loss":       signal.stop_loss,
            "take_profit":     signal.take_profit,
        },
        news_sentiment=signal.sentiment_score / 100.0,  # store as -1..+1
        final_score=signal.final_score,
    )
    session.add(row)
    await session.flush()

    logger.debug(
        f"save_signal: persisted Signal id={row.id}  "
        f"{signal.action} {signal.symbol}  score={signal.final_score:+.1f}"
    )
    return row


# ═══════════════════════════════════════════════════════════════════════════════
# Batch analyser
# ═══════════════════════════════════════════════════════════════════════════════

async def analyze_all_symbols(session: AsyncSession) -> list[TradingSignal]:
    """Run signal generation for every symbol in the watchlists.

    For each symbol:
      1. Fetches the last 200 × 1h candles from the DB.
      2. Skips the symbol when fewer than 10 candles are available.
      3. Generates a signal, saves it, and continues on any per-symbol error.

    Returns only BUY and SELL signals (HOLD is filtered out), sorted by
    abs(final_score) descending so the strongest conviction signals appear first.

    The caller is responsible for committing or rolling back the session.

    Returns
    -------
    list[TradingSignal]
    """
    from crawler.price_feed import get_latest_candles  # local to avoid circular imports

    timeframe   = "1h"
    all_symbols = settings.forex_symbols + settings.stock_symbols
    actionable: list[TradingSignal] = []

    logger.info(
        f"━━ analyze_all_symbols START ━━  {len(all_symbols)} symbols  "
        f"timeframe={timeframe}"
    )

    for symbol in all_symbols:
        try:
            candle_rows = await get_latest_candles(symbol, timeframe, 200, session)

            if len(candle_rows) < 10:
                logger.warning(
                    f"analyze_all_symbols: skipping {symbol} — "
                    f"only {len(candle_rows)} candles in DB (need ≥ 10)"
                )
                continue

            # get_latest_candles returns newest-first; reverse for chronological order
            candle_rows = list(reversed(candle_rows))
            df = pd.DataFrame([
                {
                    "open":      c.open,
                    "high":      c.high,
                    "low":       c.low,
                    "close":     c.close,
                    "volume":    c.volume,
                    "timestamp": c.timestamp,
                }
                for c in candle_rows
            ])

            signal = await generate_signal(symbol, timeframe, df, session)
            await save_signal(signal, session)

            if signal.action in ("BUY", "SELL"):
                actionable.append(signal)

        except Exception as exc:
            logger.error(f"analyze_all_symbols: error processing {symbol}: {exc}")

    actionable.sort(key=lambda s: abs(s.final_score), reverse=True)

    logger.info(
        f"━━ analyze_all_symbols DONE  ━━  "
        f"processed={len(all_symbols)}  "
        f"actionable={len(actionable)}  "
        f"(BUY={sum(1 for s in actionable if s.action == 'BUY')}  "
        f"SELL={sum(1 for s in actionable if s.action == 'SELL')})"
    )
    return actionable
