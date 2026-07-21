"""Regression tests for news_discovery_engine.py's event pipeline —
_leading_entity_tokens(), _find_canonical_event(), and _build_evidence().

Zero coverage existed before this file (2026-07-21 coverage audit). This is
where a real, live production bug was found and fixed this session: a
canonical-event dedup match crossed companies (TVS Motor's trade got
matched to a zeroed-out "DUPLICATE" stub CausalEvent whose actual news item
was about Aditya Birla Sun Life AMC), because template-heavy Indian
financial headlines ("X Q1 Results: profit rises N% YoY to Rs Y crore")
cross the difflib similarity threshold for two unrelated companies purely
from shared boilerplate. These tests lock in both guards that were added
to fix it: excluding DUPLICATE stub rows, and requiring a shared leading
entity token (the company name almost always leads the headline).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from news_discovery_engine import (
    _build_evidence,
    _find_canonical_event,
    _leading_entity_tokens,
)


# ── _leading_entity_tokens ─────────────────────────────────────────────────────

class TestLeadingEntityTokens:
    def test_extracts_company_name_tokens(self):
        tokens = _leading_entity_tokens(
            "TVS Motor Company Q1 Results: Revenue jumps 38% YoY to Rs 13,896 crore"
        )
        assert "tvs" in tokens
        assert "motor" in tokens

    def test_excludes_generic_financial_vocabulary(self):
        tokens = _leading_entity_tokens("TVS Motor Company Q1 Results: Revenue jumps 38% YoY")
        assert "company" not in tokens
        assert "q1" not in tokens
        assert "results" not in tokens

    def test_different_company_yields_disjoint_tokens(self):
        # The exact TVS Motor vs ABSL AMC scenario -- two real headlines that
        # are lexically similar in structure but about different companies.
        tvs = _leading_entity_tokens("TVS Motor Company Q1 Results: Revenue jumps 38% YoY to Rs 13,896 crore")
        absl = _leading_entity_tokens("Aditya Birla Sun Life AMC Q1 Results: profit rises 12% YoY")
        assert not (tvs & absl)

    def test_no_colon_uses_whole_headline(self):
        tokens = _leading_entity_tokens("TVS Motor surges after Q1 PAT jumps 51% YoY")
        assert "tvs" in tokens
        assert "motor" in tokens

    def test_short_words_excluded(self):
        tokens = _leading_entity_tokens("ABC Ltd: results out")
        assert all(len(t) > 2 for t in tokens)

    def test_all_generic_headline_yields_empty_set(self):
        tokens = _leading_entity_tokens("Q1 Results: Net Profit rises YoY to Rs Cr")
        assert tokens == set()


# ── _find_canonical_event ──────────────────────────────────────────────────────

def make_causal(id=1, country="HIGH", confidence=0.8):
    return SimpleNamespace(id=id, country=country, confidence=confidence,
                            bullish_stocks=[], bearish_stocks=[], event_title="EARNINGS")


def make_session_with_rows(rows: list[tuple]) -> AsyncMock:
    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.all.return_value = rows
    session.execute = AsyncMock(return_value=exec_result)
    return session


class TestFindCanonicalEvent:
    @pytest.mark.asyncio
    async def test_no_rows_returns_none(self):
        session = make_session_with_rows([])
        result = await _find_canonical_event("TVS Motor Q1 Results: profit up 51%", session)
        assert result is None

    @pytest.mark.asyncio
    async def test_dissimilar_headline_returns_none(self):
        session = make_session_with_rows([
            (make_causal(id=1), "RBI keeps repo rate unchanged at 6.5%"),
        ])
        result = await _find_canonical_event("TVS Motor Q1 Results: profit up 51%", session)
        assert result is None

    @pytest.mark.asyncio
    async def test_similar_headline_same_company_matches(self):
        session = make_session_with_rows([
            (make_causal(id=42), "TVS Motor Q1 Results: Revenue jumps 38% YoY to Rs 13,896 crore"),
        ])
        result = await _find_canonical_event(
            "TVS Motor Company Q1 results: Profit jumps 67% YoY; board approves fundraising", session,
        )
        assert result is not None
        causal, matched_headline = result
        assert causal.id == 42

    @pytest.mark.asyncio
    async def test_duplicate_stub_row_is_skipped(self):
        # A DUPLICATE stub (country="DUPLICATE") must never be reused as a
        # real canonical event, even if the headline text is similar.
        session = make_session_with_rows([
            (make_causal(id=1, country="DUPLICATE", confidence=0.0), "TVS Motor Q1 Results: Revenue jumps 38% YoY"),
        ])
        result = await _find_canonical_event("TVS Motor Company Q1 results: Profit jumps 67% YoY", session)
        assert result is None

    @pytest.mark.asyncio
    async def test_similar_headline_different_company_does_not_match(self):
        # THE regression guard: this is the exact live bug (TVS Motor
        # matched to ABSL AMC's stub) -- two boilerplate-heavy earnings
        # headlines that cross the similarity threshold purely from shared
        # phrasing, about genuinely different companies, must NOT match.
        session = make_session_with_rows([
            (make_causal(id=7), "Aditya Birla Sun Life AMC Q1 Results: profit rises 12% YoY to Rs 200 crore"),
        ])
        result = await _find_canonical_event(
            "TVS Motor Company Q1 Results: profit rises 51% YoY to Rs 1,174 crore", session,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_duplicate_stub_skipped_falls_through_to_real_match(self):
        session = make_session_with_rows([
            (make_causal(id=1, country="DUPLICATE", confidence=0.0), "TVS Motor Q1 Results: Revenue jumps 38% YoY"),
            (make_causal(id=99, country="HIGH"), "TVS Motor Q1 Results: Revenue jumps 39% YoY to record levels"),
        ])
        result = await _find_canonical_event("TVS Motor Company Q1 results: Profit jumps 67% YoY", session)
        assert result is not None
        causal, _ = result
        assert causal.id == 99

    @pytest.mark.asyncio
    async def test_no_leading_entity_tokens_in_target_does_not_block_match(self):
        # target_entities can be empty (e.g. a fully-generic headline) -- the
        # entity-overlap guard only applies `if target_entities`, so an
        # empty target set must not itself block an otherwise-similar match.
        session = make_session_with_rows([
            (make_causal(id=5), "Q1 Results: Net Profit rises YoY to Rs Cr this quarter"),
        ])
        result = await _find_canonical_event("Q1 Results: Net Profit rises YoY to Rs Cr", session)
        assert result is not None


# ── _build_evidence ─────────────────────────────────────────────────────────────

def _mock_session_ctx(session: AsyncMock):
    """A callable that mimics `AsyncSessionLocal()` -- returns an async
    context manager yielding the given mock session."""
    session.add = MagicMock()   # real AsyncSession.add() is sync, not a coroutine
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


class TestBuildEvidence:
    @pytest.mark.asyncio
    async def test_canonical_event_reused_skips_classification(self):
        canonical = SimpleNamespace(
            id=2848, country="HIGH", confidence=0.85,
            bullish_stocks=["TVSMOTOR"], bearish_stocks=[], event_title="EARNINGS",
        )
        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=(canonical, "matched headline text"))), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx(AsyncMock())), \
             patch("engine.event_classifier.classify_event", AsyncMock()) as mock_classify:
            evidence, event_id = await _build_evidence("TVSMOTOR.NS", "BUY", "TVS Motor Q1 results", "summary")
        assert event_id == 2848
        assert evidence.source_type == "CANONICAL_REUSE"
        assert evidence.direction == "BULLISH"
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_canonical_reuse_direction_from_bearish_list(self):
        canonical = SimpleNamespace(
            id=10, country="HIGH", confidence=0.8,
            bullish_stocks=[], bearish_stocks=["TVSMOTOR"], event_title="EARNINGS",
        )
        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=(canonical, "h"))), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx(AsyncMock())):
            evidence, event_id = await _build_evidence("TVSMOTOR.NS", "SELL", "headline", "summary")
        assert evidence.direction == "BEARISH"

    @pytest.mark.asyncio
    async def test_canonical_reuse_direction_falls_back_to_side_when_symbol_unlisted(self):
        # Symbol not present in either bullish_stocks or bearish_stocks --
        # direction falls back to the candidate's own side.
        canonical = SimpleNamespace(
            id=11, country="HIGH", confidence=0.8,
            bullish_stocks=["OTHERCO"], bearish_stocks=[], event_title="EARNINGS",
        )
        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=(canonical, "h"))), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx(AsyncMock())):
            evidence, event_id = await _build_evidence("TVSMOTOR.NS", "BUY", "headline", "summary")
        assert evidence.direction == "BULLISH"  # side=BUY -> BULLISH fallback

    @pytest.mark.asyncio
    async def test_no_canonical_event_classifies_fresh_and_persists(self):
        classification = SimpleNamespace(
            category="EARNINGS_BEAT", impact="HIGH", confidence=0.9, bullish=True,
            surprise_score=80, expected_half_life_hours=48,
            entities={"companies": ["TVSMOTOR"], "sectors": ["Auto"]},
        )

        persisted_causal = SimpleNamespace(id=555)

        def _fake_causal_event_ctor(**kwargs):
            return persisted_causal

        session = AsyncMock()
        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=None)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx(session)), \
             patch("engine.event_classifier.classify_event", AsyncMock(return_value=classification)), \
             patch("db.models.CausalEvent", side_effect=_fake_causal_event_ctor):
            evidence, event_id = await _build_evidence("TVSMOTOR.NS", "BUY", "fresh headline", "fresh summary")
        assert event_id == 555
        assert evidence is not None
        assert evidence.event_category == "EARNINGS_BEAT"

    @pytest.mark.asyncio
    async def test_classification_failure_is_no_event_no_trade(self):
        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=None)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx(AsyncMock())), \
             patch("engine.event_classifier.classify_event", AsyncMock(return_value=None)):
            evidence, event_id = await _build_evidence("TVSMOTOR.NS", "BUY", "headline", "summary")
        assert evidence is None
        assert event_id is None

    @pytest.mark.asyncio
    async def test_persistence_failure_is_no_event_no_trade_not_fail_open(self):
        # Classification succeeds but the DB commit fails -- must NOT
        # silently return a usable evidence/event_id (that would be the
        # exact fail-open bug the NO-EVENT-NO-TRADE invariant exists to
        # prevent: a trade with no real canonical row to trace to).
        classification = SimpleNamespace(
            category="EARNINGS_BEAT", impact="HIGH", confidence=0.9, bullish=True,
            surprise_score=80, expected_half_life_hours=48,
            entities={"companies": [], "sectors": []},
        )
        broken_session = AsyncMock()
        broken_session.commit = AsyncMock(side_effect=RuntimeError("db unavailable"))
        with patch("news_discovery_engine._find_canonical_event", AsyncMock(return_value=None)), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx(broken_session)), \
             patch("engine.event_classifier.classify_event", AsyncMock(return_value=classification)):
            evidence, event_id = await _build_evidence("TVSMOTOR.NS", "BUY", "headline", "summary")
        assert evidence is None
        assert event_id is None

    @pytest.mark.asyncio
    async def test_dedup_lookup_exception_falls_through_to_fresh_classification(self):
        # _find_canonical_event() itself raising must not crash the whole
        # pipeline -- it should proceed to classify fresh instead.
        classification = SimpleNamespace(
            category="EARNINGS_BEAT", impact="HIGH", confidence=0.9, bullish=True,
            surprise_score=80, expected_half_life_hours=48,
            entities={"companies": [], "sectors": []},
        )
        persisted_causal = SimpleNamespace(id=777)
        session = AsyncMock()
        with patch("news_discovery_engine._find_canonical_event", AsyncMock(side_effect=RuntimeError("db down"))), \
             patch("news_discovery_engine.AsyncSessionLocal", _mock_session_ctx(session)), \
             patch("engine.event_classifier.classify_event", AsyncMock(return_value=classification)), \
             patch("db.models.CausalEvent", side_effect=lambda **kw: persisted_causal):
            evidence, event_id = await _build_evidence("TVSMOTOR.NS", "BUY", "headline", "summary")
        assert event_id == 777
