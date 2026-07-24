"""Regression tests for news_discovery_engine.py's anomaly-scan wiring
(2026-07-23, Phase 1 of the pre-event anomaly engine). See
engine/anomaly_detector.py and the approved plan for the full rationale:
an anomaly score alone must NEVER construct a trade -- only a genuine
catalyst found by _investigate_anomaly_catalyst() may reach process_ticker().
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import news_discovery_engine as nde


def _reading(symbol="TEST.NS", tier="INVESTIGATE", score=95.0):
    return SimpleNamespace(
        symbol=symbol, tier=tier, anomaly_score=score,
        price_z=10.0, volume_ratio=20.0, relative_strength=3.0, vwap_deviation=0.01,
    )


@pytest.fixture(autouse=True)
def _reset_state():
    nde._last_anomaly_scan = None
    nde._last_anomaly_investigation.clear()
    yield
    nde._last_anomaly_scan = None
    nde._last_anomaly_investigation.clear()


def _mock_session_ctx():
    session = AsyncMock()
    session.add = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


class TestInvestigateAnomalyCatalyst:
    @pytest.mark.asyncio
    async def test_scheduled_earnings_event_is_found_first(self):
        event = SimpleNamespace(symbol="NESTLEIND.NS", title="Q1 Results", description="Board meeting today")
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(return_value=[event])), \
             patch("crawler.news_crawler.fetch_nse_announcements_for_symbol", AsyncMock()) as mock_nse:
            result = await nde._investigate_anomaly_catalyst("NESTLEIND.NS", session=AsyncMock())
        assert result is not None
        headline, summary, side = result
        assert "Q1 Results" in headline
        assert side == "BUY"
        mock_nse.assert_not_called()  # earnings found first, no need to check NSE feed

    @pytest.mark.asyncio
    async def test_falls_through_to_nse_symbol_scoped_fetch(self):
        ann = {
            "seq_id": "1", "symbol": "NESTLEIND.NS", "company": "Nestle India",
            "category": "Outcome of Board Meeting", "summary": "results approved",
            "headline": "Nestle India: Outcome of Board Meeting", "pdf_url": "x",
            "published_at": datetime.now(), "source": "NSE-Announcements",
        }
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(return_value=[])), \
             patch("crawler.news_crawler.fetch_nse_announcements_for_symbol", AsyncMock(return_value=[ann])):
            result = await nde._investigate_anomaly_catalyst("NESTLEIND.NS", session=AsyncMock())
        assert result is not None
        headline, summary, side = result
        assert headline == ann["headline"]
        assert side == "BUY"

    @pytest.mark.asyncio
    async def test_bearish_nse_category_maps_to_sell(self):
        ann = {
            "seq_id": "2", "symbol": "TEST.NS", "company": "Test Co",
            "category": "Resignation", "summary": "CFO resigns",
            "headline": "Test Co: Resignation", "pdf_url": "x",
            "published_at": datetime.now(), "source": "NSE-Announcements",
        }
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(return_value=[])), \
             patch("crawler.news_crawler.fetch_nse_announcements_for_symbol", AsyncMock(return_value=[ann])):
            result = await nde._investigate_anomaly_catalyst("TEST.NS", session=AsyncMock())
        assert result[2] == "SELL"

    @pytest.mark.asyncio
    async def test_falls_through_to_rss_headline_match(self):
        rss_items = [
            {"headline": "Unrelated company announces dividend", "source": "RSS", "url": "x", "published_at": datetime.now()},
            {"headline": "PARAS Defence wins new order from Ministry", "source": "RSS", "url": "x", "published_at": datetime.now()},
        ]
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(return_value=[])), \
             patch("crawler.news_crawler.fetch_nse_announcements_for_symbol", AsyncMock(return_value=[])), \
             patch("news_discovery_engine.fetch_free_rss_news", AsyncMock(return_value=rss_items)):
            result = await nde._investigate_anomaly_catalyst("PARAS.NS", session=AsyncMock())
        assert result is not None
        headline, summary, side = result
        assert "PARAS" in headline

    @pytest.mark.asyncio
    async def test_no_catalyst_anywhere_returns_none(self):
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(return_value=[])), \
             patch("crawler.news_crawler.fetch_nse_announcements_for_symbol", AsyncMock(return_value=[])), \
             patch("news_discovery_engine.fetch_free_rss_news", AsyncMock(return_value=[])):
            result = await nde._investigate_anomaly_catalyst("TEST.NS", session=AsyncMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_earnings_lookup_failure_falls_through_not_raises(self):
        ann = {
            "seq_id": "3", "symbol": "TEST.NS", "company": "Test Co",
            "category": "Dividend", "summary": "final dividend declared",
            "headline": "Test Co: Dividend", "pdf_url": "x",
            "published_at": datetime.now(), "source": "NSE-Announcements",
        }
        with patch("engine.calendar_engine.get_events_for_range", AsyncMock(side_effect=RuntimeError("db down"))), \
             patch("crawler.news_crawler.fetch_nse_announcements_for_symbol", AsyncMock(return_value=[ann])):
            result = await nde._investigate_anomaly_catalyst("TEST.NS", session=AsyncMock())
        assert result is not None


class TestRunAnomalyScan:
    @pytest.mark.asyncio
    async def test_normal_and_monitor_tiers_take_no_action(self):
        fake_settings = MagicMock(nse_symbols=["A.NS"], nse_mid_symbols=[])
        with patch("utils.config.settings", fake_settings), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.anomaly_detector.get_anomaly_reading", AsyncMock(return_value=_reading(tier="MONITOR"))), \
             patch("news_discovery_engine._investigate_anomaly_catalyst", AsyncMock()) as mock_investigate:
            await nde._run_anomaly_scan(market_open=True)
        mock_investigate.assert_not_called()

    @pytest.mark.asyncio
    async def test_investigate_tier_with_no_catalyst_never_calls_process_ticker(self):
        fake_settings = MagicMock(nse_symbols=["NESTLEIND.NS"], nse_mid_symbols=[])
        with patch("utils.config.settings", fake_settings), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.anomaly_detector.get_anomaly_reading", AsyncMock(return_value=_reading())), \
             patch("news_discovery_engine._investigate_anomaly_catalyst", AsyncMock(return_value=None)), \
             patch("news_discovery_engine.process_ticker", AsyncMock()) as mock_pt:
            await nde._run_anomaly_scan(market_open=True)
        mock_pt.assert_not_called()

    @pytest.mark.asyncio
    async def test_investigate_tier_with_catalyst_dispatches_via_process_ticker(self):
        fake_settings = MagicMock(nse_symbols=["NESTLEIND.NS"], nse_mid_symbols=[])
        with patch("utils.config.settings", fake_settings), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.anomaly_detector.get_anomaly_reading", AsyncMock(return_value=_reading())), \
             patch("news_discovery_engine._investigate_anomaly_catalyst",
                   AsyncMock(return_value=("Nestle: Outcome of Board Meeting", "results approved", "BUY"))), \
             patch("news_discovery_engine.process_ticker", AsyncMock(return_value=True)) as mock_pt:
            await nde._run_anomaly_scan(market_open=True)
        mock_pt.assert_awaited_once_with("NESTLEIND.NS", "BUY", "Nestle: Outcome of Board Meeting", "results approved")

    @pytest.mark.asyncio
    async def test_market_closed_queues_instead_of_dispatching(self):
        fake_settings = MagicMock(nse_symbols=["NESTLEIND.NS"], nse_mid_symbols=[])
        with patch("utils.config.settings", fake_settings), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.anomaly_detector.get_anomaly_reading", AsyncMock(return_value=_reading())), \
             patch("news_discovery_engine._investigate_anomaly_catalyst",
                   AsyncMock(return_value=("headline", "summary", "BUY"))), \
             patch("news_discovery_engine.process_ticker", AsyncMock()) as mock_pt:
            await nde._run_anomaly_scan(market_open=False)
        mock_pt.assert_not_called()

    @pytest.mark.asyncio
    async def test_cooldown_prevents_reinvestigation_within_window(self):
        fake_settings = MagicMock(nse_symbols=["NESTLEIND.NS"], nse_mid_symbols=[])
        nde._last_anomaly_investigation["NESTLEIND.NS"] = datetime.now()
        with patch("utils.config.settings", fake_settings), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.anomaly_detector.get_anomaly_reading", AsyncMock(return_value=_reading())), \
             patch("news_discovery_engine._investigate_anomaly_catalyst", AsyncMock()) as mock_investigate:
            await nde._run_anomaly_scan(market_open=True)
        mock_investigate.assert_not_called()

    @pytest.mark.asyncio
    async def test_cooldown_expires_and_reinvestigates(self):
        fake_settings = MagicMock(nse_symbols=["NESTLEIND.NS"], nse_mid_symbols=[])
        nde._last_anomaly_investigation["NESTLEIND.NS"] = (
            datetime.now() - timedelta(seconds=nde._ANOMALY_INVESTIGATION_COOLDOWN_SEC + 1)
        )
        with patch("utils.config.settings", fake_settings), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.anomaly_detector.get_anomaly_reading", AsyncMock(return_value=_reading())), \
             patch("news_discovery_engine._investigate_anomaly_catalyst", AsyncMock(return_value=None)) as mock_investigate:
            await nde._run_anomaly_scan(market_open=True)
        mock_investigate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_scan_failure_for_one_symbol_does_not_block_others(self):
        fake_settings = MagicMock(nse_symbols=["BAD.NS", "GOOD.NS"], nse_mid_symbols=[])

        async def _side_effect(symbol, session):
            if symbol == "BAD.NS":
                raise RuntimeError("data outage")
            return _reading(symbol="GOOD.NS")

        with patch("utils.config.settings", fake_settings), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx()), \
             patch("engine.anomaly_detector.get_anomaly_reading", side_effect=_side_effect), \
             patch("news_discovery_engine._investigate_anomaly_catalyst", AsyncMock(return_value=None)) as mock_investigate:
            await nde._run_anomaly_scan(market_open=True)
        mock_investigate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_universe_is_a_noop(self):
        fake_settings = MagicMock(nse_symbols=[], nse_mid_symbols=[])
        with patch("utils.config.settings", fake_settings), \
             patch("engine.anomaly_detector.get_anomaly_reading", AsyncMock()) as mock_reading:
            await nde._run_anomaly_scan(market_open=True)
        mock_reading.assert_not_called()
