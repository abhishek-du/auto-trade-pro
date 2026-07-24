"""Regression tests for utils/llm.py::call_mantle_chat()'s connection-error
handling and circuit breaker.

Rewritten 2026-07-24 for the Amazon Nova Pro / boto3 Bedrock Converse
provider (previously openai.gpt-oss-120b via the Mantle OpenAI-compatible
gateway). The retry/circuit-breaker DESIGN is unchanged and still guards the
same 2026-07-22 regression this file originally documented (a single
transient error must not poison a 20s window of unrelated candidate
evaluations) -- only the mocked client shape and exception types changed to
match boto3/botocore instead of the `openai` SDK. Nova has no
reasoning_effort/reasoning-channel concept, so those assertions are gone.

These tests mock _mantle_async_client() (never touching the real network)
and reset the module-level _mantle_blocked_until between tests, since it's
shared global state. IMPORTANT: call_mantle_chat looks up the client via the
`_mantle_async_client` name at call time (not `_nova_client` directly) so
that patching this name here actually intercepts -- see utils/llm.py's
comment at the client lookup for why that distinction matters.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

import utils.llm as llm_mod
from utils.llm import call_mantle_chat

# Captured at import time, before the autouse fixture below patches the
# module-attribute name -- lets TestSharedRateLimiter call the REAL function
# against a mocked Redis, while every other test in this file gets the
# no-op version so it never touches the actual shared instance.
_real_acquire_llm_rate_slot = llm_mod._acquire_llm_rate_slot


def _connect_timeout() -> ConnectTimeoutError:
    return ConnectTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com")


def _read_timeout() -> ReadTimeoutError:
    return ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com")


def _client_error(code: str, message: str = "error") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


def _internal_server_error() -> ClientError:
    return _client_error("InternalServerException", "internal server error")


def _throttling_error() -> ClientError:
    return _client_error("ThrottlingException", "too many requests")


def _access_denied_error() -> ClientError:
    return _client_error("AccessDeniedException", "not authorized")


def _make_response(content: str = "hello") -> dict:
    """boto3 Converse API response shape."""
    return {"output": {"message": {"content": [{"text": content}]}}}


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    llm_mod._mantle_blocked_until = 0.0
    llm_mod._mantle_consecutive_failures = 0
    llm_mod._mantle_block_logged = False
    yield
    llm_mod._mantle_blocked_until = 0.0
    llm_mod._mantle_consecutive_failures = 0
    llm_mod._mantle_block_logged = False


@pytest.fixture(autouse=True)
def _no_shared_rate_limit():
    """The 2026-07-24 Redis-backed rate limiter (_acquire_llm_rate_slot) is
    deliberately SHARED across every real process on this AWS account (see
    its docstring) -- tests must never hit the real Redis instance for this,
    both because it makes tests slow (waiting on a live production queue)
    and because it would steal quota slots from the actual running pipeline.
    Patched to a no-op for the whole file."""
    with patch("utils.llm._acquire_llm_rate_slot", AsyncMock(return_value=None)):
        yield


class TestConnectionErrorImmediateRetry:
    @pytest.mark.asyncio
    async def test_connection_error_then_success_recovers_without_backoff(self):
        # THE regression guard: a single transient connection error must NOT
        # trip the circuit breaker if the immediate retry succeeds.
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_connect_timeout(), _make_response("recovered")])
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result == "recovered"
        assert client.converse.call_count == 2
        assert llm_mod._mantle_blocked_until == 0.0  # breaker never tripped

    @pytest.mark.asyncio
    async def test_two_consecutive_connection_errors_trips_breaker(self):
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_connect_timeout(), _read_timeout()])
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result is None
        assert client.converse.call_count == 2
        assert llm_mod._mantle_blocked_until > 0.0

    @pytest.mark.asyncio
    async def test_non_transient_error_does_not_get_immediate_retry(self):
        # A validation error is not a transient blip -- only ONE attempt
        # should be made, then the breaker trips.
        client = MagicMock()
        client.converse = MagicMock(side_effect=_client_error("ValidationException"))
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result is None
        assert client.converse.call_count == 1
        assert llm_mod._mantle_blocked_until > 0.0


class TestCircuitBreakerDuration:
    @pytest.mark.asyncio
    async def test_generic_failure_backs_off_shorter_than_before(self):
        # Base backoff for a transient, non-auth failure that persists past
        # the immediate retry: ~8s plus up to 2s of jitter.
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_connect_timeout(), _connect_timeout()])
        before = __import__("time").monotonic()
        with patch("utils.llm._mantle_async_client", return_value=client):
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        remaining = llm_mod._mantle_blocked_until - before
        assert 8.0 <= remaining <= 10.5

    @pytest.mark.asyncio
    async def test_auth_error_still_backs_off_120s(self):
        # Auth/quota errors are NOT transient -- retrying sooner cannot
        # help, a human has to fix the credential/limit. Must keep the
        # long 120s backoff.
        client = MagicMock()
        client.converse = MagicMock(side_effect=_access_denied_error())
        before = __import__("time").monotonic()
        with patch("utils.llm._mantle_async_client", return_value=client):
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        remaining = llm_mod._mantle_blocked_until - before
        assert remaining > 100

    @pytest.mark.asyncio
    async def test_blocked_window_short_circuits_without_network_call(self):
        llm_mod._mantle_blocked_until = __import__("time").monotonic() + 5.0
        client = MagicMock()
        client.converse = MagicMock(return_value=_make_response("should not be reached"))
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result is None
        client.converse.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_call_clears_a_previously_tripped_breaker(self):
        llm_mod._mantle_blocked_until = 0.0  # not currently blocked
        client = MagicMock()
        client.converse = MagicMock(return_value=_make_response("ok"))
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result == "ok"
        assert llm_mod._mantle_blocked_until == 0.0


class TestEmptyContentHandling:
    """Nova Pro is not a chain-of-thought reasoning model (unlike the
    previous gpt-oss provider), so empty-content-on-a-successful-call isn't
    an expected failure mode -- but the defensive one-plain-retry-then-give-up
    path is still exercised here since the code keeps it as a fallback."""

    @pytest.mark.asyncio
    async def test_empty_content_returns_none_without_tripping_breaker(self):
        client = MagicMock()
        client.converse = MagicMock(return_value=_make_response(""))
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result is None
        assert llm_mod._mantle_blocked_until == 0.0

    @pytest.mark.asyncio
    async def test_empty_content_retries_once_and_recovers(self):
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_make_response(""), _make_response("recovered")])
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result == "recovered"
        assert client.converse.call_count == 2
        assert llm_mod._mantle_blocked_until == 0.0


class TestInternalServerErrorImmediateRetry:
    """AWS's own Bedrock troubleshooting docs classify InternalServerException
    as transient and retry-recoverable, same as a dropped connection."""

    @pytest.mark.asyncio
    async def test_internal_server_error_then_success_recovers_without_backoff(self):
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_internal_server_error(), _make_response("recovered")])
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result == "recovered"
        assert client.converse.call_count == 2
        assert llm_mod._mantle_blocked_until == 0.0  # breaker never tripped

    @pytest.mark.asyncio
    async def test_two_consecutive_internal_server_errors_trips_breaker(self):
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_internal_server_error(), _internal_server_error()])
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result is None
        assert client.converse.call_count == 2
        assert llm_mod._mantle_blocked_until > 0.0

    @pytest.mark.asyncio
    async def test_throttling_gets_immediate_retry_but_then_backs_off_120s(self):
        # Throttling gets ONE immediate retry (transient-code path) same as
        # any other transient error, but if it throttles again, that's
        # treated as a real quota signal -- long backoff, not the short one.
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_throttling_error(), _throttling_error()])
        before = __import__("time").monotonic()
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result is None
        assert client.converse.call_count == 2
        remaining = llm_mod._mantle_blocked_until - before
        assert remaining > 100


class TestExponentialBackoff:
    @pytest.mark.asyncio
    async def test_consecutive_failures_escalate_the_wait(self):
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_internal_server_error(), _internal_server_error()])
        with patch("utils.llm._mantle_async_client", return_value=client):
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        first_wait = llm_mod._mantle_blocked_until - __import__("time").monotonic()
        assert llm_mod._mantle_consecutive_failures == 1

        llm_mod._mantle_blocked_until = 0.0  # simulate the window having elapsed
        client.converse = MagicMock(side_effect=[_internal_server_error(), _internal_server_error()])
        with patch("utils.llm._mantle_async_client", return_value=client):
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        second_wait = llm_mod._mantle_blocked_until - __import__("time").monotonic()
        assert llm_mod._mantle_consecutive_failures == 2
        # Second failure's base (8*2^1=16) must exceed the first's base
        # (8*2^0=8) even accounting for up to 2s of jitter on each side.
        assert second_wait > first_wait

    @pytest.mark.asyncio
    async def test_backoff_is_capped(self):
        llm_mod._mantle_consecutive_failures = 10  # far past the cap
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_internal_server_error(), _internal_server_error()])
        with patch("utils.llm._mantle_async_client", return_value=client):
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        remaining = llm_mod._mantle_blocked_until - __import__("time").monotonic()
        assert remaining <= 62.0  # 60s cap + up to 2s jitter

    @pytest.mark.asyncio
    async def test_success_resets_the_consecutive_failure_counter(self):
        llm_mod._mantle_consecutive_failures = 3
        client = MagicMock()
        client.converse = MagicMock(return_value=_make_response("ok"))
        with patch("utils.llm._mantle_async_client", return_value=client):
            result = await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert result == "ok"
        assert llm_mod._mantle_consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_auth_error_does_not_escalate_consecutive_counter(self):
        llm_mod._mantle_consecutive_failures = 3
        client = MagicMock()
        client.converse = MagicMock(side_effect=_access_denied_error())
        with patch("utils.llm._mantle_async_client", return_value=client):
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert llm_mod._mantle_consecutive_failures == 0


class TestBlockWindowVisibility:
    """The circuit-breaker short-circuit logs once per block window, not
    once per swallowed call, so a long cascade doesn't flood the logs while
    still leaving a trace that a block was active."""

    @pytest.mark.asyncio
    async def test_logs_once_when_entering_a_blocked_window(self):
        llm_mod._mantle_blocked_until = __import__("time").monotonic() + 5.0
        client = MagicMock()
        client.converse = MagicMock(return_value=_make_response("x"))
        with patch("utils.llm._mantle_async_client", return_value=client), \
             patch("utils.llm.logger") as mock_logger:
            await call_mantle_chat([{"role": "user", "content": "hi"}])
            await call_mantle_chat([{"role": "user", "content": "hi"}])
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        warning_calls = [c for c in mock_logger.warning.call_args_list
                         if "circuit breaker open" in str(c)]
        assert len(warning_calls) == 1  # logged once, not once per swallowed call

    @pytest.mark.asyncio
    async def test_new_block_window_logs_again(self):
        client = MagicMock()
        client.converse = MagicMock(side_effect=[_internal_server_error(), _internal_server_error()])
        with patch("utils.llm._mantle_async_client", return_value=client):
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert llm_mod._mantle_block_logged is False  # reset for the new window

        client2 = MagicMock()
        client2.converse = MagicMock(return_value=_make_response("x"))
        with patch("utils.llm._mantle_async_client", return_value=client2), \
             patch("utils.llm.logger") as mock_logger:
            await call_mantle_chat([{"role": "user", "content": "hi"}])
        assert any("circuit breaker open" in str(c) for c in mock_logger.warning.call_args_list)


class TestConverseMessageConversion:
    """utils/llm.py::_to_converse_format() -- OpenAI-style [{"role","content"}]
    -> Converse API's (system_blocks, messages) shape."""

    def test_system_message_extracted_separately(self):
        from utils.llm import _to_converse_format
        system, messages = _to_converse_format([
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ])
        assert system == [{"text": "be terse"}]
        assert messages == [{"role": "user", "content": [{"text": "hi"}]}]

    def test_consecutive_same_role_messages_merged(self):
        from utils.llm import _to_converse_format
        _, messages = _to_converse_format([
            {"role": "user", "content": "first"},
            {"role": "tool", "content": "tool output"},  # maps to "user"
            {"role": "assistant", "content": "answer"},
        ])
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert "first" in messages[0]["content"][0]["text"]
        assert "tool output" in messages[0]["content"][0]["text"]
        assert messages[1] == {"role": "assistant", "content": [{"text": "answer"}]}

    def test_leading_assistant_message_gets_a_user_turn_prepended(self):
        from utils.llm import _to_converse_format
        _, messages = _to_converse_format([{"role": "assistant", "content": "hi"}])
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    def test_no_system_message_yields_empty_system_list(self):
        from utils.llm import _to_converse_format
        system, _ = _to_converse_format([{"role": "user", "content": "hi"}])
        assert system == []


