"""Regression tests for the ReAct tool-use orchestration loop in
engine/agent/decision_engine.py::llm_tooluse_candidate().

Zero coverage existed before this file (2026-07-21 coverage audit) — the
only things that ever exercised this function were manual, unasserted
smoke-test scripts (test_tooluse.py, test_eagle_eye.py) that hit the live
LLM/Kite APIs and never ran under pytest. These tests mock call_llm_chat
with scripted response sequences and the tool table with instant stubs, so
the ORCHESTRATION logic (core-tool enforcement, canonical-event tool
blocking, grounding retry, and this session's own empty-response retry fix)
is locked in deterministically, in-process, with no network/LLM cost.

_check_grounding() itself is mocked here (its correctness is covered
separately and thoroughly in tests/test_grounding.py) so these tests stay
focused on the loop's control flow, not grounding logic.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from engine.agent.decision_engine import llm_tooluse_candidate, get_last_tooluse_rejection_reason


def make_candidate(has_event: bool = True):
    return SimpleNamespace(
        strategy="NEWS_DIRECT", entry=100.0, stop=95.0, target=110.0, risk_reward=2.5,
        hub_subscores={}, evidence=(object() if has_event else None),
        event_id=1 if has_event else None,
    )


def make_decision():
    return SimpleNamespace(action="BUY", regime="NEUTRAL", master_score=75, confidence=70, confidence_factors={})


# All core tools across both has-event/no-event variants, plus a couple of
# extras (macro/predict_candle) some scripted sequences exercise.
_STUB_TOOL_NAMES = (
    "fundamentals", "company_intelligence", "sector", "price_action",
    "market_depth", "intraday_candles", "options", "news", "macro", "predict_candle",
)


def _make_stub_tools() -> dict:
    async def _stub(symbol: str, _name="") -> str:
        return f"{_name}: stub result for {symbol}"
    return {name: (lambda symbol, _n=name: _stub(symbol, _n)) for name in _STUB_TOOL_NAMES}


def tool_step(tool: str, thought: str = "need data") -> str:
    return f'{{"action": "tool", "tool": "{tool}", "thought": "{thought}"}}'


def decide_step(verdict: str = "TAKE", confidence: int = 80) -> str:
    return (
        f'{{"action": "decide", "verdict": "{verdict}", "confidence": {confidence}, '
        f'"bull": "solid setup", "bear": "some risk", "key_risk": "volatility", '
        f'"thesis": "grounded in tool data", "market_confirmation": "NEUTRAL"}}'
    )


_ALL_EVENT_CORE_TOOLS = ["fundamentals", "company_intelligence", "sector", "price_action", "market_depth", "intraday_candles", "options"]
_ALL_NO_EVENT_CORE_TOOLS = ["fundamentals", "company_intelligence", "sector", "price_action", "market_depth", "intraday_candles", "news"]


def _patch_common(llm_responses, grounding_result=None):
    """Common patch set for llm_tooluse_candidate orchestration tests."""
    grounding_mock = AsyncMock(
        return_value=grounding_result or {"grounded": True, "unsupported_claims": []}
    )
    return (
        patch("utils.llm.call_llm_chat", AsyncMock(side_effect=llm_responses)),
        patch("engine.agent.decision_engine._LLM_TOOLS", _make_stub_tools()),
        patch("engine.agent.decision_engine._candidate_context", AsyncMock(return_value="CONTEXT")),
        patch("engine.agent.decision_engine._check_grounding", grounding_mock),
    )


class TestCoreToolEnforcement:
    @pytest.mark.asyncio
    async def test_premature_decide_forces_remaining_core_tools(self):
        responses = (
            [decide_step()]  # premature -- no tools called yet
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS]
            + [decide_step()]
        )
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert result["verdict"] == "TAKE"
        assert set(_ALL_EVENT_CORE_TOOLS) <= set(result["tools_used"])

    @pytest.mark.asyncio
    async def test_partial_core_tools_still_forces_the_rest(self):
        # Model calls SOME core tools, then tries to decide -- must be forced
        # to call the remaining ones too (identity check, not count check --
        # this is the exact bug this session's earlier fix addressed: a
        # count-based check let a model substitute non-core tools instead).
        responses = (
            [tool_step("fundamentals"), tool_step("price_action")]
            + [decide_step()]  # premature -- only 2/7 core tools called
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS if t not in ("fundamentals", "price_action")]
            + [decide_step()]
        )
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert set(_ALL_EVENT_CORE_TOOLS) <= set(result["tools_used"])


class TestCoreToolIdentityNotCount:
    @pytest.mark.asyncio
    async def test_five_distinct_tools_missing_some_core_ones_do_not_satisfy_requirement(self):
        # THE regression guard for the count-vs-identity bug fixed earlier
        # this session: the OLD check was `len(set(used)) < 5`, satisfied by
        # ANY 5 distinct tools regardless of which ones. Here the model
        # calls exactly 5 DISTINCT tools (3 core + 2 non-core) -- enough to
        # satisfy the old count check (5 is not < 5) -- while still missing
        # 4 real core tools (company_intelligence, market_depth,
        # intraday_candles, options). The current identity-based check must
        # still force the rest.
        five_distinct = ["fundamentals", "sector", "price_action", "macro", "predict_candle"]
        responses = (
            [tool_step(t) for t in five_distinct]
            + [decide_step()]  # premature -- old count check (5 distinct >= 5) would have allowed this
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS if t not in five_distinct]
            + [decide_step()]
        )
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert set(_ALL_EVENT_CORE_TOOLS) <= set(result["tools_used"])


class TestCanonicalEventToolBlocking:
    @pytest.mark.asyncio
    async def test_news_tool_blocked_when_canonical_event_exists(self):
        responses = (
            [tool_step("news")]  # tries the blocked tool first
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS]
            + [decide_step()]
        )
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        # "news" must not appear as a successfully-used tool -- it was BLOCKED,
        # not silently executed.
        assert "news" not in result["tools_used"]

    @pytest.mark.asyncio
    async def test_news_tool_available_when_no_canonical_event(self):
        responses = [tool_step(t) for t in _ALL_NO_EVENT_CORE_TOOLS] + [decide_step()]
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=False), make_decision())
        assert result is not None
        assert "news" in result["tools_used"]


class TestGroundingRetry:
    @pytest.mark.asyncio
    async def test_one_retry_allowed_then_succeeds(self):
        responses = (
            [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS]
            + [decide_step(), decide_step()]  # first ungrounded, second grounded
        )
        grounding_mock = AsyncMock(side_effect=[
            {"grounded": False, "unsupported_claims": ["fabricated catalyst"]},
            {"grounded": True, "unsupported_claims": []},
        ])
        p1, p2, p3, _ = _patch_common(responses)
        with p1, p2, p3, patch("engine.agent.decision_engine._check_grounding", grounding_mock):
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert result["verdict"] == "TAKE"

    @pytest.mark.asyncio
    async def test_second_ungrounded_verdict_rejects_outright(self):
        responses = (
            [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS]
            + [decide_step(), decide_step()]  # both ungrounded
        )
        grounding_mock = AsyncMock(return_value={"grounded": False, "unsupported_claims": ["fabricated catalyst"]})
        p1, p2, p3, _ = _patch_common(responses)
        with p1, p2, p3, patch("engine.agent.decision_engine._check_grounding", grounding_mock):
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is None


class TestEmptyResponseRetry:
    @pytest.mark.asyncio
    async def test_transient_empty_response_recovers_within_same_round(self):
        # This session's own reliability fix: an empty/unparseable response
        # (Mantle "reasoning consumed the budget" or a transport error) on
        # ANY round must retry the SAME round up to 3x before giving up --
        # not be treated as an immediate, silent SKIP.
        responses = (
            ["", tool_step("fundamentals")]  # round 1: empty, then recovers
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS if t != "fundamentals"]
            + ["not json at all", decide_step()]  # a later round also recovers
        )
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4, patch("asyncio.sleep", AsyncMock()):
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert result["verdict"] == "TAKE"

    @pytest.mark.asyncio
    async def test_three_consecutive_empty_responses_gives_up(self):
        responses = ["", "", ""]
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4, patch("asyncio.sleep", AsyncMock()):
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is None


class TestUnknownToolAndRoundLimit:
    @pytest.mark.asyncio
    async def test_unknown_tool_name_does_not_crash_loop(self):
        responses = (
            [tool_step("not_a_real_tool")]
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS]
            + [decide_step()]
        )
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert "not_a_real_tool" not in result["tools_used"]

    @pytest.mark.asyncio
    async def test_never_deciding_within_max_rounds_returns_none(self):
        # 15 rounds all "tool" -- never decides -- must return None, not loop
        # forever or raise.
        responses = [tool_step("fundamentals") for _ in range(20)]
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is None


class TestCrossRoundToolDedup:
    """2026-07-23 round-exhaustion fix: confirmed live (e.g. TRISHIK.NS,
    INDOSM.NS) that a tool could be re-requested by the model several rounds
    after its first use, burning a whole extra round re-executing identical
    work. The loop must feed back the cached prior result instead of
    re-invoking the tool a second time."""

    @pytest.mark.asyncio
    async def test_repeated_tool_call_is_not_re_executed(self):
        call_count = {"fundamentals": 0}

        async def _counting_fundamentals(symbol: str) -> str:
            call_count["fundamentals"] += 1
            return f"fundamentals: result #{call_count['fundamentals']} for {symbol}"

        tools = _make_stub_tools()
        tools["fundamentals"] = _counting_fundamentals

        responses = (
            [tool_step("fundamentals")]  # first call -- real execution
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS if t != "fundamentals"]
            + [tool_step("fundamentals")]  # repeat -- must NOT re-execute
            + [decide_step()]
        )
        with patch("utils.llm.call_llm_chat", AsyncMock(side_effect=responses)), \
             patch("engine.agent.decision_engine._LLM_TOOLS", tools), \
             patch("engine.agent.decision_engine._candidate_context", AsyncMock(return_value="CONTEXT")), \
             patch("engine.agent.decision_engine._check_grounding",
                   AsyncMock(return_value={"grounded": True, "unsupported_claims": []})):
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert call_count["fundamentals"] == 1  # NOT 2 -- the repeat was served from cache

    @pytest.mark.asyncio
    async def test_repeated_tool_call_feeds_back_a_note_not_silence(self):
        # The model must be told explicitly it already has this data, not
        # just get an empty/blocked response with no explanation.
        captured_messages: list = []

        real_call_llm_chat = AsyncMock()

        async def _capturing_side_effect(messages, **kwargs):
            captured_messages.append(list(messages))
            return next(_response_iter)

        responses = (
            [tool_step("fundamentals")]
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS if t != "fundamentals"]
            + [tool_step("fundamentals"), decide_step()]
        )
        _response_iter = iter(responses)
        real_call_llm_chat.side_effect = _capturing_side_effect

        with patch("utils.llm.call_llm_chat", real_call_llm_chat), \
             patch("engine.agent.decision_engine._LLM_TOOLS", _make_stub_tools()), \
             patch("engine.agent.decision_engine._candidate_context", AsyncMock(return_value="CONTEXT")), \
             patch("engine.agent.decision_engine._check_grounding",
                   AsyncMock(return_value={"grounded": True, "unsupported_claims": []})):
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        all_user_content = " ".join(
            m["content"] for msgs in captured_messages for m in msgs if m.get("role") == "user"
        )
        assert "already called" in all_user_content

    @pytest.mark.asyncio
    async def test_repeated_tool_does_not_duplicate_tools_used_entry(self):
        responses = (
            [tool_step("fundamentals")]
            + [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS if t != "fundamentals"]
            + [tool_step("fundamentals"), decide_step()]
        )
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert result["tools_used"].count("fundamentals") == 1


class TestRejectionReasonSurfacing:
    """2026-07-23 fix: llm_tooluse_candidate() returning None used to be
    indistinguishable to callers -- four genuinely different situations
    (empty/unparseable LLM output, a grounding rejection, real
    round-exhaustion, an unexpected exception) all produced the exact same
    generic "Agent failed to reach a decision (Timed out/Insufficient info)"
    message downstream. Live-tested 2026-07-23: 3 of 7 real candidates in one
    run showed this misleading generic text while the real reason (a
    grounding rejection) was sitting in the debug log, invisible to anyone
    reading the trade-rejection reason alone. get_last_tooluse_rejection_reason()
    now surfaces the real one, mirroring utils.llm.get_last_reasoning()'s
    existing contextvar pattern."""

    @pytest.mark.asyncio
    async def test_empty_response_giveup_sets_specific_reason(self):
        responses = ["", "", ""]
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4, patch("asyncio.sleep", AsyncMock()):
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is None
        reason = get_last_tooluse_rejection_reason()
        assert reason is not None and "empty" in reason.lower()

    @pytest.mark.asyncio
    async def test_grounding_rejection_sets_specific_reason_not_generic(self):
        responses = (
            [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS]
            + [decide_step(), decide_step()]  # both ungrounded
        )
        grounding_mock = AsyncMock(return_value={
            "grounded": False, "unsupported_claims": ["fabricated ₹800cr figure"],
        })
        p1, p2, p3, _ = _patch_common(responses)
        with p1, p2, p3, patch("engine.agent.decision_engine._check_grounding", grounding_mock):
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is None
        reason = get_last_tooluse_rejection_reason()
        assert reason is not None
        assert "ungrounded" in reason.lower() or "grounded" in reason.lower()
        assert "fabricated ₹800cr figure" in reason
        # Must NOT be the old generic, misleading message.
        assert "timed out" not in reason.lower()

    @pytest.mark.asyncio
    async def test_genuine_round_exhaustion_sets_specific_reason(self):
        # 15 rounds, all "tool" -- never reaches "decide" -- genuine
        # round-exhaustion, distinct from every other None-path.
        responses = [tool_step("fundamentals") for _ in range(20)]
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is None
        reason = get_last_tooluse_rejection_reason()
        assert reason is not None
        assert "round" in reason.lower() or "exhaust" in reason.lower()

    @pytest.mark.asyncio
    async def test_successful_decide_leaves_no_stale_rejection_reason(self):
        responses = [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS] + [decide_step()]
        p1, p2, p3, p4 = _patch_common(responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert get_last_tooluse_rejection_reason() is None

    @pytest.mark.asyncio
    async def test_reason_cleared_at_start_of_each_call(self):
        # A prior call's rejection reason must not leak into a later,
        # successful call's read of the contextvar.
        fail_responses = ["", "", ""]
        p1, p2, p3, p4 = _patch_common(fail_responses)
        with p1, p2, p3, p4, patch("asyncio.sleep", AsyncMock()):
            await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert get_last_tooluse_rejection_reason() is not None

        success_responses = [tool_step(t) for t in _ALL_EVENT_CORE_TOOLS] + [decide_step()]
        p1, p2, p3, p4 = _patch_common(success_responses)
        with p1, p2, p3, p4:
            result = await llm_tooluse_candidate("TESTCO.NS", make_candidate(has_event=True), make_decision())
        assert result is not None
        assert get_last_tooluse_rejection_reason() is None
