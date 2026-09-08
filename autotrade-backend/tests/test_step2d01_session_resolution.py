"""STEP 2D.0.1 — session-date resolution and reader safety.

Step 2D.0 proved an ACTIVE look-ahead in performance_engine._aligned_closes:
while the legacy and canonical daily series coexist, grouping rows by calendar
date let an 18:30 bar (which belongs to the NEXT session) win the date it is
stored under. These tests pin the resolver that replaced that, and the exact
RELIANCE case that exposed it.
"""
from __future__ import annotations

import datetime as dt

import pytest

from utils.candle_contract import (
    DailyConvention,
    InstrumentClass,
    SessionStatus,
    classify_instrument,
    convention_rank,
    next_nse_session,
    nse_closed_dates,
    nse_extra_open_dates,
    resolve_daily_session_date,
)

# Real /v2/market/holidays entries, captured 2026-09-04.
PAYLOAD = [
    {"date": "2026-04-03", "holiday_type": "TRADING_HOLIDAY",
     "description": "Good Friday", "open_exchanges": []},
    {"date": "2026-08-26", "holiday_type": "SETTLEMENT_HOLIDAY",
     "description": "Id-E-Milad", "open_exchanges": [{"exchange": "NSE"}]},
    {"date": "2026-02-01", "holiday_type": "SPECIAL_TIMING",
     "description": "Budget Day", "open_exchanges": [{"exchange": "NSE"}]},
]
CLOSED = nse_closed_dates(PAYLOAD)
EXTRA = nse_extra_open_dates(PAYLOAD)
FAR_FUTURE = dt.datetime(2027, 1, 1)

R = lambda sym, ts: resolve_daily_session_date(
    sym, "1d", ts, holidays=CLOSED, extra_open=EXTRA, now_utc=FAR_FUTURE)


class TestNextNseSession:
    """Calendar-driven, never a blind +1 day."""

    def test_thursday_rolls_to_friday(self):
        assert next_nse_session(dt.date(2026, 9, 3), CLOSED, EXTRA) == dt.date(2026, 9, 4)

    def test_friday_rolls_to_monday(self):
        assert next_nse_session(dt.date(2026, 8, 28), CLOSED, EXTRA) == dt.date(2026, 8, 31)

    def test_saturday_rolls_to_monday(self):
        assert next_nse_session(dt.date(2026, 8, 29), CLOSED, EXTRA) == dt.date(2026, 8, 31)

    def test_day_before_a_closure_skips_it(self):
        """2026-04-03 is Good Friday (NSE shut) and 04-04/05 are the weekend."""
        got = next_nse_session(dt.date(2026, 4, 2), CLOSED, EXTRA)
        assert got == dt.date(2026, 4, 6), f"expected Monday 06-Apr, got {got}"

    def test_a_settlement_holiday_is_not_skipped(self):
        """26-Aug is NSE-open, so 25-Aug rolls to 26-Aug, not past it."""
        assert next_nse_session(dt.date(2026, 8, 25), CLOSED, EXTRA) == dt.date(2026, 8, 26)


