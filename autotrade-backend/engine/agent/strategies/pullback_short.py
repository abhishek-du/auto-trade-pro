"""Pullback Short — mirror of PULLBACK_LONG for bear regime.

Entry trigger: stock in BEAR_TRENDING; price bounced UP to the 20-EMA
(low-volume dead-cat bounce), then closes back below the EMA — confirming
the EMA as resistance. Short entry on the rejection bar.

NSE/SEBI Rule: equity short-selling is intraday-only (MIS product).
Gated by EQUITY_SHORT_ENABLED=True. Only activates when the 5-state macro
regime (market_regime.py) is WEAK_BEAR or STRONG_BEAR.

Exit: stop = prev_high + 0.5×ATR (above the failed bounce),
      target = entry - 2×risk  (2:1 R:R, same as PULLBACK_LONG).
"""
from __future__ import annotations

from utils.config import settings
from .base import Strategy, TradeCandidate


class PullbackShort(Strategy):
    name = "PULLBACK_SHORT"

    def evaluate(self, symbol, df, f, macro_bias, fund_grade):
        # Gate 1: SEBI compliance — intraday short only
        if not getattr(settings, "EQUITY_SHORT_ENABLED", False):
            return None

        if len(df) < 2:
            return None

        prev = df.iloc[-2]
        last = df.iloc[-1]

        # Gate 2: Per-stock regime must be BEAR_TRENDING
        # EMA20 < EMA50 confirms short-term is below mid-term
        if f.regime != "BEAR_TRENDING":
            return None
        if not (f.ema20 < f.ema50):
            return None

        # Gate 3: Established bear trend (not fresh cross) — EMA50 at least 0.5%
        # below EMA200. Fresh EMA crosses often whipsaw.
        if not (f.ema50 <= f.ema200 * 0.995):
            return None

        # Gate 4: Weekly trend proxy — close must be BELOW 100-day EMA.
        # Mirror of PULLBACK_LONG's ema100 check — ensures we're not shorting
        # a stock that is in a long-term uptrend (only a short-term dip).
        ema100 = getattr(f, "ema100", None)
        if ema100 is not None and f.close > ema100:
            return None

        # Gate 5: RSI in the dead-cat bounce zone (40-58).
        # Too low (< 40) = already deeply oversold, bounce may persist.
        # Too high (> 58) = approaching EXHAUSTION_SHORT territory.
        if not (40 <= f.rsi14 <= 58):
            return None

        # Gate 6: Trend must have directional strength.
        if f.adx14 < 20:
            return None

        # Gate 7: ADX must not be collapsing (trend still has legs).
        prev_adx = float(prev.get("adx14", f.adx14))
        if prev_adx > 0 and f.adx14 < prev_adx * 0.85:
            return None

        # Gate 8: Previous bar must ACTUALLY touch EMA20 (actual resistance test).
        # The dead-cat bounce must reach the EMA20 level — "near it" is not enough.
        # A high within 5% of EMA20 catches stocks that never tested the EMA, just
        # bounced from deep below. Require actual touch: prev_high >= ema20.
        prev_high = float(prev["high"])
        if prev_high < f.ema20:   # strict: must reach EMA20 level
            return None

        # Gate 9: Bounce must not be a clean breakout above EMA20 (within 2%).
        # A high more than 2% above EMA20 means the EMA was taken out — not a
        # rejection, it's a breakout attempt (could be a bull reclaim).
        if prev_high > f.ema20 * 1.02:
            return None

        # Gate 10: Last bar closes MEANINGFULLY below EMA20 — strong rejection.
        # Close must be at least 1% below EMA20 to confirm genuine rejection
        # (not just a flat close at the EMA which could go either way).
        if float(last["close"]) >= f.ema20 * 0.99:
            return None

        # Gate 11: Quiet bounce — previous bar should NOT have a volume spike.
        # Strong-volume bounces mean institutional buying, not a dead-cat.
        if bool(prev.get("vol_spike", False)):
            return None

        # Gate 12: Don't short into extreme fear — wait for overbought bounce.
        # If the stock just dumped with high volume on THIS bar, too risky.
        if f.vol_spike and float(last["close"]) < float(last["open"]):
            return None

        reasons = [
            "bear_trending_regime",
            "bounce_to_20ema_rejected",
            "ema20<ema50<ema200",
            f"rsi14={f.rsi14:.1f}",
            "quiet_bounce",
        ]

        entry  = float(last["close"])
        stop   = round(prev_high + 0.5 * f.atr14, 2)   # above failed bounce high
        risk   = stop - entry
        target = round(entry - 2.0 * risk, 2)            # 2:1 R:R

        if risk <= 0 or target >= entry:
            return None

        conf = 74
        if macro_bias < 0:
            conf += 4;  reasons.append(f"macro_bearish:{macro_bias}")
        if fund_grade in ("WATCHLIST",):
            pass  # neutral for shorts
        if f.pattern_direction == "BEARISH":
            conf += 4;  reasons.append(f"pattern:{f.strongest_pattern}")
        if f.vol_spike:
            conf += 2;  reasons.append("rejection_vol")

        return TradeCandidate(
            symbol=symbol, side="SELL",
            entry=round(entry, 2), stop=stop, target=target,
            confidence=min(conf, 90), reasons=reasons, strategy=self.name,
        )
