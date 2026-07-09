# LLM client: Ollama (local, primary) → Groq (cloud, fallback) → Claude (explanations).
# call_llm_chat() is the single entry point for all inference.
# Ollama uses streaming because deepseek-r1 is a thinking model — stream:false truncates.

import asyncio
import json as _json
import re
from functools import lru_cache
from utils.config import settings
from utils.logger import logger

import time as _time

# Ollama — local deepseek-r1 (primary, no rate limits)
OLLAMA_CHAT_URL = "{base}/api/chat"


# ── Mantle (AWS Bedrock, OpenAI-compatible) — PRIMARY intelligence layer ──────
# openai.gpt-oss-120b via an OpenAI-compatible endpoint. This is the first
# provider call_llm_chat tries, so ALL analysis/decision inference in the system
# routes through it (falling back to Gemini→Groq→Ollama on any failure).
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
            max_retries=0,   # we own the fallback chain — don't let the SDK stall
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


async def call_mantle_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float = 60.0,
    model: str | None = None,
) -> str | None:
    """Primary inference via the Mantle/Bedrock OpenAI-compatible endpoint.

    Returns the assistant text (content channel only — reasoning is discarded) or
    None on any failure, so call_llm_chat can fall back. A short circuit-breaker
    suppresses calls after a failure burst so a Mantle outage doesn't stall every
    Celery cycle behind a 60s timeout.
    """
    global _mantle_blocked_until
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
        content = (getattr(choice, "message", None).content if choice and choice.message else None) or ""
        content = content.strip()
        if content:
            return content
        logger.warning("[llm.mantle] empty content (reasoning consumed the budget?)")
        return None
    except Exception as exc:
        # Back off briefly so a transient outage doesn't block every task on the
        # 60s timeout. Auth/quota errors get a longer window.
        _msg = str(exc).lower()
        _wait = 120 if ("401" in _msg or "403" in _msg or "quota" in _msg or "429" in _msg) else 20
        _mantle_blocked_until = _time.monotonic() + _wait
        logger.warning(f"[llm.mantle] failed ({type(exc).__name__}) — backing off {_wait}s: {str(exc)[:160]}")
        return None
# Groq — cloud fallback
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = settings.GROQ_MODEL
# Claude — deep explanations only
CLAUDE_MODEL = settings.CLAUDE_MODEL


# ── Ollama (primary) ──────────────────────────────────────────────────────────

