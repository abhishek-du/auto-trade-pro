"""End-to-end integration tests for the News-Only pipeline's real entry
point: news_discovery_engine.py::process_ticker().

Every stage of this pipeline (event classification/dedup, the LLM ReAct
loop, grounding, the decision-router gate, execution) already has thorough
unit coverage in its own test file (test_event_pipeline.py, test_llm_loop.py,
test_grounding.py, test_decision_router.py, test_execution.py). What none of
those prove is that the pieces are WIRED together correctly — that a
canonical event's materiality/direction genuinely flows all the way through
to a real TradeIntent and gets approved, or that a rejection at any single
stage genuinely stops the whole pipeline rather than silently falling
through to execution anyway. These tests mock only the leaf I/O (LLM API
calls, DB sessions, wallet/risk-validation, live price) and let every real
orchestration function in between run for real:

    NewsItem/headline -> _build_evidence (classify_event/dedup)
        -> NewsCandidate -> llm_tooluse_candidate (ReAct + grounding)
        -> validate_evidence_consistency (thesis-vs-canonical)
        -> _execute_news_trade -> TradeIntent -> authorize_trade_intent
        -> execute_trade_intent -> open_paper_trade

This matches the four scenarios explicitly requested: full APPROVED flow,
REJECTED via thesis-vs-canonical drift, REJECTED via the LLM/grounding loop
itself, and BLOCKED via NO EVENT -> NO TRADE. The TECHNICAL hard-block
scenario is deliberately NOT duplicated here -- process_ticker() only ever
constructs EVENT_DRIVEN intents, so that path is already exhaustively
covered (and mutation-tested) at the decision_router level.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from news_discovery_engine import process_ticker


_STUB_TOOL_NAMES = (
    "fundamentals", "company_intelligence", "sector", "price_action",
    "market_depth", "intraday_candles", "options", "news", "macro", "predict_candle",
)


def _make_stub_tools() -> dict:
    async def _stub(symbol: str, _name: str) -> str:
        return f"{_name}: stub result for {symbol}"
    return {name: (lambda symbol, _n=name: _stub(symbol, _n)) for name in _STUB_TOOL_NAMES}


def tool_step(tool: str) -> str:
    return f'{{"action": "tool", "tool": "{tool}", "thought": "gathering evidence"}}'


def decide_step(verdict: str = "TAKE", confidence: int = 82, bull: str = "solid setup") -> str:
    return (
        f'{{"action": "decide", "verdict": "{verdict}", "confidence": {confidence}, '
        f'"bull": "{bull}", "bear": "some risk", "key_risk": "volatility", '
        f'"thesis": "{bull}", "market_confirmation": "NEUTRAL"}}'
    )


_CORE_TOOLS_WITH_EVENT = ["fundamentals", "company_intelligence", "sector", "price_action", "market_depth", "intraday_candles", "options"]


def _mock_session_ctx(session=None):
    session = session or AsyncMock()
    session.add = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx), session


def _make_canonical_event(id=2848, materiality="HIGH", bullish=None, bearish=None, confidence=0.85):
    return SimpleNamespace(
        id=id, country=materiality, confidence=confidence,
        bullish_stocks=bullish or [], bearish_stocks=bearish or [], event_title="EARNINGS",
    )


class TestFullApprovedFlow:
    @pytest.mark.asyncio
    async def test_grounded_take_verdict_matching_canonical_event_executes(self):
        canonical = _make_canonical_event(bullish=["TESTCO"])
        # AsyncSessionLocal is the SAME imported name used both by
        # _find_canonical_event()'s dedup lookup and, later, by
        # _verify_canonical_event()'s own re-fetch (via session.get()) inside
        # authorize_trade_intent() -- both must resolve the same canonical
        # row, so a single shared session mock backs both.
        shared_session = AsyncMock()
        shared_session.get = AsyncMock(return_value=canonical)
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = []
        shared_session.execute = AsyncMock(return_value=exec_result)
        find_ctx, _ = _mock_session_ctx(shared_session)

        llm_responses = [tool_step(t) for t in _CORE_TOOLS_WITH_EVENT] + [decide_step()]

        async def _fake_open_paper_trade(signal, position_size, session):
            return SimpleNamespace(id=123)

        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=(canonical, "matched headline"))), \
             patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("utils.llm.call_llm_chat", AsyncMock(side_effect=llm_responses)), \
             patch("engine.agent.decision_engine._LLM_TOOLS", _make_stub_tools()), \
             patch("engine.agent.decision_engine._candidate_context", AsyncMock(return_value="CONTEXT")), \
             patch("engine.agent.decision_engine._check_grounding",
                   AsyncMock(return_value={"grounded": True, "unsupported_claims": []})), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=100.0, source="zerodha_rest", fetched_at_ist="t"))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.resolve_mode", AsyncMock(return_value=__import__("engine.decision_router", fromlist=["TradeMode"]).TradeMode.PAPER)), \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary", AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("engine.risk_manager.validate_signal", AsyncMock(return_value=(True, "ok"))), \
             patch("engine.risk_manager.calculate_position_size", MagicMock(return_value={"units": 1, "usd_value": 100.0})), \
             patch("paper_trading.trade_simulator.open_paper_trade", AsyncMock(side_effect=_fake_open_paper_trade)):
            result = await process_ticker("TESTCO.NS", "BUY", "TVS-style Q1 results headline", "summary")

        assert result is True

    @pytest.mark.asyncio
    async def test_second_order_cascade_skipped_when_no_graph_trades(self):
        # Same happy path, but confirms get_second_order_trades() returning
        # nothing doesn't error the primary trade's success out.
        canonical = _make_canonical_event(bullish=["TESTCO"])
        shared_session = AsyncMock()
        shared_session.get = AsyncMock(return_value=canonical)
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = []
        shared_session.execute = AsyncMock(return_value=exec_result)
        find_ctx, _ = _mock_session_ctx(shared_session)

        llm_responses = [tool_step(t) for t in _CORE_TOOLS_WITH_EVENT] + [decide_step()]

        async def _fake_open_paper_trade(signal, position_size, session):
            return SimpleNamespace(id=124)

        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=(canonical, "matched headline"))), \
             patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("utils.llm.call_llm_chat", AsyncMock(side_effect=llm_responses)), \
             patch("engine.agent.decision_engine._LLM_TOOLS", _make_stub_tools()), \
             patch("engine.agent.decision_engine._candidate_context", AsyncMock(return_value="CONTEXT")), \
             patch("engine.agent.decision_engine._check_grounding",
                   AsyncMock(return_value={"grounded": True, "unsupported_claims": []})), \
             patch("crawler.market_snapshot.get_market_snapshot",
                   AsyncMock(return_value=SimpleNamespace(ltp=100.0, source="zerodha_rest", fetched_at_ist="t"))), \
             patch("news_discovery_engine._compute_news_trade_levels",
                   AsyncMock(return_value={"stop_loss": 95.0, "target_1": 110.0, "target_2": 120.0, "atr": 2.0, "source": "atr", "gap_pct": 0.0})), \
             patch("engine.decision_router.resolve_mode", AsyncMock(return_value=__import__("engine.decision_router", fromlist=["TradeMode"]).TradeMode.PAPER)), \
             patch("paper_trading.virtual_wallet.VirtualWallet.get_summary", AsyncMock(return_value={"balance": 1_000_000.0})), \
             patch("engine.risk_manager.validate_signal", AsyncMock(return_value=(True, "ok"))), \
             patch("engine.risk_manager.calculate_position_size", MagicMock(return_value={"units": 1, "usd_value": 100.0})), \
             patch("paper_trading.trade_simulator.open_paper_trade", AsyncMock(side_effect=_fake_open_paper_trade)), \
             patch("engine.sector_graph.get_second_order_trades", AsyncMock(return_value=[])):
            result = await process_ticker("TESTCO.NS", "BUY", "headline", "summary")
        assert result is True


class TestRejectedViaThesisCanonicalDrift:
    @pytest.mark.asyncio
    async def test_low_materiality_event_with_high_conviction_thesis_rejected(self):
        # The exact ULTRACEMCO-class scenario referenced in process_ticker()'s
        # own comments: materiality=LOW canonical event, but the LLM's thesis
        # uses high-conviction language ("earnings beat") with high
        # confidence -- validate_evidence_consistency() must reject this
        # BEFORE a TradeIntent is ever built (i.e. _execute_news_trade must
        # never be called).
        canonical = _make_canonical_event(materiality="LOW", bullish=["TESTCO"])
        find_ctx, _ = _mock_session_ctx()

        llm_responses = [tool_step(t) for t in _CORE_TOOLS_WITH_EVENT] + [
            decide_step(confidence=85, bull="Strong earnings beat drives this rally")
        ]

        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=(canonical, "matched headline"))), \
             patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("utils.llm.call_llm_chat", AsyncMock(side_effect=llm_responses)), \
             patch("engine.agent.decision_engine._LLM_TOOLS", _make_stub_tools()), \
             patch("engine.agent.decision_engine._candidate_context", AsyncMock(return_value="CONTEXT")), \
             patch("engine.agent.decision_engine._check_grounding",
                   AsyncMock(return_value={"grounded": True, "unsupported_claims": []})), \
             patch("news_discovery_engine._execute_news_trade", AsyncMock()) as mock_execute:
            result = await process_ticker("TESTCO.NS", "BUY", "headline", "summary")

        assert result is False
        mock_execute.assert_not_called()


class TestRejectedViaLLMGroundingLoop:
    @pytest.mark.asyncio
    async def test_persistently_ungrounded_verdict_never_reaches_execution(self):
        canonical = _make_canonical_event(bullish=["TESTCO"])
        find_ctx, _ = _mock_session_ctx()

        llm_responses = [tool_step(t) for t in _CORE_TOOLS_WITH_EVENT] + [decide_step(), decide_step()]

        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=(canonical, "matched headline"))), \
             patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("utils.llm.call_llm_chat", AsyncMock(side_effect=llm_responses)), \
             patch("engine.agent.decision_engine._LLM_TOOLS", _make_stub_tools()), \
             patch("engine.agent.decision_engine._candidate_context", AsyncMock(return_value="CONTEXT")), \
             patch("engine.agent.decision_engine._check_grounding",
                   AsyncMock(return_value={"grounded": False, "unsupported_claims": ["fabricated catalyst"]})), \
             patch("news_discovery_engine._execute_news_trade", AsyncMock()) as mock_execute:
            result = await process_ticker("TESTCO.NS", "BUY", "headline", "summary")

        assert result is False
        mock_execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_verdict_never_reaches_execution(self):
        canonical = _make_canonical_event(bullish=["TESTCO"])
        find_ctx, _ = _mock_session_ctx()
        llm_responses = [tool_step(t) for t in _CORE_TOOLS_WITH_EVENT] + [decide_step(verdict="SKIP")]

        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=(canonical, "matched headline"))), \
             patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("utils.llm.call_llm_chat", AsyncMock(side_effect=llm_responses)), \
             patch("engine.agent.decision_engine._LLM_TOOLS", _make_stub_tools()), \
             patch("engine.agent.decision_engine._candidate_context", AsyncMock(return_value="CONTEXT")), \
             patch("engine.agent.decision_engine._check_grounding",
                   AsyncMock(return_value={"grounded": True, "unsupported_claims": []})), \
             patch("news_discovery_engine._execute_news_trade", AsyncMock()) as mock_execute:
            result = await process_ticker("TESTCO.NS", "BUY", "headline", "summary")

        assert result is False
        mock_execute.assert_not_called()


class TestBlockedViaNoEvent:
    @pytest.mark.asyncio
    async def test_classification_failure_blocks_before_any_llm_call(self):
        find_ctx, _ = _mock_session_ctx()
        llm_mock = AsyncMock()

        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=None)), \
             patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("engine.event_classifier.classify_event", AsyncMock(return_value=None)), \
             patch("utils.llm.call_llm_chat", llm_mock):
            result = await process_ticker("TESTCO.NS", "BUY", "headline", "summary")

        assert result is False
        # "no event, no trade" must short-circuit BEFORE spending an LLM call
        # deliberating over an untradeable candidate.
        llm_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_persistence_failure_blocks_before_any_llm_call(self):
        find_ctx, session = _mock_session_ctx()
        session.commit = AsyncMock(side_effect=RuntimeError("db unavailable"))
        classification = SimpleNamespace(
            category="EARNINGS_BEAT", impact="HIGH", confidence=0.9, bullish=True,
            surprise_score=80, expected_half_life_hours=48,
            entities={"companies": [], "sectors": []},
        )
        llm_mock = AsyncMock()

        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=None)), \
             patch("news_discovery_engine.AsyncSessionLocal", find_ctx), \
             patch("engine.event_classifier.classify_event", AsyncMock(return_value=classification)), \
             patch("utils.llm.call_llm_chat", llm_mock):
            result = await process_ticker("TESTCO.NS", "BUY", "headline", "summary")

        assert result is False
        llm_mock.assert_not_called()
