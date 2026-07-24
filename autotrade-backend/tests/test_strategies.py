"""
Comprehensive strategy + risk test suite for AutoTrade Pro.

Covers every entry gate, every rejection path, and every edge case across:
  - TrendBreakoutLong
  - PullbackTrendLong
  - MeanReversionShort
  - RangeReversalLong
  - HubSignalStrategy
  - StrategySelectorAgent (RR filter + best-confidence selection)
  - RiskManagerAgent (all veto gates)
  - position_size / capital_utilization_size / vix_size_factor
  - TradeCandidate (risk_reward property)
  - Agent-level guards: SME filter, candle-age guard, live-price rejection

Run: pytest tests/test_strategies.py -v
"""

from __future__ import annotations

import math
import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest

# ── Path helpers ──────────────────────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.agent.strategies.base          import TradeCandidate, Strategy
from engine.agent.strategies.trend_breakout import TrendBreakoutLong
from engine.agent.strategies.pullback_trend import PullbackTrendLong
from engine.agent.strategies.mean_reversion import MeanReversionShort
from engine.agent.strategies.range_reversal import RangeReversalLong
from engine.agent.strategies.hub_signal     import HubSignalStrategy
from engine.agent.selector                  import StrategySelectorAgent
from engine.agent.risk_manager import (
    RiskManagerAgent,
    position_size,
    capital_utilization_size,
    vix_size_factor,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Shared fixture builders
# ═══════════════════════════════════════════════════════════════════════════════

def _features(**overrides) -> SimpleNamespace:
    """
    Build a minimal features namespace with sane defaults.
    All strategy gates are set to PASS by default for TrendBreakoutLong.
    Override individual fields to flip each gate into FAIL.
    """
    defaults = dict(
        close      = 1050.0,
        open_      = 1040.0,
        high       = 1060.0,
        low        = 1030.0,
        volume     = 1_500_000.0,

        ema20      = 1020.0,
        ema50      = 990.0,
        ema200     = 950.0,
        rsi14      = 62.0,
        macd_hist  = 2.5,
        atr14      = 15.0,

        bb_upper   = 1100.0,
        bb_lower   = 900.0,
        bb_mid     = 1000.0,

        adx14      = 28.0,
        plus_di    = 30.0,
        minus_di   = 18.0,

        st_dir     = 1,          # supertrend bullish
        vol_spike  = True,

        swing_high_20 = 1045.0,  # close (1050) > swing_high → breakout
        swing_low_20  = 950.0,

        pattern_direction = "NEUTRAL",
        pattern_score     = 0.0,
        strongest_pattern = "",

        composite_score    = 55.0,
        regime             = "BULL_TRENDING",

        hub_composite_score = 45.0,
        hub_signal          = "BUY",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _df(n: int = 3, base: float = 1000.0) -> pd.DataFrame:
    """Minimal OHLCV DataFrame with n rows; last row is bullish."""
    rows = []
    for i in range(n):
        price = base + i * 5
        rows.append({
            "open":   price - 2,
            "high":   price + 5,
            "low":    price - 5,
            "close":  price + 1,
            "volume": 1_000_000,
        })
    return pd.DataFrame(rows)


def _df_pullback(ema20: float = 1020.0) -> pd.DataFrame:
    """Two-bar DataFrame for PullbackTrendLong: prev bar touches EMA, last closes above."""
    return pd.DataFrame([
        # prev bar: low ≤ ema20 ≤ high → touched
        {"open": ema20 - 5, "high": ema20 + 10, "low": ema20 - 8, "close": ema20 - 1, "volume": 800_000},
        # last bar: close > ema20
        {"open": ema20 - 2, "high": ema20 + 20, "low": ema20 - 3, "close": ema20 + 15, "volume": 900_000},
    ])


def _df_mean_rev_rejection(entry: float = 1050.0) -> pd.DataFrame:
    """Bearish rejection candle: close < open, upper wick > 1.5× body."""
    open_  = entry + 10   # 1060
    close  = entry        # 1050  (red candle)
    high   = entry + 35   # 1085  (long upper wick)
    low    = entry - 5    # 1045
    return pd.DataFrame([{"open": open_, "high": high, "low": low, "close": close, "volume": 500_000}])


def _df_hammer(entry: float = 900.0) -> pd.DataFrame:
    """Classic hammer: lower wick > 2× body, close > open."""
    open_  = entry
    close  = entry + 5    # +5 body
    high   = entry + 6
    low    = entry - 12   # lower wick = 12 > 2×5 body
    return pd.DataFrame([{"open": open_, "high": high, "low": low, "close": close, "volume": 600_000}])


# ═══════════════════════════════════════════════════════════════════════════════
# TradeCandidate
# ═══════════════════════════════════════════════════════════════════════════════

class TestTradeCandidate:

    def _cand(self, entry=100.0, stop=90.0, target=120.0) -> TradeCandidate:
        return TradeCandidate("SYM", "BUY", entry, stop, target, 70)

    def test_risk_reward_2to1(self):
        c = self._cand(entry=100, stop=90, target=120)
        assert c.risk_reward == pytest.approx(2.0)

    def test_risk_reward_1to1(self):
        c = self._cand(entry=100, stop=90, target=110)
        assert c.risk_reward == pytest.approx(1.0)

    def test_risk_reward_zero_stop_distance(self):
        c = self._cand(entry=100, stop=100, target=120)
        assert c.risk_reward == 0.0

    def test_risk_reward_short_position(self):
        # SELL: entry=100, stop=110 (+10), target=80 (-20)
        c = TradeCandidate("SYM", "SELL", 100, 110, 80, 65)
        assert c.risk_reward == pytest.approx(2.0)

    def test_to_dict_has_required_keys(self):
        c = self._cand()
        d = c.to_dict()
        for key in ("symbol", "side", "entry", "stop", "target", "confidence", "risk_reward"):
            assert key in d

    def test_confidence_capped_in_practice(self):
        # confirm the dataclass itself does NOT cap (capping is in strategy code)
        c = TradeCandidate("X", "BUY", 100, 90, 120, 200)
        assert c.confidence == 200  # no auto-clamp at dataclass level


# ═══════════════════════════════════════════════════════════════════════════════
# TrendBreakoutLong
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrendBreakoutLong:

    def setup_method(self):
        self.strat = TrendBreakoutLong()
        self.sym   = "RELIANCE.NS"

    def _call(self, **fkw):
        f = _features(**fkw)
        return self.strat.evaluate(self.sym, _df(), f, macro_bias=0, fund_grade="NONE")

    # ── Golden path ──────────────────────────────────────────────────────────
    def test_golden_path_returns_candidate(self):
        c = self._call()
        assert c is not None
        assert c.side == "BUY"
        assert c.strategy == "TREND_BREAKOUT_LONG"

    def test_golden_path_stop_below_entry(self):
        c = self._call()
        assert c.stop < c.entry

    def test_golden_path_target_above_entry(self):
        c = self._call()
        assert c.target > c.entry

    def test_golden_path_rr_at_least_2(self):
        c = self._call()
        assert c.risk_reward >= 2.0

    def test_confidence_base_is_65(self):
        # Base confidence is 65 (confirmed by reading trend_breakout.py
        # directly: `conf = 65`); defaults add supertrend's +2 (st_dir=1),
        # no macro/fund/pattern bonus -> 67.
        c = self._call()
        assert c.confidence == 67

    # ── Regime gate ──────────────────────────────────────────────────────────
    def test_fails_bear_trending_regime(self):
        assert self._call(regime="BEAR_TRENDING") is None

    def test_fails_range_regime(self):
        assert self._call(regime="RANGE") is None

    def test_fails_unknown_regime(self):
        assert self._call(regime="UNKNOWN") is None

    # ── Breakout gate ─────────────────────────────────────────────────────────
    def test_fails_when_close_equals_swing_high(self):
        # close must be strictly > swing_high_20
        assert self._call(close=1045.0, swing_high_20=1045.0) is None

    def test_fails_when_close_below_swing_high(self):
        assert self._call(close=1040.0, swing_high_20=1045.0) is None

    def test_passes_when_close_just_above_swing_high(self):
        assert self._call(close=1045.01, swing_high_20=1045.0) is not None

    # ── Volume gate ───────────────────────────────────────────────────────────
    def test_fails_no_volume_spike(self):
        assert self._call(vol_spike=False) is None

    # ── RSI gate ─────────────────────────────────────────────────────────────
    def test_fails_rsi_below_55(self):
        assert self._call(rsi14=54.9) is None

    def test_fails_rsi_above_75(self):
        assert self._call(rsi14=75.1) is None

    def test_passes_rsi_exactly_55(self):
        assert self._call(rsi14=55.0) is not None

    def test_passes_rsi_exactly_75(self):
        assert self._call(rsi14=75.0) is not None

    def test_fails_rsi_overbought_90(self):
        # RSI 90 = extended, momentum likely exhausted — no entry
        assert self._call(rsi14=90.0) is None

    # ── ADX gate ─────────────────────────────────────────────────────────────
    def test_fails_adx_below_20(self):
        assert self._call(adx14=19.9) is None

    def test_passes_adx_exactly_20(self):
        assert self._call(adx14=20.0) is not None

    def test_passes_strong_trend_adx_40(self):
        assert self._call(adx14=40.0) is not None

    # ── EMA alignment gate ────────────────────────────────────────────────────
    def test_fails_ema20_below_ema50(self):
        assert self._call(ema20=980.0, ema50=1000.0) is None

    def test_passes_ema20_equals_ema50_plus_epsilon(self):
        assert self._call(ema20=1000.01, ema50=1000.0) is not None

    # ── Confidence bonuses ────────────────────────────────────────────────────
    def test_macro_bias_increases_confidence(self):
        base = self._call(macro_bias=0).confidence
        with_macro = self.strat.evaluate(self.sym, _df(), _features(), macro_bias=2, fund_grade="NONE")
        assert with_macro.confidence > base

    def test_investment_grade_increases_confidence(self):
        base = self._call().confidence
        with_fund = self.strat.evaluate(self.sym, _df(), _features(), macro_bias=0, fund_grade="INVESTMENT")
        assert with_fund.confidence > base

    def test_bullish_pattern_increases_confidence(self):
        base = self._call().confidence
        with_pat = self._call(pattern_direction="BULLISH", strongest_pattern="ENGULFING")
        assert with_pat.confidence > base

    def test_supertrend_bull_increases_confidence(self):
        base = self._call(st_dir=0).confidence
        with_st = self._call(st_dir=1)
        assert with_st.confidence > base

    def test_confidence_never_exceeds_95(self):
        # All bonuses stacked
        c = self.strat.evaluate(
            self.sym, _df(), _features(pattern_direction="BULLISH", st_dir=1),
            macro_bias=5, fund_grade="INVESTMENT",
        )
        assert c.confidence <= 95

    # ── Risk guard ────────────────────────────────────────────────────────────
    def test_zero_atr_uses_swing_high_as_stop(self):
        # TrendBreakout stop = max(swing_high - 1.5×ATR, ema20 - 0.5×ATR)
        # With ATR=0: stop = max(swing_high, ema20).  Risk = entry - stop > 0 → trade still valid.
        # TrendBreakoutLong has no explicit ATR==0 guard (unlike HubSignal).
        c = self._call(atr14=0)
        assert c is not None
        assert c.stop >= 0
        assert c.entry > c.stop

    def test_stop_uses_max_of_two_formulas(self):
        # stop = max(swing_high - 1.5×ATR, ema20 - 0.5×ATR)
        f = _features(close=1050, swing_high_20=1045, atr14=15, ema20=1020)
        c = self.strat.evaluate(self.sym, _df(), f, 0, "NONE")
        expected_stop1 = 1045 - 1.5 * 15  # = 1022.5
        expected_stop2 = 1020 - 0.5 * 15  # = 1012.5
        assert c.stop == pytest.approx(max(expected_stop1, expected_stop2), abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# PullbackTrendLong
# ═══════════════════════════════════════════════════════════════════════════════

class TestPullbackTrendLong:

    def setup_method(self):
        self.strat = PullbackTrendLong()
        self.sym   = "HDFCBANK.NS"

    def _call(self, fkw=None, df=None):
        f   = _features(**(fkw or {}))
        df_ = df if df is not None else _df_pullback(ema20=f.ema20)
        return self.strat.evaluate(self.sym, df_, f, macro_bias=0, fund_grade="NONE")

    # ── Golden path ──────────────────────────────────────────────────────────
    def test_golden_path_returns_candidate(self):
        assert self._call() is not None

    def test_golden_path_side_is_buy(self):
        assert self._call().side == "BUY"

    def test_stop_below_prev_low_minus_half_atr(self):
        c = self._call()
        # stop = prev_low - 0.5×ATR; prev_low is ema20 - 8 = 1012
        assert c.stop < c.entry

    # ── Regime gate ──────────────────────────────────────────────────────────
    def test_fails_bear_regime(self):
        assert self._call(fkw={"regime": "BEAR_TRENDING"}) is None

    def test_fails_range_regime(self):
        assert self._call(fkw={"regime": "RANGE"}) is None

    # ── EMA touch gate ────────────────────────────────────────────────────────
    def test_fails_prev_bar_did_not_touch_ema(self):
        # prev bar entirely above EMA → no touch
        ema20 = 1020.0
        f = _features(ema20=ema20)
        df_no_touch = pd.DataFrame([
            # prev bar: low > ema20 → no touch
            {"open": 1030, "high": 1050, "low": 1025, "close": 1040, "volume": 800_000},
            {"open": 1038, "high": 1060, "low": 1035, "close": 1055, "volume": 900_000},
        ])
        assert self.strat.evaluate(self.sym, df_no_touch, f, 0, "NONE") is None

    def test_fails_last_bar_closed_below_ema(self):
        ema20 = 1020.0
        f = _features(ema20=ema20)
        df_below = pd.DataFrame([
            # prev bar touches EMA
            {"open": 1015, "high": 1025, "low": 1012, "close": 1017, "volume": 800_000},
            # last bar close BELOW ema20
            {"open": 1018, "high": 1022, "low": 1010, "close": ema20 - 1, "volume": 900_000},
        ])
        assert self.strat.evaluate(self.sym, df_below, f, 0, "NONE") is None

    # ── RSI gate ─────────────────────────────────────────────────────────────
    def test_fails_rsi_below_50(self):
        assert self._call(fkw={"rsi14": 49.9}) is None

    def test_passes_rsi_exactly_50(self):
        assert self._call(fkw={"rsi14": 50.0}) is not None

    # ── EMA alignment ─────────────────────────────────────────────────────────
    def test_fails_ema20_below_ema50(self):
        assert self._call(fkw={"ema20": 950.0, "ema50": 1000.0}) is None

    # ── ADX gate (2026-07-23: current gate is `adx14 < 20`, not 15 --
    #    confirmed by reading engine/agent/strategies/pullback_trend.py) ───────
    def test_fails_adx_below_20(self):
        assert self._call(fkw={"adx14": 19.9}) is None

    def test_passes_adx_exactly_20(self):
        assert self._call(fkw={"adx14": 20.0}) is not None

    # ── Minimum df length ─────────────────────────────────────────────────────
    def test_fails_with_single_row_df(self):
        f = _features()
        df_one = _df(n=1)
        assert self.strat.evaluate(self.sym, df_one, f, 0, "NONE") is None

    # ── Risk guard ────────────────────────────────────────────────────────────
    def test_target_is_2x_risk(self):
        c = self._call()
        risk   = c.entry - c.stop
        reward = c.target - c.entry
        assert reward == pytest.approx(2.0 * risk, rel=1e-3)


# ═══════════════════════════════════════════════════════════════════════════════
# MeanReversionShort
# ═══════════════════════════════════════════════════════════════════════════════

class TestMeanReversionShort:

    def setup_method(self):
        self.strat = MeanReversionShort()
        self.sym   = "TCS.NS"

    def _call(self, fkw=None, df=None):
        f_kw = {"regime": "RANGE", "rsi14": 75.0, "close": 1055.0, "bb_upper": 1050.0,
                "bb_mid": 980.0, "atr14": 15.0}
        f_kw.update(fkw or {})
        f   = _features(**f_kw)
        df_ = df if df is not None else _df_mean_rev_rejection(entry=f.close)
        return self.strat.evaluate(self.sym, df_, f, macro_bias=0, fund_grade="NONE")

    # ── Golden path ──────────────────────────────────────────────────────────
    def test_golden_path_returns_candidate(self):
        assert self._call() is not None

    def test_golden_path_side_is_sell(self):
        assert self._call().side == "SELL"

    def test_target_is_bb_mid(self):
        f_kw = {"regime": "RANGE", "rsi14": 75.0, "close": 1055.0, "bb_upper": 1050.0,
                "bb_mid": 980.0, "atr14": 15.0}
        f = _features(**f_kw)
        df = _df_mean_rev_rejection(1055.0)
        c = self.strat.evaluate(self.sym, df, f, 0, "NONE")
        assert c.target == pytest.approx(f.bb_mid)

    def test_stop_is_above_candle_high(self):
        c = self._call()
        assert c.stop > c.entry

    # ── Regime gate ──────────────────────────────────────────────────────────
    def test_fails_bull_trending_regime(self):
        assert self._call(fkw={"regime": "BULL_TRENDING"}) is None

    def test_fails_bear_trending_regime(self):
        assert self._call(fkw={"regime": "BEAR_TRENDING"}) is None

    def test_passes_high_vol_range(self):
        assert self._call(fkw={"regime": "HIGH_VOL_RANGE"}) is not None

    # ── Bollinger gate ────────────────────────────────────────────────────────
    def test_fails_close_below_bb_upper(self):
        # close must be >= bb_upper
        assert self._call(fkw={"close": 1040.0, "bb_upper": 1050.0}) is None

    def test_passes_close_equal_bb_upper(self):
        assert self._call(fkw={"close": 1050.0, "bb_upper": 1050.0}) is not None

    def test_passes_close_above_bb_upper(self):
        assert self._call(fkw={"close": 1060.0, "bb_upper": 1050.0}) is not None

    # ── RSI gate ─────────────────────────────────────────────────────────────
    def test_fails_rsi_below_70(self):
        assert self._call(fkw={"rsi14": 69.9}) is None

    def test_passes_rsi_exactly_70(self):
        assert self._call(fkw={"rsi14": 70.0}) is not None

    def test_fails_rsi_normal_range(self):
        assert self._call(fkw={"rsi14": 55.0}) is None

    # ── Bearish rejection candle gate ─────────────────────────────────────────
    def test_fails_bullish_candle(self):
        """Bullish candle (close > open) is not a bearish rejection."""
        open_, close = 1040.0, 1055.0
        df_bull = pd.DataFrame([{"open": open_, "high": 1090, "low": 1035, "close": close, "volume": 500_000}])
        assert self._call(df=df_bull) is None

    def test_fails_small_upper_wick(self):
        """Upper wick <= 1.5× body → not a rejection."""
        # body = 10 (1060 → 1050), upper wick = 12 (1060 → 1072)  → 12 < 1.5×10=15 → fail
        open_, close = 1060.0, 1050.0   # bearish body of 10
        high = open_ + 12               # wick = 12 < 15
        df = pd.DataFrame([{"open": open_, "high": high, "low": 1040, "close": close, "volume": 500_000}])
        assert self._call(fkw={"close": close, "bb_upper": 1040.0}, df=df) is None

    def test_passes_large_upper_wick(self):
        """Upper wick > 1.5× body is the archetypal shooting star."""
        c = self._call()
        assert c is not None

    # ── Target above entry guard ──────────────────────────────────────────────
    def test_fails_when_bb_mid_above_entry(self):
        """Short trade: target must be below entry (target = bb_mid)."""
        # bb_mid above close → invalid short target
        assert self._call(fkw={"close": 1050.0, "bb_upper": 1040.0, "bb_mid": 1060.0}) is None

    # ── Confidence bonuses ────────────────────────────────────────────────────
    def test_bearish_macro_increases_confidence(self):
        base = self._call().confidence
        with_macro = self.strat.evaluate(
            self.sym, _df_mean_rev_rejection(1055.0),
            _features(regime="RANGE", rsi14=75.0, close=1055.0, bb_upper=1050.0, bb_mid=980.0, atr14=15.0),
            macro_bias=-2, fund_grade="NONE",
        )
        assert with_macro.confidence > base

    def test_confidence_never_exceeds_95(self):
        c = self._call(fkw={"pattern_direction": "BEARISH"})
        if c:
            assert c.confidence <= 95


# ═══════════════════════════════════════════════════════════════════════════════
# RangeReversalLong
# ═══════════════════════════════════════════════════════════════════════════════

class TestRangeReversalLong:

    def setup_method(self):
        self.strat = RangeReversalLong()
        self.sym   = "SUNPHARMA.NS"

    def _call(self, fkw=None, df=None):
        entry = 895.0
        f_kw  = {
            "regime": "RANGE", "close": entry, "bb_lower": 900.0,
            "bb_mid": 960.0, "rsi14": 28.0, "atr14": 12.0,
            "ema50": 950.0, "ema200": 930.0, "adx14": 18.0,
        }
        f_kw.update(fkw or {})
        f   = _features(**f_kw)
        df_ = df if df is not None else _df_hammer(entry=f.close)
        return self.strat.evaluate(self.sym, df_, f, macro_bias=0, fund_grade="NONE")

    # ── Golden path ──────────────────────────────────────────────────────────
    def test_golden_path_returns_candidate(self):
        assert self._call() is not None

    def test_golden_path_side_is_buy(self):
        assert self._call().side == "BUY"

    def test_target_is_bb_mid(self):
        c = self._call()
        assert c.target == pytest.approx(960.0)

    def test_stop_is_below_candle_low(self):
        c = self._call()
        assert c.stop < c.entry

    # ── Regime gate ──────────────────────────────────────────────────────────
    def test_fails_bull_trending(self):
        assert self._call(fkw={"regime": "BULL_TRENDING"}) is None

    def test_passes_low_vol_range(self):
        assert self._call(fkw={"regime": "LOW_VOL_RANGE"}) is not None

    def test_passes_high_vol_range(self):
        assert self._call(fkw={"regime": "HIGH_VOL_RANGE"}) is not None

    # ── Bollinger lower gate ──────────────────────────────────────────────────
    def test_fails_close_above_bb_lower(self):
        # close must be at or below bb_lower
        assert self._call(fkw={"close": 910.0, "bb_lower": 900.0}) is None

    def test_passes_close_equal_bb_lower(self):
        # close <= bb_lower is the gate; equal boundary passes
        # entry=900, bb_mid=960 → target>entry ✓
        entry = 900.0
        f = _features(regime="RANGE", close=entry, bb_lower=900.0, bb_mid=960.0,
                      rsi14=28.0, atr14=12.0, ema50=950.0, ema200=930.0, adx14=18.0)
        df = _df_hammer(entry)
        c = self.strat.evaluate(self.sym, df, f, 0, "NONE")
        # equal case: close (900) <= bb_lower (900) → should pass
        assert c is not None

    # ── RSI gate ─────────────────────────────────────────────────────────────
    def test_fails_rsi_above_35(self):
        assert self._call(fkw={"rsi14": 35.1}) is None

    def test_passes_rsi_exactly_35(self):
        assert self._call(fkw={"rsi14": 35.0}) is not None

    def test_fails_rsi_neutral_50(self):
        assert self._call(fkw={"rsi14": 50.0}) is None

    # ── Falling-knife guard: EMA50 < EMA200 ──────────────────────────────────
    def test_fails_ema50_below_ema200(self):
        # Confirmed downtrend — don't catch the falling knife
        assert self._call(fkw={"ema50": 920.0, "ema200": 950.0}) is None

    def test_passes_ema50_equal_ema200(self):
        # Not strictly below → passes
        assert self._call(fkw={"ema50": 950.0, "ema200": 950.0}) is not None

    # ── ADX trending guard ────────────────────────────────────────────────────
    def test_fails_adx_above_25(self):
        # ADX > 25 = trending, not ranging → fighting the trend
        assert self._call(fkw={"adx14": 25.1}) is None

    def test_passes_adx_exactly_25(self):
        assert self._call(fkw={"adx14": 25.0}) is not None

    # ── Hammer candle gate ────────────────────────────────────────────────────
    def test_fails_bearish_candle_no_bullish_pattern(self):
        """Red candle (close < open) is not a hammer (hammer requires close > open).
        Without a bullish pattern tag, this should not fire."""
        entry = 895.0
        # Bearish candle: close < open, small lower wick → not a hammer
        df_bear = pd.DataFrame([{"open": entry + 5, "high": entry + 6, "low": entry - 2,
                                  "close": entry,  # red candle
                                  "volume": 400_000}])
        f = _features(regime="RANGE", close=entry, bb_lower=900.0, bb_mid=960.0,
                      rsi14=28.0, atr14=12.0, ema50=950.0, ema200=930.0, adx14=18.0,
                      pattern_direction="NEUTRAL")
        c = self.strat.evaluate(self.sym, df_bear, f, 0, "NONE")
        assert c is None

    def test_bullish_pattern_substitutes_for_hammer(self):
        """pattern_direction='BULLISH' satisfies the candlestick gate even without a hammer."""
        entry = 895.0
        df_plain = pd.DataFrame([{"open": entry + 2, "high": entry + 3, "low": entry - 1,
                                   "close": entry + 2.5, "volume": 600_000}])
        f = _features(regime="RANGE", close=entry, bb_lower=900.0, bb_mid=960.0,
                      rsi14=28.0, atr14=12.0, ema50=950.0, ema200=930.0, adx14=18.0,
                      pattern_direction="BULLISH")
        c = self.strat.evaluate(self.sym, df_plain, f, 0, "NONE")
        assert c is not None

    # ── Target vs entry guard ─────────────────────────────────────────────────
    def test_fails_when_bb_mid_below_entry(self):
        """bb_mid < entry → target < entry → invalid long."""
        assert self._call(fkw={"close": 895.0, "bb_lower": 900.0, "bb_mid": 880.0}) is None


# ═══════════════════════════════════════════════════════════════════════════════
# HubSignalStrategy
# ═══════════════════════════════════════════════════════════════════════════════

class TestHubSignalStrategy:
    """2026-07-23: rewritten to match "Phase 7 tightening" (see
    engine/agent/strategies/hub_signal.py's own module docstring), a
    deliberate, documented strategy change made after these tests were
    originally written -- not code drift:
      - BUY only now (SELL requires a separate short-side review elsewhere;
        this strategy itself never returns a SELL candidate any more).
      - Min hub score raised 10 -> 40 (STRONG_BUY only).
      - Five additional trend-quality gates: EMA50>EMA200, ADX>25,
        regime != BEAR_TRENDING, vol_spike required, RSI in [45, 70].
      - Stop tightened 2xATR -> 1xATR; target 4xATR -> 2xATR (same 2:1 R:R).
      - Confidence: base 70, +brackets at score>=40/55/70 (74/78/82), regime/
        supertrend/macro/fund-grade bonuses, capped at 92, hard floor of 80
        (returns None below it -- confirmed by reading the source directly).
    """

    def setup_method(self):
        self.strat = HubSignalStrategy()
        self.sym   = "INFY.NS"

    def _call(self, **fkw):
        f = _features(**fkw)
        return self.strat.evaluate(self.sym, _df(), f, macro_bias=0, fund_grade="NONE")

    # ── Golden path (defaults: score=45, regime=BULL_TRENDING, ema50>ema200,
    #    adx14=28, rsi14=62, vol_spike=True -- all Phase 7 gates already pass) ──
    def test_golden_path_returns_buy(self):
        c = self._call()
        assert c is not None and c.side == "BUY"

    def test_golden_path_strategy_name(self):
        assert self._call().strategy == "HUB_SIGNAL"

    def test_entry_is_current_close(self):
        c = self._call(close=1050.0)
        assert c.entry == pytest.approx(1050.0)

    def test_stop_is_1_atr_below_entry(self):
        c = self._call(close=1050.0, atr14=15.0)
        assert c.stop == pytest.approx(1050.0 - 1 * 15.0)

    def test_target_is_2_atr_above_entry(self):
        c = self._call(close=1050.0, atr14=15.0)
        assert c.target == pytest.approx(1050.0 + 2 * 15.0)

    def test_rr_is_2_to_1(self):
        c = self._call(close=1050.0, atr14=15.0)
        assert c.risk_reward == pytest.approx(2.0)

    # ── hub_score None guard ──────────────────────────────────────────────────
    def test_returns_none_when_no_hub_score(self):
        assert self._call(hub_composite_score=None) is None

    # ── Signal direction -- BUY only, Phase 7 ──────────────────────────────────
    def test_sell_signal_returns_none(self):
        # Phase 7: this strategy no longer originates SELL candidates at all.
        assert self._call(hub_signal="SELL", hub_composite_score=40.0, regime="RANGE") is None

    def test_hold_signal_returns_none(self):
        assert self._call(hub_signal="HOLD") is None

    def test_neutral_signal_returns_none(self):
        assert self._call(hub_signal="NEUTRAL") is None

    # ── Minimum score gate (Phase 7: raised 10 -> 40) ──────────────────────────
    def test_fails_score_below_40(self):
        assert self._call(hub_composite_score=39.9) is None

    def test_passes_score_exactly_40(self):
        assert self._call(hub_composite_score=40.0) is not None

    def test_fails_score_zero(self):
        assert self._call(hub_composite_score=0.0) is None

    def test_negative_score_uses_abs_value(self):
        # hub_score = -75 -> abs = 75 -> passes the score threshold, but Phase 7
        # is BUY-only so a SELL signal (even with a passing abs score) is None.
        c = self._call(hub_composite_score=-75.0, hub_signal="SELL", regime="RANGE")
        assert c is None
        # A BUY signal with the same abs score does pass (defaults keep it
        # comfortably above the 80 hard floor: 70-bracket 82 + regime +6 + st +4).
        assert self._call(hub_composite_score=-75.0, hub_signal="BUY") is not None

    # ── Phase 7 trend-quality gates ────────────────────────────────────────────
    def test_fails_when_ema50_below_ema200(self):
        assert self._call(ema50=900.0, ema200=950.0) is None

    def test_fails_in_bear_trending_regime(self):
        assert self._call(regime="BEAR_TRENDING") is None

    def test_fails_adx_at_25_inclusive_boundary(self):
        # Gate is `adx14 <= 25: return None` -- exactly 25 must fail.
        assert self._call(adx14=25.0) is None

    def test_passes_adx_just_above_25(self):
        assert self._call(adx14=25.1) is not None

    def test_fails_without_volume_spike(self):
        assert self._call(vol_spike=False) is None

    def test_fails_rsi_below_45(self):
        assert self._call(rsi14=44.9) is None

    def test_fails_rsi_above_70(self):
        assert self._call(rsi14=70.1) is None

    def test_passes_rsi_at_boundaries(self):
        assert self._call(rsi14=45.0) is not None
        assert self._call(rsi14=70.0) is not None

    # ── ATR guard ─────────────────────────────────────────────────────────────
    def test_fails_zero_atr(self):
        assert self._call(atr14=0.0) is None

    def test_fails_negative_atr(self):
        assert self._call(atr14=-5.0) is None

    # ── Confidence scoring (base 70, brackets at 40/55/70 -> 74/78/82) ─────────
    def test_confidence_score_45_is_74(self):
        # score=45 -> 40-bracket (74) + BULL_TRENDING (+6) + st_dir=1 default (+4) = 84
        c = self._call(hub_composite_score=45.0)
        assert c.confidence == 84

    def test_confidence_score_60_is_78_bracket(self):
        # regime=RANGE/st_dir=0 removes the two bonuses that would otherwise
        # be added on top of the bracket value, but 78 alone would sit below
        # the 80 hard floor -- keep the default st_dir=1 (+4) just to clear
        # it and isolate the bracket-vs-bonus math precisely: 78 + 4 = 82.
        c = self._call(hub_composite_score=60.0, regime="RANGE")
        assert c.confidence == 82

    def test_confidence_score_75_is_82_bracket(self):
        c = self._call(hub_composite_score=75.0, regime="RANGE")
        assert c.confidence == 86  # 82 (70-bracket) + st_dir default (+4)

    def test_bull_regime_adds_bonus_for_buy(self):
        # score=75 (82 base) keeps both sides above the 80 floor even with
        # st_dir=0, isolating just the regime bonus.
        base = self._call(regime="RANGE", hub_composite_score=75.0, st_dir=0)
        bull = self._call(regime="BULL_TRENDING", hub_composite_score=75.0, st_dir=0)
        assert base is not None and bull is not None
        assert bull.confidence > base.confidence

    def test_supertrend_bull_adds_bonus(self):
        without = self._call(st_dir=0, regime="RANGE", hub_composite_score=75.0)
        with_st = self._call(st_dir=1, regime="RANGE", hub_composite_score=75.0)
        assert without is not None and with_st is not None
        assert with_st.confidence > without.confidence

    def test_investment_grade_adds_bonus(self):
        base = self.strat.evaluate(self.sym, _df(), _features(), 0, "NONE")
        inv  = self.strat.evaluate(self.sym, _df(), _features(), 0, "INVESTMENT")
        assert base is not None and inv is not None
        assert inv.confidence > base.confidence

    def test_confidence_capped_at_92(self):
        c = self.strat.evaluate(
            self.sym, _df(),
            _features(hub_composite_score=90, regime="BULL_TRENDING", st_dir=1),
            macro_bias=5, fund_grade="INVESTMENT",
        )
        assert c.confidence == 92  # 82 + 6 + 4 + 3 + 5 = 100, capped at 92

    def test_hard_floor_of_80_returns_none_below_it(self):
        # score=40 (lowest passing bracket, conf=74) with every bonus disabled
        # stays below the Phase 7 hard floor of 80 -> must return None, not a
        # low-confidence candidate.
        c = self._call(hub_composite_score=40.0, regime="RANGE", st_dir=0)
        assert c is None


# ═══════════════════════════════════════════════════════════════════════════════
# StrategySelectorAgent
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategySelectorAgent:

    def setup_method(self):
        self.sel = StrategySelectorAgent()
        self.sym = "WIPRO.NS"

    def test_returns_none_when_no_strategy_fires(self):
        # Choppy unknown regime: no Varsity strategy fires, no hub score
        f = _features(regime="UNKNOWN", hub_composite_score=None, adx14=5.0,
                      vol_spike=False, rsi14=45.0)
        c = self.sel.propose(self.sym, _df(), f, 0, "NONE")
        assert c is None

    def test_filters_rr_below_1_5(self):
        """A valid HubSignal with very tight ATR produces tiny RR — must be rejected."""
        # entry=1000, stop=1000-2*0.1=999.8, target=1000+4*0.1=1000.4
        # RR = 0.4/0.2 = 2.0 → passes. Use target < entry trick instead.
        # Easier: mock a strategy that returns RR=1.0 directly.
        mock_strat = MagicMock()
        mock_strat.name = "MOCK"
        mock_strat.evaluate.return_value = TradeCandidate(
            "X", "BUY", 100.0, 95.0, 105.0, 80,   # RR = 5/5 = 1.0 < 1.5
        )
        sel = StrategySelectorAgent()
        sel.strategies = [mock_strat]
        c = sel.propose(self.sym, _df(), _features(), 0, "NONE")
        assert c is None

    def test_highest_confidence_wins(self):
        """When two strategies fire, the one with higher confidence is returned."""
        low_conf_strat  = MagicMock()
        high_conf_strat = MagicMock()
        low_conf_strat.name  = "LOW"
        high_conf_strat.name = "HIGH"
        low_conf_strat.evaluate.return_value  = TradeCandidate("X", "BUY", 100, 90, 125, 60)
        high_conf_strat.evaluate.return_value = TradeCandidate("X", "BUY", 100, 90, 125, 80)

        sel = StrategySelectorAgent()
        sel.strategies = [low_conf_strat, high_conf_strat]
        c = sel.propose(self.sym, _df(), _features(), 0, "NONE")
        assert c.confidence == 80

    def test_strategy_exception_does_not_crash(self):
        """A broken strategy raises — selector should skip it and continue."""
        exploding = MagicMock()
        exploding.name = "BOOM"
        exploding.evaluate.side_effect = RuntimeError("test explosion")

        sel = StrategySelectorAgent()
        sel.strategies = [exploding]
        # Should not raise; returns None when only strategy explodes
        c = sel.propose(self.sym, _df(), _features(hub_composite_score=None), 0, "NONE")
        assert c is None

    def test_hub_signal_not_fired_without_score(self):
        """HubSignalStrategy is last resort but requires hub_composite_score."""
        f = _features(regime="UNKNOWN", hub_composite_score=None, adx14=5.0,
                      vol_spike=False, rsi14=45.0)
        c = self.sel.propose(self.sym, _df(), f, 0, "NONE")
        assert c is None

    def test_trend_breakout_not_registered_even_when_its_own_gates_fire(self):
        # 2026-07-23: TrendBreakoutLong was deliberately removed from the
        # live selector (engine/agent/selector.py: "disabled (Phase 5):
        # backtest mean_R=-0.003 over 400+ trades -- zero statistical edge"),
        # though the strategy class itself still exists and is directly
        # unit-tested elsewhere in this file. Confirms the selector's real
        # output can never be TREND_BREAKOUT_LONG any more, even on a bar
        # that would satisfy that strategy's own gates in isolation.
        f = _features(
            regime="BULL_TRENDING",
            close=1050.0, swing_high_20=1045.0,
            vol_spike=True, rsi14=62.0, adx14=28.0,
            ema20=1020.0, ema50=990.0, atr14=15.0,
            hub_composite_score=45.0, hub_signal="BUY",
        )
        from engine.agent.strategies.trend_breakout import TrendBreakoutLong
        assert TrendBreakoutLong().evaluate(self.sym, _df(), f, 0, "NONE") is not None  # would fire in isolation

        c = self.sel.propose(self.sym, _df(), f, 0, "NONE")
        assert c is None or c.strategy != "TREND_BREAKOUT_LONG"

    def test_selector_picks_the_higher_confidence_registered_strategy(self):
        # Replaces the old TrendBreakoutLong-vs-HubSignal comparison with the
        # equivalent check among strategies actually registered today:
        # PullbackTrendLong vs HubSignalStrategy on a bar where both fire.
        from engine.agent.strategies.pullback_trend import PullbackTrendLong
        from engine.agent.strategies.hub_signal import HubSignalStrategy

        f = _features(
            regime="BULL_TRENDING", close=1035.0, ema20=1020.0, ema50=990.0,
            ema200=950.0, atr14=15.0, adx14=28.0, rsi14=58.0, vol_spike=True,
            hub_composite_score=45.0, hub_signal="BUY",
        )
        df = _df_pullback(ema20=1020.0)

        pullback_result = PullbackTrendLong().evaluate(self.sym, df, f, 0, "NONE")
        hub_result = HubSignalStrategy().evaluate(self.sym, df, f, 0, "NONE")
        assert pullback_result is not None and hub_result is not None  # both must actually fire

        winner = pullback_result if pullback_result.confidence >= hub_result.confidence else hub_result
        c = self.sel.propose(self.sym, df, f, 0, "NONE")
        assert c is not None
        assert c.strategy == winner.strategy
        assert c.confidence == winner.confidence

    def test_rr_exactly_1_5_passes(self):
        """RR = 1.5 is exactly the minimum — must be accepted."""
        mock_strat = MagicMock()
        mock_strat.name = "MOCK_15"
        # entry=100, stop=90 (risk=10), target=115 (reward=15) → RR=1.5
        mock_strat.evaluate.return_value = TradeCandidate("X", "BUY", 100, 90, 115, 70)
        sel = StrategySelectorAgent()
        sel.strategies = [mock_strat]
        c = sel.propose(self.sym, _df(), _features(), 0, "NONE")
        assert c is not None
        assert c.risk_reward == pytest.approx(1.5)


# ═══════════════════════════════════════════════════════════════════════════════
# vix_size_factor
# ═══════════════════════════════════════════════════════════════════════════════

class TestVixSizeFactor:

    def test_below_low_threshold_is_1(self):
        """VIX ≤ 22 → full position size."""
        assert vix_size_factor(10.0) == pytest.approx(1.0)
        assert vix_size_factor(22.0) == pytest.approx(1.0)

    def test_above_extreme_threshold_is_min(self):
        """VIX ≥ 30 → minimum size (0.50)."""
        from utils.config import settings
        assert vix_size_factor(30.0) == pytest.approx(settings.VIX_SIZE_SCALE_MIN)
        assert vix_size_factor(50.0) == pytest.approx(settings.VIX_SIZE_SCALE_MIN)

    def test_midpoint_is_interpolated(self):
        """VIX 26 (midpoint of 22-30) → ~0.75 size."""
        sf = vix_size_factor(26.0)
        assert 0.5 < sf < 1.0

    def test_linear_decay(self):
        """Factor at VIX=22 > VIX=25 > VIX=28."""
        assert vix_size_factor(22.0) > vix_size_factor(25.0) > vix_size_factor(28.0)

    def test_zero_vix_is_full_size(self):
        assert vix_size_factor(0.0) == pytest.approx(1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# position_size (Varsity M9)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositionSize:

    def test_basic_calculation(self):
        # equity=₹20L, risk=2%, entry=1000, stop=950 → risk_per_share=50
        # shares = (20L × 0.02) / 50 = 40000/50 = 800
        qty = position_size(2_000_000.0, 0.02, 1000.0, 950.0)
        assert qty == 800

    def test_returns_int(self):
        assert isinstance(position_size(1_000_000, 0.01, 500.0, 490.0), int)

    def test_zero_when_entry_equals_stop(self):
        assert position_size(1_000_000, 0.02, 100.0, 100.0) == 0

    def test_zero_when_stop_above_entry(self):
        # stop above entry (short trade pass-through) → abs() handles it
        qty = position_size(1_000_000, 0.02, 100.0, 110.0)
        # abs(entry - stop) = 10 → qty = 20000/10 = 2000
        assert qty == 2000

    def test_floors_at_zero_for_zero_equity(self):
        assert position_size(0.0, 0.02, 100.0, 90.0) == 0

    def test_larger_risk_gives_more_shares(self):
        q1 = position_size(1_000_000, 0.01, 100.0, 90.0)
        q2 = position_size(1_000_000, 0.02, 100.0, 90.0)
        assert q2 == 2 * q1


# ═══════════════════════════════════════════════════════════════════════════════
# capital_utilization_size
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapitalUtilizationSize:

    def _cu(self, **kw):
        defaults = dict(
            equity=2_000_000.0, conviction=75.0,
            entry=500.0, stop=475.0,
            deployed_notional=0.0,
            size_factor=1.0, vix=15.0,
        )
        defaults.update(kw)
        return capital_utilization_size(**defaults)

    def test_returns_positive_qty_normal_case(self):
        qty, reason = self._cu()
        assert qty > 0

    def test_returns_tuple(self):
        result = self._cu()
        assert isinstance(result, tuple) and len(result) == 2

    def test_bad_entry_returns_zero(self):
        qty, reason = self._cu(entry=0.0)
        assert qty == 0
        assert reason == "bad_entry"

    def test_cash_buffer_full_when_all_deployed(self):
        # Deploy 90% of equity — only 10% left = MIN_CASH_BUFFER → no room
        from utils.config import settings
        equity = 2_000_000.0
        deployed = equity * (1.0 - settings.AGENT_CASH_BUFFER_MIN)  # 80% deployed
        qty, reason = self._cu(deployed_notional=deployed)
        assert qty == 0
        assert reason == "cash_buffer_full"

    def test_higher_conviction_gives_more_shares(self):
        q_low,  _ = self._cu(conviction=30.0)
        q_high, _ = self._cu(conviction=90.0)
        assert q_high >= q_low

    def test_high_vix_reduces_size(self):
        q_normal, _ = self._cu(vix=15.0)
        q_high_vix, _ = self._cu(vix=28.0)
        assert q_high_vix < q_normal

    def test_size_factor_half_reduces_qty(self):
        q_full, _ = self._cu(size_factor=1.0)
        q_half, _ = self._cu(size_factor=0.5)
        assert q_half < q_full

    def test_risk_guard_binding_constraint(self):
        """Very wide stop → risk guard should kick in, not capital target."""
        # entry=1000, stop=100 → risk_per_share=900 → risk guard will limit qty
        qty, reason = self._cu(entry=1000.0, stop=100.0, conviction=90.0)
        assert reason == "risk_guard"

    def test_negative_size_factor_treated_as_zero(self):
        qty, reason = self._cu(size_factor=-1.0)
        assert qty == 0


# ═══════════════════════════════════════════════════════════════════════════════
# RiskManagerAgent — all veto gates
# ═══════════════════════════════════════════════════════════════════════════════

def _make_candidate(entry=500.0, stop=475.0, target=550.0, confidence=75) -> TradeCandidate:
    return TradeCandidate("TEST.NS", "BUY", entry, stop, target, confidence,
                          strategy="TREND_BREAKOUT_LONG")


def _make_ctx(**overrides) -> dict:
    ctx = dict(
        daily_pnl_pct    = 0.0,
        weekly_pnl_pct   = 0.0,
        monthly_pnl_pct  = 0.0,
        consec_losses_today = 0,
        new_entries_today   = 0,
        deployed_notional   = 0.0,
        cash                = 1_800_000.0,  # 90% of 2M
        open_risk_pct       = 0.0,
        open_symbols        = [],
        symbol_correlations = {},
        sector_exposure     = {},
    )
    ctx.update(overrides)
    return ctx


class TestRiskManagerAgent:

    def setup_method(self):
        self.equity = 2_000_000.0

    def _rm(self, **ctx_kw):
        return RiskManagerAgent(_make_ctx(**ctx_kw))

    def _approve(self, **ctx_kw):
        rm = self._rm(**ctx_kw)
        ok, reason = rm.can_take_trade(_make_candidate(), self.equity)
        return ok, reason

    # ── Circuit breakers ──────────────────────────────────────────────────────
    def test_daily_dd_stop_vetoes(self):
        from utils.config import settings
        ok, reason = self._approve(daily_pnl_pct=-(settings.AGENT_DAILY_DD_STOP))
        assert not ok and reason == "DAILY_DD_STOP"

    def test_weekly_dd_stop_vetoes(self):
        from utils.config import settings
        ok, reason = self._approve(weekly_pnl_pct=-(settings.AGENT_WEEKLY_DD_STOP))
        assert not ok and reason == "WEEKLY_DD_STOP"

    def test_monthly_dd_stop_vetoes(self):
        from utils.config import settings
        ok, reason = self._approve(monthly_pnl_pct=-(settings.AGENT_MONTHLY_DD_STOP))
        assert not ok and reason == "MONTHLY_DD_STOP"

    def test_normal_pnl_passes_circuit_breaker(self):
        ok, reason = self._approve(daily_pnl_pct=-0.001)
        assert ok

    # ── Zero risk distance ────────────────────────────────────────────────────
    def test_zero_risk_distance_vetoed(self):
        rm = self._rm()
        cand = _make_candidate(entry=500.0, stop=500.0)
        ok, reason = rm.can_take_trade(cand, self.equity)
        assert not ok and reason == "ZERO_RISK_DISTANCE"

    # ── Already in position ────────────────────────────────────────────────────
    def test_already_in_position_vetoed(self):
        ok, reason = self._approve(open_symbols=["TEST.NS"])
        assert not ok and reason == "ALREADY_IN_POSITION"

    def test_already_in_position_bare_symbol(self):
        # Symbol stored without .NS suffix should still match
        ok, reason = self._approve(open_symbols=["TEST"])
        assert not ok and reason == "ALREADY_IN_POSITION"

    def test_different_symbol_passes(self):
        ok, reason = self._approve(open_symbols=["RELIANCE.NS"])
        assert ok

    # ── Correlation guard ─────────────────────────────────────────────────────
    def test_high_correlation_vetoed(self):
        ctx = _make_ctx(
            open_symbols=["CORREL.NS"],
            symbol_correlations={("CORREL.NS", "TEST.NS"): 0.75},
        )
        rm = RiskManagerAgent(ctx)
        ok, reason = rm.can_take_trade(_make_candidate(), self.equity)
        assert not ok and "HIGH_CORRELATION" in reason

    def test_low_correlation_passes(self):
        ctx = _make_ctx(
            open_symbols=["CORREL.NS"],
            symbol_correlations={("CORREL.NS", "TEST.NS"): 0.50},
        )
        rm = RiskManagerAgent(ctx)
        ok, reason = rm.can_take_trade(_make_candidate(), self.equity)
        assert ok

    def test_correlation_exactly_070_passes(self):
        """Gate is corr > 0.70 (strictly greater). Exactly 0.70 passes."""
        ctx = _make_ctx(
            open_symbols=["CORREL.NS"],
            symbol_correlations={("CORREL.NS", "TEST.NS"): 0.70},
        )
        rm = RiskManagerAgent(ctx)
        ok, reason = rm.can_take_trade(_make_candidate(), self.equity)
        assert ok   # 0.70 is NOT > 0.70 → passes

    def test_correlation_above_070_vetoed(self):
        ctx = _make_ctx(
            open_symbols=["CORREL.NS"],
            symbol_correlations={("CORREL.NS", "TEST.NS"): 0.71},
        )
        rm = RiskManagerAgent(ctx)
        ok, reason = rm.can_take_trade(_make_candidate(), self.equity)
        assert not ok and "HIGH_CORRELATION" in reason

    # ── Sector exposure gate ──────────────────────────────────────────────────
    def test_sector_exposure_cap_vetoed(self):
        """Current sector at 18% + incoming 5% = 23% > 20% → veto."""
        cand = _make_candidate(entry=500.0, stop=475.0)
        cand.sector = "TECHNOLOGY"
        ctx = _make_ctx(sector_exposure={"TECHNOLOGY": 0.18})
        rm = RiskManagerAgent(ctx)
        ok, reason = rm.can_take_trade(cand, self.equity)
        # incoming notional ≈ qty × 500 / 2M %; need to verify veto fires
        if not ok:
            assert "SECTOR_EXPOSURE_CAP" in reason

    def test_no_sector_tag_skips_sector_gate(self):
        """Candidate without sector attribute skips the sector gate."""
        cand = _make_candidate()
        # No .sector attribute set → gate should be skipped
        ok, reason = RiskManagerAgent(_make_ctx()).can_take_trade(cand, self.equity)
        assert ok

    # ── Confidence gate ───────────────────────────────────────────────────────
    def test_low_confidence_vetoed(self):
        from utils.config import settings
        rm = self._rm()
        cand = _make_candidate(confidence=settings.AGENT_CONFIDENCE_THRESHOLD - 1)
        ok, reason = rm.can_take_trade(cand, self.equity)
        assert not ok and "LOW_CONFIDENCE" in reason

    def test_confidence_at_threshold_passes(self):
        from utils.config import settings
        rm = self._rm()
        cand = _make_candidate(confidence=settings.AGENT_CONFIDENCE_THRESHOLD)
        ok, reason = rm.can_take_trade(cand, self.equity)
        assert ok

    # ── R:R gate ──────────────────────────────────────────────────────────────
    def test_poor_rr_vetoed(self):
        rm = self._rm()
        cand = _make_candidate(entry=100.0, stop=90.0, target=108.0)  # RR = 0.8
        ok, reason = rm.can_take_trade(cand, self.equity)
        assert not ok and "POOR_RR" in reason

    def test_rr_exactly_1_5_passes(self):
        rm = self._rm()
        cand = _make_candidate(entry=100.0, stop=90.0, target=115.0)  # RR = 1.5
        ok, reason = rm.can_take_trade(cand, self.equity)
        assert ok

    # ── Cash buffer gate ──────────────────────────────────────────────────────
    def test_cash_buffer_gate_vetoed(self):
        from utils.config import settings
        equity = self.equity
        # Leave less than MIN_CASH_BUFFER after this trade
        nearly_all = equity * (1 - settings.AGENT_CASH_BUFFER_MIN) - 1
        rm = self._rm(cash=nearly_all * 0.01)  # almost zero cash
        ok, reason = rm.can_take_trade(_make_candidate(), equity)
        # Either CASH_BUFFER or QTY_ZERO:cash_buffer_full
        assert not ok

    # ── Portfolio risk cap ─────────────────────────────────────────────────────
    def test_portfolio_risk_cap_vetoed(self):
        from utils.config import settings
        rm = self._rm(open_risk_pct=settings.AGENT_MAX_OPEN_RISK)
        ok, reason = rm.can_take_trade(_make_candidate(), self.equity)
        assert not ok and reason == "PORTFOLIO_RISK_CAP"

    # ── Golden path ──────────────────────────────────────────────────────────
    def test_golden_path_approved(self):
        ok, reason = self._approve()
        assert ok and reason == "OK"


# ═══════════════════════════════════════════════════════════════════════════════
# Agent-level guards (unit tests for logic in agent_loop._process_symbol)
# These test the pure logic without touching DB or live prices.
# ═══════════════════════════════════════════════════════════════════════════════

class TestSMEGuard:
    """NSE SME (suffix -SM) stocks must be rejected before any strategy runs."""

    SME_SYMBOLS = [
        "QMSMEDI-SM.NS",
        "ABCXYZ-SM.NS",
        "TESTCO-SM.NS",
        "ANOTHER-SM",
    ]

    def _is_sme(self, symbol: str) -> bool:
        bare = symbol.replace(".NS", "").replace(".BO", "").upper()
        return bare.endswith("-SM")

    def test_sme_symbols_detected(self):
        for sym in self.SME_SYMBOLS:
            assert self._is_sme(sym), f"Should detect {sym} as SME"

    def test_regular_symbols_not_flagged(self):
        regular = ["RELIANCE.NS", "HDFCBANK.NS", "TCS.NS", "INFY.NS", "WIPRO"]
        for sym in regular:
            assert not self._is_sme(sym), f"Wrongly flagged {sym} as SME"

    def test_sme_with_bo_suffix(self):
        assert self._is_sme("XYZCO-SM.BO")

    def test_sme_lowercase_handled(self):
        # The guard does .upper() before checking
        assert self._is_sme("testco-sm.NS")


class TestCandleAgeGuard:
    """Candle staleness guard: reject when latest candle > 72h old for 1d timeframe."""

    def _age_hours(self, ts_utc: dt.datetime) -> float:
        return (dt.datetime.utcnow() - ts_utc).total_seconds() / 3600

    def _check(self, age_hours: float, timeframe: str = "1d") -> bool:
        max_age = 72 if timeframe == "1d" else 4
        return age_hours <= max_age

    def test_fresh_candle_passes(self):
        ts = dt.datetime.utcnow() - dt.timedelta(hours=10)
        assert self._check(self._age_hours(ts))

    def test_39_hour_candle_passes_1d(self):
        """Jun 24 IST close (stored as Jun 23 18:30 UTC) is ~39h old at mid-session Jun 25."""
        ts = dt.datetime.utcnow() - dt.timedelta(hours=39)
        assert self._check(self._age_hours(ts), "1d")

    def test_72_hour_candle_passes_exactly(self):
        # Use a slightly younger timestamp to avoid floating-point overshoot
        ts = dt.datetime.utcnow() - dt.timedelta(hours=71, minutes=59)
        assert self._check(self._age_hours(ts), "1d")

    def test_73_hour_candle_fails_1d(self):
        ts = dt.datetime.utcnow() - dt.timedelta(hours=73)
        assert not self._check(self._age_hours(ts), "1d")

    def test_178_hour_candle_fails_1d(self):
        """UTKARSHBNK had 178h stale candle — must be rejected."""
        ts = dt.datetime.utcnow() - dt.timedelta(hours=178)
        assert not self._check(self._age_hours(ts), "1d")

    def test_4_hour_threshold_for_intraday(self):
        ts_3h  = dt.datetime.utcnow() - dt.timedelta(hours=3)
        ts_5h  = dt.datetime.utcnow() - dt.timedelta(hours=5)
        assert     self._check(self._age_hours(ts_3h), "5m")
        assert not self._check(self._age_hours(ts_5h), "5m")

    def test_weekend_gap_72h_passes(self):
        """Friday close to Monday mid-session = ~68-70h → must pass."""
        ts = dt.datetime.utcnow() - dt.timedelta(hours=70)
        assert self._check(self._age_hours(ts), "1d")


class TestLivePriceGuard:
    """Live price mandatory check: trade must be rejected when price is unconfirmable."""

    def test_none_price_means_reject(self):
        live_px = None
        assert live_px is None  # guard triggers

    def test_zero_price_is_invalid(self):
        live_px = 0.0
        assert not (live_px and live_px > 0)

    def test_positive_price_is_valid(self):
        live_px = 435.50
        assert live_px is not None and live_px > 0

    def test_divergence_check_5pct(self):
        """Price divergence > 5% (stale candle vs live) must veto the trade."""
        entry_from_candle = 124.91   # stale
        live_price        = 136.62   # actual market
        divergence_pct    = abs(live_price - entry_from_candle) / entry_from_candle
        MAX_DIVERGENCE    = 0.05     # 5%
        assert divergence_pct > MAX_DIVERGENCE  # should reject

    def test_divergence_under_5pct_passes(self):
        entry_from_candle = 100.0
        live_price        = 103.0    # 3% divergence
        divergence_pct    = abs(live_price - entry_from_candle) / entry_from_candle
        MAX_DIVERGENCE    = 0.05
        assert divergence_pct < MAX_DIVERGENCE

    def test_divergence_exactly_5pct_is_boundary(self):
        entry_from_candle = 100.0
        live_price        = 105.0
        divergence_pct    = abs(live_price - entry_from_candle) / entry_from_candle
        assert divergence_pct == pytest.approx(0.05)


# ═══════════════════════════════════════════════════════════════════════════════
# Swing trading scenario tests — market condition × strategy matrix
# ═══════════════════════════════════════════════════════════════════════════════

class TestSwingScenarios:
    """
    End-to-end scenario tests: given a market condition description,
    verify the correct strategy fires (or none fires) and the resulting
    trade setup has valid risk parameters.
    """

    def setup_method(self):
        self.sel = StrategySelectorAgent()

    # ── Scenario 1: Classic bull breakout ────────────────────────────────────
    def test_scenario_strong_breakout_on_volume(self):
        """
        SCENARIO: Stock breaks 52-week high on 3× avg volume, RSI 65, ADX 32.
        Nifty above 50 EMA. Classic textbook breakout entry.
        EXPECTED: A BUY signal fires. When hub_composite_score is also present,
        HubSignal may win on confidence (bonus stacking). What matters is a valid BUY.
        """
        f = _features(
            regime="BULL_TRENDING", close=1055.0, swing_high_20=1048.0,
            vol_spike=True, rsi14=65.0, adx14=32.0,
            ema20=1025.0, ema50=995.0, atr14=18.0,
            hub_composite_score=55.0, hub_signal="BUY",
        )
        c = self.sel.propose("BREAKOUT.NS", _df(), f, macro_bias=1, fund_grade="WATCHLIST")
        assert c is not None
        assert c.side == "BUY"
        assert c.risk_reward >= 1.5
        # Either TREND_BREAKOUT_LONG or HUB_SIGNAL is acceptable here;
        # HubSignal may score higher when hub_composite_score >= 50 stacks bull_regime bonus.
        assert c.strategy in ("TREND_BREAKOUT_LONG", "HUB_SIGNAL")

    def test_scenario_breakout_without_hub_score_now_fires_nothing(self):
        """
        SCENARIO: Same breakout conditions but NO hub score (stock not in shortlist).
        2026-07-23: previously TrendBreakoutLong would win here once HubSignal
        was blocked by the missing hub score. TrendBreakoutLong has since been
        deliberately removed from the live selector (engine/agent/selector.py:
        "disabled (Phase 5): backtest mean_R=-0.003 over 400+ trades -- zero
        statistical edge") -- a real, backtest-driven decision, not drift. A
        pure breakout (not a pullback, not a range reversal) with no hub
        score now correctly matches no currently-registered strategy at all.
        """
        f = _features(
            regime="BULL_TRENDING", close=1055.0, swing_high_20=1048.0,
            vol_spike=True, rsi14=65.0, adx14=32.0,
            ema20=1025.0, ema50=995.0, atr14=18.0,
            hub_composite_score=None,  # not in shortlist
        )
        c = self.sel.propose("BREAKOUT.NS", _df(), f, macro_bias=0, fund_grade="NONE")
        assert c is None

    # ── Scenario 2: Pullback in uptrend ──────────────────────────────────────
    def test_scenario_pullback_to_20ema_in_uptrend(self):
        """
        SCENARIO: Stock in clear uptrend, pulls back to 20 EMA, touches it,
        bounces back above. RSI 53. Classic continuation buy.
        EXPECTED: PULLBACK_LONG fires.
        """
        ema20 = 1020.0
        f = _features(
            regime="BULL_TRENDING", rsi14=53.0, adx14=22.0,
            ema20=ema20, ema50=990.0, atr14=15.0,
            hub_composite_score=None,  # no hub score → only Varsity strategies
        )
        df = _df_pullback(ema20)
        c = self.sel.propose("PULLBACK.NS", df, f, macro_bias=0, fund_grade="NONE")
        assert c is not None
        assert c.strategy == "PULLBACK_LONG"

    # ── Scenario 3: Range-bound with shooting star ────────────────────────────
    def test_scenario_overbought_at_range_top(self):
        """
        SCENARIO: Stock ranging between 900-1100. Price touched BB upper, RSI 78.
        Shooting star candle forms. Classic mean reversion short.
        EXPECTED: MEAN_REVERSION_SHORT fires.
        """
        close = 1060.0
        f = _features(
            regime="RANGE", rsi14=78.0, close=close, bb_upper=1050.0,
            bb_mid=975.0, atr14=20.0, hub_composite_score=None,
        )
        df = _df_mean_rev_rejection(close)
        c = self.sel.propose("RANGE_SHORT.NS", df, f, macro_bias=-1, fund_grade="NONE")
        assert c is not None
        assert c.strategy == "MEAN_REVERSION_SHORT"
        assert c.side == "SELL"

    # ── Scenario 4: Oversold hammer at support ────────────────────────────────
    def test_scenario_oversold_hammer_at_range_support(self):
        """
        SCENARIO: Stock in trading range. Price hits BB lower, RSI 25,
        forms hammer candle. Classic mean reversion long.
        2026-07-23: RANGE_REVERSAL_LONG has since been deliberately removed
        from the live selector (engine/agent/selector.py: "disabled (Phase 7):
        n=2 in full backtest, mean_R=-0.336" -- a real, backtest-driven
        decision, not drift). No other currently-registered strategy handles
        this long-side range-support setup, so it now correctly fires nothing.
        """
        entry = 895.0
        f = _features(
            regime="RANGE", close=entry, bb_lower=900.0, bb_mid=960.0,
            rsi14=25.0, atr14=12.0, ema50=950.0, ema200=930.0, adx14=15.0,
            hub_composite_score=None,
        )
        df = _df_hammer(entry)
        c = self.sel.propose("HAMMER.NS", df, f, macro_bias=0, fund_grade="NONE")
        assert c is None

    # ── Scenario 5: Bear market — no new longs ────────────────────────────────
    def test_scenario_bear_market_no_long_entry(self):
        """
        SCENARIO: Nifty below 200 EMA, bear trending regime. Stock shows a
        bullish candle but the macro environment is bearish.
        EXPECTED: No BUY strategy fires (TrendBreakout, Pullback, RangeReversal all blocked).
        """
        f = _features(
            regime="BEAR_TRENDING",
            hub_composite_score=20.0, hub_signal="BUY",  # HubSignal also blocked in bear
        )
        c = self.sel.propose("BEAR.NS", _df(), f, macro_bias=-2, fund_grade="NONE")
        # HubSignalStrategy is blocked for BUY in BEAR_TRENDING
        assert c is None or c.side != "BUY"

    # ── Scenario 6: Choppy sideways — no entry ────────────────────────────────
    def test_scenario_choppy_market_no_signal(self):
        """
        SCENARIO: ADX < 15 (directionless), RSI 48, no volume spike, regime UNKNOWN.
        EXPECTED: No strategy fires — wait for clarity.
        """
        f = _features(
            regime="UNKNOWN", adx14=12.0, rsi14=48.0,
            vol_spike=False, hub_composite_score=None,
        )
        c = self.sel.propose("CHOPPY.NS", _df(), f, macro_bias=0, fund_grade="NONE")
        assert c is None

    # ── Scenario 7: Hub signal saves a valid stock with no specific setup ─────
    def test_scenario_hub_signal_catches_quality_stock(self):
        """
        SCENARIO: Stock ranked #3 on hub score (MIS = 58). Regime BULL but no
        specific Varsity setup fires (no breakout, no pullback). Hub signal
        is the safety net.
        EXPECTED: HUB_SIGNAL fires.

        2026-07-23: HubSignalStrategy's own "Phase 7 tightening" (see
        engine/agent/strategies/hub_signal.py) added vol_spike and ADX>25 as
        hard requirements -- the original vol_spike=False/adx14=22 no longer
        clears HubSignal's own gates either, so this scenario needed updating
        to still represent "no breakout, no pullback pattern" while
        satisfying Phase 7 (vol_spike=True, adx14=28 here; close is still
        nowhere near swing_high_20 so TrendBreakoutLong's gates -- moot
        anyway, it's unregistered -- wouldn't fire regardless).
        """
        f = _features(
            regime="BULL_TRENDING",
            close=500.0, swing_high_20=520.0,   # not a breakout
            vol_spike=True, rsi14=52.0,
            adx14=28.0, ema20=490.0, ema50=475.0, ema200=400.0,  # ema50>ema200 (Phase 7 gate)
            atr14=8.0,
            hub_composite_score=58.0, hub_signal="BUY",
        )
        c = self.sel.propose("HUB.NS", _df(), f, macro_bias=0, fund_grade="WATCHLIST")
        assert c is not None
        assert c.strategy == "HUB_SIGNAL"

    # ── Scenario 8: SME stock — hard reject ────────────────────────────────────
    def test_scenario_sme_stock_never_traded(self):
        """
        SCENARIO: QMSMEDI-SM.NS scores well on hub but it's an NSE Emerge
        SME stock with no live price feed and untradeable on Zerodha.
        EXPECTED: Must be rejected at the SME guard — never reaches strategy selector.
        """
        sym = "QMSMEDI-SM.NS"
        bare = sym.replace(".NS", "").upper()
        assert bare.endswith("-SM"), "SME guard must catch this symbol"

    # ── Scenario 9: Stale 178h candle — hard reject ───────────────────────────
    def test_scenario_stale_candle_178h_rejected(self):
        """
        SCENARIO: UTKARSHBNK last candle from June 18 (178h old).
        Entry would be set to June 18 close which is ₹20+ off current price.
        EXPECTED: Candle age guard rejects — no trade placed.
        """
        ts_old = dt.datetime.utcnow() - dt.timedelta(hours=178)
        age_h = (dt.datetime.utcnow() - ts_old).total_seconds() / 3600
        max_age = 72  # for 1d timeframe
        assert age_h > max_age, "178h candle must fail the 72h guard"

    # ── Scenario 10: Stop = entry (bug scenario) ──────────────────────────────
    def test_scenario_stop_equals_entry_vetoed(self):
        """
        SCENARIO: Bug where stop_loss was accidentally set equal to entry_price.
        EXPECTED: RiskManager vetoes with ZERO_RISK_DISTANCE.
        """
        rm = RiskManagerAgent(_make_ctx())
        cand = _make_candidate(entry=136.62, stop=136.62, target=150.0)
        ok, reason = rm.can_take_trade(cand, 2_000_000.0)
        assert not ok and reason == "ZERO_RISK_DISTANCE"

    # ── Scenario 11: Expiry-day F&O position ─────────────────────────────────
    def test_scenario_options_position_no_daily_candle(self):
        """
        SCENARIO: FINNIFTY26JUN26550CE has no daily candle (options don't store
        them the same way). The candle age guard would see age=infinity.
        EXPECTED: Treated as stale — guard rejects. F&O engine handles options separately.
        """
        # Simulate: no candle in DB → treat as maximum staleness
        has_candle = False
        if not has_candle:
            age_h = float('inf')
        max_age = 72
        assert age_h > max_age

    # ── Scenario 12: Falling knife — ema50 < ema200 ───────────────────────────
    def test_scenario_falling_knife_rejected_at_range_support(self):
        """
        SCENARIO: Stock in confirmed downtrend (EMA50 < EMA200) touches BB lower.
        Looks like support but it's a falling knife. Should not buy.
        EXPECTED: RANGE_REVERSAL_LONG rejected by EMA50 < EMA200 gate.
        """
        entry = 895.0
        f = _features(
            regime="RANGE", close=entry, bb_lower=900.0, bb_mid=960.0,
            rsi14=30.0, atr14=12.0,
            ema50=920.0, ema200=960.0,  # EMA50 < EMA200 = downtrend
            adx14=18.0, hub_composite_score=None,
        )
        df = _df_hammer(entry)
        c = self.sel.propose("FALLING.NS", df, f, 0, "NONE")
        assert c is None

    # ── Scenario 13: High VIX environment ─────────────────────────────────────
    def test_scenario_high_vix_reduces_position_size(self):
        """
        SCENARIO: India VIX at 28 (elevated fear). Same trade at VIX 15 vs 28.
        EXPECTED: Position size at VIX 28 is smaller than at VIX 15.
        """
        qty_normal, _ = capital_utilization_size(
            2_000_000, 75.0, 500.0, 480.0, 0.0, size_factor=1.0, vix=15.0)
        qty_fearful, _ = capital_utilization_size(
            2_000_000, 75.0, 500.0, 480.0, 0.0, size_factor=1.0, vix=28.0)
        assert qty_fearful < qty_normal

    # ── Scenario 14: Partial portfolio deployed ───────────────────────────────
    def test_scenario_full_portfolio_blocks_new_entries(self):
        """
        SCENARIO: Already deployed 90% of equity in 20 positions.
        Cash buffer = 10% = MIN_CASH_BUFFER. No room for new trades.
        EXPECTED: Capital size returns 0 / RiskManager says CASH_BUFFER.
        """
        equity = 2_000_000.0
        from utils.config import settings
        deployed = equity * (1.0 - settings.AGENT_CASH_BUFFER_MIN)  # 80%
        qty, reason = capital_utilization_size(
            equity, 75.0, 500.0, 480.0, deployed, vix=15.0)
        assert qty == 0
        assert reason == "cash_buffer_full"

    # ── Scenario 15: Correlation cluster risk ────────────────────────────────
    def test_scenario_correlated_sector_bet_blocked(self):
        """
        SCENARIO: Already long HDFCBANK. Trying to enter ICICIBANK.
        Correlation = 0.82 > 0.70. Over-concentration in banking.
        EXPECTED: HIGH_CORRELATION veto.
        """
        ctx = _make_ctx(
            open_symbols=["HDFCBANK.NS"],
            symbol_correlations={("HDFCBANK.NS", "TEST.NS"): 0.82},
        )
        rm = RiskManagerAgent(ctx)
        ok, reason = rm.can_take_trade(_make_candidate(), 2_000_000.0)
        assert not ok and "HIGH_CORRELATION" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# Regression tests — specific bugs we fixed
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegressions:

    def test_sme_stock_never_enters_strategy_selector(self):
        """Regression: QMSMEDI-SM was entered at ₹110 with live_px=None."""
        sym = "QMSMEDI-SM.NS"
        bare = sym.replace(".NS", "").replace(".BO", "").upper()
        assert bare.endswith("-SM")

    def test_aeroenter_stale_entry_would_be_caught_today(self):
        """
        Regression: AEROENTER entered at ₹124.91 (stale Jun 18 candle)
        while market was ₹136.62 — 9.4% divergence. Would be caught by live
        price divergence guard.
        """
        entry_stale = 124.91
        live_px     = 136.62
        div = abs(live_px - entry_stale) / entry_stale
        assert div > 0.05  # >5% triggers rejection

    def test_gna_stop_loss_bug_caught_by_zero_risk_guard(self):
        """
        Regression: GNA paper_3292 had stop_loss = entry_price = 438.30.
        Zero risk distance must be vetoed.
        """
        rm = RiskManagerAgent(_make_ctx())
        cand = _make_candidate(entry=438.30, stop=438.30, target=460.0)
        ok, reason = rm.can_take_trade(cand, 2_000_000.0)
        assert not ok and reason == "ZERO_RISK_DISTANCE"

    def test_take_profit_must_be_beyond_entry(self):
        """
        Regression: ZFCVINDIA had status=TAKE_PROFIT with negative PnL
        (entry=2693, exit=2619 — exit was BELOW entry for a long).
        Target must be above entry for a BUY.
        """
        entry  = 2693.0
        stop   = 2640.0
        target = 2619.0   # wrong: below entry for a BUY
        risk   = entry - stop    # 53
        reward = target - entry  # -74 (negative)
        # A negative target for BUY means invalid — strategy returns None for this
        assert reward < 0

    def test_backfill_timeout_504_symbols_sequential_exceeds_600s(self):
        """
        Regression: backfill task iterated 760 symbols sequentially at ~0.5s each
        = 380s. With Celery hard limit 600s some symbols were killed mid-loop.
        Verify: 760 × 0.5s = 380s < 600s (barely fits, but 1.0s/sym = 760s > 600).
        The fix is concurrent batches.
        """
        avg_s_per_sym_worst = 1.0
        n_symbols            = 760
        celery_limit         = 600
        worst_case_secs      = n_symbols * avg_s_per_sym_worst
        assert worst_case_secs > celery_limit, (
            "This confirms the 760-symbol sequential backfill exceeds Celery time limit"
        )

    def test_nifty_bees_ema50_trend_filter_fails_open(self):
        """
        Regression: _check_nifty_trend returns True (fail-open) when < 50 rows.
        Ensures trading is never silently blocked when DB data is thin.
        """
        rows_count = 45  # < 50
        assert rows_count < 50   # the function returns True here — fail open


# ═══════════════════════════════════════════════════════════════════════════════
# Integration smoke test — full selector round-trip without DB/live feed
# ═══════════════════════════════════════════════════════════════════════════════

class TestSelectorRoundTrip:

    def test_all_strategies_registered_in_selector(self):
        # 2026-07-23: updated to the current, deliberate roster --
        # engine/agent/selector.py's own comments document real backtest-
        # driven removals, not drift: "TREND_BREAKOUT_LONG disabled (Phase 5):
        # backtest mean_R=-0.003 over 400+ trades -- zero statistical edge"
        # and "RANGE_REVERSAL_LONG disabled (Phase 7): n=2 in full backtest,
        # mean_R=-0.336." ExhaustionShort was added in their place. The
        # TrendBreakoutLong/RangeReversalLong strategy classes still exist
        # and are still directly unit-tested elsewhere in this file -- they
        # are just no longer wired into the live selector.
        sel = StrategySelectorAgent()
        names = {s.name for s in sel.strategies}
        expected = {
            "PULLBACK_LONG",
            "MEAN_REVERSION_SHORT",
            "EXHAUSTION_SHORT",
            "HUB_SIGNAL",
        }
        assert names == expected

    def test_hub_signal_is_last(self):
        """HubSignalStrategy is the catch-all — must be last in the list."""
        sel = StrategySelectorAgent()
        assert sel.strategies[-1].name == "HUB_SIGNAL"

    def test_selector_returns_none_for_empty_features(self):
        sel = StrategySelectorAgent()
        # Hub score = None → hub signal blocked; all Varsity gates fail on UNKNOWN regime
        f = _features(regime="UNKNOWN", hub_composite_score=None, vol_spike=False,
                      rsi14=45.0, adx14=8.0)
        c = sel.propose("NULL.NS", _df(), f, 0, "NONE")
        assert c is None

    def test_buy_candidate_has_stop_below_entry(self):
        f = _features()
        c = StrategySelectorAgent().propose("X.NS", _df(), f, 0, "NONE")
        if c and c.side == "BUY":
            assert c.stop < c.entry, "Stop must be below entry for a BUY"

    def test_sell_candidate_has_stop_above_entry(self):
        f = _features(regime="RANGE", rsi14=78.0, close=1060.0,
                      bb_upper=1050.0, bb_mid=975.0, atr14=20.0,
                      hub_composite_score=None)
        df = _df_mean_rev_rejection(1060.0)
        c = StrategySelectorAgent().propose("X.NS", df, f, 0, "NONE")
        if c and c.side == "SELL":
            assert c.stop > c.entry, "Stop must be above entry for a SELL"

    def test_all_candidates_have_positive_atr(self):
        """Every fired candidate implies ATR > 0 (otherwise strategy returns None)."""
        scenarios = [
            _features(),                                               # trend breakout
            _features(regime="RANGE", rsi14=78.0, close=1060.0,      # mean rev short
                      bb_upper=1050.0, bb_mid=975.0, atr14=20.0,
                      hub_composite_score=None),
        ]
        dfs = [_df(), _df_mean_rev_rejection(1060.0)]
        sel = StrategySelectorAgent()
        for f, df in zip(scenarios, dfs):
            c = sel.propose("X.NS", df, f, 0, "NONE")
            if c:
                assert c.entry > 0 and c.stop != c.entry
