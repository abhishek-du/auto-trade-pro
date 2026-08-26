"""Short-leg guards at the central gate (2026-08-20).

WHY THESE EXIST
---------------
Until the P0 side fix (68d6dc3) the news paths could not emit a SELL at all:
`side` came from a keyword guess that defaulted to BUY, so every bearish
headline contradicted its own classification and was dropped. Zero shorts
reached the gate in the 2026-08-20 session. From the next session they can —
which exposed two gaps:

  1. `SHORT_MAX_VIX = 28.0` sat in config with the comment "block ALL shorts
     when panic (VIX > 28)" and was read by NOTHING (0 references outside
     utils/config.py). A documented control that does not exist is worse than
     no control, because it gets relied on.
  2. `EQUITY_SHORT_ENABLED` is only consulted by hub_short/exhaustion_short.
     The news paths never read it, so the only way to stop a news short was to
     revert the P0 fix — reintroducing the bug.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.decision_router import (
    ConfidenceSource, EventDirectness, RoutingOutcome, StrategyFamily, TradeIntent,
    authorize_trade_intent,
)


def _intent(action="SELL", family=StrategyFamily.DIRECT_NEWS):
    """Geometry is mirrored for a short: stop above entry, target below."""
    if action == "SELL":
        entry, stop, target = 1400.0, 1414.0, 1360.0
    else:
        entry, stop, target = 1400.0, 1386.0, 1440.0
    return TradeIntent(
        strategy="NEWS_DIRECT", symbol="RELIANCE.NS", action=action,
        instrument_type="EQUITY", entry_price=entry, stop_loss=stop,
        take_profit=target, confidence=75.0,
        confidence_source=ConfidenceSource.CALCULATED,
        strategy_family=family,
        event_directness=EventDirectness.DIRECT,
        event_id=1, evidence_ids=["1"],
    )


class TestVixPanicGuard:

    @pytest.mark.asyncio
    async def test_short_blocked_above_the_vix_ceiling(self):
        from db.database import AsyncSessionLocal

        with patch("crawler.live_prices.PRICE_CACHE", {"^INDIAVIX": {"price": 35.0}}), \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=True):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(_intent("SELL"), s)
        assert r.approved is False
        assert r.outcome == RoutingOutcome.BLOCKED_SHORT
        assert "VIX" in r.reason

    @pytest.mark.asyncio
    async def test_longs_are_untouched_by_the_vix_guard(self):
        """The guard is about the SHORT leg only — a panic must not stop buying."""
        from db.database import AsyncSessionLocal

        with patch("crawler.live_prices.PRICE_CACHE", {"^INDIAVIX": {"price": 35.0}}), \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=True):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(_intent("BUY"), s)
        assert r.outcome != RoutingOutcome.BLOCKED_SHORT

    @pytest.mark.asyncio
    async def test_calm_vix_lets_the_short_through_this_gate(self):
        from db.database import AsyncSessionLocal

        with patch("crawler.live_prices.PRICE_CACHE", {"^INDIAVIX": {"price": 14.0}}), \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=True):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(_intent("SELL"), s)
        assert r.outcome != RoutingOutcome.BLOCKED_SHORT

    @pytest.mark.asyncio
    async def test_missing_vix_reading_fails_open(self):
        """A stale/empty cache must not silently ban shorting for the day."""
        from db.database import AsyncSessionLocal

        with patch("crawler.live_prices.PRICE_CACHE", {}), \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=True):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(_intent("SELL"), s)
        assert r.outcome != RoutingOutcome.BLOCKED_SHORT


class TestNewsShortKillSwitch:

    @pytest.mark.asyncio
    async def test_news_short_blocked_when_switched_off(self):
        from db.database import AsyncSessionLocal

        with patch("crawler.live_prices.PRICE_CACHE", {"^INDIAVIX": {"price": 14.0}}), \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=True), \
             patch("utils.config.settings.NEWS_SHORT_ENABLED", False):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(_intent("SELL"), s)
        assert r.approved is False
        assert r.outcome == RoutingOutcome.BLOCKED_SHORT

    @pytest.mark.asyncio
    async def test_switch_does_not_touch_news_longs(self):
        from db.database import AsyncSessionLocal

        with patch("crawler.live_prices.PRICE_CACHE", {"^INDIAVIX": {"price": 14.0}}), \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=True), \
             patch("utils.config.settings.NEWS_SHORT_ENABLED", False):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(_intent("BUY"), s)
        assert r.outcome != RoutingOutcome.BLOCKED_SHORT

    @pytest.mark.asyncio
    async def test_switch_is_scoped_to_news_families(self):
        """TACTICAL shorts are governed by their own controls, not this switch."""
        from db.database import AsyncSessionLocal

        with patch("crawler.live_prices.PRICE_CACHE", {"^INDIAVIX": {"price": 14.0}}), \
             patch("crawler.india_price_feed.is_nse_market_open", return_value=True), \
             patch("utils.config.settings.NEWS_SHORT_ENABLED", False):
            async with AsyncSessionLocal() as s:
                r = await authorize_trade_intent(
                    _intent("SELL", StrategyFamily.TACTICAL), s)
        assert r.outcome != RoutingOutcome.BLOCKED_SHORT


class TestConfigIsNoLongerDead:

    def test_short_max_vix_is_actually_read(self):
        """The whole point: this used to be config nobody consulted."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "engine" / "decision_router.py").read_text(
            encoding="utf-8")
        assert "SHORT_MAX_VIX" in src

    def test_news_short_flag_defaults_on(self):
        from utils.config import Settings

        assert Settings.model_fields["NEWS_SHORT_ENABLED"].default is True

    def test_runtime_key_is_whitelisted(self):
        """RuntimeConfig.set rejects unknown keys, so the kill switch would be
        unsettable without this."""
        from utils.runtime_config import _KNOWN_KEYS

        assert _KNOWN_KEYS.get("news_short_enabled") is bool
