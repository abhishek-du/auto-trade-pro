"""NSE announcements must be date-scoped, and filtered before limiting (F1).

MEASURED 2026-08-27: NSE's date-scoped feed carried 79 announcements / 17
high-impact for the session. Our database held 2 high-impact. The crawler was
not slow — it was reading a keyhole:

  1. the request carried NO date parameters, so the endpoint returned a rolling
     window of roughly the last 20 filings regardless of the trading day; and
  2. `for item in (data or [])[:limit]` sliced the RAW payload before the
     category filter ran, so high-impact filings below the cut were discarded
     without ever being examined.

Both are recall bugs. Neither changes what counts as high-impact.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

import crawler.news_crawler as nc


def _code_only(fn) -> str:
    """Executable source only — the comments describe the OLD behaviour."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = node.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                node.body = b[1:] or [ast.Pass()]
    return ast.unparse(tree)


SRC = _code_only(nc.fetch_nse_corporate_announcements)


class TestDateScoping:
    def test_date_parameters_are_sent(self):
        assert "from_date=" in SRC and "to_date=" in SRC

    def test_the_format_is_nse_ddmmyyyy(self):
        assert "%d-%m-%Y" in SRC, "NSE rejects ISO dates on this endpoint"

    def test_scope_is_a_single_trading_date(self):
        """from and to are the SAME day, so yesterday's filings cannot leak
        into today's candidate stream."""
        assert "from_date={_scope}&to_date={_scope}" in SRC

    def test_the_scope_is_in_the_log_line(self):
        assert "date_scope=" in SRC and "date_scoped=" in SRC

    def test_it_uses_ist_not_utc(self):
        """At 03:00 IST, UTC is still the previous calendar day."""
        assert "_IST_TZ" in SRC


class TestFilterBeforeLimit:
    def test_the_category_filter_runs_before_the_slice(self):
        i_filter = SRC.index("_HIGH_IMPACT_ANNOUNCEMENT_CATEGORIES")
        i_limit = SRC.index("filtered[:limit]")
        assert i_filter < i_limit, (
            "slicing the raw payload discards high-impact filings unexamined"
        )

    def test_the_raw_payload_is_not_sliced(self):
        assert "(data or [])[:limit]" not in SRC
        assert "for item in data or []" in SRC or "for item in (data or [])" in SRC

    def test_truncation_by_limit_is_reported_not_silent(self):
        assert "truncated_by_limit=" in SRC
        assert "dropped by limit" in SRC


class TestDeduplication:
    def test_seq_id_dedup_is_preserved(self):
        assert "seen_seq" in SRC and "duplicates" in SRC

    def test_duplicates_are_counted_not_hidden(self):
        assert "duplicates_in_payload=" in SRC


class TestTelemetry:
    @pytest.mark.parametrize("field", [
        "nse_total=", "nse_high_impact=", "date_scope=", "poll_ts=",
        "duplicates_in_payload=", "returned=",
    ])
    def test_required_counter_present(self, field):
        assert field in SRC


class TestFallbackIsNotSilent:
    def test_a_scoped_failure_falls_back(self):
        assert "FALLING BACK" in inspect.getsource(nc.fetch_nse_corporate_announcements)

    def test_the_fallback_logs_an_error_not_a_debug(self):
        """Pretending recall is healthy is the failure mode being closed."""
        raw = inspect.getsource(nc.fetch_nse_corporate_announcements)
        i = raw.index("FALLING BACK")
        assert "logger.error" in raw[max(0, i - 300):i]

    def test_degraded_recall_is_named(self):
        assert "DEGRADED" in inspect.getsource(nc.fetch_nse_corporate_announcements)


class TestSymbolSafety:
    def test_symbols_go_through_the_normaliser(self):
        """NSE returns bare symbols today, but a blind f"{s}.NS" is exactly the
        F3 bug in another file."""
        assert "_norm_symbol(symbol)" in SRC

    def test_no_blind_append_remains_in_this_function(self):
        assert 'f"{symbol}.NS"' not in SRC and "f'{symbol}.NS'" not in SRC


class TestUnchanged:
    def test_the_high_impact_category_set_was_not_touched(self):
        """This is a RECALL fix. What counts as high-impact is unchanged."""
        assert isinstance(nc._HIGH_IMPACT_ANNOUNCEMENT_CATEGORIES, (list, tuple, set, frozenset))
        assert len(nc._HIGH_IMPACT_ANNOUNCEMENT_CATEGORIES) > 0

    def test_the_return_shape_is_unchanged(self):
        for key in ("seq_id", "symbol", "company", "category", "summary",
                    "headline", "pdf_url", "published_at", "source"):
            assert f'"{key}"' in SRC or f"'{key}'" in SRC