class TestBudgetClamping:
    def test_ceils_at_max_output_tokens(self):
        from utils.llm import _mantle_budget
        assert _mantle_budget(999999) == llm_mod.settings.MANTLE_MAX_OUTPUT_TOKENS

    def test_floors_at_min_tokens(self):
        from utils.llm import _mantle_budget
        assert _mantle_budget(1) == llm_mod.settings.MANTLE_MIN_TOKENS

    def test_passthrough_within_bounds(self):
        from utils.llm import _mantle_budget
        assert _mantle_budget(4000) == 4000


class TestExtractJsonFromResponse:
    """utils/llm.py::extract_json_from_response() -- added 2026-07-24 after
    confirming live that Nova Pro sometimes adds explanatory prose around a
    requested "ONLY JSON" response (unlike gpt-oss, which reliably didn't),
    which broke strict json.loads() callers in engine/sector_graph.py and
    crawler/pdf_parser.py."""

    def test_none_and_empty_input(self):
        from utils.llm import extract_json_from_response
        assert extract_json_from_response(None) is None
        assert extract_json_from_response("") is None

    def test_bare_object(self):
        from utils.llm import extract_json_from_response
        assert extract_json_from_response('{"a": 1}') == {"a": 1}

    def test_bare_array(self):
        from utils.llm import extract_json_from_response
        assert extract_json_from_response('[{"a": 1}]') == [{"a": 1}]

    def test_object_with_leading_and_trailing_prose(self):
        from utils.llm import extract_json_from_response
        resp = 'Sure, here you go: {"a": 1} thanks for asking!'
        assert extract_json_from_response(resp) == {"a": 1}

    def test_array_wrapped_in_markdown_fence(self):
        from utils.llm import extract_json_from_response
        resp = '```json\n[{"a": 1}, {"b": 2}]\n```'
        assert extract_json_from_response(resp) == [{"a": 1}, {"b": 2}]

    def test_no_json_present_returns_none(self):
        from utils.llm import extract_json_from_response
        assert extract_json_from_response("not json at all") is None

    def test_array_preferred_when_it_appears_first(self):
        from utils.llm import extract_json_from_response
        resp = 'Result: [1, 2, 3] and also {"unused": true}'
        assert extract_json_from_response(resp) == [1, 2, 3]


