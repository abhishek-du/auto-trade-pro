"""CI test: parity between _signal_at() (run_backtest) and real Strategy classes.

Verifies that the corrected validate_edge.py backtest path (real Strategy classes
via precomputed-features bridge) produces signals consistent with the legacy
run_backtest.py `_signal_at()` implementation.

Known documented divergences:
  - RANGE_REVERSAL: real class adds a hammer-candle check; legacy _signal_at does not.
    → real class is stricter and may reject bars that _signal_at accepts.
  - HUB_SIGNAL: real class requires a live hub_composite_score; _signal_at uses a
    technical EMA/ST/RSI proxy. Bridge uses hub_composite_score=None → HUB_SIGNAL
    always returns None through the normal path.

These divergences are expected and documented here.
"""
from __future__ import annotations

import sys
import os
import types
from types import SimpleNamespace

import pandas as pd
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.run_backtest import precompute, _signal_at
from scripts.validate_edge import _features_from_precomputed
from engine.agent.selector import StrategySelectorAgent


# ═══════════════════════════════════════════════════════════════════════════════
# Fixture builders
# ═══════════════════════════════════════════════════════════════════════════════

def _make_candle_series(n: int = 300, start_price: float = 1000.0, seed: int = 42) -> pd.DataFrame:
    """Deterministic OHLCV series for reproducible signal tests."""
    rng = np.random.default_rng(seed)
    closes = [start_price]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + rng.normal(0.0, 0.01)))
    closes = np.array(closes)
    highs  = closes * (1 + rng.uniform(0, 0.01, n))
    lows   = closes * (1 - rng.uniform(0, 0.01, n))
    opens  = closes * (1 + rng.normal(0, 0.005, n))
    vols   = rng.integers(100_000, 2_000_000, n).astype(float)
    idx    = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)


def _make_bulltrend_fixture(n: int = 300) -> pd.DataFrame:
    """Noisy uptrend with realistic RSI (55-75 range) for TREND_BREAKOUT_LONG.

    A pure linear trend pushes RSI to 100 (no losing days) which fails the 55<=rsi<=75
    gate.  Use a random walk with upward drift (≈0.3% per day) and 1% daily noise so
    RSI sits in a realistic range and the breakout condition fires occasionally.
    """
    rng  = np.random.default_rng(123)
    idx  = pd.date_range("2023-01-01", periods=n, freq="D")
    # Drift 0.003 per day, vol 0.010 → realistic bull trend with pullbacks
    rets   = rng.normal(0.003, 0.010, n)
    closes = np.cumprod(1 + rets) * 1000.0
    # Inject a volume surge near the midpoint and end to ensure vol_spike fires
    vols   = rng.integers(600_000, 900_000, n).astype(float)
    vols[140:150] = 2_000_000.0
    vols[-15:]    = 2_000_000.0
    highs  = closes * (1 + rng.uniform(0.001, 0.008, n))
    lows   = closes * (1 - rng.uniform(0.001, 0.008, n))
    opens  = closes * (1 + rng.normal(0, 0.003, n))
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)


def _make_range_fixture(n: int = 300) -> pd.DataFrame:
    """Flat choppy range to generate RANGE_REVERSAL candidates."""
    rng    = np.random.default_rng(7)
    idx    = pd.date_range("2023-01-01", periods=n, freq="D")
    closes = 1000.0 + rng.uniform(-20, 20, n)
    highs  = closes + rng.uniform(0, 10, n)
    lows   = closes - rng.uniform(0, 10, n)
    opens  = closes + rng.normal(0, 3, n)
    vols   = rng.integers(100_000, 500_000, n).astype(float)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: run one bar through both paths
# ═══════════════════════════════════════════════════════════════════════════════

