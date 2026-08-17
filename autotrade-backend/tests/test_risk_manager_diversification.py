"""Tests for the diversification caps in engine/risk_manager.py::validate_signal
(P1-1 / P1-2 from the 2026-08-17 forensic post-mortem).

Why these exist
---------------
The capital model (MAX_PORTFOLIO_RISK / MIN_CASH_BUFFER) is deliberately
count-agnostic and MAX_OPEN_POSITIONS is only a runaway-loop guard, so nothing
constrained how CORRELATED the book was. Measured over 3-17 Aug 2026: peak 101
concurrent positions (~69% of equity), 99% long, and outcomes dominated by
sector rather than by selection --

    IT -18,653 | Infra -15,223 | Energy -5,446   (= -39,322)
    Pharma +19,310 | Metals +15,162             (= +34,472)

Because each individual position was small (median 0.68% of equity), the
capital checks never bound. These caps bound breadth instead.

All tests are deterministic and mocked -- no network, no real DB.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from engine.risk_manager import validate_signal
from engine.signal_generator import TradingSignal


def _signal(symbol="CAND.NS", confidence=70.0, entry=100.0, stop=95.0, target=115.0):
    return TradingSignal(
        symbol=symbol, timeframe="1h", action="BUY", confidence=confidence,
        entry_price=entry, stop_loss=stop, take_profit=target,
        pattern_score=0.0, indicator_score=0.0, sentiment_score=0.0, final_score=0.0,
    )


def _open_position(symbol="OTHER.NS", size_usd=1000.0, entry=100.0, stop=95.0,
                   size_units=10.0, unrealised_pnl=0.0):
    return SimpleNamespace(
        symbol=symbol, size_usd=size_usd, entry_price=entry, stop_loss=stop,
        size_units=size_units, unrealised_pnl=unrealised_pnl,
    )


def _cfg(**overrides):
    base = dict(
        max_open_positions=500, max_daily_loss=0.05, min_risk_reward=0.0,
        max_portfolio_risk=0.15, min_cash_buffer=0.10,
        max_concurrent_positions=40, max_positions_per_sector=8,
        max_sector_capital_pct=0.20, max_strategy_capital_pct=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _no_daily_loss():
    with patch("engine.risk_manager._today_closed_pnl", AsyncMock(return_value=0.0)):
        yield


def _sector_map(mapping, default=None):
    """Patch the hub's sector resolver with a fixed symbol -> sector map."""
    return patch("engine.intelligence_hub._get_sector_for_symbol",
                 side_effect=lambda s: mapping.get(s, default))


# ── P1-2: concurrency cap ────────────────────────────────────────────────────

class TestConcurrencyCap:
    @pytest.mark.asyncio
    async def test_book_at_cap_rejects_new_entry(self):
        """40 open positions with the cap at 40 -> reject, even though each is
        tiny and the capital checks are nowhere near binding."""
        positions = [_open_position(symbol=f"S{i}.NS") for i in range(40)]
        with patch("engine.risk_manager.RuntimeConfig.load", AsyncMock(return_value=_cfg())):
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is False
        assert "Concurrency cap" in reason

    @pytest.mark.asyncio
    async def test_one_below_cap_is_allowed(self):
        positions = [_open_position(symbol=f"S{i}.NS") for i in range(39)]
        with patch("engine.risk_manager.RuntimeConfig.load", AsyncMock(return_value=_cfg())), \
             _sector_map({}, default=None):
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is True, reason

    @pytest.mark.asyncio
    async def test_zero_disables_the_cap(self):
        """0 = off, so the 500 runaway-loop ceiling is the only count limit."""
        positions = [_open_position(symbol=f"S{i}.NS") for i in range(80)]
        with patch("engine.risk_manager.RuntimeConfig.load",
                   AsyncMock(return_value=_cfg(max_concurrent_positions=0))), \
             _sector_map({}, default=None):
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is True, reason

    @pytest.mark.asyncio
    async def test_runaway_guard_still_wins_when_lower(self):
        """max_open_positions is a separate, independent ceiling -- if it is the
        tighter of the two it must still fire, with its own message."""
        positions = [_open_position(symbol=f"S{i}.NS") for i in range(10)]
        with patch("engine.risk_manager.RuntimeConfig.load",
                   AsyncMock(return_value=_cfg(max_open_positions=10))):
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is False
        assert "Safety ceiling" in reason


