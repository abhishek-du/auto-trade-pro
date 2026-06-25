"""Range reversal long at BB lower — Varsity Module 2.

Hammer / bullish engulfing at support in a ranging market.
"""
from .base import Strategy, TradeCandidate


class RangeReversalLong(Strategy):
    name = "RANGE_REVERSAL_LONG"

    def evaluate(self, symbol, df, f, macro_bias, fund_grade):
        if f.regime not in ("RANGE", "HIGH_VOL_RANGE", "LOW_VOL_RANGE"): return None
        if f.close > f.bb_lower:  return None
        if f.rsi14 > 35:          return None
        # Don't buy a reversal when the medium-term trend is already down —
        # that's catching a falling knife. EMA50 < EMA200 = confirmed downtrend.
        if f.ema50 < f.ema200:    return None
        # ADX > 25 means the market is trending, not ranging; skip to avoid
        # fighting a trend with a mean-reversion entry.
        if f.adx14 > 25:          return None

        last = df.iloc[-1]
        o, c, h, lo = (float(last[x]) for x in ("open", "close", "high", "low"))
        body        = abs(c - o)
        lower_wick  = min(c, o) - lo
        is_hammer   = (lower_wick > 2 * body) and (c > o)

        if not is_hammer and f.pattern_direction != "BULLISH":
            return None

        reasons = [
            "range_regime",
            f"price_at_BB_lower:{f.bb_lower:.2f}",
            f"rsi_oversold:{f.rsi14:.1f}",
            "hammer_or_bullish_reversal",
        ]

        entry  = c
        stop   = lo - 0.5 * f.atr14
        target = f.bb_mid
        risk   = entry - stop

        if risk <= 0 or target <= entry: return None

        conf = 72
        if macro_bias > 0:                   conf += 4
        if fund_grade == "INVESTMENT":        conf += 5

        return TradeCandidate(
            symbol=symbol, side="BUY",
            entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2),
            confidence=min(conf, 95), reasons=reasons, strategy=self.name,
        )
