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

from engine.fundamental_analyzer import fetch_fundamentals_screener, fetch_fundamentals_yfinance


class TestYfinanceRateLimitMarker:
    def test_rate_limit_exception_returns_typed_marker(self):
        mock_ticker = MagicMock()
        type(mock_ticker).info = property(lambda self: (_ for _ in ()).throw(
            Exception("YFRateLimitError: Too Many Requests. Rate limited. Try after a while.")
        ))
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = fetch_fundamentals_yfinance("TESTCO.NS")
        assert result == {"_error": "rate_limited"}

    def test_generic_exception_returns_fetch_failed_marker(self):
        mock_ticker = MagicMock()
        type(mock_ticker).info = property(lambda self: (_ for _ in ()).throw(
            ConnectionError("connection reset by peer")
        ))
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = fetch_fundamentals_yfinance("TESTCO.NS")
        assert result["_error"] == "fetch_failed"
        assert "connection reset" in result["_reason"]

    def test_success_returns_real_data_no_error_marker(self):
        mock_ticker = MagicMock()
        mock_ticker.info = {"trailingPE": 25.5, "longName": "Test Co"}
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = fetch_fundamentals_yfinance("TESTCO.NS")
        assert result["pe_ratio"] == 25.5
        assert "_error" not in result


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