class TestResolverConventions:

    def test_canonical_resolves_to_its_own_date(self):
        r = R("RELIANCE.NS", dt.datetime(2026, 9, 2, 3, 45))
        assert r.session_date == dt.date(2026, 9, 2)
        assert r.convention is DailyConvention.CANONICAL_0345
        assert r.status is SessionStatus.VALID_CANONICAL and r.usable

    def test_legacy_1830_resolves_to_the_NEXT_session(self):
        r = R("RELIANCE.NS", dt.datetime(2026, 9, 2, 18, 30))
        assert r.session_date == dt.date(2026, 9, 3), "18:30 belongs to the next session"
        assert r.convention is DailyConvention.LEGACY_1830

    def test_legacy_1830_on_friday_resolves_to_monday(self):
        r = R("RELIANCE.NS", dt.datetime(2026, 8, 28, 18, 30))
        assert r.session_date == dt.date(2026, 8, 31)

    def test_legacy_0000_equity_is_flagged_unadjusted(self):
        """Its DATE is sound; its PRICES are the dead pre-split series."""
        r = R("RELIANCE.NS", dt.datetime(2026, 9, 2, 0, 0))
        assert r.session_date == dt.date(2026, 9, 2)
        assert r.status is SessionStatus.VALID_LEGACY_UNADJUSTED

    def test_legacy_0000_index_is_valid(self):
        """00:00 is currently the ONLY historical index source; refusing it
        would empty the benchmark series."""
        r = R("^NSEI", dt.datetime(2026, 9, 2, 0, 0))
        assert r.session_date == dt.date(2026, 9, 2)
        assert r.status is SessionStatus.VALID_LEGACY

    def test_0000_is_never_treated_as_1830(self):
        a = R("RELIANCE.NS", dt.datetime(2026, 9, 2, 0, 0))
        b = R("RELIANCE.NS", dt.datetime(2026, 9, 2, 18, 30))
        assert a.session_date != b.session_date
        assert a.convention is not b.convention


class TestResolverRefusals:

    def test_arbitrary_time_is_unresolved_not_guessed(self):
        r = R("RELIANCE.NS", dt.datetime(2026, 9, 2, 7, 12))
        assert r.session_date is None
        assert r.status is SessionStatus.UNRESOLVED and not r.usable

    def test_future_timestamp_rejected(self):
        r = resolve_daily_session_date("RELIANCE.NS", "1d",
                                       dt.datetime(2027, 6, 1, 3, 45),
                                       holidays=CLOSED, extra_open=EXTRA,
                                       now_utc=dt.datetime(2026, 9, 4))
        assert r.status is SessionStatus.REJECTED_FUTURE and not r.usable

    def test_canonical_on_a_weekend_rejected(self):
        r = R("RELIANCE.NS", dt.datetime(2026, 9, 5, 3, 45))     # Saturday
        assert r.status is SessionStatus.REJECTED_NON_SESSION

    def test_canonical_on_a_true_closure_rejected(self):
        r = R("RELIANCE.NS", dt.datetime(2026, 4, 3, 3, 45))     # Good Friday
        assert r.status is SessionStatus.REJECTED_NON_SESSION

    def test_canonical_on_a_settlement_holiday_accepted(self):
        r = R("RELIANCE.NS", dt.datetime(2026, 8, 26, 3, 45))
        assert r.status is SessionStatus.VALID_CANONICAL

    def test_tz_aware_and_non_daily_refused(self):
        assert not R("RELIANCE.NS",
                     dt.datetime(2026, 9, 2, 3, 45, tzinfo=dt.timezone.utc)).usable
        assert not resolve_daily_session_date(
            "RELIANCE.NS", "1m", dt.datetime(2026, 9, 2, 3, 45)).usable


class TestConventionPreference:

    def test_canonical_outranks_legacy(self):
        assert convention_rank(DailyConvention.CANONICAL_0345) < \
               convention_rank(DailyConvention.LEGACY_1830) < \
               convention_rank(DailyConvention.LEGACY_0000)

    def test_preference_only_applies_within_one_session(self):
        """The 03:45 and 18:30 bars stored on 2026-09-02 describe DIFFERENT
        sessions, so ranking never gets to choose between them."""
        a = R("RELIANCE.NS", dt.datetime(2026, 9, 2, 3, 45))
        b = R("RELIANCE.NS", dt.datetime(2026, 9, 2, 18, 30))
        assert a.session_date != b.session_date


