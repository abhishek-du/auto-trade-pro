"""Mean-reversion short at range top — Varsity Module 2 rule M2.3.

Reference: trading_agent/strategies/mean_reversion.py
"""
from .base import Strategy, TradeCandidate


class MeanReversionShort(Strategy):
    name = "MEAN_REVERSION_SHORT"

    def evaluate(self, symbol, df, f, macro_bias, fund_grade):
        if f.regime not in ("RANGE", "HIGH_VOL_RANGE"): return None
        if f.close < f.bb_upper:                         return None
        if f.rsi14 < 70:                                 return None

        last = df.iloc[-1]
        body        = abs(float(last["close"]) - float(last["open"]))
        upper_wick  = float(last["high"]) - max(float(last["close"]), float(last["open"]))
        bearish_rej = float(last["close"]) < float(last["open"]) and upper_wick > 1.5 * body
        if not bearish_rej: return None

        reasons = [
            "range_regime",
            f"price_above_BB_upper:{f.bb_upper:.2f}",
            f"rsi_overbought:{f.rsi14:.1f}",
            "bearish_rejection_candle",
        ]

        entry  = float(last["close"])
        stop   = float(last["high"]) + 0.5 * f.atr14
        target = f.bb_mid
        risk   = stop - entry

        if risk <= 0 or target >= entry: return None

        conf = 65
        if macro_bias < 0:
            conf += 5;  reasons.append(f"macro_bearish:{macro_bias}")
        if f.pattern_direction == "BEARISH":
            conf += 4;  reasons.append(f"pattern:{f.strongest_pattern}")

        return TradeCandidate(
            symbol=symbol, side="SELL",
            entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2),
            confidence=min(conf, 95), reasons=reasons, strategy=self.name,
        )
