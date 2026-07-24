# LLM client: Amazon Nova Pro via AWS Bedrock's native Runtime Converse API.
# Single provider — no fallback chain.
#
# Switched from openai.gpt-oss-120b (Mantle OpenAI-compatible gateway) to
# amazon.nova-pro-v1:0 on 2026-07-24. Nova Pro does NOT support the
# bedrock-mantle endpoint (confirmed against AWS's own model card — only
# bedrock-runtime/Converse), so this uses boto3 instead of the `openai` SDK.
# Nova is also not a chain-of-thought reasoning model like gpt-oss was — it
# has no separate reasoning channel, so get_last_reasoning() always returns
# None now. Function names/signatures below are kept identical to the
# gpt-oss-era code (call_mantle_chat, _mantle_sync_chat, etc.) even though
# the provider changed, since ~8 other files import them directly.
#
# call_llm_chat()        — async, returns content string
# call_llm_chat_stream() — async generator, yields content chunks for SSE
# call_llm_chat_full()   — async, returns {content, reasoning, model} dict + persists to DB

import asyncio
import json as _json
import random
import re
import threading
from functools import lru_cache
from utils.config import settings
from utils.logger import logger

import time as _time
import contextvars as _contextvars


# ── Reasoning capture ─────────────────────────────────────────────────────────
# gpt-oss used to return a separate `reasoning` channel alongside the answer;
# Nova doesn't, so this now always stays None -- kept as a no-op shim so
# every existing caller of get_last_reasoning() (UI display, DB persistence)
# keeps working without a None-handling rewrite.
_LAST_REASONING: "_contextvars.ContextVar[str | None]" = _contextvars.ContextVar(
    "llm_last_reasoning", default=None
)


def _set_last_reasoning(value: "str | None") -> None:
    try:
        _LAST_REASONING.set(value)
    except Exception:
        pass


def get_last_reasoning() -> "str | None":
    """Reasoning text from the most recent LLM call on this async task, if any.

    Always None as of the 2026-07-24 Nova Pro switch -- Nova has no separate
    reasoning channel. Kept for backward compat with existing callers."""
    try:
        return _LAST_REASONING.get()
    except Exception:
        return None


# ── Robust JSON extraction ──────────────────────────────────────────────────
# Added 2026-07-24 alongside the Nova Pro switch: confirmed live that Nova
# sometimes adds a sentence of explanation before or after a requested "ONLY
# JSON, nothing else" response (unlike gpt-oss, which reliably returned bare
# JSON) -- e.g. engine/sector_graph.py and crawler/pdf_parser.py used to do a
# strict `json.loads(response.strip())` after only trimming markdown fences,
# which raises on any stray prose and falls back to an empty/default result,
# silently dropping an otherwise-good candidate. This mirrors the tolerant
# "find the first {" pattern engine/agent/decision_engine.py's ReAct loop
# already used successfully (raw_decode stops after one complete value, so
# leading/trailing text is simply ignored) -- generalized here to also handle
# a leading `[` for JSON-array responses, and exposed as a shared helper so
# every caller gets the same tolerance instead of three separate ad hoc
# implementations.
def extract_json_from_response(resp: "str | None") -> "dict | list | None":
    """Extract the first complete JSON value (object or array) from an LLM
    response, tolerating markdown fences and leading/trailing prose."""
    if not resp:
        return None
    candidates = [i for i in (resp.find("{"), resp.find("[")) if i >= 0]
    if not candidates:
        return None
    i = min(candidates)
    try:
        obj, _end = _json.JSONDecoder().raw_decode(resp[i:])
        return obj if isinstance(obj, (dict, list)) else None
    except Exception:
        return None


# ── Nova Pro (native Bedrock Converse API) — sole LLM provider ─────────────────
_mantle_blocked_until: float = 0.0
# Exponential-backoff state for the transient-error branch (throttling/server
# errors that persist past the immediate retry) -- reset to 0 on any
# successful call. Auth/quota backoff stays a flat 120s (see below); that's
# a human-fixable condition, not one exponential retry helps with.
_mantle_consecutive_failures: int = 0
# Logs the circuit-breaker short-circuit once per block window instead of
# once per swallowed call -- reset whenever a new block window is opened.
_mantle_block_logged: bool = False


