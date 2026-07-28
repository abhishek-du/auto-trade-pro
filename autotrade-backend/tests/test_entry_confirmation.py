"""Regression tests for engine/entry_confirmation.py -- the deterministic
price/volume confirmation gate added 2026-07-28 after live data showed every
stopped-out trade that week (Direct News and the News-strategy's LLM debate
alike) shared near-zero MFE: price never moved favorably before reversing.

All tests are pure/synchronous -- no network, no DB, no mocking needed since
check_price_volume_confirmation() takes a plain snapshot object.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.entry_confirmation import check_price_volume_confirmation


def _snap(change_pct=None, buy_depth=None, sell_depth=None):
    return SimpleNamespace(
        change_pct=change_pct,
        buy_depth=buy_depth or [],
        sell_depth=sell_depth or [],
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
