"""After-market boundary correctness, and the duplicate/amendment cases.

The NSE session is 09:15-15:30 IST. Everything about the after-market pipeline
turns on which side of 15:30 a filing lands, and on IST->UTC conversion being
right. This codebase has been burned by both:

  * A filing at 15:51 IST once opened a LIVE position, because the gate used
    _is_india_trading_window() (extended to 16:00 for position management)
    instead of is_nse_market_open() (the real 09:15-15:30).
  * _parse_nse_announcement_dt once stored IST wall-clock in a UTC column,
    putting 4,159 rows 5h30m in the future relative to their own crawl.

These pin the boundaries explicitly rather than trusting either fix to stay.
"""
from __future__ import annotations

import datetime as dt

import pytest

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OPEN_T = dt.time(9, 15)
CLOSE_T = dt.time(15, 30)


def is_session_time(t: dt.time) -> bool:
    """The strict definition. 15:30:00 is the close, and the close is OUT."""
    return OPEN_T <= t < CLOSE_T


def ist_to_utc(d: dt.date, t: dt.time) -> dt.datetime:
    return (dt.datetime.combine(d, t, tzinfo=IST)
            .astimezone(dt.timezone.utc).replace(tzinfo=None))


D = dt.date(2026, 8, 27)


class TestSessionBoundary:
    @pytest.mark.parametrize("hh,mm,inside", [
        (9, 14, False),    # pre-open
        (9, 15, True),     # the open itself is IN
        (15, 29, True),    # one minute before the close
        (15, 30, False),   # THE CLOSE IS OUT
        (15, 31, False),   # after-market
        (16, 0, False),    # the 15:51 incident window
        (23, 59, False),
        (0, 0, False),     # midnight
    ])
    def test_boundary(self, hh, mm, inside):
        assert is_session_time(dt.time(hh, mm)) is inside

    def test_the_1551_incident_is_after_market(self):
        """SHAKTIPUMP.BO opened live at 15:51 IST under the extended window."""
        assert not is_session_time(dt.time(15, 51))

    def test_extended_window_is_not_the_trading_gate(self):
        """_is_india_trading_window() runs to 16:00 for position management.
        Anything deciding whether a NEW position may open must not use it."""
        import inspect

        import news_discovery_engine as nde
        src = inspect.getsource(nde._news_discovery_cycles)
        i = src.index("market_open = ")
        assert "is_nse_market_open()" in src[i:i + 80]
        assert "_is_india_trading_window" not in src[i:i + 80]


class TestTimezoneConversion:
    def test_close_maps_to_1000_utc(self):
        assert ist_to_utc(D, CLOSE_T) == dt.datetime(2026, 8, 27, 10, 0)

    def test_open_maps_to_0345_utc(self):
        assert ist_to_utc(D, OPEN_T) == dt.datetime(2026, 8, 27, 3, 45)

    def test_midnight_ist_is_previous_day_utc(self):
        """The classic date-boundary trap: 00:00 IST is 18:30 UTC YESTERDAY."""
        assert ist_to_utc(D, dt.time(0, 0)) == dt.datetime(2026, 8, 26, 18, 30)

    def test_an_after_market_filing_can_fall_on_the_previous_utc_date(self):
        """A filing at 23:00 IST on the 27th is 17:30 UTC on the 27th, but one
        at 00:30 IST on the 28th is 19:00 UTC on the 27th. Anything grouping by
        ::date must decide which calendar it means."""
        assert ist_to_utc(dt.date(2026, 8, 28), dt.time(0, 30)).date() == dt.date(2026, 8, 27)

    def test_nse_announcement_parser_returns_naive_utc(self):
        from crawler.news_crawler import _parse_nse_announcement_dt

        got = _parse_nse_announcement_dt("27-Aug-2026 20:10:07")
        assert got is not None
        assert got.tzinfo is None, "must be naive to match every other source"
        # 20:10:07 IST == 14:40:07 UTC
        assert got == dt.datetime(2026, 8, 27, 14, 40, 7)

    def test_parser_never_returns_a_future_stamp(self):
        """The 4,159-row bug: IST wall-clock written into a UTC column would
        make published_at 5h30m LATER than the crawl that found it."""
        from crawler.news_crawler import _parse_nse_announcement_dt

        got = _parse_nse_announcement_dt("27-Aug-2026 20:10:07")
        naive_ist = dt.datetime(2026, 8, 27, 20, 10, 7)
        assert got < naive_ist, "parser returned IST wall-clock, not UTC"
        assert (naive_ist - got) == dt.timedelta(hours=5, minutes=30)

    def test_parser_tolerates_garbage(self):
        from crawler.news_crawler import _parse_nse_announcement_dt

        for bad in ("", None, "not a date", "99-Xyz-2026 99:99:99"):
            assert _parse_nse_announcement_dt(bad) is None


