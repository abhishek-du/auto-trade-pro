"""STEP 2B — Upstox market-data validation harness.

A reusable set of invariants for the market-data layer. These are DATA-CORRECTNESS
tests, deliberately separate from the connectivity/route tests that already exist:
an HTTP 200 and a connected socket prove neither identity nor arithmetic.

Deterministic throughout — no live market required, so they run in CI at any hour.
Anything that genuinely cannot be proven without a live session is asserted about
the CODE CONTRACT instead, and named as such.
"""
from __future__ import annotations

import datetime as dt

import pytest

from crawler.live_candle_builder import LiveCandleBuilder

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UTC = dt.timezone.utc


def _ts(y, m, d, hh, mm, ss=0) -> float:
    """A unix timestamp from an IST wall-clock time."""
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=IST).timestamp()


# ── §7  Live candle builder — boundaries ─────────────────────────────────────

class TestCandleBoundaries:
    """09:14:59 / 09:15:00 / 09:15:01 / 09:19:59 / 09:20:00 as specified."""

    def test_first_tick_opens_a_bucket_and_emits_nothing(self):
        b = LiveCandleBuilder()
        assert b.observe("X.NS", 100.0, 1000.0, ts=_ts(2026, 9, 4, 9, 15, 0)) is None

    def test_ticks_inside_one_minute_do_not_roll_over(self):
        b = LiveCandleBuilder()
        b.observe("X.NS", 100.0, 1000.0, ts=_ts(2026, 9, 4, 9, 15, 0))
        for sec in (1, 15, 59):
            assert b.observe("X.NS", 101.0, 1100.0, ts=_ts(2026, 9, 4, 9, 15, sec)) is None

    def test_rollover_at_the_exact_minute_boundary(self):
        b = LiveCandleBuilder()
        b.observe("X.NS", 100.0, 1000.0, ts=_ts(2026, 9, 4, 9, 15, 0))
        b.observe("X.NS", 105.0, 1200.0, ts=_ts(2026, 9, 4, 9, 15, 59))
        out = b.observe("X.NS", 106.0, 1300.0, ts=_ts(2026, 9, 4, 9, 16, 0))
        assert out is not None, "09:16:00 must close the 09:15 bar"
        # CONTRACT (verified against the running code, not assumed): the fast
        # builder emits `timestamp` as a naive-UTC ISO *string*, because the bar
        # goes to Redis as JSON. 09:15 IST == 03:45 UTC.
        assert out["timestamp"] == "2026-09-04T03:45:00"
        assert "+" not in out["timestamp"] and "Z" not in out["timestamp"], \
            "must carry no offset — the candles table is naive UTC"

    def test_ohlc_is_correct_across_a_bucket(self):
        b = LiveCandleBuilder()
        base = _ts(2026, 9, 4, 9, 15, 0)
        b.observe("X.NS", 100.0, 1000.0, ts=base)          # open
        b.observe("X.NS", 108.0, 1050.0, ts=base + 10)     # high
        b.observe("X.NS",  95.0, 1080.0, ts=base + 20)     # low
        b.observe("X.NS", 103.0, 1100.0, ts=base + 50)     # close
        c = b.observe("X.NS", 104.0, 1150.0, ts=base + 60)
        assert (c["open"], c["high"], c["low"], c["close"]) == (100.0, 108.0, 95.0, 103.0)

    def test_volume_is_a_delta_not_the_cumulative_total(self):
        """Upstox `volume_traded` is cumulative for the session (verified live
        2026-09-04: sum of 1m candle volumes == quote volume minus the partial
        minute). The bar must therefore report the DIFFERENCE."""
        b = LiveCandleBuilder()
        base = _ts(2026, 9, 4, 9, 15, 0)
        b.observe("X.NS", 100.0, 1_000_000.0, ts=base)
        b.observe("X.NS", 101.0, 1_050_000.0, ts=base + 30)
        c = b.observe("X.NS", 102.0, 1_060_000.0, ts=base + 60)
        assert c["volume"] == 50_000.0, "must be last-first within the bucket"

    def test_volume_never_goes_negative_on_a_counter_reset(self):
        b = LiveCandleBuilder()
        base = _ts(2026, 9, 4, 9, 15, 0)
        b.observe("X.NS", 100.0, 5_000.0, ts=base)
        b.observe("X.NS", 100.0, 10.0, ts=base + 30)        # reset / bad tick
        c = b.observe("X.NS", 100.0, 20.0, ts=base + 60)
        assert c["volume"] >= 0.0

    def test_five_minute_span_produces_five_separate_bars(self):
        b = LiveCandleBuilder()
        got = []
        for minute in range(15, 21):                        # 09:15 .. 09:20
            out = b.observe("X.NS", 100.0 + minute, 1000.0 * minute,
                            ts=_ts(2026, 9, 4, 9, minute, 0))
            if out:
                got.append(out["timestamp"])
        assert got == [f"2026-09-04T03:{45 + i:02d}:00" for i in range(5)]

    def test_no_duplicate_bar_for_the_same_minute(self):
        b = LiveCandleBuilder()
        base = _ts(2026, 9, 4, 9, 15, 0)
        b.observe("X.NS", 100.0, 1000.0, ts=base)
        first = b.observe("X.NS", 101.0, 1100.0, ts=base + 60)
        second = b.observe("X.NS", 102.0, 1200.0, ts=base + 90)   # same 09:16 bucket
        assert first is not None and second is None

    def test_out_of_order_tick_is_deterministic(self):
        """A late tick belonging to an already-closed minute must not resurrect
        it. Current contract: it opens a NEW bucket for its own minute, so the
        closed bar is never mutated after publication."""
        b = LiveCandleBuilder()
        base = _ts(2026, 9, 4, 9, 15, 0)
        b.observe("X.NS", 100.0, 1000.0, ts=base)
        closed = b.observe("X.NS", 110.0, 1100.0, ts=base + 60)
        assert closed["high"] == 100.0
        late = b.observe("X.NS", 999.0, 1200.0, ts=base + 5)      # belongs to 09:15
        assert late is not None and late["timestamp"] == "2026-09-04T03:46:00"
        assert closed["high"] == 100.0, "a published bar must never be mutated"

    def test_symbols_are_bucketed_independently(self):
        b = LiveCandleBuilder()
        base = _ts(2026, 9, 4, 9, 15, 0)
        b.observe("A.NS", 10.0, 100.0, ts=base)
        b.observe("B.NS", 20.0, 200.0, ts=base)
        a = b.observe("A.NS", 11.0, 150.0, ts=base + 60)
        # The bar carries no `symbol` — the caller owns that mapping. Asserting
        # it here would test a field the contract does not provide.
        assert a["open"] == 10.0 and a["close"] == 10.0
        assert "symbol" not in a

    def test_zero_or_negative_price_is_rejected(self):
        b = LiveCandleBuilder()
        base = _ts(2026, 9, 4, 9, 15, 0)
        assert b.observe("X.NS", 0.0, 100.0, ts=base) is None
        assert b.observe("X.NS", -5.0, 100.0, ts=base) is None


