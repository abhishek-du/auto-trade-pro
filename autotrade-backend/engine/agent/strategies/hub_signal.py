"""Hub Signal Strategy — uses the market_shortlist composite score directly.

This is the widest-net strategy: any stock with a BUY signal and score ≥ min_score
qualifies. ATR-based stop and 2:1 R:R target are derived from price action.

Why this exists:
  The four Varsity strategies (TrendBreakout, Pullback, MeanReversion,
  RangeReversal) require very specific combinations of regime + indicator
  thresholds. In most market conditions only 0-2 stocks in the large-cap
  watchlist satisfy all gates simultaneously, so the agent idles.

  HubSignalStrategy uses the 7-factor Master Intelligence Score that the
  scanner already computed — a stock that cleared 9,600-symbol scoring,
  volume/price filters, and ranked in the shortlist already passed a
  high-quality bar. We just need entry/stop/target from price action.
"""
from __future__ import annotations

from .base import Strategy, TradeCandidate


class HubSignalStrategy(Strategy):
    name = "HUB_SIGNAL"

    # Map hub composite score to confidence %.
    # score ≥ 50  →  75%+  (strong BUY)
    # score ≥ 30  →  55%+  (moderate BUY)
    # score ≥ 10  →  40%+  (weak BUY, passes 30% threshold)
    _SCORE_TO_CONF = [(50, 75), (30, 55), (10, 40)]
    _MIN_SCORE = 10   # below this, don't even try

    def evaluate(self, symbol, df, features, macro_bias, fund_grade):
        # Hub composite score is stored on features by the agent loop
        hub_score = getattr(features, "hub_composite_score", None)
        hub_signal = getattr(features, "hub_signal", "HOLD")

        if hub_score is None:
            return None

        is_buy  = "BUY"  in str(hub_signal).upper()
        is_sell = "SELL" in str(hub_signal).upper()

        if not (is_buy or is_sell):
            return None

        side = "BUY" if is_buy else "SELL"
        score_abs = abs(hub_score)

        if score_abs < self._MIN_SCORE:
            return None

        # Regime gates: never enter a BUY in a confirmed bear trend or truly
        # directionless chop (UNKNOWN + weak ADX). These are the conditions
        # where HUB_SIGNAL fires most often but wins least often.
        regime = getattr(features, "regime", "UNKNOWN")
        adx    = getattr(features, "adx14", 0)
        if side == "BUY" and regime == "BEAR_TRENDING":
            return None
        if side == "BUY" and regime == "UNKNOWN" and adx < 15:
            return None

        # Derive entry/stop/target from recent price action
        entry = features.close
        atr   = features.atr14
        if atr <= 0:
            return None

        if side == "BUY":
            stop   = round(entry - 2.0 * atr, 2)
            target = round(entry + 4.0 * atr, 2)  # 2:1 R:R on 2-ATR stop
        else:
            stop   = round(entry + 2.0 * atr, 2)
            target = round(entry - 4.0 * atr, 2)

        risk = abs(entry - stop)
        if risk <= 0:
            return None

        # Confidence from hub score magnitude
        conf = 35  # base
        for threshold, conf_val in self._SCORE_TO_CONF:
            if score_abs >= threshold:
                conf = conf_val
                break

        reasons = [
            f"hub_score:{hub_score:.1f}",
            f"hub_signal:{hub_signal}",
            f"regime:{features.regime}",
        ]

        # Bonuses
        if features.regime == "BULL_TRENDING" and side == "BUY":
            conf += 8
            reasons.append("bull_regime_confirms_buy")
        if features.st_dir == 1 and side == "BUY":
            conf += 4
            reasons.append("supertrend:bull")
        if macro_bias > 0 and side == "BUY":
            conf += 3
            reasons.append(f"macro_bias:+{macro_bias}")
        if fund_grade == "INVESTMENT":
            conf += 5
            reasons.append("fund:investment_grade")
        elif fund_grade == "WATCHLIST":
            conf += 2
            reasons.append("fund:watchlist")

        return TradeCandidate(
            symbol=symbol,
            side=side,
            entry=round(entry, 2),
            stop=stop,
            target=target,
            confidence=min(conf, 90),
            reasons=reasons,
            strategy=self.name,
        )
