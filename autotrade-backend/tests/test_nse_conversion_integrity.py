"""NSE announcements must persist once, with provenance, and not re-do work.

Step 1A found 33 high-impact filings fetched and 3 rows stored. Investigation
gave THREE independent defects, each with its own evidence:

  A1  CONSUMER STARVATION (fixed separately, pinned by
      test_nse_consumer_priority.py). The consumer sat behind an unbounded RSS
      loop of LLM ReAct passes and ran twice all day, both before the open.

  A2  HEADLINE MUTATION BROKE DEDUP. The consumer appended
      "| [LLM Summary: ...]" to the headline BEFORE inserting. The unique index
      is uq_news_items_headline_day on (md5(headline), date) and the LLM
      summary is non-deterministic -- the SAME Juniper Green filing
      (published_at 2026-08-26 19:39:16) produced md5 9a401c85 / 3eceb972 /
      7b3dd191 across three passes. ON CONFLICT DO NOTHING could never fire.
      Measured: 367 duplicates in 4,770 stored announcements (7.7%); 44 of 90
      rows (49%) on 2026-08-27 once the consumer started keeping up.

  A3  NO DURABLE DEDUP. _processed_seq_ids is in-memory and watchmedo restarts
      the process on any .py write -- five restarts in six minutes on
      2026-08-27. Every restart re-downloaded the PDF, re-ran OCR and re-called
      the LLM for filings already stored. seq_id, NSE's own identifier, was
      discarded at insert so nothing durable could be checked.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

import news_discovery_engine as nde


def _code(fn) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                n.body = b[1:] or [ast.Pass()]
    return ast.unparse(tree)


SRC = _code(nde._process_nse_announcements)


class TestA2_HeadlineIsNeverMutated:
    """The dedup key must be stable across runs."""

    def test_the_llm_summary_is_not_appended_to_the_headline(self):
        assert "LLM Summary:" not in SRC, (
            "appending a non-deterministic summary to the headline makes the "
            "md5 dedup key differ on every pass"
        )

    def test_ann_headline_is_not_reassigned(self):
        tree = ast.parse(textwrap.dedent(inspect.getsource(nde._process_nse_announcements)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) \
                            and t.value.id == "ann":
                        key = getattr(getattr(t.slice, "value", None), "__str__", lambda: "")()
                        assert "headline" not in str(ast.dump(t.slice)), (
                            "ann['headline'] is reassigned before the insert again"
                        )

    def test_the_summary_goes_to_metadata_instead(self):
        assert "llm_summary" in SRC
        assert "news_metadata" in SRC

    def test_insert_still_dedups(self):
        assert "on_conflict_do_nothing" in SRC


class TestA3_DurableDedup:
    def test_seq_id_is_persisted(self):
        """Without it there is no durable key to check against."""
        assert "'seq_id': ann.get('seq_id')" in SRC or '"seq_id": ann.get("seq_id")' in SRC

    def test_a_prefilter_runs_before_the_expensive_work(self):
        # Anchor on the CALL, not the import at the top of the function.
        i_filter = SRC.index("already-persisted")
        i_pdf = SRC.index("await process_nse_announcement(")
        assert i_filter < i_pdf, (
            "the dedup check must run BEFORE the PDF download / OCR / LLM call"
        )

    def test_the_prefilter_queries_persisted_seq_ids(self):
        assert "news_metadata->>'seq_id'" in SRC

    def test_prefilter_failure_is_non_fatal(self):
        i = SRC.index("already-persisted")
        window = SRC[max(0, i - 1200): i + 600]
        assert "except Exception" in window

    def test_it_seeds_the_in_memory_set_too(self):
        assert "_processed_seq_ids.add" in SRC


class TestProvenance:
    def test_premarket_drain_resolves_a_news_id(self):
        src = _code(nde._news_discovery_cycles)
        assert "_resolve_news_id" in src, (
            "the premarket drain was the dominant event source and passed no "
            "news_id, leaving causal_events.news_id 0% populated since 07-21"
        )

    def test_it_uses_captured_at_not_none(self):
        """_resolve_news_id keys on the DATE. Passing None keys on today, while
        the queued row was inserted on its capture date -- the lookup would
        never match and the fix would be a silent no-op."""
        src = _code(nde._news_discovery_cycles)
        i = src.index("_resolve_news_id")
        assert "captured_at" in src[i - 200: i + 200]

    def test_captured_at_is_carried_through_the_drain_tuple(self):
        src = _code(nde._news_discovery_cycles)
        assert "i.captured_at" in src

    def test_lookup_failure_degrades_to_todays_behaviour(self):
        src = _code(nde._news_discovery_cycles)
        i = src.index("_resolve_news_id")
        assert "except Exception" in src[i - 400: i + 400]

    def test_resolver_is_exact_never_fuzzy(self):
        doc = inspect.getdoc(nde._resolve_news_id) or ""
        assert "exact key match" in doc.lower()
        body = _code(nde._resolve_news_id)
        for banned in ("similarity", "ilike", "levenshtein", "difflib"):
            assert banned not in body.lower()


class TestUnchangedBehaviour:
    def test_market_hours_gate_still_governs_live_processing(self):
        assert "if market_open:" in SRC
        assert "PreMarketNewsQueue" in SRC

    def test_exchange_category_still_decides_direction(self):
        assert "resolve_nse_direction" in SRC or "ann['category']" in SRC

    def test_no_threshold_or_limit_was_touched(self):
        """This is a data-integrity fix, not a strategy change."""
        for banned in ("TACTICAL_TOP_N", "V2_MIN_HOLD", "MAX_RISK", "R:R"):
            assert banned not in SRC
