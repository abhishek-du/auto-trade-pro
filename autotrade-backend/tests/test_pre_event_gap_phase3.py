"""Phase 3 tests: point-in-time snapshot, expectation gap, price discount,
relative strength. Fully mocked; emphasis on look-ahead safety (the `before=`
cutoff is always applied) and honest anchor handling (no fabricated consensus).
"""
from __future__ import annotations

from datetime import datetime, date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from engine.pre_event_expectation_gap.point_in_time import PointInTimeSnapshot, build_snapshot, NIFTY_SYMBOL
from engine.pre_event_expectation_gap.expectation import compute_expectation
from engine.pre_event_expectation_gap.price_discount import (
    analyze_price_discount, _classify, _distance_from_high, _abnormal_volume,
)
from engine.pre_event_expectation_gap.relative_strength import (
    compute_relative_strength, _window_return,
)
from engine.pre_event_expectation_gap.types import (
    NowcastResult, NowcastStatus, PriceDiscountStatus,
)


def _c(close, high=None, vol=1000):
    return SimpleNamespace(close=close, high=high if high is not None else close * 1.01,
                           low=close * 0.99, open=close, volume=vol, timestamp=datetime.now())


def _uptrend(n=90, start=130.0, step=0.5):
    # newest-first: index 0 = latest (highest), older = lower → an uptrend
    return [_c(start - i * step) for i in range(n)]


def _flat(n=90, price=100.0):
    return [_c(price) for _ in range(n)]


# ── Point-in-time snapshot ───────────────────────────────────────────────────

class TestSnapshot:
    @pytest.mark.asyncio
    async def test_before_cutoff_always_passed(self):
        cutoff = datetime(2026, 10, 1)
        snap = build_snapshot("MARUTI.NS", cutoff, AsyncMock())
        mock = AsyncMock(return_value=_flat(10))
        with patch("crawler.price_feed.get_latest_candles", mock):
            await snap.self_candles(limit=10)
        assert mock.await_args.kwargs["before"] == cutoff

    @pytest.mark.asyncio
    async def test_reads_are_cached(self):
        snap = build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())
        mock = AsyncMock(return_value=_flat(10))
        with patch("crawler.price_feed.get_latest_candles", mock):
            await snap.self_candles(limit=10)
            await snap.self_candles(limit=10)   # same key → cached
        mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nifty_uses_nifty_symbol(self):
        snap = build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())
        mock = AsyncMock(return_value=_flat(10))
        with patch("crawler.price_feed.get_latest_candles", mock):
            await snap.nifty_candles(limit=10)
        assert mock.await_args.args[0] == NIFTY_SYMBOL

    def test_sector_index_resolves_for_known_auto_symbol(self):
        snap = build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())
        assert snap.sector_index_symbol() == "^CNXAUTO"


# ── Expectation engine ───────────────────────────────────────────────────────

def _snap():
    return build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())


class TestExpectation:
    @pytest.mark.asyncio
    async def test_gap_vs_historical_baseline(self):
        nc = NowcastResult(status=NowcastStatus.OK, implied_profit_growth=0.40, baseline_profit_growth=0.20)
        exp = await compute_expectation(nc, "MARUTI.NS", _snap())
        assert exp.gap_available is True
        assert exp.anchor_used == "historical_baseline"
        assert exp.expectation_gap == pytest.approx(0.20)

    @pytest.mark.asyncio
    async def test_consensus_preferred_when_available(self):
        nc = NowcastResult(status=NowcastStatus.OK, implied_profit_growth=0.40, baseline_profit_growth=0.20)
        with patch("engine.pre_event_expectation_gap.expectation._fetch_consensus",
                   AsyncMock(return_value=0.25)):
            exp = await compute_expectation(nc, "MARUTI.NS", _snap())
        assert exp.anchor_used == "consensus"
        assert exp.consensus_pat_growth == 0.25
        assert exp.expectation_gap == pytest.approx(0.15)

    @pytest.mark.asyncio
    async def test_no_anchor_means_gap_unavailable_not_fabricated(self):
        nc = NowcastResult(status=NowcastStatus.OK, implied_profit_growth=0.40, baseline_profit_growth=None)
        exp = await compute_expectation(nc, "MARUTI.NS", _snap())
        assert exp.gap_available is False
        assert exp.expectation_gap is None

    @pytest.mark.asyncio
    async def test_unavailable_nowcast_yields_no_gap(self):
        nc = NowcastResult(status=NowcastStatus.UNAVAILABLE)
        exp = await compute_expectation(nc, "MARUTI.NS", _snap())
        assert exp.gap_available is False
        assert exp.our_expected_pat_growth is None

    @pytest.mark.asyncio
    async def test_consensus_and_guidance_hooks_return_none_by_default(self):
        # Phase 3 has no provider — the hooks must not fabricate a value.
        from engine.pre_event_expectation_gap.expectation import _fetch_consensus, _fetch_company_guidance
        assert await _fetch_consensus("MARUTI.NS", _snap()) is None
        assert await _fetch_company_guidance("MARUTI.NS", _snap()) is None


