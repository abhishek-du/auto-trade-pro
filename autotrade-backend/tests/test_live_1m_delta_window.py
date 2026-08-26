"""sync_live_1m_candles must ask Kite only for the bars it is missing.

Before 2026-08-26 this function fetched [09:15 .. now] for EVERY symbol on
EVERY run. Measured in production that day:

    14:51:20  {'symbols': 2560, 'candles': 738902, 'saved': 65495}   1107s
    15:13:24  {'symbols': 2560, 'candles': 807064, 'saved': 16679}
    15:39:35  {'symbols': 2560, 'candles': 863744, 'saved': 20491}

~800,000 candles fetched to persist ~20,000. A run took 10-18 minutes against
a 3-minute beat schedule, so the Redis NX lock dropped ~70% of dispatches and
the effective refresh cadence became the run duration — live p50 candle lag of
16 minutes across 1,743 symbols.

These tests pin the contract of the fix. They do not touch the network or the
database: the Kite fetcher is replaced with a recorder, and the session is a
stub that returns whatever last-bar rows the test wants.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import crawler.zerodha_historical as zh

IST = ZoneInfo("Asia/Kolkata")


class _Row:
    """One row of the MAX(timestamp) GROUP BY symbol lookup."""

    def __init__(self, symbol, mx):
        self.symbol = symbol
        self.mx = mx


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _StubSession:
    """Returns preset last-bar rows; records whether it was asked at all."""

    def __init__(self, rows=(), raise_on_execute=False):
        self._rows = list(rows)
        self.raise_on_execute = raise_on_execute
        self.executed = 0
        self.committed = 0

    async def execute(self, *_a, **_kw):
        self.executed += 1
        if self.raise_on_execute:
            raise RuntimeError("simulated DB failure")
        return _Result(self._rows)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        pass


def _install_recorder(monkeypatch, per_symbol_candles=None):
    """Replace the Kite fetcher with one that records its from_dt per symbol."""
    calls: dict[str, tuple] = {}

    async def _fake_fetch(symbol, from_dt, to_dt, interval="1m"):
        calls[symbol] = (from_dt, to_dt, interval)
        return list((per_symbol_candles or {}).get(symbol, []))

    monkeypatch.setattr(zh, "get_kite_candles_for_range", _fake_fetch)
    return calls


def _install_saver(monkeypatch, saved=0):
    seen = {}

    async def _fake_save(candles, session):
        seen["candles"] = list(candles)
        return saved

    monkeypatch.setattr(zh, "save_candles_to_db", _fake_save)
    return seen


def _utc_naive_from_ist(hh, mm):
    """A UTC-naive stored timestamp for today's HH:MM IST, as candles stores it."""
    today_ist = dt.datetime.now(IST).replace(
        hour=hh, minute=mm, second=0, microsecond=0
    )
    return today_ist.astimezone(dt.timezone.utc).replace(tzinfo=None)


class TestDeltaWindow:
    def test_symbol_with_a_recent_bar_is_asked_only_for_the_delta(self, monkeypatch):
        """The whole point: do not re-request the day we already hold."""
        calls = _install_recorder(monkeypatch)
        _install_saver(monkeypatch)
        session = _StubSession(rows=[_Row("AAA.NS", _utc_naive_from_ist(11, 42))])

        asyncio.run(zh.sync_live_1m_candles(session, symbols=["AAA"], delay_sec=0))

        from_dt, _to, _iv = calls["AAA"]
        assert from_dt.hour == 11 and from_dt.minute == 42, (
            f"expected the delta to start at the last stored bar (11:42 IST), "
            f"got {from_dt}"
        )

    def test_symbol_with_no_bar_today_still_gets_the_full_day(self, monkeypatch):
        """A cold symbol must not be starved — first fetch is the full window."""
        calls = _install_recorder(monkeypatch)
        _install_saver(monkeypatch)
        session = _StubSession(rows=[])  # nothing stored for anyone

        asyncio.run(zh.sync_live_1m_candles(session, symbols=["COLD"], delay_sec=0))

        from_dt, _to, _iv = calls["COLD"]
        assert (from_dt.hour, from_dt.minute) == (9, 15), (
            f"a symbol with no bar today must fall back to 09:15, got {from_dt}"
        )

    def test_mixed_universe_gets_per_symbol_windows(self, monkeypatch):
        """Warm and cold symbols in one run must not share a window."""
        calls = _install_recorder(monkeypatch)
        _install_saver(monkeypatch)
        session = _StubSession(rows=[
            _Row("WARM.NS", _utc_naive_from_ist(14, 5)),
            # COLD.NS deliberately absent
        ])

        asyncio.run(
            zh.sync_live_1m_candles(session, symbols=["WARM", "COLD"], delay_sec=0)
        )

        assert (calls["WARM"][0].hour, calls["WARM"][0].minute) == (14, 5)
        assert (calls["COLD"][0].hour, calls["COLD"][0].minute) == (9, 15)

    def test_suffixed_symbols_resolve_against_the_stored_name(self, monkeypatch):
        """.BO must not be rewritten to .NS when looking up the last bar."""
        calls = _install_recorder(monkeypatch)
        _install_saver(monkeypatch)
        session = _StubSession(rows=[_Row("XYZ.BO", _utc_naive_from_ist(10, 30))])

        asyncio.run(zh.sync_live_1m_candles(session, symbols=["XYZ.BO"], delay_sec=0))

        assert (calls["XYZ.BO"][0].hour, calls["XYZ.BO"][0].minute) == (10, 30)


