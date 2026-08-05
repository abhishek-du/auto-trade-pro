"""Event schema for the Telegram alert bus.

Phase 1 payloads are thin containers holding exactly what the *existing*
integrations.telegram_service.fmt_* functions need, so templates.py's thin
dispatcher can call them unchanged and Phase 1's output stays byte-identical
to what the old ad-hoc send()/fmt_*() call sites produced today. Phase 2
replaces templates.py's internals with real per-category renderers; the
payload shapes here (and the AlertEvent producers at every call site) don't
need to change again when that happens.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AlertCategory(str, Enum):
    TRADE         = "TRADE"          # equity paper trades (entry/exit)
    FNO_SIGNAL    = "FNO_SIGNAL"     # futures/options paper trades
    SHORTLIST     = "SHORTLIST"      # candidate flagged, may or may not execute
    DISCOVERY     = "DISCOVERY"      # universe injection (momentum/breakout screeners)
    NEWS_EVENT    = "NEWS_EVENT"     # corporate actions, market shocks, high-impact news
    WEEKLY_REPORT = "WEEKLY_REPORT"
    OPERATIONS    = "OPERATIONS"     # system health / errors / retries


class AlertAction(str, Enum):
    ENTRY  = "ENTRY"
    EXIT   = "EXIT"
    UPDATE = "UPDATE"    # reserved: SL modified, trailing-stop moved (not yet emitted)
    ALERT  = "ALERT"     # shortlist/discovery/news — no lifecycle
    REPORT = "REPORT"
    ERROR  = "ERROR"


class Severity(str, Enum):
    INFO      = "INFO"
    SUCCESS   = "SUCCESS"
    WARNING   = "WARNING"
    CRITICAL  = "CRITICAL"
    EMERGENCY = "EMERGENCY"


# ── Payloads (Phase 1: thin wrappers around the old fmt_* call shapes) ──────

@dataclass(frozen=True, slots=True)
class TradeEntryPayload:
    """Routes to fmt_entry(decision, qty=...). `qty` has no default —
    news_discovery_engine.py used to hardcode qty=0 here; a typed payload
    with no default forces every call site to pass the real quantity."""
    decision: object
    qty: float


@dataclass(frozen=True, slots=True)
class TradeEntryRawPayload:
    """For entry-adjacent alerts that are already a fully-built HTML string
    (e.g. agent_loop.py's hub-override 'TRADE PLACED' follow-up) rather than
    going through fmt_entry."""
    text: str


@dataclass(frozen=True, slots=True)
class TradeExitPayload:
    """Routes to fmt_exit(...) with the same arguments it already takes."""
    symbol: str
    side: str
    entry: float
    exit_price: float
    qty: int
    pnl: float
    reason: str


@dataclass(frozen=True, slots=True)
class ShortlistPayload:
    """Routes to fmt_shortlist_alert(...). `score`/`news_subscore`/`executed`
    are pulled out explicitly (not left inside `candidate`) so router.py can
    run the content-delta dedup gate without reaching into the duck-typed
    candidate object itself."""
    candidate: object
    score: float
    news_subscore: float
    executed: bool = False
    df: object = None
    ai_note: str = ""
    crawl_data: dict | None = None


@dataclass(frozen=True, slots=True)
class RawTextPayload:
    """Catch-all for call sites that build their own inline HTML string
    (F&O, discovery, news, operations). Text is passed through unchanged,
    with a consistent severity-badge header prepended at render time."""
    text: str


@dataclass(frozen=True, slots=True)
class ReportPayload:
    """Phase 5: the merged weekly report (rebalance signals + risk metrics
    + AI commentary), replacing what used to be two separate, uncoordinated
    Sunday tasks 30 minutes apart. Structured metrics render directly in
    the Telegram message; `ai_commentary` is a smaller addendum, not the
    whole message. `pdf_bytes`, when set, is sent as a companion document."""
    metrics: dict                      # compute_performance_metrics()'s return dict
    rebalance_trades: list             # compute_rebalance_trades()'s return list
    sector_weights: dict
    position_weights: dict
    ai_commentary: str = ""
    pdf_bytes: bytes | None = None


AlertPayload = (
    TradeEntryPayload | TradeEntryRawPayload | TradeExitPayload
    | ShortlistPayload | RawTextPayload | ReportPayload
)


@dataclass(frozen=True, slots=True)
class AlertEvent:
    category: AlertCategory
    action:   AlertAction
    severity: Severity
    payload:  AlertPayload
    symbol:   str | None = None
    trade_id: int | str | None = None   # PaperTrade.id (int) or AgentTrade.id (uuid str)
    dedup_key: str | None = None        # override; default derived from category+action+symbol/trade_id
    cooldown_seconds: int | None = None  # override the category/action default cooldown
    chart_df: object = None             # optional pre-fetched OHLC DataFrame (Phase 4 charts)
    created_at: datetime = field(default_factory=datetime.utcnow)
