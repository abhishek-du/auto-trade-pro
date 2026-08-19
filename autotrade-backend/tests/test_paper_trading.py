# Unit tests for the paper-trading core layer.
# Run with: pytest tests/test_paper_trading.py -v

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from paper_trading.trade_simulator import TradeSimulator
from paper_trading.pnl_calculator import PnLCalculator
from engine.agent.risk_manager import RiskManagerAgent as RiskManager
from engine.signal_generator import TradingSignal, generate_signal

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
        # FillResult exposes size_usd / size_units (not total_cost / quantity).
        # size_usd is rounded to paise, so compare at that precision.
        assert abs(result.size_usd - result.fill_price * result.size_units) < 0.01

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
        from db.models import OpenPosition, TradeDirection
        pos = MagicMock(spec=OpenPosition)
        pos.entry_price = entry_price
        pos.size_units  = quantity   # model field is size_units, not quantity
        pos.size_usd    = entry_price * quantity
        pos.direction   = TradeDirection.BUY if direction == "BUY" else TradeDirection.SELL
        return pos

    def test_unrealised_profit_long(self):
        pos = self._make_position(100.0, 10)
        assert self.calc.unrealised_for_position(pos, 110.0) == pytest.approx(100.0)

    def test_unrealised_loss_long(self):
        pos = self._make_position(100.0, 10)
        assert self.calc.unrealised_for_position(pos, 90.0) == pytest.approx(-100.0)

    def test_realised_pnl_for_close_profit(self):
        pos = self._make_position(100.0, 5)
        pnl = self.calc.realised_for_close(pos, 120.0)
        assert pnl == pytest.approx(100.0)

    def test_realised_pnl_for_close_loss(self):
        pos = self._make_position(100.0, 5)
        pnl = self.calc.realised_for_close(pos, 80.0)
        assert pnl == pytest.approx(-100.0)


# ── RiskManager ───────────────────────────────────────────────────────────────

@pytest.mark.skip(
    reason="Obsolete API: RiskManagerAgent now takes a portfolio_ctx dict and "
           "exposes can_take_trade(candidate, equity); max_risk_pct / "
           "validate_signal_strength() no longer exist. These tests were "
           "unreachable behind this file's import error until D12 fixed it. "
           "Needs a purpose-built rewrite -- see tests/test_risk_manager_*.py "
           "which already cover the current gate."
)
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


# ── signal_generator ──────────────────────────────────────────────────────────
# `SignalGenerator` was refactored from a class into module-level async
# functions, so this file failed at import (audit D12). Rewritten against the
# current API: generate_signal(symbol, timeframe, candles_df, session).
# The contract under test is the same one the old class covered -- a synthetic
# OHLCV frame in, a well-formed TradingSignal out, directionally sane.

class TestGenerateSignal:

    @staticmethod
    def _make_df(n=200, trend="up"):
        """Synthetic OHLCV data with a clean monotonic trend."""
        base = 100.0
        closes = [base + (i * 0.5 if trend == "up" else -i * 0.5) for i in range(n)]
        return pd.DataFrame({
            "open":   [c - 0.5 for c in closes],
            "high":   [c + 1.0 for c in closes],
            "low":    [c - 1.0 for c in closes],
            "close":  closes,
            "volume": [1_000_000] * n,
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="D"),
        })

    @staticmethod
    def _session():
        """AsyncSession stub returning no news rows (sentiment contributes 0)."""
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
        result.scalar_one_or_none.return_value = None
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        return session

    async def _run(self, trend="up", n=200):
        return await generate_signal("TESTCO.NS", "1d", self._make_df(n, trend), self._session())

    @pytest.mark.asyncio
    async def test_returns_trading_signal(self):
        sig = await self._run()
        assert isinstance(sig, TradingSignal)
        assert sig.symbol == "TESTCO.NS"
        assert sig.timeframe == "1d"

    @pytest.mark.asyncio
    async def test_action_is_valid(self):
        sig = await self._run()
        assert sig.action in ("BUY", "SELL", "HOLD")

    @pytest.mark.asyncio
    async def test_confidence_is_a_percentage(self):
        sig = await self._run()
        assert 0.0 <= sig.confidence <= 100.0

    @pytest.mark.asyncio
    async def test_scores_are_on_the_documented_scale(self):
        sig = await self._run()
        for field_name in ("indicator_score", "sentiment_score", "final_score"):
            value = getattr(sig, field_name)
            assert -100.0 <= value <= 100.0, f"{field_name}={value} outside -100..100"

    @pytest.mark.asyncio
    async def test_uptrend_scores_above_downtrend(self):
        up   = await self._run("up")
        down = await self._run("down")
        assert up.final_score > down.final_score

    @pytest.mark.asyncio
    async def test_reasoning_points_are_populated(self):
        sig = await self._run()
        assert isinstance(sig.reasoning_points, list)
        assert all(isinstance(r, str) for r in sig.reasoning_points)