class TestFailSafe:
    def test_lookup_failure_falls_back_to_the_old_full_day_behaviour(self, monkeypatch):
        """A DB problem must degrade to the previous behaviour, never skip work.

        This is the safety property that makes the change deployable: if the
        delta lookup cannot run for any reason, every symbol is fetched exactly
        as it was before 2026-08-26.
        """
        calls = _install_recorder(monkeypatch)
        _install_saver(monkeypatch)
        session = _StubSession(raise_on_execute=True)

        result = asyncio.run(
            zh.sync_live_1m_candles(session, symbols=["AAA", "BBB"], delay_sec=0)
        )

        for sym in ("AAA", "BBB"):
            assert (calls[sym][0].hour, calls[sym][0].minute) == (9, 15)
        assert result["delta_syms"] == 0

    def test_a_failing_symbol_does_not_stop_the_others(self, monkeypatch):
        """gather(return_exceptions=True) — one bad symbol must not lose a run."""
        _install_saver(monkeypatch)

        async def _fake_fetch(symbol, from_dt, to_dt, interval="1m"):
            if symbol == "BAD":
                raise RuntimeError("broker said no")
            return [{"symbol": f"{symbol}.NS", "timestamp": None}]

        monkeypatch.setattr(zh, "get_kite_candles_for_range", _fake_fetch)
        session = _StubSession(rows=[])

        result = asyncio.run(
            zh.sync_live_1m_candles(session, symbols=["GOOD", "BAD"], delay_sec=0)
        )

        assert result["errors"] == 1
        assert result["completed"] == 1
        assert result["candles"] == 1, "the healthy symbol's bar must still be saved"


class TestObservability:
    def test_summary_carries_the_timing_split(self, monkeypatch):
        """Phase 17 could only derive transform+DB as a residual of the total."""
        _install_recorder(monkeypatch)
        _install_saver(monkeypatch)
        session = _StubSession(rows=[])

        result = asyncio.run(
            zh.sync_live_1m_candles(session, symbols=["AAA"], delay_sec=0)
        )

        for key in ("lookup_ms", "fetch_ms", "transform_ms", "db_ms",
                    "completed", "delta_syms"):
            assert key in result, f"summary is missing {key}"
            assert isinstance(result[key], int)
            assert result[key] >= 0, f"{key} must be monotonic-derived, not negative"

    def test_timings_are_monotonic_not_wall_clock(self):
        """A wall-clock source would let NTP make a duration negative."""
        import ast
        import inspect

        src = inspect.getsource(zh.sync_live_1m_candles)
        tree = ast.parse(src.lstrip())
        used = {
            ast.unparse(n.func)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        timing = {u for u in used if "monotonic" in u or "utcnow" in u or "now" in u}
        assert any("monotonic" in u for u in timing), "no monotonic clock found"
        assert not any("utcnow" in u for u in timing), (
            "durations must not be derived from utcnow()"
        )


class TestNoSensitiveLogging:
    def test_summary_contains_no_payloads_or_credentials(self, monkeypatch):
        """The summary is logged verbatim — it must stay free of payloads."""
        _install_recorder(monkeypatch)
        _install_saver(monkeypatch)
        session = _StubSession(rows=[])

        result = asyncio.run(
            zh.sync_live_1m_candles(session, symbols=["AAA"], delay_sec=0)
        )

        blob = repr(result).lower()
        for banned in ("token", "api_key", "secret", "authorization", "password"):
            assert banned not in blob, f"summary leaks {banned}"
        # Counts and durations only — no symbol lists, no candle bodies.
        assert all(isinstance(v, int) for v in result.values())
