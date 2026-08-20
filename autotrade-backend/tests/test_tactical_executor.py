"""Path F — executor orchestration, end to end with mocked data.

Verifies the pipeline wiring: window gating, universe fetch, rule dispatch,
scoring, ranking, duplicate guard, sizing, persistence — and that the DB
receives TacticalSignal rows and nothing else.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from db.models import TacticalSignal
from engine.tactical_data_fetcher import MarketContext
from engine.tactical_executor import TacticalExecutor


def _frame(n=60, base=100.0, step=0.0, vol=100_000, freq="1min"):
    c = [base + i * step for i in range(n)]
    return pd.DataFrame({
        "open": [x - 0.1 for x in c], "high": [x + 0.3 for x in c],
        "low": [x - 0.3 for x in c], "close": c, "volume": [vol] * n,
        "timestamp": pd.date_range(datetime(2026, 8, 20, 9, 15), periods=n, freq=freq),
    })


def _session():
    added = []
    s = MagicMock()
    s.add = lambda o: added.append(o)
    s.commit = AsyncMock()
    s.rollback = AsyncMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = []
    res.all.return_value = []
    s.execute = AsyncMock(return_value=res)
    s._added = added
    return s


class TestWindowGating:
    @pytest.mark.asyncio
    async def test_outside_entry_window_does_nothing(self):
        with patch("engine.tactical_executor.in_entry_window", return_value=False):
            out = await TacticalExecutor().run_intraday_scan(_session())
        assert out["persisted"] == 0
        assert "entry window" in out["reason"]

    @pytest.mark.asyncio
    async def test_disabled_flag_does_nothing(self):
        with patch("utils.config.settings.TACTICAL_PIPELINE_ENABLED", False):
            out = await TacticalExecutor().run_intraday_scan(_session())
        assert out["persisted"] == 0
        assert "ENABLED" in out["reason"]


class TestScanPersistsSignals:
    @pytest.mark.asyncio
    async def test_mean_reversion_scan_writes_rows(self):
        sess = _session()
        df = _frame(60, base=100.0, step=1.2, freq="5min")
        live = float(df["close"].iloc[-1]) * 1.02

        with patch("engine.tactical_executor.in_entry_window", return_value=True), \
             patch("engine.tactical_executor.get_market_context",
                   AsyncMock(return_value=MarketContext(vix=14.0))), \
             patch("engine.tactical_executor.get_symbols_with_timeframe",
                   AsyncMock(return_value=["AAA.NS"])), \
             patch("engine.tactical_executor.existing_positions", AsyncMock(return_value={})), \
             patch("engine.tactical_executor.get_prices_batch",
                   AsyncMock(return_value={"AAA.NS": live, "BAD.NS": live})), \
             patch("engine.tactical_executor.get_candles_df", AsyncMock(return_value=df)), \
             patch("engine.tactical_scoring.fetch_sector_scores", AsyncMock(return_value={})):
            out = await TacticalExecutor().run_mean_reversion_scan(sess)

        assert out["raw_signals"] >= 1, out
        assert out["persisted"] >= 1
        assert all(isinstance(o, TacticalSignal) for o in sess._added)
        assert all(o.executed is False for o in sess._added)
        assert all(o.sub_pipeline == "F4" for o in sess._added)

    @pytest.mark.asyncio
    async def test_duplicate_position_is_recorded_as_blocked(self):
        sess = _session()
        df = _frame(60, base=100.0, step=1.2, freq="5min")
        live = float(df["close"].iloc[-1]) * 1.02

        with patch("engine.tactical_executor.in_entry_window", return_value=True), \
             patch("engine.tactical_executor.get_market_context",
                   AsyncMock(return_value=MarketContext(vix=14.0))), \
             patch("engine.tactical_executor.get_symbols_with_timeframe",
                   AsyncMock(return_value=["AAA.NS"])), \
             patch("engine.tactical_executor.existing_positions",
                   AsyncMock(return_value={"AAA": "NEWS_DIRECT"})), \
             patch("engine.tactical_executor.get_prices_batch",
                   AsyncMock(return_value={"AAA.NS": live, "BAD.NS": live})), \
             patch("engine.tactical_executor.get_candles_df", AsyncMock(return_value=df)), \
             patch("engine.tactical_scoring.fetch_sector_scores", AsyncMock(return_value={})):
            out = await TacticalExecutor().run_mean_reversion_scan(sess)

        assert out["skipped"] >= 1
        assert any("already open" in (o.reason or "") for o in sess._added)
        assert all(o.executed is False for o in sess._added)

    @pytest.mark.asyncio
    async def test_position_lookup_failure_aborts_rather_than_assuming_empty(self):
        """A failed lookup must not read as 'no open positions'."""
        sess = _session()
        with patch("engine.tactical_executor.in_entry_window", return_value=True), \
             patch("engine.tactical_executor.get_market_context",
                   AsyncMock(return_value=MarketContext())), \
             patch("engine.tactical_executor.get_symbols_with_timeframe",
                   AsyncMock(return_value=["AAA.NS"])), \
             patch("engine.tactical_executor.existing_positions",
                   AsyncMock(side_effect=RuntimeError("db down"))):
            out = await TacticalExecutor().run_mean_reversion_scan(sess)

        assert out["persisted"] == 0
        assert "position lookup failed" in out["reason"]

    @pytest.mark.asyncio
    async def test_empty_universe_is_handled(self):
        with patch("engine.tactical_executor.in_entry_window", return_value=True), \
             patch("engine.tactical_executor.get_market_context",
                   AsyncMock(return_value=MarketContext())), \
             patch("engine.tactical_executor.get_universe", AsyncMock(return_value=[])):
            out = await TacticalExecutor().run_intraday_scan(_session())
        assert out["reason"] == "empty universe"


class TestResilience:
    @pytest.mark.asyncio
    async def test_a_bad_symbol_does_not_abort_the_cycle(self):
        sess = _session()
        df = _frame(60, base=100.0, step=1.2, freq="5min")
        live = float(df["close"].iloc[-1]) * 1.02
        calls = {"n": 0}

        async def _flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad symbol")
            return df

        with patch("engine.tactical_executor.in_entry_window", return_value=True), \
             patch("engine.tactical_executor.get_market_context",
                   AsyncMock(return_value=MarketContext(vix=14.0))), \
             patch("engine.tactical_executor.get_symbols_with_timeframe",
                   AsyncMock(return_value=["BAD.NS", "AAA.NS"])), \
             patch("engine.tactical_executor.existing_positions", AsyncMock(return_value={})), \
             patch("engine.tactical_executor.get_prices_batch",
                   AsyncMock(return_value={"AAA.NS": live, "BAD.NS": live})), \
             patch("engine.tactical_executor.get_candles_df", _flaky), \
             patch("engine.tactical_scoring.fetch_sector_scores", AsyncMock(return_value={})):
            out = await TacticalExecutor().run_mean_reversion_scan(sess)

        assert out["scanned"] == 1        # the good one still scanned
        assert out["persisted"] >= 1


class TestStaleFeedIsRefused:
    """Found live 2026-08-20: the 5m feed was ~21 hours dead while 1m was
    healthy, and F4 produced 14 confident 'oversold rebound' signals an hour by
    computing Bollinger bands on yesterday's bars against today's live price.
    Fail closed instead — same posture as the D3 price fix."""

    @pytest.mark.asyncio
    async def test_stale_frame_is_refused(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from engine import tactical_data_fetcher as tdf

        old = datetime.utcnow() - timedelta(hours=21)
        rows = [MagicMock(timestamp=old - timedelta(minutes=i), open=1.0, high=1.0,
                          low=1.0, close=1.0, volume=1.0) for i in range(30)]
        with patch("engine.tactical_data_fetcher.get_latest_candles",
                   AsyncMock(return_value=rows)):
            assert await tdf.get_candles_df("X.NS", "5m", 30, MagicMock()) is None

    @pytest.mark.asyncio
    async def test_fresh_frame_is_accepted(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from engine import tactical_data_fetcher as tdf

        now = datetime.utcnow()
        rows = [MagicMock(timestamp=now - timedelta(minutes=i), open=1.0, high=1.0,
                          low=1.0, close=1.0, volume=1.0) for i in range(30)]
        with patch("engine.tactical_data_fetcher.get_latest_candles",
                   AsyncMock(return_value=rows)):
            df = await tdf.get_candles_df("X.NS", "5m", 30, MagicMock())
        assert df is not None and len(df) == 30

    @pytest.mark.asyncio
    async def test_point_in_time_replay_still_allows_old_bars(self):
        """`before=` is historical replay — old bars are the entire point."""
        from datetime import timedelta
        from unittest.mock import MagicMock

        from engine import tactical_data_fetcher as tdf

        old = datetime.utcnow() - timedelta(days=400)
        rows = [MagicMock(timestamp=old - timedelta(minutes=i), open=1.0, high=1.0,
                          low=1.0, close=1.0, volume=1.0) for i in range(30)]
        with patch("engine.tactical_data_fetcher.get_latest_candles",
                   AsyncMock(return_value=rows)):
            df = await tdf.get_candles_df("X.NS", "5m", 30, MagicMock(), before=old)
        assert df is not None, "point-in-time replay must not be blocked by the staleness guard"