async def call_ollama_chat(
    messages: list[dict],
    *,
    max_tokens: int = 2000,
    temperature: float = 0.3,
    timeout: float | None = None,
    model: str | None = None,
) -> str | None:
    """Stream a chat completion from local Ollama.

    deepseek-r1 is a thinking model: it emits tokens into the 'thinking' field
    during reasoning, then the actual answer into 'content'. We collect only the
    content tokens and strip any residual <think>…</think> blocks from the output.

    Returns the assistant text on success, None on any failure/timeout.
    """
    if not getattr(settings, "ollama_available", False):
        return None

    import httpx

    _model   = model   or settings.OLLAMA_MODEL

    # Fast-fail if the target model isn't loaded (avoids 404 → Groq fallback cycle)
    try:
        async with httpx.AsyncClient(timeout=3.0) as _chk:
            _tags = await _chk.get(f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/tags")
            if _tags.status_code == 200:
                _loaded = [m.get("name", "") for m in (_tags.json().get("models") or [])]
                _base_model = _model.split(":")[0]
                if _loaded and not any(_base_model in m for m in _loaded):
                    logger.debug(f"[llm.ollama] model {_model!r} not loaded — skipping")
                    return None
    except Exception:
        pass  # if tags check fails, proceed and let the actual call fail naturally
    _timeout = timeout if timeout is not None else settings.OLLAMA_TIMEOUT
    url      = OLLAMA_CHAT_URL.format(base=settings.OLLAMA_BASE_URL.rstrip("/"))

    # qwen2.5:3b runs on CPU at ~2-3 tokens/sec. Input tokens dominate time:
    # ~5K chars (1.25K tokens) = ~2 min response — acceptable limit.
    # Large prompts (earnings transcripts, deep analysis) must use Groq.
    total_chars = sum(len(m.get("content", "")) for m in messages)
    if total_chars > 5_000:
        logger.info(
            f"[llm.ollama] prompt {total_chars} chars > 5K CPU limit — routing to Groq"
        )
        return None

    estimated_input_tokens = total_chars // 4
    num_ctx        = max(4096, estimated_input_tokens + 2048)
    predict_budget = max(1024, max_tokens * 2)
    body     = {
        "model":    _model,
        "messages": messages,
        "stream":   True,
        "options":  {
            "temperature": temperature,
            "num_predict": predict_budget,
            "num_ctx":     num_ctx,
        },
    }

    try:
        content_parts: list[str] = []
        async with httpx.AsyncClient(timeout=_timeout) as client:
            async with client.stream("POST", url, json=body) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    try:
                        chunk = _json.loads(raw_line)
                    except _json.JSONDecodeError:
                        continue
                    msg = chunk.get("message") or {}
                    # thinking field = reasoning tokens (skip)
                    # content field  = actual response tokens (collect)
                    part = msg.get("content") or ""
                    if part:
                        content_parts.append(part)
                    if chunk.get("done"):
                        break

        result = "".join(content_parts).strip()
        # Strip <think>…</think> blocks if model uses chain-of-thought in content
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
        if result:
            logger.info(f"[llm.ollama] ✓ {_model} responded ({len(result)} chars)")
            return result
        logger.warning(f"[llm.ollama] empty content from {_model}")
        return None

    except Exception as exc:
        logger.warning(f"[llm.ollama] failed ({type(exc).__name__}): {exc}")
        return None


# ── Groq (fallback) ───────────────────────────────────────────────────────────

# Circuit-breaker: when Groq returns 429 with a long retry-after we record
# the time until which Groq calls should be suppressed.  This prevents
# background tasks from hammering the API after the daily quota is exhausted.
import time as _time
_groq_blocked_until: float = 0.0


async def call_groq_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float = 20.0,
    model: str = GROQ_MODEL,
    _retries: int = 3,
) -> str | None:
    """Cloud Groq inference — fallback when Ollama is unavailable or times out.

    Retries up to _retries times on 429, honouring the Retry-After header.
    If Retry-After > 30s (daily quota exhausted) it fails fast and sets a
    module-level circuit-breaker so subsequent calls skip Groq until the
    window expires.
    """
    global _groq_blocked_until
    if not getattr(settings, "groq_available", False) or not getattr(settings, "GROQ_API_KEY", ""):
        return None

    # Circuit-breaker: if we know Groq is rate-limited, fail fast
    if _time.monotonic() < _groq_blocked_until:
        remaining = int(_groq_blocked_until - _time.monotonic())
        logger.debug(f"[llm.groq] circuit-breaker open — {remaining}s remaining, skipping call")
        return None

    import httpx
    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    body = {
        "model":       model,
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": temperature,
    }
    MAX_RETRY_WAIT = 30
    backoff_secs   = [10, 20, 30]
    for attempt in range(_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(GROQ_URL, headers=headers, json=body)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("retry-after", backoff_secs[min(attempt, len(backoff_secs)-1)]))
                    if retry_after > MAX_RETRY_WAIT or attempt >= _retries:
                        logger.warning(
                            f"[llm.groq] 429 rate limit — retry-after={retry_after}s, "
                            "giving up (quota likely exhausted)"
                        )
                        # Open circuit-breaker for the duration Groq asked us to wait
                        _groq_blocked_until = _time.monotonic() + retry_after
                        return None
                    logger.info(f"[llm.groq] 429 — waiting {retry_after}s (attempt {attempt+1}/{_retries})")
                    await asyncio.sleep(retry_after)
                    continue
                # Successful response — reset circuit-breaker
                _groq_blocked_until = 0.0
                resp.raise_for_status()
                data = resp.json()
            choice  = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            return content.strip() or None
        except httpx.HTTPStatusError as exc:
            logger.warning(f"[llm.groq] HTTP error: {exc}")
            return None
        except Exception as exc:
            logger.warning(f"[llm.groq] failed: {exc}")
            return None
    return None


# ── Gemini (PRIMARY) ─────────────────────────────────────────────────────────

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_gemini_blocked_until: float = 0.0


def _to_gemini_contents(messages: list[dict]) -> tuple[list[dict], dict | None]:
    """Convert OpenAI-style messages → Gemini contents + optional systemInstruction."""
    system_txt = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    contents = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": m.get("content", "")}],
        })
    sys_inst = {"parts": [{"text": system_txt}]} if system_txt else None
    return contents, sys_inst


