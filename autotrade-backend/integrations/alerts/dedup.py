"""Redis-backed alert dedup/cooldown, modeled on utils/llm.py's
_acquire_llm_rate_slot (same fail-open-on-Redis-outage philosophy, same
already-running Redis instance as the Celery broker — no new infra).

Replaces four independent per-worker-process cooldown globals that used to
live in tasks/india_tasks.py and engine/agent/agent_loop.py
(_last_fii_dii_stale_alert, _last_candle_stale_alert, _exit_alerted_trade_ids,
_shortlist_alerted / _shortlist_alerted_loop) — each of which only saw its
own worker's state, so in a multi-worker Celery deployment they didn't
actually dedup consistently. A shared Redis key does.
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

_DEDUP_PREFIX     = "alerts:dedup:"
_SHORTLIST_PREFIX = "alerts:shortlist:"
_SHORTLIST_STATE_TTL_SEC = 86400  # keep last-alerted state around for a day


async def is_duplicate(dedup_key: str, cooldown_seconds: int) -> bool:
    """True if `dedup_key` was already claimed within cooldown_seconds (the
    caller should suppress this alert). False — and the slot is now
    claimed — otherwise. Fails OPEN (never suppresses) if Redis is
    unreachable; a coordination outage must never silently swallow a real
    alert."""
    if cooldown_seconds <= 0:
        return False
    try:
        from utils.cache import get_redis
        r = get_redis()
        # SET key val NX EX ttl -- atomic "claim only if absent"
        claimed = await r.set(_DEDUP_PREFIX + dedup_key, "1", nx=True, ex=cooldown_seconds)
        return not claimed
    except Exception as exc:
        logger.debug(f"[alerts.dedup] Redis unavailable, failing open: {exc}")
        return False


async def shortlist_would_alert(
    symbol: str,
    score: float,
    news_subscore: float,
    executed: bool,
    min_interval_sec: int = 1800,
    score_delta: float = 5.0,
) -> bool:
    """Read-only peek at the same decision shortlist_gate() would make,
    WITHOUT writing state. For call sites that want to skip expensive work
    (Tavily/LLM research) before an alert that will just be suppressed
    anyway — the authoritative, state-mutating decision still happens once,
    at publish() time via shortlist_gate(). Calling shortlist_gate() twice
    for the same alert would double-write state and could cause the second
    call to see its own first write as "too recent" and wrongly suppress —
    this function exists specifically to avoid that. Fails OPEN (would
    alert) if Redis is unreachable."""
    if executed:
        return True
    try:
        from utils.cache import get_redis
        r = get_redis()
        raw = await r.get(_SHORTLIST_PREFIX + symbol)
        if not raw:
            return True
        prev = json.loads(raw)
        now = time.time()
        if now - prev["ts"] < min_interval_sec:
            return False
        return abs(score - prev["score"]) >= score_delta or news_subscore != prev["news"]
    except Exception as exc:
        logger.debug(f"[alerts.dedup] shortlist_would_alert Redis unavailable, failing open: {exc}")
        return True


async def shortlist_gate(
    symbol: str,
    score: float,
    news_subscore: float,
    executed: bool,
    min_interval_sec: int = 1800,
    score_delta: float = 5.0,
) -> bool:
    """Content-delta shortlist dedup. Returns True if the alert SHOULD be
    sent. `executed=True` always alerts (bypasses the cooldown/delta check
    entirely — a trade that actually opened is never suppressed). Otherwise:
    alert only if the 7-factor score moved >= score_delta or the news
    subscore changed, and never within min_interval_sec of the last alert
    for this symbol (anti-spam floor). Fails OPEN (alerts) if Redis is
    unreachable.

    Consolidates two previously-separate implementations
    (engine/agent/agent_loop.py's flat 4h cooldown + executed-override, and
    tasks/india_tasks.py's content-delta + 30min floor) onto the
    content-delta policy, keeping the executed override.
    """
    key = _SHORTLIST_PREFIX + symbol
    try:
        from utils.cache import get_redis
        r = get_redis()

        should_alert = executed
        if not should_alert:
            raw = await r.get(key)
            now = time.time()
            if raw:
                prev = json.loads(raw)
                if now - prev["ts"] < min_interval_sec:
                    return False
                score_changed = abs(score - prev["score"]) >= score_delta
                news_changed  = news_subscore != prev["news"]
                should_alert  = score_changed or news_changed
            else:
                should_alert = True  # never alerted for this symbol before

        if should_alert:
            await r.set(
                key,
                json.dumps({"score": score, "news": news_subscore, "ts": time.time()}),
                ex=_SHORTLIST_STATE_TTL_SEC,
            )
        return should_alert
    except Exception as exc:
        logger.debug(f"[alerts.dedup] shortlist_gate Redis unavailable, failing open: {exc}")
        return True
