"""Tests for the Tier 0 deterministic news router (2026-08-18).

Every headline in here is real — taken from the `news_items` table for
11-18 Aug 2026 — because the bugs this router had were only visible against
real text. Two rounds of live validation caught them:

  * `tickers_affected` arrives from asyncpg as a raw JSON string, so the
    truthiness check sent 100% of items to COMPANY.
  * Substring matching sent company earnings down the macro path: "war"
    matched softWARe and MurudeshWAR, "rbi" matched TuRBIne.

The second class is the expensive one — each false MACRO is an LLM call
spent on news FinBERT already scores correctly.
"""
from __future__ import annotations

import pytest

from crawler.news_router import (
    COMPANY, FILING, MACRO, NOISE, dedupe_key, route_headline,
)


class TestMacroRouting:
    """Geopolitics/rates/commodities must reach the LLM — FinBERT is not
    trained on them and either stays silent or is confidently wrong."""

    @pytest.mark.parametrize("headline", [
        "Oil treads water as US-Iran peace talks stall, Hormuz shipping slows",
        "Will bomb the s*** out of Oman: Trump's latest threat over Strait of Hormuz blockade row",
        "Crude oil price steadies amid no progress in US-Iran talks; Brent near $88 per barrel",
        "Rupee opens 5 paise lower at 95.48 against US dollar",
        "Goldman Says Markets Too Hawkish on Betting Fed Will Hike Rates",
        "Asian shares mark time as West Asia war keeps oil prices elevated",
        "RBI keeps repo rate unchanged as inflation stays within band",
    ])
    def test_macro_headlines_route_to_llm(self, headline):
        assert route_headline(headline, "Markets", None) == MACRO


class TestSubstringFalsePositives:
    """The regression that motivated word-boundary matching. These are all
    company earnings; each one was misrouted to MACRO by plain `in`."""

    @pytest.mark.parametrize("headline,trap", [
        ("Indian Infotech and Software standalone net profit declines 33.96%", "softWARe"),
        ("Murudeshwar Ceramics consolidated net profit declines 58.03%", "MurudeshWAR"),
        ("Triveni Turbine shares tumble 8% after Q1 profit drops 20%", "TuRBIne"),
    ])
    def test_company_earnings_not_misrouted(self, headline, trap):
        assert route_headline(headline, "Markets", None) == COMPANY, f"{trap} leaked"

    def test_earnings_language_wins_over_macro_mention(self):
        """A firm's result that also blames crude is still company news —
        FinBERT is in-domain, so spending an LLM call on it is pure waste."""
        h = "Asian Paints Q1 net profit falls 12% as crude oil costs bite"
        assert route_headline(h, "Markets", None) == COMPANY


class TestTickerPrecedence:
    def test_named_ticker_beats_macro_vocabulary(self):
        h = "ONGC gains as crude oil prices spike on Hormuz tensions"
        assert route_headline(h, "Markets", ["ONGC.NS"]) == COMPANY

    def test_same_headline_without_ticker_is_macro(self):
        h = "Crude oil prices spike on Hormuz tensions"
        assert route_headline(h, "Markets", None) == MACRO

    def test_empty_ticker_list_is_not_a_ticker(self):
        """`[]` and `None` must behave identically — the live extractor emits
        both for an unresolved headline."""
        h = "Crude oil prices spike on Hormuz tensions"
        assert route_headline(h, "Markets", []) == MACRO


class TestFilings:
    def test_exchange_source_routes_to_filing(self):
        h = "Reliance Industries Limited: Board Meeting Intimation"
        assert route_headline(h, "NSE-Announcements", None) == FILING

    def test_filing_shape_detected_without_source(self):
        """Syndicators republish filings under their own source name."""
        h = "Tata Steel Limited: Disclosure under Regulation 30 of SEBI LODR"
        assert route_headline(h, "Markets", None) == FILING

    def test_filing_beats_ticker(self):
        h = "Infosys Limited: Analyst Meet Intimation"
        assert route_headline(h, "NSE-Announcements", ["INFY.NS"]) == FILING


class TestNoise:
    @pytest.mark.parametrize("headline", [
        "7 key things that changed for the market overnight",
        "Stock Market LIVE Updates: Sensex, Nifty trade flat",
        "Top gainers and losers today",
        "Trade setup for Tuesday: 10 stocks to watch",
    ])
    def test_market_wraps_are_noise(self, headline):
        assert route_headline(headline, "Markets", None) == NOISE

    def test_noise_checked_before_macro(self):
        """A wrap mentioning crude describes a move that already happened; it
        is not a catalyst and must not buy an LLM call."""
        h = "7 key things that changed for the market overnight: crude oil, Fed minutes"
        assert route_headline(h, "Markets", None) == NOISE


class TestDefaults:
    def test_empty_headline_is_noise(self):
        assert route_headline("", "Markets", None) == NOISE
        assert route_headline(None, "Markets", None) == NOISE

    def test_unknown_headline_defaults_to_company(self):
        """FinBERT is free; an LLM call is not. Ambiguity resolves to the
        cheap path."""
        h = "Hindustan Unilever appoints new chief operating officer"
        assert route_headline(h, "Markets", None) == COMPANY

    def test_is_pure(self):
        h = "Oil prices rise as US-Iran peace talks stall"
        assert route_headline(h, "Markets", None) == route_headline(h, "Markets", None)


class TestDedupe:
    def test_syndicated_copies_collapse(self):
        """URL dedupe misses these; the same story ran ~15 times across
        outlets on the live feed."""
        a = "Oil prices rise as US-Iran peace talks stall, Hormuz shipping slows - Reuters"
        b = "Oil prices rise as US-Iran peace talks stall, Hormuz shipping slows"
        c = "Oil prices rise as US-Iran peace talks stall, Hormuz shipping slows!"
        assert dedupe_key(a) == dedupe_key(b) == dedupe_key(c)

    def test_distinct_stories_stay_distinct(self):
        a = "Oil prices rise as US-Iran peace talks stall"
        b = "Gold prices fall as dollar strengthens"
        assert dedupe_key(a) != dedupe_key(b)

    def test_case_and_whitespace_insensitive(self):
        assert dedupe_key("  CRUDE Oil   Steadies  ") == dedupe_key("crude oil steadies")


class TestBudget:
    def test_macro_share_stays_a_minority(self):
        """The router's whole purpose is bounding LLM spend. Measured live at
        7.3% of 6,667 headlines over 8 days (~22 unique macro calls/day). If a
        vocabulary edit pushes this materially higher, the crawl's LLM budget
        needs re-checking before it ships — that is what produced 171
        SoftTimeLimitExceeded on 17-Aug."""
        sample = [
            ("Reliance Q1 net profit rises 8%", None),
            ("Infosys revenue beats estimates", None),
            ("TCS margins expand in June quarter", None),
            ("HDFC Bank declares dividend", None),
            ("Wipro order book grows", None),
            ("Tata Motors shares tumble on weak sales", None),
            ("Adani Ports EBITDA up 12%", None),
            ("SBI announces bonus issue", None),
            ("Oil prices rise on Hormuz tensions", None),   # the one MACRO
        ]
        routed = [route_headline(h, "Markets", t) for h, t in sample]
        assert routed.count(MACRO) == 1
        assert routed.count(COMPANY) == 8
