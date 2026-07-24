"""Regression tests for news_discovery_engine.py's _extract_ticker_from_news()
and _strip_corporate_suffixes() (2026-07-23 fix).

Root cause: the previous implementation asked the LLM to guess the '.NS'
ticker directly and used it unchecked. For Bharat Coking Coal (commonly
abbreviated "BCCL" in financial headlines, but actually listed as
BHARATCOAL), this produced a plausible but nonexistent 'BCCL.NS' that
silently failed at every downstream price source instead of surfacing the
mismatch -- discarding a real 82%-confidence trade candidate. The fix
resolves the LLM's extracted COMPANY NAME against the real instrument
database (engine.portfolio_service.search_stocks_async) instead of trusting
an LLM-guessed ticker string. This is a general fix (any company with an
informal abbreviation or alternate name is vulnerable), not BCCL-specific.

All tests are mocked -- no network, no real DB, no live LLM calls.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import news_discovery_engine as nde


class TestStripCorporateSuffixes:
    def test_strips_limited(self):
        assert nde._strip_corporate_suffixes("Bharat Coking Coal Limited") == "Bharat Coking Coal"

    def test_strips_ltd_with_trailing_period(self):
        assert nde._strip_corporate_suffixes("Reliance Industries Ltd.") == "Reliance Industries"

    def test_strips_bare_ltd(self):
        assert nde._strip_corporate_suffixes("HDFC Bank Ltd") == "HDFC Bank"

    def test_name_without_suffix_is_unchanged(self):
        assert nde._strip_corporate_suffixes("Tata Consultancy Services") == "Tata Consultancy Services"

    def test_blank_input_returns_empty_string(self):
        assert nde._strip_corporate_suffixes("   ") == ""

    def test_collapses_extra_whitespace(self):
        assert nde._strip_corporate_suffixes("Some   Company   Pvt   Ltd") == "Some Company"


class TestExtractTickerFromNews:
    @pytest.mark.asyncio
    async def test_bccl_style_abbreviation_resolves_to_real_symbol(self):
        # The exact failure case: LLM extracts the full company name, and
        # instrument-DB resolution finds the REAL trading symbol (BHARATCOAL),
        # not the informal abbreviation ("BCCL") the headline actually used.
        matches = [{"symbol": "BHARATCOAL.NS", "name": "Bharat Coking Coal", "ticker": "BHARATCOAL", "exchange": "NSE", "sector": "Mining"}]
        with patch("news_discovery_engine.call_llm_chat", AsyncMock(return_value="Bharat Coking Coal Limited")), \
             patch("engine.portfolio_service.search_stocks_async", AsyncMock(return_value=matches)):
            result = await nde._extract_ticker_from_news("BCCL reports earnings miss", "Bharat Coking Coal profit falls")
        assert result == "BHARATCOAL.NS"

    @pytest.mark.asyncio
    async def test_llm_returns_none_short_circuits_without_db_lookup(self):
        with patch("news_discovery_engine.call_llm_chat", AsyncMock(return_value="NONE")), \
             patch("engine.portfolio_service.search_stocks_async", AsyncMock()) as mock_search:
            result = await nde._extract_ticker_from_news("Global oil prices rise", "no indian company mentioned")
        assert result is None
        mock_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_instrument_match_fails_closed(self):
        # A company the LLM names that doesn't resolve to any real NSE
        # instrument must NOT fall back to a guessed/raw ticker -- it must
        # return None (fail-closed), same philosophy as NO EVENT -> NO TRADE.
        with patch("news_discovery_engine.call_llm_chat", AsyncMock(return_value="Some Nonexistent Fictional Corp")), \
             patch("engine.portfolio_service.search_stocks_async", AsyncMock(return_value=[])):
            result = await nde._extract_ticker_from_news("headline", "summary")
        assert result is None

    @pytest.mark.asyncio
    async def test_well_known_company_resolves_normally(self):
        matches = [{"symbol": "RELIANCE.NS", "name": "Reliance Industries", "ticker": "RELIANCE", "exchange": "NSE", "sector": "Energy"}]
        with patch("news_discovery_engine.call_llm_chat", AsyncMock(return_value="Reliance Industries Limited")), \
             patch("engine.portfolio_service.search_stocks_async", AsyncMock(return_value=matches)):
            result = await nde._extract_ticker_from_news("Reliance Q1 profit jumps", "summary")
        assert result == "RELIANCE.NS"

    @pytest.mark.asyncio
    async def test_llm_call_exception_returns_none(self):
        with patch("news_discovery_engine.call_llm_chat", AsyncMock(side_effect=RuntimeError("timeout"))):
            result = await nde._extract_ticker_from_news("headline", "summary")
        assert result is None

    @pytest.mark.asyncio
    async def test_db_lookup_exception_fails_closed_not_raises(self):
        with patch("news_discovery_engine.call_llm_chat", AsyncMock(return_value="Some Company Limited")), \
             patch("engine.portfolio_service.search_stocks_async", AsyncMock(side_effect=RuntimeError("db down"))):
            result = await nde._extract_ticker_from_news("headline", "summary")
        assert result is None

    @pytest.mark.asyncio
    async def test_blank_llm_response_returns_none(self):
        with patch("news_discovery_engine.call_llm_chat", AsyncMock(return_value="   ")):
            result = await nde._extract_ticker_from_news("headline", "summary")
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_first_ranked_match_when_multiple_returned(self):
        # search_stocks_async already ranks exact-tradingsymbol-match first;
        # this function must trust that ranking, not re-sort or pick blindly.
        matches = [
            {"symbol": "TATASTEEL.NS", "name": "Tata Steel", "ticker": "TATASTEEL", "exchange": "NSE", "sector": "Metals"},
            {"symbol": "TATASTEELBSL.NS", "name": "Tata Steel BSL", "ticker": "TATASTEELBSL", "exchange": "NSE", "sector": "Metals"},
        ]
        with patch("news_discovery_engine.call_llm_chat", AsyncMock(return_value="Tata Steel")), \
             patch("engine.portfolio_service.search_stocks_async", AsyncMock(return_value=matches)):
            result = await nde._extract_ticker_from_news("headline", "summary")
        assert result == "TATASTEEL.NS"
