"""STEP 2N — the frozen V1 baseline contract, enforced in code.

These test the BUILDER's pure logic, not a database. Under pytest
`DATABASE_URL` points at the empty `autotrade_test`, so the materialized
dataset is verified separately by `scripts/verify_v1_baseline.py`, which
re-derives every claim against production. What is testable without a database
is the part that decides which rows exist and what goes in them — and that is
exactly where a contract silently rots.

The builder imports pandas/numpy but NOT tensorflow, so a module-scope import
here is safe (see the 2M.1 segfault note in test_step2m1_target_integrity).
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from scripts import v1_contract as C
from scripts.build_v1_baseline import (
    _consecutive_up, _ema_sma_seeded, _fmt, _proxy_features, _symbol_frame,
    admit_row,
)


def _cal(dates):
    return {d: i for i, d in enumerate(dates)}


def _weekdays(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _bars(sessions, closes=None, *, convention=C.CANONICAL_CONVENTION):
    closes = closes or [100.0 + i * 0.5 for i in range(len(sessions))]
    return [(s, c, c * 1.01, c * 0.99, c, 10_000.0, convention)
            for s, c in zip(sessions, closes)]


# ── next-session index semantics ─────────────────────────────────────────────

class TestNextSessionSemantics:

    def test_adjacent_sessions_are_admitted(self):
        s = _weekdays(dt.date(2020, 1, 1), 400)
        ok, why = admit_row(300, s, _cal(s))
        assert ok and why == ""

    def test_a_skipped_session_is_rejected(self):
        """The symbol traded Monday and Wednesday; Tuesday was a market session
        it missed. Two calendar days — a calendar-day rule would wave it
        through."""
        cal = _weekdays(dt.date(2020, 1, 1), 400)
        sym = [d for d in cal if d != cal[301]]
        ok, why = admit_row(300, sym, _cal(cal))
        assert not ok and why == "NOT_NEXT_SESSION"
        assert (sym[301] - sym[300]).days <= 4, "the calendar rule would admit this"

    def test_calendar_gap_is_not_the_test(self):
        """Five calendar days across a long weekend, zero missed sessions."""
        cal = _weekdays(dt.date(2020, 1, 1), 400)
        i = next(k for k in range(260, 390) if (cal[k + 1] - cal[k]).days >= 3)
        ok, _ = admit_row(i, cal, _cal(cal))
        assert ok
        assert (cal[i + 1] - cal[i]).days >= 3

    def test_off_calendar_session_is_rejected(self):
        s = _weekdays(dt.date(2020, 1, 1), 400)
        cal = _cal(s)
        del cal[s[301]]
        ok, why = admit_row(300, s, cal)
        assert not ok and why == "SESSION_OFF_CALENDAR"

    def test_last_bar_has_no_target(self):
        s = _weekdays(dt.date(2020, 1, 1), 300)
        ok, why = admit_row(len(s) - 1, s, _cal(s))
        assert not ok and why == "NO_TARGET"


# ── long weekends and declared special sessions ──────────────────────────────

class TestHolidayHandling:

    def test_friday_to_special_saturday_is_adjacent(self):
        """2024-03-02 was a declared NSE Saturday session. Friday → Saturday is
        one session apart and must be admitted."""
        from utils.candle_contract import NSE_SPECIAL_SESSIONS
        assert "2024-03-02" in {str(x) for x in NSE_SPECIAL_SESSIONS}
        cal = _weekdays(dt.date(2023, 1, 2), 300)
        sat = dt.date(2024, 3, 2)
        cal = sorted(set(cal) | {sat})
        i = cal.index(sat) - 1
        assert i >= C.WARMUP_SESSIONS - 1
        ok, _ = admit_row(i, cal, _cal(cal))
        assert ok

    def test_multi_day_exchange_closure_is_admitted(self):
        """A week the exchange never opened. Both sides are still adjacent
        sessions on the calendar, so the row stands."""
        cal = _weekdays(dt.date(2020, 1, 1), 500)
        closed = {d for d in cal if dt.date(2021, 1, 4) <= d <= dt.date(2021, 1, 8)}
        cal = [d for d in cal if d not in closed]
        i = next(k for k in range(C.WARMUP_SESSIONS - 1, len(cal) - 1)
                 if (cal[k + 1] - cal[k]).days >= 7)
        assert (cal[i + 1] - cal[i]).days >= 7
        ok, _ = admit_row(i, cal, _cal(cal))
        assert ok, "a week-long closure with no missed session is still adjacent"


# ── suspension / listing boundary ────────────────────────────────────────────

class TestSuspensionAndListing:

    def test_multi_year_suspension_is_rejected(self):
        """The RAINBOW.NS shape: a symbol's next stored bar is years later."""
        cal = _weekdays(dt.date(2018, 1, 1), 1500)
        sym = cal[:300] + cal[1200:]
        ok, why = admit_row(299, sym, _cal(cal))
        assert not ok and why == "NOT_NEXT_SESSION"

    def test_listing_boundary_cannot_survive_warmup(self):
        """A boundary inside the first 10 sessions is removed by warm-up before
        the session rule is even consulted."""
        cal = _weekdays(dt.date(2018, 1, 1), 1500)
        sym = cal[:8] + cal[1200:]
        ok, why = admit_row(7, sym, _cal(cal))
        assert not ok and why == "WARMUP"

    def test_short_inactivity_is_rejected(self):
        cal = _weekdays(dt.date(2020, 1, 1), 400)
        sym = [d for d in cal if d not in cal[301:304]]
        ok, why = admit_row(300, sym, _cal(cal))
        assert not ok and why == "NOT_NEXT_SESSION"


