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

import datetime as _dt
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


class TestGetKiteHistoricalIsUpstoxBacked:
    """get_kite_historical kept its name but not its broker (2026-09-02).

    The two tests this class replaces exercised a Kite 403 cooldown
    (`_kite_historical_available`). That path is gone: the function opened with
    `if not kite.access_token: return []`, and with Kite's token expired it
    returned ZERO candles for every symbol and every timeframe — silently, since
    all six production callers treat [] as "no data".

    Worth recording why the old pair had to be replaced rather than adapted: the
    second one ("within cooldown, skip the network call") would still PASS today,
    because the Upstox path also returns [] for the fake symbol TESTCO.NS, which
    has no instrument key. It would have been passing for entirely the wrong
    reason — the exact failure mode these tests exist to catch.
    """

    @pytest.mark.asyncio
    async def test_delegates_to_upstox_and_never_touches_kite(self):
        called = {}

        async def _fake_upstox(symbol, from_date, to_date, interval="1d", oi=False):
            called.update(symbol=symbol, interval=interval,
                          from_date=from_date, to_date=to_date)
            return [{"symbol": "TESTCO.NS", "timeframe": "1d", "open": 100.0,
                     "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0,
                     "timestamp": _dt.datetime(2026, 7, 23, 18, 30)}]

        kite = AsyncMock()
        kite.access_token = "tok"
        kite.get_historical_data = AsyncMock(
            side_effect=AssertionError("Kite was called — the migration regressed"))

        with patch("crawler.upstox_candles.get_upstox_candles_for_range",
                   AsyncMock(side_effect=_fake_upstox)), \
             patch("crawler.zerodha_market.get_kite_client", return_value=kite):
            result = await zm.get_kite_historical(
                "TESTCO.NS", "2026-07-20", "2026-07-23", "day")

        assert len(result) == 1
        assert called["symbol"] == "TESTCO.NS"
        kite.get_historical_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_expired_kite_token_no_longer_zeroes_the_result(self):
        """The exact regression: no token used to mean no candles, for everyone."""
        async def _fake_upstox(symbol, from_date, to_date, interval="1d", oi=False):
            return [{"symbol": symbol, "timeframe": "15m", "open": 1.0, "high": 1.0,
                     "low": 1.0, "close": 1.0, "volume": 0.0,
                     "timestamp": _dt.datetime(2026, 7, 23, 4, 0)}]

        kite = AsyncMock()
        kite.access_token = ""      # expired, as in production

        with patch("crawler.upstox_candles.get_upstox_candles_for_range",
                   AsyncMock(side_effect=_fake_upstox)), \
             patch("crawler.zerodha_market.get_kite_client", return_value=kite):
            result = await zm.get_kite_historical(
                "TESTCO.NS", "2026-07-20", "2026-07-23", "15minute")

        assert result, "an empty Kite token must no longer suppress candles"

    @pytest.mark.asyncio
    async def test_kite_style_intervals_are_not_silently_downgraded_to_daily(self):
        """"15minute" must not fall through to the daily default.

        upstox_candles._INTERVAL_MAP uses .get() with a DAILY fallback, so an
        unmapped name returns daily bars instead of raising — a caller asking for
        15-minute data would receive daily data and never know.
        """
        from crawler.upstox_candles import _INTERVAL_MAP

        for kite_name, expected in [
            ("minute", ("minutes", "1")), ("5minute", ("minutes", "5")),
            ("15minute", ("minutes", "15")), ("30minute", ("minutes", "30")),
            ("60minute", ("hours", "1")), ("day", ("days", "1")),
        ]:
            assert _INTERVAL_MAP.get(kite_name) == expected, (
                f"{kite_name!r} is unmapped — callers would silently get daily bars"
            )


