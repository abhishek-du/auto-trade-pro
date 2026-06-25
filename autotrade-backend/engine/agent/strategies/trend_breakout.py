"""Trend-following breakout long — Varsity Module 2 rule M2.1.

Reference: trading_agent/strategies/trend_breakout.py
Additions: EMA alignment gate, supertrend confirmation.
"""
from .base import Strategy, TradeCandidate


class TrendBreakoutLong(Strategy):
    name = "TREND_BREAKOUT_LONG"

    def evaluate(self, symbol, df, f, macro_bias, fund_grade):
        if f.regime != "BULL_TRENDING":          return None
        if f.close <= f.swing_high_20:            return None
        if not f.vol_spike:                       return None
        if not (55 <= f.rsi14 <= 75):             return None
        if f.adx14 < 20:                          return None

        # EMA alignment (Varsity Dow Theory)
        if not (f.ema20 > f.ema50):               return None

        reasons = [
            "bull_trending_regime",
            "breakout_20bar_high",
            "volume_spike_>1.5x_avg",
            f"rsi14={f.rsi14:.1f}",
            f"adx14={f.adx14:.1f}",
            "ema20>ema50",
        ]

        entry  = f.close
        stop   = max(f.swing_high_20 - 1.5 * f.atr14, f.ema20 - 0.5 * f.atr14)
        risk   = entry - stop
        target = entry + 2.0 * risk

        if risk <= 0 or target <= entry:           return None

        conf = 65
        if macro_bias > 0:
            conf += 5;  reasons.append(f"macro_bias:+{macro_bias}")
        if fund_grade in ("INVESTMENT", "WATCHLIST"):
            conf += 5;  reasons.append(f"fund:{fund_grade.lower()}")
        if f.pattern_direction == "BULLISH":
            conf += 3;  reasons.append(f"pattern:{f.strongest_pattern}")
        if f.st_dir == 1:
            conf += 2;  reasons.append("supertrend:bull")

        return TradeCandidate(
            symbol=symbol, side="BUY",
            entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2),
            confidence=min(conf, 95), reasons=reasons, strategy=self.name,
        )
