"""Pullback continuation long — Varsity Module 2 rule M2.2.

Reference: trading_agent/strategies/pullback_trend.py
"""
from .base import Strategy, TradeCandidate


class PullbackTrendLong(Strategy):
    name = "PULLBACK_LONG"

    def evaluate(self, symbol, df, f, macro_bias, fund_grade):
        if f.regime != "BULL_TRENDING":   return None
        if len(df) < 2:                   return None

        prev = df.iloc[-2]
        last = df.iloc[-1]

        # Previous bar touched the 20EMA
        touched = float(prev["low"]) <= f.ema20 <= float(prev["high"])
        if not touched:                   return None
        # Last bar closed back above EMA
        if float(last["close"]) <= f.ema20: return None
        if f.rsi14 < 45:                  return None
        if not (f.ema20 > f.ema50):       return None
        # Require some actual momentum — ADX < 15 is directionless chop where
        # pullback entries have no follow-through.
        if f.adx14 < 15:                  return None

        reasons = [
            "bull_trending_regime",
            "pullback_to_20ema",
            "close_back_above_20ema",
            f"rsi14={f.rsi14:.1f}",
        ]

        entry  = float(last["close"])
        stop   = float(prev["low"]) - 0.5 * f.atr14
        risk   = entry - stop
        target = entry + 2.0 * risk

        if risk <= 0 or target <= entry:  return None

        conf = 70
        if macro_bias > 0:
            conf += 5;  reasons.append(f"macro_bias:+{macro_bias}")
        if fund_grade in ("INVESTMENT", "WATCHLIST"):
            conf += 3;  reasons.append(f"fund:{fund_grade.lower()}")
        if f.pattern_direction == "BULLISH":
            conf += 3;  reasons.append(f"pattern:{f.strongest_pattern}")

        return TradeCandidate(
            symbol=symbol, side="BUY",
            entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2),
            confidence=min(conf, 95), reasons=reasons, strategy=self.name,
        )