# ── warm-up ──────────────────────────────────────────────────────────────────

class TestWarmup:

    def test_frozen_value_is_252(self):
        assert C.WARMUP_SESSIONS == 252
        assert C.MIN_SESSIONS_PER_SYMBOL == 253

    def test_row_251_is_rejected_and_252_admitted(self):
        s = _weekdays(dt.date(2020, 1, 1), 400)
        cal = _cal(s)
        assert admit_row(250, s, cal) == (False, "WARMUP")
        assert admit_row(251, s, cal)[0] is True

    def test_warmup_covers_the_longest_lookback(self):
        """252 is not a taste — it is the 52-week window, and nothing in the
        catalog looks back further."""
        s = _weekdays(dt.date(2020, 1, 1), 260)
        d = _symbol_frame(_bars(s), {})
        assert not math.isnan(d["high_52w"].iloc[251])
        assert math.isnan(d["high_52w"].iloc[250])

    def test_no_feature_is_imputed_before_warmup(self):
        s = _weekdays(dt.date(2020, 1, 1), 260)
        d = _symbol_frame(_bars(s), {})
        for col in ("sma_200", "ema_200", "high_52w", "low_52w"):
            assert d[col].iloc[:199].isna().all(), f"{col} filled during warm-up"


# ── PIT: a feature at D may never see D+1 ────────────────────────────────────

class TestPointInTime:

    def test_features_are_unchanged_by_appending_the_future(self):
        """The decisive property. Compute row D from a series that stops at D,
        then from one that runs 40 sessions further, and compare."""
        s = _weekdays(dt.date(2019, 1, 1), 400)
        closes = list(np.linspace(100, 400, 400))
        short = _symbol_frame(_bars(s[:301], closes[:301]), {})
        long_ = _symbol_frame(_bars(s, closes), {})
        cols = [c for c in C.PRICE_FEATURES + C.VOLUME_FEATURES]
        a = short[cols].iloc[300]
        b = long_[cols].iloc[300]
        for c in cols:
            x, y = a[c], b[c]
            if isinstance(x, float) and math.isnan(x):
                assert math.isnan(y), c
            else:
                assert abs(float(x) - float(y)) < 1e-9, f"{c} changed when the future arrived"

    def test_a_future_spike_does_not_move_row_d(self):
        s = _weekdays(dt.date(2019, 1, 1), 400)
        closes = list(np.linspace(100, 400, 400))
        calm = _symbol_frame(_bars(s, closes), {})
        spiked = list(closes)
        spiked[301] = 4000.0
        loud = _symbol_frame(_bars(s, spiked), {})
        assert abs(calm["high_20d"].iloc[300] - loud["high_20d"].iloc[300]) < 1e-9
        assert abs(calm["realised_vol_21d"].iloc[300]
                   - loud["realised_vol_21d"].iloc[300]) < 1e-9

    def test_breakout_uses_the_prior_window_not_todays_high(self):
        """`breakout_20d` compares D's close against the 20 sessions BEFORE D;
        including D's own high would make it true by construction."""
        s = _weekdays(dt.date(2020, 1, 1), 60)
        closes = [100.0] * 59 + [200.0]
        d = _symbol_frame(_bars(s, closes), {})
        assert d["breakout_20d"].iloc[59] == 1.0
        assert d["breakout_20d"].iloc[58] == 0.0