@lru_cache(maxsize=1)
def _nova_client():
    """Cached boto3 bedrock-runtime client (or None).

    Reuses the same Bedrock long-term API key that used to authenticate the
    OpenAI-compatible Mantle gateway -- confirmed live 2026-07-24 that the
    identical MANTLE_API_KEY value works as a bearer token for boto3's
    native Converse API too (AWS_BEARER_TOKEN_BEDROCK env var), no separate
    IAM access-key/secret pair needed.

    read_timeout is fixed at client-construction time (boto3 has no clean
    per-call timeout override) -- 90s covers the longest caller in this
    codebase (engine/agent/decision_engine.py's ReAct loop); the `timeout`
    kwarg individual call_llm_chat*() callers pass is accepted for backward
    compat but is a no-op against this provider.
    """
    if not getattr(settings, "mantle_available", False):
        return None
    try:
        import os
        import boto3
        from botocore.config import Config
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.MANTLE_API_KEY
        return boto3.client(
            "bedrock-runtime",
            region_name=getattr(settings, "MANTLE_REGION", "us-east-1"),
            config=Config(read_timeout=90, connect_timeout=10, retries={"max_attempts": 0}),
        )
    except Exception as exc:
        logger.warning(f"[llm.nova] client init failed: {exc}")
        return None


# Old names kept as thin aliases -- nothing outside this file calls them
# directly (confirmed via grep), but keeping the names avoids any risk of a
# stale reference surviving the provider swap.
_mantle_async_client = _nova_client
_mantle_sync_client = _nova_client


def _mantle_budget(max_tokens: int) -> int:
    """Floor every request at MANTLE_MIN_TOKENS, ceil at
    MANTLE_MAX_OUTPUT_TOKENS (10,000 -- Nova Pro's real ceiling on this
    account, confirmed 2026-07-24 live via the API's own ValidationException,
    not the ~5K figure on AWS's model card page or the 300K context-window
    figure -- context window and max output tokens are different limits). A
    caller requesting more than this ceiling isn't getting a bigger budget,
    Bedrock rejects the call outright -- so clamping here keeps every caller
    honest and prevents a hard validation error reaching them."""
    lo = int(getattr(settings, "MANTLE_MIN_TOKENS", 512))
    hi = int(getattr(settings, "MANTLE_MAX_OUTPUT_TOKENS", 10000))
    return min(max(int(max_tokens or 0), lo), hi)


def _to_converse_format(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """OpenAI-style [{"role","content"}] (as built throughout this codebase's
    ReAct/chat loops) -> Converse API's (system_blocks, messages) shape,
    where messages use role user/assistant and content=[{"text": ...}]
    blocks, and system prompts are a separate top-level parameter rather
    than a message with role="system".

    Merges consecutive same-role turns (Converse requires alternating
    user/assistant) -- defensive, since callers built their message lists
    for the old OpenAI-compatible API's looser rules, including a
    role="tool" convention this maps to "user" (Converse has no bare-text
    tool-role message; this codebase's tool-use loops are prompted/parsed
    JSON over plain text, not native function-calling, so this is a faithful
    mapping, not a lossy approximation of tool semantics).
    """
    system_parts: list[str] = []
    merged: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
            continue
        conv_role = "assistant" if role == "assistant" else "user"
        if merged and merged[-1]["role"] == conv_role:
            merged[-1]["content"][0]["text"] += "\n\n" + content
        else:
            merged.append({"role": conv_role, "content": [{"text": content}]})
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": [{"text": "(continued)"}]})
    system = [{"text": "\n\n".join(system_parts)}] if system_parts else []
    return system, merged


def _extract_converse_text(resp: dict) -> str:
    blocks = ((resp.get("output") or {}).get("message") or {}).get("content") or []
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and "text" in b).strip()


# ── Shared cross-process rate limiter ───────────────────────────────────────
# AWS Bedrock's Nova Pro quota (25 RPM cross-region, confirmed live via the
# service-quotas API) is per AWS ACCOUNT, not per process -- but this
# codebase runs several independent Python processes that all call the same
# account (the news-engine systemd service, every Celery worker process, the
# uvicorn API server's chat endpoints). An in-process-only limiter can't see
# what the OTHER processes are doing, so it can't actually keep the combined
# request rate under the account's real ceiling. Since the quota itself
# can't be increased (2026-07-24 decision), every process instead checks in
# against one shared Redis counter (Celery's existing broker, already
# running) before making a real Bedrock call -- this turns "periodically
# fails with ThrottlingException" into "occasionally waits a second or two,"
# which is both faster in aggregate (no wasted failed calls + 120s circuit-
# breaker blackouts) and never exceeds the hard limit in the first place.
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = redis.call('GET', key)
if current and tonumber(current) >= limit then
    return 0
