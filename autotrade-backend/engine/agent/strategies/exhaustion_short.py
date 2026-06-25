"""Exhaustion short — dead-cat-bounce fade in a confirmed downtrend.

Entry: stock bounces back to near EMA20 resistance while EMA20 < EMA50 < EMA200
(triple-bear stack). Bearish rejection candle confirms the bounce is failing.
Exit: target = EMA50 below entry; stop = last high + 0.5×ATR.

NSE Rule: SELL (short-selling) can only be done intraday (MIS product).
Delivery short-selling of equities is not permitted on NSE/BSE under SEBI rules.
This strategy is therefore MIS-only and requires EQUITY_SHORT_ENABLED=True.
"""
from __future__ import annotations

from utils.config import settings
from .base import Strategy, TradeCandidate


class ExhaustionShort(Strategy):
    name = "EXHAUSTION_SHORT"

    def evaluate(self, symbol, df, f, macro_bias, fund_grade):
        if not getattr(settings, "EQUITY_SHORT_ENABLED", False):
            return None

        # Triple-bear EMA stack: confirmed medium-term downtrend
        if not (f.ema20 < f.ema50 < f.ema200):
            return None

        # Price has bounced up toward EMA20 resistance zone (within 7%).
        # Phase 5: widened from 2% to 7% — more dead-cat bounces qualify.
        if f.close < f.ema20 * 0.93:
            return None

        # Momentum shows exhaustion on the bounce — above neutral midpoint.
        # Phase 5: lowered from 62 to 55 to capture more fades.
        if f.rsi14 < 55:
            return None

        # Trend must still have some strength (not pure chop)
        if f.adx14 < 15:
            return None

        last = df.iloc[-1]
        o    = float(last["open"])
        c    = float(last["close"])
        hi   = float(last["high"])

        # Phase 6: simplified candle requirement — just bearish close (c < o).
        # Phase 5 required upper_wick >= 1.5×body which was too strict (n=15 only).
        if c >= o:
            return None

        reasons = [
            "ema20<ema50<ema200",
            "dead_cat_bounce_to_ema20",
            f"rsi_exhausted:{f.rsi14:.1f}",
            "bearish_close",
        ]

        entry  = c
        stop   = round(hi + 0.5 * f.atr14, 2)
        target = round(f.ema50, 2)       # mean-reversion to EMA50
        risk   = stop - entry

        if risk <= 0 or target >= entry:
            return None

        conf = 68
        if macro_bias < 0:
            conf += 5;  reasons.append(f"macro_bearish:{macro_bias}")
        if f.pattern_direction == "BEARISH":
            conf += 4;  reasons.append(f"pattern:{f.strongest_pattern}")
        if f.vol_spike:
            conf += 3;  reasons.append("vol_spike:sellers_active")

        return TradeCandidate(
            symbol=symbol, side="SELL",
            entry=round(entry, 2), stop=stop, target=target,
            confidence=min(conf, 90), reasons=reasons, strategy=self.name,
        )
