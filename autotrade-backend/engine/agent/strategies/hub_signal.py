"""Hub Signal Strategy — uses the market_shortlist composite score directly.

Phase 7 tightening:
  - EMA50 > EMA200 required (long-term bull trend confirmed, no dead-cat bounces)
  - ADX > 25 required (strong momentum, not chop)
  - Volume spike required (institutional participation)
  - RSI 45–70 required (not overbought, not dead)
  - Min hub score raised 10 → 40 (STRONG_BUY only)
  - Stop tightened 2×ATR → 1×ATR (half the risk, same 2:1 R:R)
  - Min confidence raised to 80 before returning
"""
from __future__ import annotations

from .base import Strategy, TradeCandidate


class HubSignalStrategy(Strategy):
    name = "HUB_SIGNAL"

    _SCORE_TO_CONF = [(70, 82), (55, 78), (40, 74)]
    _MIN_SCORE = 40   # Phase 7: STRONG_BUY only (was 10)

    def evaluate(self, symbol, df, features, macro_bias, fund_grade):
        hub_score  = getattr(features, "hub_composite_score", None)
        hub_signal = getattr(features, "hub_signal", "HOLD")

        if hub_score is None:
            return None

        is_buy = "BUY" in str(hub_signal).upper()
        if not is_buy:
            return None   # Phase 7: BUY only (SELL requires a separate short review)

        score_abs = abs(hub_score)
        if score_abs < self._MIN_SCORE:
            return None

        # ── Phase 7 trend quality gates ──────────────────────────────────────
        # 1. Long-term bull trend: EMA50 must be above EMA200
        if not (features.ema50 > features.ema200):
            return None

        # 2. Strong momentum only — ADX > 25 (trending, not chop)
        if getattr(features, "adx14", 0) <= 25:
            return None

        # 3. Per-stock regime: never buy a confirmed bear trend
        regime = getattr(features, "regime", "UNKNOWN")
        if regime == "BEAR_TRENDING":
            return None

        # 4. Volume confirmation — institutional participation on this bar
        if not getattr(features, "vol_spike", False):
            return None

        # 5. RSI in a healthy range: not exhausted, not overbought
        rsi = getattr(features, "rsi14", 50)
        if not (45 <= rsi <= 70):
            return None

        # ── Entry / stop / target ─────────────────────────────────────────────
        entry = features.close
        atr   = features.atr14
        if atr <= 0:
            return None

        # Phase 7: 1×ATR stop (was 2×ATR) → tighter losses, same 2:1 R:R
        stop   = round(entry - 1.0 * atr, 2)
        target = round(entry + 2.0 * atr, 2)
        risk   = entry - stop
        if risk <= 0:
            return None

        # ── Confidence ────────────────────────────────────────────────────────
        conf = 70  # base (higher floor than before)
        for threshold, conf_val in self._SCORE_TO_CONF:
            if score_abs >= threshold:
                conf = conf_val
                break

        reasons = [
            f"hub_score:{hub_score:.1f}",
            f"hub_signal:{hub_signal}",
            f"regime:{regime}",
        ]

        if regime == "BULL_TRENDING":
            conf += 6;  reasons.append("bull_regime_confirms_buy")
        if features.st_dir == 1:
            conf += 4;  reasons.append("supertrend:bull")
        if macro_bias > 0:
            conf += 3;  reasons.append(f"macro_bias:+{macro_bias}")
        if fund_grade == "INVESTMENT":
            conf += 5;  reasons.append("fund:investment_grade")
        elif fund_grade == "WATCHLIST":
            conf += 2;  reasons.append("fund:watchlist")

        conf = min(conf, 92)

        # Phase 7: hard minimum confidence of 80 — below this, not worth taking
        if conf < 80:
            return None

        return TradeCandidate(
            symbol=symbol, side="BUY",
            entry=round(entry, 2), stop=stop, target=target,
            confidence=conf, reasons=reasons, strategy=self.name,
        )
