"""Regression tests for engine/entry_confirmation.py -- the deterministic
price/volume confirmation gate added 2026-07-28 after live data showed every
stopped-out trade that week (Direct News and the News-strategy's LLM debate
alike) shared near-zero MFE: price never moved favorably before reversing.

All tests are pure/synchronous -- no network, no DB, no mocking needed since
check_price_volume_confirmation() takes a plain snapshot object.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.entry_confirmation import check_price_volume_confirmation, check_day_range_stability


def _snap(change_pct=None, buy_depth=None, sell_depth=None, ohlc=None):
    return SimpleNamespace(
        change_pct=change_pct,
        buy_depth=buy_depth or [],
        sell_depth=sell_depth or [],
        ohlc=ohlc or {},
    )


class TestMissingData:
    def test_no_snapshot_is_not_confirmed(self):
        confirmed, reason = check_price_volume_confirmation(None, "BUY")
        assert confirmed is False
        assert "snapshot" in reason.lower()

    def test_no_change_pct_is_not_confirmed(self):
        confirmed, reason = check_price_volume_confirmation(_snap(change_pct=None), "BUY")
        assert confirmed is False
        assert "day-change" in reason.lower()


class TestDayChangeThreshold:
    def test_buy_flat_on_the_day_not_confirmed(self):
        confirmed, reason = check_price_volume_confirmation(_snap(change_pct=0.1), "BUY")
        assert confirmed is False
        assert "follow-through" in reason.lower()

    def test_buy_wrong_direction_not_confirmed(self):
        confirmed, _ = check_price_volume_confirmation(_snap(change_pct=-1.2), "BUY")
        assert confirmed is False

    def test_buy_past_threshold_confirmed(self):
        confirmed, reason = check_price_volume_confirmation(_snap(change_pct=1.5), "BUY")
        assert confirmed is True
        assert "confirmed" in reason.lower()

    def test_sell_flat_on_the_day_not_confirmed(self):
        confirmed, _ = check_price_volume_confirmation(_snap(change_pct=-0.1), "SELL")
        assert confirmed is False

    def test_sell_wrong_direction_not_confirmed(self):
        confirmed, _ = check_price_volume_confirmation(_snap(change_pct=1.2), "SELL")
        assert confirmed is False

    def test_sell_past_threshold_confirmed(self):
        confirmed, _ = check_price_volume_confirmation(_snap(change_pct=-1.5), "SELL")
        assert confirmed is True

    def test_exact_threshold_boundary_is_confirmed(self):
        # >= MIN_DAY_CHANGE_PCT (0.5), not strictly greater
        confirmed, _ = check_price_volume_confirmation(_snap(change_pct=0.5), "BUY")
        assert confirmed is True


class TestOrderBookSkew:
    def test_buy_seller_dominated_book_not_confirmed_despite_price_move(self):
        confirmed, reason = check_price_volume_confirmation(
            _snap(change_pct=1.0,
                  buy_depth=[{"quantity": 100}],
                  sell_depth=[{"quantity": 500}]),
            "BUY",
        )
        assert confirmed is False
        assert "order book" in reason.lower()

    def test_sell_buyer_dominated_book_not_confirmed_despite_price_move(self):
        confirmed, reason = check_price_volume_confirmation(
            _snap(change_pct=-1.0,
                  buy_depth=[{"quantity": 500}],
                  sell_depth=[{"quantity": 100}]),
            "SELL",
        )
        assert confirmed is False
        assert "order book" in reason.lower()

    def test_buy_balanced_book_confirmed(self):
        confirmed, _ = check_price_volume_confirmation(
            _snap(change_pct=1.0,
                  buy_depth=[{"quantity": 300}],
                  sell_depth=[{"quantity": 400}]),
            "BUY",
        )
        assert confirmed is True

    def test_empty_depth_does_not_block(self):
        # No depth data at all -- only the day-change signal applies.
        confirmed, _ = check_price_volume_confirmation(
            _snap(change_pct=1.0, buy_depth=[], sell_depth=[]), "BUY",
        )
        assert confirmed is True

    def test_malformed_depth_entries_fail_safe_not_crash(self):
        confirmed, _ = check_price_volume_confirmation(
            _snap(change_pct=1.0, buy_depth=[{}], sell_depth=[{"quantity": None}]),
            "BUY",
        )
        assert confirmed is True  # malformed -> treated as 0 quantity, no skew detected


class TestDayRangeStability:
    """check_day_range_stability() -- added 2026-07-29 after AASTHA.NS
    (-11.27%, DIRECT_NEWS's worst trade): entry passed
    check_price_volume_confirmation() (a real bullish move existed) but the
    day's own range was a ~23% whipsaw (high 105.79, low 81.9), and the
    position gapped through its overnight stop the next morning."""

    def test_aastha_shaped_whipsaw_is_rejected(self):
        # The actual AASTHA.NS entry-day OHLC that motivated this check.
        confirmed, reason = check_day_range_stability(
            _snap(ohlc={"open": 103.9, "high": 105.79, "low": 81.9, "close": 81.9})
        )
        assert confirmed is False
        assert "whipsaw" in reason.lower() or "gap" in reason.lower()

    def test_stable_day_range_confirmed(self):
        # A normal ~3% range on an active-news day.
        confirmed, _ = check_day_range_stability(
            _snap(ohlc={"open": 100.0, "high": 102.5, "low": 99.6, "close": 101.0})
        )
        assert confirmed is True

    def test_exact_threshold_boundary_is_confirmed(self):
        # (high-low)/low == 0.12 exactly -- boundary is inclusive (not > threshold)
        confirmed, _ = check_day_range_stability(
            _snap(ohlc={"high": 112.0, "low": 100.0})
        )
        assert confirmed is True

    def test_just_past_threshold_is_rejected(self):
        confirmed, _ = check_day_range_stability(
            _snap(ohlc={"high": 112.01, "low": 100.0})
        )
        assert confirmed is False

    def test_missing_ohlc_fails_open_not_closed(self):
        # Supplementary filter -- missing data should never be the reason a
        # trade gets blocked; check_price_volume_confirmation is the
        # fail-closed gate.
        confirmed, reason = check_day_range_stability(_snap(ohlc={}))
        assert confirmed is True
        assert "no day-range data" in reason.lower()

    def test_zero_low_fails_open_not_divide_by_zero(self):
        confirmed, _ = check_day_range_stability(_snap(ohlc={"high": 10.0, "low": 0.0}))
        assert confirmed is True

    def test_none_snapshot_ohlc_attr_fails_open(self):
        snap = SimpleNamespace()  # no .ohlc attribute at all
        confirmed, _ = check_day_range_stability(snap)
        assert confirmed is True