end
local new = redis.call('INCR', key)
if new == 1 then
    redis.call('EXPIRE', key, ttl)
end
return 1
"""
_rate_limit_script = None


async def _acquire_llm_rate_slot() -> None:
    """Blocks briefly until a request slot is free in the current 60s
    window, shared across every process via Redis. Fails OPEN (proceeds
    without throttling) if Redis is unreachable or after a bounded wait --
    a coordination outage or a very backed-up queue must never hang the
    whole pipeline indefinitely."""
    global _rate_limit_script
    limit = int(getattr(settings, "MANTLE_MAX_RPM", 20))
    try:
        from utils.cache import get_redis
        r = get_redis()
        if _rate_limit_script is None:
            _rate_limit_script = r.register_script(_RATE_LIMIT_LUA)
        deadline = _time.monotonic() + 90.0  # give up coordinating after 90s, proceed anyway
        while _time.monotonic() < deadline:
            bucket = int(_time.time() // 60)
            key = f"llm:rpm:{bucket}"
            acquired = await _rate_limit_script(keys=[key], args=[limit, 90])
            if acquired:
                return
            await asyncio.sleep(1.0)
        logger.debug("[llm.rate_limit] gave up waiting for a shared slot after 90s -- proceeding anyway")
    except Exception as exc:
        logger.debug(f"[llm.rate_limit] Redis coordination unavailable, proceeding without shared throttle: {exc}")


# ── Non-streaming call ────────────────────────────────────────────────────────

async def _nova_completion_once(client, *, model, system, messages, max_tokens, temperature) -> str:
    """One raw completion attempt — no retry/backoff logic, just the call.

    boto3 is synchronous; bridged to asyncio via to_thread so callers keep
    the same `await`-based interface the old AsyncOpenAI client provided."""
    await _acquire_llm_rate_slot()

    def _call():
        kwargs = dict(
            modelId=model, messages=messages,
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        if system:
            kwargs["system"] = system
        return client.converse(**kwargs)

    resp = await asyncio.to_thread(_call)
    return _extract_converse_text(resp)


# Error codes botocore's ClientError can carry for a transient, retry-worthy
# server-side condition (AWS's Bedrock troubleshooting guidance groups these
# with connection drops, not with auth/validation problems).
_NOVA_TRANSIENT_CODES = {
    "ThrottlingException", "ServiceUnavailableException",
    "InternalServerException", "ModelTimeoutException", "ModelNotReadyException",
}
# Error codes that mean a human has to fix something (credential, quota,
# malformed request) -- retrying sooner cannot help these.
_NOVA_AUTH_OR_QUOTA_CODES = {
    "AccessDeniedException", "ValidationException",
    "UnrecognizedClientException", "ExpiredTokenException", "ThrottlingException",
}


async def call_mantle_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float = 60.0,
    model: str | None = None,
) -> str | None:
    """Inference via Amazon Nova Pro (native Bedrock Runtime Converse API).

    Returns the assistant text or None on any failure. `timeout` is accepted
    for backward compat but is a no-op against this provider -- see
    _nova_client()'s docstring for why.

    Retry/circuit-breaker design carried over from the gpt-oss/Mantle era
    (see this function's git history for the full 2026-07-22 postmortem it
    was built from): retry once, immediately, on a transient connection or
    throttling error; only trip the process-wide circuit breaker if BOTH the
    original attempt and the retry fail — a real, sustained problem, not a
    one-off blip. Auth/validation errors get a flat 120s backoff with no
    retry, since a human has to fix the credential/request, not a faster
    retry loop.

    One thing that does NOT carry over: the old provider (gpt-oss-120b) was
    a chain-of-thought reasoning model whose reasoning tokens shared the
    max_tokens budget with the answer, so a successful-but-empty response
    was a real, common failure mode requiring a "retry at low reasoning
    effort" mitigation (see git history). Nova Pro has no such shared-budget
    reasoning channel, so that failure mode isn't expected here — empty
    content on a technically-successful call still gets one plain retry
    below, purely as a defensive fallback, not because it's anticipated.
    """
    global _mantle_blocked_until, _mantle_consecutive_failures, _mantle_block_logged
    _set_last_reasoning(None)   # Nova has no reasoning channel; always None
    # Looked up via the alias name, not _nova_client directly, so
    # patch("utils.llm._mantle_async_client", ...) in tests actually
    # intercepts this call (module-level name resolved fresh each call).
    client = _mantle_async_client()
    if client is None:
        return None
    if _time.monotonic() < _mantle_blocked_until:
        if not _mantle_block_logged:
            _mantle_block_logged = True
            remaining = _mantle_blocked_until - _time.monotonic()
            logger.warning(
                f"[llm.nova] circuit breaker open ({remaining:.1f}s remaining) — this and "
                "every other call made before it clears returns None with no further logging "
                "(logged once per block window, not once per call)"
            )
        return None

    from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError

    budget = _mantle_budget(max_tokens)
    mdl = model or settings.MANTLE_MODEL
    system, conv_messages = _to_converse_format(messages)
    last_exc: Exception | None = None
    content: str | None = None

    try:
        content = await _nova_completion_once(
            client, model=mdl, system=system, messages=conv_messages,
            max_tokens=budget, temperature=temperature,
        )
    except Exception as exc:
        is_conn = isinstance(exc, (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError))
        is_transient = (
            isinstance(exc, ClientError)
            and exc.response.get("Error", {}).get("Code") in _NOVA_TRANSIENT_CODES
        )
        if is_conn or is_transient:
            logger.debug(f"[llm.nova] {type(exc).__name__} on first attempt — retrying once immediately")
            try:
                content = await _nova_completion_once(
                    client, model=mdl, system=system, messages=conv_messages,
                    max_tokens=budget, temperature=temperature,
                )
            except Exception as exc2:
                last_exc = exc2
        else:
            last_exc = exc

    if last_exc is None:
        _mantle_blocked_until = 0.0
        _mantle_consecutive_failures = 0
        _mantle_block_logged = False
        if content:
            return content
        logger.warning("[llm.nova] empty content on a successful response (unexpected for Nova) — retrying once")
        try:
            content = await _nova_completion_once(
                client, model=mdl, system=system, messages=conv_messages,
                max_tokens=budget, temperature=temperature,
            )
            _mantle_blocked_until = 0.0
            _mantle_consecutive_failures = 0
            _mantle_block_logged = False
            if content:
                return content
            logger.warning("[llm.nova] empty content on retry too — giving up")
            return None
        except Exception as exc:
            last_exc = exc

    if isinstance(last_exc, ClientError):
        _code = last_exc.response.get("Error", {}).get("Code", "")
        is_auth_or_quota = _code in _NOVA_AUTH_OR_QUOTA_CODES
    else:
        _msg = str(last_exc).lower()
        is_auth_or_quota = any(
            k in _msg for k in ("accessdenied", "expiredtoken", "unrecognizedclient", "throttl")
        )

    if is_auth_or_quota:
        _wait = 120.0
        _mantle_consecutive_failures = 0
    else:
        # Exponential backoff + jitter -- both the original attempt AND the
        # immediate retry above already failed, so this scales up only for a
        # genuinely sustained problem, not a single blip. Capped at 60s so
        # one bad stretch can't block the whole process indefinitely.
        _mantle_consecutive_failures += 1
        _wait = min(8.0 * (2 ** (_mantle_consecutive_failures - 1)), 60.0) + random.uniform(0, 2.0)
    _mantle_blocked_until = _time.monotonic() + _wait
    _mantle_block_logged = False
    logger.warning(f"[llm.nova] failed ({type(last_exc).__name__}) — backing off {_wait:.1f}s: {str(last_exc)[:160]}")
    return None


# ── Streaming call ────────────────────────────────────────────────────────────

async def call_llm_chat_stream(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float = 90.0,
    model: str | None = None,
):
    """Async generator that streams Amazon Nova Pro responses.

    Yields dicts: {"type": "content"|"error"|"done", "text": "..."}. No
    "reasoning" type is ever yielded -- the previous gpt-oss provider had a
    separate reasoning channel streamed this way; Nova doesn't, so UI
    callers that switch on type=="reasoning" simply never see it fire now.

    boto3's converse_stream() returns a blocking iterator, not a native
    asyncio stream -- bridged here via a background thread that pushes
    parsed events into an asyncio.Queue, so the event loop stays non-blocking
    while still delivering true incremental chunks to the caller.
    """
    global _mantle_blocked_until
    _set_last_reasoning(None)
    client = _mantle_async_client()

    if client is None:
        yield {"type": "error", "text": "LLM not configured. Set MANTLE_API_KEY in .env."}
        yield {"type": "done", "text": ""}
        return

    if _time.monotonic() < _mantle_blocked_until:
        yield {"type": "error", "text": "LLM temporarily unavailable (rate-limited). Try again shortly."}
        yield {"type": "done", "text": ""}
        return

    system, conv_messages = _to_converse_format(messages)
    mdl = model or settings.MANTLE_MODEL
    budget = _mantle_budget(max_tokens)

    await _acquire_llm_rate_slot()

    queue: "asyncio.Queue" = asyncio.Queue()
    loop = asyncio.get_event_loop()
    _DONE = object()

    def _run_stream() -> None:
        try:
            kwargs = dict(
                modelId=mdl, messages=conv_messages,
                inferenceConfig={"maxTokens": budget, "temperature": temperature},
            )
            if system:
                kwargs["system"] = system
            resp = client.converse_stream(**kwargs)
            for event in resp["stream"]:
                text = (event.get("contentBlockDelta", {}) or {}).get("delta", {}).get("text")
                if text:
                    loop.call_soon_threadsafe(queue.put_nowait, ("content", text))
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))

    threading.Thread(target=_run_stream, daemon=True).start()

    while True:
        item = await queue.get()
        if item is _DONE:
            _mantle_blocked_until = 0.0
            break
        kind, payload = item
        if kind == "error":
            exc = payload
            _msg = str(exc).lower()
            _wait = 120 if any(k in _msg for k in ("accessdenied", "expiredtoken", "unrecognizedclient", "throttl")) else 20
            _mantle_blocked_until = _time.monotonic() + _wait
            logger.warning(f"[llm.nova] stream failed ({type(exc).__name__}): {str(exc)[:160]}")
            yield {"type": "error", "text": f"LLM error: {str(exc)[:200]}"}
            break
        yield {"type": "content", "text": payload}

    yield {"type": "done", "text": ""}


# ── Unified entry point ──────────────────────────────────────────────────────

async def call_llm_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float | None = None,
    model: str | None = None,
    # Legacy params — accepted but ignored (all inference is Nova Pro now)
    groq_fallback: bool = True,
    skip_ollama: bool = False,
) -> str | None:
    """Single entry point for all non-streaming inference.

    Uses Amazon Nova Pro exclusively. The `groq_fallback` and `skip_ollama`
    params are accepted for backward compatibility but ignored — there is
    only one provider now.
    """
    return await call_mantle_chat(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout or 60.0,
        model=model,
    )


async def call_llm_chat_full(
    messages: list[dict],
    *,
    source: str = "",
    symbol: str | None = None,
    persist: bool = True,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float | None = None,
    model: str | None = None,
    # Legacy params — accepted but ignored
    groq_fallback: bool = True,
    skip_ollama: bool = False,
) -> dict:
    """call_llm_chat that also returns the model's reasoning, and (by default)
    persists both to the llm_reasoning_log table.

    Returns {"content": str|None, "reasoning": str|None, "model": str}.
    `reasoning` is always None as of the Nova Pro switch (see
    get_last_reasoning()'s docstring) -- kept in the return shape so callers
    that destructure this dict don't need a rewrite.
    Persistence is best-effort and never raises into the caller.
    """
    content = await call_llm_chat(
        messages, max_tokens=max_tokens, temperature=temperature, timeout=timeout,
        model=model,
    )
    reasoning = get_last_reasoning()
    used_model = settings.MANTLE_MODEL
    if persist and (content or reasoning):
        try:
            prompt_summary = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    prompt_summary = (m.get("content") or "")[:2000]
                    break
            await log_llm_reasoning(
                source=source or "llm", symbol=symbol,
                prompt=prompt_summary, content=content, reasoning=reasoning,
                model=used_model,
            )
        except Exception as exc:
            logger.debug(f"[llm] reasoning persist skipped: {exc}")
    return {"content": content, "reasoning": reasoning, "model": used_model}


async def log_llm_reasoning(
    *, source: str, prompt: str, content: str | None, reasoning: str | None,
    symbol: str | None = None, model: str | None = None,
) -> None:
    """Persist one LLM interaction (prompt summary + answer + reasoning) to
    llm_reasoning_log. Self-contained session; never raises into the caller."""
    if not (content or reasoning):
        return
    try:
        from db.database import AsyncSessionLocal
        from db.models import LLMReasoningLog
        async with AsyncSessionLocal() as s:
            s.add(LLMReasoningLog(
                source=(source or "")[:40],
                symbol=(symbol or None),
                prompt=(prompt or "")[:4000],
                content=(content or "")[:8000],
                reasoning=(reasoning or "")[:12000],
                model=(model or "")[:60],
            ))
            await s.commit()
    except Exception as exc:
        logger.debug(f"[llm.reasoning_log] persist failed: {exc}")


# ── Sync helpers ──────────────────────────────────────────────────────────────

def _acquire_llm_rate_slot_sync() -> None:
    """Sync-context equivalent of _acquire_llm_rate_slot() for
    _mantle_sync_chat() -- same shared Redis budget, plain (non-async) redis
    client since this runs in a worker thread with no event loop available.
    Lower-volume caller (trade_explainer.py's narrative generation, not the
    ReAct loop) but still shares the same account-wide quota, so it needs to
    coordinate too rather than silently consuming slots the async callers
    were budgeting around."""
    limit = int(getattr(settings, "MANTLE_MAX_RPM", 20))
    try:
        import redis as _redis_sync
        r = _redis_sync.from_url(settings.REDIS_URL, decode_responses=True)
        script = r.register_script(_RATE_LIMIT_LUA)
        deadline = _time.monotonic() + 90.0
        while _time.monotonic() < deadline:
            bucket = int(_time.time() // 60)
            key = f"llm:rpm:{bucket}"
            if script(keys=[key], args=[limit, 90]):
                return
            _time.sleep(1.0)
    except Exception as exc:
        logger.debug(f"[llm.rate_limit] sync Redis coordination unavailable: {exc}")


def _mantle_sync_chat(prompt: str, system: str, max_tokens: int = 512) -> str:
    """Sync Nova Pro inference. Returns '' on any failure."""
    client = _mantle_sync_client()
    if client is None:
        return ""
    _acquire_llm_rate_slot_sync()
    try:
        kwargs = dict(
            modelId=settings.MANTLE_MODEL,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": _mantle_budget(max_tokens), "temperature": 0.3},
        )
        if system:
            kwargs["system"] = [{"text": system}]
        resp = client.converse(**kwargs)
        return _extract_converse_text(resp)
    except Exception as exc:
        logger.warning(f"[llm.nova] sync call failed: {exc}")
        return ""


def quick_analysis(prompt: str, system: str = "You are a concise financial analyst.") -> str:
    """Fast sync inference via Amazon Nova Pro. Returns '' if unavailable."""
    return _mantle_sync_chat(prompt, system, max_tokens=512)


def explain(prompt: str, system: str = "You are an expert trading strategy explainer.") -> str:
    """Detailed explanation via Amazon Nova Pro."""
    return _mantle_sync_chat(prompt, system, max_tokens=1024)


def summarise_signal(symbol: str, signal: str, score: float, indicators: dict) -> str:
    prompt = (
        f"Symbol: {symbol}\n"
        f"Signal: {signal}  (confluence score: {score:.3f}/1.0)\n"
        f"Indicator snapshot: {indicators}\n\n"
        f"Write a 2-sentence trading commentary explaining why this signal was generated "
        f"and the key risks to watch. Be concise and factual."
    )
    return quick_analysis(prompt)


def explain_signal(symbol: str, signal: str, reasoning: str) -> str:
    prompt = (
        f"A paper-trading system generated a {signal} signal for {symbol}.\n"
        f"Internal reasoning: {reasoning}\n\n"
        f"Explain in plain English (3-4 paragraphs) what each indicator is saying, "
        f"why they converged on this signal, and what a trader should watch for next."
    )
    return explain(prompt)
