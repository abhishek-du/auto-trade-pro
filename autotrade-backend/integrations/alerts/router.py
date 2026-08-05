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


def _model_for_trade_id(trade_id):
    # PaperTrade.id is Integer; AgentTrade.id is a String(36) UUID -- the
    # trade_id's own type tells us which table it belongs to (see
    # AlertEvent.trade_id's docstring in events.py).
    from db.models import AgentTrade, PaperTrade
    return PaperTrade if isinstance(trade_id, int) else AgentTrade


async def _lookup_reply_to(trade_id) -> int | None:
    """SELECT the stored telegram_message_id for trade_id, so an EXIT/UPDATE
    alert can reply onto its ENTRY alert's message. Best-effort: any failure
    (DB unreachable, no entry alert was ever stored) returns None, meaning
    the caller sends un-threaded rather than blocking the alert on it."""
    try:
        from sqlalchemy import select

        from tasks._db import celery_session
        Model = _model_for_trade_id(trade_id)
        async with celery_session() as session:
            result = await session.execute(
                select(Model.telegram_message_id).where(Model.id == trade_id)
            )
            row = result.first()
            return row[0] if row else None
    except Exception as exc:
        logger.debug(f"[alerts.router] thread lookup failed for trade_id={trade_id}: {exc}")
        return None


async def _store_message_id(trade_id, message_id: int) -> None:
    """Persist an ENTRY alert's message_id, using a short-lived session
    opened fresh here (tasks._db.celery_session(), NullPool-backed) rather
    than any session the caller might have had open -- Telegram's round-trip
    can take up to ~64s (3x30s retries), long enough that the caller's own
    session may already be committed and closed by the time this runs.
    Best-effort: a write-back failure must never surface as an alert-send
    failure, it just means this trade's lifecycle won't thread."""
    try:
        from sqlalchemy import update

        from tasks._db import celery_session
        Model = _model_for_trade_id(trade_id)
        async with celery_session() as session:
            await session.execute(
                update(Model).where(Model.id == trade_id).values(telegram_message_id=message_id)
            )
            await session.commit()
    except Exception as exc:
        logger.debug(f"[alerts.router] thread write-back failed for trade_id={trade_id}: {exc}")


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

        reply_to = None
        if event.action in (AlertAction.EXIT, AlertAction.UPDATE) and event.trade_id is not None:
            reply_to = await _lookup_reply_to(event.trade_id)

        text = render(event)
        from integrations.telegram_service import _post
        message_id = await _post(text, reply_to_message_id=reply_to)

        if event.action == AlertAction.ENTRY and event.trade_id is not None and message_id is not None:
            await _store_message_id(event.trade_id, message_id)
    except Exception as exc:
        logger.warning(f"[alerts.router] publish failed ({event.category}/{event.action}): {exc}")


def publish_sync(event: AlertEvent) -> None:
    """Sync-context equivalent of publish(), for Celery task bodies that
    aren't themselves async — mirrors this codebase's existing
    `_run_async(coro)` helper shape used elsewhere in tasks/india_tasks.py."""
    asyncio.run(publish(event))
