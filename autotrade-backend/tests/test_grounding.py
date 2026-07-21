"""Regression tests for the LLM verdict grounding/anti-hallucination layer in
engine/agent/decision_engine.py.

This subsystem had ZERO test coverage before this file (2026-07-21 coverage
audit) despite being the most heavily and recently hand-modified code this
session: a capability-based provenance refactor (replacing an earlier
tool-name-based version that produced a real false-positive rejection on a
genuinely-grounded TVS Motor verdict), a numeric-consistency check that was
itself bug-fixed once already (magnitude-only -> category-aware, after it
missed a fabricated "1.2 L cr cash flow" claim), and a nearest-match fix to
_nearby_category (first-in-dict-order -> actually nearest to the figure).
All of that was validated only through one-off live pipeline runs; nothing
locks the behavior in place. These tests do.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.agent.decision_engine import (
    EvidenceCapability,
    _capabilities_from_tools,
    _check_grounding,
    _deterministic_entity_overlap_check,
    _deterministic_numeric_consistency_check,
    _deterministic_provenance_check,
    _nearby_category,
)


# ── _capabilities_from_tools ───────────────────────────────────────────────────

class TestCapabilitiesFromTools:
    def test_fundamentals_grants_financial_and_valuation(self):
        caps = _capabilities_from_tools(["fundamentals"])
        assert EvidenceCapability.FINANCIAL_PERFORMANCE in caps
        assert EvidenceCapability.VALUATION in caps

    def test_unknown_tool_grants_nothing(self):
        assert _capabilities_from_tools(["not_a_real_tool"]) == set()

    def test_empty_tool_list_grants_nothing(self):
        assert _capabilities_from_tools([]) == set()

    def test_capabilities_union_across_multiple_tools(self):
        caps = _capabilities_from_tools(["price_action", "options", "macro"])
        assert caps == {
            EvidenceCapability.PRICE_ACTION,
            EvidenceCapability.OPTIONS_FLOW,
            EvidenceCapability.MACRO,
        }


# ── _deterministic_provenance_check ────────────────────────────────────────────

class TestProvenanceCheck:
    def test_options_claim_without_options_tool_is_violation(self):
        violations = _deterministic_provenance_check("PCR is 0.75, max-pain at 1350", used_tools=[])
        assert violations
        assert any("OPTIONS_FLOW" in v for v in violations)

    def test_options_claim_with_options_tool_called_is_clean(self):
        violations = _deterministic_provenance_check("PCR is 0.75, max-pain at 1350", used_tools=["options"])
        assert violations == []

    def test_macro_claim_without_macro_tool_is_violation(self):
        violations = _deterministic_provenance_check("Strong net FII inflow supports the move", used_tools=["price_action"])
        assert violations
        assert any("MACRO" in v for v in violations)

    def test_macro_claim_with_macro_tool_called_is_clean(self):
        violations = _deterministic_provenance_check("Strong net FII inflow supports the move", used_tools=["macro"])
        assert violations == []

    def test_earnings_beat_claim_satisfied_by_fundamentals_alias(self):
        # THE regression guard: this is the exact false-positive the
        # 2026-07-21 capability refactor fixed. `earnings` is a literal
        # alias of `fundamentals` (_tool_earnings_report just relabels
        # _tool_fundamentals's output) -- a verdict citing "earnings beat"
        # after calling `fundamentals` (not `earnings` by that exact name)
        # must NOT be rejected.
        violations = _deterministic_provenance_check(
            "Q1 earnings beat expectations on strong volume growth", used_tools=["fundamentals"],
        )
        assert violations == []

    def test_earnings_beat_claim_satisfied_by_company_intelligence(self):
        violations = _deterministic_provenance_check(
            "Q1 earnings beat expectations", used_tools=["company_intelligence"],
        )
        assert violations == []

    def test_earnings_beat_claim_without_any_financial_tool_is_violation(self):
        violations = _deterministic_provenance_check(
            "Q1 earnings beat expectations", used_tools=["price_action", "market_depth"],
        )
        assert violations
        assert any("FINANCIAL_PERFORMANCE" in v for v in violations)

    def test_analyst_rating_claim_always_a_violation(self):
        # No tool in this system provides analyst ratings/targets at all --
        # must be flagged regardless of which tools were called.
        violations = _deterministic_provenance_check(
            "Brokerage upgrade with target price raised to 4200",
            used_tools=["fundamentals", "company_intelligence", "macro", "options"],
        )
        assert violations
        assert any("analyst ratings" in v for v in violations)

    def test_plain_claim_with_no_capability_terms_is_clean(self):
        violations = _deterministic_provenance_check(
            "Price action shows a fresh breakout from consolidation", used_tools=[],
        )
        assert violations == []


# ── _deterministic_entity_overlap_check ────────────────────────────────────────

class TestEntityOverlapCheck:
    def test_invented_partnership_not_in_evidence_is_violation(self):
        violations = _deterministic_entity_overlap_check(
            "New 5G partnership announced with a global telecom major",
            evidence_pool="price_action: LIVE LTP=100 5d_return=2%",
        )
        assert violations
        assert any("partnership" in v for v in violations)

    def test_partnership_present_in_evidence_is_clean(self):
        evidence = "news: TVS Motor announces manufacturing partnership with XYZ Corp"
        violations = _deterministic_entity_overlap_check(
            "The manufacturing partnership announced today drives the thesis", evidence_pool=evidence,
        )
        assert violations == []

    def test_case_insensitive_match(self):
        violations = _deterministic_entity_overlap_check(
            "Regulatory Approval received for the new plant", evidence_pool="regulatory approval granted per company filing",
        )
        assert violations == []

    def test_multiple_terms_partial_overlap(self):
        evidence = "news: company announced a buyback of shares worth 500cr"
        violations = _deterministic_entity_overlap_check(
            "The buyback and the rumored acquisition both support the bull case", evidence_pool=evidence,
        )
        # "buyback" is grounded, "acquisition" is not
        assert len(violations) == 1
        assert "acquisition" in violations[0]

    def test_no_event_terms_cited_is_clean(self):
        violations = _deterministic_entity_overlap_check(
            "Strong technical setup with volume confirmation", evidence_pool="",
        )
        assert violations == []


# ── _nearby_category ────────────────────────────────────────────────────────────

class TestNearbyCategory:
    def test_single_category_keyword_detected(self):
        text = "cash flow of 1.2 L cr this quarter"
        pos = text.find("1.2")
        labels = _nearby_category(text, pos)
        assert labels == ("op_cash_flow", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow")

    def test_no_category_keyword_returns_none(self):
        text = "the stock price is 3792.0 today"
        pos = text.find("3792")
        assert _nearby_category(text, pos) is None

    def test_nearest_category_wins_when_two_present_in_window(self):
        # THE regression guard for the rfind-based fix: "cash flow" appears
        # earlier in the window, "revenue" appears immediately before the
        # figure -- the NEARER keyword (revenue) must be picked, not
        # whichever happens to iterate first in _FIGURE_CATEGORY_LABELS.
        text = "cash flow was strong, and revenue of 1086181.0 crore was reported"
        pos = text.find("1086181")
        labels = _nearby_category(text, pos)
        assert labels == ("revenue",)

    def test_reversed_order_still_picks_nearest(self):
        text = "revenue was strong, and cash flow of 192113.0 crore was reported"
        pos = text.find("192113")
        labels = _nearby_category(text, pos)
        assert labels == ("op_cash_flow", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow")


# ── _deterministic_numeric_consistency_check ───────────────────────────────────

class TestNumericConsistencyCheck:
    def test_distorted_cash_flow_figure_is_violation(self):
        # The exact fabricated-figure scenario that motivated this check:
        # claim says "1.2 L cr" (120,000 cr) but real op_cash_flow=192113
        # (~1.92 L cr) -- ~37% off, well outside the 25% tolerance.
        claim = "Operating cash flow of 1.2 L cr signals strong liquidity"
        evidence = "company_intelligence: op_cash_flow=192113.0 revenue=886000.0"
        violations = _deterministic_numeric_consistency_check(claim, evidence)
        assert violations
        assert "cash flow" not in violations[0]  # message format uses "figure ~X crore", not the category label
        assert "crore" in violations[0]

    def test_matching_figure_within_tolerance_is_clean(self):
        claim = "Operating cash flow of 1.9 L cr signals strong liquidity"
        evidence = "company_intelligence: op_cash_flow=192113.0 revenue=886000.0"
        violations = _deterministic_numeric_consistency_check(claim, evidence)
        assert violations == []

    def test_category_scoping_avoids_false_negative_from_coincidental_collision(self):
        # This is the exact false-negative the magnitude-only version of
        # this check had: 120,000 cr (claimed cash flow) coincidentally
        # landed within 25% of a real but UNRELATED op_profit=123162 figure,
        # so the old broad-pool comparison missed a genuinely fabricated
        # cash-flow number. Category-scoping must catch it because
        # op_cash_flow (192113) is the only value it's allowed to compare
        # against once "cash flow" is recognized as the category.
        claim = "Operating cash flow of 1.2 L cr signals strong liquidity"
        evidence = "company_intelligence: op_cash_flow=192113.0 op_profit=123162.0"
        violations = _deterministic_numeric_consistency_check(claim, evidence)
        assert violations, "category-aware check must not be fooled by a coincidentally-close unrelated figure"

    def test_no_crore_figures_in_claim_is_clean(self):
        violations = _deterministic_numeric_consistency_check(
            "RSI is overbought at 78, R:R is 2.5", "price_action: LIVE LTP=3792.0",
        )
        assert violations == []

    def test_no_pool_values_available_is_clean(self):
        # Not this check's job to flag "no data" -- that's the entity-overlap
        # and provenance checks' territory.
        violations = _deterministic_numeric_consistency_check(
            "Revenue of 500 cr this quarter", "news: no figures available",
        )
        assert violations == []

    def test_uncategorized_figure_falls_back_to_broad_pool(self):
        claim = "The special dividend payout is worth 500 cr"  # not in _FIGURE_CATEGORY_LABELS
        evidence = "misc_note: reference figure of 550 noted elsewhere"  # bare number, ~9% off -- within tolerance via broad-pool fallback
        violations = _deterministic_numeric_consistency_check(claim, evidence)
        assert violations == []


# ── _check_grounding (async orchestration) ─────────────────────────────────────

class TestCheckGroundingOrchestration:
    @pytest.mark.asyncio
    async def test_clean_verdict_is_grounded(self):
        verdict = {"bull": "Strong price action", "bear": "thin volume", "thesis": "", "thought": ""}
        with patch(
            "utils.llm.call_llm_chat",
            AsyncMock(return_value='{"unsupported_claims": [], "grounded": true}'),
        ):
            result = await _check_grounding("TESTCO.NS", verdict, tool_outputs=["price_action: LTP=100"],
                                             used_tools=["price_action"], candidate_context="")
        assert result["grounded"] is True
        assert result["unsupported_claims"] == []

    @pytest.mark.asyncio
    async def test_deterministic_violation_rejects_even_if_llm_says_grounded(self):
        # Layer 1-3 findings must never be discardable by the LLM layer --
        # they are the reliable backbone, not the LLM's opinion.
        verdict = {"bull": "PCR shows bullish positioning", "bear": "", "thesis": "", "thought": ""}
        with patch(
            "utils.llm.call_llm_chat",
            AsyncMock(return_value='{"unsupported_claims": [], "grounded": true}'),
        ):
            result = await _check_grounding("TESTCO.NS", verdict, tool_outputs=[], used_tools=[], candidate_context="")
        assert result["grounded"] is False
        assert any("OPTIONS_FLOW" in c for c in result["unsupported_claims"])

    @pytest.mark.asyncio
    async def test_llm_layer_exception_fails_open_without_discarding_deterministic_findings(self):
        verdict = {"bull": "Net FII inflow drives the move", "bear": "", "thesis": "", "thought": ""}
        with patch("utils.llm.call_llm_chat", AsyncMock(side_effect=RuntimeError("Mantle timeout"))):
            result = await _check_grounding("TESTCO.NS", verdict, tool_outputs=[], used_tools=[], candidate_context="")
        assert result["grounded"] is False
        assert result.get("llm_layer_failed") is True
        assert any("MACRO" in c for c in result["unsupported_claims"])

    @pytest.mark.asyncio
    async def test_llm_layer_adds_its_own_findings_on_top_of_deterministic(self):
        verdict = {"bull": "Some free-text fabrication the fixed vocab can't catch", "bear": "", "thesis": "", "thought": ""}
        with patch(
            "utils.llm.call_llm_chat",
            AsyncMock(return_value='{"unsupported_claims": ["invented catalyst X"], "grounded": false}'),
        ):
            result = await _check_grounding("TESTCO.NS", verdict, tool_outputs=[], used_tools=[], candidate_context="")
        assert result["grounded"] is False
        assert "invented catalyst X" in result["unsupported_claims"]

    @pytest.mark.asyncio
    async def test_candidate_context_facts_are_not_flagged(self):
        # entry/stop/target/canonical-event facts given up-front are
        # legitimate inputs, not claims requiring tool-provenance --
        # confirmed via the entity-overlap layer (provenance/numeric checks
        # don't look at candidate_context, only entity-overlap does).
        verdict = {"bull": "The announced merger drives upside", "bear": "", "thesis": "", "thought": ""}
        context = "CANONICAL_EVENT: merger announced between X and Y, category=M&A"
        with patch(
            "utils.llm.call_llm_chat",
            AsyncMock(return_value='{"unsupported_claims": [], "grounded": true}'),
        ):
            result = await _check_grounding("TESTCO.NS", verdict, tool_outputs=[], used_tools=[], candidate_context=context)
        assert result["grounded"] is True
