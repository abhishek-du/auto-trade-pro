"""Regression tests for crawler/zerodha_instruments.py's get_token() dual
NSE/BSE cache lookup, added 2026-07-28 to close a 4-layer gap where no BSE
symbol could ever get a candle backfilled (refresh_instrument_cache() only
ever loaded NSE into INSTRUMENT_CACHE, and get_token() never stripped a
".BO" suffix before searching it -- confirmed live via ASIIL.BO/MOLDTKPAC.NS,
two Direct News trades taken with zero price history on either symbol).

All tests are deterministic and mocked -- no network, no DB.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import crawler.zerodha_instruments as zi


@pytest.fixture(autouse=True)
def _reset_caches():
    with patch.object(zi, "INSTRUMENT_CACHE", {}), \
         patch.object(zi, "INSTRUMENT_CACHE_BSE", {}):
        yield


class TestGetTokenNSE:
    def test_bare_symbol_resolves_from_nse_cache(self):
        zi.INSTRUMENT_CACHE["RELIANCE"] = {"instrument_token": 111}
        assert zi.get_token("RELIANCE") == 111

    def test_ns_suffix_resolves_from_nse_cache(self):
        zi.INSTRUMENT_CACHE["RELIANCE"] = {"instrument_token": 111}
        assert zi.get_token("RELIANCE.NS") == 111

    def test_exchange_prefixed_form_resolves_from_nse_cache(self):
        zi.INSTRUMENT_CACHE["RELIANCE"] = {"instrument_token": 111}
        assert zi.get_token("NSE:RELIANCE") == 111

    def test_unknown_nse_symbol_returns_none(self):
        assert zi.get_token("NOPE.NS") is None


class TestGetTokenBSE:
    def test_bo_suffix_resolves_from_bse_cache(self):
        zi.INSTRUMENT_CACHE_BSE["ASIIL"] = {"instrument_token": 128515844}
        assert zi.get_token("ASIIL.BO") == 128515844

    def test_bo_suffix_never_falls_back_to_nse_cache(self):
        # Same bare symbol present in NSE cache but NOT in BSE cache -- a .BO
        # lookup must not silently return the NSE (different!) token.
        zi.INSTRUMENT_CACHE["RELIANCE"] = {"instrument_token": 111}
        assert zi.get_token("RELIANCE.BO") is None

    def test_dual_listed_symbol_resolves_to_distinct_tokens_per_exchange(self):
        zi.INSTRUMENT_CACHE["RELIANCE"] = {"instrument_token": 111}
        zi.INSTRUMENT_CACHE_BSE["RELIANCE"] = {"instrument_token": 222}
        assert zi.get_token("RELIANCE.NS") == 111
        assert zi.get_token("RELIANCE.BO") == 222

    def test_unknown_bse_symbol_returns_none(self):
        assert zi.get_token("NOPE.BO") is None

    def test_bse_lookup_skips_nse_surveillance_suffix_fallback(self):
        # The -BE/-BZ/... fallback is an NSE-specific convention; a .BO miss
        # must not try it against the BSE cache.
        zi.INSTRUMENT_CACHE_BSE["ASIIL-BE"] = {"instrument_token": 999}
        assert zi.get_token("ASIIL.BO") is None
