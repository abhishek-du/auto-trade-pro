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
        if f.rsi14 < 50:                  return None
        if not (f.ema20 > f.ema50):       return None
        # Require actual momentum — ADX < 20 = directionless chop, no follow-through.
        if f.adx14 < 20:                  return None
        # Phase 6: EMA spread — EMA50 must be at least 1% above EMA200 to confirm
        # an established trend (not just a fresh, fragile EMA cross).
        if not (f.ema50 >= f.ema200 * 1.01): return None
        # Phase 6: weekly trend proxy — close must be above 100-day EMA (≈ 20-week EMA).
        # Blocks entries in stocks in a long-term downtrend even if daily stack aligns.
        ema100 = getattr(f, "ema100", None)
        if ema100 is not None and f.close < ema100: return None
        # Don't buy into already-overbought territory.
        if f.rsi14 > 70:                  return None
        # Phase 5: shallow touch only — prev bar's low must be within 3% of EMA20.
        if float(prev["low"]) < f.ema20 * 0.97: return None
        # Phase 6: quiet pullback — prev bar must NOT have had a vol_spike (panic sell).
        # Quiet pullback + high-volume bounce = institutional accumulation pattern.
        if bool(prev.get("vol_spike", False)): return None
        # Phase 5: require volume confirmation on the bounce bar — buyers must step in.
        if not f.vol_spike:               return None
        # Phase 6: ADX must not be collapsing — trend strength holding.
        # In backtest: prev bar has precomputed adx14. In live: raw df has no adx14,
        # so .get() falls back to current adx14 and the check always passes (safe).
        prev_adx = float(prev.get("adx14", f.adx14))
        if prev_adx > 0 and f.adx14 < prev_adx * 0.85: return None
        # EMA20 slope filter (Phase 9): the 20-EMA must be RISING — today's EMA20 must
        # exceed its value 5 bars ago. A flat or declining EMA20 means the pullback may
        # be a trend reversal, not accumulation. Confirmed by expert sources (Zerodha
        # Varsity, Swingfolio): "slope filter = 20 MA today above 20 MA 5 bars ago."
        if len(df) >= 6:
            ema20_5ago = float(df["close"].ewm(span=20, adjust=False).mean().iloc[-6])
            if f.ema20 <= ema20_5ago: return None

        reasons = [
            "bull_trending_regime",
            "pullback_to_20ema",
            "close_back_above_20ema",
            f"rsi14={f.rsi14:.1f}",
            "ema50>ema200",
        ]

        entry  = float(last["close"])
        stop   = float(prev["low"]) - 0.5 * f.atr14
        risk   = entry - stop
        target = entry + 2.0 * risk

        if risk <= 0 or target <= entry:  return None

        conf = 76
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
