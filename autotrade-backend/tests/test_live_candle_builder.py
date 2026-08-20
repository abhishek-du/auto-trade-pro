"""Path F — tick-built 1-minute candles (audit blocker 3).

kite_live_candles bulk-fetches thousands of symbols, so the newest 1m bar in
`candles` trails 15-40 minutes (measured at 37 min on 2026-08-20). F1 was
computing ORB/VWAP/pivot levels on half-hour-old bars, and its freshness guard
rejected whole stretches of the session outright.

These bars are SAMPLED (~12 observations/minute), not tick-exact — see the
module docstring. The tests below pin the aggregation, the minute rollover, and
the volume-delta handling.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crawler.live_candle_builder import (
    FAST_CANDLE_HISTORY,
    LiveCandleBuilder,
    fast_candle_key,
    read_fast_candles,
)


def _ts(minute: int, second: int = 0) -> float:
    return datetime(2026, 8, 20, 6, minute, second, tzinfo=timezone.utc).timestamp()


class TestOHLCAggregation:

    def test_first_observation_opens_the_bar(self):
        b = LiveCandleBuilder()
        assert b.observe("X.NS", 100.0, 1000.0, ts=_ts(1, 0)) is None
        assert "X.NS" in b._buckets

    def test_high_and_low_track_extremes(self):
        b = LiveCandleBuilder()
        for px in (100.0, 103.0, 98.0, 101.0):
            b.observe("X.NS", px, 1000.0, ts=_ts(1, 0))
        c = b._buckets["X.NS"].to_candle()
        assert c["open"] == 100.0 and c["high"] == 103.0
        assert c["low"] == 98.0 and c["close"] == 101.0

    def test_rollover_returns_the_finished_bar(self):
        b = LiveCandleBuilder()
        b.observe("X.NS", 100.0, 1000.0, ts=_ts(1, 0))
        b.observe("X.NS", 105.0, 1200.0, ts=_ts(1, 30))
        done = b.observe("X.NS", 106.0, 1300.0, ts=_ts(2, 0))   # next minute
        assert done is not None
        assert done["open"] == 100.0 and done["high"] == 105.0 and done["close"] == 105.0
        assert done["timestamp"].startswith("2026-08-20T06:01")

    def test_new_bucket_opens_at_the_rollover_price(self):
        b = LiveCandleBuilder()
        b.observe("X.NS", 100.0, 1000.0, ts=_ts(1, 0))
        b.observe("X.NS", 106.0, 1300.0, ts=_ts(2, 0))
        assert b._buckets["X.NS"].open == 106.0

    def test_volume_is_a_delta_not_the_cumulative_total(self):
        """volume_traded is cumulative for the DAY; a bar wants the difference."""
        b = LiveCandleBuilder()
        b.observe("X.NS", 100.0, 500_000.0, ts=_ts(1, 0))
        b.observe("X.NS", 101.0, 500_800.0, ts=_ts(1, 30))
        done = b.observe("X.NS", 102.0, 501_000.0, ts=_ts(2, 0))
        assert done["volume"] == 800.0, "should be 500800-500000, not the running total"

    def test_missing_volume_reports_zero_not_a_bogus_total(self):
        b = LiveCandleBuilder()
        b.observe("X.NS", 100.0, None, ts=_ts(1, 0))
        done = b.observe("X.NS", 101.0, None, ts=_ts(2, 0))
        assert done["volume"] == 0.0

    def test_sample_count_is_recorded(self):
        """Lets a reader judge bar quality — a 1-sample bar is barely a bar."""
        b = LiveCandleBuilder()
        for s in (0, 10, 20, 30):
            b.observe("X.NS", 100.0 + s, 1000.0, ts=_ts(1, s))
        done = b.observe("X.NS", 99.0, 1000.0, ts=_ts(2, 0))
        assert done["samples"] == 4

    @pytest.mark.parametrize("bad", [0.0, -5.0, None])
    def test_bad_prices_are_ignored(self, bad):
        b = LiveCandleBuilder()
        assert b.observe("X.NS", bad, 1000.0, ts=_ts(1, 0)) is None
        assert "X.NS" not in b._buckets

    def test_symbols_are_independent(self):
        b = LiveCandleBuilder()
        b.observe("A.NS", 100.0, 1.0, ts=_ts(1, 0))
        b.observe("B.NS", 200.0, 1.0, ts=_ts(1, 0))
        assert b._buckets["A.NS"].open == 100.0
        assert b._buckets["B.NS"].open == 200.0

    def test_minute_floor_is_utc(self):
        """candles stores naive UTC; both sources must share one clock or a
        merge would interleave bars wrongly."""
        got = LiveCandleBuilder._minute_of(_ts(7, 45))
        assert got == datetime(2026, 8, 20, 6, 7)
        assert got.tzinfo is None


class TestPublication:

    @pytest.mark.asyncio
    async def test_publish_pushes_trims_and_expires(self):
        r = MagicMock()
        r.lpush, r.ltrim, r.expire = AsyncMock(), AsyncMock(), AsyncMock()
        b = LiveCandleBuilder()
        with patch("utils.cache.get_redis", return_value=r):
            assert await b.publish("X.NS", {"timestamp": "t", "close": 1.0}) is True
        r.lpush.assert_awaited_once()
        assert r.lpush.await_args.args[0] == fast_candle_key("X.NS")
        r.ltrim.assert_awaited_once_with(fast_candle_key("X.NS"), 0, FAST_CANDLE_HISTORY - 1)
        r.expire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publish_failure_is_not_fatal(self):
        with patch("utils.cache.get_redis", side_effect=ConnectionError("down")):
            assert await LiveCandleBuilder().publish("X.NS", {"a": 1}) is False

    @pytest.mark.asyncio
    async def test_read_returns_oldest_first(self):
        """LPUSH stores newest-first; indicators need oldest-first."""
        stored = [json.dumps({"timestamp": f"2026-08-20T06:0{i}:00", "close": i})
                  for i in (3, 2, 1)]          # as LPUSH would leave them
        r = MagicMock(); r.lrange = AsyncMock(return_value=stored)
        with patch("utils.cache.get_redis", return_value=r):
            out = await read_fast_candles("X.NS")
        assert [c["close"] for c in out] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_read_failure_returns_empty_not_raise(self):
        with patch("utils.cache.get_redis", side_effect=ConnectionError("down")):
            assert await read_fast_candles("X.NS") == []

    @pytest.mark.asyncio
    async def test_corrupt_entries_are_skipped(self):
        r = MagicMock(); r.lrange = AsyncMock(return_value=["{bad json", '{"close": 5}'])
        with patch("utils.cache.get_redis", return_value=r):
            out = await read_fast_candles("X.NS")
        assert len(out) == 1 and out[0]["close"] == 5


class TestSampleOnce:

    @pytest.mark.asyncio
    async def test_reads_live_ticks_and_tolerates_unknown_symbols(self):
        b = LiveCandleBuilder()
        with patch("crawler.zerodha_ticker.LIVE_TICKS", {111: {"last_price": 100.0, "volume_traded": 5.0, "_ts": _ts(1)}}), \
             patch("crawler.zerodha_market.NSE_TOKENS", {"X.NS": 111}), \
             patch("crawler.zerodha_market.INDEX_TOKENS", {}):
            out = await b.sample_once(["X.NS", "UNKNOWN.NS"])
        assert out["observed"] == 1
        assert out["tracking"] == 1

    @pytest.mark.asyncio
    async def test_no_ticks_is_harmless(self):
        with patch("crawler.zerodha_ticker.LIVE_TICKS", {}), \
             patch("crawler.zerodha_market.NSE_TOKENS", {"X.NS": 111}), \
             patch("crawler.zerodha_market.INDEX_TOKENS", {}):
            out = await LiveCandleBuilder().sample_once(["X.NS"])
        assert out["observed"] == 0


class TestWiredIntoUvicorn:
    def test_loop_runs_in_the_ticker_process(self):
        """LIVE_TICKS is a module dict owned by the ticker thread in uvicorn —
        the sampler must run there, not in a Celery worker."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent / "main.py").read_text()
        assert "_fast_candle_loop" in src and "live_candle_builder" in src
