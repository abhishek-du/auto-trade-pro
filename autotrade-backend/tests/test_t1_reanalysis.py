"""Regression tests for engine/agent/t1_reanalysis.py -- the T1-hit fresh
re-analysis feature (2026-07-22, user-requested): instead of always
mechanically booking a 50% partial and riding the rest to T2, a fresh LLM
analysis runs the MOMENT T1 is touched, deciding CONTINUE vs EXIT (full
close now, reversal risk -- watch for a real breakout before re-entering).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from engine.agent.t1_reanalysis import analyze_t1_hit


def _session_with(news=None, hub_row=None) -> AsyncMock:
    session = AsyncMock()
    news_result = AsyncMock()
    news_result.scalars = lambda: SimpleNamespace(all=lambda: news or [])
    hub_result = AsyncMock()
    hub_result.scalar_one_or_none = lambda: hub_row
    session.execute = AsyncMock(side_effect=[news_result, hub_result])
    return session


class TestDecisionParsing:
    @pytest.mark.asyncio
    async def test_continue_decision(self):
        session = _session_with()
        with patch("utils.llm.call_llm_chat", AsyncMock(
            return_value='{"decision": "CONTINUE", "watch_level": null, "reasoning": "momentum intact"}'
        )):
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["decision"] == "CONTINUE"
        assert result["watch_level"] is None

    @pytest.mark.asyncio
    async def test_exit_decision_with_watch_level(self):
        session = _session_with()
        with patch("utils.llm.call_llm_chat", AsyncMock(
            return_value='{"decision": "EXIT", "watch_level": 105.0, "reasoning": "exhaustion spike, weak hub"}'
        )):
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["decision"] == "EXIT"
        assert result["watch_level"] == 105.0
        assert "exhaustion" in result["reasoning"]

    @pytest.mark.asyncio
    async def test_exit_decision_missing_watch_level_is_none_not_error(self):
        session = _session_with()
        with patch("utils.llm.call_llm_chat", AsyncMock(
            return_value='{"decision": "EXIT", "reasoning": "reversal risk"}'
        )):
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["decision"] == "EXIT"
        assert result["watch_level"] is None

    @pytest.mark.asyncio
    async def test_exit_decision_non_numeric_watch_level_is_none(self):
        session = _session_with()
        with patch("utils.llm.call_llm_chat", AsyncMock(
            return_value='{"decision": "EXIT", "watch_level": "around 105", "reasoning": "x"}'
        )):
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["watch_level"] is None

    @pytest.mark.asyncio
    async def test_exit_decision_negative_watch_level_is_none(self):
        session = _session_with()
        with patch("utils.llm.call_llm_chat", AsyncMock(
            return_value='{"decision": "EXIT", "watch_level": -5.0, "reasoning": "x"}'
        )):
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["watch_level"] is None


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_llm_exception_fails_open_to_continue(self):
        session = _session_with()
        with patch("utils.llm.call_llm_chat", AsyncMock(side_effect=RuntimeError("mantle down"))):
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["decision"] == "CONTINUE"

    @pytest.mark.asyncio
    async def test_invalid_decision_value_fails_open_to_continue(self):
        session = _session_with()
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value='{"decision": "MAYBE", "reasoning": "x"}')):
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["decision"] == "CONTINUE"

    @pytest.mark.asyncio
    async def test_unparseable_response_fails_open_to_continue(self):
        session = _session_with()
        with patch("utils.llm.call_llm_chat", AsyncMock(return_value="not json at all")):
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["decision"] == "CONTINUE"

    @pytest.mark.asyncio
    async def test_context_fetch_failure_still_calls_llm(self):
        # A DB hiccup fetching news/hub context must not prevent the analysis
        # from running at all -- it should proceed with empty context.
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("db down"))
        with patch("utils.llm.call_llm_chat", AsyncMock(
            return_value='{"decision": "CONTINUE", "reasoning": "x"}'
        )) as mock_llm:
            result = await analyze_t1_hit(
                symbol="TESTCO.NS", direction="BUY", entry_price=100.0, price=110.0,
                t1=110.0, t2=120.0, unrealised_pct=10.0, session=session,
            )
        assert result["decision"] == "CONTINUE"
        mock_llm.assert_called_once()