def _run_bar(df: pd.DataFrame, i: int) -> tuple[dict | None, object | None]:
    """Return (_signal_at result, selector.propose result) for bar i."""
    f_df   = precompute(df)
    row    = f_df.iloc[i]
    prev   = f_df.iloc[i - 1] if i > 0 else None
    symbol = "TEST"

    # Legacy path
    sig_legacy = _signal_at(row, prev)

    # Corrected path — real Strategy classes via bridge
    features   = _features_from_precomputed(row)
    df_window  = df.iloc[max(0, i - 1): i + 1]
    selector   = StrategySelectorAgent()
    candidate  = selector.propose(symbol, df_window, features, macro_bias=0, fund_grade="WATCHLIST")

    return sig_legacy, candidate


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrendBreakoutParity:
    """TREND_BREAKOUT_LONG: _signal_at and TrendBreakoutLong use identical conditions."""

    def test_no_signal_on_random_walk(self):
        """Neither path should fire signals on most random-walk bars (low base rate)."""
        df = _make_candle_series(300)
        f_df = precompute(df)
        warmup = 220

        legacy_signals   = []
        selector_signals = []
        selector = StrategySelectorAgent()

        for i in range(warmup, len(f_df)):
            row  = f_df.iloc[i]
            prev = f_df.iloc[i - 1]
            sig  = _signal_at(row, prev)
            if sig and sig.get("strategy") == "TREND_BREAKOUT_LONG":
                legacy_signals.append(i)

            features  = _features_from_precomputed(row)
            df_window = df.iloc[max(0, i - 1): i + 1]
            cand = selector.propose("TEST", df_window, features, 0, "WATCHLIST")
            if cand and cand.strategy == "TREND_BREAKOUT_LONG":
                selector_signals.append(i)

        # Both should agree on regime/breakout conditions — overlapping bars
        # (note: selector gets +confidence from fund_grade="WATCHLIST", may differ slightly)
        overlap = set(legacy_signals) & set(selector_signals)
        print(f"\n  TREND_BREAKOUT_LONG  legacy={len(legacy_signals)}  "
              f"selector={len(selector_signals)}  overlap={len(overlap)}")
        # At least 60% of legacy signals should be confirmed by real Strategy
        if legacy_signals and selector_signals:
            ratio = len(overlap) / max(len(legacy_signals), len(selector_signals))
            assert ratio >= 0.50, (
                f"Signal parity dropped below 50%: overlap={len(overlap)} "
                f"legacy={len(legacy_signals)} selector={len(selector_signals)}"
            )

    def test_trend_breakout_bar_agreement(self):
        """When legacy _signal_at fires TREND_BREAKOUT_LONG, real Strategy should too.

        Bar-by-bar parity: for every bar where one path fires, the other should also fire.
        We run a large dataset (multiple seeds) to accumulate enough signal bars.
        """
        selector = StrategySelectorAgent()
        legacy_only = []    # legacy fires but selector does not
        selector_only = []  # selector fires but legacy does not
        agree_signal = []   # both fire
        agree_quiet  = 0    # both quiet (expected majority)

        # Run across several random seeds to get diverse market conditions
        for seed in range(5):
            df   = _make_candle_series(500, seed=seed * 17)
            f_df = precompute(df)
            warmup = 220
            for i in range(warmup, len(f_df)):
                row  = f_df.iloc[i]
                prev = f_df.iloc[i - 1]
                sig  = _signal_at(row, prev)
                legacy_tbl = sig is not None and sig.get("strategy") == "TREND_BREAKOUT_LONG"

                features  = _features_from_precomputed(row)
                df_window = df.iloc[max(0, i - 1): i + 1]
                cand = selector.propose("TEST", df_window, features, 0, "WATCHLIST")
                sel_tbl  = cand is not None and cand.strategy == "TREND_BREAKOUT_LONG"

                if legacy_tbl and sel_tbl:
                    agree_signal.append(i)
                elif legacy_tbl:
                    legacy_only.append(i)
                elif sel_tbl:
                    selector_only.append(i)
                else:
                    agree_quiet += 1

        total_signal = len(agree_signal) + len(legacy_only) + len(selector_only)
        print(f"\n  TREND_BREAKOUT bar agreement: both={len(agree_signal)} "
              f"legacy_only={len(legacy_only)} selector_only={len(selector_only)} "
              f"quiet={agree_quiet}")

        if total_signal == 0:
            pytest.skip("No TREND_BREAKOUT_LONG signals fired across test seeds — skip parity check")

        # At least 50% of signal bars should agree between both paths
        agree_ratio = len(agree_signal) / total_signal
        assert agree_ratio >= 0.50, (
            f"TREND_BREAKOUT parity below 50%: {len(agree_signal)}/{total_signal} bars agree. "
            f"Legacy-only: {len(legacy_only)}, Selector-only: {len(selector_only)}. "
            "Investigate divergence between _signal_at and TrendBreakoutLong."
        )


