"""Telegram transport for AutoTrade Pro.

Pure transport layer -- low-level Bot API calls only. Everything above this
(severity, dedup/cooldown, templates, the event schema) lives in
integrations/alerts/; callers should use integrations.alerts.publish(), not
this module, directly. Uses httpx (already a project dependency).
"""
from __future__ import annotations

import logging

import httpx

from utils.config import settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_PHOTO_API_URL = "https://api.telegram.org/bot{token}/sendPhoto"


async def _post(text: str, reply_to_message_id: int | None = None) -> int | None:
    """Sends `text` and returns Telegram's own message_id on success (used
    by integrations/alerts/router.py to thread a trade's lifecycle -- an
    exit alert replies onto its entry alert's message_id), or None on any
    failure/suppression. `reply_to_message_id`, when given, is passed
    through as Telegram's own reply_to_message_id -- if the referenced
    message no longer exists (e.g. deleted from the chat), Telegram sends
    the message normally rather than erroring, so this is safe to pass
    speculatively."""
    # Hard guard: never send a live Telegram message from inside a test run.
    # pytest sets PYTEST_CURRENT_TEST for every test, so this fires even for
    # tests that forget to stub the notifier. Fixture data like the "TESTCO
    # ₹100 flat entry/SL/target, Qty 0" trade must never reach the real chat.
    import os
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("DISABLE_TELEGRAM"):
        logger.debug("[telegram] suppressed (test / DISABLE_TELEGRAM env)")
        return None
    token   = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_CHAT_ID",   "")
    if not token or not chat_id:
        logger.warning("[telegram] missing token or chat_id")
        return None
    # api.telegram.org can be slow on this network — generous timeout + retries
    # so alerts (equity AND F&O) aren't silently dropped on a transient delay.
    import asyncio as _aio
    payload = {
        "chat_id":                  chat_id,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(_API_URL.format(token=token), json=payload)
            if r.status_code == 200:
                logger.info(f"[telegram] ✓ sent to {chat_id}")
                try:
                    return r.json().get("result", {}).get("message_id")
                except Exception:
                    return None
            logger.warning(f"[telegram] {r.status_code}: {r.text[:200]}")
            return None  # non-200 (e.g. bad chat) — don't retry
        except Exception as exc:
            if attempt == 2:
                logger.warning(f"[telegram] send failed after retries: {exc}")
            else:
                await _aio.sleep(2)
    return None


async def _post_photo(photo_bytes: bytes, caption: str = "", reply_to_message_id: int | None = None) -> int | None:
    """Sends a PNG photo (multipart sendPhoto) with an optional caption,
    returning Telegram's message_id on success or None on any failure/
    suppression -- same shape and retry/timeout behavior as _post()."""
    import os
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("DISABLE_TELEGRAM"):
        logger.debug("[telegram] photo suppressed (test / DISABLE_TELEGRAM env)")
        return None
    token   = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_CHAT_ID",   "")
    if not token or not chat_id:
        logger.warning("[telegram] missing token or chat_id")
        return None
    import asyncio as _aio
    data = {"chat_id": chat_id, "parse_mode": "HTML"}
    if caption:
        data["caption"] = caption[:1024]  # Telegram's own photo-caption limit
    if reply_to_message_id is not None:
        data["reply_to_message_id"] = str(reply_to_message_id)
    files = {"photo": ("chart.png", photo_bytes, "image/png")}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(_PHOTO_API_URL.format(token=token), data=data, files=files)
            if r.status_code == 200:
                logger.info(f"[telegram] ✓ photo sent to {chat_id}")
                try:
                    return r.json().get("result", {}).get("message_id")
                except Exception:
                    return None
            logger.warning(f"[telegram] photo {r.status_code}: {r.text[:200]}")
            return None
        except Exception as exc:
            if attempt == 2:
                logger.warning(f"[telegram] photo send failed after retries: {exc}")
            else:
                await _aio.sleep(2)
    return None
