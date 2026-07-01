"""Hub Short Strategy — executes SELL signals from the 7-factor Hub score.

NSE/SEBI Rule: equity short-selling is intraday-only (MIS product).
Only fires when:
  - hub_signal is SELL or STRONG_SELL (negative hub_composite_score)
  - Individual stock is in BEAR_TRENDING (EMA50 < EMA200)
  - ADX > 20 (trending, not chop — lower bar than longs since bear moves are sharper)
  - RSI 30–55 (not in extreme oversold bounce territory)

Stop is ABOVE entry (1×ATR); target is BELOW entry (2×ATR → 2:1 R:R).
"""
from __future__ import annotations

from utils.config import settings
from .base import Strategy, TradeCandidate


class HubShortStrategy(Strategy):
    name = "HUB_SHORT"

    _SCORE_TO_CONF = [(70, 80), (55, 76), (40, 72)]
    _MIN_SCORE = 40   # minimum |hub_score| to consider a short

    def evaluate(self, symbol, df, features, macro_bias, fund_grade):
        if not getattr(settings, "EQUITY_SHORT_ENABLED", False):
            return None

        hub_score  = getattr(features, "hub_composite_score", None)
        hub_signal = getattr(features, "hub_signal", "HOLD")

        if hub_score is None:
            return None

        is_sell = "SELL" in str(hub_signal).upper()
        if not is_sell:
            return None

        score_abs = abs(hub_score)
        if score_abs < self._MIN_SCORE:
            return None

        # Individual stock must be in a confirmed bear trend
        if not (features.ema50 < features.ema200):
            return None

        # Momentum must be trending (not random chop)
        if getattr(features, "adx14", 0) <= 20:
            return None

        # Avoid extreme oversold RSI — those are bounce candidates, not fresh shorts
        rsi = getattr(features, "rsi14", 50)
        if not (30 <= rsi <= 55):
            return None

        # Volume confirmation — don't short on thin air
        if not getattr(features, "vol_spike", False):
            return None

        entry = features.close
        atr   = features.atr14
        if atr <= 0:
            return None

        stop   = round(entry + 1.0 * atr, 2)   # stop ABOVE entry for a short
        target = round(entry - 2.0 * atr, 2)   # target BELOW entry
        risk   = stop - entry
        if risk <= 0:
            return None

        conf = 68  # slightly lower base than BUY (shorts are harder)
        for threshold, conf_val in self._SCORE_TO_CONF:
            if score_abs >= threshold:
                conf = conf_val
                break

        reasons = [
            f"hub_score:{hub_score:.1f}",
            f"hub_signal:{hub_signal}",
            f"regime:{getattr(features, 'regime', '?')}",
        ]

        regime = getattr(features, "regime", "UNKNOWN")
        if regime == "BEAR_TRENDING":
            conf += 6;  reasons.append("bear_regime_confirms_short")
        if features.st_dir == -1:
            conf += 4;  reasons.append("supertrend:bear")
        if macro_bias < 0:
            conf += 3;  reasons.append(f"macro_bias:{macro_bias}")

        conf = min(conf, 88)

        if conf < 72:
            return None

        return TradeCandidate(
            symbol=symbol, side="SELL",
            entry=round(entry, 2), stop=stop, target=target,
            confidence=conf, reasons=reasons, strategy=self.name,
        )