# ── P1-1: per-sector caps ────────────────────────────────────────────────────

class TestSectorCountCap:
    @pytest.mark.asyncio
    async def test_sector_at_count_cap_rejects(self):
        """8 IT names already open, candidate is also IT -> reject. Other
        sectors in the book must not count toward IT's budget."""
        positions = [_open_position(symbol=f"IT{i}.NS") for i in range(8)]
        positions += [_open_position(symbol=f"PH{i}.NS") for i in range(5)]
        mapping = {f"IT{i}.NS": "IT" for i in range(8)}
        mapping.update({f"PH{i}.NS": "Pharma" for i in range(5)})
        mapping["CAND.NS"] = "IT"

        with patch("engine.risk_manager.RuntimeConfig.load", AsyncMock(return_value=_cfg())), \
             _sector_map(mapping):
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is False
        assert "Sector cap" in reason and "IT" in reason

    @pytest.mark.asyncio
    async def test_different_sector_is_allowed_at_same_book_size(self):
        """Identical book, but the candidate is Pharma (5 open, cap 8) -> allow.
        Proves the cap is per-sector, not a disguised global count."""
        positions = [_open_position(symbol=f"IT{i}.NS") for i in range(8)]
        positions += [_open_position(symbol=f"PH{i}.NS") for i in range(5)]
        mapping = {f"IT{i}.NS": "IT" for i in range(8)}
        mapping.update({f"PH{i}.NS": "Pharma" for i in range(5)})
        mapping["CAND.NS"] = "Pharma"

        with patch("engine.risk_manager.RuntimeConfig.load", AsyncMock(return_value=_cfg())), \
             _sector_map(mapping):
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is True, reason


