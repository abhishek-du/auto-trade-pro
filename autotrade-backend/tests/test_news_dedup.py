"""News ingestion dedup (P1, 2026-08-20).

82% of one session's news_items rows were redundant: 2,371 rows for 418 unique
headlines. Feeds re-serve the same story every cycle and the crawler's
`seen_urls` set only dedups WITHIN one run, so every cycle re-inserted the lot.
Downstream STALE checks caught it (212 rejections that session) but only after
the LLM budget and log volume were already spent.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def code_of(*parts: str) -> str:
    """Source with comments and docstrings stripped.

    These assertions must not be satisfiable by a COMMENT that merely mentions
    the thing. That is not hypothetical: the first version of this file passed
    while `.on_conflict_do_nothing()` was deleted from the actual call, because
    the explanatory comment above it still contained the string.
    """
    import ast

    src = (BACKEND.joinpath(*parts)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            node.value.value = ""          # blank out docstrings
    return ast.unparse(tree)               # unparse drops comments entirely


class TestCrawlerUsesOnConflict:

    def test_insert_is_conflict_safe(self):
        code = code_of("crawler", "news_crawler.py")
        assert "on_conflict_do_nothing()" in code, (
            "the INSERT is no longer conflict-safe — a duplicate now raises "
            "instead of being skipped"
        )
        assert "pg_insert" in code

    def test_no_bare_orm_add_for_news_rows(self):
        """A plain session.add(NewsItem(...)) bypasses ON CONFLICT and raises on
        a unique violation instead of skipping."""
        assert "session.add(row)" not in code_of("crawler", "news_crawler.py")

    def test_duplicates_are_not_broadcast(self):
        """A suppressed row must not reach the websocket, or the UI shows the
        same headline again on every crawl."""
        code = code_of("crawler", "news_crawler.py")
        i = code.index("_inserted_id is None")
        assert "continue" in code[i:i + 400]

    def test_saved_counter_excludes_duplicates(self):
        code = code_of("crawler", "news_crawler.py")
        i = code.index("_inserted_id = ")
        seg = code[i:i + 600]
        # the skip path must come before total_saved is incremented
        assert seg.index("_dupes_skipped") < seg.index("total_saved += 1")


class TestIndexDefinition:
    """The index shape encodes three decisions that are easy to undo by accident."""

    def _ddl(self) -> str:
        return (BACKEND / "db" / "database.py").read_text(encoding="utf-8")

    def test_index_is_created_on_boot(self):
        assert "uq_news_items_headline_day" in self._ddl()

    def test_hashes_the_headline(self):
        """Headlines are TEXT (819 chars observed); btree caps a key near 2704
        bytes, so indexing the raw column would fail on a long headline."""
        assert "md5(headline)" in self._ddl()

    def test_coalesces_the_nullable_date(self):
        """published_at is NULLABLE (820 NULLs in history) and Postgres treats
        NULLs as distinct — keying on it alone would let exactly those rows
        keep duplicating."""
        assert "COALESCE(published_at, crawled_at)" in self._ddl()

    def test_index_is_partial(self):
        """MUST stay partial. 14,105 historical rows are duplicates and 685 are
        referenced by causal_events.news_id under an ON DELETE NO ACTION FK, so
        a full unique index cannot be built without breaking those references."""
        ddl = self._ddl()
        i = ddl.index("uq_news_items_headline_day")
        assert "WHERE crawled_at >=" in ddl[i:i + 500], (
            "index is no longer partial — it would fail to build on this table"
        )


class TestSequenceRepair:
    """news_items_id_seq fell behind MAX(id) on 2026-08-20 and killed ingestion
    for 5.5 hours, silently: the crawler reported saved=N because the counter is
    incremented before the commit that then failed on the primary key."""

    def test_boot_repairs_the_sequence(self):
        ddl = (BACKEND / "db" / "database.py").read_text(encoding="utf-8")
        assert "setval('news_items_id_seq'" in ddl

    def test_repair_uses_false_so_next_id_is_max_plus_one(self):
        """setval(..., true) would make the next id MAX(id)+2 and leave a gap
        every boot; setval(..., false) yields exactly MAX(id)+1."""
        ddl = (BACKEND / "db" / "database.py").read_text(encoding="utf-8")
        i = ddl.index("setval('news_items_id_seq'")
        assert ", false)" in ddl[i:i + 160]
