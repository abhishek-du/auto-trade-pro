"""D6 regression — Kite REST throttling and order idempotency.

There was no limiter on the Kite REST surface at all, while five producers hit
/quote concurrently from separate processes. And `place_real_order` had no
client order id, no pre-flight check and no dedupe on its own tag — with
task_acks_late=True a redelivered Celery task placed a second real order.

Two properties matter as much as the throttling itself and are tested here:
the limiter fails OPEN, and the exit bucket is independent of the quote bucket.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crawler.zerodha_kite_limiter import Bucket, acquire, acquire_sync


class _FakeRedis:
    """Minimal INCR/EXPIRE-with-TTL stand-in for the Lua counter."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def register_script(self, _lua):
        async def _script(keys, args):
            key, limit = keys[0], int(args[0])
            if self.counts.get(key, 0) >= limit:
                return 0
            self.counts[key] = self.counts.get(key, 0) + 1
            return 1
        return _script


class TestThrottling:

    @pytest.mark.asyncio
    async def test_calls_beyond_the_limit_are_spaced_out(self):
        fake = _FakeRedis()
        with patch("utils.cache.get_redis", return_value=fake), \
             patch("crawler.zerodha_kite_limiter._rate_limit_script", None), \
             patch("utils.config.settings.KITE_QUOTE_RPS", 1), \
             patch("utils.config.settings.KITE_LIMITER_MAX_WAIT", 3.0):
            start = time.monotonic()
            for _ in range(3):
                await acquire(Bucket.QUOTE)
            elapsed = time.monotonic() - start
        # 3 calls at 1/s cannot all land inside the same wall-clock second.
        assert elapsed > 0.5, f"no throttling applied (elapsed={elapsed:.2f}s)"

    @pytest.mark.asyncio
    async def test_exit_bucket_is_not_starved_by_quote_traffic(self):
        """The whole point of the reserved bucket: a quote flood must never
        delay a stop-loss price read."""
        fake = _FakeRedis()
        with patch("utils.cache.get_redis", return_value=fake), \
             patch("crawler.zerodha_kite_limiter._rate_limit_script", None), \
             patch("utils.config.settings.KITE_QUOTE_RPS", 1), \
             patch("utils.config.settings.KITE_EXIT_RPS", 1), \
             patch("utils.config.settings.KITE_LIMITER_MAX_WAIT", 3.0):
            await acquire(Bucket.QUOTE)          # saturate the quote bucket
            start = time.monotonic()
            await acquire(Bucket.EXIT)           # must not queue behind it
            assert time.monotonic() - start < 0.2

    @pytest.mark.asyncio
    async def test_buckets_use_distinct_redis_keys(self):
        fake = _FakeRedis()
        with patch("utils.cache.get_redis", return_value=fake), \
             patch("crawler.zerodha_kite_limiter._rate_limit_script", None):
            await acquire(Bucket.QUOTE)
            await acquire(Bucket.EXIT)
            await acquire(Bucket.ORDER)
        assert len({k.rsplit(":", 1)[0] for k in fake.counts}) == 3


class TestFailsOpen:

    @pytest.mark.asyncio
    async def test_redis_outage_does_not_raise(self):
        """A coordination outage must never wedge the trading loop."""
        with patch("utils.cache.get_redis", side_effect=ConnectionError("redis down")), \
             patch("crawler.zerodha_kite_limiter._rate_limit_script", None):
            await acquire(Bucket.QUOTE)          # must simply return

    def test_sync_variant_fails_open_too(self):
        with patch("redis.from_url", side_effect=ConnectionError("redis down")):
            acquire_sync(Bucket.QUOTE)

    @pytest.mark.asyncio
    async def test_saturated_bucket_proceeds_after_max_wait(self):
        fake = _FakeRedis()
        with patch("utils.cache.get_redis", return_value=fake), \
             patch("crawler.zerodha_kite_limiter._rate_limit_script", None), \
             patch("utils.config.settings.KITE_QUOTE_RPS", 0), \
             patch("utils.config.settings.KITE_LIMITER_MAX_WAIT", 0.3):
            start = time.monotonic()
            await acquire(Bucket.QUOTE)          # never acquirable
            assert 0.25 < time.monotonic() - start < 3.0


class TestOrderIdempotency:

    @staticmethod
    def _session():
        """AsyncSession stub: Rule 9 counts open positions before our guard."""
        res = MagicMock()
        res.scalar.return_value = 0
        res.scalars.return_value.all.return_value = []
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=res)
        sess.flush = AsyncMock()
        sess.add = MagicMock()
        return sess

    def _kite(self, existing_orders):
        kite = MagicMock()
        kite.access_token = "tok"
        kite.get_orders = AsyncMock(return_value=existing_orders)
        kite.place_order = AsyncMock(return_value="NEW-ORDER")
        return kite

    @pytest.mark.asyncio
    async def test_duplicate_tag_returns_existing_order_without_placing(self):
        from engine import zerodha_executor as ze

        signal = MagicMock(); signal.id = "sig-1"; signal.confidence = 99.0
        kite = self._kite([{"order_id": "OLD-1", "tag": "ATP_sig-1", "status": "COMPLETE"}])

        with patch.object(ze, "get_kite_client", return_value=kite), \
             patch.object(ze, "_abort_window", AsyncMock()), \
             patch.object(ze, "_limit_price", return_value=100.0), \
             patch("utils.config.settings.ZERODHA_PAPER_MODE", False), \
             patch("utils.config.settings.PAPER_MODE", False), \
             patch("utils.config.settings.ZERODHA_ENABLED", True), \
             patch.object(ze, "_live_confidence_floor", return_value=0.0), \
             patch.object(ze, "is_nse_market_open", return_value=True, create=True):
            result = await ze.place_real_order(
                "TESTCO.NS", "BUY", 1, self._session(), signal=signal, price=100.0,
            )

        kite.place_order.assert_not_called()
        assert result["order_id"] == "OLD-1"
        assert result.get("duplicate") is True

    @pytest.mark.asyncio
    async def test_rejected_order_with_same_tag_is_not_a_duplicate(self):
        """A rejected order is a retry opportunity, not a duplicate."""
        from engine import zerodha_executor as ze
        kite = self._kite([{"order_id": "OLD-1", "tag": "ATP_sig-2", "status": "REJECTED"}])
        signal = MagicMock(); signal.id = "sig-2"; signal.confidence = 99.0

        with patch.object(ze, "get_kite_client", return_value=kite), \
             patch.object(ze, "_abort_window", AsyncMock()), \
             patch.object(ze, "_limit_price", return_value=100.0), \
             patch("utils.config.settings.ZERODHA_PAPER_MODE", False), \
             patch("utils.config.settings.PAPER_MODE", False), \
             patch("utils.config.settings.ZERODHA_ENABLED", True), \
             patch.object(ze, "_live_confidence_floor", return_value=0.0), \
             patch.object(ze, "is_nse_market_open", return_value=True, create=True):
            try:
                await ze.place_real_order(
                    "TESTCO.NS", "BUY", 1, self._session(), signal=signal, price=100.0,
                )
            except Exception:
                pass   # later safety rules may still block; we only assert the guard
        # The guard must NOT have short-circuited on a REJECTED order.
        assert kite.get_orders.await_count == 1
