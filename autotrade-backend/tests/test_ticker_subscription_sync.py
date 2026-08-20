"""Regression — a position opened in another process must still get subscribed.

THE BUG
-------
`subscribe_open_position()` only works inside the process that owns the
WebSocket. `_resubscribe_open_positions()` begins with

    if not CONNECTED or _active_ws is None:
        return

which is a SILENT no-op. The KiteTicker runs on a daemon thread in the uvicorn
process, but trades are opened by the news engine and the Celery worker —
separate OS processes where `_active_ws` is always None. So the subscribe call
returned without subscribing, without raising, and without logging.

Kite was working correctly the whole time: it was simply never asked for that
symbol, so it never pushed a tick, and the position's price stayed frozen at the
last cached value.

Compounding it, the only caller of `subscribe_open_position()` is
engine/agent/execution.py:167, on the agent path disabled by the News-Only
TECHNICAL block — so the live path never subscribed at all, in any process.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crawler import zerodha_ticker as zt


class TestTheIsolationFailureIsSilent:
    """Characterisation: this is what went wrong, and why nothing surfaced it."""

    def test_subscribe_is_a_silent_noop_without_a_socket(self):
        with patch.object(zt, "CONNECTED", False), patch.object(zt, "_active_ws", None):
            # Must not raise — which is exactly why it went unnoticed.
            zt.subscribe_open_position("JUNIPER.NS")

    def test_symbol_is_tracked_locally_but_never_reaches_kite(self):
        """The symbol lands in this process's set, so local state LOOKS right."""
        saved = set(zt._OPEN_POSITION_SYMBOLS)
        try:
            with patch.object(zt, "CONNECTED", False), patch.object(zt, "_active_ws", None):
                zt.subscribe_open_position("JUNIPER.NS")
            assert "JUNIPER.NS" in zt._OPEN_POSITION_SYMBOLS
        finally:
            zt._OPEN_POSITION_SYMBOLS.clear()
            zt._OPEN_POSITION_SYMBOLS.update(saved)


def _db(symbols):
    """AsyncSessionLocal stub yielding the given open-position symbols."""
    result = MagicMock()
    result.fetchall.return_value = [(s,) for s in symbols]
    sess = MagicMock()
    sess.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=sess)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory


