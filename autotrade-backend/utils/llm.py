# LLM client: Ollama (local, primary) → Groq (cloud, fallback) → Claude (explanations).
# call_llm_chat() is the single entry point for all inference.
# Ollama uses streaming because deepseek-r1 is a thinking model — stream:false truncates.

import asyncio
import json as _json
import re
from functools import lru_cache
from utils.config import settings
from utils.logger import logger

# Ollama — local deepseek-r1 (primary, no rate limits)
OLLAMA_CHAT_URL = "{base}/api/chat"
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


# ── Unified entry point (Ollama → Groq) ──────────────────────────────────────

async def call_llm_chat(
    messages: list[dict],
    *,
    max_tokens: int = 600,
    temperature: float = 0.3,
    timeout: float | None = None,
    model: str | None = None,
    groq_fallback: bool = True,
) -> str | None:
    """Try Ollama first (local, no quota); fall back to Groq on failure.

    Pass groq_fallback=False for background/batch tasks to protect the
    Groq daily quota for user-facing requests.
    """
    result = await call_ollama_chat(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        model=model,
    )
    if result:
        return result

    if not groq_fallback:
        logger.debug("[llm] Ollama unavailable — groq_fallback=False, skipping Groq")
        return None

    logger.info("[llm] Ollama unavailable/timeout — falling back to Groq")
    return await call_groq_chat(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout or 20.0,
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

def quick_analysis(prompt: str, system: str = "You are a concise financial analyst.") -> str:
    """Fast sync inference via Groq. Falls back to empty string if unavailable."""
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
    """Detailed explanation via Claude; falls back to Groq if Claude key not set."""
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
