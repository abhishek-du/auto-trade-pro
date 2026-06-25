"""Market Analyzer Agent — computes features and classifies market regime.

Adapted from trading_agent/analyzer.py (reference) and wired to the
existing engine/indicators.py + engine/candlestick.py in this codebase.

Varsity Modules used: 2 (Technical Analysis), 9 (Risk), 16 (Quant)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.agent.indicators_agent import (
    ema, rsi, macd, atr, bollinger, adx_indicator, supertrend, volume_spike,
)
from utils.logger import logger


@dataclass
class MarketFeatures:
    # Price snapshot
    close:          float
    open_:          float
    high:           float
    low:            float
    volume:         float

    # EMAs
    ema20:          float
    ema50:          float
    ema100:         float   # 100-day ≈ weekly EMA20 proxy (Phase 6)
    ema200:         float

    # Momentum
    rsi14:          float
    macd_hist:      float

    # Volatility
    atr14:          float
    bb_upper:       float
    bb_lower:       float
    bb_mid:         float

    # Trend
    adx14:          float
    plus_di:        float
    minus_di:       float
    st_dir:         int     # 1 = bullish, -1 = bearish

    # Volume
    vol_spike:      bool

    # Structure
    swing_high_20:  float
    swing_low_20:   float

    # Pattern (from engine/candlestick.py)
    pattern_direction: str   # "BULLISH" | "BEARISH" | "NEUTRAL"
    pattern_score:     float
    strongest_pattern: str

    # Composite
    composite_score:   float
    regime:            str


class MarketAnalyzerAgent:
    """Computes a MarketFeatures snapshot from an OHLCV DataFrame."""

    def compute_features(self, df: pd.DataFrame) -> MarketFeatures:
        if len(df) < 30:
            raise ValueError(f"Need at least 30 bars, got {len(df)}")

        c = df["close"]

        e20  = ema(c, 20)
        e50  = ema(c, 50)
        e100 = ema(c, 100)
        e200 = ema(c, 200)

        r = rsi(c, 14)
        _, _, m_hist = macd(c)
        a = atr(df, 14)

        bu, bm, bl = bollinger(c)
        adxv, pdi, mdi = adx_indicator(df, 14)
        _, sdir = supertrend(df)
        vs = bool(volume_spike(df).iloc[-1])

        a_avg = a.rolling(50).mean().iloc[-1] if len(df) > 50 else float(a.iloc[-1])

        # Swing levels exclude current bar to test true breakouts
        sh = float(df["high"].rolling(20).max().iloc[-2]) if len(df) > 21 else float(df["high"].max())
        sl = float(df["low"].rolling(20).min().iloc[-2])  if len(df) > 21 else float(df["low"].min())

        regime = self._classify_regime(
            close=float(c.iloc[-1]),
            e20=float(e20.iloc[-1]),
            e50=float(e50.iloc[-1]),
            e200=float(e200.iloc[-1]),
            adxv=float(adxv.iloc[-1]) if not math.isnan(float(adxv.iloc[-1])) else 15.0,
            pdi=float(pdi.iloc[-1])   if not math.isnan(float(pdi.iloc[-1]))  else 20.0,
            mdi=float(mdi.iloc[-1])   if not math.isnan(float(mdi.iloc[-1]))  else 20.0,
            atrv=float(a.iloc[-1]),
            atrv_avg=float(a_avg) if not math.isnan(float(a_avg)) else float(a.iloc[-1]),
        )

        # Candlestick pattern summary (Varsity M2)
        pat_direction, pat_score, pat_name = self._pattern_summary(df)

        # Composite score for quick ranking
        comp = self._composite(
            rsi_val=float(r.iloc[-1]),
            macd_h=float(m_hist.iloc[-1]),
            regime=regime,
            st_dir=int(sdir.iloc[-1]),
            pat_score=pat_score,
        )

        last = df.iloc[-1]
        return MarketFeatures(
            close=float(c.iloc[-1]),
            open_=float(last["open"]),
            high=float(last["high"]),
            low=float(last["low"]),
            volume=float(last["volume"]),
            ema20=float(e20.iloc[-1]),
            ema50=float(e50.iloc[-1]),
            ema100=float(e100.iloc[-1]),
            ema200=float(e200.iloc[-1]),
            rsi14=float(r.iloc[-1]),
            macd_hist=float(m_hist.iloc[-1]),
            atr14=float(a.iloc[-1]),
            bb_upper=float(bu.iloc[-1]),
            bb_lower=float(bl.iloc[-1]),
            bb_mid=float(bm.iloc[-1]),
            adx14=float(adxv.iloc[-1]) if not math.isnan(float(adxv.iloc[-1])) else 15.0,
            plus_di=float(pdi.iloc[-1]) if not math.isnan(float(pdi.iloc[-1])) else 20.0,
            minus_di=float(mdi.iloc[-1]) if not math.isnan(float(mdi.iloc[-1])) else 20.0,
            st_dir=int(sdir.iloc[-1]),
            vol_spike=vs,
            swing_high_20=sh,
            swing_low_20=sl,
            pattern_direction=pat_direction,
            pattern_score=pat_score,
            strongest_pattern=pat_name,
            composite_score=comp,
            regime=regime,
        )

    @staticmethod
    def _classify_regime(
        close: float, e20: float, e50: float, e200: float,
        adxv: float, pdi: float, mdi: float,
        atrv: float, atrv_avg: float,
    ) -> str:
        """Dow Theory + ADX-based regime classifier (Varsity M2)."""
        trending = adxv >= 25
        bull = (close > e50 > e200) and (pdi > mdi)
        bear = (close < e50 < e200) and (mdi > pdi)

        if atrv_avg and atrv_avg > 0 and not math.isnan(atrv_avg):
            high_vol = atrv > 1.5 * atrv_avg
            low_vol  = atrv < 0.7 * atrv_avg
        else:
            high_vol = low_vol = False

        if trending and bull:   return "BULL_TRENDING"
        if trending and bear:   return "BEAR_TRENDING"
        if high_vol:            return "HIGH_VOL_RANGE"
        if low_vol:             return "LOW_VOL_RANGE"
        return "RANGE"

    @staticmethod
    def _pattern_summary(df: pd.DataFrame) -> tuple[str, float, str]:
        """Try existing candlestick engine; gracefully fall back."""
        try:
            from engine.candlestick import detect_patterns, get_pattern_summary
            raw = detect_patterns(df)
            summary = get_pattern_summary(raw)
            direction = summary.get("direction", "NEUTRAL")
            score     = float(summary.get("total_score", 0))
            name      = summary.get("strongest_pattern", "")
            return direction, score, name
        except Exception as exc:
            logger.debug(f"[agent/analyzer] candlestick engine skipped: {exc}")
            return "NEUTRAL", 0.0, ""

    @staticmethod
    def _composite(
        rsi_val: float, macd_h: float,
        regime: str, st_dir: int, pat_score: float,
    ) -> float:
        score = 0.0
        if not math.isnan(rsi_val):
            score += max(-20.0, min(20.0, 50.0 - rsi_val))
        if not math.isnan(macd_h):
            score += max(-15.0, min(15.0, macd_h * 100))
        regime_scores = {
            "BULL_TRENDING": 25.0, "BULL_RANGING": 10.0,
            "BEAR_TRENDING": -25.0, "BEAR_RANGING": -10.0,
        }
        score += regime_scores.get(regime, 0.0)
        score += st_dir * 10.0
        score += max(-10.0, min(10.0, pat_score * 3.0))
        return round(max(-100.0, min(100.0, score)), 2)
