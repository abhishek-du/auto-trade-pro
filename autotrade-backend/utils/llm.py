# LLM client: gpt-oss-120b via Mantle (AWS Bedrock, OpenAI-compatible).
# Single provider — no fallback chain. gpt-oss is a reasoning model that
# emits both reasoning traces and content in every response.
#
# call_llm_chat()        — async, returns content string (reasoning in contextvar)
# call_llm_chat_stream() — async generator, yields reasoning + content chunks for SSE
# call_llm_chat_full()   — async, returns {content, reasoning, model} dict + persists to DB

import asyncio
import json as _json
import re
from functools import lru_cache
from utils.config import settings
from utils.logger import logger

import time as _time
import contextvars as _contextvars


# ── Reasoning capture ─────────────────────────────────────────────────────────
# gpt-oss returns a separate `reasoning` channel alongside the answer.
# We stash the most recent reasoning in a contextvar (async-task-local, so
# concurrent Celery/uvicorn coroutines don't clobber each other) so any caller
# can read it right after a call_llm_chat* invocation — to show on the UI and
# persist to the DB — without changing the str return type the existing
# call sites depend on.
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

    Read it immediately after call_llm_chat()/call_llm_chat_full()."""
    try:
        return _LAST_REASONING.get()
    except Exception:
        return None


# ── Mantle (gpt-oss-120b) — sole LLM provider ────────────────────────────────
# All inference routes through here. gpt-oss is a reasoning model: it emits
# reasoning tokens alongside the answer. The reasoning shares the max_tokens
# budget, so we floor it at MANTLE_MIN_TOKENS to avoid empty content.
_mantle_blocked_until: float = 0.0


@lru_cache(maxsize=1)
def _mantle_async_client():
    """Cached AsyncOpenAI client for the Mantle/Bedrock endpoint (or None)."""
    if not getattr(settings, "mantle_available", False):
        return None
    try:
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            base_url=settings.MANTLE_BASE_URL,
            api_key=settings.MANTLE_API_KEY,
            default_headers={"OpenAI-Project": getattr(settings, "MANTLE_PROJECT", "default")},
            max_retries=0,
        )
    except Exception as exc:
        logger.warning(f"[llm.mantle] client init failed: {exc}")
        return None


@lru_cache(maxsize=1)
def _mantle_sync_client():
    """Cached sync OpenAI client for the Mantle/Bedrock endpoint (or None)."""
    if not getattr(settings, "mantle_available", False):
        return None
    try:
        from openai import OpenAI
        return OpenAI(
            base_url=settings.MANTLE_BASE_URL,
            api_key=settings.MANTLE_API_KEY,
            default_headers={"OpenAI-Project": getattr(settings, "MANTLE_PROJECT", "default")},
            max_retries=0,
        )
    except Exception as exc:
        logger.warning(f"[llm.mantle] sync client init failed: {exc}")
        return None


def _mantle_budget(max_tokens: int) -> int:
    """gpt-oss reasoning shares the max_tokens budget with the answer, so a small
    budget returns empty content. Floor it at MANTLE_MIN_TOKENS."""
    return max(int(max_tokens or 0), int(getattr(settings, "MANTLE_MIN_TOKENS", 512)))


# ── Non-streaming call ────────────────────────────────────────────────────────

async def call_mantle_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float = 60.0,
    model: str | None = None,
) -> str | None:
    """Inference via gpt-oss-120b (Mantle/Bedrock OpenAI-compatible endpoint).

    Returns the assistant text (content channel) or None on any failure.
    The reasoning channel is captured into a contextvar — read via
    get_last_reasoning() — so callers can show it in the UI / persist it.
    """
    global _mantle_blocked_until
    _set_last_reasoning(None)   # clear per-call so stale reasoning never leaks
    client = _mantle_async_client()
    if client is None:
        return None
    if _time.monotonic() < _mantle_blocked_until:
        return None
    try:
        resp = await client.chat.completions.create(
            model=model or settings.MANTLE_MODEL,
            messages=messages,
            max_tokens=_mantle_budget(max_tokens),
            temperature=temperature,
            timeout=timeout,
        )
        _mantle_blocked_until = 0.0
        choice = (resp.choices or [None])[0]
        _msg_obj = getattr(choice, "message", None) if choice else None
        content = (getattr(_msg_obj, "content", None) or "").strip()
        # Capture the reasoning channel (gpt-oss harmony format).
        reasoning = (getattr(_msg_obj, "reasoning", None)
                     or getattr(_msg_obj, "reasoning_content", None) or "")
        if reasoning:
            _set_last_reasoning(str(reasoning).strip())
        if content:
            return content
        logger.warning("[llm.mantle] empty content (reasoning consumed the budget?)")
        return None
    except Exception as exc:
        _msg = str(exc).lower()
        _wait = 120 if ("401" in _msg or "403" in _msg or "quota" in _msg or "429" in _msg) else 20
        _mantle_blocked_until = _time.monotonic() + _wait
        logger.warning(f"[llm.mantle] failed ({type(exc).__name__}) — backing off {_wait}s: {str(exc)[:160]}")
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
    """Async generator that streams gpt-oss-120b responses.

    Yields dicts: {"type": "reasoning"|"content"|"error"|"done", "text": "..."}

    gpt-oss is a reasoning model — it first emits reasoning tokens (the model's
    chain-of-thought), then content tokens (the actual answer). This generator
    yields both phases separately so the UI can show them in real-time.
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

    reasoning_parts: list[str] = []
    content_parts: list[str] = []

    try:
        stream = await client.chat.completions.create(
            model=model or settings.MANTLE_MODEL,
            messages=messages,
            max_tokens=_mantle_budget(max_tokens),
            temperature=temperature,
            timeout=timeout,
            stream=True,
        )

        _mantle_blocked_until = 0.0

        async for chunk in stream:
            choice = (chunk.choices or [None])[0]
            if not choice:
                continue
            delta = choice.delta
            if not delta:
                continue

            # gpt-oss reasoning tokens (chain-of-thought)
            reasoning_text = (
                getattr(delta, "reasoning", None)
                or getattr(delta, "reasoning_content", None)
                or ""
            )
            if reasoning_text:
                reasoning_parts.append(reasoning_text)
                yield {"type": "reasoning", "text": reasoning_text}

            # gpt-oss content tokens (actual answer)
            content_text = getattr(delta, "content", None) or ""
            if content_text:
                content_parts.append(content_text)
                yield {"type": "content", "text": content_text}

            # Check for finish reason
            if getattr(choice, "finish_reason", None):
                break

        # Stash reasoning for the contextvar (so call_llm_chat_full can read it)
        full_reasoning = "".join(reasoning_parts).strip()
        if full_reasoning:
            _set_last_reasoning(full_reasoning)

    except Exception as exc:
        _msg = str(exc).lower()
        _wait = 120 if ("401" in _msg or "403" in _msg or "quota" in _msg or "429" in _msg) else 20
        _mantle_blocked_until = _time.monotonic() + _wait
        logger.warning(f"[llm.mantle] stream failed ({type(exc).__name__}): {str(exc)[:160]}")
        yield {"type": "error", "text": f"LLM error: {str(exc)[:200]}"}

    yield {"type": "done", "text": ""}