# ── market proxy ─────────────────────────────────────────────────────────────

class TestMarketProxy:

    def test_proxy_features_are_backward_only(self):
        s = _weekdays(dt.date(2019, 1, 1), 300)
        closes = list(np.linspace(100, 200, 300))
        short = _proxy_features([(x, c, c, c, c, 1.0) for x, c in zip(s[:250], closes[:250])])
        long_ = _proxy_features([(x, c, c, c, c, 1.0) for x, c in zip(s, closes)])
        for k in ("mkt_return_1d", "mkt_return_21d", "mkt_vol_21d"):
            assert abs(short[s[249]][k] - long_[s[249]][k]) < 1e-12, k

    def test_proxy_is_keyed_by_session_not_position(self):
        s = _weekdays(dt.date(2019, 1, 1), 60)
        p = _proxy_features([(x, 100.0 + i, 100.0 + i, 100.0 + i, 100.0 + i, 1.0)
                             for i, x in enumerate(s)])
        assert set(p) == set(s)

    def test_proxy_name_is_frozen(self):
        assert C.MARKET_PROXY_NAME == "NIFTYBEES_MARKET_PROXY"
        assert C.MARKET_PROXY_SYMBOL == "NIFTYBEES.NS"

    def test_beta_uses_the_full_symbol_series(self):
        """beta_63d must be defined on the first emitted row. An earlier build
        computed it over the KEPT rows and nulled the first 63 per symbol —
        2,065 rows in a 40-symbol trial."""
        s = _weekdays(dt.date(2019, 1, 1), 400)
        rng = np.random.default_rng(0)
        mkt = {x: float(r) for x, r in zip(s, rng.normal(0, 0.01, len(s)))}
        d = _symbol_frame(_bars(s, list(100 * np.cumprod(1 + rng.normal(0, 0.01, len(s))))), mkt)
        assert not math.isnan(d["beta_63d"].iloc[C.WARMUP_SESSIONS - 1])


# ── canonical timestamp selection ────────────────────────────────────────────

class TestCanonicalSelection:

    def test_only_the_canonical_convention_is_named(self):
        assert C.CANONICAL_CONVENTION == "canonical_0345"

    def test_legacy_conventions_are_not_the_canonical_one(self):
        from utils.candle_contract import DailyConvention
        assert DailyConvention.CANONICAL_0345.value == C.CANONICAL_CONVENTION
        for legacy in (DailyConvention.LEGACY_1830, DailyConvention.LEGACY_0000):
            assert legacy.value != C.CANONICAL_CONVENTION

    def test_universe_is_equities_only(self):
        from utils.candle_contract import classify_instrument, InstrumentClass
        for ctx in ("NIFTYBEES.NS", "BANKBEES.NS", "^NSEI", "^NSEBANK"):
            assert classify_instrument(ctx) is not InstrumentClass.NSE_EQUITY

    def test_invit_and_reit_series_are_not_equities(self):
        from utils.candle_contract import classify_instrument, InstrumentClass
        for t in ("IRBINVIT-IV.NS", "PGINVIT-IV.NS", "EMBASSY-RR.NS", "NXST-RR.NS"):
            assert classify_instrument(t) is InstrumentClass.NON_EQUITY_SERIES


