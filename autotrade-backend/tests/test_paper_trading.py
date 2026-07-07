# Unit tests for the paper-trading core layer.
# Run with: pytest tests/test_paper_trading.py -v

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from paper_trading.trade_simulator import TradeSimulator
from paper_trading.pnl_calculator import PnLCalculator
from engine.agent.risk_manager import RiskManagerAgent as RiskManager
from engine.signal_generator import SignalGenerator

import pandas as pd
import numpy as np


# ── TradeSimulator ────────────────────────────────────────────────────────────

class TestTradeSimulator:

    def setup_method(self):
        self.sim = TradeSimulator()

    def test_buy_fill_price_is_above_requested(self):
        result = self.sim.execute_buy("AAPL", 150.0, 10)
        # Adverse slippage means fill > requested for a BUY
        assert result.fill_price >= 150.0

    def test_sell_fill_price_is_below_requested(self):
        result = self.sim.execute_sell("AAPL", 150.0, 10)
        # Adverse slippage means fill < requested for a SELL
        assert result.fill_price <= 150.0

    def test_slippage_within_expected_range(self):
        for _ in range(50):
            result = self.sim.execute_buy("TSLA", 200.0, 1)
            # Max slippage: 8 bps = 0.08 %
            assert result.slippage_pct <= 0.0009, f"Slippage {result.slippage_pct} exceeded 8 bps"

    def test_total_cost_equals_fill_times_qty(self):
        result = self.sim.execute_buy("MSFT", 300.0, 5)
        assert abs(result.total_cost - result.fill_price * result.quantity) < 1e-6

    def test_commission_is_zero(self):
        result = self.sim.execute_buy("GOOG", 100.0, 2)
        assert result.commission == 0.0

    def test_direction_recorded_correctly(self):
        buy  = self.sim.execute_buy("AAPL",  100.0, 1)
        sell = self.sim.execute_sell("AAPL", 100.0, 1)
        assert buy.direction  == "BUY"
        assert sell.direction == "SELL"


# ── PnLCalculator ─────────────────────────────────────────────────────────────

class TestPnLCalculator:

    def setup_method(self):
        self.calc = PnLCalculator()

    def _make_position(self, entry_price, quantity, direction="BUY"):
        from db.models import Position, TradeDirection
        pos = MagicMock(spec=Position)
        pos.entry_price = entry_price
        pos.quantity    = quantity
        pos.direction   = TradeDirection.BUY if direction == "BUY" else TradeDirection.SELL
        return pos

    def test_unrealised_profit_long(self):
        pos = self._make_position(100.0, 10)
        assert self.calc.unrealised_pnl(pos, 110.0) == pytest.approx(100.0)

    def test_unrealised_loss_long(self):
        pos = self._make_position(100.0, 10)
        assert self.calc.unrealised_pnl(pos, 90.0) == pytest.approx(-100.0)

    def test_realised_pnl_for_close_profit(self):
        pos = self._make_position(100.0, 5)
        pnl = self.calc.realised_pnl_for_close(pos, 120.0)
        assert pnl == pytest.approx(100.0)

    def test_realised_pnl_for_close_loss(self):
        pos = self._make_position(100.0, 5)
        pnl = self.calc.realised_pnl_for_close(pos, 80.0)
        assert pnl == pytest.approx(-100.0)


# ── RiskManager ───────────────────────────────────────────────────────────────

class TestRiskManager:

    def setup_method(self):
        # 2 % risk, max 5 positions
        self.rm = RiskManager(max_risk_pct=0.02, max_open_positions=5)

    def test_approved_when_conditions_met(self):
        result = self.rm.size_position(1000.0, 100.0, 95.0, 2)
        assert result.approved is True
        assert result.quantity > 0

    def test_rejected_when_max_positions_reached(self):
        result = self.rm.size_position(1000.0, 100.0, 95.0, 5)
        assert result.approved is False
        assert "Max open positions" in result.reject_reason

    def test_rejected_when_equity_is_zero(self):
        result = self.rm.size_position(0.0, 100.0, 95.0, 0)
        assert result.approved is False

    def test_risk_amount_does_not_exceed_max(self):
        result = self.rm.size_position(1000.0, 100.0, 95.0, 0)
        assert result.risk_amount <= 1000.0 * 0.02 + 1e-6   # allow tiny float error

    def test_rejected_when_stop_equals_entry(self):
        result = self.rm.size_position(1000.0, 100.0, 100.0, 0)
        assert result.approved is False

    def test_signal_strength_validation(self):
        assert self.rm.validate_signal_strength(0.70) is True
        assert self.rm.validate_signal_strength(0.50) is False


# ── SignalGenerator ───────────────────────────────────────────────────────────

class TestSignalGenerator:

    def setup_method(self):
        self.gen = SignalGenerator()

    def _make_df(self, n=100, trend="up"):
        """Create synthetic OHLCV data."""
        base = 100.0
        closes = [base + (i * 0.5 if trend == "up" else -i * 0.5) for i in range(n)]
        df = pd.DataFrame({
            "open":   [c - 0.5 for c in closes],
            "high":   [c + 1.0 for c in closes],
            "low":    [c - 1.0 for c in closes],
            "close":  closes,
            "volume": [1_000_000] * n,
        })
        return df

    def test_returns_valid_signal_type(self):
        df = self._make_df()
        result = self.gen.generate(df)
        assert result.signal in ("BUY", "SELL", "HOLD")

    def test_score_is_between_0_and_1(self):
        df = self._make_df()
        result = self.gen.generate(df)
        assert 0.0 <= result.score <= 1.0

    def test_uptrend_data_leans_bullish(self):
        df = self._make_df(200, "up")
        result = self.gen.generate(df)
        # With strong uptrend, score should be above 0.5
        assert result.score > 0.5

    def test_downtrend_data_leans_bearish(self):
        df = self._make_df(200, "down")
        result = self.gen.generate(df)
        # With strong downtrend, score should be below 0.5
        assert result.score < 0.5

    def test_reasoning_is_non_empty_string(self):
        df = self._make_df()
        result = self.gen.generate(df)
        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 10

    def test_sentiment_shifts_score(self):
        df = self._make_df(100, "up")
        result_no_sent  = self.gen.generate(df, sentiment_score=None)
        result_positive = self.gen.generate(df, sentiment_score=1.0)
        result_negative = self.gen.generate(df, sentiment_score=-1.0)
        # Positive sentiment should increase score vs negative
        assert result_positive.score >= result_negative.score