class TestSectorCapitalCap:
    @pytest.mark.asyncio
    async def test_sector_capital_cap_rejects_even_under_count_cap(self):
        """Only 3 IT positions (well under the count cap of 8) but they already
        absorb 19% of equity -- one more must be refused on capital."""
        positions = [_open_position(symbol=f"IT{i}.NS", size_usd=190_000.0) for i in range(3)]
        mapping = {f"IT{i}.NS": "IT" for i in range(3)}
        mapping["CAND.NS"] = "IT"

        # equity = balance + deployed = 2,430,000 + 570,000 = 3,000,000; 20% = 600,000
        with patch("engine.risk_manager.RuntimeConfig.load",
                   AsyncMock(return_value=_cfg(max_positions_per_sector=8))), \
             _sector_map(mapping):
            ok, reason = await validate_signal(_signal(), wallet_balance=2_430_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is False
        assert "Sector capital cap" in reason

    @pytest.mark.asyncio
    async def test_zero_pct_disables_capital_cap(self):
        positions = [_open_position(symbol=f"IT{i}.NS", size_usd=190_000.0) for i in range(3)]
        mapping = {f"IT{i}.NS": "IT" for i in range(3)}
        mapping["CAND.NS"] = "IT"

        with patch("engine.risk_manager.RuntimeConfig.load",
                   AsyncMock(return_value=_cfg(max_sector_capital_pct=0.0))), \
             _sector_map(mapping):
            ok, reason = await validate_signal(_signal(), wallet_balance=2_430_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is True, reason


class TestStrategyAllocationCap:
    """P2-2: no single strategy may become the whole book while its edge is
    unproven. PRE_EVENT_EXPECTATION_GAP was 91% of trades at profit factor
    1.069, with no score component correlating with outcome."""

    def _session_with_strategy_capital(self, deployed: float):
        """validate_signal queries PaperTrade for capital already deployed by
        the candidate's strategy (OpenPosition carries no attribution)."""
        session = AsyncMock()
        session.execute = AsyncMock(return_value=SimpleNamespace(
            scalar_one=lambda: deployed))
        return session

    def _strategy_signal(self, name="PRE_EVENT_EXPECTATION_GAP"):
        sig = _signal()
        sig.strategy = name          # duck-typed, same as open_paper_trade reads
        return sig

    @pytest.mark.asyncio
    async def test_strategy_over_cap_is_rejected(self):
        # equity = 1,900,000 balance + 100,000 deployed = 2,000,000; 35% = 700,000
        positions = [_open_position(symbol="X.NS", size_usd=100_000.0)]
        session = self._session_with_strategy_capital(690_000.0)
        with patch("engine.risk_manager.RuntimeConfig.load",
                   AsyncMock(return_value=_cfg(max_strategy_capital_pct=0.35))), \
             _sector_map({}, default=None):
            ok, reason = await validate_signal(self._strategy_signal(),
                                               wallet_balance=1_900_000.0,
                                               open_positions=positions, session=session)
        assert ok is False
        assert "Strategy allocation cap" in reason
        assert "PRE_EVENT_EXPECTATION_GAP" in reason

    @pytest.mark.asyncio
    async def test_strategy_under_cap_is_allowed(self):
        positions = [_open_position(symbol="X.NS", size_usd=100_000.0)]
        session = self._session_with_strategy_capital(50_000.0)
        with patch("engine.risk_manager.RuntimeConfig.load",
                   AsyncMock(return_value=_cfg(max_strategy_capital_pct=0.35))), \
             _sector_map({}, default=None):
            ok, reason = await validate_signal(self._strategy_signal(),
                                               wallet_balance=1_900_000.0,
                                               open_positions=positions, session=session)
        assert ok is True, reason

    @pytest.mark.asyncio
    async def test_zero_disables_the_cap(self):
        positions = [_open_position(symbol="X.NS", size_usd=100_000.0)]
        session = self._session_with_strategy_capital(9_999_999.0)
        with patch("engine.risk_manager.RuntimeConfig.load",
                   AsyncMock(return_value=_cfg(max_strategy_capital_pct=0.0))), \
             _sector_map({}, default=None):
            ok, reason = await validate_signal(self._strategy_signal(),
                                               wallet_balance=1_900_000.0,
                                               open_positions=positions, session=session)
        assert ok is True, reason

    @pytest.mark.asyncio
    async def test_unattributed_signal_skips_the_cap(self):
        """Legacy callers construct a bare TradingSignal with no strategy
        attribute. They must not be blocked by a cap that cannot be evaluated
        for them -- and must not trigger the DB query either."""
        positions = [_open_position(symbol="X.NS", size_usd=100_000.0)]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=AssertionError("must not query"))
        with patch("engine.risk_manager.RuntimeConfig.load",
                   AsyncMock(return_value=_cfg(max_strategy_capital_pct=0.35))), \
             patch("engine.risk_manager._today_closed_pnl", AsyncMock(return_value=0.0)), \
             _sector_map({}, default=None):
            ok, reason = await validate_signal(_signal(),   # no .strategy set
                                               wallet_balance=1_900_000.0,
                                               open_positions=positions, session=session)
        assert ok is True, reason


class TestSectorResolutionFailsOpen:
    @pytest.mark.asyncio
    async def test_unmapped_candidate_is_not_blocked(self):
        """A symbol the sector cache doesn't know (newly listed / illiquid)
        must still be tradable -- silently blocking every unmapped name would
        shrink the universe in a way that looks like 'no signals' rather than
        a rejection. It still faces every other check."""
        positions = [_open_position(symbol=f"IT{i}.NS") for i in range(8)]
        mapping = {f"IT{i}.NS": "IT" for i in range(8)}   # CAND.NS deliberately absent

        with patch("engine.risk_manager.RuntimeConfig.load", AsyncMock(return_value=_cfg())), \
             _sector_map(mapping, default=None):
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is True, reason

    @pytest.mark.asyncio
    async def test_resolver_raising_does_not_break_validation(self):
        """If the sector module itself blows up, validation continues rather
        than failing every candidate."""
        positions = [_open_position(symbol=f"IT{i}.NS") for i in range(8)]
        with patch("engine.risk_manager.RuntimeConfig.load", AsyncMock(return_value=_cfg())), \
             patch("engine.intelligence_hub._get_sector_for_symbol",
                   side_effect=RuntimeError("sector cache unavailable")):
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=positions, session=AsyncMock())
        assert ok is True, reason

    @pytest.mark.asyncio
    async def test_empty_book_skips_sector_work_entirely(self):
        """First trade of the day: nothing open, so no sector can be over cap."""
        with patch("engine.risk_manager.RuntimeConfig.load", AsyncMock(return_value=_cfg())), \
             patch("engine.intelligence_hub._get_sector_for_symbol") as mock_sector:
            ok, reason = await validate_signal(_signal(), wallet_balance=10_000_000.0,
                                               open_positions=[], session=AsyncMock())
        assert ok is True, reason
        mock_sector.assert_not_called()