async def call_gemini_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float = 30.0,
    model: str | None = None,
) -> str | None:
    """Google Gemini inference (primary). Returns None on any failure so the
    caller can fall back to Groq/Ollama."""
    global _gemini_blocked_until
    if not getattr(settings, "gemini_available", False):
        return None
    if _time.monotonic() < _gemini_blocked_until:
        return None

    import httpx
    mdl = model or settings.GEMINI_MODEL
    contents, sys_inst = _to_gemini_contents(messages)
    body: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
            # gemini-2.5-flash is a thinking model; thinking tokens eat the output
            # budget and add latency. Disable it so we get a direct answer.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if sys_inst:
        body["systemInstruction"] = sys_inst
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                GEMINI_URL.format(model=mdl),
                params={"key": settings.GEMINI_API_KEY},
                headers={"Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code == 429:
                _gemini_blocked_until = _time.monotonic() + 60
                logger.warning("[llm.gemini] 429 rate limit — backing off 60s")
                return None
            resp.raise_for_status()
            data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            return None
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        return text.strip() or None
    except Exception as exc:
        logger.warning(f"[llm.gemini] failed: {exc}")
        return None


# ── Unified entry point (Gemini → Groq → Ollama) ─────────────────────────────

async def call_llm_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float | None = None,
    model: str | None = None,
    groq_fallback: bool = True,
    skip_ollama: bool = False,
) -> str | None:
    """Primary Mantle → Gemini → Groq → local Ollama.

    Mantle (AWS Bedrock, openai.gpt-oss-120b) is the primary intelligence layer:
    every analysis/decision LLM call in the system routes through here and tries
    Mantle first, falling back on any failure so inference never hard-blocks.

    `groq_fallback=False` (background/batch) skips the cloud providers entirely
    after Gemini and only allows the local Ollama path, to protect cloud quotas.
    `skip_ollama=True` returns None instead of falling back to Ollama — use in
    latency-sensitive paths (e.g. shadow-mode reasoning gate) where a 40s Ollama
    call would push the Celery task past SoftTimeLimitExceeded.
    """
    # 0. Mantle / Bedrock (PRIMARY intelligence layer)
    result = await call_mantle_chat(
        messages, max_tokens=max_tokens, temperature=temperature,
        timeout=timeout or 60.0, model=model if (model and "gpt-oss" in model) else None,
    )
    if result:
        return result

    # 1. Gemini (cloud fallback)
    result = await call_gemini_chat(
        messages, max_tokens=max_tokens, temperature=temperature,
        timeout=timeout or 30.0,
    )
    if result:
        return result

    # 2. Groq (secondary, cloud) — only when cloud fallback is allowed
    if groq_fallback:
        logger.info("[llm] Gemini unavailable — falling back to Groq")
        result = await call_groq_chat(
            messages, max_tokens=max_tokens, temperature=temperature,
            timeout=timeout or 20.0,
        )
        if result:
            return result

    # 3. Ollama (tertiary, local — no quota)
    if skip_ollama:
        return None
    logger.info("[llm] falling back to local Ollama")
    return await call_ollama_chat(
        messages, max_tokens=max_tokens, temperature=temperature,
        timeout=timeout, model=model if model and ":" in (model or "") else None,
    )


# ── Sync clients (kept for legacy sync callers) ───────────────────────────────

@lru_cache(maxsize=1)
def _groq_client():
    if not settings.groq_available:
        return None
    try:
        from groq import Groq
        return Groq(api_key=settings.GROQ_API_KEY)
    except Exception as exc:
        logger.warning(f"Groq client init failed: {exc}")
        return None


@lru_cache(maxsize=1)
def _claude_client():
    if not settings.claude_available:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    except Exception as exc:
        logger.warning(f"Claude client init failed: {exc}")
        return None


# ── Public interface ──────────────────────────────────────────────────────────

def _mantle_sync_chat(prompt: str, system: str, max_tokens: int = 512) -> str:
    """Sync Mantle inference for the legacy sync helpers. '' on any failure."""
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
    """Fast sync inference — Mantle primary, Groq fallback, '' if both unavailable."""
    out = _mantle_sync_chat(prompt, system, max_tokens=512)
    if out:
        return out
    client = _groq_client()
    if client is None:
        logger.debug("Groq unavailable — skipping quick_analysis")
        return ""
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            max_tokens=512,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error(f"Groq quick_analysis failed: {exc}")
        return ""


def explain(prompt: str, system: str = "You are an expert trading strategy explainer.") -> str:
    """Detailed explanation — Mantle primary, then Claude, then Groq."""
    out = _mantle_sync_chat(prompt, system, max_tokens=1024)
    if out:
        return out
    client = _claude_client()
    if client is None:
        logger.info("Claude key not set — routing explain() to Groq instead")
        return quick_analysis(prompt, system)
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.error(f"Claude explain failed: {exc}. Falling back to Groq.")
        return quick_analysis(prompt, system)


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