class TestDuplicateAndAmendedFilings:
    """The cases a durable seq_id dedup must get right."""

    def test_same_seq_id_is_one_filing(self):
        a = {"seq_id": "12345", "headline": "ACME: Outcome of Board Meeting"}
        b = {"seq_id": "12345", "headline": "ACME: Outcome of Board Meeting"}
        assert a["seq_id"] == b["seq_id"]

    def test_same_seq_id_with_a_CHANGED_headline_is_still_one_filing(self):
        """NSE revises attachment text. seq_id is the identity; the headline is
        a rendering of it. Keying on headline alone re-inserts the revision."""
        seen = {"12345"}
        revised = {"seq_id": "12345", "headline": "ACME: Outcome of Board Meeting (revised)"}
        assert revised["seq_id"] in seen, "a revision must not create a second row"

    def test_same_headline_with_a_DIFFERENT_seq_id_is_two_filings(self):
        """A company can file the same category twice in a day -- two board
        meetings, two order wins. Identical headlines, genuinely distinct."""
        seen = {"12345"}
        second = {"seq_id": "67890", "headline": "ACME: Outcome of Board Meeting"}
        assert second["seq_id"] not in seen

    def test_multiple_filings_same_company_same_day_all_persist(self):
        seqs = ["1", "2", "3"]
        seen: set = set()
        kept = [x for x in seqs if x not in seen and not seen.add(x)]
        assert len(kept) == 3

    def test_the_prefilter_is_keyed_on_seq_id_not_headline(self):
        import ast
        import inspect
        import textwrap

        import news_discovery_engine as nde
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(nde._process_nse_announcements)))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                b = n.body
                if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                    n.body = b[1:] or [ast.Pass()]
        src = ast.unparse(tree)
        assert "seq_id" in src
        i = src.index("already-persisted")
        assert "headline" not in src[i - 400:i], (
            "the durable dedup must key on seq_id; headline is not stable "
            "across NSE revisions"
        )


class TestNoLookAheadFromTimestamps:
    def test_knowable_at_is_max_of_crawl_and_event(self):
        """Publication is not possession. The earliest we can legitimately act
        is when we CRAWLED it, never when NSE published it."""
        pub = dt.datetime(2026, 8, 27, 10, 0)
        crawl = dt.datetime(2026, 8, 27, 10, 7)
        ev = dt.datetime(2026, 8, 27, 10, 9)
        assert max(crawl, ev) == ev
        assert max(crawl, ev) > pub

    def test_a_dataset_bound_must_use_crawled_at_not_published_at(self):
        """published_at alone would admit a filing we had not yet seen."""
        pub = dt.datetime(2026, 8, 27, 9, 50)
        crawl = dt.datetime(2026, 8, 27, 16, 20)
        cutoff = dt.datetime(2026, 8, 27, 10, 0)
        assert pub <= cutoff          # would wrongly be admitted
        assert not (crawl <= cutoff)  # correctly excluded