# ── §11  Timezone contract ───────────────────────────────────────────────────

class TestTimezoneContract:

    def test_session_open_and_close_map_to_the_expected_utc_minutes(self):
        b = LiveCandleBuilder()
        assert b._minute_of(_ts(2026, 9, 4, 9, 15)) == dt.datetime(2026, 9, 4, 3, 45)
        assert b._minute_of(_ts(2026, 9, 4, 15, 29)) == dt.datetime(2026, 9, 4, 9, 59)

    def test_upstox_candle_timestamps_convert_to_naive_utc(self):
        from crawler.upstox_candles import _to_naive_utc
        got = _to_naive_utc("2026-09-04T09:15:00+05:30")
        assert got == dt.datetime(2026, 9, 4, 3, 45) and got.tzinfo is None

    def test_daily_bar_lands_on_1830_utc_not_midnight(self):
        """00:00 UTC is the DEAD pre-split daily series in this database;
        18:30 UTC is the live one. A daily bar must never be written at 00:00."""
        from crawler.upstox_candles import _to_naive_utc
        assert _to_naive_utc("2026-09-04T00:00:00+05:30") == dt.datetime(2026, 9, 3, 18, 30)

    def test_kite_style_intervals_are_not_downgraded_to_daily(self):
        from crawler.upstox_candles import _INTERVAL_MAP
        for name, expect in [("minute", ("minutes", "1")), ("5minute", ("minutes", "5")),
                             ("15minute", ("minutes", "15")), ("30minute", ("minutes", "30")),
                             ("60minute", ("hours", "1")), ("day", ("days", "1"))]:
            assert _INTERVAL_MAP.get(name) == expect, f"{name} unmapped -> silent daily bars"