class TestSharedRateLimiter:
    """utils/llm.py::_acquire_llm_rate_slot() -- Redis-backed, shared across
    every process on this AWS account, added 2026-07-24 after confirming the
    account's Nova Pro RPM quota can't be increased. These call the REAL
    function (captured before this file's autouse fixture patches the name)
    against a mocked Redis -- never the actual shared instance."""

    @pytest.fixture(autouse=True)
    def _reset_script_cache(self):
        llm_mod._rate_limit_script = None
        yield
        llm_mod._rate_limit_script = None

    @pytest.mark.asyncio
    async def test_acquires_immediately_when_slot_available(self):
        mock_script = AsyncMock(return_value=1)
        mock_redis = MagicMock()
        mock_redis.register_script.return_value = mock_script
        with patch("utils.cache.get_redis", return_value=mock_redis):
            await _real_acquire_llm_rate_slot()
        mock_script.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fails_open_when_redis_unreachable(self):
        with patch("utils.cache.get_redis", side_effect=ConnectionError("no redis")):
            await _real_acquire_llm_rate_slot()  # must not raise

    @pytest.mark.asyncio
    async def test_uses_configured_rpm_limit(self):
        mock_script = AsyncMock(return_value=1)
        mock_redis = MagicMock()
        mock_redis.register_script.return_value = mock_script
        with patch("utils.cache.get_redis", return_value=mock_redis), \
             patch.object(llm_mod.settings, "MANTLE_MAX_RPM", 7):
            await _real_acquire_llm_rate_slot()
        _, kwargs = mock_script.call_args
        assert kwargs["args"][0] == 7