# ── Unified entry point ──────────────────────────────────────────────────────

async def call_llm_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float | None = None,
    model: str | None = None,
    # Legacy params — accepted but ignored (all inference is gpt-oss now)
    groq_fallback: bool = True,
    skip_ollama: bool = False,
) -> str | None:
    """Single entry point for all non-streaming inference.

    Uses gpt-oss-120b exclusively. The `groq_fallback` and `skip_ollama` params
    are accepted for backward compatibility but ignored — there is only one
    provider now.
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
    Use this from chat and decision paths that must show/save the reasoning.
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

def _mantle_sync_chat(prompt: str, system: str, max_tokens: int = 512) -> str:
    """Sync Mantle inference. Returns '' on any failure."""
    client = _mantle_sync_client()
    if client is None:
        return ""
    try:
        resp = client.chat.completions.create(
            model=settings.MANTLE_MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            max_tokens=_mantle_budget(max_tokens),
            temperature=0.3,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning(f"[llm.mantle] sync call failed: {exc}")
        return ""


def quick_analysis(prompt: str, system: str = "You are a concise financial analyst.") -> str:
    """Fast sync inference via gpt-oss-120b. Returns '' if unavailable."""
    return _mantle_sync_chat(prompt, system, max_tokens=512)


def explain(prompt: str, system: str = "You are an expert trading strategy explainer.") -> str:
    """Detailed explanation via gpt-oss-120b."""
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