# ── Price discount ───────────────────────────────────────────────────────────

class TestPriceDiscountClassification:
    def test_flat_is_not_discounted(self):
        assert _classify(0.0, 0.10) == PriceDiscountStatus.NOT_DISCOUNTED

    def test_moderate(self):
        assert _classify(0.05, 0.10) == PriceDiscountStatus.MODERATELY_DISCOUNTED

    def test_heavy(self):
        assert _classify(0.10, 0.10) == PriceDiscountStatus.HEAVILY_DISCOUNTED

    def test_extreme_is_overextended(self):
        assert _classify(0.20, 0.10) == PriceDiscountStatus.OVEREXTENDED

    def test_heavy_near_high_is_overextended(self):
        assert _classify(0.09, 0.01) == PriceDiscountStatus.OVEREXTENDED

    def test_missing_excess_is_not_discounted(self):
        assert _classify(None, None) == PriceDiscountStatus.NOT_DISCOUNTED


class TestPriceDiscountHelpers:
    def test_distance_from_high(self):
        # latest 95, peak high ~101 → ~6% below
        candles = [_c(95, high=95)] + [_c(100, high=101) for _ in range(30)]
        d = _distance_from_high(candles)
        assert d == pytest.approx((101 - 95) / 101, abs=1e-3)

    def test_abnormal_volume_true(self):
        candles = [_c(100, vol=5000) for _ in range(5)] + [_c(100, vol=1000) for _ in range(20)]
        assert _abnormal_volume(candles) is True

    def test_abnormal_volume_false_when_flat(self):
        candles = [_c(100, vol=1000) for _ in range(25)]
        assert _abnormal_volume(candles) is False


class TestPriceDiscountEndToEnd:
    @pytest.mark.asyncio
    async def test_strong_runup_vs_flat_nifty_is_overextended(self):
        stock, nifty = _uptrend(), _flat()

        async def fake(sym, tf="1d", limit=90, session=None, before=None):
            return (nifty if sym == NIFTY_SYMBOL else stock)[:limit]

        snap = build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())
        with patch("crawler.price_feed.get_latest_candles", side_effect=fake):
            pd = await analyze_price_discount(snap)
        assert pd.status == PriceDiscountStatus.OVEREXTENDED
        assert "20d" in pd.returns

    @pytest.mark.asyncio
    async def test_flat_stock_is_not_discounted(self):
        flat = _flat()

        async def fake(sym, tf="1d", limit=90, session=None, before=None):
            return flat[:limit]

        snap = build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())
        with patch("crawler.price_feed.get_latest_candles", side_effect=fake):
            pd = await analyze_price_discount(snap)
        assert pd.status == PriceDiscountStatus.NOT_DISCOUNTED


# ── Relative strength ────────────────────────────────────────────────────────

class TestRelativeStrength:
    def test_window_return(self):
        # newest-first: latest 110, 20 bars ago 100 → +10%
        candles = [_c(110 - i * 0.5) for i in range(25)]
        r = _window_return(candles, 20)
        assert r == pytest.approx((candles[0].close - candles[20].close) / candles[20].close)

    def test_window_return_insufficient(self):
        assert _window_return([_c(100)], 20) is None

    @pytest.mark.asyncio
    async def test_outperformance_gives_positive_score(self):
        stock, nifty = _uptrend(), _flat()

        async def fake(sym, tf="1d", limit=90, session=None, before=None):
            return (nifty if sym == NIFTY_SYMBOL else stock)[:limit]

        snap = build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())
        with patch("crawler.price_feed.get_latest_candles", side_effect=fake):
            rs = await compute_relative_strength(snap, window=20)
        assert rs.vs_nifty is not None and rs.vs_nifty > 0
        assert rs.score > 0

    @pytest.mark.asyncio
    async def test_score_clamped_to_unit_range(self):
        # Extreme outperformance must still clamp to <= 1.0.
        stock = [_c(300 - i * 3) for i in range(30)]   # huge run-up
        nifty = _flat()

        async def fake(sym, tf="1d", limit=90, session=None, before=None):
            return (nifty if sym == NIFTY_SYMBOL else stock)[:limit]

        snap = build_snapshot("MARUTI.NS", datetime(2026, 10, 1), AsyncMock())
        with patch("crawler.price_feed.get_latest_candles", side_effect=fake):
            rs = await compute_relative_strength(snap, window=20)
        assert -1.0 <= rs.score <= 1.0
