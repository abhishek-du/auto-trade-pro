"""Regression tests for engine/fundamental_analyzer.py's typed failure
markers (2026-07-23 round-exhaustion fix, Fix 4).

Root cause: both fetchers returned a bare `{}` on ANY failure (rate-limited,
network error, or genuinely no data), indistinguishable from real absence.
`_tool_fundamentals` (engine/agent/decision_engine.py) needs to tell the LLM
"retrying this won't help, it's throttled" apart from "this stock really
has no PE ratio" -- these tests lock in the `_error`/`_reason` markers that
make that distinction possible.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from engine.fundamental_analyzer import fetch_fundamentals_screener, fetch_fundamentals_upstox


class TestUpstoxFailureMarker:
    """The yfinance fetcher these tests originally targeted was replaced by the
    Upstox Fundamentals API, so `fetch_fundamentals_yfinance` no longer exists
    and this file failed at import (audit D12). The *contract* under test is
    unchanged and still worth locking in: a failure must return a typed
    `_error` marker rather than a bare {} that looks like genuine absence."""

    @pytest.mark.asyncio
    async def test_upstream_exception_returns_fetch_failed_marker(self):
        with patch("crawler.upstox_data.get_key_ratios",
                   AsyncMock(side_effect=ConnectionError("connection reset by peer"))), \
             patch("crawler.upstox_data.get_shareholding", AsyncMock(return_value=[])), \
             patch("crawler.upstox_data.get_company_profile", AsyncMock(return_value={})):
            result = await fetch_fundamentals_upstox("TESTCO.NS")
        assert result["_error"] == "fetch_failed"
        assert "connection reset" in result["_reason"]

    @pytest.mark.asyncio
    async def test_success_returns_real_data_no_error_marker(self):
        with patch("crawler.upstox_data.get_key_ratios",
                   AsyncMock(return_value=[{"name": "P/E", "company_value": "25.5"}])), \
             patch("crawler.upstox_data.get_shareholding", AsyncMock(return_value=[])), \
             patch("crawler.upstox_data.get_company_profile",
                   AsyncMock(return_value={"company_profile": "Test Co"})):
            result = await fetch_fundamentals_upstox("TESTCO.NS")
        assert result["pe_ratio"] == 25.5
        assert "_error" not in result

    def test_removed_yfinance_fetcher_stays_removed(self):
        # Guards the exact regression that broke this file: a caller (or test)
        # referencing the deleted symbol. engine/fundamental_analyzer.py:690 was
        # still calling it, raising NameError into a debug-level swallow.
        import engine.fundamental_analyzer as fa
        assert not hasattr(fa, "fetch_fundamentals_yfinance")


class TestScreenerRateLimitMarker:
    def _mock_client(self, side_effect_or_responses):
        client = MagicMock()
        client.get = AsyncMock(side_effect=side_effect_or_responses)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    @pytest.mark.asyncio
    async def test_both_urls_404_is_genuine_absence_not_error(self):
        req = httpx.Request("GET", "https://www.screener.in/x")
        resp1 = httpx.Response(404, request=req)
        resp2 = httpx.Response(404, request=req)
        ctx = self._mock_client([resp1, resp2])
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch("asyncio.sleep", AsyncMock()), \
             patch("engine.fundamental_analyzer._BS4_AVAILABLE", True):
            result = await fetch_fundamentals_screener("NONEXISTENT")
        assert result == {}  # genuine absence, not an _error marker

    @pytest.mark.asyncio
    async def test_429_returns_rate_limited_marker(self):
        req = httpx.Request("GET", "https://www.screener.in/x")
        resp = httpx.Response(429, request=req)

        async def _raise_429(*a, **kw):
            raise httpx.HTTPStatusError("rate limited", request=req, response=resp)

        client = MagicMock()
        client.get = AsyncMock(side_effect=_raise_429)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch("asyncio.sleep", AsyncMock()), \
             patch("engine.fundamental_analyzer._BS4_AVAILABLE", True):
            result = await fetch_fundamentals_screener("TESTCO")
        assert result == {"_error": "rate_limited"}

    @pytest.mark.asyncio
    async def test_network_error_returns_fetch_failed_marker(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=ctx), \
             patch("asyncio.sleep", AsyncMock()), \
             patch("engine.fundamental_analyzer._BS4_AVAILABLE", True):
            result = await fetch_fundamentals_screener("TESTCO")
        assert result["_error"] == "fetch_failed"

    @pytest.mark.asyncio
    async def test_beautifulsoup_missing_returns_unavailable_marker(self):
        with patch("engine.fundamental_analyzer._BS4_AVAILABLE", False):
            result = await fetch_fundamentals_screener("TESTCO")
        assert result == {"_error": "unavailable", "_reason": "beautifulsoup4 not installed"}
