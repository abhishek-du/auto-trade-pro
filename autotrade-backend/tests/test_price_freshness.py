"""D3 regression — PRICE_CACHE staleness checks must actually bite.

`get_price()` computed `age = now - cached.get("_ts", now)`, and `_ts` was
written at exactly one site. For every entry from the ticker, the batch fetch or
Redis hydration the default applied, `age` was exactly 0.0, and the 30s guard
always passed — a days-old price came back reporting `age_seconds: 0.0`.

Same inert default in zerodha_ticker.get_live_tick, and market_snapshot
discarded the age entirely.
"""
from __future__ import annotations

import time

import pytest


class TestGetPriceStaleness:

    def setup_method(self):
        from crawler.live_prices import PRICE_CACHE
        self._saved = dict(PRICE_CACHE)
        PRICE_CACHE.clear()

    def teardown_method(self):
        from crawler.live_prices import PRICE_CACHE
        PRICE_CACHE.clear()
        PRICE_CACHE.update(self._saved)

    def test_entry_without_ts_is_treated_as_stale(self):
        """The exact D3 defect: no _ts must NOT mean 'perfectly fresh'."""
        from crawler.live_prices import PRICE_CACHE, get_price
        PRICE_CACHE["TESTCO.NS"] = {"price": 100.0, "symbol": "TESTCO.NS"}
        assert get_price("TESTCO.NS") is None

    def test_old_ts_is_treated_as_stale(self):
        from crawler.live_prices import PRICE_CACHE, get_price
        PRICE_CACHE["TESTCO.NS"] = {"price": 100.0, "_ts": time.time() - 3600}
        assert get_price("TESTCO.NS") is None

    def test_fresh_ts_is_returned_with_a_real_age(self):
        from crawler.live_prices import PRICE_CACHE, get_price
        PRICE_CACHE["TESTCO.NS"] = {"price": 100.0, "_ts": time.time() - 2}
        got = get_price("TESTCO.NS")
        assert got is not None and got["price"] == 100.0
        # The bug's signature was age_seconds == 0.0 for everything.
        assert 1.0 <= got["age_seconds"] <= 5.0


class TestTickerStampsTimestamps:

    def test_on_ticks_stamps_ts_into_price_cache(self):
        """LIVE_TICKS always had a _ts; the PRICE_CACHE mirror did not."""
        from crawler.live_prices import PRICE_CACHE
        from crawler.zerodha_ticker import LIVE_TICKS, _TOKEN_TO_SYMBOL, on_ticks

        token = 999_999
        _TOKEN_TO_SYMBOL[token] = "TESTCO.NS"
        PRICE_CACHE.pop("TESTCO.NS", None)
        try:
            on_ticks(None, [{
                "instrument_token": token,
                "last_price": 101.5,
                "ohlc": {"open": 100, "high": 102, "low": 99, "close": 100},
                "volume_traded": 1234,
            }])
            assert "_ts" in PRICE_CACHE["TESTCO.NS"], "PRICE_CACHE entry has no _ts (D3)"
            assert abs(PRICE_CACHE["TESTCO.NS"]["_ts"] - time.time()) < 5
        finally:
            _TOKEN_TO_SYMBOL.pop(token, None)
            LIVE_TICKS.pop(token, None)
            PRICE_CACHE.pop("TESTCO.NS", None)

    def test_get_live_tick_age_defaults_to_stale(self):
        from crawler.zerodha_ticker import LIVE_TICKS, NSE_TOKENS, get_live_tick

        token = 999_998
        NSE_TOKENS["TESTCO.NS"] = token
        LIVE_TICKS[token] = {"last_price": 50.0}      # deliberately no _ts
        try:
            tick = get_live_tick("TESTCO.NS")
            assert tick["_age_seconds"] > 1e8, "missing _ts must read as very old"
        finally:
            NSE_TOKENS.pop("TESTCO.NS", None)
            LIVE_TICKS.pop(token, None)


class TestMarketSnapshotHonoursAge:

    def test_stale_ws_tick_falls_through_to_rest(self):
        from crawler import market_snapshot as ms

        stale = {"last_price": 42.0, "_age_seconds": 999.0, "ohlc": {"close": 40.0}}
        fresh = {"last_price": 42.0, "_age_seconds": 1.0, "ohlc": {"close": 40.0}}

        import unittest.mock as m
        with m.patch("crawler.zerodha_ticker.get_live_tick", return_value=stale):
            assert ms._from_websocket_tick("TESTCO.NS") is None
        with m.patch("crawler.zerodha_ticker.get_live_tick", return_value=fresh):
            snap = ms._from_websocket_tick("TESTCO.NS")
            assert snap is not None and snap.ltp == 42.0
