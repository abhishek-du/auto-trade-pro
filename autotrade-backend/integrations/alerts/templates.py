"""Phase 1: thin render dispatcher.

Produces output byte-identical to what the old ad-hoc send()/fmt_*() call
sites sent today, by simply forwarding each payload to the existing
integrations.telegram_service formatters. Phase 2 replaces the bodies of
this function with real decision-first/reason/detail templates per
category; router.py and every event producer stay unchanged when that
happens — this is the seam.
"""
from __future__ import annotations

from integrations.telegram_service import fmt_entry, fmt_exit, fmt_shortlist_alert
from .events import (
    AlertEvent,
    RawTextPayload,
    ShortlistPayload,
    TradeEntryPayload,
    TradeEntryRawPayload,
    TradeExitPayload,
)


def render(event: AlertEvent) -> str:
    p = event.payload

    if isinstance(p, TradeEntryPayload):
        return fmt_entry(p.decision, qty=p.qty)

    if isinstance(p, TradeEntryRawPayload):
        return p.text

    if isinstance(p, TradeExitPayload):
        return fmt_exit(
            symbol=p.symbol, side=p.side, entry=p.entry, exit_price=p.exit_price,
            qty=p.qty, pnl=p.pnl, reason=p.reason,
        )

    if isinstance(p, ShortlistPayload):
        return fmt_shortlist_alert(
            p.candidate, df=p.df, ai_note=p.ai_note, executed=p.executed,
            crawl_data=p.crawl_data,
        )

    if isinstance(p, RawTextPayload):
        return p.text

    raise TypeError(f"No renderer for payload type {type(p)!r} (event={event.category}/{event.action})")