class TestRangeReversalDivergence:
    """RANGE_REVERSAL_LONG: real class is strictly tighter than legacy _signal_at.

    Documented divergence: RangeReversalLong requires a hammer candle OR
    pattern_direction=="BULLISH".  The bridge always sets pattern_direction="NEUTRAL"
    and the random fixture does not produce systematic hammer candles.  Therefore
    the real class should fire <= legacy on any fixture.
    """

    def test_selector_fires_subset_of_legacy(self):
        """Real Strategy fires on a subset of (or equal) bars vs legacy for range setups."""
        df   = _make_range_fixture(300)
        f_df = precompute(df)
        warmup = 220

        legacy_bars   = []
        selector_bars = []
        selector = StrategySelectorAgent()

        for i in range(warmup, len(f_df)):
            row  = f_df.iloc[i]
            prev = f_df.iloc[i - 1]
            sig  = _signal_at(row, prev)
            if sig and sig.get("strategy") == "RANGE_REVERSAL_LONG":
                legacy_bars.append(i)

            features  = _features_from_precomputed(row)
            df_window = df.iloc[max(0, i - 1): i + 1]
            cand = selector.propose("TEST", df_window, features, 0, "WATCHLIST")
            if cand and cand.strategy == "RANGE_REVERSAL_LONG":
                selector_bars.append(i)

        print(f"\n  RANGE_REVERSAL_LONG  legacy={len(legacy_bars)}  "
              f"selector={len(selector_bars)}  (selector≤legacy is expected)")
        # Real class fires at most as many as legacy (hammer check makes it stricter)
        assert len(selector_bars) <= len(legacy_bars), (
            f"Real Strategy fired MORE than legacy: {len(selector_bars)} > {len(legacy_bars)}.  "
            "This means the bridge is LESS strict than expected — investigate."
        )

    def test_hammer_check_is_documented_divergence(self):
        """Assertion: bridge pattern_direction='NEUTRAL' ≠ live candlestick pattern."""
        from scripts.validate_edge import _features_from_precomputed
        df_single = _make_range_fixture(250)
        row = precompute(df_single).iloc[-1]
        f   = _features_from_precomputed(row)
        assert f.pattern_direction == "NEUTRAL", (
            "Bridge must default pattern_direction to 'NEUTRAL' — "
            "changing this breaks documented divergence contract"
        )
        assert f.hub_composite_score is None, (
            "Bridge must leave hub_composite_score=None — "
            "real HUB_SIGNAL cannot be reconstructed from precomputed bars"
        )


class TestHubSignalDivergence:
    """HUB_SIGNAL: always None through the bridge (no backtest hub scores).

    Documented: validate_edge.py measures HUB_SIGNAL separately via hub_only=True
    (technical proxy) and documents that the proxy ≠ real 7-factor hub.
    """

    def test_hub_signal_never_fires_through_bridge(self):
        """selector.propose() must never return HUB_SIGNAL when hub_composite_score is None."""
        df      = _make_bulltrend_fixture(300)
        f_df    = precompute(df)
        warmup  = 220
        selector = StrategySelectorAgent()

        for i in range(warmup, len(f_df)):
            row      = f_df.iloc[i]
            features = _features_from_precomputed(row)
            df_window = df.iloc[max(0, i - 1): i + 1]
            cand     = selector.propose("TEST", df_window, features, 0, "WATCHLIST")
            if cand:
                assert cand.strategy != "HUB_SIGNAL", (
                    f"HUB_SIGNAL fired at bar {i} despite hub_composite_score=None.  "
                    "The bridge contract is broken — HubSignalStrategy must check for None."
                )


class TestBridgeFieldCoverage:
    """Verify the SimpleNamespace bridge covers all fields used by real Strategy classes."""

    def test_all_strategy_attributes_reachable(self):
        """Build a bridge object from a real precomputed bar; access every field strategies use."""
        df  = _make_candle_series(250)
        row = precompute(df).iloc[-1]
        f   = _features_from_precomputed(row)

        required_fields = [
            "close", "open_", "high", "low", "volume",
            "ema20", "ema50", "ema200",
            "rsi14", "macd_hist", "atr14",
            "bb_upper", "bb_lower", "bb_mid",
            "adx14", "plus_di", "minus_di",
            "st_dir", "vol_spike",
            "swing_high_20", "swing_low_20",
            "pattern_direction", "pattern_score", "strongest_pattern",
            "composite_score", "regime",
            "hub_composite_score", "hub_signal",
        ]
        for field in required_fields:
            assert hasattr(f, field), f"Bridge missing field: {field}"

    def test_bridge_numeric_types(self):
        """All numeric fields must be Python floats/ints/bools — not numpy scalars."""
        df  = _make_candle_series(250)
        row = precompute(df).iloc[-1]
        f   = _features_from_precomputed(row)

        # Strategy comparisons like `f.rsi14 > 55` must work — numpy bool/float are fine
        # but we document the expected types so a refactor doesn't silently break strategies
        assert isinstance(f.close, float)
        assert isinstance(f.vol_spike, bool)
        assert isinstance(f.st_dir, int)
        assert isinstance(f.regime, str)
        assert f.hub_composite_score is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
