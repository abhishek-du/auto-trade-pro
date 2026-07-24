"""Regression tests for crawler/upstox_data.py::get_isin()'s single-flight
de-duplication (2026-07-23 round-exhaustion fix, Fix 5).

Root cause: engine/company_intelligence.py fires 8 Upstox sub-fetchers
concurrently via asyncio.gather, each independently calling get_isin() for
the same symbol. All 8 start in the same event-loop tick, so none can see
another's in-flight resolution -- confirmed live to fire 8 (sometimes 16)
redundant instruments/search round-trips per candidate. These tests lock in
that N concurrent callers for the same not-yet-cached symbol collapse into
exactly one real resolution.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import crawler.upstox_data as ud


@pytest.fixture(autouse=True)
def _reset_isin_state():
    ud._ISIN_CACHE.clear()
    ud._ISIN_INFLIGHT.clear()
    yield
    ud._ISIN_CACHE.clear()
    ud._ISIN_INFLIGHT.clear()


def _mock_no_db_row():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


class TestSingleFlightDeduplication:
    @pytest.mark.asyncio
    async def test_concurrent_calls_for_same_symbol_resolve_only_once(self):
        call_count = 0

        async def _slow_resolve(bare):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # simulate the real network round-trip
            return ("INE123A01234", "upstox_search")

        with patch("db.database.AsyncSessionLocal", _mock_no_db_row()), \
             patch("crawler.upstox_data._resolve_isin_live", _slow_resolve):
            results = await asyncio.gather(*[ud.get_isin("TESTCO") for _ in range(8)])

        assert call_count == 1  # NOT 8 -- single-flight collapsed them
        assert all(r == "INE123A01234" for r in results)

    @pytest.mark.asyncio
    async def test_sixteen_concurrent_calls_still_resolve_once(self):
        # Mirrors the observed 16x case (company_intelligence invoked twice
        # across rounds, each firing its own 8-way gather).
        call_count = 0

        async def _slow_resolve(bare):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.02)
            return ("INE999Z09999", "upstox_search")

        with patch("db.database.AsyncSessionLocal", _mock_no_db_row()), \
             patch("crawler.upstox_data._resolve_isin_live", _slow_resolve):
            results = await asyncio.gather(*[ud.get_isin("BIGCO") for _ in range(16)])

        assert call_count == 1
        assert all(r == "INE999Z09999" for r in results)

    @pytest.mark.asyncio
    async def test_result_is_cached_after_first_resolution(self):
        async def _resolve(bare):
            return ("INE555B05555", "upstox_search")

        with patch("db.database.AsyncSessionLocal", _mock_no_db_row()), \
             patch("crawler.upstox_data._resolve_isin_live", _resolve):
            await ud.get_isin("CACHEME")

        assert ud._ISIN_CACHE["CACHEME"] == "INE555B05555"

        # A second call after resolution completes must hit the cache, not
        # _resolve_isin_live again.
        with patch("crawler.upstox_data._resolve_isin_live", AsyncMock()) as mock_live:
            result = await ud.get_isin("CACHEME")
        mock_live.assert_not_called()
        assert result == "INE555B05555"

    @pytest.mark.asyncio
    async def test_inflight_entry_is_cleaned_up_after_resolution(self):
        async def _resolve(bare):
            return ("INE111C01111", "upstox_search")

        with patch("db.database.AsyncSessionLocal", _mock_no_db_row()), \
             patch("crawler.upstox_data._resolve_isin_live", _resolve):
            await ud.get_isin("CLEANUP")

        assert "CLEANUP" not in ud._ISIN_INFLIGHT

    @pytest.mark.asyncio
    async def test_different_symbols_do_not_block_each_other(self):
        call_order = []

        async def _resolve(bare):
            call_order.append(bare)
            return (f"ISIN_{bare}", "upstox_search")

        with patch("db.database.AsyncSessionLocal", _mock_no_db_row()), \
             patch("crawler.upstox_data._resolve_isin_live", _resolve):
            results = await asyncio.gather(ud.get_isin("AAA"), ud.get_isin("BBB"), ud.get_isin("CCC"))

        assert set(call_order) == {"AAA", "BBB", "CCC"}
        assert results == ["ISIN_AAA", "ISIN_BBB", "ISIN_CCC"]

    @pytest.mark.asyncio
    async def test_inflight_cleaned_up_even_on_resolution_failure(self):
        async def _resolve(bare):
            raise RuntimeError("upstox down")

        with patch("db.database.AsyncSessionLocal", _mock_no_db_row()), \
             patch("crawler.upstox_data._resolve_isin_live", _resolve):
            with pytest.raises(RuntimeError):
                await ud.get_isin("FAILCASE")

        assert "FAILCASE" not in ud._ISIN_INFLIGHT
        assert "FAILCASE" not in ud._ISIN_CACHE

    @pytest.mark.asyncio
    async def test_concurrent_callers_all_see_the_failure(self):
        async def _resolve(bare):
            await asyncio.sleep(0.02)
            raise RuntimeError("upstox down")

        with patch("db.database.AsyncSessionLocal", _mock_no_db_row()), \
             patch("crawler.upstox_data._resolve_isin_live", _resolve):
            results = await asyncio.gather(
                *[ud.get_isin("MULTIFAIL") for _ in range(4)], return_exceptions=True
            )
        assert all(isinstance(r, RuntimeError) for r in results)

    @pytest.mark.asyncio
    async def test_already_cached_symbol_never_touches_inflight_path(self):
        ud._ISIN_CACHE["PRECACHED"] = "INE000P00000"
        with patch("crawler.upstox_data._resolve_isin_live", AsyncMock()) as mock_live:
            result = await ud.get_isin("PRECACHED.NS")
        mock_live.assert_not_called()
        assert result == "INE000P00000"
