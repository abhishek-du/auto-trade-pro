"""causal_events.news_id must carry the EXACT NewsItem this cycle produced.

Background: news_id was 100% populated 2026-07-16..07-21 by
crawler/event_pipeline.py, then 0% once origination moved to
news_discovery_engine, whose CausalEvent site hardcoded news_id=None. Phase 7
established the historical linkage is unrecoverable — 0 exact matches on every
candidate key — so this is forward-only, and the tests below exist to make sure
the forward link is *exact* rather than merely present.

The interesting case is the duplicate: ON CONFLICT DO NOTHING returns NULL, so
without a deterministic re-read a repeated headline would silently produce a
NULL link again.

These run against the allowlisted TEST database (see tests/TEST_DATABASE.md).
"""
from __future__ import annotations

import ast
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _insert(session, headline: str, published_at):
    """Mirror the production insert exactly: ON CONFLICT DO NOTHING + RETURNING."""
    from db.models import NewsItem
    return (await session.execute(
        pg_insert(NewsItem.__table__)
        .values(headline=headline, source="TEST-RSS", url=None,
                published_at=published_at, sentiment="neutral", score=0.0,
                tickers_affected=None)
        .on_conflict_do_nothing()
        .returning(NewsItem.__table__.c.id)
    )).scalar()


@pytest.mark.asyncio
async def test_a_new_news_item_returns_its_own_id():
    from db.database import AsyncSessionLocal
    assert AsyncSessionLocal.kw["bind"].url.database == "autotrade_test"
    h = _unique("phase10-new")
    pub = datetime.utcnow()
    async with AsyncSessionLocal() as s:
        new_id = await _insert(s, h, pub)
        assert new_id is not None, "a fresh headline must return its id"
        await s.rollback()


@pytest.mark.asyncio
async def test_duplicate_headline_resolves_to_the_same_existing_id():
    """The case that would otherwise re-introduce a NULL link."""
    from db.database import AsyncSessionLocal
    from news_discovery_engine import _resolve_news_id
    h = _unique("phase10-dupe")
    pub = datetime.utcnow()
    async with AsyncSessionLocal() as s:
        first = await _insert(s, h, pub)
        await s.commit()
    try:
        async with AsyncSessionLocal() as s:
            second = await _insert(s, h, pub)
            assert second is None, "the second insert must conflict"
            resolved = await _resolve_news_id(s, h, pub)
            assert resolved == first, (
                f"resolver returned {resolved!r}, expected the existing id {first!r}"
            )
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(text("DELETE FROM news_items WHERE headline = :h"), {"h": h})
            await s.commit()


@pytest.mark.asyncio
async def test_resolver_returns_none_for_an_unknown_headline():
    """No fuzzy fallback: an absent headline yields None, never a near match."""
    from db.database import AsyncSessionLocal
    from news_discovery_engine import _resolve_news_id
    async with AsyncSessionLocal() as s:
        assert await _resolve_news_id(s, _unique("phase10-absent"), datetime.utcnow()) is None


@pytest.mark.asyncio
async def test_each_of_several_headlines_resolves_to_its_own_row():
    """One-to-one attribution — the failure mode a naive implementation has."""
    from db.database import AsyncSessionLocal
    from news_discovery_engine import _resolve_news_id
    pub = datetime.utcnow()
    hs = [_unique(f"phase10-multi-{i}") for i in range(4)]
    ids = {}
    async with AsyncSessionLocal() as s:
        for h in hs:
            ids[h] = await _insert(s, h, pub)
        await s.commit()
    try:
        assert len(set(ids.values())) == 4, "four headlines must produce four distinct ids"
        async with AsyncSessionLocal() as s:
            for h in hs:
                assert await _resolve_news_id(s, h, pub) == ids[h], f"mis-attributed {h}"
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(text("DELETE FROM news_items WHERE headline = ANY(:h)"), {"h": hs})
            await s.commit()


@pytest.mark.asyncio
async def test_same_headline_on_a_different_day_is_a_different_row():
    """The conflict key is (md5(headline), date) — the date half must matter."""
    from db.database import AsyncSessionLocal
    from news_discovery_engine import _resolve_news_id
    h = _unique("phase10-twoday")
    d1 = datetime.utcnow()
    d2 = d1 - timedelta(days=1)
    async with AsyncSessionLocal() as s:
        id1 = await _insert(s, h, d1)
        id2 = await _insert(s, h, d2)
        await s.commit()
    try:
        assert id1 is not None and id2 is not None and id1 != id2
        async with AsyncSessionLocal() as s:
            assert await _resolve_news_id(s, h, d1) == id1
            assert await _resolve_news_id(s, h, d2) == id2
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(text("DELETE FROM news_items WHERE headline = :h"), {"h": h})
            await s.commit()


# ── structural guarantees ────────────────────────────────────────────────────

def _engine_ast():
    return ast.parse(Path("news_discovery_engine.py").read_text())


def test_causal_event_no_longer_hardcodes_news_id_none():
    """AST, not text: a comment mentioning news_id cannot satisfy this."""
    tree = _engine_ast()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "CausalEvent":
            kw = {k.arg: k.value for k in node.keywords}
            assert "news_id" in kw, "CausalEvent no longer passes news_id at all"
            v = kw["news_id"]
            assert not (isinstance(v, ast.Constant) and v.value is None), (
                "news_id is hardcoded None again — the link is dead"
            )
            assert isinstance(v, ast.Name) and v.id == "news_id"
            return
    pytest.fail("no CausalEvent(...) construction found")


def test_the_resolver_never_falls_back_to_a_heuristic():
    """It must key on md5(headline) + the date, plus the partial-index range."""
    src = Path("news_discovery_engine.py").read_text()
    fn = next(n for n in ast.walk(_engine_ast())
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_resolve_news_id")
    body = ast.get_source_segment(src, fn) or ""
    assert "md5(headline) = md5(:headline)" in body
    assert "COALESCE(published_at, crawled_at))::date" in body
    assert "crawled_at >= TIMESTAMP" in body, "partial-index predicate missing"
    for banned in ("ILIKE", "similarity(", "LIMIT 1", "ORDER BY"):
        assert banned not in body, f"{banned} suggests a heuristic match, not an exact key"


def test_paths_without_a_news_item_still_pass_none():
    """The anomaly-catalyst and pre-market-queue callers legitimately have no
    NewsItem; a NULL there is correct and must not be forced."""
    src = Path("news_discovery_engine.py").read_text()
    fn = next(n for n in ast.walk(_engine_ast())
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "process_ticker")
    defaults = {a.arg: d for a, d in zip(fn.args.args[-len(fn.args.defaults):], fn.args.defaults)} \
        if fn.args.defaults else {}
    assert "news_id" in defaults, "news_id must be optional"
    assert isinstance(defaults["news_id"], ast.Constant) and defaults["news_id"].value is None