class TestReconcileFixesIt:

    @pytest.fixture(autouse=True)
    def _isolate(self):
        saved = set(zt._OPEN_POSITION_SYMBOLS)
        yield
        zt._OPEN_POSITION_SYMBOLS.clear()
        zt._OPEN_POSITION_SYMBOLS.update(saved)

    @pytest.mark.asyncio
    async def test_position_opened_elsewhere_gets_subscribed(self):
        """The JUNIPER case: in the DB, unknown to the ticker, never subscribed."""
        zt._OPEN_POSITION_SYMBOLS.clear()
        zt._OPEN_POSITION_SYMBOLS.add("ZAGGLE.BO")

        with patch.object(zt, "CONNECTED", True), \
             patch.object(zt, "_active_ws", MagicMock()), \
             patch("db.database.AsyncSessionLocal", _db(["ZAGGLE.BO", "JUNIPER.NS"])), \
             patch.object(zt, "_resubscribe_open_positions") as resub:
            out = await zt.sync_open_position_subscriptions()

        assert out["added"] == ["JUNIPER.NS"]
        resub.assert_called_once()
        assert resub.call_args.kwargs["symbols"] == {"JUNIPER.NS"}
        assert "JUNIPER.NS" in zt._OPEN_POSITION_SYMBOLS

    @pytest.mark.asyncio
    async def test_no_change_does_not_resubscribe(self):
        """Steady state must not re-issue subscribe every 30s."""
        zt._OPEN_POSITION_SYMBOLS.clear()
        zt._OPEN_POSITION_SYMBOLS.update({"ZAGGLE.BO", "JUNIPER.NS"})

        with patch.object(zt, "CONNECTED", True), \
             patch.object(zt, "_active_ws", MagicMock()), \
             patch("db.database.AsyncSessionLocal", _db(["ZAGGLE.BO", "JUNIPER.NS"])), \
             patch.object(zt, "_resubscribe_open_positions") as resub:
            out = await zt.sync_open_position_subscriptions()

        assert out["added"] == []
        resub.assert_not_called()

    @pytest.mark.asyncio
    async def test_closed_position_is_dropped_from_the_set_but_not_unsubscribed(self):
        zt._OPEN_POSITION_SYMBOLS.clear()
        zt._OPEN_POSITION_SYMBOLS.update({"ZAGGLE.BO", "JUNIPER.NS"})

        with patch.object(zt, "CONNECTED", True), \
             patch.object(zt, "_active_ws", MagicMock()), \
             patch("db.database.AsyncSessionLocal", _db(["ZAGGLE.BO"])), \
             patch.object(zt, "_resubscribe_open_positions") as resub:
            out = await zt.sync_open_position_subscriptions()

        assert out["removed"] == ["JUNIPER.NS"]
        assert "JUNIPER.NS" not in zt._OPEN_POSITION_SYMBOLS
        resub.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_in_a_process_without_the_ticker(self):
        """Celery worker / news engine must not try to subscribe."""
        with patch.object(zt, "CONNECTED", False), patch.object(zt, "_active_ws", None):
            out = await zt.sync_open_position_subscriptions()
        assert "skipped" in out

    @pytest.mark.asyncio
    async def test_db_failure_does_not_raise(self):
        """A reconcile failure must never kill the background loop."""
        factory = MagicMock(side_effect=RuntimeError("db down"))
        with patch.object(zt, "CONNECTED", True), \
             patch.object(zt, "_active_ws", MagicMock()), \
             patch("db.database.AsyncSessionLocal", factory):
            out = await zt.sync_open_position_subscriptions()
        assert "error" in out

    @pytest.mark.asyncio
    async def test_symbols_are_normalised(self):
        """DB may hold bare symbols; the ticker keys on the .NS form."""
        zt._OPEN_POSITION_SYMBOLS.clear()
        with patch.object(zt, "CONNECTED", True), \
             patch.object(zt, "_active_ws", MagicMock()), \
             patch("db.database.AsyncSessionLocal", _db(["JUNIPER"])), \
             patch.object(zt, "_resubscribe_open_positions"):
            await zt.sync_open_position_subscriptions()
        assert zt._OPEN_POSITION_SYMBOLS == {zt._normalise_symbol("JUNIPER")}


class TestWiredIntoTheTickerProcess:
    def test_uvicorn_runs_the_reconcile_loop(self):
        """The loop must live in main.py — the process that owns the socket."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent / "main.py").read_text()
        assert "sync_open_position_subscriptions" in src
        assert "_ticker_subscription_loop" in src


class TestSymbolNormalisation:
    """`_normalise_symbol` blindly appended ".NS" to anything not already
    ending in it, so a BSE holding became "ZAGGLE.BO.NS" and an index became
    "^NSEI.NS". Neither resolves to an instrument token, so
    `_resubscribe_open_positions()` silently dropped them — an independent
    reason an open BSE position's price sat frozen."""

    @pytest.mark.parametrize("raw,expected", [
        ("TCS", "TCS.NS"),                                  # bare NSE -> suffixed
        ("JUNIPER.NS", "JUNIPER.NS"),                       # already suffixed
        ("ZAGGLE.BO", "ZAGGLE.BO"),                         # BSE preserved
        ("^NSEI", "^NSEI"),                                 # index preserved
        ("^INDIAVIX", "^INDIAVIX"),
        ("NIFTY26AUG24000CE", "NIFTY26AUG24000CE"),         # option untouched
        ("BANKNIFTY26AUGFUT", "BANKNIFTY26AUGFUT"),         # future untouched
    ])
    def test_normalisation(self, raw, expected):
        assert zt._normalise_symbol(raw) == expected

    def test_never_double_suffixes(self):
        for raw in ("TCS", "TCS.NS", "ZAGGLE.BO", "^NSEI"):
            once = zt._normalise_symbol(raw)
            assert zt._normalise_symbol(once) == once, f"{raw} not idempotent"
            assert not once.endswith(".BO.NS")
            assert not once.endswith(".NS.NS")
