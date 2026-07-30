"""Regression tests for crawler/upstox_auth.py::ensure_upstox_token_fresh()
(2026-07-29 cross-process cascade fix).

Root cause: token-freshness state (_state dict) is per-OS-process, but this
codebase runs the check from 4 separate celery worker child processes
(prefork pool) plus uvicorn plus celery beat, each with its own independent
copy. Confirmed live via celery_worker.log: 7 full Upstox re-logins in 5
hours one day, 13 in under 5 hours another, two of them 3 seconds apart from
different ForkPoolWorkers -- instead of the intended once-per-morning
refresh. Root mechanism: Upstox invalidates the previous access token the
instant a new one is issued (single-active-session), so whenever any ONE
process refreshes, every OTHER process's in-memory token silently goes
stale, and each of THEM independently triggers its own fresh TOTP login on
next check -- a self-perpetuating cascade, each iteration of which looks
like a fresh account login to Upstox (almost certainly why the user was
getting an OTP challenge repeatedly instead of once a day).

Fix: before doing a REAL login, reload the token from .env (a sibling
process may have refreshed moments ago) and re-verify THAT. Only fall
through to an actual TOTP login if the reloaded token is ALSO invalid.

All tests are deterministic and mocked -- no real network, no real Upstox
login attempted (which would itself trigger the exact problem being fixed).
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

import crawler.upstox_auth as ua


@pytest.fixture(autouse=True)
def _reset_state():
    ua._state.update({
        "last_verified_ts": 0.0, "last_verified_ok": False,
        "last_refresh_ts": None, "last_failure_ts": None,
        "last_failure_reason": None, "failure_count": 0,
    })
    yield
    ua._state.update({
        "last_verified_ts": 0.0, "last_verified_ok": False,
        "last_refresh_ts": None, "last_failure_ts": None,
        "last_failure_reason": None, "failure_count": 0,
    })


class TestSiblingProcessTokenPickup:
    @pytest.mark.asyncio
    async def test_reload_from_env_avoids_real_login_when_sibling_already_refreshed(self):
        """Own in-memory token fails verification, but a sibling process
        already wrote a fresh one to .env -- ensure_upstox_token_fresh()
        should pick that up instead of doing its own TOTP login."""
        with patch("crawler.upstox_auth.settings") as mock_settings, \
             patch("crawler.upstox_auth.verify_upstox_token", AsyncMock(side_effect=[False, True])), \
             patch("crawler.upstox_auth._reload_token_from_env") as mock_reload, \
             patch("crawler.upstox_auth.refresh_upstox_token_with_retry", AsyncMock()) as mock_refresh:
            mock_settings.UPSTOX_ACCESS_TOKEN = "stale-token"
            mock_settings.UPSTOX_API_KEY = "key"
            mock_settings.UPSTOX_API_SECRET = "secret"

            result = await ua.ensure_upstox_token_fresh()

        assert result is True
        mock_reload.assert_called_once()
        mock_refresh.assert_not_called()   # the whole point: no real login happened

    @pytest.mark.asyncio
    async def test_real_login_only_happens_when_reloaded_env_token_also_fails(self):
        """Both the in-memory token AND the freshly-reloaded .env token fail
        verification -- only then should a real TOTP login be attempted."""
        with patch("crawler.upstox_auth.settings") as mock_settings, \
             patch("crawler.upstox_auth.verify_upstox_token", AsyncMock(return_value=False)), \
             patch("crawler.upstox_auth._reload_token_from_env"), \
             patch("crawler.upstox_auth.os.path.getmtime", return_value=0.0), \
             patch("crawler.upstox_auth.refresh_upstox_token_with_retry",
                   AsyncMock(return_value=True)) as mock_refresh:
            mock_settings.UPSTOX_ACCESS_TOKEN = "stale-token"
            mock_settings.UPSTOX_API_KEY = "key"
            mock_settings.UPSTOX_API_SECRET = "secret"

            result = await ua.ensure_upstox_token_fresh()

        assert result is True
        mock_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_recently_written_env_file_triggers_wait_and_recheck_not_immediate_login(self):
        """Near-simultaneous race guard: if .env was written moments ago (a
        sibling is almost certainly mid-refresh right now), wait briefly and
        re-check rather than piling on with a second concurrent login."""
        now = time.time()
        with patch("crawler.upstox_auth.settings") as mock_settings, \
             patch("crawler.upstox_auth.verify_upstox_token",
                   AsyncMock(side_effect=[False, False, False, True])), \
             patch("crawler.upstox_auth._reload_token_from_env"), \
             patch("crawler.upstox_auth.os.path.getmtime", return_value=now - 5), \
             patch("crawler.upstox_auth.asyncio.sleep", AsyncMock()) as mock_sleep, \
             patch("crawler.upstox_auth.refresh_upstox_token_with_retry", AsyncMock()) as mock_refresh:
            mock_settings.UPSTOX_ACCESS_TOKEN = "stale-token"
            mock_settings.UPSTOX_API_KEY = "key"
            mock_settings.UPSTOX_API_SECRET = "secret"

            result = await ua.ensure_upstox_token_fresh()

        assert result is True
        mock_sleep.assert_called_once()
        mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_recent_successful_verification_is_cached_no_network_call(self):
        ua._state["last_verified_ok"] = True
        ua._state["last_verified_ts"] = time.time()
        with patch("crawler.upstox_auth.settings") as mock_settings, \
             patch("crawler.upstox_auth.verify_upstox_token", AsyncMock()) as mock_verify:
            mock_settings.UPSTOX_ACCESS_TOKEN = "token"
            mock_settings.UPSTOX_API_KEY = "key"
            mock_settings.UPSTOX_API_SECRET = "secret"

            result = await ua.ensure_upstox_token_fresh()

        assert result is True
        mock_verify.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_token_and_no_credentials_returns_false_without_any_call(self):
        with patch("crawler.upstox_auth.settings") as mock_settings, \
             patch("crawler.upstox_auth.verify_upstox_token", AsyncMock()) as mock_verify, \
             patch("crawler.upstox_auth.refresh_upstox_token_with_retry", AsyncMock()) as mock_refresh:
            mock_settings.UPSTOX_ACCESS_TOKEN = ""
            mock_settings.UPSTOX_API_KEY = ""
            mock_settings.UPSTOX_API_SECRET = ""

            result = await ua.ensure_upstox_token_fresh()

        assert result is False
        mock_verify.assert_not_called()
        mock_refresh.assert_not_called()
