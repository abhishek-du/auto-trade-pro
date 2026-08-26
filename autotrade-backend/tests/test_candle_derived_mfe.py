"""MFE must say what happened to the position, not what the poller happened to see.

`paper_trades.mfe_pct` came from a tracker inside
update_positions_with_current_prices(), so it only advanced when that task
happened to run. That is bounded by trade-loop cadence (measured 53.7% coverage
with gaps to 605s) and by price freshness (measured p50 16-minute candle lag),
so any peak occurring between samples was never recorded.

Measured on 38 intraday TACTICAL trades: seventeen stored exactly 0.00 and
eleven of those had more than 0.1% favourable movement. PAYTM stored 0.00%
against a candle-derived 5.31%.

_candle_excursion reads the true intrabar extremes over the position's own
holding window. These tests pin its arithmetic and, most importantly, that it
cannot influence any trading decision.
"""
from __future__ import annotations

import ast
import asyncio
import datetime as dt
import inspect

import pytest

from paper_trading.trade_simulator import _candle_excursion


class _Row:
    def __init__(self, hi, lo):
        self.hi = hi
        self.lo = lo


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Session:
    """Returns one preset (hi, lo) and records the window it was asked for."""

    def __init__(self, hi, lo):
        self._row = _Row(hi, lo) if hi is not None else _Row(None, None)
        self.window = None

    async def execute(self, _stmt, params=None):
        self.window = params
        return _Result(self._row)


T0 = dt.datetime(2026, 8, 26, 4, 0)     # UTC-naive, as candles and paper_trades both are
T1 = dt.datetime(2026, 8, 26, 6, 30)


class TestArithmetic:
    def test_long_peak_comes_from_the_high(self):
        """The exact case the tracker misses: an intrabar spike."""
        s = _Session(hi=110.0, lo=98.0)
        peak, trough = asyncio.run(
            _candle_excursion(s, "X.NS", T0, T1, entry_price=100.0, direction="BUY", units=10)
        )
        assert peak == pytest.approx(100.0)     # (110 - 100) * 10
        assert trough == pytest.approx(-20.0)   # (98 - 100) * 10

    def test_short_inverts_favourable_and_adverse(self):
        s = _Session(hi=110.0, lo=98.0)
        peak, trough = asyncio.run(
            _candle_excursion(s, "X.NS", T0, T1, entry_price=100.0, direction="SELL", units=10)
        )
        assert peak == pytest.approx(20.0)      # (100 - 98) * 10
        assert trough == pytest.approx(-100.0)  # (100 - 110) * 10

    def test_peak_between_tracker_samples_is_captured(self):
        """PAYTM stored 0.00% while the candles showed 5.31%.

        The tracker saw only flat samples; the candle high did not care.
        """
        s = _Session(hi=105.31, lo=100.0)
        peak, _ = asyncio.run(
            _candle_excursion(s, "PAYTM.NS", T0, T1, entry_price=100.0, direction="BUY", units=100)
        )
        assert peak == pytest.approx(531.0, abs=1.0)
        assert peak > 0, "a stored 0.00 with a real favourable move is the whole defect"

    def test_position_that_never_moved_favourably(self):
        """Peak must be able to be zero — not every trade has upside."""
        s = _Session(hi=100.0, lo=95.0)
        peak, trough = asyncio.run(
            _candle_excursion(s, "X.NS", T0, T1, entry_price=100.0, direction="BUY", units=10)
        )
        assert peak == pytest.approx(0.0)
        assert trough == pytest.approx(-50.0)