# ── §3 / §16  NSE-only identity ──────────────────────────────────────────────

class TestNseOnlyIdentity:

    def test_bse_symbols_are_rejected(self):
        from utils.symbols import nse_gate
        ok, why = nse_gate("RELIANCE.BO")
        assert ok is False and why == "BSE_SYMBOL"

    def test_bare_symbol_is_not_silently_assumed_nse(self):
        from utils.symbols import nse_gate
        assert nse_gate("RELIANCE")[0] is False

    def test_unknown_exchange_is_rejected(self):
        from utils.symbols import nse_gate
        assert nse_gate("FOO.XYZ")[0] is False

    def test_filter_returns_reasons_not_just_a_count(self):
        from utils.symbols import filter_nse_only
        ok, rejected = filter_nse_only(["A.NS", "B.BO", "C.XYZ"], allow_bare=False)
        assert ok == ["A.NS"] and sum(rejected.values()) == 2


# ── §16  OHLC / timestamp invariants, reusable on any candle list ────────────

def validate_candles(rows: list[dict]) -> list[str]:
    """Reusable validator. Returns a list of human-readable violations.

    Kept as a plain function so audit scripts and tests share ONE definition of
    'valid', rather than each re-implementing the rules slightly differently.
    """
    out: list[str] = []
    now = dt.datetime.utcnow() + dt.timedelta(minutes=5)
    seen: set[tuple] = set()
    prev_ts = None
    for i, c in enumerate(rows):
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        if h < l:                       out.append(f"[{i}] high<low")
        if not (l <= o <= h):           out.append(f"[{i}] open outside [low,high]")
        if not (l <= cl <= h):          out.append(f"[{i}] close outside [low,high]")
        if min(o, h, l, cl) <= 0:       out.append(f"[{i}] non-positive price")
        if c.get("volume", 0) < 0:      out.append(f"[{i}] negative volume")
        ts = c["timestamp"]
        if getattr(ts, "tzinfo", None) is not None:
            # Check FIRST and skip the comparison: naive vs aware raises.
            out.append(f"[{i}] tz-aware timestamp")
        elif ts > now:
            out.append(f"[{i}] future timestamp")
        key = (c.get("symbol"), c.get("timeframe"), ts)
        if key in seen:                 out.append(f"[{i}] duplicate bar {key}")
        seen.add(key)
        if prev_ts is not None and ts < prev_ts: out.append(f"[{i}] out-of-order")
        prev_ts = ts
    return out


class TestValidatorItself:
    """A validator nobody has tested is not a control."""

    def _bar(self, **kw):
        base = dict(symbol="X.NS", timeframe="1m", open=10.0, high=12.0, low=9.0,
                    close=11.0, volume=100.0, timestamp=dt.datetime(2026, 9, 4, 3, 45))
        base.update(kw)
        return base

    def test_clean_bars_produce_no_violations(self):
        assert validate_candles([self._bar()]) == []

    def test_catches_each_class_of_defect(self):
        assert validate_candles([self._bar(high=5.0)])                 # high<low
        assert validate_candles([self._bar(open=99.0)])                # open outside
        assert validate_candles([self._bar(close=99.0)])               # close outside
        assert validate_candles([self._bar(low=0.0, open=0.0)])        # non-positive
        assert validate_candles([self._bar(volume=-1.0)])              # negative volume
        assert validate_candles([self._bar(timestamp=dt.datetime(2099, 1, 1))])  # future
        assert validate_candles([self._bar(), self._bar()])            # duplicate
        assert validate_candles([self._bar(timestamp=dt.datetime(2026, 9, 4, 3, 46)),
                                 self._bar(timestamp=dt.datetime(2026, 9, 4, 3, 45))])  # order

    def test_tz_aware_timestamp_is_a_violation(self):
        assert validate_candles([self._bar(timestamp=dt.datetime(2026, 9, 4, 3, 45, tzinfo=UTC))])
