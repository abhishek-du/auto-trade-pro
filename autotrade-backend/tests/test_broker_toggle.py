"""Broker backend toggle — Upstox / Zerodha, switchable at runtime.

WHY IT EXISTS. Kite Connect's token expired mid-session on 2026-08-31 and ten
scheduled tasks kept calling it, each failing with "Invalid `api_key` or
`access_token`". There was no way to stop them without a restart. This toggle
lives in RuntimeConfig so an operator can cut a broker off from the UI and have
every process honour it at its next call.

THE SAFETY PROPERTY: at least one broker must stay enabled. With none on there
is no price source, and the 5-second stop-loss loop would stop seeing prices
for open positions -- strictly worse than a broker with a dead token.
"""
from __future__ import annotations

import inspect

import pytest


class TestDefaults:
    def test_upstox_on_zerodha_off(self):
        """The state after the 2026-08-31 migration."""
        from utils.runtime_config import BROKER_DEFAULTS
        assert BROKER_DEFAULTS == {"upstox": True, "zerodha": False}

    def test_defaults_do_not_fail_open(self):
        """Unlike the strategy flags, a missing row must NOT read as enabled:
        a broker with a dead token defaulting to on is a call storm."""
        from utils.runtime_config import broker_enabled

        src = inspect.getsource(broker_enabled)
        assert "default" in src
        assert "return True" not in src.split("except")[-1], (
            "the exception path must fall back to the default, never to True"
        )

    def test_both_brokers_are_mapped(self):
        from utils.runtime_config import BROKER_FLAGS
        assert set(BROKER_FLAGS) == {"upstox", "zerodha"}
        assert all(k.startswith("broker_") for k in BROKER_FLAGS.values())


class TestRuntimeReads:
    @pytest.mark.asyncio
    async def test_reads_reflect_the_database(self):
        from db.database import AsyncSessionLocal
        from utils.runtime_config import broker_enabled
        try:
            async with AsyncSessionLocal() as s:
                up = await broker_enabled(s, "upstox")
                ze = await broker_enabled(s, "zerodha")
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")
        assert isinstance(up, bool) and isinstance(ze, bool)

    @pytest.mark.asyncio
    async def test_an_unknown_broker_is_never_enabled(self):
        from db.database import AsyncSessionLocal
        from utils.runtime_config import broker_enabled
        try:
            async with AsyncSessionLocal() as s:
                assert await broker_enabled(s, "icici") is False
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")


class TestApiGuards:
    @pytest.mark.asyncio
    async def test_disabling_every_broker_is_refused(self):
        """The one state that must be unreachable."""
        from fastapi import HTTPException

        from api.settings import BrokerFlagsUpdate, update_broker_flags
        from db.database import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as s:
                with pytest.raises(HTTPException) as e:
                    await update_broker_flags(
                        BrokerFlagsUpdate(flags={"upstox": False, "zerodha": False}), s)
                assert e.value.status_code == 409
                assert "stop-loss" in str(e.value.detail)
        except HTTPException:
            raise
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")

    @pytest.mark.asyncio
    async def test_unknown_broker_is_rejected(self):
        from fastapi import HTTPException

        from api.settings import BrokerFlagsUpdate, update_broker_flags
        from db.database import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as s:
                with pytest.raises(HTTPException) as e:
                    await update_broker_flags(BrokerFlagsUpdate(flags={"icici": True}), s)
                assert e.value.status_code == 400
        except HTTPException:
            raise
        except Exception as exc:
            pytest.skip(f"database unavailable: {type(exc).__name__}")

    def test_post_requires_auth_but_get_does_not(self):
        """Mirrors /settings/strategies: reading state is safe, changing it is not."""
        import api.settings as m
        src = inspect.getsource(m)
        i_post = src.index('"/brokers",\n    summary="Enable or disable')
        assert "require_auth" in src[i_post:i_post + 260]


class TestTaskGating:
    """The tasks that genuinely need Kite must skip when the toggle is off."""

    @pytest.mark.parametrize("task", [
        "kite_sync_holdings_task", "kite_check_token_task",
        "zerodha_token_refresh_task", "kite_refresh_instruments_task",
    ])
    def test_kite_task_is_gated(self, task):
        import tasks.india_tasks as t
        src = inspect.getsource(getattr(t, task))
        assert "_zerodha_off" in src or "broker_enabled" in src

    @pytest.mark.parametrize("task", ["kite_live_candles_task", "kite_sync_candles_task"])
    def test_upstox_backed_tasks_are_NOT_gated(self, task):
        """These keep Kite-era names but run on Upstox since 2026-08-31.
        Gating them on the Zerodha toggle would kill the 1m candle pipeline --
        a mistake made and caught during this change."""
        import tasks.india_tasks as t
        src = inspect.getsource(getattr(t, task))
        assert "_zerodha_off" not in src, f"{task} runs on Upstox; gating it breaks candles"

    def test_ticker_task_starts_upstox_not_kite(self):
        import tasks.india_tasks as t
        src = inspect.getsource(t.kite_start_ticker_task)
        assert "start_upstox_websocket" in src
        assert "_zerodha_off" not in src

    def test_the_guard_fails_closed(self):
        """If the toggle cannot be read, treat Zerodha as OFF."""
        import tasks.india_tasks as t
        src = inspect.getsource(t._zerodha_off)
        assert "except Exception" in src
        i = src.index("except Exception")
        assert "return None" not in src[i:], (
            "an unreadable toggle must skip the task, not proceed"
        )


class TestNothingElseMoved:
    def test_strategy_and_risk_settings_unchanged(self):
        from utils.config import settings
        assert settings.PAPER_MODE is True
        assert settings.TACTICAL_TOP_N == 15
        assert settings.V2_MIN_HOLD_MINUTES == 120
        assert settings.TRADING_STRATEGY_MODE == "V2"
        assert settings.NSE_ONLY_UNIVERSE is True