class TestWindowBoundaries:
    def test_window_is_the_positions_own_holding_period(self):
        s = _Session(hi=110.0, lo=98.0)
        asyncio.run(
            _candle_excursion(s, "X.NS", T0, T1, entry_price=100.0, direction="BUY", units=10)
        )
        assert s.window["a"] == T0, "window must start at entry"
        assert s.window["b"] == T1, "window must end at this trade's own exit"

    def test_no_lookahead_past_the_exit(self):
        """The upper bound is the exit, which has already happened when this runs."""
        src = inspect.getsource(_candle_excursion)
        assert "timestamp <= :b" in src
        assert "now()" not in src and "utcnow" not in src, (
            "the window must be bounded by the trade's own timestamps, never by "
            "the wall clock"
        )

    def test_multi_day_window_is_not_truncated(self):
        s = _Session(hi=120.0, lo=90.0)
        later = T0 + dt.timedelta(days=3)
        peak, trough = asyncio.run(
            _candle_excursion(s, "X.NS", T0, later, entry_price=100.0, direction="BUY", units=5)
        )
        assert s.window["b"] == later
        assert peak == pytest.approx(100.0)


class TestDegradesSafely:
    def test_no_candles_returns_none_so_the_tracker_is_kept(self):
        s = _Session(hi=None, lo=None)
        assert asyncio.run(
            _candle_excursion(s, "X.NS", T0, T1, entry_price=100.0, direction="BUY", units=10)
        ) is None

    @pytest.mark.parametrize("bad", [
        dict(opened_at=None), dict(closed_at=None),
        dict(units=0), dict(entry_price=0),
    ])
    def test_missing_inputs_return_none_rather_than_guessing(self, bad):
        kw = dict(opened_at=T0, closed_at=T1, entry_price=100.0, direction="BUY", units=10)
        kw.update(bad)
        s = _Session(hi=110.0, lo=98.0)
        assert asyncio.run(_candle_excursion(
            s, "X.NS", kw["opened_at"], kw["closed_at"],
            kw["entry_price"], kw["direction"], kw["units"])) is None


class TestCannotAffectTrading:
    """The whole point: this is measurement, and measurement must be inert."""

    def test_it_runs_after_price_pnl_and_status_are_settled(self):
        import paper_trading.trade_simulator as ts

        src = inspect.getsource(ts.close_paper_trade)
        for field in ("trade.exit_price", "trade.pnl ", "trade.closed_at", "trade.status"):
            assert src.find(field) < src.find("_candle_excursion"), (
                f"{field} must be decided before the MFE measurement runs"
            )

    def test_failure_cannot_propagate(self):
        import paper_trading.trade_simulator as ts

        src = inspect.getsource(ts.close_paper_trade)
        idx = src.find("_candle_excursion")
        window = src[max(0, idx - 700): idx + 700]
        assert "try:" in window and "except Exception" in window, (
            "a measurement failure must never break a close"
        )

    def test_it_writes_only_excursion_fields(self):
        """It must not touch price, pnl, status, stop, target or quantity."""
        import paper_trading.trade_simulator as ts

        src = inspect.getsource(ts.close_paper_trade)
        idx = src.find("_candle_excursion")
        assert idx > 0
        # Everything from the measurement block to the end of the excursion
        # writes. Assignments there must only be to excursion fields.
        after = src[idx: src.find("trade.mae_r", idx) + 40]
        forbidden = ("trade.exit_price =", "trade.pnl =", "trade.status =",
                     "trade.stop_loss =", "trade.take_profit =",
                     "trade.size_units =", "trade.closed_at =")
        for f in forbidden:
            assert f not in after, (
                f"the measurement block assigns a trading field: {f.strip()}"
            )

    def test_only_the_excursion_columns_change(self):
        """Positive form of the same guard: name what it IS allowed to write."""
        import paper_trading.trade_simulator as ts
        import re

        src = inspect.getsource(ts.close_paper_trade)
        idx = src.find("_candle_excursion")
        after = src[idx: src.find("trade.mae_r", idx) + 40]
        written = set(re.findall(r"(trade\.[a-z_]+)\s*=", after))
        allowed = {"trade.mfe_abs", "trade.mae_abs", "trade.max_open_profit",
                   "trade.mfe_pct", "trade.mae_pct", "trade.mfe_r", "trade.mae_r"}
        assert written <= allowed, f"unexpected writes: {written - allowed}"
