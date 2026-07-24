"""Regression tests for engine/sector_graph.py's 2nd-order relationship
quality bar.

Root-caused 2026-07-22: get_second_order_trades() validated only that
{ticker, action, reason} existed syntactically -- it never checked whether
the claimed relationship was real or strong. "Paras Defence wins a Madhya
Pradesh investment commitment" produced a cascade trade in Tata Chemicals
with no coherent causal link at all (LT.NS also got pulled in the same way).
These tests lock in the closed-set relationship_type check and the
relationship_strength/company_exposure thresholds that now filter such
candidates out before they ever reach a TradeIntent.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from engine.sector_graph import get_second_order_trades


def _llm_response(trades: list[dict]) -> str:
    return json.dumps(trades)


class TestRelationshipTypeClosedSet:
    @pytest.mark.asyncio
    async def test_valid_relationship_type_passes(self):
        trades = [{
            "ticker": "MOTHERSUMI.NS", "action": "BUY",
            "reason": "Direct auto-component supplier to TVS Motor",
            "relationship_type": "SUPPLIER", "relationship_strength": 0.8,
            "company_exposure": 0.5,
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert len(result) == 1
        assert result[0]["ticker"] == "MOTHERSUMI.NS"

    @pytest.mark.asyncio
    async def test_missing_relationship_type_dropped(self):
        trades = [{"ticker": "TATACHEM.NS", "action": "BUY", "reason": "vague sector link"}]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("PARAS.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_relationship_type_dropped(self):
        # THE exact TATACHEM/LT regression shape: a made-up or vague
        # relationship label that isn't in the closed set.
        trades = [{
            "ticker": "TATACHEM.NS", "action": "BUY",
            "reason": "Both benefit from government infrastructure spending",
            "relationship_type": "GOVERNMENT_THEME", "relationship_strength": 0.9,
            "company_exposure": 0.9,
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("PARAS.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_relationship_type_is_case_insensitive(self):
        trades = [{
            "ticker": "MOTHERSUMI.NS", "action": "BUY", "reason": "supplier",
            "relationship_type": "supplier", "relationship_strength": 0.8, "company_exposure": 0.5,
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert len(result) == 1
        assert result[0]["relationship_type"] == "SUPPLIER"


class TestQualityThresholds:
    @pytest.mark.asyncio
    async def test_weak_relationship_strength_dropped(self):
        trades = [{
            "ticker": "MOTHERSUMI.NS", "action": "BUY", "reason": "loosely related",
            "relationship_type": "SUPPLIER", "relationship_strength": 0.3,  # below 0.6 bar
            "company_exposure": 0.5,
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_low_company_exposure_dropped(self):
        trades = [{
            "ticker": "MOTHERSUMI.NS", "action": "BUY", "reason": "minor supplier relationship",
            "relationship_type": "SUPPLIER", "relationship_strength": 0.8,
            "company_exposure": 0.1,  # below 0.3 bar -- barely exposed to this relationship
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_missing_strength_fields_treated_as_zero_and_dropped(self):
        trades = [{"ticker": "MOTHERSUMI.NS", "action": "BUY", "reason": "x", "relationship_type": "SUPPLIER"}]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_non_numeric_strength_fields_treated_as_zero_and_dropped(self):
        trades = [{
            "ticker": "MOTHERSUMI.NS", "action": "BUY", "reason": "x",
            "relationship_type": "SUPPLIER", "relationship_strength": "high", "company_exposure": "a lot",
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_at_exact_thresholds_passes(self):
        trades = [{
            "ticker": "MOTHERSUMI.NS", "action": "BUY", "reason": "supplier",
            "relationship_type": "SUPPLIER", "relationship_strength": 0.6, "company_exposure": 0.3,
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert len(result) == 1


class TestExistingValidation:
    @pytest.mark.asyncio
    async def test_self_referencing_ticker_dropped(self):
        trades = [{
            "ticker": "TVSMOTOR.NS", "action": "BUY", "reason": "x",
            "relationship_type": "SUPPLIER", "relationship_strength": 0.9, "company_exposure": 0.9,
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_action_dropped(self):
        trades = [{
            "ticker": "MOTHERSUMI.NS", "action": "HOLD", "reason": "x",
            "relationship_type": "SUPPLIER", "relationship_strength": 0.9, "company_exposure": 0.9,
        }]
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=_llm_response(trades))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_list_response_returns_empty(self):
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value="[]")):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty_not_raises(self):
        with patch("utils.llm.call_llm_chat", AsyncMock(side_effect=RuntimeError("mantle down"))):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert result == []

    @pytest.mark.asyncio
    async def test_markdown_wrapped_json_still_parses(self):
        trades = [{
            "ticker": "MOTHERSUMI.NS", "action": "BUY", "reason": "supplier",
            "relationship_type": "SUPPLIER", "relationship_strength": 0.8, "company_exposure": 0.5,
        }]
        wrapped = f"```json\n{_llm_response(trades)}\n```"
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value=wrapped)):
            result = await get_second_order_trades("TVSMOTOR.NS", "headline", "summary", "positive")
        assert len(result) == 1
