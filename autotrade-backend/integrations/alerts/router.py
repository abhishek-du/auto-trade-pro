"""Single funnel for every outbound Telegram alert.

Replaces the pattern that used to be duplicated at all 29 call sites:
    if settings.telegram_available:
        try:
            from integrations.telegram_service import send, fmt_x
            await send(fmt_x(...))
        except Exception:
            pass
with one `await publish(AlertEvent(...))` (or `publish_sync(...)` from a
non-async Celery task body, mirroring this codebase's existing
`_run_async(...)` shape).
"""
from __future__ import annotations

import asyncio
import logging

from utils.config import settings

from . import dedup as _dedup
from .events import AlertAction, AlertCategory, AlertEvent, ShortlistPayload, Severity
from .templates import render

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {
    Severity.INFO: 0, Severity.SUCCESS: 1, Severity.WARNING: 2,
    Severity.CRITICAL: 3, Severity.EMERGENCY: 4,
}

# Per (category, action) default cooldown in seconds, used when the event
# doesn't set its own cooldown_seconds. 0 = no dedup (send every time),
# matching what most categories did before this router existed.
_DEFAULT_COOLDOWN_SEC = {
    (AlertCategory.OPERATIONS, AlertAction.ERROR): 3600,     # matches old 1hr staleness-alert cooldowns
    (AlertCategory.TRADE,      AlertAction.EXIT):  604800,   # one alert per trade_id, ever (7d TTL is plenty)
    (AlertCategory.FNO_SIGNAL, AlertAction.EXIT):  604800,
}


def _default_dedup_key(event: AlertEvent) -> str:
    if event.trade_id is not None:
        return f"{event.category.value}:{event.action.value}:{event.trade_id}"
    return f"{event.category.value}:{event.action.value}:{event.symbol or ''}"


async def publish(event: AlertEvent) -> None:
    if not settings.telegram_available:
        return

    min_sev = Severity(getattr(settings, "TELEGRAM_MIN_SEVERITY", "INFO"))
    if _SEVERITY_RANK[event.severity] < _SEVERITY_RANK[min_sev]:
        return

    try:
        if event.category == AlertCategory.SHORTLIST and isinstance(event.payload, ShortlistPayload):
            p = event.payload
            should_alert = await _dedup.shortlist_gate(
                event.symbol or "", p.score, p.news_subscore, p.executed,
            )
            if not should_alert:
                return
        else:
            cooldown = event.cooldown_seconds
            if cooldown is None:
                cooldown = _DEFAULT_COOLDOWN_SEC.get((event.category, event.action), 0)
            if cooldown > 0:
                key = event.dedup_key or _default_dedup_key(event)
                if await _dedup.is_duplicate(key, cooldown):
                    return

        text = render(event)
        from integrations.telegram_service import _post
        await _post(text)
    except Exception as exc:
        logger.warning(f"[alerts.router] publish failed ({event.category}/{event.action}): {exc}")


def publish_sync(event: AlertEvent) -> None:
    """Sync-context equivalent of publish(), for Celery task bodies that
    aren't themselves async — mirrors this codebase's existing
    `_run_async(coro)` helper shape used elsewhere in tasks/india_tasks.py."""
    asyncio.run(publish(event))
