"""Regression tests for engine/agent/decision_engine.py's tool-level fixes:

- (2026-07-23, round-exhaustion investigation) Fix 1: `earnings`/
  `screener_deep` removed as separate dispatchable tools -- they were
  undisclosed aliases of `_tool_fundamentals` (confirmed live to fire the
  identical yfinance/screener.in fetch 2-4x per candidate in 100% of 55
  round-exhaustion cases traced).
- (2026-07-23, Upstox-primary fix, follow-up) `_tool_fundamentals` now
  sources PE/ROE/ROCE/D-E/promoter-holding from
  engine.company_intelligence.get_company_intelligence() -- Upstox primary,
  DB-then-live-yfinance/screener per-field fallback already correctly
  implemented there -- rather than a second, parallel yfinance-primary
  pipeline. Only 3-year revenue/profit growth CAGR (no Upstox equivalent)
  is still fetched independently from screener.in. `_tool_fundamentals` and
  `_tool_company_intelligence` share one per-debate-session cache of the
  raw get_company_intelligence() result, so a debate calling both core
  tools (every debate) triggers exactly one real Upstox fetch.

  This replaced an earlier, broken version of the Upstox fallback (added in
  the same session, caught before it ever shipped live) that called
  crawler.upstox_data.get_key_ratios() and treated its return as a flat
  {"pe":..., "roe":...} dict -- it actually returns Upstox's raw payload, a
  *list* of {"name": "P/E", "company_value": ...} dicts, which has no
  .get() method at all. Routing through get_company_intelligence() instead
  reuses its already-correct, already-tested _merge_ratios() normalization
  rather than re-deriving the field mapping a second time.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import engine.agent.decision_engine as de


@pytest.fixture(autouse=True)
def _reset_caches():
    de._fundamentals_cache_var.set(None)
    de._company_intelligence_cache_var.set(None)
    yield
    de._fundamentals_cache_var.set(None)
    de._company_intelligence_cache_var.set(None)


def _make_ci(
    pe=20.0, roe=15.0, roce=18.0, debt_to_equity=0.5, promoter_pct=55.0,
    valuation_source="UPSTOX", quality_source="UPSTOX",
) -> dict:
    """Build a realistic get_company_intelligence() return, matching its
    real documented shape (engine/company_intelligence.py::get_company_intelligence)
    -- including per-field `field_sources`, live-verified against RELIANCE.NS
    to be the actual field _tool_fundamentals reads (the coarse section-level
    `source` alone is NOT what's displayed per-field; a real live call caught
    this: ROE/ROCE succeeding from Upstox tagged the whole "quality" section
    UPSTOX while D/E was genuinely unavailable from every source)."""
    return {
        "symbol": "TESTCO",
        "identity": {"sector": "IT", "business_description": "test co"},
        "financial_statements": None,
        "valuation": {"pe": pe, "pb": 3.0, "market_cap_cr": 1000.0} if pe is not None else None,
        "quality": {"roe": roe, "roce": roce, "debt_to_equity": debt_to_equity, "current_ratio": 1.5} if roe is not None else None,
        "ownership": {"promoter": {"latest_pct": promoter_pct, "trend": None}, "fii": None, "dii": None, "public": None},
        "corporate_events": None,
        "competitors": None,
        "metadata": {
            "status": "healthy",
            "completeness": 1.0,
            "failed_sections": [],
            "sections": {
                "valuation": {
                    "source": valuation_source, "retrieved_at": "2026-07-23T00:00:00",
                    "field_sources": {
                        "pe": valuation_source if pe is not None else "UNAVAILABLE",
                        "pb": valuation_source if pe is not None else "UNAVAILABLE",
                    },
                },
                "quality": {
                    "source": quality_source, "retrieved_at": "2026-07-23T00:00:00",
                    "field_sources": {
                        "roe": quality_source if roe is not None else "UNAVAILABLE",
                        "roce": quality_source if roce is not None else "UNAVAILABLE",
                        "debt_to_equity": quality_source if debt_to_equity is not None else "UNAVAILABLE",
                        "current_ratio": quality_source if roe is not None else "UNAVAILABLE",
                    },
                },
            },
            "retrieved_at": "2026-07-23T00:00:00",
        },
    }


class TestAliasToolsRemoved:
    def test_earnings_not_in_llm_tools(self):
        assert "earnings" not in de._LLM_TOOLS

    def test_screener_deep_not_in_llm_tools(self):
        assert "screener_deep" not in de._LLM_TOOLS

    def test_fundamentals_and_company_intelligence_still_present(self):
        assert "fundamentals" in de._LLM_TOOLS
        assert "company_intelligence" in de._LLM_TOOLS

    def test_earnings_not_in_tool_capabilities(self):
        assert "earnings" not in de._TOOL_CAPABILITIES

    def test_screener_deep_not_in_tool_capabilities(self):
        assert "screener_deep" not in de._TOOL_CAPABILITIES

    def test_removed_tool_functions_no_longer_exist(self):
        assert not hasattr(de, "_tool_earnings_report")
        assert not hasattr(de, "_tool_screener_deep")

    def test_fundamentals_capability_unchanged_by_removal(self):
        caps = set(de._TOOL_CAPABILITIES["fundamentals"])
        assert de.EvidenceCapability.FINANCIAL_PERFORMANCE in caps
        assert de.EvidenceCapability.VALUATION in caps


class TestFundamentalsSourcesFromCompanyIntelligence:
    @pytest.mark.asyncio
    async def test_pulls_pe_roe_roce_de_from_company_intelligence(self):
        with patch("engine.company_intelligence.get_company_intelligence",
                   AsyncMock(return_value=_make_ci(pe=22.5, roe=17.0, roce=19.0, debt_to_equity=0.4))), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            result = await de._tool_fundamentals("TESTCO.NS")
        assert "PE=22.5" in result
        assert "ROE=17.0" in result
        assert "ROCE=19.0" in result
        assert "D/E=0.4" in result
        assert "Upstox-primary" in result

    @pytest.mark.asyncio
    async def test_pulls_promoter_holding_from_ownership(self):
        with patch("engine.company_intelligence.get_company_intelligence",
                   AsyncMock(return_value=_make_ci(promoter_pct=62.3))), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            result = await de._tool_fundamentals("TESTCO.NS")
        assert "promoter=62.3" in result

    @pytest.mark.asyncio
    async def test_source_tag_reflects_actual_provenance(self):
        with patch("engine.company_intelligence.get_company_intelligence",
                   AsyncMock(return_value=_make_ci(valuation_source="YFINANCE_SCREENER", quality_source="FUNDAMENTAL_DATA"))), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            result = await de._tool_fundamentals("TESTCO.NS")
        assert "PE=20.0[YFINANCE_SCREENER]" in result
        assert "ROE=15.0%[FUNDAMENTAL_DATA]" in result

    @pytest.mark.asyncio
    async def test_growth_cagr_still_comes_from_screener(self):
        with patch("engine.company_intelligence.get_company_intelligence",
                   AsyncMock(return_value=_make_ci())), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener",
                   AsyncMock(return_value={"revenue_growth_3yr": 14.2, "profit_growth_3yr": 9.8})):
            result = await de._tool_fundamentals("TESTCO.NS")
        assert "rev_growth_3y=14.2" in result
        assert "profit_growth_3y=9.8" in result

    @pytest.mark.asyncio
    async def test_screener_rate_limited_notes_growth_unavailable_not_crash(self):
        with patch("engine.company_intelligence.get_company_intelligence",
                   AsyncMock(return_value=_make_ci())), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener",
                   AsyncMock(return_value={"_error": "rate_limited"})):
            result = await de._tool_fundamentals("TESTCO.NS")
        assert "screener.in rate_limited" in result
        assert "growth figures unavailable" in result
        assert "PE=20.0" in result  # Upstox-sourced fields unaffected

    @pytest.mark.asyncio
    async def test_unavailable_valuation_and_quality_are_noted(self):
        ci = _make_ci(
            pe=None, roe=None, roce=None, debt_to_equity=None,
            valuation_source="UNAVAILABLE", quality_source="UNAVAILABLE",
        )
        ci["valuation"] = None
        ci["quality"] = None
        with patch("engine.company_intelligence.get_company_intelligence", AsyncMock(return_value=ci)), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            result = await de._tool_fundamentals("TESTCO.NS")
        assert "PE unavailable" in result
        assert "quality ratios" in result
        assert "retrying this tool will not help" in result

    @pytest.mark.asyncio
    async def test_get_company_intelligence_exception_does_not_crash(self):
        with patch("engine.company_intelligence.get_company_intelligence",
                   AsyncMock(side_effect=RuntimeError("upstox down"))):
            result = await de._tool_fundamentals("TESTCO.NS")
        assert "fundamentals: error" in result

    @pytest.mark.asyncio
    async def test_yfinance_never_called_directly_by_this_tool(self):
        # Upstox-primary fix: fetch_fundamentals_yfinance is no longer called
        # directly by _tool_fundamentals at all -- it's still used (correctly)
        # inside get_company_intelligence()'s own internal fallback, but not
        # here as a second, parallel path.
        with patch("engine.company_intelligence.get_company_intelligence",
                   AsyncMock(return_value=_make_ci())), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_yfinance") as mock_yf:
            await de._tool_fundamentals("TESTCO.NS")
        mock_yf.assert_not_called()


class TestFundamentalsPerSessionCache:
    @pytest.mark.asyncio
    async def test_no_active_session_does_not_cache(self):
        with patch("engine.company_intelligence.get_company_intelligence",
                   AsyncMock(return_value=_make_ci(pe=10.0))), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            r1 = await de._tool_fundamentals("TESTCO.NS")
            r2 = await de._tool_fundamentals("TESTCO.NS")
        assert "PE=10.0" in r1
        assert "PE=10.0" in r2

    @pytest.mark.asyncio
    async def test_active_session_caches_and_skips_refetch(self):
        de._fundamentals_cache_var.set({})
        de._company_intelligence_cache_var.set({})
        ci_calls = []

        async def _ci(symbol):
            ci_calls.append(symbol)
            return _make_ci(pe=22.0)

        with patch("engine.company_intelligence.get_company_intelligence", _ci), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            r1 = await de._tool_fundamentals("TESTCO.NS")
            r2 = await de._tool_fundamentals("TESTCO.NS")
        assert r1 == r2
        assert ci_calls == ["TESTCO.NS"]  # only ONE real fetch

    @pytest.mark.asyncio
    async def test_cache_is_keyed_per_symbol(self):
        de._fundamentals_cache_var.set({})
        de._company_intelligence_cache_var.set({})
        ci_calls = []

        async def _ci(symbol):
            ci_calls.append(symbol)
            return _make_ci()

        with patch("engine.company_intelligence.get_company_intelligence", _ci), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            await de._tool_fundamentals("AAA.NS")
            await de._tool_fundamentals("BBB.NS")
            await de._tool_fundamentals("AAA.NS")  # repeat -- should be cached
        assert ci_calls == ["AAA.NS", "BBB.NS"]

    @pytest.mark.asyncio
    async def test_different_debate_sessions_do_not_share_cache(self):
        ci_calls = []

        async def _ci(symbol):
            ci_calls.append(symbol)
            return _make_ci()

        with patch("engine.company_intelligence.get_company_intelligence", _ci), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            de._fundamentals_cache_var.set({})
            de._company_intelligence_cache_var.set({})
            await de._tool_fundamentals("AAA.NS")
            de._fundamentals_cache_var.set({})  # new session
            de._company_intelligence_cache_var.set({})
            await de._tool_fundamentals("AAA.NS")
        assert ci_calls == ["AAA.NS", "AAA.NS"]


class TestSharedCompanyIntelligenceCache:
    """Both `fundamentals` and `company_intelligence` are mandatory core
    tools sourcing from the same get_company_intelligence() call -- a debate
    calling both must trigger exactly one real Upstox fetch, not two."""

    @pytest.mark.asyncio
    async def test_fundamentals_then_company_intelligence_fetches_once(self):
        de._fundamentals_cache_var.set({})
        de._company_intelligence_cache_var.set({})
        ci_calls = []

        async def _ci(symbol):
            ci_calls.append(symbol)
            return _make_ci()

        with patch("engine.company_intelligence.get_company_intelligence", _ci), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            await de._tool_fundamentals("TESTCO.NS")
            await de._tool_company_intelligence("TESTCO.NS")
        assert ci_calls == ["TESTCO.NS"]  # one fetch shared by both tools

    @pytest.mark.asyncio
    async def test_company_intelligence_then_fundamentals_fetches_once(self):
        de._fundamentals_cache_var.set({})
        de._company_intelligence_cache_var.set({})
        ci_calls = []

        async def _ci(symbol):
            ci_calls.append(symbol)
            return _make_ci()

        with patch("engine.company_intelligence.get_company_intelligence", _ci), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            await de._tool_company_intelligence("TESTCO.NS")
            await de._tool_fundamentals("TESTCO.NS")
        assert ci_calls == ["TESTCO.NS"]

    @pytest.mark.asyncio
    async def test_no_active_session_each_tool_fetches_independently(self):
        ci_calls = []

        async def _ci(symbol):
            ci_calls.append(symbol)
            return _make_ci()

        with patch("engine.company_intelligence.get_company_intelligence", _ci), \
             patch("engine.fundamental_analyzer.fetch_fundamentals_screener", AsyncMock(return_value={})):
            await de._tool_fundamentals("TESTCO.NS")
            await de._tool_company_intelligence("TESTCO.NS")
        assert ci_calls == ["TESTCO.NS", "TESTCO.NS"]  # no session cache active -- each fetches fresh


class TestFilterClaimsPresentInContext:
    """engine/agent/decision_engine.py::_filter_claims_present_in_context()
    -- added 2026-07-24 after confirming live (SBILIFE.NS) that the LLM
    fact-checker layer in _check_grounding sometimes non-deterministically
    flags a claim as "unsupported" even when it's a verbatim restatement of
    the given canonical-event headline. This is a deterministic safety net
    on the LLM layer's output only -- the 3 deterministic layers in
    _check_grounding are untouched and remain the primary defense."""

    def test_claim_number_matching_context_number_is_dropped(self):
        context = "title: SBI Life Insurance Q1 Results: Net profit rises 22% YoY to Rs 725 crore"
        claims = ["Earnings report shows 22% YoY profit increase."]
        assert de._filter_claims_present_in_context(claims, context) == []

    def test_claim_with_a_number_not_in_context_is_kept(self):
        context = "title: SBI Life Insurance Q1 Results: Net profit rises 22% YoY to Rs 725 crore"
        claims = ["Analyst upgraded target price by 45%."]
        result = de._filter_claims_present_in_context(claims, context)
        assert result == claims

    def test_claim_with_partially_matching_numbers_is_kept(self):
        # Only 22% is given; 15% is a genuinely new, unsupported figure --
        # the whole claim must still be flagged, not silently exempted.
        context = "Net profit rises 22% YoY to Rs 725 crore"
        claims = ["Profit up 22%, revenue up 15%."]
        result = de._filter_claims_present_in_context(claims, context)
        assert result == claims

    def test_mixed_batch_only_grounded_ones_dropped(self):
        context = "Net profit rises 22% YoY to Rs 725 crore"
        claims = ["Earnings report shows 22% YoY profit increase.", "5G partnership announced with Reliance Jio."]
        result = de._filter_claims_present_in_context(claims, context)
        assert result == ["5G partnership announced with Reliance Jio."]

    def test_numberless_claim_with_high_keyword_overlap_is_dropped(self):
        context = "board meeting approved unaudited financial results for the quarter"
        claims = ["board meeting approved unaudited financial results"]
        assert de._filter_claims_present_in_context(claims, context) == []

    def test_numberless_claim_with_low_keyword_overlap_is_kept(self):
        context = "board meeting approved unaudited financial results for the quarter"
        claims = ["management announced a surprise stock split"]
        result = de._filter_claims_present_in_context(claims, context)
        assert result == claims

    def test_empty_claims_list_returns_empty(self):
        assert de._filter_claims_present_in_context([], "some context") == []


class TestCheckGroundingLLMLayerFalsePositive:
    """Integration-level regression guard for the exact SBILIFE.NS false
    positive: a claim restating the canonical event's own given headline
    must not fail grounding, even if the LLM fact-checker (mocked here)
    flags it -- the deterministic post-filter must catch it."""

    @pytest.mark.asyncio
    async def test_llm_flagging_a_given_headline_fact_does_not_fail_grounding(self):
        candidate_context = (
            "=== CANONICAL_EVENT ===\n"
            "title: SBI Life Insurance Q1 Results: Net profit rises 22% YoY to Rs 725 crore\n"
        )
        verdict_step = {
            "bull": "Earnings report shows 22% YoY profit increase.",
            "bear": "Stock near resistance.",
            "thesis": "Bullish on results.",
            "thought": "Deciding.",
        }
        llm_resp = '{"unsupported_claims": ["Earnings report shows 22% YoY profit increase."], "grounded": false}'
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=llm_resp)):
            result = await de._check_grounding(
                "SBILIFE.NS", verdict_step, tool_outputs=[], used_tools=["fundamentals"],
                candidate_context=candidate_context,
            )
        assert result["grounded"] is True
        assert result["unsupported_claims"] == []

    @pytest.mark.asyncio
    async def test_genuinely_fabricated_claim_still_fails_grounding(self):
        candidate_context = "title: SBI Life Insurance Q1 Results: Net profit rises 22% YoY to Rs 725 crore\n"
        verdict_step = {
            "bull": "5G partnership announced with a major telecom player.",
            "bear": "", "thesis": "", "thought": "",
        }
        llm_resp = '{"unsupported_claims": ["5G partnership announced with a major telecom player."], "grounded": false}'
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=llm_resp)):
            result = await de._check_grounding(
                "SBILIFE.NS", verdict_step, tool_outputs=[], used_tools=["fundamentals"],
                candidate_context=candidate_context,
            )
        assert result["grounded"] is False
        assert "5G partnership announced with a major telecom player." in result["unsupported_claims"]


class TestFilterOpinionSynthesisClaims:
    """engine/agent/decision_engine.py::_filter_opinion_synthesis_claims()
    -- added 2026-07-24 after confirming live (ROUTE.NS) that the LLM
    fact-checker layer sometimes flags a pure conclusion/opinion sentence
    ("bearish earnings justify a short position") as an unsupported claim,
    even though its own prompt says reasoning/synthesis is fine."""

    def test_justify_conclusion_with_no_number_or_event_term_is_dropped(self):
        claims = ["Bearish earnings justify short position."]
        assert de._filter_opinion_synthesis_claims(claims) == []

    def test_supports_a_trade_conclusion_is_dropped(self):
        claims = ["Current downtrend supports a short (SELL) trade."]
        assert de._filter_opinion_synthesis_claims(claims) == []

    def test_synthesis_marker_with_a_number_is_kept(self):
        # A number inside reasoning-shaped prose could still be a fabricated
        # figure -- must not be exempted just because of the marker phrase.
        claims = ["A 45% margin expansion justifies a long position."]
        assert de._filter_opinion_synthesis_claims(claims) == claims

    def test_synthesis_marker_with_tracked_event_term_is_kept(self):
        # "acquisition" is tracked event vocabulary -- must still go through
        # _deterministic_entity_overlap_check regardless of phrasing.
        claims = ["The rumored acquisition supports a bullish position."]
        assert de._filter_opinion_synthesis_claims(claims) == claims

    def test_claim_with_no_synthesis_marker_is_kept(self):
        claims = ["Weak ROE and ROCE."]
        assert de._filter_opinion_synthesis_claims(claims) == claims

    def test_mixed_batch_only_pure_synthesis_dropped(self):
        claims = [
            "Bearish earnings justify short position.",
            "Revenue growth over 3 years is 30%.",
        ]
        result = de._filter_opinion_synthesis_claims(claims)
        assert result == ["Revenue growth over 3 years is 30%."]

    def test_empty_claims_list_returns_empty(self):
        assert de._filter_opinion_synthesis_claims([]) == []


class TestCheckGroundingOpinionSynthesisFalsePositive:
    """Integration-level regression guard for the exact ROUTE.NS false
    positive."""

    @pytest.mark.asyncio
    async def test_llm_flagging_a_pure_conclusion_does_not_fail_grounding(self):
        verdict_step = {
            "bull": "", "bear": "Bearish earnings justify short position.",
            "thesis": "Current downtrend supports a short (SELL) trade.", "thought": "",
        }
        llm_resp = (
            '{"unsupported_claims": ["Bearish earnings justify short position.", '
            '"Current downtrend supports a short (SELL) trade."], "grounded": false}'
        )
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=llm_resp)):
            result = await de._check_grounding(
                "ROUTE.NS", verdict_step, tool_outputs=[], used_tools=["fundamentals"],
                candidate_context="Route Mobile Q1 PAT slides 40% to Rs 69 crore",
            )
        assert result["grounded"] is True
        assert result["unsupported_claims"] == []