class TestTheRelianceRegression:
    """The exact case from Step 2D.0, as a unit test."""

    CANON = (dt.datetime(2026, 9, 2, 3, 45), 1313.1)    # session 02-Sep
    LEGACY = (dt.datetime(2026, 9, 2, 18, 30), 1302.5)  # session 03-Sep

    def test_the_two_bars_describe_different_sessions(self):
        assert R("RELIANCE.NS", self.CANON[0]).session_date == dt.date(2026, 9, 2)
        assert R("RELIANCE.NS", self.LEGACY[0]).session_date == dt.date(2026, 9, 3)

    def test_session_02_sep_gets_its_own_close_not_the_next_days(self):
        best: dict[dt.date, tuple[int, float]] = {}
        for ts, close in (self.LEGACY, self.CANON):      # legacy first: DESC order
            r = R("RELIANCE.NS", ts)
            rank = convention_rank(r.convention)
            if r.session_date not in best or rank < best[r.session_date][0]:
                best[r.session_date] = (rank, close)
        picked = {d: v for d, (_, v) in best.items()}
        assert picked[dt.date(2026, 9, 2)] == 1313.1, "must be its OWN session's close"
        assert picked[dt.date(2026, 9, 2)] != 1302.5, "must NOT be the next session's"
        assert picked[dt.date(2026, 9, 3)] == 1302.5

    def test_descending_order_no_longer_decides(self):
        """The old bug was ORDER BY timestamp DESC + first-wins. Feeding the
        rows in either order must now give the same answer."""
        def pick(rows):
            best = {}
            for ts, close in rows:
                r = R("RELIANCE.NS", ts)
                k = convention_rank(r.convention)
                if r.session_date not in best or k < best[r.session_date][0]:
                    best[r.session_date] = (k, close)
            return {d: v for d, (_, v) in best.items()}
        assert pick([self.LEGACY, self.CANON]) == pick([self.CANON, self.LEGACY])


class TestIndexRouting:

    def test_nse_indices_classify_for_the_canonical_pipeline(self):
        for s in ("^NSEI", "^NSEBANK", "^INDIAVIX"):
            assert classify_instrument(s) is InstrumentClass.NSE_INDEX

    def test_non_nse_indices_do_not(self):
        """^BSESN has no Upstox key. Routing it canonically would fail closed
        and silently empty /india/market-indices and _REGIME_DAILY_SYMBOLS."""
        assert classify_instrument("^BSESN") is InstrumentClass.OTHER_INDEX

    def test_fetch_routes_nse_index_to_the_canonical_delegate(self):
        import ast
        import inspect

        from crawler import india_price_feed as ipf
        tree = ast.parse(inspect.getsource(ipf.fetch_nse_candles))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        called = {getattr(n.func, "id", getattr(n.func, "attr", None))
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "_delegate_daily_equity_to_canonical" in called
        assert "NSE_INDEX" in names, "index class must participate in the routing test"


class TestAlignedClosesUsesTheResolver:

    def test_it_no_longer_groups_by_calendar_date(self):
        """Asserted against the AST, not the source text.

        The function's docstring legitimately names `_trading_date(` while
        explaining the bug it replaced; a substring search over source would
        match that prose and fail for the wrong reason.
        """
        import ast
        import inspect

        from engine.agent.performance_engine import _aligned_closes

        tree = ast.parse(inspect.getsource(_aligned_closes))
        called = {getattr(n.func, "id", getattr(n.func, "attr", None))
                  for n in ast.walk(tree) if isinstance(n, ast.Call)}
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                imported.update(a.name for a in n.names)

        # Step 2D.2 moved the resolution one level down: _aligned_closes now
        # delegates to engine.daily_series, which every other daily reader also
        # uses, so there is exactly one implementation to audit. The guarantee
        # is unchanged and is asserted here at both levels.
        assert "session_closes" in imported | called
        assert "_trading_date" not in called, "must not fall back to the old mapper"

        from engine import daily_series

        shared = ast.parse(inspect.getsource(daily_series))
        shared_names = {getattr(n.func, "id", getattr(n.func, "attr", None))
                        for n in ast.walk(shared) if isinstance(n, ast.Call)}
        for n in ast.walk(shared):
            if isinstance(n, ast.ImportFrom):
                shared_names.update(a.name for a in n.names)

        assert "resolve_daily_session_date" in shared_names
        assert "convention_rank" in shared_names
