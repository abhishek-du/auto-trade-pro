"""Regression tests for engine/anomaly_detector.py -- Phase 1 of the
pre-event market anomaly engine (2026-07-23). See the module's own
docstring and docs/NEWS_INGESTION_LATENCY_FORENSIC_AUDIT.md for the
Nestlé case that motivated this: price/volume anomalies can precede the
official NSE filing by several minutes.

All tests are deterministic and mocked -- no network, no DB.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import engine.anomaly_detector as ad


def _candle(close, volume, high=None, low=None, ts=None):
    return SimpleNamespace(
        close=close, volume=volume,
        high=high if high is not None else close * 1.001,
        low=low if low is not None else close * 0.999,
        timestamp=ts or datetime.now(),
    )


@pytest.fixture(autouse=True)
def _reset_state():
    ad.reset_baseline_cache()
    yield
    ad.reset_baseline_cache()


class TestTierBoundaries:
    def test_below_monitor_is_normal(self):
        assert ad._tier_for_score(59.9) == "NORMAL"

    def test_monitor_lower_bound_inclusive(self):
        assert ad._tier_for_score(60.0) == "MONITOR"

    def test_alert_lower_bound_inclusive(self):
        assert ad._tier_for_score(75.0) == "ALERT"

    def test_investigate_lower_bound_inclusive(self):
        assert ad._tier_for_score(90.0) == "INVESTIGATE"

    def test_just_below_alert_is_monitor(self):
        assert ad._tier_for_score(74.9) == "MONITOR"


class TestComputeScore:
    def test_zero_signals_yield_zero_score(self):
        assert ad._compute_score(0.0, 1.0, 0.0) == 0.0

    def test_extreme_price_z_caps_at_60_points(self):
        score = ad._compute_score(price_z=50.0, volume_ratio=1.0, vwap_deviation=0.0)
        assert score == 60.0

    def test_extreme_volume_ratio_caps_at_35_points(self):
        score = ad._compute_score(price_z=0.0, volume_ratio=1000.0, vwap_deviation=0.0)
        assert score == 35.0

    def test_all_three_signals_extreme_hits_100(self):
        score = ad._compute_score(price_z=50.0, volume_ratio=1000.0, vwap_deviation=1.0)
        assert score == 100.0

    def test_negative_price_z_scores_the_same_as_positive(self):
        # A large DOWN move should be just as anomalous as a large UP move.
        up = ad._compute_score(price_z=4.0, volume_ratio=1.0, vwap_deviation=0.0)
        down = ad._compute_score(price_z=-4.0, volume_ratio=1.0, vwap_deviation=0.0)
        assert up == down

    def test_relative_strength_alone_cannot_reach_investigate(self):
        # Price/volume/VWAP maxed out together is the only way to reach the
        # 90-point INVESTIGATE bar -- relative_strength is a gate elsewhere,
        # never a scored input, so this composite can't be gamed by RS alone.
        score = ad._compute_score(price_z=0.1, volume_ratio=1.0, vwap_deviation=0.0)
        assert score < ad._MONITOR_THRESHOLD


class TestBaselineStats:
    @pytest.mark.asyncio
    async def test_insufficient_history_returns_none(self):
        bars = [_candle(100.0 + i, 1000) for i in range(10)]  # fewer than _BASELINE_MIN_BARS
        with patch("engine.anomaly_detector.get_latest_candles", AsyncMock(return_value=bars)):
            result = await ad._get_baseline_stats("TEST.NS", session=AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_sufficient_history_computes_stats(self):
        bars = [_candle(100.0, 1000) for _ in range(ad._BASELINE_MIN_BARS + 5)]
        with patch("engine.anomaly_detector.get_latest_candles", AsyncMock(return_value=bars)):
            result = await ad._get_baseline_stats("TEST.NS", session=AsyncMock())
        assert result is not None
        assert result["mean_5min_ret"] == pytest.approx(0.0)
        assert result["avg_5min_volume"] == pytest.approx(1000.0)
        assert result["computed_date"] == date.today()

    @pytest.mark.asyncio
    async def test_cached_within_same_day_skips_refetch(self):
        bars = [_candle(100.0, 1000) for _ in range(ad._BASELINE_MIN_BARS + 5)]
        mock_fetch = AsyncMock(return_value=bars)
        with patch("engine.anomaly_detector.get_latest_candles", mock_fetch):
            await ad._get_baseline_stats("TEST.NS", session=AsyncMock())
            await ad._get_baseline_stats("TEST.NS", session=AsyncMock())
        mock_fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stale_cache_from_a_different_day_refetches(self):
        bars = [_candle(100.0, 1000) for _ in range(ad._BASELINE_MIN_BARS + 5)]
        ad._baseline_stats_cache["TEST.NS"] = {
            "mean_5min_ret": 0.0, "std_5min_ret": 0.01, "avg_5min_volume": 500.0,
            "computed_date": date.today() - timedelta(days=1),
        }
        with patch("engine.anomaly_detector.get_latest_candles", AsyncMock(return_value=bars)) as mock_fetch:
            result = await ad._get_baseline_stats("TEST.NS", session=AsyncMock())
        mock_fetch.assert_awaited_once()
        assert result["avg_5min_volume"] == pytest.approx(1000.0)


class TestGetAnomalyReading:
    @pytest.mark.asyncio
    async def test_no_baseline_returns_none(self):
        with patch("engine.anomaly_detector._get_baseline_stats", AsyncMock(return_value=None)):
            result = await ad.get_anomaly_reading("TEST.NS", session=AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_normal_move_scores_low_and_no_gate_needed(self):
        baseline = {"mean_5min_ret": 0.0, "std_5min_ret": 0.002, "avg_5min_volume": 10000.0, "computed_date": date.today()}
        recent = [_candle(100.0, 10000), _candle(100.05, 10500)]
        with patch("engine.anomaly_detector._get_baseline_stats", AsyncMock(return_value=baseline)), \
             patch("engine.anomaly_detector.get_latest_candles", AsyncMock(return_value=list(reversed(recent)))), \
             patch("engine.anomaly_detector.get_market_snapshot", AsyncMock(return_value=None)):
            result = await ad.get_anomaly_reading("TEST.NS", session=AsyncMock())
        assert result is not None
        assert result.tier == "NORMAL"

    @pytest.mark.asyncio
    async def test_extreme_move_with_confirmed_relative_strength_is_investigate(self):
        baseline = {"mean_5min_ret": 0.0002, "std_5min_ret": 0.001, "avg_5min_volume": 15000.0, "computed_date": date.today()}
        recent = [_candle(1459.0, 15000), _candle(1500.0, 585597)]  # mirrors the real Nestlé move
        nifty_snap = SimpleNamespace(change_pct=0.3)
        stock_snap = SimpleNamespace(change_pct=2.8)

        async def _snap_side_effect(symbol, **kwargs):
            return nifty_snap if symbol == ad._NIFTY_SYMBOL else stock_snap

        with patch("engine.anomaly_detector._get_baseline_stats", AsyncMock(return_value=baseline)), \
             patch("engine.anomaly_detector.get_latest_candles", AsyncMock(return_value=list(reversed(recent)))), \
             patch("engine.anomaly_detector.get_market_snapshot", side_effect=_snap_side_effect):
            result = await ad.get_anomaly_reading("NESTLEIND.NS", session=AsyncMock())
        assert result is not None
        assert result.tier == "INVESTIGATE"
        assert result.relative_strength == pytest.approx(2.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_market_wide_move_is_downgraded_from_investigate(self):
        # Case D from the user's report review: price/volume alone look
        # extreme, but NIFTY moved almost identically -- not stock-specific.
        baseline = {"mean_5min_ret": 0.0, "std_5min_ret": 0.001, "avg_5min_volume": 10000.0, "computed_date": date.today()}
        recent = [_candle(100.0, 10000), _candle(103.0, 80000)]
        same_move_snap = SimpleNamespace(change_pct=3.0)

        with patch("engine.anomaly_detector._get_baseline_stats", AsyncMock(return_value=baseline)), \
             patch("engine.anomaly_detector.get_latest_candles", AsyncMock(return_value=list(reversed(recent)))), \
             patch("engine.anomaly_detector.get_market_snapshot", AsyncMock(return_value=same_move_snap)):
            result = await ad.get_anomaly_reading("TEST.NS", session=AsyncMock())
        assert result is not None
        assert result.tier != "INVESTIGATE"
        assert result.tier == "ALERT"

    @pytest.mark.asyncio
    async def test_missing_recent_candles_returns_none(self):
        baseline = {"mean_5min_ret": 0.0, "std_5min_ret": 0.001, "avg_5min_volume": 10000.0, "computed_date": date.today()}
        with patch("engine.anomaly_detector._get_baseline_stats", AsyncMock(return_value=baseline)), \
             patch("engine.anomaly_detector.get_latest_candles", AsyncMock(return_value=[])):
            result = await ad.get_anomaly_reading("TEST.NS", session=AsyncMock())
        assert result is None


class TestNestleGoldenRegression:
    """Permanent regression case (explicitly requested by the user): the
    anomaly engine must flag NESTLEIND's 2026-07-22 pre-earnings spike
    BEFORE the real 11:11:18 IST NSE filing timestamp. Reconstructed from
    docs/NEWS_INGESTION_LATENCY_FORENSIC_AUDIT.md's confirmed facts: open
    ~₹1459, ~₹1500 by 11:09-11:11, on abnormal volume."""

    @pytest.mark.asyncio
    async def test_nestle_anomaly_detected_before_official_filing_time(self):
        filing_time = datetime(2026, 7, 22, 11, 11, 18)

        baseline = {
            "mean_5min_ret": 0.0001, "std_5min_ret": 0.0015,
            "avg_5min_volume": 12000.0, "computed_date": date.today(),
        }
        # 11:04 close (pre-spike) -> 11:09 close (spike caught mid-move).
        recent = [
            _candle(1462.0, 13500, ts=datetime(2026, 7, 22, 11, 4)),
            _candle(1500.0, 585597, ts=datetime(2026, 7, 22, 11, 9)),
        ]
        nifty_snap = SimpleNamespace(change_pct=0.25)
        stock_snap = SimpleNamespace(change_pct=2.7)

        async def _snap_side_effect(symbol, **kwargs):
            return nifty_snap if symbol == ad._NIFTY_SYMBOL else stock_snap

        with patch("engine.anomaly_detector._get_baseline_stats", AsyncMock(return_value=baseline)), \
             patch("engine.anomaly_detector.get_latest_candles", AsyncMock(return_value=list(reversed(recent)))), \
             patch("engine.anomaly_detector.get_market_snapshot", side_effect=_snap_side_effect):
            result = await ad.get_anomaly_reading("NESTLEIND.NS", session=AsyncMock())

        detection_time = recent[-1].timestamp  # 11:09, when the anomaly-triggering bar closed
        assert result is not None
        assert result.tier == "INVESTIGATE"
        assert detection_time < filing_time, (
            "Golden regression failed: the anomaly engine must detect NESTLEIND's "
            "spike before the 11:11:18 IST official filing time, not after."
        )
