"""Path F — risk bucket tests.

Path F cannot inherit the existing risk caps: `engine/risk_manager.validate_signal`
is family-blind and its per-strategy cap keys on the free-text strategy name, so
a new name gets a full fresh allocation rather than sharing the news families'
budget. These assert Path F actually caps itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from engine.tactical_risk import TacticalRiskManager
from engine.tactical_rules import Signal


def _sig(entry=100.0, stop=98.0, target=106.0, side="BUY"):
    return Signal("X.NS", side, entry, stop, target, 70.0, "ORB", datetime.now())


class TestSizing:
    def test_quantity_from_risk_budget(self):
        rm = TacticalRiskManager(capital=500_000.0)
        # 0.5% of 500k = 2,500 risk; 2.0 per share => 1,250 shares
        d = rm.size(_sig(), ml_prob=0.5)
        assert d.approved and d.quantity == 1250
        assert d.risk_amount == pytest.approx(2500.0)

    def test_zero_stop_distance_rejected(self):
        rm = TacticalRiskManager()
        assert not rm.size(_sig(stop=100.0)).approved

    def test_high_ml_prob_scales_up(self):
        rm = TacticalRiskManager(capital=500_000.0)
        base = rm.size(_sig(), ml_prob=0.5).quantity
        hot = TacticalRiskManager(capital=500_000.0).size(_sig(), ml_prob=0.8).quantity
        assert hot == int(base * 1.2)

    def test_low_ml_prob_scales_down(self):
        rm = TacticalRiskManager(capital=500_000.0)
        base = rm.size(_sig(), ml_prob=0.5).quantity
        cold = TacticalRiskManager(capital=500_000.0).size(_sig(), ml_prob=0.4).quantity
        assert cold == int(base * 0.7)

    def test_neutral_stub_probability_does_not_change_size(self):
        """The ML layer is a stub returning 0.5 — it must not silently resize."""
        a = TacticalRiskManager(capital=500_000.0).size(_sig(), ml_prob=0.5).quantity
        b = TacticalRiskManager(capital=500_000.0).size(_sig(), ml_prob=0.5).quantity
        assert a == b == 1250

    def test_high_vix_halves_size(self):
        rm = TacticalRiskManager(capital=500_000.0)
        calm = rm.size(_sig(), vix=14.0).quantity
        wild = TacticalRiskManager(capital=500_000.0).size(_sig(), vix=30.0).quantity
        assert wild == int(calm * 0.5)

    def test_minimum_quantity_is_one_when_the_budget_allows(self):
        rm = TacticalRiskManager(capital=500_000.0)
        # Risk per share 4,000 vs a 2,500 per-trade budget -> 0.625 -> int 0,
        # so the floor lifts it to 1 share (4,000 still fits the 10,000 bucket).
        d = rm.size(_sig(entry=50_000.0, stop=46_000.0, target=58_000.0))
        assert d.approved and d.quantity == 1
        assert d.risk_amount == pytest.approx(4000.0)

    def test_single_share_that_blows_the_bucket_is_rejected(self):
        """The floor is 1 share, not 'always approve' — if even one share
        exceeds the remaining bucket, the trade must be refused."""
        rm = TacticalRiskManager(capital=1_000.0)   # bucket = 20
        d = rm.size(_sig(entry=50_000.0, stop=46_000.0, target=58_000.0))
        assert not d.approved and "exceed tactical bucket" in d.reason


class TestBucketCap:
    def test_total_bucket_is_two_percent(self):
        rm = TacticalRiskManager(capital=500_000.0)
        assert rm.total_risk_budget == pytest.approx(10_000.0)

    def test_trade_exceeding_remaining_budget_is_rejected(self):
        rm = TacticalRiskManager(capital=500_000.0)
        rm.open_risk = 9_000.0            # only 1,000 left, trade wants 2,500
        d = rm.size(_sig())
        assert not d.approved and "exceed tactical bucket" in d.reason

    def test_commit_consumes_budget(self):
        rm = TacticalRiskManager(capital=500_000.0)
        d = rm.size(_sig())
        rm.commit(d)
        assert rm.open_risk == pytest.approx(2500.0)
        assert rm.remaining_budget == pytest.approx(7500.0)

    def test_bucket_fills_after_four_trades(self):
        rm = TacticalRiskManager(capital=500_000.0)
        approved = 0
        for _ in range(6):
            d = rm.size(_sig())
            if d.approved:
                rm.commit(d)
                approved += 1
        assert approved == 4, "2% bucket / 0.5% per trade == 4 trades"


class TestCooldown:
    def test_three_stops_triggers_cooldown(self):
        rm = TacticalRiskManager()
        for _ in range(3):
            rm.record_stop_loss()
        assert rm.in_cooldown()
        assert not rm.size(_sig()).approved

    def test_two_stops_do_not(self):
        rm = TacticalRiskManager()
        rm.record_stop_loss(); rm.record_stop_loss()
        assert not rm.in_cooldown()

    def test_a_win_resets_the_streak(self):
        rm = TacticalRiskManager()
        rm.record_stop_loss(); rm.record_stop_loss()
        rm.record_win()
        rm.record_stop_loss()
        assert not rm.in_cooldown()

    def test_cooldown_expires_after_an_hour(self):
        rm = TacticalRiskManager()
        for _ in range(3):
            rm.record_stop_loss()
        later = datetime.now() + timedelta(hours=1, minutes=1)
        assert not rm.in_cooldown(now=later)
        assert rm.consecutive_losses == 0
