"""Tests for the one-off Upstox shadow backfill.

The script is a standalone utility, so these cover the parts that decide whether
it can corrupt anything: what it selects, what it refuses to persist, and
whether a failure on one symbol can affect another.

It must never be importable by production — it lives in scripts/ and is loaded
here by path, the same way an operator invokes it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest

from scripts import oneoff_upstox_backfill as bf

_PATH = Path(bf.__file__).resolve()

CANON = dt.time(3, 45)


def _c(sym="RELIANCE.NS", d=dt.date(2026, 9, 3), t=CANON, **kw):
    base = {"symbol": sym, "timeframe": "1d", "open": 100.0, "high": 110.0,
            "low": 95.0, "close": 105.0, "volume": 1000.0,
            "timestamp": dt.datetime.combine(d, t)}
    base.update(kw)
    return base


# ── date-range construction ──────────────────────────────────────────────────

class TestDateRange:
    def test_default_span_is_ten_years(self):
        args = bf.build_parser().parse_args(["--pilot"])
        assert args.years == bf.DEFAULT_YEARS == 10
        to = dt.date(2026, 9, 7)
        frm = to - dt.timedelta(days=365 * args.years + 3)
        assert 9.9 <= (to - frm).days / 365.25 <= 10.1

    def test_explicit_range_overrides(self):
        a = bf.build_parser().parse_args(["--pilot", "--from-date", "2020-01-01",
                                          "--to-date", "2021-01-01"])
        assert a.from_date == "2020-01-01" and a.to_date == "2021-01-01"

    def test_defaults_are_conservative(self):
        a = bf.build_parser().parse_args(["--pilot"])
        assert a.concurrency == 20
        assert a.dry_run is False and a.resume is False
        assert a.include_etf is False, "equity-only by default"


# ── universe selection ───────────────────────────────────────────────────────

class TestUniverse:
    def test_pilot_is_ten_distinct_symbols_with_no_index(self):
        """Composition changed in Step 2H: 8 equities + 2 ETFs, reported
        separately. Indices stay out — the replacement target is equity, and an
        index carries no volume, which would distort the OHLCV checks."""
        from utils.candle_contract import InstrumentClass, classify_instrument
        assert len(bf.PILOT_SYMBOLS) == 10
        assert len(set(bf.PILOT_SYMBOLS)) == 10
        allowed = {InstrumentClass.NSE_EQUITY, InstrumentClass.ETF_OR_INAV}
        for s in bf.PILOT_SYMBOLS:
            assert classify_instrument(s) in allowed, s
            assert not s.startswith("^"), f"no index in the pilot: {s}"

    def test_pilot_covers_the_required_sectors(self):
        for required in ("RELIANCE.NS", "HDFCBANK.NS", "TCS.NS", "LT.NS"):
            assert required in bf.PILOT_SYMBOLS

    @pytest.mark.asyncio
    async def test_universe_filters_by_contract_class(self):
        """ETFs are excluded unless asked for; nothing is silently dropped."""
        class _R:
            def __init__(s, t, k): s.tradingsymbol, s.instrument_key = t, k

        class _Res:
            def __init__(s, rows): s._rows = rows
            def all(s): return s._rows

        class _S:
            def __init__(s, rows): s._rows = rows
            async def execute(s, *a, **k): return _Res(s._rows)

        rows = [_R("RELIANCE", "NSE_EQ|A"), _R("NIFTYBEES", "NSE_EQ|B"),
                _R("TCS", "NSE_EQ|C")]
        eq = await bf.build_universe(_S(rows))
        assert {s for s, _ in eq} == {"RELIANCE.NS", "TCS.NS"}

        withetf = await bf.build_universe(_S(rows), include_etf=True)
        assert "NIFTYBEES.NS" in {s for s, _ in withetf}

    @pytest.mark.asyncio
    async def test_keys_are_never_manufactured(self):
        class _R:
            def __init__(s, t, k): s.tradingsymbol, s.instrument_key = t, k
        class _Res:
            def __init__(s, rows): s._rows = rows
            def all(s): return s._rows
        class _S:
            def __init__(s, rows): s._rows = rows
            async def execute(s, *a, **k): return _Res(s._rows)
        out = await bf.build_universe(_S([_R("RELIANCE", "NSE_EQ|A")]))
        assert out == [("RELIANCE.NS", "NSE_EQ|A")]


# ── validation: what must never reach the database ───────────────────────────

class TestValidation:
    def setup_method(self):
        self.stats = bf.Stats()

    def test_canonical_batch_passes(self):
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(d=dt.date(2026, 9, 3))], self.stats)
        assert ok is True, why

    def test_rejects_0000(self):
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(t=dt.time(0, 0))], self.stats)
        assert ok is False and "03:45" in why
        assert self.stats.violations["not_0345"] == 1

    def test_rejects_1830(self):
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(t=dt.time(18, 30))], self.stats)
        assert ok is False and self.stats.violations["not_0345"] == 1

    def test_rejects_weekend_session(self):
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(d=dt.date(2026, 9, 5))], self.stats)  # Sat
        assert ok is False and "weekend" in why
        assert self.stats.violations["weekend_or_holiday"] == 1

    def test_rejects_future_session(self):
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(d=dt.date(2099, 1, 1))], self.stats)
        assert ok is False and "future" in why

    def test_rejects_duplicate_session_dates(self):
        d = dt.date(2026, 9, 3)
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(d=d), _c(d=d)], self.stats)
        assert ok is False and "duplicate" in why

    @pytest.mark.parametrize("bad", [
        {"high": 90.0},                 # high below open
        {"low": 120.0},                 # low above close
        {"close": 200.0},               # close above high
        {"open": float("nan")},         # NaN
    ])
    def test_rejects_broken_ohlc(self, bad):
        ok, _ = bf.validate_batch("RELIANCE.NS", [_c(**bad)], self.stats)
        assert ok is False

    def test_rejects_negative_volume(self):
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(volume=-5.0)], self.stats)
        assert ok is False and "volume" in why

    def test_one_bad_bar_rejects_the_whole_batch(self):
        """All-or-nothing: a symbol is never left half-backfilled."""
        good = [_c(d=dt.date(2026, 9, 1)), _c(d=dt.date(2026, 9, 2))]
        ok, _ = bf.validate_batch("RELIANCE.NS", good + [_c(t=dt.time(0, 0))], self.stats)
        assert ok is False

    def test_empty_batch_is_not_success(self):
        ok, _ = bf.validate_batch("RELIANCE.NS", [], self.stats)
        assert ok is False


# ── checkpoint / resume ──────────────────────────────────────────────────────

class TestCheckpoint:
    def test_roundtrip_and_resume_skips_only_success(self, tmp_path):
        p = tmp_path / "log.csv"
        bf.append_checkpoint(p, bf.Result(symbol="A.NS", status=bf.SUCCESS, rows_saved=10))
        bf.append_checkpoint(p, bf.Result(symbol="B.NS", status=bf.ERROR,
                                          error_type="HTTPError", error_message="500"))
        bf.append_checkpoint(p, bf.Result(symbol="C.NS", status=bf.NO_DATA))
        bf.append_checkpoint(p, bf.Result(symbol="D.NS", status=bf.NO_KEY))
        done = bf.load_completed(p)
        assert done == {"A.NS"}, "only SUCCESS may be skipped on resume"

    def test_log_is_append_only(self, tmp_path):
        p = tmp_path / "log.csv"
        for s in ("A.NS", "B.NS", "C.NS"):
            bf.append_checkpoint(p, bf.Result(symbol=s, status=bf.SUCCESS))
        assert len(p.read_text().strip().splitlines()) == 4      # header + 3

    def test_every_required_field_is_recorded(self, tmp_path):
        p = tmp_path / "log.csv"
        bf.append_checkpoint(p, bf.Result(symbol="A.NS", status=bf.SUCCESS))
        header = p.read_text().splitlines()[0].split(",")
        for f in ("timestamp", "symbol", "instrument_key", "status", "rows_received",
                  "rows_saved", "first_session", "last_session", "error_type",
                  "error_message", "elapsed_seconds", "attempt"):
            assert f in header

    def test_missing_checkpoint_means_nothing_done(self, tmp_path):
        assert bf.load_completed(tmp_path / "nope.csv") == set()


# ── fetch behaviour: retry, dry-run, failure isolation ───────────────────────

class TestFetchBehaviour:
    pytestmark = pytest.mark.asyncio

    async def test_retries_with_bounded_attempts_then_errors(self, monkeypatch):
        calls = {"n": 0}
        async def boom(*a, **k):
            calls["n"] += 1
            raise ConnectionError("transient")
        monkeypatch.setattr(bf, "BACKOFF_BASE", 0)   # 0**n == 0 -> no real delay
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", boom)

        st = bf.Stats()
        r = await bf.backfill_symbol("X.NS", "K", "2020-01-01", "2020-02-01",
                                     dry_run=True, stats=st)
        assert r.status == bf.ERROR
        assert calls["n"] == bf.MAX_ATTEMPTS, "must stop, not retry forever"
        assert st.retries == bf.MAX_ATTEMPTS - 1
        assert r.error_type == "ConnectionError"

    async def test_recovers_when_a_retry_succeeds(self, monkeypatch):
        state = {"n": 0}
        async def flaky(*a, **k):
            state["n"] += 1
            if state["n"] < 3:
                raise TimeoutError("blip")
            return [_c(d=dt.date(2026, 9, 3))]
        monkeypatch.setattr(bf, "BACKOFF_BASE", 0)   # 0**n == 0 -> no real delay
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", flaky)

        st = bf.Stats()
        r = await bf.backfill_symbol("X.NS", "K", "2020-01-01", "2020-02-01",
                                     dry_run=True, stats=st)
        assert r.status == bf.SUCCESS and r.attempt == 3

    async def test_dry_run_persists_nothing(self, monkeypatch):
        async def ok(*a, **k): return [_c(d=dt.date(2026, 9, 3))]
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", ok)

        called = {"save": 0}
        import crawler.price_feed as pf
        async def spy(*a, **k):
            called["save"] += 1
            return 1
        monkeypatch.setattr(pf, "save_candles_to_db", spy)

        st = bf.Stats()
        r = await bf.backfill_symbol("X.NS", "K", "2026-09-01", "2026-09-03",
                                     dry_run=True, stats=st)
        assert r.status == bf.SUCCESS and r.rows_saved == 0
        assert called["save"] == 0, "dry-run must not touch the database"

    async def test_empty_response_is_no_data_not_error(self, monkeypatch):
        async def none(*a, **k): return []
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", none)
        r = await bf.backfill_symbol("X.NS", "K", "2020-01-01", "2020-02-01",
                                     dry_run=True, stats=bf.Stats())
        assert r.status == bf.NO_DATA

    async def test_invalid_data_is_error_and_saves_nothing(self, monkeypatch):
        async def bad(*a, **k): return [_c(t=dt.time(0, 0))]
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", bad)
        called = {"save": 0}
        import crawler.price_feed as pf
        async def spy(*a, **k):
            called["save"] += 1
            return 1
        monkeypatch.setattr(pf, "save_candles_to_db", spy)

        st = bf.Stats()
        r = await bf.backfill_symbol("X.NS", "K", "2026-09-01", "2026-09-03",
                                     dry_run=False, stats=st)
        assert r.status == bf.ERROR and r.error_type == "ValidationError"
        assert called["save"] == 0
        assert st.rows_rejected == 1

    async def test_one_symbol_failing_does_not_affect_another(self, monkeypatch):
        async def per_symbol(symbol, *a, **k):
            if symbol == "BAD.NS":
                raise ConnectionError("down")
            return [_c(sym=symbol, d=dt.date(2026, 9, 3))]
        monkeypatch.setattr(bf, "BACKOFF_BASE", 0)   # 0**n == 0 -> no real delay
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", per_symbol)

        st = bf.Stats()
        bad, good = await asyncio.gather(
            bf.backfill_symbol("BAD.NS", "K", "2026-09-01", "2026-09-03", dry_run=True, stats=st),
            bf.backfill_symbol("GOOD.NS", "K", "2026-09-01", "2026-09-03", dry_run=True, stats=st),
        )
        assert bad.status == bf.ERROR
        assert good.status == bf.SUCCESS, "failure must be isolated per symbol"


# ── persistence contract ─────────────────────────────────────────────────────

class TestPersistenceContract:
    pytestmark = pytest.mark.asyncio

    async def test_saves_through_the_choke_point_with_the_contract_on(self, monkeypatch):
        seen = {}
        async def ok(*a, **k): return [_c(d=dt.date(2026, 9, 3))]
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", ok)

        import crawler.price_feed as pf
        async def spy(candles, session, **kw):
            seen.update(kw); seen["n"] = len(candles)
            return len(candles)
        monkeypatch.setattr(pf, "save_candles_to_db", spy)

        r = await bf.backfill_symbol("X.NS", "K", "2026-09-01", "2026-09-03",
                                     dry_run=False, stats=bf.Stats())
        assert r.status == bf.SUCCESS
        assert seen.get("source") == "oneoff-upstox-backfill"
        # enforce_contract must be left at its default True, and the backfill
        # must never claim the current-session refresh another writer owns.
        assert seen.get("enforce_contract", True) is True
        assert seen.get("refresh_current_session", False) is False

    async def test_idempotent_second_run_saves_zero(self, monkeypatch):
        """ON CONFLICT DO NOTHING: the second pass inserts nothing."""
        async def ok(*a, **k): return [_c(d=dt.date(2026, 9, 3))]
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", ok)

        state = {"first": True}
        import crawler.price_feed as pf
        async def spy(candles, session, **kw):
            if state["first"]:
                state["first"] = False
                return len(candles)
            return 0
        monkeypatch.setattr(pf, "save_candles_to_db", spy)

        st = bf.Stats()
        a = await bf.backfill_symbol("X.NS", "K", "2026-09-01", "2026-09-03", dry_run=False, stats=st)
        b = await bf.backfill_symbol("X.NS", "K", "2026-09-01", "2026-09-03", dry_run=False, stats=st)
        assert a.rows_saved == 1 and b.rows_saved == 0


class TestScriptHygiene:
    """Static guarantees — no event loop needed."""

    def test_script_has_no_delete_or_update(self):
        """Additive only — no DELETE, TRUNCATE or blanket UPDATE anywhere."""
        src = _PATH.read_text().upper()
        for banned in ("DELETE FROM", "TRUNCATE", "UPDATE CANDLES"):
            assert banned not in src, f"{banned} present in a shadow backfill"

    def test_script_is_not_imported_by_production(self):
        import subprocess
        out = subprocess.run(
            ["grep", "-rn", "oneoff_upstox_backfill", "--include=*.py",
             "engine", "crawler", "tasks", "api", "utils", "paper_trading"],
            cwd=str(_PATH.parent.parent), capture_output=True, text=True).stdout
        assert out.strip() == "", f"production imports the one-off script:\n{out}"


# ── Step 2H adjustments: special sessions, class split, reproducibility ──────

class TestNseSpecialSessions:
    """NSE trades on ten weekend dates in ten years; the legacy series carries
    all of them, so a canonical series that cannot must not be called a
    replacement."""

    def test_the_ten_known_sessions_are_declared(self):
        assert len(bf.NSE_SPECIAL_SESSIONS) == 10
        for d in ("2019-10-27", "2020-02-01", "2023-11-12", "2025-02-01", "2026-02-01"):
            assert d in bf.NSE_SPECIAL_SESSIONS

    def test_every_declared_date_really_is_a_weekend(self):
        for d in bf.NSE_SPECIAL_SESSIONS:
            assert dt.date.fromisoformat(d).weekday() >= 5, d

    def test_a_muhurat_session_now_validates(self):
        st = bf.Stats()
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(d=dt.date(2019, 10, 27))], st)
        assert ok is True, why

    def test_an_ordinary_weekend_is_still_rejected(self):
        st = bf.Stats()
        ok, why = bf.validate_batch("RELIANCE.NS", [_c(d=dt.date(2026, 9, 5))], st)
        assert ok is False and "weekend" in why

    def test_contract_default_is_unchanged_by_the_new_parameter(self):
        """extra_open is opt-in: omitting it must behave exactly as before."""
        from utils.candle_contract import validate_canonical_daily_equity_candle as v
        bar = _c(d=dt.date(2019, 10, 27))
        assert v(bar)[0] is False, "default must still refuse a weekend"
        assert v(bar, extra_open={"2019-10-27"})[0] is True


class TestPilotComposition:
    def test_pilot_is_eight_equities_plus_two_etfs(self):
        from utils.candle_contract import InstrumentClass, classify_instrument
        assert len(bf.PILOT_EQUITY) == 8 and len(bf.PILOT_ETF) == 2
        assert bf.PILOT_SYMBOLS == bf.PILOT_EQUITY + bf.PILOT_ETF
        for s in bf.PILOT_EQUITY:
            assert classify_instrument(s) is InstrumentClass.NSE_EQUITY, s
        for s in bf.PILOT_ETF:
            assert classify_instrument(s) is InstrumentClass.ETF_OR_INAV, s

    def test_classes_are_distinguishable_in_results(self):
        r = bf.Result(symbol="X.NS", instrument_class="nse_equity")
        assert r.instrument_class == "nse_equity"


class TestReproducibilityCheck:
    """The strongest price-basis validation: existing Upstox-written 03:45 rows
    against a fresh Upstox fetch. Preserved as an explicit step, not a one-off."""
    pytestmark = pytest.mark.asyncio

    async def test_identical_rows_count_as_reproducible(self, monkeypatch):
        import scripts.oneoff_upstox_backfill as mod

        class _Res:
            def __init__(s, rows): s._rows = rows
            def all(s): return s._rows
        class _S:
            def __init__(s, rows): s._rows = rows
            async def execute(s, *a, **k): return _Res(s._rows)
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False

        stored = [(dt.datetime(2026, 9, 3, 3, 45), 105.0, 1000.0)]
        import db.database as dbmod
        monkeypatch.setattr(dbmod, "AsyncSessionLocal", lambda: _S(stored))

        r = bf.Result(symbol="X.NS")
        await mod.compare_against_existing("X.NS", [_c(d=dt.date(2026, 9, 3))], r)
        assert r.refetch_compared == 1 and r.refetch_identical == 1

    async def test_a_changed_price_is_flagged_not_hidden(self, monkeypatch):
        import scripts.oneoff_upstox_backfill as mod

        class _Res:
            def __init__(s, rows): s._rows = rows
            def all(s): return s._rows
        class _S:
            def __init__(s, rows): s._rows = rows
            async def execute(s, *a, **k): return _Res(s._rows)
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False

        stored = [(dt.datetime(2026, 9, 3, 3, 45), 999.0, 1000.0)]   # disagrees
        import db.database as dbmod
        monkeypatch.setattr(dbmod, "AsyncSessionLocal", lambda: _S(stored))

        r = bf.Result(symbol="X.NS")
        await mod.compare_against_existing("X.NS", [_c(d=dt.date(2026, 9, 3))], r)
        assert r.refetch_compared == 1 and r.refetch_identical == 0

    async def test_the_check_never_blocks_a_backfill(self, monkeypatch):
        import scripts.oneoff_upstox_backfill as mod
        import db.database as dbmod
        def boom(): raise RuntimeError("db down")
        monkeypatch.setattr(dbmod, "AsyncSessionLocal", boom)
        r = bf.Result(symbol="X.NS")
        await mod.compare_against_existing("X.NS", [_c()], r)   # must not raise
        assert r.refetch_compared == 0


class TestPersistencePassesSpecialSessions:
    pytestmark = pytest.mark.asyncio

    async def test_extra_open_reaches_the_choke_point(self, monkeypatch):
        seen = {}
        async def ok(*a, **k): return [_c(d=dt.date(2026, 9, 3))]
        import crawler.upstox_candles as uc
        monkeypatch.setattr(uc, "get_upstox_candles_for_range", ok)
        import crawler.price_feed as pf
        async def spy(candles, session, **kw):
            seen.update(kw); return len(candles)
        monkeypatch.setattr(pf, "save_candles_to_db", spy)

        await bf.backfill_symbol("X.NS", "K", "2026-09-01", "2026-09-03",
                                 dry_run=False, stats=bf.Stats())
        assert seen.get("extra_open") == bf.NSE_SPECIAL_SESSIONS
