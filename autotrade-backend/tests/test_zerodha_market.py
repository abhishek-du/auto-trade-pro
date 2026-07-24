"""Regression tests for crawler/zerodha_market.py's Kite market-data cooldown
(2026-07-23 fix, Fix 6).

Root cause: `_kite_historical_available`/`_kite_quotes_available` used to be
plain booleans, set False PERMANENTLY (for the process's whole lifetime)
after the first 403, on the wrong assumption that this account is on a free
Kite Connect plan without market-data access. Verified live: this account
DOES have paid Kite Connect access (get_historical_data()/get_quote() both
succeed against the real API) -- so a 403 is a transient condition (auth
hiccup, a race with the daily token-refresh job, etc.), not a permanent
plan limitation. A permanent latch meant one blip silently forced the whole
process onto the yfinance fallback for its entire remaining lifetime. These
tests lock in the replacement: a short, expiring cooldown instead.
"""
from __future__ import annotations

import time as _time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import crawler.zerodha_market as zm


@pytest.fixture(autouse=True)
def _reset_cooldowns():
    zm._kite_historical_blocked_until = 0.0
    zm._kite_quotes_blocked_until = 0.0
    yield
    zm._kite_historical_blocked_until = 0.0
    zm._kite_quotes_blocked_until = 0.0


class TestCooldownNotPermanent:
    def test_historical_available_by_default(self):
        assert zm._kite_historical_available() is True

    def test_quotes_available_by_default(self):
        assert zm._kite_quotes_available() is True

    def test_handle_403_sets_a_cooldown_not_a_permanent_block(self):
        zm._handle_market_data_403("historical")
        assert zm._kite_historical_available() is False
        # Must be bounded, not forever -- the whole point of this fix.
        remaining = zm._kite_historical_blocked_until - _time.monotonic()
        assert 0 < remaining <= zm._KITE_MARKET_DATA_COOLDOWN_SEC

    def test_cooldown_expires_and_becomes_available_again(self):
        zm._kite_historical_blocked_until = _time.monotonic() - 1.0  # already elapsed
        assert zm._kite_historical_available() is True

    def test_historical_and_quotes_cooldowns_are_independent(self):
        zm._handle_market_data_403("historical")
        assert zm._kite_historical_available() is False
        assert zm._kite_quotes_available() is True  # unaffected

    def test_quotes_403_does_not_affect_historical(self):
        zm._handle_market_data_403("quote")
        assert zm._kite_quotes_available() is False
        assert zm._kite_historical_available() is True


class TestGetKiteHistoricalRespectsAndRecoversFromCooldown:
    @pytest.mark.asyncio
    async def test_403_starts_a_cooldown_and_next_call_retries_after_it_expires(self):
        req = httpx.Request("GET", "https://api.kite.trade/x")
        resp_403 = httpx.Response(403, request=req)

        kite = AsyncMock()
        kite.access_token = "tok"
        kite.get_historical_data = AsyncMock(
            side_effect=[httpx.HTTPStatusError("forbidden", request=req, response=resp_403)]
        )

        with patch("crawler.zerodha_market.get_kite_client", return_value=kite), \
             patch("crawler.zerodha_market._get_token", return_value=12345):
            result = await zm.get_kite_historical("TESTCO.NS", "2026-07-20", "2026-07-23", "day")
        assert result == []
        assert zm._kite_historical_available() is False

        # Simulate the cooldown having elapsed -- the NEXT call must try the
        # real API again, not assume it will never work.
        zm._kite_historical_blocked_until = _time.monotonic() - 1.0
        kite.get_historical_data = AsyncMock(return_value=[
            {"timestamp": "2026-07-23T00:00:00+0530", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000}
        ])
        with patch("crawler.zerodha_market.get_kite_client", return_value=kite), \
             patch("crawler.zerodha_market._get_token", return_value=12345):
            result2 = await zm.get_kite_historical("TESTCO.NS", "2026-07-20", "2026-07-23", "day")
        assert len(result2) == 1
        kite.get_historical_data.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_within_cooldown_window_skips_the_network_call_entirely(self):
        zm._kite_historical_blocked_until = _time.monotonic() + 30.0
        kite = AsyncMock()
        kite.access_token = "tok"
        kite.get_historical_data = AsyncMock(return_value=[{"timestamp": "x"}])

        with patch("crawler.zerodha_market.get_kite_client", return_value=kite), \
             patch("crawler.zerodha_market._get_token", return_value=12345):
            result = await zm.get_kite_historical("TESTCO.NS", "2026-07-20", "2026-07-23", "day")
        assert result == []
        kite.get_historical_data.assert_not_called()