# ── quarantine ───────────────────────────────────────────────────────────────

class TestQuarantine:

    def test_threshold_is_frozen(self):
        assert C.EXTREME_RETURN_THRESHOLD == 0.50

    def test_flag_is_a_stored_column(self):
        assert "extreme_return_flag" in C.COLUMNS
        assert "extreme_return_flag" not in C.FEATURES

    def test_flag_is_not_a_target(self):
        assert "extreme_return_flag" not in C.TARGETS

    def test_corporate_action_suspect_is_gone(self):
        """2M.1 proved none of these is a corporate action; the old column name
        asserted something false."""
        assert "corporate_action_suspect" not in C.COLUMNS


# ── key and targets ──────────────────────────────────────────────────────────

class TestKeyAndTargets:

    def test_key_columns_are_frozen(self):
        assert C.KEY_COLUMNS[:3] == ["symbol", "prediction_session", "target_session"]

    def test_calendar_gap_is_carried_but_is_not_a_feature(self):
        assert "calendar_gap_days" in C.COLUMNS
        assert "calendar_gap_days" not in C.FEATURES

    def test_three_raw_targets_only(self):
        assert C.TARGETS == ["next_session_return", "next_session_high_return",
                             "next_session_low_return"]

    def test_no_label_column_exists(self):
        for banned in ("label", "signal", "buy", "sell", "hold", "y"):
            assert banned not in C.COLUMNS

    def test_baseline_excludes_news_and_fundamentals(self):
        joined = " ".join(C.FEATURES).lower()
        for banned in ("news", "sentiment", "sector", "fii", "dii", "pre_open",
                       "iep", "earnings", "pe_ratio", "eps"):
            assert banned not in joined, f"{banned} leaked into the baseline"

    def test_column_order_is_deterministic(self):
        assert C.COLUMNS == (C.KEY_COLUMNS + C.CONTEXT_COLUMNS
                             + C.TARGETS + C.FEATURES)


# ── serialisation determinism ────────────────────────────────────────────────

class TestDeterministicOutput:

    def test_nan_serialises_as_empty_not_zero(self):
        assert _fmt(float("nan")) == ""
        assert _fmt(np.float64("nan")) == ""
        assert _fmt(0.0) == "0"

    def test_floats_are_fixed_precision(self):
        assert _fmt(1 / 3) == _fmt(1 / 3)
        assert _fmt(0.1 + 0.2) == "0.3"

    def test_bools_and_dates_round_trip(self):
        assert _fmt(True) == "1" and _fmt(False) == "0"
        assert _fmt(dt.date(2026, 9, 7)) == "2026-09-07"


# ── small numeric helpers ────────────────────────────────────────────────────

class TestHelpers:

    def test_ema_is_sma_seeded(self):
        x = np.arange(1.0, 21.0)
        e = _ema_sma_seeded(x, 5)
        assert math.isnan(e[3])
        assert abs(e[4] - x[:5].mean()) < 1e-12

    def test_ema_short_series_is_all_nan(self):
        assert np.isnan(_ema_sma_seeded(np.arange(3.0), 5)).all()

    def test_consecutive_up_resets_and_caps(self):
        c = np.array([1, 2, 3, 2, 3, 4, 5], dtype=float)
        r = _consecutive_up(c)
        assert list(r[:4]) == [0, 1, 2, 0]
        assert _consecutive_up(np.arange(1.0, 30.0)).max() == 10

    def test_locked_bar_nulls_are_not_zeros(self):
        s = _weekdays(dt.date(2020, 1, 1), 30)
        bars = [(x, 100.0, 100.0, 100.0, 100.0, 5.0, C.CANONICAL_CONVENTION) for x in s]
        d = _symbol_frame(bars, {})
        assert d["locked_bar_flag"].iloc[-1] == 1
        for col in C.NULLABLE_ON_LOCKED_BAR:
            assert math.isnan(d[col].iloc[-1]), f"{col} was filled on a locked bar"
